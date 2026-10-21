# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Handler-owned State Store clients; standalone operations keep local ownership."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from typing import Any

import aiohttp
from azure.ai.agentserver.core import get_request_context
from azure.ai.agentserver.core.storage import (
    FoundryStateStore,
    FoundryStorageNotFoundError,
)
from azure.core.pipeline.transport import AioHttpTransport

_current_scope: ContextVar[_StateStoreScope | None] = ContextVar(
    "langchain_azure_state_store_scope", default=None
)


class _StateStoreScope:
    def __init__(self) -> None:
        self.stack = AsyncExitStack()
        self.clients: dict[tuple[object, str, str | None], FoundryStateStore] = {}
        self.descriptors: dict[int, Any] = {}
        self.locks: dict[tuple[object, str, str | None], asyncio.Lock] = {}
        self.idle = asyncio.Event()
        self.idle.set()
        self.active = 0
        self.closing = False
        self.session: aiohttp.ClientSession | None = None

    async def acquire(
        self,
        owner: object,
        factory: type[FoundryStateStore],
        name: str,
        *args: Any,
        **options: Any,
    ) -> FoundryStateStore:
        # Each consumer instance fixes endpoint, credential and pipeline options.
        key = (owner, name, get_request_context().user_id)
        async with self.locks.setdefault(key, asyncio.Lock()):
            store = self.clients.get(key)
            if store is not None:
                try:
                    # Validation consumes this acquisition's fresh metadata.
                    self.descriptors[id(store)] = await store.get()
                    return store
                except FoundryStorageNotFoundError:
                    # Other borrowers may still be using it; close at scope exit.
                    del self.clients[key]
            if self.session is None:
                self.session = await self.stack.enter_async_context(
                    aiohttp.ClientSession(
                        cookie_jar=aiohttp.DummyCookieJar(),
                        trust_env=True,
                        auto_decompress=False,
                    )
                )
            store = await factory.get_or_create(
                name,
                *args,
                **options,
                transport=AioHttpTransport(
                    session=self.session, session_owner=False, **options
                ),
            )
            self.stack.push_async_callback(store.aclose)
            self.clients[key] = store
            return store


def _store_descriptor(store: FoundryStateStore) -> Any:
    scope = _current_scope.get()
    if scope is not None and id(store) in scope.descriptors:
        return scope.descriptors[id(store)]
    return getattr(store, "resolved_descriptor", None)


@asynccontextmanager
async def _state_store_scope() -> AsyncIterator[None]:
    scope = _StateStoreScope()
    token = _current_scope.set(scope)
    try:
        yield
    finally:
        scope.closing = True
        _current_scope.reset(token)

        async def close() -> None:
            await scope.idle.wait()
            await scope.stack.aclose()

        cleanup = asyncio.create_task(close())
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        await cleanup
        if cancelled:
            raise asyncio.CancelledError()


@asynccontextmanager
async def _state_store(
    owner: object,
    factory: type[FoundryStateStore],
    name: str,
    *args: Any,
    **options: Any,
) -> AsyncIterator[FoundryStateStore]:
    scope = _current_scope.get()
    # Custom pipelines/sessions retain their existing ownership and proxy settings.
    if scope is None or any(
        key in options
        for key in (
            "transport",
            "pipeline",
            "session",
            "session_owner",
            "use_env_settings",
        )
    ):
        store = await factory.get_or_create(name, *args, **options)
        async with store:
            yield store
        return
    if scope.closing:
        raise RuntimeError("State Store scope has already ended")
    scope.active += 1
    scope.idle.clear()
    try:
        store = await scope.acquire(owner, factory, name, *args, **options)
        yield store
    finally:
        scope.active -= 1
        if not scope.active:
            scope.idle.set()
