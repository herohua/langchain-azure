# Proposal: Pack Pending Writes per Task

Status: proposed; no implementation or measured improvement. Source links pin hosting `d6befba`, AgentServer core `f00fb28c` and LangGraph `1.2.16`. This changes stored item format; it is separate from [checkpoint read optimization](CHECKPOINT_READS.md).

## Current behavior and target

LangGraph calls `aput_writes(config, writes, task_id, task_path)` with a sequence of channel outputs. The [Foundry saver][write] creates one item per output, keyed by checkpoint namespace, checkpoint ID, task ID and index. Ordinary outputs use create-only writes: an existing index keeps its first value. Special channels use [fixed negative indices][indices] and replaceable items for error, scheduling, interrupt and resume state.

In the local conversation workload, the input task supplies two ordinary outputs (`messages` and `branch:to:revise`); the `revise` task supplies four (`messages`, `files`, `todos`, `turns`). These are two saver calls but six item writes. Three checkpoint writes and one chain-store reference write bring each request to ten item writes.

| Operation | Current item writes | Proposed fresh-write target |
| --- | ---: | ---: |
| Input task outputs | 2 | 1 |
| `revise` task outputs | 4 | 1 |
| All request item writes | 10 | 6 |
| Continuation storage HTTP calls, including reads | 38 | 34 |

Targets assume no retries/conflicts, payloads within the service item-size limit and unchanged metadata/client behavior. They are derived, not measured improvements. The [retained baseline](../results/RESULTS.md) records request totals; per-task composition was checked by tracing the saver calls. The standalone 24/105-write probes use one output per task, so task packing alone does not reduce their item counts.

## Proposed change

Store ordinary outputs for one task in a versioned item, with checkpoint/thread scope, task ID/path and an ordered list of original indices, channel names and serialized values. Keep different tasks and checkpoints separate. Start with ordinary outputs; retain separate replaceable items for special channels. Preserve the original indices even when splitting a mixed ordinary/special sequence.

The normal fresh path should create one packed item per task. Do not delay persistence until graph completion or coalesce different `aput_writes()` calls merely to save requests: successful return must still mean that call's required outputs are durable. This is not `durability="exit"`.

A duplicate task key is not enough to skip the whole batch. Repeated calls can contain existing and previously unseen indices. Preserve first-write-wins per ordinary index while adding missing indices; never replace the document with the latest batch. If merging is needed, use [conditional updates][core-write] with ETags and conflict reconciliation, preserving concurrent additions. Retain stable keys/body for retry reconciliation; a timeout may follow a committed write. Report any extra reads, conditional writes and conflicts separately from the two-write fresh-path target.

Before writing, serialize and check the actual service item-size limit. Define a bounded, deterministic split or legacy-format fallback for oversized batches, including retry/conflict behavior across parts. Do not silently truncate outputs or weaken isolation, TTL, errors or cancellation cleanup. No new backend batch endpoint is presumed; identify any missing supported core contract separately.

## Developer inspection and representation

LangGraph defines the logical pending-write interface, not a universal physical storage schema. Packing keeps that interface but changes the State Store representation: one item contains several channel outputs instead of one. Developers inspecting raw items may mistake one item for one output, miss nested channels or interpret a lower item count as missing data. Existing inspection scripts that expect top-level `channel`/`value` fields can also break.

Treat this as a developer-facing format change. Before rollout, document the format version, task/checkpoint keys, nested index/channel/value mapping and separate special-channel records. Include a before/after raw-item example and show how the saver's configured serializer reconstructs the logical pending-write list. Raw values may already be serialized; do not imply that packing makes them directly readable JSON. Validate both raw inspection and saver readback. If one-item-per-output inspection is an explicit requirement, retain that format or evaluate a service batch-write API separately rather than assuming packing is acceptable.

## Read compatibility and rollout

Extend [`_pending_writes()`][read] to decode both legacy single-output and packed items, flatten them, then retain the existing `(task_id, index)` ordering. Preserve serializer types, task paths and special-channel semantics. Pagination now counts stored items, not logical outputs. Thread deletion and history loading must cover both formats.

Deploy a dual-format reader before enabling packed writes. Old readers cannot consume the new format: define mixed-version writer ownership, duplicate-index precedence and rollback behavior before rollout. A rolling deployment must not let old and new writers independently create conflicting representations of one logical output. Do not rewrite or delete existing data as part of the first delivery.

## Cosmos DB applicability

The idea also applies in principle to [CosmosDBSaver's per-output writes][cosmos], unlike the Foundry-specific descriptor issue. It would require a separate format/compatibility change there. Cosmos already queries full pending-write documents, so fewer documents do not imply proportionally fewer read HTTP requests or RU savings. Measure write requests, bytes and RU separately; no Cosmos change is included here.

## Components and validation

| Owner | Required work |
| --- | --- |
| `langchain-azure-ai` saver | Update `aput_writes`, item schema/key handling, `_pending_writes` and history/deletion compatibility. Keep public saver signatures and checkpoint/reference formats unchanged. |
| AgentServer core | Verify [create-only and conditional replace semantics][core-write], returned ETags and real service payload limits. No production core change is presumed. |
| Tests | Extend `test_foundry_checkpoint_saver.py` for multiple outputs, repeated/changed/extended batches, concurrent additions, special/mixed channels, ordering, serialization, empty/oversized batches and legacy/packed coexistence. Cover committed-then-timeout, conditional conflicts and cancellation without lost durable outputs. Existing graph, interrupt/resume and recovery tests must pass. |
| Sample | Add a multi-output-per-task probe and write/readback assertions. Record logical outputs versus physical items, requests, payload bytes and conflicts. Keep the one-output-per-task and pagination controls. The current mock rejects conditional writes; extend and test it if the candidate uses ETag merging. |
| Documentation / inspection | Provide legacy/packed examples mapping stored items to the same logical writes. Identify affected direct-store inspection scripts and update or document their required changes; make format/version, nested outputs and special records clear. |

Measure packing alone in ABBA order (base, candidate, candidate, base), with identical dependencies, histories, delays and durability. Verify the six-to-two pending-write target on the fresh path and identical restored outputs. Report extra reconciliation/chunking work, item reads, metadata calls, transports and elapsed time. Do not add savings from other proposals or claim cloud latency benefits from synthetic counts.

[write]: https://github.com/langchain-ai/langchain-azure/blob/d6befba95a86f55328b2e95eb61151a2f8889485/libs/azure-ai/langchain_azure_ai/agents/hosting/_foundry_checkpoint_saver.py#L339-L399
[read]: https://github.com/langchain-ai/langchain-azure/blob/d6befba95a86f55328b2e95eb61151a2f8889485/libs/azure-ai/langchain_azure_ai/agents/hosting/_foundry_checkpoint_saver.py#L475-L537
[indices]: https://github.com/langchain-ai/langgraph/blob/41c9c22fdc83d68a2859d0c9a2b627c8615a792f/libs/checkpoint/langgraph/checkpoint/base/__init__.py#L817-L824
[core-write]: https://github.com/Azure/azure-sdk-for-python/blob/f00fb28c5a53593d3dc74d8bebe204e27a604c7f/sdk/agentserver/azure-ai-agentserver-core/azure/ai/agentserver/core/storage/_state.py#L374-L461
[cosmos]: https://github.com/langchain-ai/langchain-azure/blob/d6befba95a86f55328b2e95eb61151a2f8889485/libs/azure-cosmosdb/src/langchain_azure_cosmosdb/aio/_langgraph_checkpoint_store.py#L391-L453
