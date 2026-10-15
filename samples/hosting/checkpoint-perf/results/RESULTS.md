# Local Checkpoint Performance Baseline

Tested 2026-10-14 from source commit `cb9db867b4e4f7dab74995254efe3332a6b6e2f5` using the [sample requirements and commands](../README.md). Three serial fresh processes; requested storage delay 5 ms; four conversation turns; one fresh-conversation control; 24 pending writes. AgentServer core `2.2.0`, Responses `2.3.0`, LangGraph `1.2.5`. No model, search, or service calls.

| Workload | Median elapsed | Descriptor GETs | Item GETs | Key lists | New transports |
| --- | ---: | ---: | ---: | ---: | ---: |
| First turn | 299.2 ms | 18 | 1 | 2 | 9 |
| Continuation, turn 2 | 306.7 ms | 18 | 3 | 2 | 9 |
| Continuation, turn 3 | 298.2 ms | 18 | 3 | 2 | 9 |
| Continuation, turn 4 | 292.7 ms | 18 | 3 | 2 | 9 |
| Fresh conversation | 271.3 ms | 18 | 1 | 2 | 9 |
| Read 24 pending writes | 436.4 ms | 2 | 25 | 1 | 1 |

Fresh-process startup median: 2241.9 ms, excluding interpreter launch. Conversation requests performed 10 item writes each. Pending-write reads reached storage concurrency 1; conversation requests reached 2 because graph writes can overlap. Counts were consistent across all three runs.

Completion, persisted file/TODO/message state after host reconstruction, pending-write content/order, and transport cleanup passed. Four harness tests, Ruff lint/format, dependency compatibility, and Markdown links passed. A separate 105-write zero-delay probe passed with two key-list pages and 106 item GETs.

These synthetic timings identify repeated descriptor reads/client construction and sequential reads; they do not estimate service latency or establish an optimization benefit. No candidate SDK was tested.

Separate [hosted evidence and reliability findings](../README.md#hosted-evidence-and-separate-reliability-follow-up) reinforce the repeated-read targets. They do not change this offline baseline or demonstrate that any proposed optimization fixes cloud timeouts.

More complex execution can generate more checkpoint/pending-write operations and therefore more storage HTTP calls; the relationship depends on the operation mix, not node count alone. See [graph complexity and storage calls](../README.md#graph-complexity-and-storage-calls). No scaling experiment was performed.

Evidence: [source observations/events](source-baseline.json). Relevant SDK source hashes and package versions are recorded; no machine paths or credentials are included.
