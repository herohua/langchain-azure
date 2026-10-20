# Local Checkpoint Performance Example

A deterministic graph runs through the public `ResponsesHostServer`, `FoundryCheckpointSaver`, conversation-chain store, and the SDK's storage HTTP pipeline with a mock transport. No model, search, Azure credentials, deployment, or service calls are needed.

The mock transport replaces network/service calls with in-memory storage and configurable asynchronous delay. The harness selects the SDK's HTTP storage path and injects this transport; the host's response/task persistence stays local. SDK checkpoint and hosting source code is unchanged.

We keep the real saver methods, client construction and HTTP pipeline because those are what this diagnostic measures. Skipping storage calls or mocking the saver methods would hide repeated descriptor GETs, transport construction and serial item reads. Normal local file storage also bypasses the HTTP pipeline. This mock reproduces client-side call patterns, not service latency, network connection pooling or backend failures.

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

Expected signals: fewer descriptor GETs with unchanged client construction for [descriptor reuse](specs/STATE_STORE_DESCRIPTOR_REUSE.md); less client or connection-pool setup with unchanged metadata refresh policy for [client/transport reuse](specs/STATE_STORE_CLIENT_REUSE.md), measured separately for pipelines, transports and sessions; higher bounded read concurrency and lower read duration for [checkpoint reads](specs/CHECKPOINT_READS.md). HTTP call count need not fall with parallelism.

## Hosted performance observations

In separate hosted measurements with real LLM/tools bypassed, logical storage/task I/O covered a median 96.1-99.1% of successful single-handler request time. Median logical call counts increased from 42-52 for empty-history requests to 102-112 for the first continuation.

Warm empty-history requests had median latency around 3-4 seconds, compared with 52-57 seconds after ten completed turns and 64-84 seconds when restoring that history in a new session. History-related overhead persisted in measurements with fewer observed storage timeouts. These observations support investigating repeated metadata/checkpoint reads, response-chain resolution and pending-write reconstruction; they do not isolate each mechanism's cost or demonstrate optimization savings.

These hosted workloads differ from this offline sample. Latency summaries cover successful requests; failures, preparation failures and skipped measurements limit comparisons. The offline transport keeps Responses/task persistence local and cannot reproduce service timeouts or measure production connection-pooling gains. Keep reliability follow-up separate from the three performance specs, preserving durability and write idempotency.

## Limits

Fresh-process startup excludes interpreter launch and Azure infrastructure. The in-process client adds no network/TLS cost. Requested delays depend on OS timer resolution and are not measured service round-trip times.

The fixture implements only the routes needed here. It does not validate authentication, TTL expiry, real user isolation, throttling, conditional writes, or active-turn recovery. Storage survives host reconstruction within the worker, not worker-process termination. Production transport pooling gains require a real transport and service test.
