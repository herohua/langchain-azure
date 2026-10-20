# Descriptor reuse POC

Implements proposal 1 from [PR #1](https://github.com/herohua/langchain-azure/pull/1). The checkpoint saver and conversation-chain store validate the descriptor returned by each store acquisition, avoiding the immediate second GET. Every later operation still resolves fresh metadata. Isolation/TTL checks, mismatch policies, item persistence, and client construction/closure are retained.

The companion [core branch](https://github.com/herohua/azure-sdk-for-python/tree/perf/descriptor-reuse-poc) adds the public, read-only `resolved_descriptor` property. It captures the fetch, create, conflict-refetch, or local-backend result. Explicit `get()` still fetches current metadata. This is an acquisition snapshot, not protection against concurrent store deletion/recreation. No SDK PR is opened.

Published core versions without this accessor use the original GET path. The hosting dependency floor is unchanged until the accessor is released; installing only the hosting patch does not enable the optimization with core 2.2.0.

## Run offline

From this directory (PowerShell):

```powershell
python -m uv venv --python 3.12
python -m uv pip install --python .venv/Scripts/python.exe -r requirements.txt
# Baseline behavior with published core:
.venv/Scripts/python.exe benchmark.py --output results/my-published-baseline.json
# Install the companion checkout to enable descriptor reuse:
python -m uv pip install --python .venv/Scripts/python.exe --no-deps -e ../../../../azure-sdk-for-python-descriptor-reuse/sdk/agentserver/azure-ai-agentserver-core
.venv/Scripts/python.exe benchmark.py --output results/my-candidate.json
.venv/Scripts/python.exe -m unittest discover -s . -p test_benchmark.py -v
```

The checkout path assumes sibling worktrees; adjust it to the companion repository. Linux/macOS use `.venv/bin/python`. The synthetic transport rejects external endpoints. Real Responses hosting, LangGraph, checkpoint saving, and storage HTTP pipelines remain in use. Task/response storage stays in temporary local files; no credentials, model, or Azure service is needed.

The diagnostic and four fixture tests reuse [the PR #1 harness at 64d010c](https://github.com/herohua/langchain-azure/tree/64d010c3691b8be517d002c8b7c493f39f94a481/samples/hosting/checkpoint-perf), with an explicit `check=False` for subprocess lint compliance. This keeps the POC runnable directly from main without merging the other proposals. No client reuse, parallel reads, checkpoint reuse, packing, or snapshots are implemented.

## Validation

See [retained ABBA results](results/descriptor-reuse/RESULTS.md). Compare base/candidate/candidate/base in fresh processes with identical dependencies and corresponding turns; inspect raw source hashes, storage events, interval unions, transport counts, and state checks. Output files are never overwritten. Install source packages with `--no-deps`; do not sync a lockfile over the candidate.

The fixture checks restored report files, TODOs, message count, pending-write content/order, pagination, and transport cleanup. It does not model authentication, real isolation, TTL expiry, throttling, conditional writes, network pooling, or service failures. Timing uses configurable asynchronous synthetic delay and is not production latency evidence.
