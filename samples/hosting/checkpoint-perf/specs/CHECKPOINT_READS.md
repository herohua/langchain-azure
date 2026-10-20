# Proposal: Reduce Checkpoint Restoration Overhead

Status: proposed; no implementation or measured improvement. Source links pin hosting `d6befba`, AgentServer core `f00fb28c` and LangGraph `1.2.16`.

## Why there are multiple `get_item()` calls

Pending writes are task outputs saved before the next complete checkpoint. They let LangGraph retain completed work and interrupt/resume state.

1. [`aget_tuple()`][load] fetches one checkpoint item by ID, or lists the latest checkpoint key and then fetches that item.
2. [`_checkpoint_tuple()` / `_pending_writes()`][writes] lists keys tagged with that checkpoint's namespace and ID. Core's [`list_keys()`][keys] returns keys, not values, so the saver calls [`get_item()`][item] once per pending write, currently in a serial loop. It then sorts records by `(task_id, index)` and deserializes their values.
3. The normal [Responses handler][host] first calls [`detect_pending_interrupts()`][interrupts], which calls `graph.aget_state()`, then starts `graph.astream()`. LangGraph's [state inspection][graph-state] and [execution startup][graph-loop] each call the saver. They can reload the same checkpoint and its pending writes; record the actual thread/namespace/checkpoint before treating any reads as duplicates. LangGraph 1.2.16 also [checks whether an explicitly addressed checkpoint has been superseded][superseded] by loading the latest checkpoint. Recovery with an existing current-response checkpoint reference skips the interrupt check, but can still need the latest-checkpoint check.

For one exact checkpoint load with N available pending-write items, expect one checkpoint item GET plus N pending-write GETs, plus key-list pages and store metadata reads. The [local probe](../results/RESULTS.md) confirms 24 writes -> 25 item GETs, one key-list call and concurrency 1; 105 writes require two pages and 106 item GETs. These are logical calls without retries, not a formula for every host request.

In the refreshed local baseline, each continuation has four item GETs: one chain-store checkpoint-reference read and three reads of the same checkpoint key, with no pending-write items at that point. The reads serve interrupt inspection, execution loading and a latest-checkpoint check. The separate pending-write probe measures per-item fan-out.

[`alist()`][history] also loads each selected checkpoint and its pending writes. `_pending_writes()` itself filters one checkpoint; it does not scan all history. Chain-store lookups and response-history traversal are separate work. [`get()` metadata reads](STATE_STORE_DESCRIPTOR_REUSE.md) are also distinct from `get_item()`.

## Cosmos DB applicability

[CosmosDBSaver queries complete pending-write documents](https://github.com/langchain-ai/langchain-azure/blob/4b5bb9bcc2b5dc8bc4890675ddae05c88a1dcf71/libs/azure-cosmosdb/src/langchain_azure_cosmosdb/aio/_langgraph_checkpoint_store.py#L659-L688), potentially across pages; it has no key-list plus per-item GET loop to parallelize. Reuse between host inspection and graph execution may still help with Cosmos DB, subject to the freshness and mutation-isolation constraints below.

## First delivery: bounded concurrent reads

In `_pending_writes()`, read item values concurrently within each listed page. Start with an internal limit of eight outstanding reads per invocation; eight is an initial bound to validate, not a measured optimum. Process one page at a time and avoid one task per key across all pages. Concurrent invocations can multiply traffic; this is not a saver-wide or service-wide limit.

Preserve page cursors, missing-item handling, content and final `(task_id, index)` order. On failure or cancellation, cancel and await all child reads before the caller closes the store; preserve the original error/cancellation rather than exposing an exception-group wrapper. Keep retries, writes, history/schema, identity isolation and interrupt/recovery behavior unchanged. Concurrency reduces serial waiting, not the required read count.

This hosting-only change needs no new public option, backend batch API, LangGraph change or core dependency. It can ship independently of [descriptor reuse](STATE_STORE_DESCRIPTOR_REUSE.md) and [client reuse](STATE_STORE_CLIENT_REUSE.md).

## Optional follow-up: reuse confirmed duplicate reads

Trace first. Reuse only within one invocation and the same identity, thread, namespace and resolved checkpoint. A request for latest state cannot reuse an older selection without proving freshness. Preserve the superseded-checkpoint check; equality in this fixture does not justify replacing a latest lookup with an addressed checkpoint. Do not remove interrupt inspection to save reads.

Pending writes can change under the same checkpoint ID. Invalidate on `aput_writes`, `aput`, thread deletion and invocation failure/cancellation/exit; prevent an in-flight read from refilling invalidated data. Local invalidation cannot detect another writer: do not reuse mutable data unless other writers are excluded or a supported consistency check covers them.

Return independent mutable state to each caller. LangGraph's [snapshot preparation][snapshot] applies pending writes to the loaded checkpoint, so inspection and execution must not receive the same mutable `CheckpointTuple`. Serialized-data reuse with independent deserialization is one option. If freshness and mutation isolation cannot be guaranteed, ship concurrent reads alone.

## Components and validation

| Owner | Required work |
| --- | --- |
| `langchain-azure-ai` saver | Change [`_pending_writes()`][writes]; verify `aget_tuple`/`alist` close the store after children settle. Extend `test_foundry_checkpoint_saver.py` for pagination, missing items, ordering, the eight-read bound, simultaneous invocations and original-error/cancellation cleanup. |
| Hosting / LangGraph | Verify interrupt/resume and recovery paths in `test_responses_host.py` and `test_resilient.py`. For optional reuse only, trace inspection/execution and add mutation isolation, invalidation/read-race, deletion, freshness and request-isolation tests. No upstream LangGraph hook is presumed. |
| AgentServer core | Verify concurrent [`get_item`][item] use on one client with [key pagination][keys] and actual retry attempts. No production change is expected for bounded reads. |
| Sample | Preserve persisted file/TODO/message and pending-write checks. Add store acquisition, pagination, item-read/deserialization timings, returned bytes, logical reads versus HTTP attempts and maximum concurrency. |

Compare base/candidate in ABBA order (base, candidate, candidate, base), with identical dependencies, corresponding four-turn histories, a fresh-conversation control and 24/105-write probes. Report elapsed time, storage interval union, read counts, retries, failures and throttling; do not add overlapping timings. Host reconstruction is not process-crash recovery. Claim concurrent-read and duplicate-read savings separately; no service-timeout fix is implied.

[load]: https://github.com/langchain-ai/langchain-azure/blob/d6befba95a86f55328b2e95eb61151a2f8889485/libs/azure-ai/langchain_azure_ai/agents/hosting/_foundry_checkpoint_saver.py#L171-L204
[writes]: https://github.com/langchain-ai/langchain-azure/blob/d6befba95a86f55328b2e95eb61151a2f8889485/libs/azure-ai/langchain_azure_ai/agents/hosting/_foundry_checkpoint_saver.py#L475-L537
[history]: https://github.com/langchain-ai/langchain-azure/blob/d6befba95a86f55328b2e95eb61151a2f8889485/libs/azure-ai/langchain_azure_ai/agents/hosting/_foundry_checkpoint_saver.py#L237-L294
[host]: https://github.com/langchain-ai/langchain-azure/blob/d6befba95a86f55328b2e95eb61151a2f8889485/libs/azure-ai/langchain_azure_ai/agents/hosting/_responses_host.py#L883-L960
[interrupts]: https://github.com/langchain-ai/langchain-azure/blob/d6befba95a86f55328b2e95eb61151a2f8889485/libs/azure-ai/langchain_azure_ai/agents/hosting/_converters/_hitl.py#L115-L145
[keys]: https://github.com/Azure/azure-sdk-for-python/blob/f00fb28c5a53593d3dc74d8bebe204e27a604c7f/sdk/agentserver/azure-ai-agentserver-core/azure/ai/agentserver/core/storage/_state.py#L549-L611
[item]: https://github.com/Azure/azure-sdk-for-python/blob/f00fb28c5a53593d3dc74d8bebe204e27a604c7f/sdk/agentserver/azure-ai-agentserver-core/azure/ai/agentserver/core/storage/_state.py#L476-L500
[graph-state]: https://github.com/langchain-ai/langgraph/blob/41c9c22fdc83d68a2859d0c9a2b627c8615a792f/libs/langgraph/langgraph/pregel/main.py#L1489-L1512
[graph-loop]: https://github.com/langchain-ai/langgraph/blob/41c9c22fdc83d68a2859d0c9a2b627c8615a792f/libs/langgraph/langgraph/pregel/_loop.py#L2061-L2090
[snapshot]: https://github.com/langchain-ai/langgraph/blob/41c9c22fdc83d68a2859d0c9a2b627c8615a792f/libs/langgraph/langgraph/pregel/main.py#L1425-L1443
[superseded]: https://github.com/langchain-ai/langgraph/blob/41c9c22fdc83d68a2859d0c9a2b627c8615a792f/libs/langgraph/langgraph/pregel/_checkpoint.py#L175-L200
