"""Fresh-process Responses/checkpoint diagnostic without external services."""

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from unittest.mock import patch


def interval_union_ms(events):
    total = 0
    end = float("-inf")
    for event in sorted(events, key=lambda event: event["start"]):
        total += max(0, event["end"] - max(end, event["start"]))
        end = max(end, event["end"])
    return total * 1000


def worker(args):
    started = perf_counter()
    from azure.ai.agentserver.core import (
        FoundryAgentRequestContext,
        reset_request_context,
        set_request_context,
    )
    from azure.ai.agentserver.core.storage import (
        FoundryStateStore,
        FoundryStorageEndpoint,
    )
    from azure.ai.agentserver.core.storage._client import FoundryStorageClient
    from langchain_azure_ai.agents.hosting import (
        FoundryCheckpointSaver,
        ResponsesHostServer,
    )
    from langchain_azure_ai.agents.hosting._responses import (
        FoundryConversationChainStore,
    )
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.graph import END, START, MessagesState, StateGraph
    from starlette.testclient import TestClient
    from storage_transport import OfflineCredential, StorageFixture, StorageTransport

    imported = perf_counter()
    sdk_sources = {}
    for name in (
        "langchain_azure_ai.agents.hosting._foundry_checkpoint_saver",
        "langchain_azure_ai.agents.hosting._responses.conversation_chain_store",
        "azure.ai.agentserver.core.storage._state",
        "azure.ai.agentserver.core.storage._client",
    ):
        sdk_sources[name] = hashlib.sha256(
            Path(sys.modules[name].__file__).read_bytes()
        ).hexdigest()

    fixture = StorageFixture(args.delay_ms)
    credential = OfflineCredential()
    endpoint = FoundryStorageEndpoint(
        storage_base_url="https://storage.test.invalid/api/storage/"
    )
    original_init = FoundryStorageClient.__init__
    original_state_init = FoundryStateStore.__init__

    def inject_transport(self, *values, **options):
        options["transport"] = StorageTransport(fixture)
        original_init(self, *values, **options)

    def select_storage_http(self, *values, **options):
        with patch.dict(os.environ, {"FOUNDRY_HOSTING_ENVIRONMENT": "offline-perf"}):
            original_state_init(self, *values, **options)

    class ReportState(MessagesState):
        files: dict
        todos: list
        turns: int

    thread_ids = []

    async def revise(state, config):
        thread_ids.append(config["configurable"]["thread_id"])
        turns = state.get("turns", 0) + 1
        existing = state.get("files", {}).get("/final_report.md", "")
        report = existing + f"Turn {turns}.\n"
        return {
            "messages": [AIMessage(content=f"READY {turns}")],
            "files": {"/final_report.md": report},
            "todos": [{"content": "Write report", "status": "completed"}],
            "turns": turns,
        }

    observations = []
    with (
        tempfile.TemporaryDirectory() as storage,
        patch.dict(
            os.environ,
            {
                "AGENTSERVER_STATE_ROOT": storage,
                "FOUNDRY_HOSTING_ENVIRONMENT": "",
                "APPLICATIONINSIGHTS_CONNECTION_STRING": "",
                "OTEL_SDK_DISABLED": "true",
                "LANGSMITH_TRACING": "false",
                "LANGCHAIN_TRACING_V2": "false",
            },
        ),
        patch.object(FoundryStorageClient, "__init__", inject_transport),
    ):
        saver = FoundryCheckpointSaver(credential, endpoint, user_isolation=True)
        chain_store = FoundryConversationChainStore(
            credential, endpoint, user_isolation=True
        )
        builder = StateGraph(ReportState)
        builder.add_node("revise", revise)
        builder.add_edge(START, "revise")
        builder.add_edge("revise", END)
        graph = builder.compile(checkpointer=saver)
        # Select only the storage HTTP path; keep host task/response persistence local.
        with patch.object(FoundryStateStore, "__init__", select_storage_http):
            host = ResponsesHostServer(graph, conversation_chain_store=chain_store)
            constructed = perf_counter()
            with TestClient(host.app) as client:
                ready = perf_counter()
                for label, conversation, expected in [
                    *(
                        ("continuation" if turn > 1 else "first", "conv-perf", turn)
                        for turn in range(1, args.turns + 1)
                    ),
                    ("fresh-conversation", "conv-fresh", 1),
                ]:
                    before = len(fixture.events)
                    clients = fixture.constructed
                    fixture.max_active = 0
                    start = perf_counter()
                    response = client.post(
                        "/responses",
                        json={
                            "input": "revise",
                            "conversation": conversation,
                            "stream": False,
                        },
                        headers={
                            "x-agent-user-id": "fixture-user",
                            "x-agent-foundry-call-id": (
                                f"call-{conversation}-{expected}"
                            ),
                        },
                    )
                    elapsed = (perf_counter() - start) * 1000
                    if response.status_code != 200:
                        raise RuntimeError(response.text)
                    payload = response.json()
                    text = "".join(
                        block.get("text", "")
                        for item in payload["output"]
                        if item["type"] == "message"
                        for block in item["content"]
                    )
                    if payload["status"] != "completed" or text != f"READY {expected}":
                        raise RuntimeError(f"Unexpected response: {payload}")
                    events = fixture.events[before:]
                    observations.append(
                        {
                            "case": label,
                            "turn": expected,
                            "request_ms": elapsed,
                            "storage_union_ms": interval_union_ms(events),
                            "storage_calls": dict(
                                Counter(e["operation"] for e in events)
                            ),
                            "transport_constructions": fixture.constructed - clients,
                            "max_storage_concurrency": fixture.max_active,
                        }
                    )
                # Verify reconstructed graph/host state outside timed requests.
                restarted_graph = builder.compile(checkpointer=saver)
                with TestClient(
                    ResponsesHostServer(
                        restarted_graph,
                        conversation_chain_store=chain_store,
                    ).app
                ):

                    async def verify():
                        token = set_request_context(
                            FoundryAgentRequestContext(
                                user_id="fixture-user",
                                call_id="verify",
                            )
                        )
                        try:
                            state = await restarted_graph.aget_state(
                                {
                                    "configurable": {"thread_id": thread_ids[0]},
                                }
                            )
                            expected = "".join(
                                f"Turn {n}.\n" for n in range(1, args.turns + 1)
                            )
                            if (
                                state.values.get("files", {}).get("/final_report.md")
                                != expected
                            ):
                                raise RuntimeError(
                                    "Checkpoint file lost after host reconstruction"
                                )
                            if len(state.values["messages"]) != 2 * args.turns:
                                raise RuntimeError(
                                    "Conversation messages were duplicated or lost"
                                )
                            if state.values["todos"][0]["status"] != "completed":
                                raise RuntimeError("Checkpoint TODO state was lost")
                        finally:
                            reset_request_context(token)

                    asyncio.run(verify())

                async def pending_probe():
                    token = set_request_context(
                        FoundryAgentRequestContext(
                            user_id="fixture-user",
                            call_id="pending-probe",
                        )
                    )
                    try:
                        checkpoint = empty_checkpoint()
                        config = await saver.aput(
                            {
                                "configurable": {
                                    "thread_id": "pending-perf",
                                    "checkpoint_ns": "",
                                }
                            },
                            checkpoint,
                            {"source": "input", "step": 0, "parents": {}},
                            {},
                        )
                        expected = []
                        for n in range(args.pending_writes):
                            task = f"task-{n:06d}"
                            await saver.aput_writes(config, [("probe", n)], task)
                            expected.append((task, "probe", n))
                        before = len(fixture.events)
                        clients = fixture.constructed
                        fixture.max_active = 0
                        start = perf_counter()
                        result = await saver.aget_tuple(config)
                        elapsed = (perf_counter() - start) * 1000
                        if result is None or result.pending_writes != expected:
                            raise RuntimeError("Pending writes changed content/order")
                        events = fixture.events[before:]
                        observations.append(
                            {
                                "case": "pending-writes",
                                "count": args.pending_writes,
                                "request_ms": elapsed,
                                "storage_union_ms": interval_union_ms(events),
                                "storage_calls": dict(
                                    Counter(e["operation"] for e in events)
                                ),
                                "transport_constructions": fixture.constructed
                                - clients,
                                "max_storage_concurrency": fixture.max_active,
                            }
                        )
                    finally:
                        reset_request_context(token)
                        await saver.aclose()

                asyncio.run(pending_probe())
        if fixture.constructed != fixture.closed:
            raise RuntimeError(
                f"Transport leak: {fixture.constructed} created, "
                f"{fixture.closed} closed"
            )
    return {
        "startup_ms": {
            "imports": (imported - started) * 1000,
            "graph_and_host": (constructed - imported) * 1000,
            "lifespan": (ready - constructed) * 1000,
            "total": (ready - started) * 1000,
        },
        "observations": observations,
        "state_verified": True,
        "transports_created": fixture.constructed,
        "transports_closed": fixture.closed,
        "storage_events": fixture.events,
        "sdk_source_sha256": sdk_sources,
        "versions": {
            name: version(name)
            for name in (
                "langchain-azure-ai",
                "azure-ai-agentserver-core",
                "azure-ai-agentserver-responses",
                "langgraph",
            )
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--turns", type=int, default=4)
    parser.add_argument("--pending-writes", type=int, default=24)
    parser.add_argument("--delay-ms", type=float, default=5)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if (
        min(args.repeats, args.turns, args.pending_writes) < 1
        or not 0 <= args.delay_ms <= 1000
    ):
        parser.error(
            "Counts must be positive; delay must be finite and between 0 and 1000 ms"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        parser.error("Output already exists; choose a new filename")
    if args.worker:
        args.output.write_text(json.dumps(worker(args), indent=2), encoding="utf-8")
        return
    runs = []
    with tempfile.TemporaryDirectory() as temporary:
        for repeat in range(args.repeats):
            output = Path(temporary) / f"{repeat}.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    "--turns",
                    str(args.turns),
                    "--pending-writes",
                    str(args.pending_writes),
                    "--delay-ms",
                    str(args.delay_ms),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                env={**os.environ, "PYTHONUTF8": "1"},
            )
            if result.returncode != 0:
                raise RuntimeError(result.stdout + result.stderr)
            runs.append(json.loads(output.read_text(encoding="utf-8")))
    evidence = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "mode": "offline synthetic HTTP transport; in-process non-streaming Responses",
        "configuration": {
            "repeats": args.repeats,
            "turns": args.turns,
            "pending_writes": args.pending_writes,
            "delay_ms": args.delay_ms,
        },
        "runs": runs,
    }
    args.output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    startup = statistics.median(r["startup_ms"]["total"] for r in runs)
    print(f"Fresh-process startup median: {startup:.1f} ms")
    for index, first in enumerate(runs[0]["observations"]):
        times = [r["observations"][index]["request_ms"] for r in runs]
        print(
            f"{first['case']} {first.get('turn', first.get('count'))}: "
            f"{statistics.median(times):.1f} ms; "
            f"storage calls {first['storage_calls']}"
        )
    print(
        "Verified files, TODOs, messages, pending writes, and transport cleanup. "
        f"Results: {args.output}"
    )


if __name__ == "__main__":
    main()
