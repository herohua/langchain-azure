"""Small checks for the timing calculation and offline protocol fixture."""

import asyncio
import base64
import json
import unittest

from azure.core.rest import HttpRequest
from benchmark import interval_union_ms
from storage_transport import StorageFixture, StorageTransport


class BenchmarkTests(unittest.TestCase):
    def test_interval_union_does_not_double_count_overlap(self):
        self.assertEqual(
            interval_union_ms(
                [
                    {"start": 2, "end": 4},
                    {"start": 1, "end": 3},
                    {"start": 5, "end": 6},
                ]
            ),
            4000,
        )
        self.assertEqual(interval_union_ms([]), 0)

    def test_fixture_filters_and_paginates_in_creation_order(self):
        fixture = StorageFixture(0)
        base = "https://storage.test.invalid/api/storage/state_stores"
        encoded = base64.urlsafe_b64encode(b"probe").decode().rstrip("=")
        store = base + "/" + encoded
        fixture.dispatch(
            HttpRequest(
                "POST",
                base,
                content=json.dumps(
                    {
                        "name": "probe",
                        "user_isolation": True,
                        "item_ttl_seconds": 30,
                    }
                ),
            )
        )
        for n in range(3):
            fixture.dispatch(
                HttpRequest(
                    "POST",
                    store + "/items",
                    content=json.dumps(
                        {
                            "key": str(n),
                            "value": {"n": n},
                            "tags": {"kind": "write"},
                        }
                    ),
                )
            )
        operation, status, page = fixture.dispatch(
            HttpRequest(
                "GET",
                store + "/items:keys?limit=2&order=asc&tags.kind=write",
            )
        )
        self.assertEqual((operation, status), ("key_list", 200))
        self.assertEqual([item["key"] for item in page["data"]], ["0", "1"])
        self.assertTrue(page["has_more"])
        _, _, last = fixture.dispatch(
            HttpRequest(
                "GET",
                store + f"/items:keys?limit=2&order=asc&after={page['last_id']}",
            )
        )
        self.assertEqual([item["key"] for item in last["data"]], ["2"])
        self.assertFalse(last["has_more"])
        _, _, empty = fixture.dispatch(
            HttpRequest(
                "GET",
                store + "/items:keys?tags.kind=checkpoint",
            )
        )
        self.assertEqual(empty["data"], [])

    def test_fixture_rejects_external_urls(self):
        with self.assertRaisesRegex(RuntimeError, "rejects external"):
            StorageFixture(0).dispatch(HttpRequest("GET", "https://example.com"))

    def test_transport_close_is_idempotent_and_rejects_reuse(self):
        async def check():
            fixture = StorageFixture(0)
            transport = StorageTransport(fixture)
            await transport.close()
            await transport.close()
            self.assertEqual(fixture.closed, 1)
            with self.assertRaisesRegex(RuntimeError, "after close"):
                await transport.open()

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
