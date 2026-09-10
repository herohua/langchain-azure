# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the human-in-the-loop converter functions in ``_hitl.py``.

These exercise the pure translation layer between LangGraph ``Interrupt``
objects and Responses-API items — no graph and no host involved.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("azure.ai.agentserver.responses")

from azure.ai.agentserver.responses.models import (
    FunctionCallOutputItemParam,
    ItemFunctionToolCall,
    MCPApprovalResponse,
)
from langchain_core.runnables import RunnableLambda

from langchain_azure_ai.agents.hosting._converters import (
    HITL_FUNCTION_NAME,
    HITL_MCP_SERVER_LABEL,
    build_messages_input,
    detect_pending_interrupts,
    hitl_call_ids,
    interrupt_arguments_json,
    parse_resume_command,
    track_pending_interrupts,
)

from .conftest import emitted_items, pending_interrupt


async def test_detect_pending_interrupts_returns_empty_for_stateless_runnable() -> None:
    graph = RunnableLambda(lambda value: value)

    pending = await detect_pending_interrupts(graph, {})

    assert pending == ()


async def test_detect_pending_interrupts_skips_completed_empty_result() -> None:
    answered = pending_interrupt(id="int-a", value="answered")
    outstanding = pending_interrupt(id="int-b", value="outstanding")
    graph = MagicMock()
    graph.aget_state = AsyncMock(
        return_value=SimpleNamespace(
            tasks=(
                SimpleNamespace(result={}, interrupts=(answered,)),
                SimpleNamespace(result=None, interrupts=(outstanding,)),
            )
        )
    )

    pending = await detect_pending_interrupts(graph, {})

    assert pending == (outstanding,)


async def test_detect_pending_interrupts_keeps_sequential_partial_result() -> None:
    outstanding = pending_interrupt(id="int-a", value="next question")
    graph = MagicMock()
    graph.aget_state = AsyncMock(
        return_value=SimpleNamespace(
            tasks=(SimpleNamespace(result={}, interrupts=(outstanding,)),)
        )
    )

    pending = await detect_pending_interrupts(graph, {})

    assert pending == (outstanding,)


async def test_track_pending_interrupts_passes_through_and_accumulates() -> None:
    first = pending_interrupt(id="int-a", value="first")
    second = pending_interrupt(id="int-b", value="second")
    refreshed_second = pending_interrupt(id="int-b", value="second-updated")
    chunks = [
        ("updates", {"__interrupt__": (first,)}),
        ("updates", {"__interrupt__": (second,)}),
        ("updates", {"__interrupt__": (refreshed_second,)}),
        ("updates", {"node": {"messages": []}}),
    ]

    async def graph_stream() -> AsyncIterator[Any]:
        for chunk in chunks:
            yield chunk

    pending: list = []
    passed_through = [
        chunk async for chunk in track_pending_interrupts(graph_stream(), pending)
    ]

    assert passed_through == chunks
    assert pending == [first, refreshed_second]


def _sentinel_call(call_id: str, value: str = "Where are you?") -> ItemFunctionToolCall:
    """The ``function_call`` item the host emits for a pending interrupt."""
    return ItemFunctionToolCall(
        type="function_call",
        call_id=call_id,
        name=HITL_FUNCTION_NAME,
        arguments=json.dumps({"interrupt_id": call_id, "value": value}),
    )


def _tool_call(call_id: str, name: str) -> ItemFunctionToolCall:
    """An ordinary model-issued tool call."""
    return ItemFunctionToolCall(
        type="function_call",
        call_id=call_id,
        name=name,
        arguments="{}",
    )


def _tool_output(call_id: str, output: str) -> FunctionCallOutputItemParam:
    """The client's answer to either kind of call."""
    return FunctionCallOutputItemParam(
        type="function_call_output",
        call_id=call_id,
        output=output,
    )


def _approval_response(
    approval_request_id: str,
    approve: bool,
    reason: str | None = None,
) -> MCPApprovalResponse:
    return MCPApprovalResponse(
        type="mcp_approval_response",
        approval_request_id=approval_request_id,
        approve=approve,
        reason=reason,
    )


def _tool_call_names(messages: list) -> list[str]:
    """Every tool-call name across a message list."""
    return [
        call["name"]
        for message in messages
        for call in getattr(message, "tool_calls", None) or ()
    ]


class TestInterruptArgumentsJson:
    """The outbound ``{"interrupt_id", "value"}`` envelope."""

    def test_emits_envelope_for_strings(self) -> None:
        out = interrupt_arguments_json(pending_interrupt(id="int-1", value="Where?"))
        assert json.loads(out) == {"interrupt_id": "int-1", "value": "Where?"}

    def test_falls_back_for_non_serializable(self) -> None:
        class Opaque:
            def __str__(self) -> str:
                return "opaque-value"

        out = interrupt_arguments_json(pending_interrupt(id="int-1", value=Opaque()))
        assert json.loads(out) == {"interrupt_id": "int-1", "value": "opaque-value"}


class TestApprovalResumeChannel:
    """The ``mcp_approval_response`` resume channel."""

    def test_approve_true_returns_approval_object(self) -> None:
        pending = pending_interrupt(id="int-1", value={"question": "Where?"})
        items = [_approval_response("int-1", True)]
        command, consumed = parse_resume_command(items, (pending,))
        assert command is not None
        assert command.resume == {"approve": True}
        assert consumed == frozenset({"int-1"})

    def test_approve_false_returns_approval_object(self) -> None:
        pending = pending_interrupt(id="int-1")
        items = [_approval_response("int-1", False)]
        command, consumed = parse_resume_command(items, (pending,))
        assert command is not None
        assert command.resume == {"approve": False}
        assert consumed == frozenset({"int-1"})

    def test_approval_for_unknown_id_is_rejected(self) -> None:
        pending = pending_interrupt(id="int-1")
        items = [_approval_response("other", True)]
        with pytest.raises(ValueError, match="not a current pending interrupt"):
            parse_resume_command(items, (pending,))

    def test_preserves_the_complete_approval_response(self) -> None:
        pending = pending_interrupt(id="int-1", value={"action": "delete"})
        command, consumed = parse_resume_command(
            [_approval_response("int-1", False, reason="too risky")],
            (pending,),
        )

        assert command is not None
        assert command.resume == {"approve": False, "reason": "too risky"}
        assert consumed == frozenset({"int-1"})

    def test_rejects_unknown_and_duplicate_approval_ids(self) -> None:
        pending = (pending_interrupt(id="int-1"),)

        with pytest.raises(ValueError, match="not a current pending interrupt"):
            parse_resume_command([_approval_response("other", True)], pending)
        with pytest.raises(ValueError, match="Duplicate"):
            parse_resume_command(
                [
                    _approval_response("int-1", True),
                    _approval_response("int-1", False),
                ],
                pending,
            )

    def test_rejects_approval_mixed_with_new_input(self) -> None:
        with pytest.raises(ValueError, match="cannot be submitted with other input"):
            parse_resume_command(
                [_approval_response("int-1", True), {"type": "message"}],
                (pending_interrupt(id="int-1"),),
            )


class TestParallelInterruptResumeMap:
    """Several pauses outstanding at once.

    LangGraph refuses a bare resume value when more than one interrupt is
    pending: "When there are multiple pending interrupts, you must specify
    the interrupt id when resuming." So the converter must fold every
    matched item into an id-keyed resume map.
    https://docs.langchain.com/oss/python/langgraph/interrupts#handling-multiple-interrupts
    """

    def test_routes_approvals_by_id_not_position(self) -> None:
        pending = (
            pending_interrupt(id="int-a", value="a?"),
            pending_interrupt(id="int-b", value="b?"),
        )
        items = [
            _approval_response("int-b", False, reason="B"),
            _approval_response("int-a", True, reason="A"),
        ]
        command, consumed = parse_resume_command(items, pending)
        assert command is not None
        assert command.resume == {
            "int-a": {"approve": True, "reason": "A"},
            "int-b": {"approve": False, "reason": "B"},
        }
        assert consumed == frozenset({"int-a", "int-b"})

    def test_map_allows_partial_approvals(self) -> None:
        # Answering only one of two parallel pauses is legal; LangGraph keeps
        # the unanswered branch suspended.
        pending = (pending_interrupt(id="int-a"), pending_interrupt(id="int-b"))
        items = [_approval_response("int-a", True)]
        command, consumed = parse_resume_command(items, pending)
        assert command is not None
        assert command.resume == {"int-a": {"approve": True}}
        assert consumed == frozenset({"int-a"})

class TestApprovalIdRoundTrip:
    """``mcp_approval_request`` id encoding must survive the round trip.

    ``emit_interrupts`` cannot reuse the raw LangGraph interrupt id as the
    ``mcp_approval_request.id`` (storage requires an ``mcpr_*`` shape), so it
    encodes the interrupt id into the generated id. The inbound path must be
    able to recover it.
    """

    async def test_emit_interrupts_emits_one_approval_per_interrupt(self) -> None:
        items = await emitted_items(
            (
                pending_interrupt(id="int-a", value="a?"),
                pending_interrupt(id="int-b", value="b?"),
            )
        )
        function_calls = [it for it in items if it["type"] == "function_call"]
        approvals = [it for it in items if it["type"] == "mcp_approval_request"]

        assert function_calls == []
        assert len(approvals) == 2
        assert all(it["id"].startswith("mcpr_") for it in approvals)
        assert all(it["server_label"] == HITL_MCP_SERVER_LABEL for it in approvals)
        assert [json.loads(it["arguments"])["value"] for it in approvals] == [
            "a?",
            "b?",
        ]
        # And each approval id must be distinct so partial answers stay routable.
        assert approvals[0]["id"] != approvals[1]["id"]

    async def test_parse_resume_command_accepts_emitted_approval_id(self) -> None:
        pending = pending_interrupt(id="int-1", value="echo-me")
        items = await emitted_items((pending,))
        approval_id = next(
            it["id"] for it in items if it["type"] == "mcp_approval_request"
        )
        assert approval_id != "int-1"  # encoded, not the raw interrupt id

        command, consumed = parse_resume_command(
            [_approval_response(approval_id, True)],
            (pending,),
        )
        assert command is not None
        assert command.resume == {"approve": True}
        assert consumed == frozenset({approval_id})

class TestHitlSentinelFiltering:
    """Wire plumbing must never reach the model, pending or not.

    On the turn that consumes an interrupt the host strips the sentinel
    pair through the resume path's consumed-id set. But stateless clients
    echo the previous turn's output items back with every request, so the
    sentinel keeps arriving long after the pause closed — when nothing is
    pending and no consumed-id set exists. Filtering therefore keys off
    the reserved function name instead.
    """

    def test_reserves_only_the_hitl_function_name(self) -> None:
        items = [_sentinel_call("int-a"), _tool_call("call_1", "get_weather")]
        assert hitl_call_ids(items) == frozenset({"int-a"})

    def test_drops_an_echoed_sentinel_pair(self) -> None:
        # No skip_call_ids: this is a turn *after* the pause was consumed,
        # so the host has no consumed-id set left to filter with.
        items = [_sentinel_call("int-a"), _tool_output("int-a", '{"resume": "Paris"}')]
        assert build_messages_input(items)["messages"] == []

    def test_keeps_ordinary_tool_round_trips(self) -> None:
        items = [_tool_call("call_1", "get_weather"), _tool_output("call_1", "sunny")]
        messages = build_messages_input(items)["messages"]
        assert _tool_call_names(messages) == ["get_weather"]
        assert messages[-1].content == "sunny"

    def test_drops_only_the_sentinel_when_both_are_present(self) -> None:
        # Adjacent function_call items are folded into one AIMessage, so
        # the sentinel has to be dropped without taking the real call with
        # it or leaving its answer behind as an orphan.
        items = [
            _sentinel_call("int-a"),
            _tool_call("call_1", "get_weather"),
            _tool_output("int-a", '{"resume": "Paris"}'),
            _tool_output("call_1", "sunny"),
        ]
        messages = build_messages_input(items)["messages"]
        assert _tool_call_names(messages) == ["get_weather"]
        assert [m.content for m in messages] == ["", "sunny"]
