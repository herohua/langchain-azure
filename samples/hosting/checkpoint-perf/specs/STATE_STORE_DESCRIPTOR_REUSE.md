# Proposal: Reuse State-Store Descriptors

Status: proposed; no implementation or measured improvement. Source links pin the local baseline: hosting `cb9db867`, AgentServer core `f00fb28c`.

## Current behavior

A descriptor holds store settings such as user isolation and item time-to-live (TTL); it is separate from item data.

1. Each saver operation calls hosting's [`_get_or_create_store`][saver], which calls core's [`FoundryStateStore.get_or_create`][resolve]. Core constructs a client, fetches the descriptor, or creates the store after a not-found response.
2. Core discards the returned descriptor and returns only the client. Hosting then calls [`store.get()`][get] to fetch the descriptor again and validate isolation/TTL.
3. The [conversation-chain store][chain] repeats this pattern in `get`/`set`, unless `on_mismatch="ignore"` skips validation.

The [local baseline](../results/RESULTS.md) records 18 descriptor GETs and nine transport constructions per conversation request. These metadata reads are not `get_item()` calls.

## Proposed change

Expose the descriptor resolved by core through a supported, backward-compatible accessor, then use it for hosting validation. Capture the server result on fetch, create and create-conflict/refetch paths, and provide equivalent local-backend behavior. Do not change `get_or_create`'s return type or read private fields.

Remove only the immediate second GET. Each later saver/chain-store operation must still resolve fresh metadata and validate it. Preserve `on_mismatch`, creation/conflict handling, errors, cancellation cleanup, client construction and closure. Validate the descriptor from that acquisition; this is not an atomic guarantee against concurrent store deletion/recreation.

For nine successful existing-store acquisitions without retries, the expected signal is 18 -> 9 descriptor GETs, with nine transports unchanged. Item reads and writes should not change. Handler-wide metadata caching and [client reuse](STATE_STORE_CLIENT_REUSE.md) are separate changes.

## Components and validation

| Owner | Required work |
| --- | --- |
| `azure-ai-agentserver-core` | Retain/expose the resolved descriptor in [storage `_state.py`][resolve]; keep explicit `get()` as a fresh fetch. Cover fetch/create/conflict, local storage and failure/cancellation cleanup in [core tests][core-tests]. |
| `langchain-azure-ai` | Consume it in the [saver][saver] and [chain-store validator][chain]. Extend saver and `test_resilient.py` tests for isolation/TTL rejection, both mismatch modes and changed settings between operations. |
| Sample | Verify descriptor counts fall while item calls, transport construction/closure and persisted state stay unchanged. |

Release the supported core API before raising hosting's dependency floor and sample requirements. Compare the descriptor-only change in ABBA order (base, candidate, candidate, base), with identical dependencies and corresponding conversation turns. Report metadata GETs, transport counts, storage interval union and elapsed time; retain failures. Removed HTTP intervals do not equal guaranteed end-to-end savings.

[saver]: https://github.com/langchain-ai/langchain-azure/blob/cb9db867b4e4f7dab74995254efe3332a6b6e2f5/libs/azure-ai/langchain_azure_ai/agents/hosting/_foundry_checkpoint_saver.py#L429-L460
[chain]: https://github.com/langchain-ai/langchain-azure/blob/cb9db867b4e4f7dab74995254efe3332a6b6e2f5/libs/azure-ai/langchain_azure_ai/agents/hosting/_responses/conversation_chain_store.py#L144-L235
[resolve]: https://github.com/Azure/azure-sdk-for-python/blob/f00fb28c5a53593d3dc74d8bebe204e27a604c7f/sdk/agentserver/azure-ai-agentserver-core/azure/ai/agentserver/core/storage/_state.py#L243-L336
[get]: https://github.com/Azure/azure-sdk-for-python/blob/f00fb28c5a53593d3dc74d8bebe204e27a604c7f/sdk/agentserver/azure-ai-agentserver-core/azure/ai/agentserver/core/storage/_state.py#L463-L474
[core-tests]: https://github.com/Azure/azure-sdk-for-python/blob/f00fb28c5a53593d3dc74d8bebe204e27a604c7f/sdk/agentserver/azure-ai-agentserver-core/tests/test_foundry_state_store.py
