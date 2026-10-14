# Spec: Reuse State-Store Clients and Transports

Status: proposed; no implementation or measured improvement.

## Goal and evidence

Reduce repeated client/pipeline construction and enable connection reuse during handler execution, without changing descriptor refresh policy.

The [local baseline](../results/RESULTS.md) recorded nine transport constructions per conversation request. Saver operations and conversation-chain operations repeatedly create and close clients. The synthetic transport counts constructions but does not model TCP/TLS, connection pools, or token acquisition cost. Production savings remain unmeasured.

## Scope and proposed change

Owner: `langchain-azure-ai` hosting, coordinating with AgentServer core storage for transport ownership support.

Reuse compatible store clients and their HTTP transport within one handler. Assign one lifecycle owner; close owned resources once after all handler work, including outstanding storage tasks, has completed or been cancelled. Individual saver operations must not close a client still used by another operation.

Share only resources whose endpoint, credential ownership, store binding, and identity scope are compatible. Never cache user identity or request headers. Resolve platform headers, delegated identity, call ID, and User-Agent for each outbound request. Preserve caller ownership of supplied credentials/transports and avoid retaining owned clients after handler completion.

Client reuse does not change descriptor refresh/validation frequency, isolation/TTL checks, creation/conflict handling, retries, or backend errors. [Descriptor reuse](STATE_STORE_DESCRIPTOR_REUSE.md) must be implemented and measured independently. Cross-request caches and application memory are outside scope.

## Acceptance and handoff

- Tests prove fewer constructions with unchanged descriptor GET frequency for the same workload, correct current identity/headers, and no cross-user client leakage.
- Cover concurrent storage operations and handler completion/error/cancellation. Owned resources close once after tasks settle; borrowed resources remain caller-owned. Reuse existing saver/host tests.
- Compare client-only base/candidate in small ABBA blocks with identical dependencies, runtime settings, and conversation histories. If applied after descriptor reuse, report that incremental delta explicitly.
- Report construction/closure counts, metadata GETs, storage interval union, and elapsed time. Validate connection-pooling gains separately with a real transport and remote matched-history runs; retain failures separately.

Deliver an SDK PR with focused tests and concise before/after results. Fewer synthetic constructions alone do not prove remote latency improvement.

## Code entry points

Source baseline: `langchain-azure` commit `cb9db867b4e4f7dab74995254efe3332a6b6e2f5`; `azure-sdk-for-python` commit `f00fb28c5a53593d3dc74d8bebe204e27a604c7f`.

- [Checkpoint saver](../../../../libs/azure-ai/langchain_azure_ai/agents/hosting/_foundry_checkpoint_saver.py): `_get_or_create_store`, operation-level `aclose`.
- [Conversation-chain store](../../../../libs/azure-ai/langchain_azure_ai/agents/hosting/_responses/conversation_chain_store.py): client construction and `async with` lifetime.
- AgentServer core [storage/_client.py](https://github.com/Azure/azure-sdk-for-python/blob/f00fb28c5a53593d3dc74d8bebe204e27a604c7f/sdk/agentserver/azure-ai-agentserver-core/azure/ai/agentserver/core/storage/_client.py): pipeline construction/ownership; `storage/_state.py`: current request headers.
