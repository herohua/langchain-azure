# Checkpoint reuse POC

Implements [proposal 3.2 in PR #1](https://github.com/herohua/langchain-azure/pull/1) through the existing saver interface. The Responses host owns a request scope before reference selection and releases it after graph persistence and the final chain-store update. SDK production code is unchanged.

`reuse.py` contains the POC. `benchmark.py` and `storage_transport.py` adapt the diagnostic from PR #1 at `64d010c3691b8be517d002c8b7c493f39f94a481`. They run the real Responses host, Foundry saver and storage HTTP pipeline with private in-memory service state. No Azure credentials, model, deployment or external requests are needed. Host response/task storage uses temporary local files.

## Ownership and reuse

- One saver and event loop own the private fixture. A saver-wide lock serializes request scopes and outside writers, including saver writes from `update_state()`. Graph persistence tasks inherit the scope. The host closes its inner event generator before releasing ownership.
- The first read selects the actual latest checkpoint and reconstructs its pending writes. Only a matching exact reference can reuse it. LangGraph's later latest-checkpoint check still runs and sees the verified selection. An absent or stale reference disables reuse for that scope and uses ordinary reads. Historical replay remains available.
- A context-local entry includes platform identity, thread and namespace; its serialized tuple identifies the exact checkpoint. Every hit deserializes independent mutable values. Mutation invalidates before and after persistence, with a generation guard against stale in-flight refill. Partial writes, read failure, cancellation and scope exit discard cached data. Writes remain synchronous and durable at the same points as the base saver.

The local lock is **not a distributed lease**. Do not use this sample against shared storage: another saver, process, recovery worker or direct backend writer could invalidate its freshness assumption. Production adoption needs ownership covering every writer from initial selection through the final reference update, plus fencing/lease-loss handling. The POC uses pinned saver internals and keeps one cached selection per scope; it does not implement a general cache or shared client lifetime.

## Run

From this directory, using Python 3.12:

```powershell
python -m uv venv --python 3.12
python -m uv pip install --python .venv/Scripts/python.exe -r requirements.txt
.venv/Scripts/python.exe benchmark.py --repeats 1 --output results/base.json
.venv/Scripts/python.exe benchmark.py --reuse --repeats 1 --output results/reuse.json
.venv/Scripts/python.exe -m unittest discover -s . -p 'test_*.py' -v
.venv/Scripts/python.exe verify_results.py
```

Use fresh output filenames; existing files are rejected. To repeat ABBA, run base, reuse, reuse, base in that order with identical arguments. Each invocation launches a fresh process and empty storage. Add `--pending-writes 105 --delay-ms 0` for pagination. `results/data/dependencies.txt` records all installed packages; pass it as `-c results/data/dependencies.txt` when installing to reproduce the retained environment.

Each run checks four conversation turns, a fresh-conversation control, reconstructed file/TODO/message state and exact pending-write content/order. `pending-writes` performs one unscoped load as a control. `pending-reuse` performs exact, exact, latest reads in one owned scope. This separate probe has pending writes; the conversation fixture's continuation checkpoints have none.

[Results](results/RESULTS.md) retain the matched ABBA and pagination measurements, raw HTTP events, source hashes and dependency versions. Raw JSON is gzip-compressed to keep the review small; Python's standard `gzip` module can read it. `verify_results.py` checks the retained counts and recomputes timing medians.

## Validation and limits

The sample tests cover current/stale/latest selection, no-scope fallback, independent state, identity/thread/namespace separation, writes/deletion, partial failures, invalidation races, cancellation, inherited-scope expiry, exclusive ownership, graph state updates, interrupt/resume and host recovery from an older persisted response checkpoint. The recovery test checks that only the remaining node runs and ownership spans the final reference update. It injects a persisted response; it is not a process-kill test.

This delivery excludes 3.1 parallel reads, descriptor/client reuse, packing/batch writes, DeltaChannel snapshots and exit-time durability. Synthetic timings do not predict hosted latency or demonstrate a service reliability fix. The fixture partitions users locally and does not validate Foundry's server-side call-ID identity resolution. Runtime validation used Python 3.12; other Python versions and older dependency combinations were not exercised.
