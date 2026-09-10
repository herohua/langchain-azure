# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import pytest

pytest.importorskip("azure.ai.agentserver.responses")

from langgraph.types import Interrupt

from langchain_azure_ai.agents.hosting._converters import (
    HITL_FUNCTION_NAME,
    interrupt_output_items,
    parse_resume_command,
)


def _mcp_interrupt(interrupt_id: str = "int-1") -> Interrupt:
    return Interrupt(
        id=interrupt_id,
        value=[
            {
                "type": "mcp_approval_request",
                "id": "approval-1",
                "server_label": "files",
                "tool_name": "read_file",
                "arguments": '{"path": "README.md"}',
            }
        ],
    )


def test_interrupt_protocols_are_distinct() -> None:
    ordinary, mcp = interrupt_output_items(
        (Interrupt(id="ordinary", value="question"), _mcp_interrupt())
    )

    assert ordinary["type"] == "function_call"
    assert ordinary["name"] == HITL_FUNCTION_NAME
    assert mcp["type"] == "mcp_approval_request"
    assert mcp["name"] == "read_file"
    assert "tool_name" not in mcp


@pytest.mark.parametrize(
    ("approve", "reason"),
    [(True, None), (False, "not allowed")],
)
def test_mcp_response_resumes_with_complete_decision(
    approve: bool, reason: str | None
) -> None:
    item = {
        "type": "mcp_approval_response",
        "approval_request_id": "approval-1",
        "approve": approve,
    }
    if reason is not None:
        item["reason"] = reason

    command, consumed = parse_resume_command([item], (_mcp_interrupt(),))

    assert command is not None
    assert command.resume == {
        "approve": approve,
        **({"reason": reason} if reason is not None else {}),
    }
    assert consumed == frozenset({"approval-1"})


def test_mcp_response_rejects_unknown_and_duplicate_ids() -> None:
    item = {
        "type": "mcp_approval_response",
        "approval_request_id": "unknown",
        "approve": True,
    }
    with pytest.raises(ValueError, match="not pending"):
        parse_resume_command([item], (_mcp_interrupt(),))

    item["approval_request_id"] = "approval-1"
    with pytest.raises(ValueError, match="more than once"):
        parse_resume_command([item, item], (_mcp_interrupt(),))
