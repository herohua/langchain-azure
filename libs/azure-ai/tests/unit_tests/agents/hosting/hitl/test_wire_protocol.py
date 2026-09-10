# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""End-to-end tests for the HITL wire protocol between client and host.

Covers what the client actually sees and sends: the paired sentinel items a
pause emits, the two resume channels, recovery from a mismatched resume id,
streaming parity, and thread scoping.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("azure.ai.agentserver.responses")
pytest.importorskip("starlette")

from langchain_core.messages import AIMessage

from langchain_azure_ai.agents.hosting import ResponsesHostServer
from langchain_azure_ai.agents.hosting._converters import (
    HITL_FUNCTION_NAME,
)

from .conftest import (
    REAL_INTERRUPT_ASYNC_XFAIL,
    ScriptRegistrar,
    approval_requests,
    assistant_text,
    client_for,
    hitl_items_in,
    resume_item,
    sentinel_item,
    sentinels,
    sse_payloads,
)
from .graphs import (
    ScriptedModel,
    build_ask_human_graph,
    build_mcp_approval_interrupt_graph,
    build_simple_interrupt_graph,
)


class TestInterruptEmission:
    """An ordinary pause uses the function-call resume channel."""

    @REAL_INTERRUPT_ASYNC_XFAIL
    def test_emits_function_call_and_resumes(self, script: ScriptRegistrar) -> None:
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
            # 1. Initial turn — graph should pause and the response should
            #    expose a __hosted_agent_adapter_interrupt__ function_call item.
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
            interrupts = sentinels(first_payload)
            assert len(interrupts) == 1, first_payload
            envelope = json.loads(interrupts[0]["arguments"])
            assert envelope["value"] == "Where are you?"
            call_id = interrupts[0]["call_id"]
            assert call_id  # LangGraph interrupt id
            assert envelope["interrupt_id"] == call_id

            approvals = approval_requests(first_payload)
            assert approvals == [], first_payload

            # 2. Resume turn — submit a function_call_output keyed by the
            #    interrupt id. The host should resume the graph and return
            #    the assistant's final message.
            second = client.post(
                "/responses",
                json={
                    "conversation": {"id": conversation_id},
                    "input": [resume_item(call_id, "Seattle")],
                },
            )
            assert second.status_code == 200, second.text
            second_payload = second.json()
            assert second_payload["status"] == "completed"
            # No new pending interrupt this time.
            assert not sentinels(second_payload), second_payload
            # And we should see the final assistant message text.
            assert "Seattle" in assistant_text(second_payload)


class TestEchoedSentinelItems:
    """Clients that replay output items must not poison the model context.

    The stateless Responses pattern is to resend the previous turn's
    output items alongside the new input. That echoes the HITL sentinel
    back to us on every later turn, long after the interrupt was consumed
    and the resume path stopped filtering it by consumed id.
    """

    @REAL_INTERRUPT_ASYNC_XFAIL
    def test_echoed_sentinel_never_reaches_the_model(
        self, script: ScriptRegistrar
    ) -> None:
        key = "hitl-echo"
        script(
            key,
            [
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
                AIMessage(content="It's sunny in Seattle."),
                AIMessage(content="Happy to help."),
            ],
        )
        host = ResponsesHostServer(build_ask_human_graph(key))
        conversation_id = "conv-hitl-echo"

        with client_for(host) as client:
            first = client.post(
                "/responses",
                json={
                    "input": "Look up the weather where I am.",
                    "conversation": {"id": conversation_id},
                },
            )
            assert first.status_code == 200, first.text
            call_id = sentinels(first.json())[0]["call_id"]

            # Resume, echoing the sentinel the way a stateless client
            # would. The pause is still open here, so the consumed-id set
            # is what keeps the pair out of the message channel.
            second = client.post(
                "/responses",
                json={
                    "conversation": {"id": conversation_id},
                    "input": [
                        sentinel_item(call_id, "Where are you?"),
                        resume_item(call_id, "Seattle"),
                    ],
                },
            )
            assert second.status_code == 200, second.text
            assert "Seattle" in assistant_text(second.json())

            # A later turn still carrying the echo. Nothing is pending now,
            # so only the reserved-name filter stands between the sentinel
            # and the model.
            third = client.post(
                "/responses",
                json={
                    "conversation": {"id": conversation_id},
                    "input": [
                        sentinel_item(call_id, "Where are you?"),
                        resume_item(call_id, "Seattle"),
                    ],
                },
            )
            assert third.status_code == 200, third.text
            assert third.json()["status"] == "completed", third.text
            assert not sentinels(third.json()), third.text

        turns = ScriptedModel.seen[key]
        assert len(turns) == 3, turns
        for turn in turns:
            reserved = [
                call
                for message in turn
                for call in getattr(message, "tool_calls", None) or ()
                if call["name"] == HITL_FUNCTION_NAME
            ]
            assert not reserved, turn


class TestResumeCallIdMismatch:
    """Recovery when the client answers with the wrong id."""

    @REAL_INTERRUPT_ASYNC_XFAIL
    def test_reemits_sentinel_when_a_pause_is_outstanding(
        self, script: ScriptRegistrar
    ) -> None:
        """Pending interrupt + wrong-call_id resume → host re-emits the
        sentinel instead of driving the graph with a malformed message list.

        This is the recovery path for a client that echoed the wrong
        function_call's call_id on resume (e.g. the LLM's ``AskHuman`` id
        instead of the interrupt sentinel id).
        """
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
            interrupt_items = sentinels(first.json())
            assert len(interrupt_items) == 1
            sentinel_call_id = interrupt_items[0]["call_id"]

            # Client mistakenly echoes the AskHuman call_id instead of
            # the sentinel.
            second = client.post(
                "/responses",
                json={
                    "conversation": {"id": conversation_id},
                    "input": [
                        {
                            "type": "function_call_output",
                            "call_id": "call_ask_bad",
                            "output": '{"resume": "Seattle"}',
                        }
                    ],
                },
            )
            assert second.status_code == 200, second.text
            payload = second.json()
            assert payload["status"] == "completed"
            # Host re-emits the same pending sentinel.
            reemitted = sentinels(payload)
            assert len(reemitted) == 1
            assert reemitted[0]["call_id"] == sentinel_call_id
            assert approval_requests(payload) == []
            # And no spurious assistant message from a second LLM call.
            assert not [it for it in payload["output"] if it.get("type") == "message"]
        # The second scripted AIMessage must remain un-consumed because
        # the graph was not driven on the bad-resume turn.
        assert len(remaining) == 1


class TestMcpApprovalChannel:
    """Resuming an explicitly typed MCP approval interrupt."""

    @REAL_INTERRUPT_ASYNC_XFAIL
    def test_approve_resumes_the_graph(self) -> None:
        host = ResponsesHostServer(build_mcp_approval_interrupt_graph())
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
            assert "'approve': True" in assistant_text(payload)

    @REAL_INTERRUPT_ASYNC_XFAIL
    def test_reject_resumes_the_graph(self) -> None:
        host = ResponsesHostServer(build_mcp_approval_interrupt_graph())
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
            assert "'approve': False" in assistant_text(payload)
            assert "user said no" in assistant_text(payload)


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
            pending = sentinels(first.json())
            assert len(pending) == 1, first.json()
            call_id = pending[0]["call_id"]

            # Same resume item, wrong thread: nothing is pending there, so it
            # is treated as ordinary input and that thread pauses on its own.
            other = client.post(
                "/responses",
                json={
                    "conversation": {"id": "conv-thread-b"},
                    "input": [resume_item(call_id, "Ada")],
                },
            )
            assert other.status_code == 200, other.text
            other_payload = other.json()
            other_sentinels = sentinels(other_payload)
            assert len(other_sentinels) == 1, other_payload
            assert other_sentinels[0]["call_id"] != call_id, other_payload
            assert "ok:Ada" not in assistant_text(other_payload), other_payload

            # The original thread is untouched and still resumable.
            resumed = client.post(
                "/responses",
                json={
                    "conversation": {"id": "conv-thread-a"},
                    "input": [resume_item(call_id, "Ada")],
                },
            )
            assert resumed.status_code == 200, resumed.text
            payload = resumed.json()
            assert payload["status"] == "completed", payload
            assert not sentinels(payload), payload
            assert "ok:Ada" in assistant_text(payload), payload


class TestStreaming:
    """Streaming with human-in-the-loop interrupts.

    https://docs.langchain.com/oss/python/langgraph/interrupts#stream-with-human-in-the-loop-hitl-interrupts
    """

    @REAL_INTERRUPT_ASYNC_XFAIL
    def test_streams_function_call_and_resumes(self) -> None:
        """Streaming clients must see the same two channels, and be able to
        resume over the streaming endpoint too.

        The buffered counterpart is ``TestInterruptEmission``; this pins that
        both item types survive SSE framing and that a streamed resume
        completes the run.
        """
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
            assert not [
                item for item in items if item["type"] == "mcp_approval_request"
            ]
            call_ids = {
                item["call_id"] for item in items if item["type"] == "function_call"
            }
            assert len(call_ids) == 1, first.text
            call_id = call_ids.pop()

            second = client.post(
                "/responses",
                json={
                    "conversation": {"id": conversation_id},
                    "input": [resume_item(call_id, "Ada")],
                    "stream": True,
                },
            )
            assert second.status_code == 200, second.text
            assert not hitl_items_in(sse_payloads(second.text)), second.text
            assert "ok:Ada" in second.text, second.text
