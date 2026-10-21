# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Client ownership, identity isolation and real HTTP connection reuse."""

import asyncio
from contextlib import asynccontextmanager
from time import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from azure.ai.agentserver.core import (
    FoundryAgentRequestContext,
    reset_request_context,
    set_request_context,
)
from azure.ai.agentserver.core.storage import (
    FoundryStateStore,
    FoundryStorageEndpoint,
    FoundryStorageNotFoundError,
)
from azure.core.credentials import AccessToken
from langchain_core.runnables import RunnableConfig

from langchain_azure_ai.agents.hosting import FoundryCheckpointSaver
from langchain_azure_ai.agents.hosting._state_store_scope import (
    _state_store,
    _state_store_scope,
)


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    created: list[Any] = []

    async def acquire(name: str, *args: Any, **options: Any) -> Any:
        await asyncio.sleep(0)
        store = SimpleNamespace(
            name=name,
            args=args,
            options=options,
            get=AsyncMock(),
            aclose=AsyncMock(),
        )
        created.append(store)
        return store

    monkeypatch.setattr(FoundryStateStore, "get_or_create", acquire)
    return created


async def test_concurrent_borrowers_refresh_and_close_once(clients: list[Any]) -> None:
    owner = object()

    async def borrow() -> Any:
        async with _state_store(owner, FoundryStateStore, "store") as store:
            await asyncio.sleep(0)
            cast(Any, store).aclose.assert_not_awaited()
            return store

    async with _state_store_scope():
        borrowed = await asyncio.gather(*(borrow() for _ in range(8)))
        assert all(store is clients[0] for store in borrowed)
        assert len(clients) == 1
        assert clients[0].get.await_count == 7
    clients[0].aclose.assert_awaited_once()


async def test_store_consumer_and_user_bindings_are_separate(
    clients: list[Any],
) -> None:
    first, second = object(), object()
    async with _state_store_scope():
        for owner, name, user in [
            (first, "checkpoint", "a"),
            (first, "chain", "a"),
            (second, "checkpoint", "a"),
            (first, "checkpoint", "b"),
        ]:
            token = set_request_context(FoundryAgentRequestContext(user_id=user))
            try:
                async with _state_store(owner, FoundryStateStore, name):
                    pass
            finally:
                reset_request_context(token)
        assert len(clients) == 4
        assert len({id(c.options["transport"].session) for c in clients}) == 1
    for client in clients:
        client.aclose.assert_awaited_once()


async def test_missing_store_reacquires_without_closing_other_borrowers(
    clients: list[Any],
) -> None:
    owner = object()
    async with _state_store_scope():
        async with _state_store(owner, FoundryStateStore, "store") as old:
            cast(Any, old).get.side_effect = FoundryStorageNotFoundError("deleted")
            async with _state_store(owner, FoundryStateStore, "store") as new:
                assert old is not new
                cast(Any, old).aclose.assert_not_awaited()
            async with _state_store(owner, FoundryStateStore, "store") as reused:
                assert reused is new
    for client in clients:
        client.aclose.assert_awaited_once()


@pytest.mark.parametrize("error", [RuntimeError("backend"), asyncio.CancelledError()])
async def test_refresh_failure_propagates_and_closes(
    clients: list[Any], error: BaseException
) -> None:
    owner = object()
    with pytest.raises(type(error)):
        async with _state_store_scope():
            async with _state_store(owner, FoundryStateStore, "store") as store:
                cast(Any, store).get.side_effect = error
            async with _state_store(owner, FoundryStateStore, "store"):
                pytest.fail("Failed refresh must not lend the client")
    clients[0].aclose.assert_awaited_once()


async def test_cancelled_owner_waits_for_active_borrowers(clients: list[Any]) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    owner = object()

    async def child() -> None:
        async with _state_store(owner, FoundryStateStore, "store") as store:
            started.set()
            await release.wait()
            cast(Any, store).aclose.assert_not_awaited()

    async def handler() -> None:
        async with _state_store_scope():
            task = asyncio.create_task(child())
            await started.wait()
            try:
                await asyncio.Event().wait()
            finally:
                # The scope waits even if the owner leaves before this child.
                assert not task.done()

    task = asyncio.create_task(handler())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    clients[0].aclose.assert_not_awaited()
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    clients[0].aclose.assert_awaited_once()


async def test_late_child_cannot_reopen_closed_scope(clients: list[Any]) -> None:
    release = asyncio.Event()

    async def child() -> None:
        await release.wait()
        async with _state_store(object(), FoundryStateStore, "store"):
            pytest.fail("Closed scope must reject new work")

    async with _state_store_scope():
        task = asyncio.create_task(child())
    release.set()
    with pytest.raises(RuntimeError, match="already ended"):
        await task
    assert not clients


async def test_scopes_never_share_clients(clients: list[Any]) -> None:
    owner = object()

    async def handler() -> Any:
        async with _state_store_scope():
            async with _state_store(owner, FoundryStateStore, "store") as store:
                await asyncio.sleep(0)
                return store

    a, b = await asyncio.gather(handler(), handler())
    assert a is not b
    assert a.options["transport"].session is not b.options["transport"].session
    assert all(c.options["transport"].session.closed for c in clients)


async def test_scoped_saver_revalidates_settings_before_item_access(
    clients: list[Any],
) -> None:
    saver = FoundryCheckpointSaver(credential=AsyncMock())
    config: RunnableConfig = {"configurable": {"thread_id": "thread"}}
    # A returned store must pass the original validation on every operation.
    with pytest.raises(ValueError, match="user_isolation=False"):
        async with _state_store_scope():
            async with _state_store(
                saver, FoundryStateStore, saver._store_name("thread")
            ) as store:
                cast(Any, store).get.return_value = SimpleNamespace(
                    user_isolation=True, item_ttl_seconds=30 * 24 * 60 * 60
                )
                cast(Any, store).list_keys = AsyncMock(
                    return_value=SimpleNamespace(keys=[])
                )
            assert await saver.aget_tuple(config) is None
            cast(Any, store).get.return_value.user_isolation = False
            await saver.aget_tuple(config)
    assert cast(Any, store).list_keys.await_count == 1
    assert cast(Any, store).get.await_count == 2
    cast(Any, store).aclose.assert_awaited_once()


@pytest.mark.parametrize("consumer", ["checkpoint", "chain"])
async def test_descriptor_validation_uses_this_acquisitions_refresh(
    clients: list[Any], consumer: str
) -> None:
    from langchain_azure_ai.agents.hosting._responses.conversation_chain_store import (
        FoundryConversationChainStore,
    )

    owner = (
        FoundryCheckpointSaver(credential=AsyncMock())
        if consumer == "checkpoint"
        else FoundryConversationChainStore(user_isolation=True)
    )
    good = SimpleNamespace(user_isolation=True, item_ttl_seconds=30 * 24 * 60 * 60)
    async with _state_store_scope():
        async with _state_store(
            owner, FoundryStateStore, owner._store_name("thread")
        ) as store:
            cast(Any, store).resolved_descriptor = good
            cast(Any, store).get.return_value = SimpleNamespace(
                user_isolation=False, item_ttl_seconds=good.item_ttl_seconds
            )
            cast(Any, store).list_keys = AsyncMock(
                return_value=SimpleNamespace(keys=[])
            )
            cast(Any, store).get_item = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="user_isolation=False"):
            if consumer == "checkpoint":
                await cast(FoundryCheckpointSaver, owner).aget_tuple(
                    {"configurable": {"thread_id": "thread"}}
                )
            else:
                await cast(FoundryConversationChainStore, owner).get("thread", "key")
        cast(Any, store).get.assert_awaited_once()
        cast(Any, store).list_keys.assert_not_awaited()
        cast(Any, store).get_item.assert_not_awaited()
    clients[0].aclose.assert_awaited_once()


async def test_failed_acquisition_closes_owned_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transports = []

    async def fail(*args: Any, **options: Any) -> None:
        transports.append(options["transport"])
        raise RuntimeError("acquisition failed")

    monkeypatch.setattr(FoundryStateStore, "get_or_create", fail)
    with pytest.raises(RuntimeError, match="acquisition failed"):
        async with _state_store_scope():
            async with _state_store(object(), FoundryStateStore, "store"):
                pass
    assert transports[0].session.closed


async def test_close_failure_still_closes_remaining_resources(
    clients: list[Any],
) -> None:
    with pytest.raises(RuntimeError, match="close failed"):
        async with _state_store_scope():
            for name in ("a", "b"):
                async with _state_store(object(), FoundryStateStore, name):
                    pass
            clients[-1].aclose.side_effect = RuntimeError("close failed")
    assert all(c.options["transport"].session.closed for c in clients)
    for client in clients:
        client.aclose.assert_awaited_once()


@pytest.mark.parametrize(
    "option", ["transport", "pipeline", "session", "use_env_settings"]
)
async def test_custom_pipeline_options_keep_operation_ownership(
    monkeypatch: pytest.MonkeyPatch, option: str
) -> None:
    store = AsyncMock()
    acquire = AsyncMock(return_value=store)
    monkeypatch.setattr(FoundryStateStore, "get_or_create", acquire)
    value = object()
    async with _state_store_scope():
        async with _state_store(
            object(), FoundryStateStore, "store", **{option: value}
        ):
            pass
        store.__aexit__.assert_awaited_once()
    assert acquire.call_args.kwargs == {option: value}


@asynccontextmanager
async def _http_storage() -> Any:
    requests: list[Any] = []

    async def handle(request: web.Request) -> web.Response:
        requests.append(request)
        if "/items/" in request.path:
            return web.json_response({"error": {"message": "missing"}}, status=404)
        return web.json_response(
            {
                "id": "store",
                "name": "store",
                "object": "state_store",
                "user_isolation": True,
                "item_ttl_seconds": 600,
                "created_at": 1,
                "updated_at": 1,
                "tags": {},
            }
        )

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    # Keep the listener alive until aiohttp finishes deregistering it.
    import socket

    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        await web.SockSite(runner, listener).start()
        yield f"http://127.0.0.1:{listener.getsockname()[1]}/", requests
    finally:
        try:
            await runner.cleanup()
        finally:
            listener.close()


async def test_real_transport_pool_headers_and_borrower_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOUNDRY_HOSTING_ENVIRONMENT", "test")
    # A loopback server needs no TLS; production keeps Azure Core's HTTPS check.
    monkeypatch.setattr(
        "azure.core.pipeline.policies._authentication_async._enforce_https",
        lambda request: None,
    )
    credential = SimpleNamespace(
        get_token=AsyncMock(return_value=AccessToken("test", int(time()) + 3600)),
        close=AsyncMock(),
    )
    user_agent = ["first"]
    owner = object()
    async with _http_storage() as (url, requests):
        endpoint = FoundryStorageEndpoint(storage_base_url=url)
        async with _state_store_scope():
            first = None
            for call_id in ("call-1", "call-2"):
                token = set_request_context(
                    FoundryAgentRequestContext(user_id="user", call_id=call_id)
                )
                try:
                    async with _state_store(
                        owner,
                        FoundryStateStore,
                        "store",
                        credential,
                        endpoint,
                        user_isolation=True,
                        item_ttl_seconds=600,
                        user_id="delegated",
                        get_server_version=lambda: user_agent[0],
                    ) as store:
                        if first is None:
                            first = store
                        assert store is first
                        assert await store.get_item("missing") is None
                finally:
                    reset_request_context(token)
                user_agent[0] = "second"
            # Retiring one store's pipeline must not close the shared session.
            assert first is not None
            await first.aclose()
            async with _state_store(
                object(),
                FoundryStateStore,
                "other",
                credential,
                endpoint,
            ) as other:
                await other.get()
        assert len({request.transport for request in requests}) == 1
        assert [r.headers.get("x-agent-foundry-call-id") for r in requests[:4]] == [
            "call-1",
            "call-1",
            "call-2",
            "call-2",
        ]
        assert requests[1].headers["x-ms-user-id"] == "delegated"
        assert requests[1].headers["User-Agent"] == "first"
        assert requests[3].headers["User-Agent"] == "second"
        credential.close.assert_not_awaited()
