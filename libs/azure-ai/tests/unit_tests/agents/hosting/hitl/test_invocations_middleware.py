# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Invocations-host coverage for LangChain HITL middleware decisions."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("azure.ai.agentserver.invocations")
pytest.importorskip("starlette")

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from langchain_azure_ai.agents.hosting import InvocationsHostServer  # noqa: E402

from .conftest import REAL_INTERRUPT_ASYNC_XFAIL, ScriptRegistrar  # noqa: E402
from .graphs import ScriptedModel, build_hitl_middleware_graph  # noqa: E402


@pytest.mark.parametrize(
    ("approve", "expected_calls", "expected_text"),
    [
        (True, ["approved"], "approved"),
        (False, [], "rejected"),
    ],
)
@REAL_INTERRUPT_ASYNC_XFAIL
def test_invocations_host_resumes_hitl_middleware(
    script: ScriptRegistrar,
    approve: bool,
    expected_calls: list[str],
    expected_text: str,
) -> None:
    key = f"invocations-hitl-{approve}"
    script(
        key,
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_risky",
                        "name": "risky_tool",
                        "args": {"value": "approved"},
                    }
                ],
            ),
            AIMessage(content=f"The action was {expected_text}."),
        ],
    )
    tool_calls: list[str] = []
    server = InvocationsHostServer(build_hitl_middleware_graph(key, tool_calls))
    session_id = f"middleware-{approve}"

    with TestClient(server.app) as client:
        first = client.post(
            f"/invocations?agent_session_id={session_id}",
            json={"message": "do it"},
        )
        approval = next(
            item
            for item in first.json()["output"]
            if item.get("type") == "mcp_approval_request"
        )
        decision: dict[str, Any] = {
            "type": "mcp_approval_response",
            "approval_request_id": approval["id"],
            "approve": approve,
        }
        if not approve:
            decision["reason"] = "Denied"
        resumed = client.post(
            f"/invocations?agent_session_id={session_id}",
            json={"message": [decision]},
        )

    assert resumed.status_code == 200, resumed.text
    assert expected_text in resumed.json()["response"]
    assert tool_calls == expected_calls
    if not approve:
        rejection_messages = [
            message
            for turn in ScriptedModel.seen[key]
            for message in turn
            if isinstance(message, ToolMessage) and message.status == "error"
        ]
        assert rejection_messages
        assert all("Denied" in str(message.content) for message in rejection_messages)
