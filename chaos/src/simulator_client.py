"""HTTP client for the provisioning simulator's fault registry.

The simulator is the source of truth for simulator-domain faults: this client
registers a rule, asks whether it still exists, and clears it. It never
mirrors rule state locally.
"""

from __future__ import annotations

from typing import Any

import httpx

from config import config


class SimulatorClientError(Exception):
    """The simulator returned something the engine cannot use."""


class SimulatorClient:
    """Small httpx wrapper around `/faults`."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or config.SIMULATOR_URL).rstrip("/")
        self._timeout = timeout
        # Injected only by tests; production uses the default transport.
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout, transport=self._transport
        )

    async def register_fault(self, request: dict[str, Any]) -> dict[str, Any]:
        """POST /faults; the returned rule id becomes the scenario's backend_ref."""
        async with self._client() as client:
            response = await client.post("/faults", json=request)
        if response.status_code != 201:
            raise SimulatorClientError(
                f"simulator rejected fault registration ({response.status_code}): {response.text}"
            )
        return response.json()

    async def get_fault(self, fault_id: str) -> dict[str, Any] | None:
        """Return the rule, or None when it expired or was cleared."""
        async with self._client() as client:
            response = await client.get(f"/faults/{fault_id}")
            if response.status_code in (404, 405):
                # The simulator exposes listing rather than get-by-id: the route
                # answers 405 when the path exists for DELETE only, and 404 when
                # nothing serves it at all. Both mean "ask the listing".
                listed = await client.get("/faults")
                if listed.status_code != 200:
                    raise SimulatorClientError(
                        f"simulator rejected fault listing ({listed.status_code}): {listed.text}"
                    )
                for rule in listed.json().get("rules", []):
                    if rule.get("id") == fault_id:
                        return rule
                return None
            if response.status_code != 200:
                raise SimulatorClientError(
                    f"simulator rejected fault lookup ({response.status_code}): {response.text}"
                )
            return response.json()

    async def clear_fault(self, fault_id: str) -> None:
        """DELETE /faults/{id}; clearing an absent rule is idempotent."""
        async with self._client() as client:
            response = await client.delete(f"/faults/{fault_id}")
        if response.status_code not in (204, 404):
            raise SimulatorClientError(
                f"simulator rejected fault clear ({response.status_code}): {response.text}"
            )
