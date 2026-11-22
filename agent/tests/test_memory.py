"""`memory.py` — the incident maths frozen by CONTRACT D8.

`recall(fault_type)` is the 20 most recent incidents for that fault type,
newest first. `aborted` is a non-success but is not an action failure: it is
excluded from the confidence denominator and from `success_rate`, and MTTR is
averaged over successful runs only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from memory import IncidentMemory

STATUS_BY_OUTCOME = {"success": "done", "failed": "error", "aborted": "aborted"}


def _run(
    fault_type: str = "pod_kill",
    outcome: str = "success",
    mttr: float = 10.0,
    index: int = 0,
    scenario_id: str | None = None,
) -> dict[str, Any]:
    ended = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=index)
    started = ended - timedelta(seconds=mttr)
    identifier = scenario_id or f"sc-{fault_type}-{index:02d}"
    return {
        "scenario_id": identifier,
        "scenario": {"id": identifier, "fault_type": fault_type},
        "fault_type": fault_type,
        "outcome": outcome,
        "status": STATUS_BY_OUTCOME[outcome],
        "mttr_seconds": mttr if outcome == "success" else None,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "updated_at": ended.isoformat(),
    }


async def test_recall_returns_the_twenty_most_recent_newest_first(memory: IncidentMemory) -> None:
    for index in range(25):
        await memory.record(_run(index=index))

    payload = await memory.recall("pod_kill")

    incidents = payload["incidents"]
    assert len(incidents) == 20
    assert incidents[0]["scenario_id"] == "sc-pod_kill-24"
    assert incidents[-1]["scenario_id"] == "sc-pod_kill-05"


async def test_aborted_is_excluded_from_confidence_and_success_rate(memory: IncidentMemory) -> None:
    for mttr in (10.0, 20.0, 30.0, 40.0, 50.0, 60.0):
        await memory.record(_run(outcome="success", mttr=mttr, index=int(mttr)))
    for index in (100, 101):
        await memory.record(_run(outcome="failed", index=index))
    for index in (200, 201, 202):
        await memory.record(_run(outcome="aborted", index=index))

    payload = await memory.recall("pod_kill")

    # 6 successes / (6 successes + 2 failures); the three aborts are absent.
    assert payload["confidence"] == 75
    assert payload["success_rate"] == pytest.approx(0.75) or (
        payload["success_rate"] == pytest.approx(75.0)
    )
    assert payload["mean_mttr_seconds"] == pytest.approx(35.0)


async def test_mean_mttr_is_none_without_a_success(memory: IncidentMemory) -> None:
    await memory.record(_run(outcome="aborted", index=0))
    await memory.record(_run(outcome="aborted", index=1))
    await memory.record(_run(outcome="failed", index=2))

    payload = await memory.recall("pod_kill")

    assert payload["mean_mttr_seconds"] is None
    assert payload["confidence"] == 0


async def test_fault_types_do_not_leak_between_recalls(memory: IncidentMemory) -> None:
    for index in range(3):
        await memory.record(_run(fault_type="pod_kill", index=index))
    for index in range(2):
        await memory.record(_run(fault_type="network_latency", index=index))

    pod = await memory.recall("pod_kill")
    network = await memory.recall("network_latency")

    assert {incident["scenario_id"] for incident in pod["incidents"]} == {
        "sc-pod_kill-00",
        "sc-pod_kill-01",
        "sc-pod_kill-02",
    }
    assert {incident["scenario_id"] for incident in network["incidents"]} == {
        "sc-network_latency-00",
        "sc-network_latency-01",
    }
