# State Store client reuse POC

Implements [proposal 2 in PR #1](https://github.com/herohua/langchain-azure/pull/1)
using the existing public AgentServer core APIs. No Azure SDK change is required.

The Responses handler opens a private context before `build_runnable_config` and
keeps it through graph execution and the final conversation-chain write. Each
saver/chain-store instance reuses one client per store and current user within
that handler. Separate consumer instances keep distinct clients, credentials,
endpoints, delegated users, API versions and pipeline options. Clients share one
handler-owned aiohttp session, with cookies disabled. Headers and User-Agent
continue to be evaluated per outbound request. There is no cross-handler cache.

Each reused acquisition calls public `get()` in place of the metadata read in
`get_or_create()`. Existing isolation/TTL validation still runs. A missing store
is reacquired through `get_or_create()`; old clients are retained until all
borrowers finish. Item errors, creation conflicts, retries, write timing,
checkpoint data and pending-write formats retain their original behavior.

Only the handler closes borrowed clients, after active storage operations settle,
including cancellation. The graph generator is explicitly closed before the
handler releases resources. A child cannot open a new operation after scope
shutdown begins. Caller-supplied credentials remain caller-owned. Standalone
operations retain their operation-level client lifetime. Explicit custom
transport, pipeline, session or environment-proxy options retain their existing
ownership and bypass reuse. Thread deletion keeps its original bound-client path.

## Reproduce

This is the offline benchmark from PR #1 commit
`64d010c3691b8be517d002c8b7c493f39f94a481`, extended with separate
pipeline/transport/session construction and closure counters. It runs the real
Responses host, graph, saver and HTTP pipeline against synthetic storage.

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
.venv/Scripts/python.exe benchmark.py --output results/my-run.json
.venv/Scripts/python.exe -m unittest discover -s . -p test_benchmark.py -v
```

Run from this directory. Linux/macOS use `.venv/bin/python`. To compare two
checkouts using the same dependencies, set `PYTHONPATH` to the chosen checkout's
`libs/azure-ai` before each fresh process. Run base/candidate/candidate/base with
`--repeats 1`, four turns, 24 pending writes and 5 ms synthetic storage delay.
Do not run `uv sync` between cases: the sample pins LangGraph 1.2.16, while this
checkout's lock resolves 1.2.15. Results record versions and hosting source hashes.

## Observed results

Retained evidence: [results/abba.json](results/abba.json). Measured locally on
Python 3.12.10 with core 2.2.0, Responses 2.3.0 and LangGraph 1.2.16; base is
`d6befba95a86f55328b2e95eb61151a2f8889485`. Each run uses fresh temporary storage.
The compact report retains every observation, source hash and cleanup total; raw
storage events are replaced by their count and digest.

| Continuation metric | Base | Client reuse |
| --- | ---: | ---: |
| Client/pipeline constructions and closures | 10 | 2 |
| Transport constructions and closures | 10 | 2 |
| Handler-owned sessions created/closed | 0 | 1 |
| Descriptor GETs | 20 | 20 |
| Item GETs / key lists / item writes | 4 / 4 / 10 | 4 / 4 / 10 |
| Total storage requests | 38 | 38 |
| Request median across six continuations | 357.4 ms | 341.5 ms |
| Storage interval-union median | 295.6 ms | 286.6 ms |

First/fresh-conversation construction counts fall from 9 to 2. All created
pipelines, transports and sessions close. Files, TODOs, messages and ordered
pending writes survive host reconstruction. A separate 105-write pagination
probe passes; standalone pending-write reads still construct one operation-owned
client and retain their original request counts.

The offline transport creates no real connections, so the base session count is
zero. Candidate sessions are allocated but unused by that transport. Synthetic
timings do not measure network/TLS/token savings and do not establish production
latency gains. A separate unit test uses real `AioHttpTransport` against a local
HTTP server and verifies one TCP connection across distinct store clients, lazy
headers and safe borrower closure. Remote Foundry pooling, authentication, user
isolation, throttling and latency remain unmeasured.

## Validation

- 119 focused saver, chain-store, scope and Responses host tests pass, including
  concurrent initialization, metadata mismatch, acquisition/refresh/close errors,
  cancellation, identity boundaries and early generator exit.
- Hosting suite: 486 passed, two pre-existing HITL output assertions failed before
  the final seven focused tests were added. Both failures also reproduce on the
  unmodified base with the same dependencies: final output contains only the
  newly completed branch, while tests expect both branches.
- Four fixture tests, ABBA state/count/cleanup assertions, 105-write pagination,
  changed-file Ruff/format, production/new-test mypy and `git diff --check` pass.

No descriptor cache, pending-read parallelism, checkpoint cache, packing, batch
write, Cosmos change, cross-request reuse or cloud deployment is included.
