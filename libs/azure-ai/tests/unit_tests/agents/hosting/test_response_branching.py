# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Public Responses branching contracts with real graphs and checkpoint history."""

from __future__ import annotations

import asyncio
import hashlib
import json
import operator
from collections.abc import AsyncGenerator, AsyncIterator
from copy import deepcopy
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

pytest.importorskip("azure.ai.agentserver.responses")

from azure.ai.agentserver.responses import (  # noqa: E402
    FileResponseStore,
    InMemoryResponseProvider,
    PlatformContext,
    ResponseContext,
    ResponseEventStream,
    ResponsesServerOptions,
)
from azure.ai.agentserver.responses.models import (  # noqa: E402
    CreateResponse,
    ResponseObject,
)
from azure.ai.agentserver.responses.models.runtime import (
    ResponseModeFlags,  # noqa: E402
)
from azure.ai.agentserver.responses.streaming._checkpoint import (  # noqa: E402
    ResponseCheckpointEvent,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: E402
from langchain_core.runnables import RunnableConfig  # noqa: E402
from langgraph.checkpoint.base import BaseCheckpointSaver  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import END, START, MessagesState, StateGraph  # noqa: E402
from langgraph.graph.state import CompiledStateGraph  # noqa: E402
from langgraph.types import interrupt  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from langchain_azure_ai.agents.hosting import ResponsesHostServer  # noqa: E402
from langchain_azure_ai.agents.hosting._responses import (  # noqa: E402
    HostingRunnableConfig,
    TaskStorageManager,
)


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTSERVER_STATE_ROOT", str(tmp_path))


def _graph(
    calls: list[list[str]], *, saver: BaseCheckpointSaver | None = None
) -> CompiledStateGraph:
    async def answer(state: MessagesState) -> dict[str, Any]:
        texts = [
            str(message.content)
            for message in state["messages"]
            if isinstance(message, (HumanMessage, SystemMessage))
        ]
        calls.append(texts)
        return {"messages": [AIMessage(content="|".join(texts))]}

    builder = StateGraph(MessagesState)
    builder.add_node("answer", answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    return builder.compile(checkpointer=saver if saver is not None else InMemorySaver())


def _text(response: dict[str, Any]) -> str:
    return "".join(
        part.get("text", "")
        for item in response["output"]
        if item["type"] == "message"
        for part in item["content"]
    )


def _config(response: dict[str, Any]) -> RunnableConfig:
    reference = TaskStorageManager.from_stream(
        ResponseEventStream(response_id=response["id"], response=cast(Any, response))
    ).checkpoint_ref
    assert reference is not None
    return {
        "configurable": {
            "thread_id": reference.thread_id,
            "checkpoint_id": reference.checkpoint_id,
            "checkpoint_ns": "",
        }
    }


def _create(
    client: TestClient, *, headers: dict[str, str] | None = None, **body: Any
) -> dict[str, Any]:
    result = client.post("/responses", json=body, headers=headers)
    assert result.status_code == 200, result.text
    response = result.json()
    assert response["status"] == "completed", response
    return cast(dict[str, Any], response)


def test_siblings_restore_the_exact_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    graph = _graph(calls)
    provider = InMemoryResponseProvider()
    server = ResponsesHostServer(graph, store=provider, enable_response_branching=True)
    with TestClient(server.app) as client:
        parent = _create(client, input="A")
        assert client.portal is not None
        get_response = provider.get_response
        stored_parent = client.portal.call(get_response, parent["id"])
        before = graph.get_state(_config(cast(Any, stored_parent)))
        lookup = AsyncMock(wraps=get_response)
        monkeypatch.setattr(provider, "get_response", lookup)
        first = _create(client, input="B", previous_response_id=parent["id"])
        assert lookup.await_count == 1
        stored_first = client.portal.call(get_response, first["id"])
        lookup.reset_mock()
        second = _create(client, input="C", previous_response_id=parent["id"])
        assert lookup.await_count == 1
        stored_second = client.portal.call(get_response, second["id"])
        continued = _create(client, input="D", previous_response_id=first["id"])

    assert [_text(item) for item in (parent, first, second, continued)] == [
        "A",
        "A|B",
        "A|C",
        "A|B|D",
    ]
    assert calls == [["A"], ["A", "B"], ["A", "C"], ["A", "B", "D"]]
    assert graph.get_state(_config(cast(Any, stored_parent))).values == before.values
    configs = [
        _config(cast(Any, item))["configurable"]
        for item in (stored_parent, stored_first, stored_second)
    ]
    assert len({config["thread_id"] for config in configs}) == 1
    assert len({config["checkpoint_id"] for config in configs}) == 3
    assert graph.checkpointer.__class__ is InMemorySaver


def test_default_file_provider_and_unretained_child() -> None:
    calls: list[list[str]] = []
    server = ResponsesHostServer(_graph(calls), enable_response_branching=True)
    assert server.app._orchestrator is not None
    assert isinstance(server.app._orchestrator._provider, FileResponseStore)
    with TestClient(server.app) as client:
        parent = _create(client, input="A")
        child = _create(
            client, input="B", previous_response_id=parent["id"], store=False
        )
        assert client.get(f"/responses/{child['id']}").status_code == 404
        assert client.get(f"/responses/{parent['id']}").status_code == 200
        rejected = client.post(
            "/responses", json={"input": "C", "previous_response_id": child["id"]}
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "failed"
    assert calls == [["A"], ["A", "B"]]


@pytest.mark.parametrize("instructions", [None, "child instructions"])
def test_request_instructions_do_not_leak_into_children(
    instructions: str | None,
) -> None:
    calls: list[list[str]] = []
    server = ResponsesHostServer(
        _graph(calls), store=InMemoryResponseProvider(), enable_response_branching=True
    )
    with TestClient(server.app) as client:
        parent = _create(
            client,
            instructions="parent instructions",
            input=[
                {"role": "system", "content": "history system"},
                {"role": "developer", "content": "history developer"},
                {"role": "user", "content": "A"},
            ],
        )
        child = _create(
            client,
            input="B",
            previous_response_id=parent["id"],
            instructions=instructions,
        )
    expected = ["history system", "history developer", "A"]
    if instructions is not None:
        expected.append(instructions)
    expected.append("B")
    assert calls[-1] == expected
    assert "parent instructions" not in _text(child)


@pytest.mark.parametrize(
    "extra",
    [
        {"app": MagicMock()},
        {"options": ResponsesServerOptions(steerable_conversations=True)},
        {"store": True},
        {"store": False},
    ],
)
def test_unsupported_host_combinations_fail_at_startup(extra: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        ResponsesHostServer(_graph([]), enable_response_branching=True, **extra)


def test_branching_requires_async_checkpoint_support() -> None:
    with pytest.raises(ValueError, match="checkpointer"):
        ResponsesHostServer(
            _graph([]).copy({"checkpointer": None}), enable_response_branching=True
        )
    with pytest.raises(ValueError, match="asynchronous"):
        ResponsesHostServer(
            _graph([], saver=BaseCheckpointSaver()), enable_response_branching=True
        )


@pytest.mark.parametrize("status", ["in_progress", "failed", "cancelled"])
@pytest.mark.parametrize("stream", [False, True])
def test_unfinished_response_is_not_a_parent(status: str, stream: bool) -> None:
    calls: list[list[str]] = []
    provider = InMemoryResponseProvider()
    server = ResponsesHostServer(
        _graph(calls), store=provider, enable_response_branching=True
    )
    with TestClient(server.app) as client:
        parent = _create(client, input="A")
        assert client.portal is not None
        stored = client.portal.call(provider.get_response, parent["id"])
        stored["status"] = cast(Any, status)
        client.portal.call(provider.update_response, stored)
        response = client.post(
            "/responses",
            json={"input": "B", "previous_response_id": parent["id"], "stream": stream},
        )
    assert response.status_code == 200
    if stream:
        assert "response.failed" in response.text
    else:
        assert response.json()["status"] == "failed"
    assert calls == [["A"]]


@pytest.mark.parametrize("static_pause", [False, True])
@pytest.mark.parametrize(
    "reply_kind", ["text", "function_call_output", "mcp_approval_response"]
)
def test_paused_graph_is_not_a_branch_parent(
    static_pause: bool, reply_kind: str
) -> None:
    runs = []

    async def pause(state: MessagesState) -> dict[str, Any]:
        runs.append(True)
        answer = interrupt("Your answer?")
        return {"messages": [AIMessage(content=str(answer))]}

    builder = StateGraph(MessagesState)
    builder.add_node("pause", pause)
    builder.add_edge(START, "pause")
    builder.add_edge("pause", END)
    graph = builder.compile(
        checkpointer=InMemorySaver(),
        interrupt_before=["pause"] if static_pause else None,
    )
    server = ResponsesHostServer(
        graph, store=InMemoryResponseProvider(), enable_response_branching=True
    )
    with TestClient(server.app) as client:
        parent = _create(client, input="A")
        original_runs = list(runs)
        graph_input: Any = "B"
        if reply_kind == "function_call_output":
            call_id = next(
                (
                    item["call_id"]
                    for item in parent["output"]
                    if item["type"] == "function_call"
                ),
                "unanswered-call",
            )
            graph_input = [
                {
                    "type": reply_kind,
                    "call_id": call_id,
                    "output": json.dumps({"resume": "answered"}),
                }
            ]
        elif reply_kind == "mcp_approval_response":
            approval_id = next(
                (
                    item["id"]
                    for item in parent["output"]
                    if item["type"] == "mcp_approval_request"
                ),
                "unanswered-approval",
            )
            graph_input = [
                {
                    "type": reply_kind,
                    "approval_request_id": approval_id,
                    "approve": True,
                }
            ]
        rejected = client.post(
            "/responses",
            json={"input": graph_input, "previous_response_id": parent["id"]},
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "failed"
        assert runs == original_runs


@pytest.mark.parametrize("damage", ["reference", "checkpoint", "identity"])
def test_missing_parent_state_does_not_fall_back(
    damage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    saver = InMemorySaver()
    graph = _graph(calls, saver=saver)
    provider = InMemoryResponseProvider()
    server = ResponsesHostServer(graph, store=provider, enable_response_branching=True)
    with TestClient(server.app) as client:
        parent = _create(client, input="A")
        assert client.portal is not None
        stored = client.portal.call(provider.get_response, parent["id"])
        if damage == "reference":
            seeded = ResponseEventStream(response_id=parent["id"], response=stored)
            seeded.internal_metadata["langgraph_checkpoint_id"] = None
            client.portal.call(provider.update_response, cast(Any, seeded.response))
        elif damage == "checkpoint":
            config = _config(cast(Any, stored))["configurable"]
            saver.storage[config["thread_id"]][""].pop(config["checkpoint_id"])
        else:
            saved = client.portal.call(saver.aget_tuple, _config(cast(Any, stored)))
            assert saved is not None
            wrong_config = {
                "configurable": {
                    **saved.config["configurable"],
                    "checkpoint_id": "different-checkpoint",
                }
            }
            monkeypatch.setattr(
                saver,
                "aget_tuple",
                AsyncMock(return_value=saved._replace(config=wrong_config)),
            )
        result = client.post(
            "/responses", json={"input": "B", "previous_response_id": parent["id"]}
        )
    assert result.status_code == 200
    assert result.json()["status"] == "failed"
    assert result.json()["error"]["code"] == "server_error"
    assert calls == [["A"]]


def test_completed_parent_awaiting_tool_result_is_not_a_branch() -> None:
    calls = []

    async def answer(state: MessagesState) -> dict[str, Any]:
        calls.append(state["messages"][-1].content)
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "call", "name": "lookup", "args": {}}],
                )
            ]
        }

    builder = StateGraph(MessagesState)
    builder.add_node("answer", answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    server = ResponsesHostServer(
        builder.compile(checkpointer=InMemorySaver()),
        store=InMemoryResponseProvider(),
        enable_response_branching=True,
    )
    with TestClient(server.app) as client:
        parent = _create(client, input="A")
        assert any(item["type"] == "function_call" for item in parent["output"])
        child = client.post(
            "/responses", json={"input": "B", "previous_response_id": parent["id"]}
        )
    assert child.status_code == 200
    assert child.json()["status"] == "failed"
    assert calls == ["A"]


def test_exact_parent_from_an_ordinary_conversation_survives_newer_turns() -> None:
    calls: list[list[str]] = []
    graph = _graph(calls)
    provider = InMemoryResponseProvider()
    ordinary = ResponsesHostServer(graph, store=provider)
    with TestClient(ordinary.app) as client:
        parent = _create(
            client,
            input="A",
            instructions="old request instructions",
            conversation="existing-conversation",
        )
        _create(client, input="newer", conversation="existing-conversation")
    branching = ResponsesHostServer(
        graph, store=provider, enable_response_branching=True
    )
    with TestClient(branching.app) as client:
        child = _create(client, input="B", previous_response_id=parent["id"])
    assert _text(child) == "A|B"
    assert calls[-1] == ["A", "B"]


@pytest.mark.parametrize("call_super", [False, True])
def test_hooks_and_bound_configuration_cannot_redirect_a_branch(
    call_super: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ConfiguredHost(ResponsesHostServer):
        async def build_runnable_config(
            self, request: CreateResponse, context: ResponseContext
        ) -> RunnableConfig:
            config = (
                await super().build_runnable_config(request, context)
                if call_super
                else {"configurable": {}}
            )
            config["configurable"].update(
                thread_id="hook-thread",
                checkpoint_ns="hook:private",
                checkpoint_id="hook-checkpoint",
            )
            return config

    graph = _graph([])
    provider = InMemoryResponseProvider()
    with TestClient(ResponsesHostServer(graph, store=provider).app) as client:
        parent = _create(client, input="A")
    get_response = provider.get_response
    lookup = AsyncMock(wraps=get_response)
    monkeypatch.setattr(provider, "get_response", lookup)
    graph = graph.with_config(
        {
            "configurable": {
                "thread_id": "bound-thread",
                "checkpoint_ns": "bound:private",
                "checkpoint_id": "bound-checkpoint",
            }
        }
    )
    server = ConfiguredHost(graph, store=provider, enable_response_branching=True)
    with TestClient(server.app) as client:
        child = _create(client, input="B", previous_response_id=parent["id"])
        assert lookup.await_count == 1
        assert client.portal is not None
        stored = client.portal.call(get_response, parent["id"])
    assert _text(child) == "A|B"
    assert _config(cast(Any, stored))["configurable"]["thread_id"] == parent["id"]
    assert graph.config is not None
    assert graph.config["configurable"]["thread_id"] == "bound-thread"


def test_provider_scope_and_scoped_thread_identity() -> None:
    from azure.ai.agentserver.core import get_request_context

    class ScopedSaver(InMemorySaver):
        async def aget_tuple(self, config: RunnableConfig) -> Any:
            platform = get_request_context()
            assert platform.user_id == "alice"
            assert platform.call_id == "opaque-call"
            return await super().aget_tuple(config)

    calls: list[list[str]] = []
    provider = FileResponseStore()
    server = ResponsesHostServer(
        _graph(calls, saver=ScopedSaver()),
        store=provider,
        enable_response_branching=True,
    )
    headers = {"x-agent-user-id": "alice", "x-agent-foundry-call-id": "opaque-call"}
    with TestClient(server.app) as client:
        parent = _create(client, input="A", headers=headers)
        denied = client.post(
            "/responses",
            headers={"x-agent-user-id": "bob"},
            json={"input": "denied", "previous_response_id": parent["id"]},
        )
        assert denied.status_code == 200
        assert denied.json()["status"] == "failed"
        child = _create(
            client, input="B", previous_response_id=parent["id"], headers=headers
        )
        assert client.portal is not None
        stored = client.portal.call(
            partial(
                provider.get_response,
                parent["id"],
                context=PlatformContext(user_id_key="alice", call_id="opaque-call"),
            )
        )
    thread = _config(cast(Any, stored))["configurable"]["thread_id"]
    assert thread == f"user-{hashlib.sha256(b'alice').hexdigest()}:{parent['id']}"
    assert _text(child) == "A|B"
    assert calls == [["A"], ["A", "B"]]


@pytest.mark.parametrize("user_id", ["bob", None])
def test_parent_scope_is_checked_with_an_unpartitioned_provider(
    user_id: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    provider = InMemoryResponseProvider()
    server = ResponsesHostServer(
        _graph(calls), store=provider, enable_response_branching=True
    )
    with TestClient(server.app) as client:
        parent = _create(client, input="A", headers={"x-agent-user-id": "alice"})
        assert client.portal is not None
        saved = client.portal.call(
            partial(
                provider.get_response,
                parent["id"],
                context=PlatformContext(user_id_key="alice"),
            )
        )
        monkeypatch.setattr(provider, "get_response", AsyncMock(return_value=saved))
        denied = client.post(
            "/responses",
            json={"input": "B", "previous_response_id": parent["id"]},
            headers={"x-agent-user-id": user_id} if user_id else {},
        )
    assert denied.status_code == 200
    assert denied.json()["status"] == "failed"
    assert calls == [["A"]]


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_concurrent_siblings_keep_full_graph_state(
    backend: str, tmp_path: Path
) -> None:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    class State(MessagesState):
        turns: Annotated[list[str], operator.add]

    barrier = asyncio.Barrier(2)

    async def run(saver: BaseCheckpointSaver) -> None:
        async def answer(state: State) -> dict[str, Any]:
            texts = [
                str(message.content)
                for message in state["messages"]
                if isinstance(message, HumanMessage)
            ]
            if texts[-1] != "A":
                await barrier.wait()
            return {
                "messages": [AIMessage(content="|".join(texts))],
                "turns": [texts[-1]],
            }

        builder = StateGraph(State)
        builder.add_node("answer", answer)
        builder.add_edge(START, "answer")
        builder.add_edge("answer", END)
        graph = builder.compile(checkpointer=saver)
        provider = InMemoryResponseProvider()
        chain = SimpleNamespace(get=AsyncMock(), set=AsyncMock())
        server = ResponsesHostServer(
            graph,
            store=provider,
            conversation_chain_store=chain,
            enable_response_branching=True,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://test"
        ) as client:
            parent = (await client.post("/responses", json={"input": "A"})).json()
            parent_snapshot = await provider.get_response(parent["id"])
            before = await graph.aget_state(_config(cast(Any, parent_snapshot)))
            chain.get.reset_mock()
            chain.set.reset_mock()
            results = await asyncio.wait_for(
                asyncio.gather(
                    *(
                        client.post(
                            "/responses",
                            json={"input": text, "previous_response_id": parent["id"]},
                        )
                        for text in ("B", "C")
                    )
                ),
                timeout=10,
            )
        assert [_text(result.json()) for result in results] == ["A|B", "A|C"]
        assert before.values["turns"] == ["A"]
        assert (
            await graph.aget_state(_config(cast(Any, parent_snapshot)))
        ).values == before.values
        for result, text in zip(results, ("B", "C")):
            stored = await provider.get_response(result.json()["id"])
            state = await graph.aget_state(_config(cast(Any, stored)))
            assert state.values["turns"] == ["A", text]
        chain.get.assert_not_awaited()
        chain.set.assert_not_awaited()

    if backend == "sqlite":
        async with AsyncSqliteSaver.from_conn_string(
            str(tmp_path / "checkpoints.sqlite")
        ) as saver:
            await run(saver)
    else:
        await run(InMemorySaver())


def test_streamed_child_and_null_linkage_on_prefixed_routes() -> None:
    server = ResponsesHostServer(
        _graph([]), prefix="/v1", enable_response_branching=True
    )
    with TestClient(server.app) as client:
        parent = client.post(
            "/v1/responses",
            json={"input": "A", "previous_response_id": None, "conversation": None},
        ).json()
        response = client.post(
            "/v1/responses",
            json={"input": "B", "previous_response_id": parent["id"], "stream": True},
        )
    assert response.status_code == 200
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    assert events[-1]["type"] == "response.completed"
    assert _text(events[-1]["response"]) == "A|B"


def test_hosted_default_provider_remains_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "azure.ai.agentserver.core.tasks._enablement._RESILIENT_TASKS_ENABLED", False
    )
    provider = InMemoryResponseProvider()
    factory = MagicMock(return_value=provider)
    monkeypatch.setenv("FOUNDRY_HOSTING_ENVIRONMENT", "test")
    monkeypatch.setenv(
        "FOUNDRY_PROJECT_ENDPOINT",
        "https://test.services.ai.azure.com/api/projects/test",
    )
    monkeypatch.setenv("FOUNDRY_AGENT_SESSION_GUID", "a" * 32)
    monkeypatch.setattr(
        "azure.ai.agentserver.responses.store._foundry_provider.FoundryStorageProvider",
        factory,
    )
    monkeypatch.setattr("azure.identity.aio.DefaultAzureCredential", MagicMock())
    server = ResponsesHostServer(_graph([]), enable_response_branching=True)
    factory.assert_called_once()
    assert server.app._orchestrator is not None
    assert server.app._orchestrator._provider is provider
    with TestClient(server.app) as client:
        parent = _create(client, input="A")
        child = _create(client, input="B", previous_response_id=parent["id"])
    assert _text(child) == "A|B"


@pytest.mark.parametrize("kind", ["text", "tool_result", "approval"])
@pytest.mark.parametrize("branching", [False, True])
async def test_ordinary_continuation_keeps_chain_checkpoint(
    kind: str, branching: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = InMemoryResponseProvider()
    lookup = AsyncMock()
    monkeypatch.setattr(provider, "get_response", lookup)
    reference = {"thread_id": "chain-thread", "checkpoint_id": "chain-latest"}
    chain = SimpleNamespace(get=AsyncMock(return_value=reference), set=AsyncMock())
    server = ResponsesHostServer(
        _graph([]),
        store=provider,
        conversation_chain_store=chain,
        enable_response_branching=branching,
    )
    request = cast(
        CreateResponse,
        {
            **(
                {"conversation": "conversation"}
                if branching
                else {"previous_response_id": "parent"}
            ),
            "input": "next"
            if kind == "text"
            else [
                {"type": "function_call_output", "call_id": "call", "output": "result"}
                if kind == "tool_result"
                else {
                    "type": "mcp_approval_response",
                    "approval_request_id": "approval",
                    "approve": True,
                }
            ],
        },
    )
    context = ResponseContext(
        response_id="current",
        request=request,
        previous_response_id=request.get("previous_response_id"),
        conversation_id="conversation" if branching else None,
        mode_flags=ResponseModeFlags(stream=False, store=True, background=False),
        provider=provider,
    )
    monkeypatch.setattr(
        server,
        "_resolve_thread_config",
        AsyncMock(return_value=HostingRunnableConfig.create("chain-thread", context)),
    )
    config = await server.build_runnable_config(request, context)
    assert config["configurable"]["checkpoint_id"] == "chain-latest"
    assert config["configurable"]["thread_id"] == "chain-thread"
    chain.get.assert_awaited_once()
    lookup.assert_not_awaited()


def _recovery_context(
    request: CreateResponse,
    provider: InMemoryResponseProvider,
    response_id: str,
    snapshot: ResponseObject | None = None,
) -> ResponseContext:
    context = ResponseContext(
        response_id=response_id,
        request=request,
        previous_response_id=request.get("previous_response_id"),
        conversation_id=cast(str | None, request.get("conversation")),
        mode_flags=ResponseModeFlags(stream=False, store=True, background=True),
        provider=provider,
    )
    context.is_recovery = snapshot is not None
    context.persisted_response = snapshot
    return context


@pytest.mark.parametrize("admitted", [False, True])
async def test_branch_recovery_without_progress_restores_exact_parent(
    admitted: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    graph = _graph(calls)
    provider = InMemoryResponseProvider()
    chain = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    server = ResponsesHostServer(
        graph,
        store=provider,
        conversation_chain_store=chain,
        enable_response_branching=True,
    )
    parent_request = cast(
        CreateResponse, {"input": "A", "conversation": "existing-conversation"}
    )
    parent = [
        event
        async for event in server._handle_create_async_gen(
            parent_request,
            _recovery_context(parent_request, provider, "parent"),
            asyncio.Event(),
        )
    ][-1]["response"]
    await provider.create_response(parent, None, None)
    request = cast(CreateResponse, {"input": "B", "previous_response_id": "parent"})
    snapshot = None
    if admitted:
        events = server._handle_create_async_gen(
            request, _recovery_context(request, provider, "child"), asyncio.Event()
        )
        async for event in events:
            if isinstance(event, ResponseCheckpointEvent):
                snapshot = deepcopy(event.response)
                break
        await cast(AsyncGenerator[Any, None], events).aclose()
        assert snapshot is not None
        assert (
            TaskStorageManager.from_stream(
                ResponseEventStream(response_id="child", response=snapshot)
            ).checkpoint_ref
            is None
        )
    sibling_request = cast(
        CreateResponse, {"input": "newer", "previous_response_id": "parent"}
    )
    sibling = [
        event
        async for event in server._handle_create_async_gen(
            sibling_request,
            _recovery_context(sibling_request, provider, "sibling"),
            asyncio.Event(),
        )
    ][-1]["response"]
    assert _text(sibling) == "A|newer"
    chain.get.reset_mock()
    chain.set.reset_mock()
    lookup = AsyncMock(wraps=provider.get_response)
    monkeypatch.setattr(provider, "get_response", lookup)
    context = _recovery_context(request, provider, "child", snapshot)
    context.is_recovery = True
    recovered = [
        event
        async for event in server._handle_create_async_gen(
            request, context, asyncio.Event()
        )
    ][-1]["response"]
    assert recovered["status"] == "completed"
    assert recovered["id"] == "child"
    assert _text(recovered) == "A|B"
    assert calls[-1] == ["A", "B"]
    assert lookup.await_count == 1
    chain.get.assert_not_awaited()
    chain.set.assert_not_awaited()


@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("override_config", [False, True])
async def test_branch_recovery_uses_own_progress_without_parent_lookup(
    paused: bool, override_config: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def answer(state: MessagesState) -> dict[str, Any]:
        texts = [
            str(message.content)
            for message in state["messages"]
            if isinstance(message, HumanMessage)
        ]
        if paused and texts[-1] == "B":
            interrupt("Continue?")
        return {"messages": [AIMessage(content="|".join(texts))]}

    class ConfiguredHost(ResponsesHostServer):
        async def build_runnable_config(
            self, request: CreateResponse, context: ResponseContext
        ) -> RunnableConfig:
            if override_config:
                return {
                    "configurable": {"thread_id": "wrong", "checkpoint_id": "wrong"}
                }
            return await super().build_runnable_config(request, context)

    builder = StateGraph(MessagesState)
    builder.add_node("answer", answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    provider = InMemoryResponseProvider()
    chain = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    original = ResponsesHostServer(
        graph, store=provider, conversation_chain_store=chain
    )
    parent_request = cast(CreateResponse, {"input": "A"})
    parent = [
        event
        async for event in original._handle_create_async_gen(
            parent_request,
            _recovery_context(parent_request, provider, "parent"),
            asyncio.Event(),
        )
    ][-1]["response"]
    await provider.create_response(parent, None, None)
    server = ConfiguredHost(
        graph,
        store=provider,
        conversation_chain_store=chain,
        enable_response_branching=True,
    )
    request = cast(CreateResponse, {"input": "B", "previous_response_id": "parent"})
    snapshot = deepcopy(
        [
            event
            async for event in server._handle_create_async_gen(
                request, _recovery_context(request, provider, "child"), asyncio.Event()
            )
        ][-1]["response"]
    )
    own_config = _config(snapshot)
    state = await graph.aget_state(own_config)
    assert bool(state.next) == paused
    sibling_request = cast(
        CreateResponse, {"input": "newer", "previous_response_id": "parent"}
    )
    _ = [
        event
        async for event in server._handle_create_async_gen(
            sibling_request,
            _recovery_context(sibling_request, provider, "sibling"),
            asyncio.Event(),
        )
    ]
    captured: list[tuple[Any, RunnableConfig]] = []
    original_stream = server.graph.astream

    async def capture(
        graph_input: Any, *, config: RunnableConfig, **kwargs: Any
    ) -> AsyncIterator[Any]:
        captured.append((graph_input, config))
        async for chunk in original_stream(graph_input, config=config, **kwargs):
            yield chunk

    monkeypatch.setattr(server.graph, "astream", capture)
    lookup = AsyncMock(side_effect=RuntimeError("Parent is unavailable"))
    monkeypatch.setattr(provider, "get_response", lookup)
    chain.get.reset_mock()
    chain.set.reset_mock()
    recovered = [
        event
        async for event in server._handle_create_async_gen(
            request,
            _recovery_context(request, provider, "child", snapshot),
            asyncio.Event(),
        )
    ][-1]["response"]
    assert recovered["status"] == "completed"
    assert recovered["id"] == "child"
    assert len(captured) == 1
    assert captured[0][0] is None
    assert HostingRunnableConfig(captured[0][1]).checkpoint_ref == (
        HostingRunnableConfig(own_config).checkpoint_ref
    )
    state = await graph.aget_state(own_config)
    assert [
        message.content
        for message in state.values["messages"]
        if isinstance(message, HumanMessage)
    ] == ["A", "B"]
    lookup.assert_not_awaited()
    chain.get.assert_not_awaited()
    chain.set.assert_not_awaited()


@pytest.mark.parametrize("damage", ["partial", "malformed", "missing"])
async def test_branch_recovery_with_invalid_progress_does_not_restart(
    damage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    saver = InMemorySaver()
    graph = _graph(calls, saver=saver)
    provider = InMemoryResponseProvider()
    chain = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    server = ResponsesHostServer(
        graph,
        store=provider,
        conversation_chain_store=chain,
        enable_response_branching=True,
    )
    parent_request = cast(CreateResponse, {"input": "A"})
    parent = [
        event
        async for event in server._handle_create_async_gen(
            parent_request,
            _recovery_context(parent_request, provider, "parent"),
            asyncio.Event(),
        )
    ][-1]["response"]
    await provider.create_response(parent, None, None)
    request = cast(CreateResponse, {"input": "B", "previous_response_id": "parent"})
    snapshot = deepcopy(
        [
            event
            async for event in server._handle_create_async_gen(
                request, _recovery_context(request, provider, "child"), asyncio.Event()
            )
        ][-1]["response"]
    )
    config = _config(snapshot)["configurable"]
    seed = ResponseEventStream(response_id="child", response=snapshot)
    if damage == "partial":
        seed.internal_metadata.pop("langgraph_checkpoint_id")
    elif damage == "malformed":
        seed.internal_metadata["langgraph_checkpoint_id"] = 42
    else:
        saver.storage[config["thread_id"]][""].pop(config["checkpoint_id"])
    lookup = AsyncMock(wraps=provider.get_response)
    monkeypatch.setattr(provider, "get_response", lookup)
    chain.get.reset_mock()
    chain.set.reset_mock()
    before = deepcopy(calls)
    recovered = [
        event
        async for event in server._handle_create_async_gen(
            request,
            _recovery_context(
                request, provider, "child", cast(ResponseObject, seed.response)
            ),
            asyncio.Event(),
        )
    ][-1]["response"]
    assert recovered["status"] == "failed"
    assert calls == before
    lookup.assert_not_awaited()
    chain.get.assert_not_awaited()
    chain.set.assert_not_awaited()
