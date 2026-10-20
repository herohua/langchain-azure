"""Request-local checkpoint reuse for an exclusively owned offline fixture.

This is a POC, not a deployment-wide ownership implementation. All writers
must use the same saver and event loop, against a private fixture backend.
"""

import asyncio
from contextlib import aclosing, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from azure.ai.agentserver.core import get_request_context
from langchain_azure_ai.agents.hosting import (
    FoundryCheckpointSaver,
    ResponsesHostServer,
)
from langgraph.checkpoint.base import CheckpointTuple, get_checkpoint_id


@dataclass
class _Scope:
    active: bool = True
    enabled: bool = True
    generation: int = 0
    mutating: int = 0
    key: tuple | None = None
    data: tuple[str, bytes] | None = None

    def invalidate(self):
        self.generation += 1
        self.key = None
        self.data = None


class ExclusiveFixtureSaver(FoundryCheckpointSaver):
    """Serialize fixture writers; reuse only a verified current checkpoint.

    shortcut: one private backend and event loop, require a distributed lease
    covering every writer before using this design with shared storage.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ownership = asyncio.Lock()
        self._scope = ContextVar(f"checkpoint_reuse_{id(self)}", default=None)

    @asynccontextmanager
    async def exclusive_request(self):
        """Own the fixture from reference selection through reference update."""
        if self._active_scope() is not None:
            raise RuntimeError("Nested ownership scopes are not supported")
        async with self._ownership:
            scope = _Scope()
            token = self._scope.set(scope)
            try:
                yield
            finally:
                scope.active = False
                scope.invalidate()
                self._scope.reset(token)

    def _active_scope(self):
        scope = self._scope.get()
        return scope if scope is not None and scope.active else None

    async def aget_tuple(self, config):
        scope = self._active_scope()
        if scope is None or not scope.enabled or scope.mutating:
            return await super().aget_tuple(config)
        thread, namespace = self._thread_and_namespace(config)
        identity = get_request_context()
        key = (
            identity.user_id,
            identity.call_id,
            identity.session_id,
            thread,
            namespace,
        )
        checkpoint_id = get_checkpoint_id(config)
        generation = scope.generation
        try:
            if scope.key != key or scope.data is None:
                # Exact identity alone cannot answer LangGraph's latest check.
                latest = await super().aget_tuple(
                    {"configurable": {"thread_id": thread, "checkpoint_ns": namespace}}
                )
                if latest is None or (
                    checkpoint_id and get_checkpoint_id(latest.config) != checkpoint_id
                ):
                    scope.enabled = False
                    scope.invalidate()
                    return await super().aget_tuple(config) if checkpoint_id else latest
                data = self.serde.dumps_typed(list(latest))
                if (
                    scope.active
                    and scope.generation == generation
                    and not scope.mutating
                ):
                    scope.key, scope.data = key, data
            else:
                data = scope.data
            values = self.serde.loads_typed(data)
            values[4] = [tuple(write) for write in values[4]]
            result = CheckpointTuple(*values)
            if checkpoint_id and get_checkpoint_id(result.config) != checkpoint_id:
                # Historical replay follows the normal path, including freshness.
                scope.enabled = False
                scope.invalidate()
                return await super().aget_tuple(config)
            return result
        except BaseException:
            scope.enabled = False
            scope.invalidate()
            raise

    @asynccontextmanager
    async def _mutation(self):
        scope = self._active_scope()
        if scope is None:
            async with self._ownership:
                yield
        else:
            # Invalidate on both sides so an overlapping read cannot refill.
            scope.invalidate()
            scope.mutating += 1
            try:
                yield
            finally:
                scope.mutating -= 1
                scope.invalidate()

    async def aput(self, config, checkpoint, metadata, new_versions):
        async with self._mutation():
            return await super().aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        async with self._mutation():
            await super().aput_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id):
        async with self._mutation():
            await super().adelete_thread(thread_id)


class ExclusiveFixtureHost(ResponsesHostServer):
    """Keep fixture ownership across the complete Responses handler."""

    async def handle_create(self, request, context, cancellation_signal):
        async with (
            self.graph.checkpointer.exclusive_request(),
            aclosing(
                super().handle_create(request, context, cancellation_signal)
            ) as events,
        ):
            async for event in events:
                yield event
