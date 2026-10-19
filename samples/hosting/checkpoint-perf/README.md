# Local Checkpoint Performance Example

A deterministic graph runs through the public `ResponsesHostServer`, `FoundryCheckpointSaver`, conversation-chain store, and real Azure storage HTTP pipeline. No model, search, Azure credentials, deployment, or service calls are needed.

The storage transport is an offline fixture with configurable asynchronous delay. Normal local file storage bypasses the HTTP pipeline, so it cannot expose repeated descriptor GETs or transport construction. This fixture selects the storage HTTP path while keeping the host's response/task persistence local. SDK checkpoint and hosting code are unchanged.

## Run

From this sample directory:

```powershell
python -m uv venv --python 3.12
python -m uv pip install --python .venv/Scripts/python.exe -r requirements.txt
.\.venv\Scripts\python.exe benchmark.py --output results/my-baseline.json
```

Defaults: three fresh Python processes, four turns in one conversation, one fresh-conversation control, 24 pending writes, and a requested 5 ms delay per storage operation. Each run gets empty temporary storage. Existing output files are never overwritten.

```powershell
# Exercise multiple pending-write pages and remove the injected delay.
.\.venv\Scripts\python.exe benchmark.py --repeats 1 --pending-writes 105 --delay-ms 0 --output results/my-pagination.json
.\.venv\Scripts\python.exe -m unittest discover -s . -p test_benchmark.py -v
```

## Workloads and recorded data

- **Conversation:** append a deterministic line to `/final_report.md`, update TODOs, and return `READY <turn>`. Completion status and turn count are checked. An inspection outside timed requests checks actual file contents, TODOs, and message count after graph/host reconstruction.
- **Pending writes:** seed a separate checkpoint with the requested number of task outputs, then time `aget_tuple`. Verify every returned value and its ordering. Setup writes are excluded from the measured read.
- **Timing:** imports, graph/host construction, and host lifespan startup; complete in-process request duration; storage interval union; descriptor/item/list/write counts; transport constructions and maximum concurrent storage calls. Raw storage events, package versions, and hashes of the four relevant SDK source files are retained in JSON.

All constructed transports must be closed. There is no LLM/search duration to subtract. Storage interval unions must not be subtracted again from the total when reporting SDK overhead.

## Graph complexity and storage calls

More execution steps, tool loops, or checkpointed subgraphs can produce more checkpoint saves and pending-write operations. With similar work per operation, storage HTTP calls may grow roughly in proportion to checkpointer activity: each saver operation resolves/validates its store and reads or writes items. The relevant count is executed persistence operations, not the number of nodes defined in the graph.

The ratio is not fixed. Pending-write volume, checkpoint restoration, pagination, retries, and Responses bookkeeping also affect HTTP counts. This single-node sample measures a small workload; its counts do not predict a more complex graph's traffic, and it does not establish a scaling curve. Compare checkpoint save/read and pending-write operation counts alongside actual HTTP attempts when testing a larger graph.

## Compare an SDK change

The requirements install this checkout's `libs/azure-ai` package in editable mode and pin the storage/Responses/LangGraph versions used by the local baseline. On Linux/macOS, replace `.venv/Scripts/python.exe` with `.venv/bin/python` in the commands. See [results](results/RESULTS.md) for the local baseline.

Run the same script in separate base/candidate environments with identical dependencies except the SDK change. Use the Python executable from each environment; the script imports whichever SDK that interpreter resolves. Install another source checkout with `python -m uv pip install --python <python-executable> --no-deps --editable <checkout>/libs/azure-ai`. AgentServer core changes require installing that package into the same environment too.

Use a small base/candidate/candidate/base sequence with separate output files and identical `--turns`, `--pending-writes`, and `--delay-ms`. Do not run `uv sync` after installing a candidate: it can restore the locked baseline. Record the source commits alongside the JSON results; package versions alone do not identify editable source changes. Compare corresponding turns and the pending-write probe.

Expected signals: fewer descriptor GETs with unchanged client construction for [descriptor reuse](specs/STATE_STORE_DESCRIPTOR_REUSE.md); fewer transport constructions with unchanged metadata refresh policy for [client/transport reuse](specs/STATE_STORE_CLIENT_REUSE.md); higher bounded read concurrency and lower read duration for [checkpoint reads](specs/CHECKPOINT_READS.md). HTTP call count need not fall with parallelism.

## Hosted evidence and separate reliability follow-up

The [2026-10-16 repeat report](https://github.com/herohua/langgraph-foundry-migration-study/blob/4c9ba0aa5f79e15547276cc8f6138fb3cf3b9cfc/results/foundry-hosted-benchmark/2026-10-16/RESULTS.md), merged in [migration-study PR #22](https://github.com/herohua/langgraph-foundry-migration-study/pull/22), is the primary hosted reference. It replaces the smaller [2026-10-14 run](https://github.com/herohua/langgraph-foundry-migration-study/pull/13) for this discussion. Real LLM/tools were bypassed; graph, checkpoint, Responses/history and task APIs remained real. The repeat reused the same deployed SDK source (`4b5bb9b`), images and attempt budgets as 2026-10-15; no optimization, rebuild or redeployment occurred. Its workload and source differ from this offline baseline, so counts/timings are not directly comparable.

**Lower observed failure rate:** S1/S2 sent 2,989 requests, with 2,976 successes and 13 failures (0.43%), versus 35/2,976 failures (1.18%) on 2026-10-15. One credential error prevented a send and is separate. Success requires one completed READY terminal event and stream EOF; failures remain in the sent-request denominator and are excluded only from latency quantiles.

**The I/O hotspot repeated:** in successful S1/S2 traces with exactly one handler, logical storage/task I/O covered a median 96.1-99.1% of handler time. These are interval unions clipped to the handler; HTTP child spans are not added to their parents.

| Group | Server resilience / request background | S1 logical calls P50 | S2 logical calls P50 |
| --- | --- | ---: | ---: |
| A | off / false | 42 | 102 |
| B | on / true | 52 | 112 |
| C | on / false | 46 | 106 |

**History cost remains without the earlier timeout-heavy sample:** foreground request medians below compare A/C with request background=false. S3 uses warm compute and empty history; S4 continues ten completed turns; S5 restores that ten-turn chain in a new session on resumed compute. Counts are successful / measured requests sent.

| Scenario | Resilience off P50 (success/sent) | Resilience on P50 (success/sent) |
| --- | ---: | ---: |
| S3: warm, empty history | 3.19 s (49/49) | 3.91 s (50/50) |
| S4: warm, ten-turn history | 57.29 s (50/50) | 52.05 s (49/50) |
| S5: cross-session ten-turn restore | 84.36 s (20/20) | 64.36 s (38/40) |

The [per-request S5 comparison](https://github.com/herohua/langgraph-foundry-migration-study/blob/4c9ba0aa5f79e15547276cc8f6138fb3cf3b9cfc/results/foundry-hosted-benchmark/2026-10-16/data/telemetry/s5-off-comparison.json) found storage HTTP408 attempts in 8/16 successful off-mode requests on 2026-10-15, versus 0/20 on 2026-10-16. The earlier timeout-free subset had P50 72.35 s, versus 84.36 s in the repeat. Thus the overall S5 off-mode P50 drop from 156.45 to 84.36 s does not establish a performance gain. S5 reached measurement in only 84/150 attempts across all groups: 13 preparation failures and 53 admission-pause skips remained outside the measured table. These small, selected samples cannot rank resilience reliability or prove a backend fix.

The repeat provides evidence with less observed timeout interference for investigating repeated metadata/checkpoint reads, response-chain resolution and pending-write reconstruction. It does not isolate their individual costs or measure client/transport reuse savings. Storage/task timeouts and client outcome accounting still need separate AgentServer core/Responses, platform and benchmark-client follow-up.

This offline transport, with Responses/task persistence kept local, cannot reproduce or establish a fix for those service failures. Keep reliability work separate from the three performance specs; preserve durability and write idempotency rather than adding blind POST/PATCH retries.

## Limits

Fresh-process startup excludes interpreter launch and Azure infrastructure. The in-process client adds no network/TLS cost. Requested delays depend on OS timer resolution and are not measured service round-trip times.

The fixture implements only the routes needed here. It does not validate authentication, TTL expiry, real user isolation, throttling, conditional writes, or active-turn recovery. Storage survives host reconstruction within the worker, not worker-process termination. Production transport pooling gains require a real transport and service test.
