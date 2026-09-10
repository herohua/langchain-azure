# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""End-to-end tests for MCP approval of ordinary LangGraph interrupts."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("azure.ai.agentserver.responses")
pytest.importorskip("starlette")

from langchain_core.messages import AIMessage

from langchain_azure_ai.agents.hosting import ResponsesHostServer
from langchain_azure_ai.agents.hosting._converters import (
    HITL_MCP_SERVER_LABEL,
)

from .conftest import (
    REAL_INTERRUPT_ASYNC_XFAIL,
    ScriptRegistrar,
    approval_requests,
    assistant_text,
    client_for,
    hitl_items_in,
    sentinels,
    sse_payloads,
)
from .graphs import (
    build_ask_human_graph,
    build_simple_interrupt_graph,
)


class TestInterruptEmission:
    """A pause must surface as one resumable approval item."""

    @REAL_INTERRUPT_ASYNC_XFAIL
    def test_emits_approval_and_resumes(self, script: ScriptRegistrar) -> None:
        key = "hitl-test"
        script(
            key,
            [
                # Turn 1: model decides to ask the user for their location.
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "call_ask_1",
                            "name": "AskHuman",
                            "args": {"question": "Where are you?"},
                        }
                    ],
                ),
                # Turn 2 (after resume): model produces the final answer.
                AIMessage(content="It's sunny in Seattle."),
            ],
        )
        host = ResponsesHostServer(build_ask_human_graph(key))
        conversation_id = "conv-hitl-1"

        with client_for(host) as client:
            # 1. Initial turn — graph pauses with one MCP approval request.
            first = client.post(
                "/responses",
                json={
                    "input": "Look up the weather where I am.",
                    "conversation": {"id": conversation_id},
                },
            )
            assert first.status_code == 200, first.text
            first_payload = first.json()
            assert first_payload["status"] == "completed"
            assert not [
                item
                for item in first_payload["output"]
                if item.get("type") == "function_call"
                and item.get("name") == "__hosted_agent_adapter_interrupt__"
            ]
            approvals = approval_requests(first_payload)
            assert len(approvals) == 1, first_payload
            assert approvals[0]["id"].startswith("mcpr_")
            assert approvals[0]["server_label"] == HITL_MCP_SERVER_LABEL
            envelope = json.loads(approvals[0]["arguments"])
            assert envelope["value"] == "Where are you?"

            # 2. Resume with the complete approval object.
            second = client.post(
                "/responses",
                json={
                    "conversation": {"id": conversation_id},
                    "input": [
                        {
                            "type": "mcp_approval_response",
                            "approval_request_id": approvals[0]["id"],
                            "approve": True,
                            "reason": "Seattle",
                        }
                    ],
                },
            )
            assert second.status_code == 200, second.text
            second_payload = second.json()
            assert second_payload["status"] == "completed"
            # No new pending interrupt this time.
            assert not approval_requests(second_payload), second_payload
            # And we should see the final assistant message text.
            assert "Seattle" in assistant_text(second_payload)


class TestApprovalIdMismatch:
    """Invalid approvals fail before graph execution."""

    @REAL_INTERRUPT_ASYNC_XFAIL
    def test_fails_without_driving_graph_when_id_is_unknown(
        self, script: ScriptRegistrar
    ) -> None:
        """An unknown approval ID fails the request and leaves Graph paused."""
        key = "hitl-bad-resume"
        remaining = script(
            key,
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "call_ask_bad",
                            "name": "AskHuman",
                            "args": {"question": "Where?"},
                        }
                    ],
                ),
                # Second AIMessage should NEVER be consumed — the host must not
                # drive the graph on the bad-resume turn.
                AIMessage(content="should not be reached"),
            ],
        )
        host = ResponsesHostServer(build_ask_human_graph(key))
        conversation_id = "conv-bad-resume"
        with client_for(host) as client:
            first = client.post(
                "/responses",
                json={
                    "input": "ask me",
                    "conversation": {"id": conversation_id},
                },
            )
            assert first.status_code == 200, first.text
            assert len(approval_requests(first.json())) == 1

            second = client.post(
                "/responses",
                json={
                    "conversation": {"id": conversation_id},
                    "input": [
                        {
                            "type": "mcp_approval_response",
                            "approval_request_id": "mcpr_unknown",
                            "approve": True,
                        }
                    ],
                },
            )
            assert second.status_code == 200, second.text
            payload = second.json()
            assert payload["status"] == "failed"
            assert payload["error"]["code"] == "invalid_hitl_input"
            # And no spurious assistant message from a second LLM call.
            assert not [it for it in payload["output"] if it.get("type") == "message"]
        # The second scripted AIMessage must remain un-consumed because
        # the graph was not driven on the bad-resume turn.
        assert len(remaining) == 1


class TestMcpApprovalChannel:
    """Resuming a turn via ``mcp_approval_response``."""

    @REAL_INTERRUPT_ASYNC_XFAIL
    def test_approve_resumes_the_graph(self, script: ScriptRegistrar) -> None:
        """An approval resumes the graph with ``{"approve": True}``."""
        key = "hitl-approve"
        script(
            key,
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "call_ask_approve",
                            "name": "AskHuman",
                            "args": {"question": "Confirm: run weather lookup?"},
                        }
                    ],
                ),
                AIMessage(content="OK, lookup completed."),
            ],
        )
        host = ResponsesHostServer(build_ask_human_graph(key))
        conversation_id = "conv-approve"
        with client_for(host) as client:
            first = client.post(
                "/responses",
                json={
                    "input": "do the thing",
                    "conversation": {"id": conversation_id},
                },
            )
            assert first.status_code == 200, first.text
            approvals = approval_requests(first.json())
            assert len(approvals) == 1
            approval_id = approvals[0]["id"]

            second = client.post(
                "/responses",
                json={
                    "conversation": {"id": conversation_id},
                    "input": [
                        {
                            "type": "mcp_approval_response",
                            "approval_request_id": approval_id,
                            "approve": True,
                        }
                    ],
                },
            )
            assert second.status_code == 200, second.text
            payload = second.json()
            assert payload["status"] == "completed"
            # No new pending interrupt this time.
            assert not sentinels(payload), payload
            assert not approval_requests(payload), payload
            assert "lookup completed" in assistant_text(payload)

    @REAL_INTERRUPT_ASYNC_XFAIL
    def test_reject_resumes_the_graph(self, script: ScriptRegistrar) -> None:
        """A rejection and its reason are delivered to the graph."""
        key = "hitl-reject"
        remaining = script(
            key,
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "call_ask_reject",
                            "name": "AskHuman",
                            "args": {"question": "Confirm: irreversible action?"},
                        }
                    ],
                ),
                AIMessage(content="should not be reached"),
            ],
        )
        host = ResponsesHostServer(build_ask_human_graph(key))
        conversation_id = "conv-reject"
        with client_for(host) as client:
            first = client.post(
                "/responses",
                json={
                    "input": "do something risky",
                    "conversation": {"id": conversation_id},
                },
            )
            assert first.status_code == 200, first.text
            approvals = approval_requests(first.json())
            assert len(approvals) == 1
            approval_id = approvals[0]["id"]

            second = client.post(
                "/responses",
                json={
                    "conversation": {"id": conversation_id},
                    "input": [
                        {
                            "type": "mcp_approval_response",
                            "approval_request_id": approval_id,
                            "approve": False,
                            "reason": "user said no",
                        }
                    ],
                },
            )
            assert second.status_code == 200, second.text
            payload = second.json()
            assert payload["status"] == "completed", payload
            assert "should not be reached" in assistant_text(payload)
        assert not remaining


class TestThreadScoping:
    """Resume is scoped to the thread that paused.

    https://docs.langchain.com/oss/python/langgraph/interrupts#resuming-interrupts
    """

    @REAL_INTERRUPT_ASYNC_XFAIL
    def test_does_not_resume_a_pause_from_another_conversation(self) -> None:
        """An interrupt id is only meaningful on the thread that produced it.

        Conversation id maps to LangGraph's ``thread_id``, so replaying a
        resume item against a different conversation must not reach into the
        original thread. The second conversation starts its own run (and its
        own pause), and the first stays resumable.
        """
        host = ResponsesHostServer(build_simple_interrupt_graph())
        with client_for(host) as client:
            first = client.post(
                "/responses",
                json={"input": "hi", "conversation": {"id": "conv-thread-a"}},
            )
            assert first.status_code == 200, first.text
            pending = approval_requests(first.json())
            assert len(pending) == 1, first.json()
            approval_id = pending[0]["id"]

            # Same approval, wrong thread: no pending ID matches, so the whole
            # request fails without executing that thread's graph.
            other = client.post(
                "/responses",
                json={
                    "conversation": {"id": "conv-thread-b"},
                    "input": [
                        {
                            "type": "mcp_approval_response",
                            "approval_request_id": approval_id,
                            "approve": True,
                        }
                    ],
                },
            )
            assert other.status_code == 200, other.text
            other_payload = other.json()
            assert other_payload["status"] == "failed", other_payload
            assert other_payload["error"]["code"] == "invalid_hitl_input"

            # The original thread is untouched and still resumable.
            resumed = client.post(
                "/responses",
                json={
                    "conversation": {"id": "conv-thread-a"},
                    "input": [
                        {
                            "type": "mcp_approval_response",
                            "approval_request_id": approval_id,
                            "approve": True,
                        }
                    ],
                },
            )
            assert resumed.status_code == 200, resumed.text
            payload = resumed.json()
            assert payload["status"] == "completed", payload
            assert not approval_requests(payload), payload
            assert "'approve': True" in assistant_text(payload), payload


class TestStreaming:
    """Streaming with human-in-the-loop interrupts.

    https://docs.langchain.com/oss/python/langgraph/interrupts#stream-with-human-in-the-loop-hitl-interrupts
    """

    @REAL_INTERRUPT_ASYNC_XFAIL
    def test_streams_approval_and_resumes(self) -> None:
        """Streaming exposes and resumes the same MCP approval contract."""
        host = ResponsesHostServer(build_simple_interrupt_graph())
        conversation_id = "conv-stream-hitl"
        with client_for(host) as client:
            first = client.post(
                "/responses",
                json={
                    "input": "hi",
                    "conversation": {"id": conversation_id},
                    "stream": True,
                },
            )
            assert first.status_code == 200, first.text
            items = hitl_items_in(sse_payloads(first.text))
            assert items, first.text
            assert {item["type"] for item in items} == {"mcp_approval_request"}
            approval_id = items[0]["id"]

            second = client.post(
                "/responses",
                json={
                    "conversation": {"id": conversation_id},
                    "input": [
                        {
                            "type": "mcp_approval_response",
                            "approval_request_id": approval_id,
                            "approve": True,
                            "reason": "Ada",
                        }
                    ],
                    "stream": True,
                },
            )
            assert second.status_code == 200, second.text
            assert not hitl_items_in(sse_payloads(second.text)), second.text
            assert "approve" in second.text, second.text
