"""The simulator has no get-by-id route; the client must not mistake that for an error.

`sim` exposes `GET /faults` and `DELETE /faults/{id}`. Requesting `GET /faults/{id}`
therefore answers 405, not 404. The client has to fall back to the authoritative
listing on either status, otherwise a running simulator scenario becomes
unreadable and the agent records a verification failure that never happened.
"""

from __future__ import annotations

import httpx
import pytest

from simulator_client import SimulatorClient, SimulatorClientError

RULE = {
    "id": "fault-regression1",
    "fault_type": "latency",
    "resource_type": "volume",
    "operation": "create",
    "probability": 1.0,
    "params": {},
    "created_at": "2026-11-22T00:00:00Z",
    "expires_at": None,
    "hits": 0,
}


def _client(handler) -> SimulatorClient:
    return SimulatorClient(base_url="http://sim.test", transport=httpx.MockTransport(handler))


def _no_direct_route(request) -> httpx.Response:
    if request.url.path == "/faults":
        return httpx.Response(200, json={"total": 1, "rules": [RULE]})
    if request.url.path.startswith("/faults/"):
        return httpx.Response(405, json={"detail": "Method Not Allowed"})
    return httpx.Response(404, json={"detail": "not found"})


async def test_method_not_allowed_falls_back_to_the_listing():
    client = _client(_no_direct_route)

    rule = await client.get_fault(RULE["id"])

    assert rule == RULE


async def test_missing_rule_in_the_listing_reads_as_none():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/faults":
            return httpx.Response(200, json={"total": 0, "rules": []})
        return httpx.Response(405, json={"detail": "Method Not Allowed"})

    client = _client(handler)

    assert await client.get_fault("fault-gone") is None


async def test_direct_route_still_wins_when_the_simulator_offers_it():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/faults/{RULE['id']}":
            return httpx.Response(200, json=RULE)
        raise AssertionError(f"unexpected request {request.url}")

    client = _client(handler)

    assert await client.get_fault(RULE["id"]) == RULE


async def test_a_broken_listing_is_still_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/faults":
            return httpx.Response(503, text="simulator down")
        return httpx.Response(405, json={"detail": "Method Not Allowed"})

    client = _client(handler)

    with pytest.raises(SimulatorClientError, match="simulator rejected fault listing"):
        await client.get_fault(RULE["id"])
