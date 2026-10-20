# Local Checkpoint Performance Baseline

See [Analysis.md](../Analysis.md) for the data-flow diagram, per-stage request counts and source-code walkthrough.

Refreshed 2026-10-20 against upstream [langchain-azure HEAD `d6befba`](https://github.com/langchain-ai/langchain-azure/commit/d6befba95a86f55328b2e95eb61151a2f8889485), package `1.2.11`. Latest stable versions checked on PyPI: [LangGraph `1.2.16`](https://pypi.org/project/langgraph/1.2.16/), [AgentServer core `2.2.0`](https://pypi.org/project/azure-ai-agentserver-core/2.2.0/) and [Responses `2.3.0`](https://pypi.org/project/azure-ai-agentserver-responses/2.3.0/). Core/Responses were already current.

Python 3.12.13 on Windows; three serial fresh processes; four conversation turns; one fresh-conversation control; 24 pending writes; requested asynchronous delay 5 ms per storage call. Uses the [sample commands and requirements](../README.md). No model, search or service calls. An isolated environment retained the previous environment's other package versions; LangGraph and the editable hosting package were updated. The four recorded saver/chain-store/core-storage source hashes are unchanged from the original baseline.

| Workload | Median elapsed | Descriptor GETs | Item GETs | Key lists | New transports |
| --- | ---: | ---: | ---: | ---: | ---: |
| First turn | 309.1 ms | 18 | 1 | 2 | 9 |
| Continuation, turn 2 | 389.4 ms | 20 | 4 | 4 | 10 |
| Continuation, turn 3 | 387.7 ms | 20 | 4 | 4 | 10 |
| Continuation, turn 4 | 390.0 ms | 20 | 4 | 4 | 10 |
| Fresh conversation | 279.8 ms | 18 | 1 | 2 | 9 |
| Read 24 pending writes | 439.0 ms | 2 | 25 | 1 | 1 |

Fresh-process startup median: 2710.6 ms, excluding interpreter launch. Conversation requests still write 10 items each. Counts match across all three processes. Pending-write reads reach concurrency 1; conversation requests reach 2 because graph writes can overlap. Each process constructs and closes 75 transports, including setup and verification outside timed requests.

## What changed

All three original optimization targets remain: duplicate descriptor GETs, repeated client construction and serial pending-write reads persist. The [original 2026-10-14 baseline](https://github.com/herohua/langchain-azure/blob/8185bbb05c7ab8cccef897117dda6ac89ad3ed84/samples/hosting/checkpoint-perf/results/source-baseline.json) recorded 18 descriptor GETs, three item GETs, two key lists and nine transports per continuation. The refreshed continuation adds two descriptor GETs, one item GET, two key lists and one transport.

LangGraph 1.2.16's [execution startup](https://github.com/langchain-ai/langgraph/blob/41c9c22fdc83d68a2859d0c9a2b627c8615a792f/libs/langgraph/langgraph/pregel/_loop.py#L2061-L2073) checks whether an explicitly addressed checkpoint has been superseded. That [check loads the latest checkpoint](https://github.com/langchain-ai/langgraph/blob/41c9c22fdc83d68a2859d0c9a2b627c8615a792f/libs/langgraph/langgraph/pregel/_checkpoint.py#L191-L200), adding a store acquisition, latest-key lookup and pending-write listing. In this fixture, the latest checkpoint equals the addressed checkpoint: continuation events contain one chain-store reference read and three reads of the same checkpoint item. Do not remove this correctness check or assume an addressed checkpoint is always latest.

Historical continuation medians were 293-307 ms; refreshed medians are 388-390 ms. These are synthetic, different-date observations, not a controlled latency regression estimate. Extra logical calls are reproducible; injected delay and OS scheduling affect elapsed time. No optimization or cloud-timeout fix is established.

## Verification and evidence

Persisted file/TODO/message state after host reconstruction, pending-write content/order and transport cleanup passed. All four fixture tests and dependency compatibility checks passed. The [105-write zero-delay probe](source-pagination.json) passed with two key-list pages, 106 item GETs and read concurrency 1.

[Raw baseline observations/events](source-baseline.json) and [pagination observations/events](source-pagination.json) retain package versions and relevant source hashes. The harness and mock were unchanged for this refresh. The [read/client/descriptor proposals](../README.md#compare-an-sdk-change) use the refreshed counts and pin the tested source versions. The separate [pending-write packing proposal](../specs/PENDING_WRITE_PACKING.md) defines an unmeasured storage-format change; this baseline does not demonstrate its savings. Separate [hosted observations](../README.md#hosted-performance-observations) were not rerun; this mock cannot measure real network pooling or service reliability.
