# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Select the exact completed parent checkpoint for a new Responses request."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from azure.ai.agentserver.responses import (
    ResponseContext,
    ResponseEventStream,
    ResponseProviderProtocol,
)
from azure.ai.agentserver.responses.models._errors import RequestValidationError
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver

from .checkpoint_ref import CheckpointRef
from .hosting_runnable_config import HostingRunnableConfig, _scope_thread_id
from .task_storage_manager import TaskStorageManager

if TYPE_CHECKING:
    from azure.ai.agentserver.responses.models import CreateResponse
    from langgraph.graph.state import CompiledStateGraph
    from langgraph.types import StateSnapshot


def validate_branching_saver(saver: object) -> BaseCheckpointSaver:
    """Reject missing or unimplemented async saver operations at startup."""
    if not isinstance(saver, BaseCheckpointSaver):
        raise ValueError("Response branching requires a LangGraph checkpointer.")
    if any(
        getattr(saver.__class__, name, None) is getattr(BaseCheckpointSaver, name)
        for name in ("aget_tuple", "aput", "aput_writes", "alist")
    ):
        raise ValueError(
            "Response branching requires asynchronous checkpoint reads, "
            "writes, pending writes, and history."
        )
    return saver


async def validate_branch_checkpoint(
    graph: CompiledStateGraph, config: RunnableConfig
) -> StateSnapshot:
    """Require a restorable exact root boundary, not a paused or unfinished graph."""
    state = await graph.aget_state(config)
    if (
        HostingRunnableConfig(state.config).checkpoint_ref
        != HostingRunnableConfig(config).checkpoint_ref
        or (state.config.get("configurable") or {}).get("checkpoint_ns", "") != ""
        or state.created_at is None
    ):
        raise RuntimeError("The parent's graph checkpoint could not be restored.")
    if state.next or state.tasks or state.interrupts:
        raise RequestValidationError(
            "The previous response's graph has not finished.",
            param="previous_response_id",
        )
    return state


async def select_branch_checkpoint(
    request: CreateResponse,
    context: ResponseContext,
) -> CheckpointRef:
    """Resolve and validate the selected completed parent."""
    parent_id = request.get("previous_response_id")
    if not isinstance(parent_id, str) or not parent_id.strip():
        raise RequestValidationError(
            "previous_response_id must be a nonempty string.",
            param="previous_response_id",
        )
    if request.get("conversation") is not None or context.conversation_id:
        raise RequestValidationError(
            "previous_response_id and conversation are mutually exclusive.",
            param="previous_response_id",
        )
    provider = getattr(context, "_provider", None)
    if not isinstance(provider, ResponseProviderProtocol):
        raise RuntimeError("The Responses host has no response storage provider.")
    platform = context.platform_context
    try:
        parent = await provider.get_response(parent_id, context=platform)
    except KeyError as exc:
        raise RequestValidationError(
            "The previous response was not found or was not retained.",
            code="previous_response_not_found",
            param="previous_response_id",
        ) from exc
    if parent.get("id") != parent_id:
        raise RuntimeError("The response provider returned a different parent.")
    storage = TaskStorageManager.from_stream(
        ResponseEventStream(response_id=parent_id, response=parent)
    )
    reference = storage.checkpoint_ref
    if reference is not None:
        namespace = _scope_thread_id("", context)
        if (namespace and not reference.thread_id.startswith(namespace)) or (
            not namespace and re.match(r"user-[0-9a-f]{64}:", reference.thread_id)
        ):
            raise RequestValidationError(
                "The previous response was not found or was not retained.",
                code="previous_response_not_found",
                param="previous_response_id",
            )
    if parent.get("status") != "completed":
        raise RequestValidationError(
            "The previous response must be completed.",
            param="previous_response_id",
        )
    output = parent.get("output") or []
    answered = {
        item.get("call_id")
        for item in output
        if item.get("type") == "function_call_output"
    }
    if any(
        item.get("type") == "mcp_approval_request"
        or (item.get("type") == "function_call" and item.get("call_id") not in answered)
        for item in output
    ):
        raise RequestValidationError(
            "The previous response is awaiting tool results or approval.",
            param="previous_response_id",
        )
    if reference is None:
        raise RuntimeError("The parent has no saved graph checkpoint.")
    return reference
