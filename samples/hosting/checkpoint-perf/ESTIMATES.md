# Optimization Estimates and Applicability

Planning estimates for the source versions pinned in [ANALYSIS.md](ANALYSIS.md), not measured candidate results. Each row assumes that proposal is applied alone. Percentages refer to calls or a named component, never guaranteed end-to-end latency. Do not add rows: read reuse/materialization also remove metadata/client work targeted elsewhere.

Code ranges count approximate added or modified hand-written lines, not net additions. Production code and tests/benchmark fixtures are separate; docs, generated SDK code and dependency locks are excluded. These are scope estimates, not implementation commitments. Service implementation size cannot be estimated from the client repositories.

Confidence is higher for the call-count arithmetic than for code size. Ranges for 1/3.1 come from small, identified functions; 2/3.2/4/5 depend on API/ownership/rollout choices and need re-estimation once those contracts are agreed. No production patch was written to derive these ranges.

Notation: 3.1/3.2 are complementary subchanges; 4 (Option A)/4 (Option B) are alternative implementations.

## Independent benefit estimates

| Proposal / delivery | Baseline and independent target | Expected latency effect |
| --- | --- | --- |
| [1. Descriptor reuse](specs/STATE_STORE_DESCRIPTOR_REUSE.md) | Continuation 38 → 28 storage HTTP calls (-26%); descriptor GETs 20 → 10. First/fresh 33 → 24 (-27%). The 24-output probe 28 → 27 (-4%). | Removes one metadata round trip per acquisition; overlap and service latency determine the elapsed saving. |
| [2. Client reuse](specs/STATE_STORE_CLIENT_REUSE.md) | Full handle reuse: continuation 10 → 2 store-bound clients (-80%), first/fresh 9 → 2 (-78%); compatible connections may use one pool. Pool-only reuse retains nine/ten clients but targets one owned HTTP session/pool. HTTP calls remain 38/33. | Real connection/pipeline setup may fall. The mock cannot quantify connection-pooling gains; no latency percentage is supported. |
| [3.1 Parallel pending-write reads](specs/CHECKPOINT_READS.md#31-parallel-pending-write-reads) | HTTP count unchanged. The 24-read segment can run in three waves of eight rather than 24 serial waits. | Ideal equal-delay model: that segment's wait falls 87.5%; whole 28-call probe roughly 75% if its other four calls remain serial. Not a benchmark prediction. Normal continuation has no pending items, so this subchange has no expected benefit there. |
| [3.2 Checkpoint reuse](specs/CHECKPOINT_READS.md#32-checkpoint-reuse) | Under exclusive thread ownership/current-state validation, three reconstructions → one. A conservative fixture target retains one latest-key lookup: 38 → 30 calls (-21%), item GETs 4 → 2, key lists 4 → 2, clients 10 → 8. | Removes duplicate remote read work; selection validation may add calls. This target excludes lock/lease traffic and is invalid if the ownership contract cannot be met. |
| [4 (Option A) Pending-write packing](specs/PENDING_WRITE_PACKING.md#option-a-pending-write-packing) | Fresh ordinary task outputs: six → two write calls (-67% for that segment); all writes ten → six (-40%); continuation 38 → 34 (-11%). | Fewer write round trips. Conflict reconciliation, larger payloads or chunking can reduce or reverse the saving. |
| [4 (Option B) Batch writes](specs/PENDING_WRITE_PACKING.md#option-b-batch-writes) | Same fresh-path HTTP target as 4 (Option A), but retain six output items. No item-read reduction is implied. | Saves client/server round trips, not necessarily backend operations or write cost. Requires a new service batch API and SDK support; not available in the documented/pinned interface. |
| [5. Full checkpoint snapshots](specs/CHECKPOINT_MATERIALIZATION.md) | Newly materialized DeltaChannel checkpoints: zero ancestor reads for state reconstruction; top-level tuple loads and pending-write reads remain. Hosted ten-turn evidence had 102 tuple loads/request, versus two with empty history. A large fraction is a candidate for removal, but 102 → 2 is not an established target without classifying all loads. No benefit predicted for the non-delta 38-call fixture. | Highest potential for the observed deep-history path; no defensible total percentage or seconds estimate yet. More snapshot bytes, CPU and item-size constraints can offset fewer reads. |

A 5 ms configured mock delay is not a measured service round trip. The modeled wave estimate for 3.1 assumes equal durations, no throttling/retries and negligible scheduling/serialization cost.

## Repositories, code size and external prerequisites

An external dependency means another repository/package release, service API or enforced coordination guarantee is needed before `langchain-azure` can deliver the stated behavior. Verification against another repository alone is not a required code change.

| Proposal / delivery | Production code estimate by repository | Tests / benchmark code, total | Dependency from langchain-azure's perspective |
| --- | --- | ---: | --- |
| 1. Descriptor reuse | `langchain-azure` 15–35; `azure-sdk-for-python` core 30–60 | 120–220 | **Required core change/release**: expose the resolved descriptor on fetch/create/conflict/local paths. No State Store API change. |
| 2. Client reuse — pool-only | `langchain-azure` 40–90 | 120–220 | **No external code change expected** if existing `transport`/borrowed-session injection passes lifecycle tests. No service API change. This does not deliver the two-handle target. |
| 2. Client reuse — full handle reuse | `langchain-azure` 100–180; no core changes | 250–450 | **No external change required**: hosting owns/reuses/closes handles, initially acquires with `get_or_create()` and refreshes with existing public `get()`. Handle missing-store/reacquisition through current APIs. Preserve the metadata-call policy for isolated measurement. Descriptor proposal 1 is not required. |
| 3.1 Parallel pending-write reads | `langchain-azure` 40–80 | 180–320 | **No external change** expected: concurrent reads use the existing core API. |
| 3.2 Checkpoint reuse | `langchain-azure` 120–220; no LangGraph/core changes | 300–600 | **No external API/package change required**: host-managed request scope and saver-local caching use existing `aget_tuple()` calls. Exact-ID and verified-latest selections remain distinct. A deployment-wide ownership contract is required; missing coordination is additional work outside these line ranges. |
| 4 (Option A) Pending-write packing | `langchain-azure` 140–260 | 300–550 | **No external API change expected**: existing create/get/ETag replace operations suffice in principle. A dual-format rollout and writer coordination are required; this is a data-format dependency even within one repo. |
| 4 (Option B) Batch writes | TBD after the service batch contract is designed; previous client-only ranges withdrawn | TBD, including service and SDK tests | **New State Store API/backend capability required**, followed by core SDK support/release and hosting integration. Public documentation and the pinned SDK expose no batch-write operation. This is a service feature proposal, not merely SDK wrapping. |
| 5. Full checkpoint snapshots | `langgraph` 60–140; `langchain-azure` 30–70 | 300–600 | **Expected LangGraph policy hook/release** for exact every-checkpoint materialization; the saver alone lacks live channel values. DeepAgents is a compatibility target, not a required production change unless policy configuration cannot pass through it. No State Store API change for payloads within limits. Durable large-value offload, if needed, is additional scope. |

Tests above include failure/cancellation, isolation, compatibility and the necessary local benchmark changes. They are larger than production diffs because ownership, stored formats and recovery semantics need targeted checks. No blanket refactor, Cosmos implementation or new distributed lock service is included.

## General applicability

Applicability follows the code path, not merely the framework name. `ResponsesHostServer` uses Responses history when there is no checkpointer; [it returns before chain-store checkpoint lookup][host-config]. With a custom/Cosmos saver, it can still use a separate Foundry-backed conversation-chain store. That remaining store can benefit from 1/2 independently of the graph's saver.

| Proposal | No checkpointer in the standard host | Foundry saver, ordinary LangChain/LangGraph | Cosmos DB saver | DeepAgents / other DeltaChannel graph |
| --- | --- | --- | --- | --- |
| 1. Descriptor reuse | No checkpoint-path saving. Applies only if application code separately uses the same State Store acquisition pattern. | Applies to saver and default Foundry chain store. | No duplicate descriptor path in the Cosmos saver; still applies to a retained Foundry chain store. | Same Foundry rules; DeepAgents is not required. |
| 2. Client reuse | General connection reuse is good practice, but this proposal does not optimize Responses/history clients. | Applies to repeated store clients/pools. | Saver already retains its Cosmos client/container; avoid recreating it per request. Foundry chain-store reuse remains separate. | Same backend-dependent rules; not DeepAgents-specific. |
| 3.1 Parallel pending-write reads | No checkpoint reads to optimize. | Applies when restoring multiple pending items. | The key-list plus per-item GET loop is absent; this implementation does not transfer. | Same backend-dependent behavior; delta-history work is separately targeted by 5. |
| 3.2 Checkpoint reuse | No checkpoint reads to optimize. | Applies to repeated state loads under the ownership contract. | May avoid repeated whole-state queries under the same contract. | Applies to repeated tuple loads; distinct ancestors needed for delta reconstruction are separate work. |
| 4 (Option A) Pending-write packing | No saver pending-write calls. | Applies to multi-output tasks; changes stored format. | Per-output writes exist, but a separate format implementation/rollout is needed. No proportional RU/read saving is assumed. | Same multi-output-task opportunity; DeepAgents is not required. |
| 4 (Option B) Batch writes | No saver pending-write calls. | Applies to multi-output tasks only after service/SDK batch support is added. | A separate backend-specific batch integration and semantics review is needed; no Cosmos implementation is proposed here. | Same multi-output-task opportunity; DeepAgents is not required. |
| 5. Full checkpoint snapshots | Not applicable. | Not applicable to ordinary non-delta graphs such as `MessagesState`/standard `AgentState` without custom delta channels. Old delta-written history can still require reconstruction after a schema change. | Only for delta-backed state; [Cosmos already overrides history loading with bulk queries][cosmos-history], so Foundry's per-ancestor request reduction does not transfer directly. | Main observed case: DeepAgents 0.7.25 defaults to DeltaChannel messages/files. Also applies to custom LangGraph/LangChain graphs that opt into DeltaChannel; not exclusive to DeepAgents. |

## Delivery order

Start external API discussions for 1 and 5 early. 1 has the clearest request-count reduction; 3.1 is a small independent hosting change with benefit concentrated in multi-output recovery. Investigate 2 pool-only without waiting for a new core API. Scope 3.2's ownership contract before estimating rollout cost. Treat 4 (Option A)/4 (Option B) as alternatives, and 5 as the high-potential deep-history optimization with a distinct byte/correctness benchmark. No proposal requires another proposal to ship first; combined gains need a separate matched run.

[host-config]: https://github.com/langchain-ai/langchain-azure/blob/d6befba95a86f55328b2e95eb61151a2f8889485/libs/azure-ai/langchain_azure_ai/agents/hosting/_responses_host.py#L635-L664
[cosmos-history]: https://github.com/langchain-ai/langchain-azure/blob/d6befba95a86f55328b2e95eb61151a2f8889485/libs/azure-cosmosdb/src/langchain_azure_cosmosdb/aio/_langgraph_checkpoint_store.py#L272-L335
