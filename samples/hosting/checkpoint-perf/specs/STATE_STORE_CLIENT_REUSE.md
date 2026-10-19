# Proposal: Reuse State-Store Clients and Transports

Status: proposed; no implementation or measured improvement. Source links pin the local baseline: hosting `cb9db867`, AgentServer core `f00fb28c`.

## Current behavior

Each [saver operation][saver] acquires a store client and closes it in `finally`. Each [conversation-chain read/write][chain] similarly creates a client and closes it through `async with`. Core's [`get_or_create`][state] constructs a new store-bound client each time; [`FoundryStorageClient`][client] constructs its HTTP pipeline, and `aclose()` closes that pipeline.

The [local baseline](../results/RESULTS.md) records nine transport constructions per conversation request. The fixture measures construction/closure, not real network setup, connection-pool or token-acquisition costs. Reusing a connection can reduce setup work; remote savings remain unmeasured, and this proposal does not reduce item or metadata reads.

## Proposed change

Use one handler-owned scope to reuse compatible clients or their HTTP connection pool. Begin before [`build_runnable_config`][host] performs chain-store reads; keep it alive through graph work and final chain-store writes. Saver/chain-store operations borrow resources; only the scope owner closes owned resources, after all outstanding work settles on success, failure or cancellation. Preserve standalone use outside a host.

Reuse store clients only for compatible store name, endpoint/API version, isolation/TTL settings, credential, delegated user and pipeline options. Never share a store-bound client across stores or identities. Checkpoint and chain stores have different names; they may share a compatible transport/session without sharing store bindings.

Core already forwards transport options through `**kwargs`. First check that path and Azure Core's [borrowed-session ownership][transport] before requiring a new core API. A shared transport is unsafe if one borrower's close disables another; prove ownership/closure behavior in tests. Add a supported injection/ownership contract only if existing APIs cannot express it. Do not access private clients.

Preserve per-operation metadata refresh and isolation/TTL validation. Client reuse must not silently skip `get_or_create`'s metadata read; [descriptor reuse](STATE_STORE_DESCRIPTOR_REUSE.md) is separate. Keep conflict handling, retry/write semantics and backend errors unchanged.

Build platform headers and call IDs per outbound request, as [`_request`][headers] does today; keep User-Agent evaluation lazy. Explicit `user_id` is a client binding and must remain isolated. Borrowed credentials/transports stay caller-owned. Cross-request caches are outside scope.

## Components and validation

| Owner | Required work |
| --- | --- |
| `langchain-azure-ai` | Establish scope in [Responses hosting][host]; adapt [saver][saver] and [chain-store][chain] acquisition/closure while retaining standalone behavior. |
| AgentServer core / Azure Core | Verify [pipeline construction/closure][client], [store/credential ownership][state] and [session borrowing][transport]. Change core only if a supported contract is missing. Responses dispatch is a compatibility target, not a presumed production change. |
| Tests / sample | Extend saver, `test_resilient.py`, `test_responses_host.py` and [core storage tests][core-tests]. Cover concurrent operations, distinct stores/users/options, current headers, failure/cancellation and owner-only closure after children settle. |

Compare the client-only change in ABBA order (base, candidate, candidate, base), with matched dependencies and histories. Report pipeline/transport construction and closure separately, descriptor/item counts, storage interval union and elapsed time. Metadata counts must stay unchanged; session-only reuse may leave pipeline construction unchanged. Test actual pooling with a real transport and matched remote workloads; retain failures/throttling. Synthetic construction counts alone do not prove latency savings.

No LangGraph or service API change is expected. If a new core/Responses contract is required, release it before updating hosting's dependency floor and sample requirements. Measure each proposal separately, or report its incremental effect explicitly.

[saver]: https://github.com/langchain-ai/langchain-azure/blob/cb9db867b4e4f7dab74995254efe3332a6b6e2f5/libs/azure-ai/langchain_azure_ai/agents/hosting/_foundry_checkpoint_saver.py#L171-L204
[chain]: https://github.com/langchain-ai/langchain-azure/blob/cb9db867b4e4f7dab74995254efe3332a6b6e2f5/libs/azure-ai/langchain_azure_ai/agents/hosting/_responses/conversation_chain_store.py#L179-L235
[host]: https://github.com/langchain-ai/langchain-azure/blob/cb9db867b4e4f7dab74995254efe3332a6b6e2f5/libs/azure-ai/langchain_azure_ai/agents/hosting/_responses_host.py#L870-L976
[state]: https://github.com/Azure/azure-sdk-for-python/blob/f00fb28c5a53593d3dc74d8bebe204e27a604c7f/sdk/agentserver/azure-ai-agentserver-core/azure/ai/agentserver/core/storage/_state.py#L158-L317
[client]: https://github.com/Azure/azure-sdk-for-python/blob/f00fb28c5a53593d3dc74d8bebe204e27a604c7f/sdk/agentserver/azure-ai-agentserver-core/azure/ai/agentserver/core/storage/_client.py#L55-L99
[headers]: https://github.com/Azure/azure-sdk-for-python/blob/f00fb28c5a53593d3dc74d8bebe204e27a604c7f/sdk/agentserver/azure-ai-agentserver-core/azure/ai/agentserver/core/storage/_state.py#L215-L240
[transport]: https://github.com/Azure/azure-sdk-for-python/blob/f00fb28c5a53593d3dc74d8bebe204e27a604c7f/sdk/core/azure-core/azure/core/pipeline/transport/_aiohttp.py#L120-L210
[core-tests]: https://github.com/Azure/azure-sdk-for-python/blob/f00fb28c5a53593d3dc74d8bebe204e27a604c7f/sdk/agentserver/azure-ai-agentserver-core/tests/test_foundry_state_store.py
