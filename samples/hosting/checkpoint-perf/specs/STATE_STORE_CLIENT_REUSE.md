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

## Components and package boundaries

Source baseline: `langchain-azure` commit `cb9db867b4e4f7dab74995254efe3332a6b6e2f5`; `azure-sdk-for-python` commit `f00fb28c5a53593d3dc74d8bebe204e27a604c7f`.

| Package / repository | Component | Expected work |
| --- | --- | --- |
| `langchain-azure-ai` / `langchain-azure` | Hosting [checkpoint saver](../../../../libs/azure-ai/langchain_azure_ai/agents/hosting/_foundry_checkpoint_saver.py), `_get_or_create_store`, operation-level `aclose` | Required: borrow a compatible handler-owned client; keep metadata refresh per operation and move final resource closure to the owner. Preserve standalone saver usage. |
| `langchain-azure-ai` / `langchain-azure` | Hosting [conversation-chain store](../../../../libs/azure-ai/langchain_azure_ai/agents/hosting/_responses/conversation_chain_store.py), `get`, `set`, `async with` lifetime | Required: use the same ownership rules for its own store binding; preserve mismatch validation and standalone calls. |
| `langchain-azure-ai` / `langchain-azure` | Hosting `_responses_host.py`, `ResponsesHostServer.handle_create` | Required: establish handler scope, propagate it to graph/chain-store operations, and close owned resources after tasks settle on completion/error/cancellation. |
| `azure-ai-agentserver-core` / `azure-sdk-for-python` | [storage/_client.py](https://github.com/Azure/azure-sdk-for-python/blob/f00fb28c5a53593d3dc74d8bebe204e27a604c7f/sdk/agentserver/azure-ai-agentserver-core/azure/ai/agentserver/core/storage/_client.py), pipeline construction/`aclose`; `storage/_state.py`, constructor/`aclose`/`_request` | Conditional: add supported client/transport injection or borrowed-resource ownership if current APIs cannot express it. Verify per-call identity/headers and owned-credential cleanup even if core code stays unchanged. |
| `azure-ai-agentserver-responses` / `azure-sdk-for-python` | Handler dispatch/lifecycle | Verification only by default. Change only if hosting cannot keep the storage scope alive through all handler work using the current contract; document that separate dependency. |
| Both affected packages | Hosting `test_foundry_checkpoint_saver.py`, `test_resilient.py`, `test_responses_host.py`; core storage lifecycle tests | Required: shared/borrowed ownership, parallel operations, identity isolation, and closure after error/cancellation. |
| Sample / `langchain-azure` | `benchmark.py`, `storage_transport.py`, `test_benchmark.py` | Required verification; adapt fixture injection only as needed to the supported ownership contract. Compare construction/closure counts while metadata refresh counts stay unchanged. |

LangGraph execution and the storage service API need no changes by default. If core or Responses requires a new contract, deliver/release that package first, then update hosting's minimum dependency and sample requirements. Client reuse must not depend on the descriptor-reuse change.
