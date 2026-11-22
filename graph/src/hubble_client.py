"""Flow source for the topology builder.

Hubble Relay speaks gRPC; this deployment also exposes a JSON flow gateway that
returns already-flattened flows. The client reads that gateway, asks for at most
`max_flows` entries, and raises on any transport or shape error so the builder
reports Hubble as a blind source rather than guessing edges.
"""

from __future__ import annotations

from typing import Any

import httpx

from config import config

_FLOW_KEYS = ("source_ns", "source_workload", "dest_ns", "dest_workload")


class HubbleReadError(RuntimeError):
    """The flow gateway could not answer a read."""


def _flow_row(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    if not all(isinstance(row.get(key), str) for key in _FLOW_KEYS):
        return None
    return {key: row[key] for key in _FLOW_KEYS}


class HubbleClient:
    """Read observed flows from the JSON flow gateway."""

    def __init__(self, flows_url: str | None = None, timeout: float | None = None) -> None:
        self.flows_url = flows_url or config.HUBBLE_FLOWS_URL
        self.timeout = timeout or config.SOURCE_TIMEOUT_SECONDS

    async def get_flows(self, max_flows: int = 1000) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self.flows_url, params={"max_flows": max_flows})
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HubbleReadError(f"GET {self.flows_url} failed: {exc}") from exc

        rows = payload.get("flows") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise HubbleReadError(f"GET {self.flows_url} returned no flow list")
        flows = [_flow_row(row) for row in rows]
        return [flow for flow in flows if flow is not None]
