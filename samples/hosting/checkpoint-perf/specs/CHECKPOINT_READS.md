# Spec: Reduce Checkpoint Restoration Overhead

Status: proposed; no implementation or measured improvement.

## Goal and evidence

First reduce sequential pending-write read latency. Checkpoint reuse is an optional follow-up, not a condition for delivering concurrent reads.

`_pending_writes` lists keys for the selected checkpoint, then awaits each item read sequentially and sorts by `(task_id, index)`. It does not scan all checkpoint history. The [local baseline](../results/RESULTS.md) restores 24 pending writes with 25 item GETs, one key list, and storage concurrency 1. Run the [sample](../README.md) with defaults to reproduce; `--pending-writes 105 --delay-ms 0` exercises pagination. These reads are a clear optimization target, but do not explain every source of conversation latency.

At baseline `cb9db867`, the normal host path calls `detect_pending_interrupts()` (which uses `aget_state`) before `graph.astream()`, creating a repeated-loading path. Recovery with an existing current-response checkpoint reference skips that check; recovery without a reference follows the normal path. Tracing must establish which reads actually target the same checkpoint before proposing reuse.

## Scope and proposed change

Primary owner: `langchain-azure-ai` checkpoint saver and Responses hosting. This work can start independently of [descriptor reuse](STATE_STORE_DESCRIPTOR_REUSE.md) and [client/transport reuse](STATE_STORE_CLIENT_REUSE.md); measure the changes separately.

### First delivery: bounded concurrent reads

Replace serial item reads within each listed page. Start with an internal limit of eight outstanding item reads per `_pending_writes()` invocation; process one page at a time. This is not a saver-wide or process-wide limit: concurrent invocations can multiply total traffic. Do not create one unbounded task per key. Preserve pagination and final `(task_id, index)` ordering. No new public option or backend batch endpoint is required.

On read failure or cancellation, cancel and await all tasks created by that invocation before closing its store. Preserve the existing exception type/propagation and cancellation behavior; task grouping must not expose a new exception wrapper. Keep missing-item handling and backend retries unchanged.

Checkpoint writes, history retention, storage schema, and the graph's public API remain unchanged. Do not remove interrupt inspection or add cross-thread memory.

## Correctness constraints

- Preserve pending-write content/order, checkpoint selection, user/thread isolation, write durability, interrupt/resume, and recovery semantics.
- Retain backend retries and report throttling. Per-invocation concurrency does not cap aggregate traffic across requests.

### Conditional follow-up: checkpoint reuse

Only pursue reuse after tracing confirms duplicate reads and mutation safety can be established. Scope reuse to the invocation, identity, thread, namespace, and checkpoint selection.

- Invalidate on `aput_writes()`, `aput()`, deletion, and request failure/cancellation/exit. Pending writes can increase or be replaced under the same checkpoint ID, so ID equality alone is insufficient. Prevent an in-flight read from repopulating stale data after invalidation, for example with a generation check. Do not cache a whole tuple if other writers cannot be accounted for.
- Return independently mutable state to every caller. State inspection and execution can mutate loaded checkpoints; never return the same mutable `CheckpointTuple` to both. Prefer caching serialized data and independently deserializing each return, including nested checkpoint, metadata, config, and pending-write values.
- Add mutation-isolation, pending-write invalidation, invalidation/read-race, deletion, and request-isolation tests. If a safe integration is unavailable, deliver concurrent reads alone.

## Acceptance and handoff

- Focused tests cover multiple pages, out-of-order completion, missing items, and the eight-read bound. Failure/cancellation tests prove all spawned tasks settle before store closure and the original error propagates. Cover simultaneous invocations to document the aggregate-traffic limit. Existing checkpoint round-trip and host interrupt/recovery tests must pass.
- The harness must still verify persisted file/TODO/message state after host reconstruction and pending-write content/order. It does not test process-crash recovery.
- Compare base/candidate in small ABBA blocks with identical dependencies and matched four-turn histories. Include the fresh-conversation control and the 24/105-write probes; compare equivalent workload positions.
- Record store acquisition/validation, key pagination, item GET, and deserialization timings separately, plus returned serialized-data bytes. Record logical reads and actual HTTP attempts including retries, maximum concurrency, and end-to-end elapsed time. Extend instrumentation for this breakdown; the current harness does not capture every stage. Overlapping timings must not be summed as elapsed time.
- Retain failures and throttling separately. Parallelism may reduce elapsed time without reducing HTTP count. If model/search intervals are subtracted in a service test, call the result **remaining elapsed time**: it can still contain other tool execution, writes, and queueing. The offline sample has no model/search calls.

Deliver an SDK PR with focused tests and a concise before/after result. Claim improvement only for measured paths; fewer duplicate reads and faster parallel reads require separate evidence.

## Code entry points

Baseline: `langchain-azure` commit `cb9db867b4e4f7dab74995254efe3332a6b6e2f5`.

- [Checkpoint saver](https://github.com/langchain-ai/langchain-azure/blob/cb9db867b4e4f7dab74995254efe3332a6b6e2f5/libs/azure-ai/langchain_azure_ai/agents/hosting/_foundry_checkpoint_saver.py): `aget_tuple`, `_checkpoint_tuple`, `_pending_writes`.
- Hosting `_converters/_hitl.py`: pending-interrupt `aget_state` lookup; `_responses_host.py`: invocation and recovery paths.
- Existing tests: `tests/unit_tests/agents/hosting/test_foundry_checkpoint_saver.py` and `test_responses_host.py`.
