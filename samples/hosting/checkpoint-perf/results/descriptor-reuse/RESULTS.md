# Descriptor reuse: offline ABBA comparison

Measured on Windows, Python 3.12.14, on 2026-10-20. Four fresh processes in order: [base-1](base-1.json), [candidate-1](candidate-1.json), [candidate-2](candidate-2.json), [base-2](base-2.json). Each runs four conversation turns, one fresh-conversation control, and a separate 24-write probe with 5 ms requested synthetic storage delay. No failed benchmark runs.

Base sources: hosting `d6befba95a86f55328b2e95eb61151a2f8889485`, core `cddd0ee8fe3c0fe2453ee38af71b9c4bf64ae7b5`. Candidate core: [84861688b8](https://github.com/herohua/azure-sdk-for-python/commit/84861688b82551c21c8fae81ea78a673d34529fb); candidate hosting: this PR's two descriptor-validation changes. Raw JSON retains SHA-256 of all four relevant source files. Both sides use the same core source revision plus/minus the descriptor patch, rather than comparing different package releases.

Dependencies are identical across ABBA runs: core reports `2.3.0b2`, Responses `2.3.0`, LangGraph `1.2.16`, hosting `1.2.11`. [Dependency inventory](dependencies.txt) records installed third-party versions; editable source identities are specified above and in raw hashes.

## Call counts

| Workload | Descriptor GETs, base → candidate | All storage calls | Transports per observation |
| --- | --- | --- | --- |
| First / fresh conversation | 18 → 9 | 33 → 24 | 9 → 9 |
| Each continuation (turns 2–4) | 20 → 10 | 38 → 28 | 10 → 10 |
| 24 pending writes, read probe | 2 → 1 | 28 → 27 | 1 → 1 |

Every corresponding observation retains identical item GET, key-list, store-create and item-write counts. Continuations still perform four item GETs, four key lists and ten item writes. Every worker constructs and closes 75 transports, including setup and verification. All workers verify reconstructed report files, TODOs, message count and exact pending-write values/order.

## Timing

Medians of the two observations for each variant, in milliseconds. These are local synthetic measurements, not production speedup estimates. Windows timer resolution can inflate the requested delay. Import/startup and filesystem/cache noise remain; startup is recorded separately and is not evidence of descriptor savings.

| Workload | Whole request, base → candidate | Storage interval union, base → candidate |
| --- | --- | --- |
| First turn | 342.3 → 235.6 | 256.0 → 171.8 |
| Continuation 2 | 402.0 → 303.7 | 304.4 → 215.7 |
| Continuation 3 | 401.2 → 294.6 | 299.7 → 211.8 |
| Continuation 4 | 399.0 → 296.6 | 304.4 → 209.7 |
| Fresh conversation | 288.7 → 207.3 | 232.3 → 153.8 |
| Pending-write probe | 435.8 → 423.0 | 414.7 → 398.0 |

Removing HTTP intervals does not guarantee equal end-to-end savings. No cloud/network pooling measurements were made.

## Other checks

- [105-write pagination probe](candidate-pagination.json), zero injected delay: 1 descriptor GET, 106 item GETs, 2 key lists; exact content/order and closure verified.
- Hosting saver/resilience tests: 64 passed with the candidate core, and 64 with published core 2.2.0 (legacy fallback). SDK state-store/storage tests: 61 passed. Harness fixture tests: 4 passed.
- Full hosting suite: 483 passed, 2 failed. The same two parallel HITL tests fail on unmodified hosting/core with the same dependencies: `TestParallelInterrupts.test_stays_paused_until_every_branch_is_answered` and `test_ignores_a_repeated_answer_to_the_same_interrupt`. They are outside this patch.
- New hosting regression tests against base: 6 fail on the duplicate GET, 4 pass (ignore policy), then all 10 pass with the patch.
- Changed hosting files pass Ruff lint/format and mypy. Mypy disables `import-untyped` because the editable core package is not resolved as typed in this environment.

Python 3.11, 3.13 and 3.14 runtime suites and live Azure behavior were not tested. Public signatures/return types and the hosting dependency floor are unchanged. Optimization requires the companion core accessor; older releases retain the original behavior.
