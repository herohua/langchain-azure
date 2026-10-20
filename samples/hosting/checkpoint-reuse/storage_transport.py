"""Offline storage fixture behind the real Azure HTTP pipeline."""

import asyncio
import base64
import json
from time import perf_counter, time
from urllib.parse import parse_qs, urlsplit

from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import AsyncHttpResponse, AsyncHttpTransport


class OfflineCredential:
    async def get_token(self, *scopes, **kwargs):
        return AccessToken("offline-fixture-token", int(time()) + 3600)


class FixtureResponse(AsyncHttpResponse):
    def __init__(self, request, status, payload):
        super().__init__(request, None)
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.reason = str(status)
        self.content_type = "application/json"
        self._body = json.dumps(payload).encode()

    def body(self):
        return self._body


class StorageFixture:
    def __init__(self, delay_ms):
        self.delay_ms = delay_ms
        self.stores = {}
        self.events = []
        self.constructed = 0
        self.closed = 0
        self.active = 0
        self.max_active = 0
        self.sequence = 0

    @staticmethod
    def decode(segment):
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)).decode()

    def dispatch(self, request):
        url = urlsplit(request.url)
        if url.scheme != "https" or url.netloc != "storage.test.invalid":
            raise RuntimeError("The offline fixture rejects external endpoints")
        parts = url.path.strip("/").split("/")
        if len(parts) < 3 or parts[:3] != ["api", "storage", "state_stores"]:
            raise RuntimeError(f"Unsupported fixture route: {url.path}")
        method = request.method
        payload = json.loads(request.content) if request.content else {}
        if len(parts) == 3 and method == "POST":
            name = payload["name"]
        elif len(parts) >= 4:
            name = self.decode(parts[3])
        else:
            raise RuntimeError(f"Unsupported fixture route: {url.path}")
        user = request.headers.get("x-ms-user-id", "fixture-user")
        store = self.stores.get(name)
        if len(parts) <= 4:
            operation = "descriptor_get" if method == "GET" else "store_create"
            if method == "POST":
                if store is not None:
                    return operation, 409, {"error": {"message": "Store exists"}}
                store = self.stores[name] = {
                    "properties": {
                        "id": name,
                        "object": "state_store",
                        "created_at": 1,
                        "updated_at": 1,
                        **payload,
                    },
                    "items": {},
                }
            elif method != "GET":
                raise RuntimeError(f"Unsupported fixture method: {method}")
            return (
                operation,
                200 if store else 404,
                (
                    store["properties"]
                    if store
                    else {"error": {"message": "Missing store"}}
                ),
            )
        if store is None:
            return "missing_store", 404, {"error": {"message": "Missing store"}}
        # shortcut: fixed caller partitioning, validate isolation on a real service.
        partition = user if store["properties"]["user_isolation"] else "shared"
        items = store["items"].setdefault(partition, {})
        if parts[4:] == ["items:keys"] and method == "GET":
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            records = [
                item
                for item in items.values()
                if all(
                    (item.get("tags") or {}).get(k[5:]) == v
                    for k, v in query.items()
                    if k.startswith("tags.")
                )
            ]
            records.sort(
                key=lambda item: item["created_at"],
                reverse=query.get("order") == "desc",
            )
            if "after" in query:
                position = next(
                    i for i, item in enumerate(records) if item["id"] == query["after"]
                )
                records = records[position + 1 :]
            page = records[: int(query.get("limit", 20))]
            keys = [{k: v for k, v in item.items() if k != "value"} for item in page]
            return (
                "key_list",
                200,
                {
                    "object": "list",
                    "data": keys,
                    "has_more": len(records) > len(page),
                    "first_id": keys[0]["id"] if keys else None,
                    "last_id": keys[-1]["id"] if keys else None,
                },
            )
        if parts[4:] == ["items"] and method == "POST":
            key = payload["key"]
        elif len(parts) == 6 and parts[4] == "items":
            key = self.decode(parts[5])
        else:
            raise RuntimeError(f"Unsupported fixture route: {url.path}")
        current = items.get(key)
        if method == "GET":
            return (
                "item_get",
                200 if current else 404,
                (current or {"error": {"message": "Missing item"}}),
            )
        if method == "DELETE":
            items.pop(key, None)
            return "item_delete", 204, {}
        if method not in {"POST", "PUT"}:
            raise RuntimeError(f"Unsupported fixture method: {method}")
        if method == "POST" and current is not None:
            return "item_write", 409, {"error": {"message": "Item exists"}}
        if "If-Match" in request.headers:
            raise RuntimeError("Conditional writes are outside this fixture")
        self.sequence += 1
        item = items[key] = {
            "id": current["id"] if current else str(self.sequence),
            "object": "state_store.item",
            "key": key,
            "value": payload["value"],
            "tags": payload.get("tags"),
            "etag": str(self.sequence),
            "created_at": current["created_at"] if current else self.sequence,
            "updated_at": self.sequence,
        }
        return "item_write", 200, {k: v for k, v in item.items() if k != "value"}


class StorageTransport(AsyncHttpTransport):
    def __init__(self, fixture):
        self.fixture = fixture
        self.is_closed = False
        fixture.constructed += 1

    async def open(self):
        if self.is_closed:
            raise RuntimeError("Transport used after close")

    async def close(self):
        if not self.is_closed:
            self.is_closed = True
            self.fixture.closed += 1

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def send(self, request, **kwargs):
        await self.open()
        fixture = self.fixture
        start = perf_counter()
        fixture.active += 1
        fixture.max_active = max(fixture.max_active, fixture.active)
        try:
            await asyncio.sleep(fixture.delay_ms / 1000)
            operation, status, payload = fixture.dispatch(request)
            fixture.events.append(
                {
                    "operation": operation,
                    "status": status,
                    "start": start,
                    "end": perf_counter(),
                    "call_id": request.headers.get("x-agent-foundry-call-id"),
                    "method": request.method,
                    "path": urlsplit(request.url).path,
                }
            )
            return FixtureResponse(request, status, payload)
        finally:
            fixture.active -= 1
