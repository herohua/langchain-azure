# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Complete continuation batches at the HTTP admission boundary."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from azure.ai.agentserver.responses import ResponsesServerOptions
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command, Interrupt, interrupt
from starlette.testclient import TestClient

from langchain_azure_ai.agents.hosting import (
    InvocationsHostServer,
    ResponsesHostServer,
)
from langchain_azure_ai.agents.hosting._converters import (
    IncompleteResumeError,
    validate_resume_command,
)
from langchain_azure_ai.agents.hosting._invoke_host import _internal_session_id

from .conftest import (
    approval_requests,
    hitl_items_in,
    interrupt_value,
    resume_item,
    sentinels,
    sse_payloads,
)
from .graphs import build_parallel_interrupt_graph


@pytest.mark.parametrize("protocol", ["responses", "invocations"])
@pytest.mark.parametrize("mode", ["json", "stream", "background"])
@pytest.mark.parametrize("channel", ["function", "approval", "rejection", "text"])
def test_partial_batch_returns_400_without_advancing_state(
    protocol: str, mode: str, channel: str
) -> None:
    graph = build_parallel_interrupt_graph()
    options = ResponsesServerOptions(resilient_background=mode == "background")
    server = (
        ResponsesHostServer(graph, options=options)
        if protocol == "responses"
        else InvocationsHostServer(graph, options=options)
    )
    endpoint = (
        "/responses"
        if protocol == "responses"
        else "/invocations?agent_session_id=complete-batch"
    )
    input_key = "input" if protocol == "responses" else "message"
    linkage = {"conversation": "complete-batch"} if protocol == "responses" else {}
    config: Any = {
        "configurable": {
            "thread_id": (
                "complete-batch"
                if protocol == "responses"
                else _internal_session_id("complete-batch", None)
            )
        }
    }

    with TestClient(server.app) as client:
        first = client.post(endpoint, json={input_key: "go", **linkage})
        assert first.status_code == 200, first.text
        calls = sentinels(first.json())
        assert len(calls) == 2
        before = graph.get_state(config)
        partial: Any = [resume_item(calls[0]["call_id"], "A")]
        if channel in {"approval", "rejection"}:
            partial = [
                {
                    "type": "mcp_approval_response",
                    "approval_request_id": approval_requests(first.json())[0]["id"],
                    "approve": channel == "approval",
                }
            ]
        elif channel == "text":
            partial = "continue"

        with (
            patch.object(server.graph, "astream", wraps=server.graph.astream) as run,
            patch(
                "azure.ai.agentserver.core.streaming.streams.get_or_create"
            ) as create,
        ):
            rejected = client.post(
                endpoint,
                json={
                    input_key: partial,
                    **linkage,
                    "stream": mode == "stream",
                    "background": mode == "background",
                },
            )
            assert rejected.status_code == 400, rejected.text
            assert rejected.headers["content-type"].startswith("application/json")
            run.assert_not_called()
            create.assert_not_called()
        assert graph.get_state(config) == before

        complete = client.post(
            endpoint,
            json={
                input_key: [resume_item(item["call_id"], "answer") for item in calls],
                **linkage,
            },
        )
        assert complete.status_code == 200, complete.text
        assert not sentinels({"output": complete.json().get("output", [])})


@pytest.mark.parametrize("protocol", ["responses", "invocations"])
@pytest.mark.parametrize("empty_result", [False, True])
@pytest.mark.parametrize("stream", [False, True])
def test_completed_sibling_is_not_required_at_the_next_pause(
    protocol: str, empty_result: bool, stream: bool
) -> None:
    def ask_a(state: MessagesState) -> dict[str, Any]:
        interrupt("A1")
        answer = interrupt("A2")
        return {"messages": [AIMessage(content=f"A={answer}")]}

    def ask_b(state: MessagesState) -> dict[str, Any]:
        answer = interrupt("B1")
        return {} if empty_result else {"messages": [AIMessage(content=f"B={answer}")]}

    builder = StateGraph(MessagesState)
    for name, node in (("a", ask_a), ("b", ask_b)):
        builder.add_node(name, node)
        builder.add_edge(START, name)
        builder.add_edge(name, END)
    graph = builder.compile(checkpointer=InMemorySaver())
    server = (
        ResponsesHostServer(graph)
        if protocol == "responses"
        else InvocationsHostServer(graph)
    )
    endpoint = (
        "/responses"
        if protocol == "responses"
        else "/invocations?agent_session_id=sequential"
    )
    input_key = "input" if protocol == "responses" else "message"
    linkage = {"conversation": "sequential"} if protocol == "responses" else {}
    with TestClient(server.app) as client:
        first = client.post(endpoint, json={input_key: "go", **linkage})
        assert first.status_code == 200, first.text
        calls = sentinels(first.json())
        assert {interrupt_value(item) for item in calls} == {"A1", "B1"}
        second = client.post(
            endpoint,
            json={
                input_key: [resume_item(item["call_id"], "yes") for item in calls],
                **linkage,
                "stream": stream,
            },
        )
        assert second.status_code == 200, second.text
        if stream:
            pending = list(
                {
                    item["call_id"]: item
                    for item in hitl_items_in(sse_payloads(second.text))
                    if item.get("type") == "function_call"
                }.values()
            )
        else:
            pending = sentinels(second.json())
        assert [interrupt_value(item) for item in pending] == ["A2"]
        third = client.post(
            endpoint,
            json={
                input_key: [resume_item(pending[0]["call_id"], "done")],
                **linkage,
            },
        )
        assert third.status_code == 200, third.text
        assert not sentinels({"output": third.json().get("output", [])})


@pytest.mark.parametrize("resume", [None, "yes", {}, {"metadata": "yes"}])
def test_parallel_normalized_command_must_answer_both_ids(resume: Any) -> None:
    pending = (Interrupt(id="a" * 32, value="A"), Interrupt(id="b" * 32, value="B"))
    with pytest.raises(IncompleteResumeError):
        validate_resume_command(Command(resume=resume), pending)


def test_raw_approvals_do_not_fill_gaps_in_executable_commands() -> None:
    pending = (Interrupt(id="a" * 32, value="A"), Interrupt(id="b" * 32, value="B"))
    items = [
        {"type": "mcp_approval_response", "approval_request_id": it.id, "approve": True}
        for it in pending
    ]
    for resume in (
        {pending[0].id: True},
        {it.id: True for it in pending} | {"metadata": 1},
    ):
        with pytest.raises(IncompleteResumeError):
            validate_resume_command(Command(resume=resume), pending, items=items)


@pytest.mark.parametrize("protocol", ["responses", "invocations"])
@pytest.mark.parametrize("complete", [False, True])
def test_custom_input_hooks_are_validated_after_normalization(
    protocol: str, complete: bool
) -> None:
    graph = build_parallel_interrupt_graph()
    command: Command = Command()

    class CustomResponses(ResponsesHostServer):
        async def build_resume_command(self, *args: Any) -> Any:
            return command, frozenset()

    class CustomInvocations(InvocationsHostServer):
        def build_input(self, message: str) -> Any:
            return (
                command if message == "custom reply" else super().build_input(message)
            )

    server = (
        CustomResponses(graph) if protocol == "responses" else CustomInvocations(graph)
    )
    endpoint = (
        "/responses"
        if protocol == "responses"
        else "/invocations?agent_session_id=custom"
    )
    key = "input" if protocol == "responses" else "message"
    linkage = {"conversation": "custom"} if protocol == "responses" else {}
    with TestClient(server.app) as client:
        first = client.post(endpoint, json={key: "go", **linkage})
        assert first.status_code == 200, first.text
        calls = sentinels(first.json())
        command = Command(
            resume={it["call_id"]: False for it in calls[: 2 if complete else 1]}
        )
        result = client.post(endpoint, json={key: "custom reply", **linkage})
        assert result.status_code == (200 if complete else 400), result.text


@pytest.mark.parametrize("protocol", ["responses", "invocations"])
def test_checkpoint_read_failure_is_redacted_and_does_not_execute(
    protocol: str,
) -> None:
    graph = build_parallel_interrupt_graph()
    server = (
        ResponsesHostServer(graph)
        if protocol == "responses"
        else InvocationsHostServer(graph)
    )
    key = "input" if protocol == "responses" else "message"
    with TestClient(server.app) as client:
        with (
            patch.object(
                server.graph,
                "aget_state",
                AsyncMock(side_effect=RuntimeError("private storage detail")),
            ),
            patch.object(server.graph, "astream", wraps=server.graph.astream) as run,
        ):
            result = client.post("/" + protocol, json={key: "go", "stream": True})
            assert result.status_code == 500, result.text
            assert "private storage detail" not in result.text
            run.assert_not_called()


@pytest.mark.parametrize("protocol", ["responses", "invocations"])
@pytest.mark.parametrize("answer", [None, False, 0, "", [], {}])
def test_empty_answers_reach_the_interrupt_unchanged(
    protocol: str, answer: Any
) -> None:
    received: list[Any] = []

    def ask(state: MessagesState) -> dict[str, Any]:
        received.append(interrupt("question"))
        return {"messages": [AIMessage(content="done")]}

    builder = StateGraph(MessagesState)
    builder.add_node("ask", ask)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    server = (
        ResponsesHostServer(graph)
        if protocol == "responses"
        else InvocationsHostServer(graph)
    )
    key = "input" if protocol == "responses" else "message"
    linkage = {"conversation": "empty-answer"} if protocol == "responses" else {}
    endpoint = (
        "/responses"
        if protocol == "responses"
        else "/invocations?agent_session_id=empty-answer"
    )
    with TestClient(server.app) as client:
        first = client.post(endpoint, json={key: "go", **linkage})
        assert first.status_code == 200, first.text
        item = resume_item(sentinels(first.json())[0]["call_id"], answer)
        if answer == "" or answer == []:
            item["output"] = answer
        result = client.post(endpoint, json={key: [item], **linkage})
        assert result.status_code == 200, result.text
        assert received == [answer]


def test_task_checkpoint_override_requires_shared_selection(
    invocation_state_store: dict[Any, Any],
) -> None:
    class ChangedCheckpoint(InvocationsHostServer):
        def build_task_runnable_config(self, *args: Any) -> Any:
            config = super().build_task_runnable_config(*args)
            config["configurable"]["thread_id"] = "different-thread"
            return config

    server = ChangedCheckpoint(
        build_parallel_interrupt_graph(),
        options=ResponsesServerOptions(steerable_conversations=True),
    )
    with TestClient(server.app) as client:
        with patch.object(server.graph, "astream", wraps=server.graph.astream) as run:
            result = client.post("/invocations", json={"message": "go"})
            assert result.status_code == 400, result.text
            assert "build_checkpoint_config" in result.json()["error"]
            run.assert_not_called()
        assert invocation_state_store
        assert all(
            record["status"] == "failed" for record in invocation_state_store.values()
        )
