# Proposal: Materialize Complete State at Every Checkpoint

Status: proposed; no implementation or measured improvement. Source evidence establishes ancestor-read amplification; the performance benefit of full materialization still needs measurement. This is separate from [request-scoped read reuse](CHECKPOINT_READS.md) and [pending-write packing/batching](PENDING_WRITE_PACKING.md).

## Current behavior and evidence

DeepAgents `0.7.25` uses DeltaChannel for [messages][messages] and [files][files], with `snapshot_frequency=50`. DeltaChannel keeps the complete logical value in memory but normally omits that value from checkpoint storage between snapshots. [Channel reconstruction][restore] invokes the saver's history loader when a required value is absent.

FoundryCheckpointSaver does not override `aget_delta_channel_history()`. It inherits the [default ancestor walk][history], which calls `aget_tuple()` for the target and successive parents until the required channel seeds are found. Each Foundry tuple load can acquire/validate a store, read the checkpoint, list its pending-write keys and fetch their values. Many small history reads can therefore produce substantial remote I/O even when no individual checkpoint has many pending writes.

Retained hosted measurements with real models/tools bypassed showed the following off-mode observations. Checkpoint load counts are aggregate instrumented invocations divided by measured requests; logical I/O counts and latency are separate medians.

| Warm workload | Checkpoint loads / request | Logical storage/task calls P50 | Client latency P50 |
| --- | ---: | ---: | ---: |
| Empty history | 98 / 49 = 2 | 42 | 3.19 s |
| After ten completed turns | 5,100 / 50 = 102 | 579 | 57.29 s |

This is enough evidence to propose reducing ancestor reconstruction. It does not attribute all latency to DeltaChannel: response-chain resolution, repeated hosting reads, payload size and service variation also contribute. The existing local `MessagesState` fixture has no DeltaChannel history walk; its 38-request continuation baseline neither reproduces nor measures this finding. See [the analysis](../ANALYSIS.md#history-depth-amplification-with-deltachannel).

## Proposed change

Evaluate a Foundry-backed persistence policy that materializes every initialized, persistent channel in each newly saved checkpoint, including unchanged DeltaChannels. A saved checkpoint should restore its channel state without fetching ancestor checkpoints solely to reconstruct delta history. Untracked/runtime-only values remain excluded; subgraph checkpoint namespaces retain their existing meaning.

Keep `sync` durability, intermediate checkpoint saves and pending-write persistence unchanged. Recovery from any retained checkpoint must preserve task scheduling, interrupt/resume, errors, versions and pending outputs. A complete state snapshot does not replace pending writes from partially completed supersteps, and it does not imply one HTTP read for the complete tuple.

Create the materialized representation while LangGraph still has the live channel state, before the existing checkpoint is persisted. Use or add a supported graph/checkpoint integration, retaining LangGraph's [serialization and version bookkeeping][create]. The [saver's `aput()`][save] receives the already-built checkpoint, which may omit DeltaChannel values; it cannot simply turn that object into a full snapshot without acquiring more state. Do not reconstruct ancestors on every save, mutate graph-private fields, add dummy `update_state()` calls or overwrite previously committed checkpoint IDs to force materialization.

Lowering `snapshot_frequency` is a useful comparison, not an equivalent guarantee: [snapshot selection][cadence] is driven by each channel's updates and superstep counters. A channel not updated in a step may still be omitted at that checkpoint, even with frequency one. Snapshotting only at API completion is also a narrower alternative; it leaves arbitrary intermediate checkpoints dependent on history.

## Tradeoffs and compatibility

- Full state increases serialized bytes, CPU and storage. With steadily growing message history, repeating the whole history at every checkpoint can make cumulative write volume quadratic in history length. Measure bytes as well as request counts.
- State Store [limits serialized item values to 1 MB][limits]. Check size before persistence and define supported failure or separate durable large-value storage behavior; never truncate state or claim full materialization when channels are silently omitted. Larger files/messages may make this policy unsuitable.
- Prefer LangGraph-compatible snapshot values and serializer behavior. DeltaChannel's [snapshot contract is beta][delta]. Test the exact supported LangGraph/DeepAgents versions, legacy delta checkpoints and mixed old/new history. Do not assume old history becomes self-contained or prune its ancestors merely because new checkpoints are complete.
- Document how full channel values appear in raw State Store records; serialization may still make them opaque. Preserve checkpoint/reference identity and user/thread isolation.

This proposal changes representation and snapshot policy, not the choice to persist only at graph exit. No request-count reduction is promised for graph writes or for the non-delta local fixture. Existing independent metadata/client optimizations remain applicable.

## Benefit, implementation size and applicability

The target is zero ancestor-history reads for newly materialized channels; top-level checkpoint and pending-write reads remain. The observed 102 versus two tuple loads/request identifies a large optimization opportunity, but not all excess loads have been classified. If all 100 excess loads were removable history reconstruction, that would be an illustrative 98% ceiling for tuple-load invocations, not a target or total latency estimate. The ordinary non-delta local fixture should see no benefit. Larger snapshots may offset the read saving.

Estimate: 60–140 production lines in LangGraph for a supported snapshot-policy hook, 30–70 in langchain-azure for integration/validation, plus 300–600 test/fixture lines. A LangGraph release is an expected external dependency for the exact every-checkpoint policy. DeepAgents is a compatibility target; production changes there are conditional on how the policy is exposed. No core/service change is expected within item limits; large-value offload is additional scope. See [the estimate definitions](../ESTIMATES.md).

The main observed case is DeepAgents 0.7.25 with default delta-backed messages/files. This is not relevant to ordinary LangChain/LangGraph graphs without delta-backed state, or to requests without a checkpointer. It also applies to custom graphs using DeltaChannel, so it is not exclusive to DeepAgents. Cosmos [already bulk-loads delta history][cosmos-history]; the read/byte tradeoff still exists, but Foundry's per-ancestor request saving does not transfer directly. Legacy delta-written history may still need reconstruction after a channel-schema change.

## Components and validation

The snapshot-versus-replay tradeoff also applies to other DeltaChannel savers, including Cosmos DB. Their history-query implementation can differ from Foundry's default walk; do not transfer these call counts or assume the same benefit without a separate comparison.

| Owner | Required work |
| --- | --- |
| LangGraph / hosting | Identify a supported per-checkpoint snapshot-policy integration at [checkpoint creation][create]. Keep reducers, versions, interrupts, branching/replay and current durability semantics. Release any required upstream contract before hosting consumes it. |
| FoundryCheckpointSaver | Preserve [checkpoint serialization][save], pending writes and dual-history readability; verify size failures and raw inspection. No service batch API is required for this policy. |
| DeepAgents | Verify both messages and files, including unchanged channels, subagents and resumable tasks, against [the pinned schemas][messages]. Scope any channel-policy customization explicitly. |
| Tests / benchmark | Add a credential-free DeltaChannel graph with multiple state channels. Compare default cadence, frequency one and full materialization at matched history depths. Keep this separate from the existing simple-graph and 24/105-output probes. |

Acceptance requires identical state from selected intermediate and final checkpoints, with no ancestor-history reads needed for channels in newly materialized checkpoints. Cover interrupt/resume, successful sibling-task preservation after failure, process-loss recovery, concurrent branches, old delta history, deletion/retention, cancellation and oversized states. Do not establish recovery from a successful HTTP status alone.

Measure in ABBA order with identical graphs, versions, workloads and `sync` durability. Record checkpoint/history-loader invocations, ancestor depth, logical/HTTP attempts, item/key/metadata calls, serialized bytes, serialization/deserialization time, stored size, failures and end-to-end latency at several history depths. Report CPU/byte costs and remaining response-chain work separately. Local repro and controlled base/candidate measurements are implementation acceptance work, not prerequisites for recording the existing finding.

[messages]: https://github.com/langchain-ai/deepagents/blob/c6c340d47d2013a2e7b7230698f73977b7de6756/libs/deepagents/deepagents/graph.py#L72-L77
[files]: https://github.com/langchain-ai/deepagents/blob/c6c340d47d2013a2e7b7230698f73977b7de6756/libs/deepagents/deepagents/middleware/filesystem.py#L1297-L1301
[restore]: https://github.com/langchain-ai/langgraph/blob/41c9c22fdc83d68a2859d0c9a2b627c8615a792f/libs/langgraph/langgraph/pregel/_checkpoint.py#L502-L534
[history]: https://github.com/langchain-ai/langgraph/blob/41c9c22fdc83d68a2859d0c9a2b627c8615a792f/libs/checkpoint/langgraph/checkpoint/base/__init__.py#L680-L719
[create]: https://github.com/langchain-ai/langgraph/blob/41c9c22fdc83d68a2859d0c9a2b627c8615a792f/libs/langgraph/langgraph/pregel/_checkpoint.py#L268-L342
[cadence]: https://github.com/langchain-ai/langgraph/blob/41c9c22fdc83d68a2859d0c9a2b627c8615a792f/libs/langgraph/langgraph/pregel/_checkpoint.py#L90-L118
[save]: https://github.com/langchain-ai/langchain-azure/blob/d6befba95a86f55328b2e95eb61151a2f8889485/libs/azure-ai/langchain_azure_ai/agents/hosting/_foundry_checkpoint_saver.py#L296-L337
[delta]: https://reference.langchain.com/python/langgraph/channels/delta/DeltaChannel
[limits]: https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-state-store?tabs=python#limits
[cosmos-history]: https://github.com/langchain-ai/langchain-azure/blob/d6befba95a86f55328b2e95eb61151a2f8889485/libs/azure-cosmosdb/src/langchain_azure_cosmosdb/aio/_langgraph_checkpoint_store.py#L272-L335
