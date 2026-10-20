"""Exercise reuse through the real saver and offline HTTP storage pipeline."""

import asyncio
import os
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from azure.ai.agentserver.core import (
    FoundryAgentRequestContext,
    reset_request_context,
    set_request_context,
)
from azure.ai.agentserver.core.storage import FoundryStateStore, FoundryStorageEndpoint
from azure.ai.agentserver.core.storage._client import FoundryStorageClient
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command, interrupt

from reuse import ExclusiveFixtureHost, ExclusiveFixtureSaver
from storage_transport import OfflineCredential, StorageFixture, StorageTransport


class ReuseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = StorageFixture(0)
        self.patches = ExitStack()
        state_root = self.patches.enter_context(tempfile.TemporaryDirectory())
        self.patches.enter_context(
            patch.dict(
                os.environ,
                {
                    "FOUNDRY_HOSTING_ENVIRONMENT": "offline-perf",
                    "AGENTSERVER_STATE_ROOT": state_root,
                    "OTEL_SDK_DISABLED": "true",
                    "APPLICATIONINSIGHTS_CONNECTION_STRING": "",
                    "LANGSMITH_TRACING": "false",
                    "LANGCHAIN_TRACING_V2": "false",
                },
            )
        )
        original_init = FoundryStorageClient.__init__

        def inject(client, *args, **kwargs):
            kwargs["transport"] = StorageTransport(self.fixture)
            original_init(client, *args, **kwargs)

        self.patches.enter_context(
            patch.object(FoundryStorageClient, "__init__", inject)
        )
        self.saver = ExclusiveFixtureSaver(
            OfflineCredential(),
            FoundryStorageEndpoint(
                storage_base_url="https://storage.test.invalid/api/storage/"
            ),
        )
        self.token = set_request_context(
            FoundryAgentRequestContext(user_id="user", call_id="call")
        )
        self.config = await self.saver.aput(
            {"configurable": {"thread_id": "thread", "checkpoint_ns": ""}},
            empty_checkpoint(),
            {"source": "input", "step": 0, "parents": {}},
            {},
        )
        self.latest = {"configurable": {"thread_id": "thread"}}

    async def asyncTearDown(self):
        await self.saver.aclose()
        reset_request_context(self.token)
        self.patches.close()
        self.assertEqual(self.fixture.constructed, self.fixture.closed)

    def reads(self):
        return sum(e["operation"] == "item_get" for e in self.fixture.events)

    async def test_reuses_exact_and_verified_latest_with_independent_values(self):
        await self.saver.aput_writes(self.config, [("probe", {"v": [1]})], "task")
        before = self.reads()
        async with self.saver.exclusive_request():
            first = await self.saver.aget_tuple(self.config)
            first.checkpoint["channel_values"]["mutated"] = True
            first.metadata["step"] = 999
            first.pending_writes[0][2]["v"].append(2)
            exact = await self.saver.aget_tuple(self.config)
            latest = await self.saver.aget_tuple(self.latest)
            self.assertEqual(exact, latest)
            self.assertNotIn("mutated", latest.checkpoint["channel_values"])
            self.assertEqual(latest.metadata["step"], 0)
            self.assertEqual(latest.pending_writes, [("task", "probe", {"v": [1]})])
            self.assertEqual(self.reads() - before, 2)
        await self.saver.aget_tuple(self.config)
        self.assertEqual(self.reads() - before, 4)

    async def test_unowned_reads_and_empty_selection_are_not_cached(self):
        before = self.reads()
        await self.saver.aget_tuple(self.config)
        await self.saver.aget_tuple(self.config)
        self.assertEqual(self.reads() - before, 2)
        async with self.saver.exclusive_request():
            missing = {"configurable": {"thread_id": "empty"}}
            self.assertIsNone(await self.saver.aget_tuple(missing))
            self.assertIsNone(await self.saver.aget_tuple(missing))

    async def test_stale_reference_falls_back_without_poisoning_latest(self):
        newer = await self.saver.aput(self.config, empty_checkpoint(), {}, {})
        async with self.saver.exclusive_request():
            old = await self.saver.aget_tuple(self.config)
            current = await self.saver.aget_tuple(self.latest)
            self.assertEqual(old.config, self.config)
            self.assertEqual(current.config, newer)
            self.assertFalse(self.saver._active_scope().enabled)
        async with self.saver.exclusive_request():
            await self.saver.aget_tuple(self.latest)
            self.assertEqual(
                (await self.saver.aget_tuple(self.config)).config, self.config
            )
            self.assertEqual((await self.saver.aget_tuple(self.latest)).config, newer)

    async def test_identity_thread_and_namespace_do_not_share_cached_values(self):
        async with self.saver.exclusive_request():
            await self.saver.aget_tuple(self.config)
            token = set_request_context(
                FoundryAgentRequestContext(user_id="other", call_id="other")
            )
            before = self.reads()
            try:
                await self.saver.aget_tuple(self.config)
                self.assertEqual(self.reads() - before, 1)
            finally:
                reset_request_context(token)
            for configurable in (
                {"thread_id": "other-thread"},
                {"thread_id": "thread", "checkpoint_ns": "other-ns"},
            ):
                self.assertIsNone(
                    await self.saver.aget_tuple({"configurable": configurable})
                )

    async def test_writes_checkpoint_and_deletion_invalidate(self):
        async with self.saver.exclusive_request():
            await self.saver.aget_tuple(self.config)
            await self.saver.aput_writes(self.config, [("probe", 1)], "task")
            loaded = await self.saver.aget_tuple(self.config)
            self.assertEqual(loaded.pending_writes, [("task", "probe", 1)])
            newer = await self.saver.aput(self.config, empty_checkpoint(), {}, {})
            self.assertEqual((await self.saver.aget_tuple(self.latest)).config, newer)
            # The fixture implements item deletion, preserving user partitions.
            await self.saver.adelete_thread("thread")
            self.assertIsNone(await self.saver.aget_tuple(self.latest))

    async def test_failed_partial_write_invalidates_and_preserves_error(self):
        async with self.saver.exclusive_request():
            await self.saver.aget_tuple(self.config)
            original = FoundryStateStore.create_item

            async def fail_second(store, key, *args, **kwargs):
                if key.endswith("/1"):
                    raise RuntimeError("write failed")
                return await original(store, key, *args, **kwargs)

            with (
                patch.object(FoundryStateStore, "create_item", fail_second),
                self.assertRaisesRegex(RuntimeError, "write failed"),
            ):
                await self.saver.aput_writes(
                    self.config, [("first", 1), ("second", 2)], "task"
                )
            loaded = await self.saver.aget_tuple(self.config)
            self.assertEqual(loaded.pending_writes, [("task", "first", 1)])

    async def test_read_failure_and_request_failure_discard_scope(self):
        with self.assertRaisesRegex(RuntimeError, "request failed"):
            async with self.saver.exclusive_request():
                scope = self.saver._active_scope()
                await self.saver.aget_tuple(self.config)
                scope.invalidate()
                with (
                    patch.object(
                        FoundryStateStore,
                        "get_item",
                        AsyncMock(side_effect=RuntimeError("read failed")),
                    ),
                    self.assertRaisesRegex(RuntimeError, "read failed"),
                ):
                    await self.saver.aget_tuple(self.config)
                self.assertIsNone(scope.data)
                self.assertFalse(scope.enabled)
                raise RuntimeError("request failed")
        self.assertFalse(scope.active)
        self.assertIsNone(self.saver._active_scope())

    async def test_inflight_read_cannot_refill_after_mutation(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.saver._checkpoint_tuple

        async def paused(*args):
            result = await original(*args)
            entered.set()
            await release.wait()
            return result

        async with self.saver.exclusive_request():
            with patch.object(self.saver, "_checkpoint_tuple", paused):
                read = asyncio.create_task(self.saver.aget_tuple(self.config))
                await entered.wait()
                await self.saver.aput_writes(self.config, [("probe", 1)], "task")
                release.set()
                await read
            self.assertIsNone(self.saver._active_scope().data)
            self.assertEqual(
                (await self.saver.aget_tuple(self.config)).pending_writes,
                [("task", "probe", 1)],
            )

    async def test_cancellation_and_exit_clear_inherited_scope(self):
        entered = asyncio.Event()
        async with self.saver.exclusive_request():
            scope = self.saver._active_scope()
            await self.saver.aget_tuple(self.config)
            original = self.saver._checkpoint_tuple

            async def paused(*args):
                await original(*args)
                entered.set()
                await asyncio.Event().wait()

            scope.invalidate()
            with patch.object(self.saver, "_checkpoint_tuple", paused):
                read = asyncio.create_task(self.saver.aget_tuple(self.config))
                await entered.wait()
                read.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await read
            self.assertIsNone(scope.data)
            self.assertFalse(scope.enabled)
        self.assertFalse(scope.active)
        self.assertIsNone(self.saver._active_scope())
        async with self.saver.exclusive_request():
            self.assertTrue(self.saver._active_scope().enabled)
            release = asyncio.Event()

            async def inherited():
                await release.wait()
                self.assertIsNone(self.saver._active_scope())
                return await self.saver.aget_tuple(self.config)

            child = asyncio.create_task(inherited())
        before = self.reads()
        release.set()
        await child
        self.assertEqual(self.reads() - before, 1)

    async def test_exclusive_scope_blocks_other_writers_through_exit(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def owner():
            async with self.saver.exclusive_request():
                entered.set()
                await release.wait()

        owner_task = asyncio.create_task(owner())
        await entered.wait()
        writer = asyncio.create_task(
            self.saver.aput_writes(self.config, [("probe", 1)], "task")
        )
        await asyncio.sleep(0)
        self.assertFalse(writer.done())
        release.set()
        await owner_task
        await writer

    async def test_interrupt_resume_and_reconstruction(self):
        async def ask(state):
            answer = interrupt("Approve?")
            return {"messages": [{"role": "assistant", "content": answer}]}

        builder = StateGraph(MessagesState)
        builder.add_node("ask", ask)
        builder.add_edge(START, "ask")
        builder.add_edge("ask", END)
        graph = builder.compile(checkpointer=self.saver)
        config = {"configurable": {"thread_id": "interrupt"}}
        async with self.saver.exclusive_request():
            await graph.ainvoke(
                {"messages": [("user", "go")]}, config, durability="sync"
            )
        graph = builder.compile(checkpointer=self.saver)
        async with self.saver.exclusive_request():
            paused = await graph.aget_state(config)
            self.assertEqual(paused.tasks[0].interrupts[0].value, "Approve?")
            result = await graph.ainvoke(
                Command(resume="yes"), config, durability="sync"
            )
            self.assertEqual(result["messages"][-1].content, "yes")
        restored = await builder.compile(checkpointer=self.saver).aget_state(config)
        self.assertFalse(restored.next)
        self.assertEqual(restored.values["messages"][-1].content, "yes")
        async with self.saver.exclusive_request():
            await graph.aget_state(config)
            await graph.aupdate_state(config, {"messages": [("user", "updated")]})
            updated = await graph.aget_state(config)
            self.assertEqual(updated.values["messages"][-1].content, "updated")

    async def test_host_recovery_uses_persisted_reference_and_keeps_scope_to_final_set(
        self,
    ):
        from langchain_azure_ai.agents.hosting._responses import (
            METADATA_LANGGRAPH_CHECKPOINT_ID,
            METADATA_LANGGRAPH_THREAD_ID,
        )

        calls = []

        async def plan(state):
            calls.append("plan")
            return {"messages": [("assistant", "plan")]}

        async def finish(state):
            calls.append("finish")
            return {"messages": [("assistant", "done")]}

        builder = StateGraph(MessagesState)
        builder.add_node("plan", plan)
        builder.add_node("finish", finish)
        builder.add_edge(START, "plan")
        builder.add_edge("plan", "finish")
        builder.add_edge("finish", END)
        graph = builder.compile(checkpointer=self.saver)
        config = {"configurable": {"thread_id": "recovery"}}
        saved = None
        async for mode, payload in graph.astream(
            {"messages": [("user", "go")]},
            config,
            stream_mode=["checkpoints"],
            durability="sync",
        ):
            if payload["next"] == ["finish"]:
                saved = payload["config"]["configurable"]["checkpoint_id"]
        self.assertIsNotNone(saved)
        calls.clear()
        chain = MagicMock()

        async def set_reference(*args):
            self.assertIsNotNone(self.saver._active_scope())

        chain.set = AsyncMock(side_effect=set_reference)
        with patch.dict(os.environ, {"FOUNDRY_HOSTING_ENVIRONMENT": ""}):
            host = ExclusiveFixtureHost(graph, conversation_chain_store=chain)
        context = MagicMock()
        context.response_id = "resp-recovery"
        context.conversation_chain_id = "recovery"
        context.platform_context = SimpleNamespace(user_id_key="user")
        context.is_recovery = True
        context.client_cancelled = False
        context.shutdown = asyncio.Event()
        context.persisted_response = {
            "id": "resp-recovery",
            "output": [],
            "metadata": {
                "_internal_metadata": {
                    METADATA_LANGGRAPH_CHECKPOINT_ID: saved,
                    METADATA_LANGGRAPH_THREAD_ID: "recovery",
                }
            },
        }
        with patch.object(
            host, "build_runnable_config", AsyncMock(return_value=config)
        ):
            events = [
                event
                async for event in host.handle_create(
                    {"input": "do not replay"}, context, asyncio.Event()
                )
            ]
        self.assertEqual(events[-1]["type"], "response.completed")
        self.assertEqual(calls, ["finish"])
        chain.set.assert_awaited_once()
        self.assertIsNone(self.saver._active_scope())


if __name__ == "__main__":
    unittest.main()
