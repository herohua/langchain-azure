# Checkpoint reuse results - 2026-10-20

POC for [3.2 Checkpoint reuse](https://github.com/herohua/langchain-azure/pull/1). Production SDK base: `d6befba95a86f55328b2e95eb61151a2f8889485`, identical in both variants. Candidate selects the sample's saver/host subclasses with `--reuse`.

Four fresh processes ran in ABBA order, one four-turn conversation plus a fresh-conversation control per process. All used the same Python 3.12.13 environment, AgentServer core 2.2.0, Responses 2.3.0, LangGraph 1.2.16, a requested 5 ms async delay and 24 pending writes. Raw evidence is in [data/](data/); source hashes identify the implementation. [dependencies.txt](data/dependencies.txt) records the complete environment.

## Call counts

| Case | Base | Reuse |
| --- | ---: | ---: |
| Continuation: total HTTP calls | 38 | 30 |
| Continuation: checkpoint item GETs | 3 | 1 |
| Continuation: chain-reference item GETs | 1 | 1 |
| Continuation: descriptor GETs | 20 | 16 |
| Continuation: key lists | 4 | 2 |
| Continuation: item writes | 10 | 10 |
| Continuation: transport constructions | 10 | 8 |
| First/fresh request: total HTTP calls | 33 | 33 |
| Single 24-write load: total HTTP calls | 28 | 28 |
| Three 24-write loads: total HTTP calls | 85 | 29 |
| Three 24-write loads: item GETs | 75 | 25 |

Continuation traffic fell 21.1%. Reuse eliminates two checkpoint reconstructions; the initial latest lookup preserves freshness. Descriptor/client implementations remain unchanged: fewer saver operations account for their reduced counts. Writes and the separate chain-store reference remain intact.

## Timings

Medians in milliseconds. Continuation pools the six corresponding warm requests per variant; other rows use two observations per variant. Storage time is the union of HTTP intervals, counted once even when writes overlap.

| Case | Base request | Reuse request | Base storage union | Reuse storage union |
| --- | ---: | ---: | ---: | ---: |
| First turn | 305.2 | 312.8 | 239.8 | 243.8 |
| Continuation | 392.4 | 269.6 | 303.9 | 191.4 |
| Fresh conversation | 282.8 | 275.2 | 227.7 | 221.5 |
| Single 24-write load | 434.0 | 435.9 | 414.1 | 416.9 |
| Three 24-write loads | 1322.3 | 452.7 | 1262.4 | 431.2 |

Continuation elapsed time fell 31.3% in this synthetic fixture; the three-load pending probe fell 65.8%. These are small local samples, not hosted speedup estimates. Windows timer granularity makes the actual wait differ from the requested 5 ms. No LLM/tool time or real networking is included.

## Pagination and correctness

A separate base/reuse pair used 105 pending writes and zero injected delay. One unscoped load was unchanged: 106 item GETs and two key-list pages. Three loads required 318 item GETs and seven key lists in base, versus 106 item GETs and three key lists with reuse (one latest selection plus two pending-write pages). Serial pending-read concurrency stayed at one. Host storage concurrency reached two in both variants because persistence can overlap; parallel pending reads were not implemented.

All six runs verified completion, file/TODO/message contents after graph/host reconstruction, pending-write values/order and transport closure. Four ABBA runs closed 78/70/70/78 transports; the pagination pair closed 159/151. No request failed and no 429/5xx/retry attempt occurred. Expected missing-store/reference 404s are retained; the fixture does not simulate throttling or transient failures.

Validation: 16 sample tests, 99 existing saver/Responses/resilience tests, Ruff lint/format, dependency compatibility, retained-evidence assertions and `git diff --check` passed. No cloud deployment, production distributed ownership, process-crash recovery or runtime matrix beyond Python 3.12 was tested.
