"""Submit rich decisions to the HITL middleware sample."""

from __future__ import annotations

import argparse
import json
import uuid
from typing import Any

import httpx


def post(base_url: str, body: dict[str, Any]) -> dict[str, Any]:
    """Post a Responses request and print its payload."""
    response = httpx.post(f"{base_url.rstrip('/')}/responses", json=body, timeout=120)
    response.raise_for_status()
    payload = response.json()
    print(json.dumps(payload, indent=2))
    return payload


def main() -> None:
    """Request two tools, approve the first, and reject the second."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    args = parser.parse_args()

    conversation_id = f"hitl-middleware-{uuid.uuid4().hex[:8]}"
    first = post(
        args.base_url,
        {
            "input": "Call get_weather and get_local_time for Seattle in parallel.",
            "conversation": {"id": conversation_id},
        },
    )
    interrupt_calls = [
        item
        for item in first["output"]
        if item.get("type") == "function_call"
        and item.get("name") == "__hosted_agent_adapter_interrupt__"
    ]
    if len(interrupt_calls) != 1:
        raise RuntimeError(
            f"Expected one middleware interrupt, got {len(interrupt_calls)}"
        )
    interrupt_call = interrupt_calls[0]
    actions = json.loads(interrupt_call["arguments"])["value"]["action_requests"]
    action_names = [action["name"] for action in actions]
    if len(actions) != 2 or set(action_names) != {"get_weather", "get_local_time"}:
        raise RuntimeError(f"Expected both sample tools, got {action_names}")
    decisions = [
        {"type": "approve"}
        if action["name"] == "get_weather"
        else {"type": "reject", "message": "Local time access denied"}
        for action in actions
    ]
    post(
        args.base_url,
        {
            "conversation": {"id": conversation_id},
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": interrupt_call["call_id"],
                    "output": json.dumps({"resume": {"decisions": decisions}}),
                }
            ],
        },
    )


if __name__ == "__main__":
    main()
