# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Invocations-host coverage for LangChain HITL middleware decisions."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("azure.ai.agentserver.invocations")
pytest.importorskip("starlette")

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from langchain_azure_ai.agents.hosting import InvocationsHostServer  # noqa: E402

from .conftest import REAL_INTERRUPT_ASYNC_XFAIL, ScriptRegistrar  # noqa: E402
from .graphs import ScriptedModel, build_hitl_middleware_graph  # noqa: E402


@REAL_INTERRUPT_ASYNC_XFAIL
def test_invocations_host_accepts_mixed_middleware_decisions(
    script: ScriptRegistrar,
) -> None:
    key = "invocations-hitl-mixed"
    script(
        key,
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_approved",
                        "name": "approved_tool",
                        "args": {"value": "A"},
                    },
                    {
                        "id": "call_rejected",
                        "name": "rejected_tool",
                        "args": {"value": "B"},
                    },
                ],
            ),
            AIMessage(content="Mixed review completed."),
        ],
    )
    tool_calls: list[str] = []
    server = InvocationsHostServer(build_hitl_middleware_graph(key, tool_calls))

    with TestClient(server.app) as client:
        first = client.post(
            "/invocations?agent_session_id=middleware-mixed",
            json={"message": "review both"},
        )
        interrupt_call = next(
            item
            for item in first.json()["output"]
            if item.get("name") == "__hosted_agent_adapter_interrupt__"
        )
        resumed = client.post(
            "/invocations?agent_session_id=middleware-mixed",
            json={
                "message": [
                    {
                        "type": "function_call_output",
                        "call_id": interrupt_call["call_id"],
                        "output": json.dumps(
                            {
                                "resume": {
                                    "decisions": [
                                        {"type": "approve"},
                                        {"type": "reject", "message": "Denied B"},
                                    ]
                                }
                            }
                        ),
                    }
                ]
            },
        )

    assert resumed.status_code == 200, resumed.text
    assert "Mixed review completed" in resumed.json()["response"]
    assert tool_calls == ["approved:A"]
    rejection_messages = [
        message
        for turn in ScriptedModel.seen[key]
        for message in turn
        if isinstance(message, ToolMessage) and message.status == "error"
    ]
    assert rejection_messages
    assert all("Denied B" in str(message.content) for message in rejection_messages)
