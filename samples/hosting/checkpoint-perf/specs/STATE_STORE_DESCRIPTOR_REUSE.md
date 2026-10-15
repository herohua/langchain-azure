# Spec: Reuse State-Store Descriptors

Status: proposed; no implementation or measured improvement.

## Goal and evidence

Remove the descriptor GET immediately repeated after `FoundryStateStore.get_or_create`, without changing client construction or closure.

The [local baseline](../results/RESULTS.md) records 18 descriptor GETs and nine transport constructions per conversation request. Run the [sample](../README.md) with default settings to reproduce these counts. `get_or_create` fetches properties, then the hosting callers immediately call `get` again for validation.

## Scope and proposed change

Owner: `langchain-azure-ai` hosting, coordinating with AgentServer core storage if a supported descriptor accessor is needed.

Reuse the properties already fetched by `get_or_create` for the caller's isolation/TTL validation. Remove only the immediately repeated `store.get()` in checkpoint saver and conversation-chain operations. Preserve the existing return contract; avoid private-field access.

Each later saver/chain-store operation still refreshes and validates metadata. Keep client construction, closure, user isolation, TTL mismatch behavior, creation/conflict handling, retries, and backend error propagation unchanged.

Validating only once per handler is a separate, later optimization. It can miss a same-name store being deleted and recreated with different settings during the handler. Do not include it without an explicit decision accepting that behavior or a supported invalidation mechanism. [Client/transport reuse](STATE_STORE_CLIENT_REUSE.md) is also a separate change.

## Acceptance and handoff

- Focused tests prove removal of the duplicate GET with unchanged client construction/closure and isolation/TTL rejection, including changed properties between operations. Preserve create/conflict/error/cancellation cleanup. Reuse existing saver/host tests.
- Compare descriptor-only base/candidate in small ABBA blocks with identical dependencies and sample settings. Compare corresponding conversation turns and the fresh-conversation control.
- Report descriptor GETs, client/transport constructions, storage interval union, and elapsed time. Retain failures separately. The sample has no model/search intervals to subtract.
- Accept only with unchanged correctness and fewer descriptor GETs; quantify end-to-end improvement rather than equating removed HTTP intervals with guaranteed savings.

Deliver an SDK PR with focused tests and concise before/after results.

## Components and package boundaries

Source baseline: `langchain-azure` commit `cb9db867b4e4f7dab74995254efe3332a6b6e2f5`; `azure-sdk-for-python` commit `f00fb28c5a53593d3dc74d8bebe204e27a604c7f`.

| Package / repository | Component | Expected work |
| --- | --- | --- |
| `langchain-azure-ai` / `langchain-azure` | Hosting [checkpoint saver](../../../../libs/azure-ai/langchain_azure_ai/agents/hosting/_foundry_checkpoint_saver.py), `_get_or_create_store` | Required: validate the descriptor already resolved by core instead of issuing the second GET. |
| `langchain-azure-ai` / `langchain-azure` | Hosting [conversation-chain store](../../../../libs/azure-ai/langchain_azure_ai/agents/hosting/_responses/conversation_chain_store.py), `get`, `set`, `_validate_store` | Required: consume the resolved descriptor while preserving `on_mismatch` semantics. This is Responses bookkeeping, not application memory. |
| `azure-ai-agentserver-core` / `azure-sdk-for-python` | [storage/_state.py](https://github.com/Azure/azure-sdk-for-python/blob/f00fb28c5a53593d3dc74d8bebe204e27a604c7f/sdk/agentserver/azure-ai-agentserver-core/azure/ai/agentserver/core/storage/_state.py), `FoundryStateStore.get_or_create`, `_fetch_properties`, `_create_properties` | Expected prerequisite at this baseline: expose the resolved descriptor through a supported, backward-compatible contract, covering fetch/create/conflict-refetch and local storage. Do not change the existing return type. |
| Both packages | Hosting `test_foundry_checkpoint_saver.py`, `test_resilient.py`; core `tests/test_foundry_state_store.py` | Required: descriptor freshness/mismatch, reduced GET count, and unchanged lifecycle/error behavior. |
| Sample / `langchain-azure` | `benchmark.py`, `storage_transport.py`, `test_benchmark.py` | Verification: compare metadata counts and timings; construction/closure counts must remain unchanged. Update fixture setup only if the supported core contract requires it. |

Core storage/pipeline lifetime, `azure-ai-agentserver-responses`, and LangGraph require no behavior changes for this requirement. If a new core API is needed, deliver/release it first, then update hosting's `libs/azure-ai/pyproject.toml` minimum dependency and the sample requirements. Test both against that version; do not silently rely on an unreleased or private API.
