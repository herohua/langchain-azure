"""Check retained ABBA/pagination evidence and print measured medians."""

import gzip
import hashlib
import json
import statistics
from pathlib import Path

root = Path(__file__).parent
runs = []
for name in (
    "abba-1-base",
    "abba-2-reuse",
    "abba-3-reuse",
    "abba-4-base",
    "pagination-1-base",
    "pagination-2-reuse",
):
    evidence = json.loads(
        gzip.decompress((root / "results" / "data" / f"{name}.json.gz").read_bytes())
    )
    run = evidence["runs"][0]
    reuse = evidence["configuration"]["reuse"]
    count = evidence["configuration"]["pending_writes"]
    assert run["state_verified"]
    assert run["transports_created"] == run["transports_closed"]
    assert all(e["status"] in (200, 404) for e in run["storage_events"])
    assert (
        run["sdk_source_sha256"]["reuse"]
        == hashlib.sha256((root / "reuse.py").read_bytes()).hexdigest()
    )
    for observation in run["observations"]:
        calls = observation["storage_calls"]
        if observation["case"] == "continuation":
            assert calls == {
                "descriptor_get": 16 if reuse else 20,
                "item_get": 2 if reuse else 4,
                "key_list": 2 if reuse else 4,
                "item_write": 10,
            }
        if observation["case"] == "pending-reuse":
            assert observation["max_storage_concurrency"] == 1
            assert calls["item_get"] == (count + 1) * (1 if reuse else 3)
            assert calls["key_list"] == (
                (3 if count == 105 else 2) if reuse else (7 if count == 105 else 4)
            )
    runs.append((evidence, run))

assert all(run["versions"] == runs[0][1]["versions"] for _, run in runs)
assert all(
    run["sdk_source_sha256"] == runs[0][1]["sdk_source_sha256"] for _, run in runs
)
for case in (
    "first",
    "continuation",
    "fresh-conversation",
    "pending-writes",
    "pending-reuse",
):
    for reuse in (False, True):
        observations = [
            o
            for evidence, run in runs
            if evidence["configuration"]["pending_writes"] == 24
            and evidence["configuration"]["reuse"] == reuse
            for o in run["observations"]
            if o["case"] == case
        ]
        print(
            case,
            "reuse" if reuse else "base",
            f"request={statistics.median(o['request_ms'] for o in observations):.1f}ms",
            f"storage={statistics.median(o['storage_union_ms'] for o in observations):.1f}ms",
        )
print("Verified six runs, source hashes, call counts, state and transport closure.")
