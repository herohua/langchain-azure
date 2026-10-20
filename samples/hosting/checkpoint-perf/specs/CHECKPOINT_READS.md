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

Sections 3.1 and 3.2 are complementary and can coexist. Each can be delivered and measured separately; 3.1 does not depend on 3.2.

## 3.1 Bounded concurrent reads

In `_pending_writes()`, read item values concurrently within each listed page. Start with an internal limit of eight outstanding reads per invocation; eight is an initial bound to validate, not a measured optimum. Process one page at a time and avoid one task per key across all pages. Concurrent invocations can multiply traffic; this is not a saver-wide or service-wide limit.

Preserve page cursors, missing-item handling, content and final `(task_id, index)` order. On failure or cancellation, cancel and await all child reads before the caller closes the store; preserve the original error/cancellation rather than exposing an exception-group wrapper. Keep retries, writes, history/schema, identity isolation and interrupt/recovery behavior unchanged. Concurrency reduces serial waiting, not the required read count.

This hosting-only change needs no new public option, backend batch API, LangGraph change or core dependency. It can ship independently of [descriptor reuse](STATE_STORE_DESCRIPTOR_REUSE.md) and [client reuse](STATE_STORE_CLIENT_REUSE.md).

[Packing multiple outputs per task](PENDING_WRITE_PACKING.md) is a separate storage-format proposal. It targets fewer persisted items; bounded reads and request-scoped reuse do not require that change.

[Complete checkpoint materialization](CHECKPOINT_MATERIALIZATION.md) separately targets DeltaChannel ancestor-history reconstruction. Reusing one requested tuple does not remove reads of distinct historical checkpoints required by the current delta representation.

## 3.2 Request-scoped checkpoint reuse

For sequential conversation handling, use this flow: acquire exclusive thread ownership, establish the current checkpoint, load it and its pending writes once, inspect interrupts, then execute the graph from that loaded data. Keep existing durable writes and the final chain-store reference update. One load means one checkpoint reconstruction, not one HTTP request: pending-write items may still require multiple reads.

This needs an enforced ownership contract covering all writers, including other requests, recovery workers and `update_state()`, across processes. Ownership must span initial selection through durable writes and the final reference update. A single request happening to run alone is insufficient. If the supplied checkpoint is not current, or ownership cannot be guaranteed, retain the normal reads and latest-checkpoint check. Historical replay remains a separate path.

Implement reuse inside langchain-azure through the existing saver interface. The host establishes a request-local scope; saver `aget_tuple()` resolves a selection and caches its serialized data. LangGraph still calls the saver normally for interrupt inspection, execution startup and the [superseded-checkpoint check][superseded]; the saver can satisfy matching reads from that scope without a new LangGraph hook.

Scope cache keys to identity, thread, namespace and exact checkpoint. Track the verified latest selection separately: an exact-ID cache hit alone does not answer a latest query. Under exclusive thread ownership, an initial latest-key lookup can validate the supplied reference and let subsequent latest queries reuse that selection until mutation. Preserve the check's meaning; do not globally disable it or treat every addressed checkpoint as latest. The fixture's target is one checkpoint item read instead of three, plus the separate chain-store reference read; this is not yet measured.

Return independent mutable state to each caller: [snapshot preparation][snapshot] can modify the loaded checkpoint. Reuse serialized data and deserialize independently, or prove equivalent isolation. Pending writes can change under the same checkpoint ID. On `aput_writes`, `aput` or deletion, invalidate affected cached data before mutation and prevent stale in-flight reads from refilling it. Publish updated cache data only after successful persistence; otherwise reload. Clear the scope on failure, cancellation or exit, and use the [handle ownership/cleanup rules](STATE_STORE_CLIENT_REUSE.md). This changes read reuse, not write durability.

## Separate future option: persist at graph exit

LangGraph supports [exit-time persistence][exit-save] with `durability="exit"`; the [current host selects `sync`][host-durability]. Changing this policy is outside the read-reuse delivery. Evaluate the existing mode separately instead of buffering or discarding saver writes in a custom wrapper.

Exit includes normal completion, an interrupt or an error. It does not protect intermediate progress against a process crash, and replay can repeat external side effects. Exit-time persistence also need not be one HTTP write: checkpoint, pending-write and chain-store records remain distinct. Any later adoption must preserve interrupt/resume and response recovery, await required graph persistence before publishing its chain-store reference, and document the reduced recovery guarantee.

## Benefit, implementation size and applicability

Bounded reads (3.1) keep HTTP counts unchanged. With 24 equal-duration reads and a bound of eight, the item-read waiting segment ideally falls from 24 serial waits to three waves (-87.5%); including four unchanged serial calls gives a modeled 75% reduction for that probe, not measured end-to-end savings. Empty pending-write lists gain nothing. Estimate: 40–80 production lines in langchain-azure plus 180–320 test/fixture lines; no external API change.

Request reuse (3.2) has a conservative fixture target of 38 → 30 calls (-21%) if one latest-key lookup establishes freshness and one reconstruction serves all consumers, excluding ownership/lease traffic. Estimate: 120–220 production lines in langchain-azure plus 300–600 test/fixture lines. No LangGraph, AgentServer core or State Store API change/release is required for request-local caching through `aget_tuple()`. A deployment-wide ownership contract remains required; new coordination infrastructure is not included.

Both need a checkpointer but not DeepAgents. Cosmos already queries complete pending-write documents, so 3.1 does not transfer; 3.2 may still avoid repeated queries. No-checkpointer requests have no saver reads to optimize. See [the estimates and applicability matrix](../ESTIMATES.md).

## Components and validation

| Owner | Required work |
| --- | --- |
| `langchain-azure-ai` saver | Change [`_pending_writes()`][writes]; verify `aget_tuple`/`alist` close the store after children settle. Extend `test_foundry_checkpoint_saver.py` for pagination, missing items, ordering, the eight-read bound, simultaneous invocations and original-error/cancellation cleanup. |
| Hosting / LangGraph compatibility | Implement the request-local scope and saver cache in langchain-azure using existing public interfaces; no LangGraph production change. Verify interrupt/resume and recovery paths in `test_responses_host.py` and `test_resilient.py`. Test exclusive ownership, exact versus latest selection, stale references/replay fallback, independent mutable returns, writes/deletion, failed persistence, invalidation/read races and scope cleanup. Assert one physical checkpoint load across inspection/execution only when the contract holds. |
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
[host-durability]: https://github.com/langchain-ai/langchain-azure/blob/d6befba95a86f55328b2e95eb61151a2f8889485/libs/azure-ai/langchain_azure_ai/agents/hosting/_responses_host.py#L348-L352
[exit-save]: https://github.com/langchain-ai/langgraph/blob/41c9c22fdc83d68a2859d0c9a2b627c8615a792f/libs/langgraph/langgraph/pregel/_loop.py#L1497-L1511
