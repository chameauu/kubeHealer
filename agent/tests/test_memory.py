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
_UNSET = object()


def _run(
    fault_type: str = "pod_kill",
    outcome: str = "success",
    mttr: float = 10.0,
    index: int = 0,
    scenario_id: str | None = None,
    action: str | None = None,
    stored_mttr: Any = _UNSET,
) -> dict[str, Any]:
    ended = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=index)
    started = ended - timedelta(seconds=mttr)
    identifier = scenario_id or f"sc-{fault_type}-{index:02d}"
    recorded_mttr = (
        (mttr if outcome == "success" else None)
        if stored_mttr is _UNSET
        else stored_mttr
    )
    run = {
        "scenario_id": identifier,
        "scenario": {"id": identifier, "fault_type": fault_type},
        "fault_type": fault_type,
        "outcome": outcome,
        "status": STATUS_BY_OUTCOME[outcome],
        "mttr_seconds": recorded_mttr,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "updated_at": ended.isoformat(),
    }
    if action is not None:
        run["action"] = action
    return run


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


async def test_by_action_keeps_two_actions_apart(memory: IncidentMemory) -> None:
    await memory.record(_run(action="restart_deployment", mttr=10.0, index=0))
    await memory.record(_run(action="restart_deployment", mttr=30.0, index=1))
    await memory.record(_run(action="restart_deployment", outcome="failed", index=2))
    await memory.record(_run(action="scale_deployment", mttr=50.0, index=3))
    await memory.record(_run(action="scale_deployment", outcome="failed", index=4))
    await memory.record(_run(action="scale_deployment", outcome="failed", index=5))

    payload = await memory.recall("pod_kill")

    assert payload["by_action"] == {
        "restart_deployment": {
            "attempts": 3,
            "successes": 2,
            "mean_mttr_seconds": pytest.approx(20.0),
        },
        "scale_deployment": {
            "attempts": 3,
            "successes": 1,
            "mean_mttr_seconds": pytest.approx(50.0),
        },
    }
    # The whole-incident aggregate is untouched by the per-action split.
    assert payload["confidence"] == 50
    assert payload["mean_mttr_seconds"] == pytest.approx(30.0)


async def test_by_action_mean_is_none_when_the_only_success_has_no_mttr(
    memory: IncidentMemory,
) -> None:
    await memory.record(
        _run(action="restart_deployment", index=0, stored_mttr=None)
    )

    payload = await memory.recall("pod_kill")

    assert payload["by_action"]["restart_deployment"] == {
        "attempts": 1,
        "successes": 1,
        "mean_mttr_seconds": None,
    }


async def test_by_action_actions_are_sorted(memory: IncidentMemory) -> None:
    for index, action in enumerate(
        ("scale_deployment", "restart_deployment", "stop_scenario")
    ):
        await memory.record(_run(action=action, index=index))

    payload = await memory.recall("pod_kill")

    assert list(payload["by_action"]) == [
        "restart_deployment",
        "scale_deployment",
        "stop_scenario",
    ]


async def test_incidents_without_an_action_are_absent_from_by_action(
    memory: IncidentMemory,
) -> None:
    await memory.record(_run(index=0))
    await memory.record(_run(index=1, action="restart_deployment"))

    payload = await memory.recall("pod_kill")

    assert payload["by_action"] == {
        "restart_deployment": {
            "attempts": 1,
            "successes": 1,
            "mean_mttr_seconds": pytest.approx(10.0),
        }
    }
    # The actionless run is still an ordinary incident for the whole aggregate.
    assert len(payload["incidents"]) == 2
    assert payload["confidence"] == 100


async def test_by_action_is_an_empty_object_when_no_incident_names_one(
    memory: IncidentMemory,
) -> None:
    await memory.record(_run(index=0))

    payload = await memory.recall("pod_kill")

    assert payload["by_action"] == {}


async def test_aborted_with_an_action_is_an_attempt_without_a_success(
    memory: IncidentMemory,
) -> None:
    # Chosen semantics: `aborted` stays out of `success_rate`/`confidence` as
    # before, but an aborted incident that names an action records that the
    # action was selected and never validated — an attempt, never a success.
    await memory.record(
        _run(outcome="aborted", action="restart_deployment", index=0)
    )
    await memory.record(_run(outcome="failed", index=1))
    await memory.record(_run(action="restart_deployment", index=2))

    payload = await memory.recall("pod_kill")

    assert payload["confidence"] == 50
    assert payload["by_action"]["restart_deployment"] == {
        "attempts": 2,
        "successes": 1,
        "mean_mttr_seconds": pytest.approx(10.0),
    }


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
