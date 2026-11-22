"""Deterministic doubles for the chaos engine HTTP client.

The router resolves ``chaos`` from its own module globals on every request, so
replacing that attribute with one of these doubles redirects the API without a
network call. ``chaos_client`` is imported lazily inside ``ExplodingChaosClient``
so that collecting the pre-existing tests never depends on modules authored for
this milestone.
"""

from typing import Any

DEFAULT_TARGET: dict[str, Any] = {"namespace": "default", "label_selector": {"app": "web"}}


def scenario(
    *,
    scenario_id: str = "scenario-1",
    domain: str = "chaos_mesh",
    fault_type: str = "pod_kill",
    target: dict[str, Any] | None = None,
    status: str = "completed",
    started: bool = True,
    name: str | None = None,
) -> dict[str, Any]:
    """Return one recorded scenario in the shape the chaos engine reports."""
    return {
        "id": scenario_id,
        "name": name if name is not None else f"scenario {scenario_id}",
        "domain": domain,
        "fault_type": fault_type,
        "target": dict(target) if target is not None else dict(DEFAULT_TARGET),
        "status": status,
        "started_at": "2026-01-01T00:00:00Z" if started else None,
    }


class FakeChaosClient:
    """In-memory chaos engine: replays its scenarios and counts the calls."""

    def __init__(self, scenarios: list[dict[str, Any]] | None = None) -> None:
        self.scenarios: list[dict[str, Any]] = list(scenarios or [])
        self.list_calls = 0
        self.error: Exception | None = None

    async def list_scenarios(self) -> list[dict[str, Any]]:
        self.list_calls += 1
        if self.error is not None:
            raise self.error
        return list(self.scenarios)


class ExplodingChaosClient:
    """Chaos engine that is down: every listing raises the client's own error."""

    def __init__(self, message: str = "chaos unreachable") -> None:
        self.message = message
        self.list_calls = 0

    async def list_scenarios(self) -> list[dict[str, Any]]:
        from chaos_client import ChaosClientError

        self.list_calls += 1
        raise ChaosClientError(self.message)
