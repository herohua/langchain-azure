# Spec: Reduce Checkpoint Restoration Overhead

Status: proposed; no implementation or measured improvement.

## Goal and evidence

Reduce repeated checkpoint loading and sequential pending-write reads during continued conversations.

`_pending_writes` lists keys, then awaits each item read sequentially. The [local baseline](../results/RESULTS.md) restores 24 pending writes with 25 item GETs, one key list, and storage concurrency 1. Run the [sample](../README.md) with defaults to reproduce; `--pending-writes 105 --delay-ms 0` exercises pagination. The host also calls `aget_state` before execution; whether it duplicates a checkpoint load needs tracing.

## Scope and proposed change

Primary owner: `langchain-azure-ai` checkpoint saver and Responses hosting. This work can start independently of [descriptor reuse](STATE_STORE_DESCRIPTOR_REUSE.md) and [client/transport reuse](STATE_STORE_CLIENT_REUSE.md); measure the changes separately.

1. Trace checkpoint reads during the host's pending-interrupt inspection (`aget_state`) and graph execution. Record the user/thread, namespace, checkpoint ID, read count, and duration without checkpoint contents. Determine whether both paths actually load the same checkpoint.
2. Replace serial item reads in `_pending_writes` with bounded concurrency within each listed page. Use an existing batch API only if supported; no new backend endpoint is required. Preserve pagination and final ordering by `(task_id, index)`.
3. If tracing confirms duplicate reads, reuse an unchanged checkpoint only within the same invocation and identity/thread/namespace/checkpoint scope. Invalidate after writes and never cache an unresolved latest-checkpoint lookup across mutations. If no supported safe integration exists, ship bounded reads alone and document that limit.

Checkpoint writes, history retention, storage schema, and the graph's public API remain unchanged. Do not remove interrupt inspection or add cross-thread memory.

## Correctness constraints

- Preserve pending-write content/order, missing-item behavior, error propagation, cancellation, and bounded task cleanup.
- Preserve checkpoint selection, user/thread isolation, write durability, interrupt/resume, and recovery semantics. Reused mutable state must not leak modifications between callers.
- Retain backend retries; parallel reads must not create unbounded requests or hide throttling.

## Acceptance and handoff

- Focused tests cover multiple pages, out-of-order read completion, missing items, read failures/cancellation, and the concurrency bound. Existing checkpoint round-trip and host interrupt/recovery tests must pass.
- The harness must still verify persisted file/TODO/message state after host reconstruction and pending-write content/order. It does not test process-crash recovery.
- Compare base/candidate in small ABBA blocks with identical dependencies and matched four-turn histories. Include the fresh-conversation control and the 24/105-write probes; compare equivalent workload positions.
- Report checkpoint/pending-write read counts and durations, maximum concurrency, and elapsed time. Retain failures and throttling separately. Parallelism may reduce elapsed time without reducing HTTP count.

Deliver an SDK PR with focused tests and a concise before/after result. Claim improvement only for measured paths; fewer duplicate reads and faster parallel reads require separate evidence.

## Code entry points

Baseline: `langchain-azure` commit `cb9db867b4e4f7dab74995254efe3332a6b6e2f5`.

- [Checkpoint saver](https://github.com/langchain-ai/langchain-azure/blob/cb9db867b4e4f7dab74995254efe3332a6b6e2f5/libs/azure-ai/langchain_azure_ai/agents/hosting/_foundry_checkpoint_saver.py): `aget_tuple`, `_checkpoint_tuple`, `_pending_writes`.
- Hosting `_converters/_hitl.py`: pending-interrupt `aget_state` lookup; `_responses_host.py`: invocation and recovery paths.
- Existing tests: `tests/unit_tests/agents/hosting/test_foundry_checkpoint_saver.py` and `test_responses_host.py`.
