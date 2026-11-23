"""`nodes.py` — the pipeline stages, exercised with fakes only.

Seams (module globals, monkeypatched like `routers.runs`):
`get_blast_radius`, `get_catalog_entry`, `get_scenario`, `get_workload_health`,
`stop_scenario`, `restart_deployment`, `scale_deployment`, `diagnosis_provider`,
`log_event`, `now`, `sleep`, `memory`, `store`. No node may call the network or
wait on a real clock in these tests.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

import nodes
import providers
from state import new_state

SCENARIO = {
    "id": "sc-1",
    "fault_type": "pod_kill",
    "status": "running",
    "started_at": "2026-11-22T12:00:00+00:00",
    "target": {"namespace": "edge", "label_selector": {"app": "api"}},
}

DIAGNOSIS = {
    "causal_chain": ["pod terminated", "no ready replicas"],
    "recommended_action": "restart_deployment",
    "action_target": "edge/api",
    "risk": "high",
    "rationale": "the deployment has no ready replicas",
}


def _state(**overrides: Any) -> Any:
    state = new_state("sc-1", dict(SCENARIO))
    state.update(overrides)
    return state


async def test_detect_gathers_both_sources_in_parallel(monkeypatch: Any) -> None:
    started: list[str] = []
    release = asyncio.Event()

    async def blast(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        started.append("blast")
        await release.wait()
        return {"affected_nodes": [{"node_id": "edge/api"}]}

    async def catalog(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        started.append("catalog")
        await release.wait()
        return {"fault_type": "pod_kill"}

    monkeypatch.setattr(nodes, "get_blast_radius", blast)
    monkeypatch.setattr(nodes, "get_catalog_entry", catalog)

    async def both_started() -> None:
        while len(started) < 2:
            await asyncio.sleep(0)

    task = asyncio.create_task(nodes.detect(_state()))
    try:
        await asyncio.wait_for(both_started(), timeout=2.0)
    finally:
        release.set()
    result = await task

    assert result["blast_radius"] == {"affected_nodes": [{"node_id": "edge/api"}]}
    assert result["catalog_entry"] == {"fault_type": "pod_kill"}


async def test_detect_survives_a_blind_source(monkeypatch: Any) -> None:
    async def blast(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("graph unreachable")

    async def catalog(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"fault_type": "pod_kill", "authored": True}

    monkeypatch.setattr(nodes, "get_blast_radius", blast)
    monkeypatch.setattr(nodes, "get_catalog_entry", catalog)

    result = await nodes.detect(_state())

    assert result["blast_radius"] == {}
    assert result["catalog_entry"] == {"fault_type": "pod_kill", "authored": True}


async def test_diagnose_parses_strict_json(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        nodes, "diagnosis_provider", lambda **_kwargs: json.dumps(DIAGNOSIS)
    )

    result = await nodes.diagnose(_state())

    diagnosis = result["diagnosis"]
    assert diagnosis["recommended_action"] == "restart_deployment"
    assert diagnosis["action_target"] == "edge/api"
    assert diagnosis["risk"] == "high"


async def test_diagnose_strips_markdown_fences(monkeypatch: Any) -> None:
    fenced = "```json\n" + json.dumps(DIAGNOSIS) + "\n```"
    monkeypatch.setattr(nodes, "diagnosis_provider", lambda **_kwargs: fenced)

    result = await nodes.diagnose(_state())

    assert result["diagnosis"]["recommended_action"] == "restart_deployment"
    assert result["diagnosis"]["risk"] == "high"


async def test_diagnose_falls_back_closed_on_unparseable_output(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        nodes, "diagnosis_provider", lambda **_kwargs: "this is not JSON at all"
    )

    result = await nodes.diagnose(_state())
    diagnosis = result["diagnosis"]

    assert diagnosis["recommended_action"] == "stop_scenario"
    assert diagnosis["risk"] == "low"
    assert diagnosis["diagnosis_provenance"]["mode"] == "stub"
    assert diagnosis.get("parse_error") or diagnosis.get("error")


async def test_diagnose_forwards_blast_radius_to_the_provider(
    monkeypatch: Any, stub_provider: Any
) -> None:
    seen: dict[str, Any] = {}

    def provider(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return stub_provider(**kwargs)

    monkeypatch.setattr(nodes, "diagnosis_provider", provider)
    blast = {"affected_nodes": [{"node_id": "edge/api", "distance_hops": 1}]}

    await nodes.diagnose(_state(blast_radius=blast))

    assert seen["blast_radius"] == blast
    assert seen["blast_radius"]["affected_nodes"]


MEMORY_INCIDENT_KEYS = {
    "scenario_id",
    "fault_type",
    "outcome",
    "risk",
    "approval_status",
    "mttr_seconds",
}


def _incident(index: int, **overrides: Any) -> dict[str, Any]:
    """One stored incident, as `report` writes it: the scenario document included."""
    incident = {
        "scenario_id": f"sc-{index}",
        "scenario": dict(SCENARIO),
        "fault_type": "pod_kill",
        "outcome": "success",
        "risk": "high",
        "approval_status": "approved",
        "mttr_seconds": 60.0 + index,
        "status": "done",
        "started_at": "2026-11-22T12:00:00+00:00",
        "ended_at": "2026-11-22T12:01:00+00:00",
    }
    incident.update(overrides)
    return incident


class _FakeMemory:
    """The `recall` seam only; `record` belongs to `report`."""

    def __init__(
        self,
        incidents: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.incidents = incidents or []
        self.error = error
        self.calls: list[str] = []

    async def recall(self, fault_type: str, limit: int = 20) -> dict[str, Any]:
        self.calls.append(fault_type)
        if self.error is not None:
            raise self.error
        return {
            "incidents": list(self.incidents),
            "success_rate": 0.5,
            "confidence": 50,
            "mean_mttr_seconds": 61.0,
        }


@pytest.fixture(autouse=True)
def _empty_memory(monkeypatch: Any) -> _FakeMemory:
    """Diagnose tests must not read the session-wide incident DB.

    Without this, `memory_context` depends on which earlier test file recorded
    a run. Tests that need incidents replace the fake through monkeypatch.
    """
    fake = _FakeMemory()
    monkeypatch.setattr(nodes, "memory", fake)
    return fake


async def test_diagnose_feeds_bounded_memory_into_the_provider_payload(
    monkeypatch: Any,
) -> None:
    incidents = [
        _incident(1),
        _incident(
            2,
            outcome="failed",
            risk="medium",
            approval_status="rejected",
            mttr_seconds=None,
        ),
    ]
    monkeypatch.setattr(nodes, "memory", _FakeMemory(incidents))
    seen: dict[str, Any] = {}

    def provider(**kwargs: Any) -> str:
        seen.update(kwargs)
        return json.dumps(DIAGNOSIS)

    monkeypatch.setattr(nodes, "diagnosis_provider", provider)

    result = await nodes.diagnose(_state())

    context = {
        "incidents": [
            {
                "scenario_id": "sc-1",
                "fault_type": "pod_kill",
                "outcome": "success",
                "risk": "high",
                "approval_status": "approved",
                "mttr_seconds": 61.0,
            },
            {
                "scenario_id": "sc-2",
                "fault_type": "pod_kill",
                "outcome": "failed",
                "risk": "medium",
                "approval_status": "rejected",
                "mttr_seconds": None,
            },
        ],
        "success_rate": 0.5,
        "confidence": 50,
        "mean_mttr_seconds": 61.0,
    }
    assert result["memory_context"] == context
    assert seen["memory_context"] == context
    assert "scenario" not in seen["memory_context"]["incidents"][0]
    assert "target" not in seen["memory_context"]["incidents"][0]


async def test_memory_summaries_are_capped_and_key_whitelisted(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        nodes, "memory", _FakeMemory([_incident(index) for index in range(25)])
    )
    monkeypatch.setattr(
        nodes, "diagnosis_provider", lambda **_kwargs: json.dumps(DIAGNOSIS)
    )

    result = await nodes.diagnose(_state())

    summaries = result["memory_context"]["incidents"]
    assert len(summaries) == 20
    assert all(set(summary) == MEMORY_INCIDENT_KEYS for summary in summaries)
    assert summaries[0]["scenario_id"] == "sc-0"
    assert summaries[-1]["scenario_id"] == "sc-19"


async def test_empty_memory_keeps_the_prompt_hash_unchanged(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def provider(**kwargs: Any) -> str:
        captured.update(kwargs)
        return json.dumps(DIAGNOSIS)

    monkeypatch.setattr(nodes, "diagnosis_provider", provider)
    state = _state(
        blast_radius={"affected_nodes": []},
        catalog_entry={"domain": "chaos_mesh", "fault_type": "pod_kill"},
    )

    result = await nodes.diagnose(state)

    before = {
        "scenario": state["scenario"],
        "blast_radius": {"affected_nodes": []},
        "catalog_entry": {"domain": "chaos_mesh", "fault_type": "pod_kill"},
        "memory_context": None,
    }
    assert result["memory_context"] is None
    assert captured == before
    provenance = result["diagnosis"]["diagnosis_provenance"]
    assert providers.prompt_sha256(before) == provenance["prompt_sha256"]
    assert providers.prompt_sha256(captured) == provenance["prompt_sha256"]


async def _hash_with(monkeypatch: Any, incidents: list[dict[str, Any]]) -> str:
    monkeypatch.setattr(nodes, "memory", _FakeMemory(incidents))
    monkeypatch.setattr(
        nodes, "diagnosis_provider", lambda **_kwargs: json.dumps(DIAGNOSIS)
    )
    result = await nodes.diagnose(_state())
    return result["diagnosis"]["diagnosis_provenance"]["prompt_sha256"]


async def test_incident_memory_changes_the_prompt_hash(monkeypatch: Any) -> None:
    # The hash is what a cassette keys on: memory that never moved the hash
    # never reached the payload either.
    empty = await _hash_with(monkeypatch, [])
    with_memory = await _hash_with(monkeypatch, [_incident(1)])

    assert empty != with_memory


async def test_recall_failure_is_logged_once_and_does_not_fail_the_run(
    monkeypatch: Any,
) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        nodes, "log_event", lambda event, **fields: events.append((event, fields))
    )
    monkeypatch.setattr(
        nodes, "memory", _FakeMemory(error=RuntimeError("memory is down"))
    )
    monkeypatch.setattr(
        nodes, "diagnosis_provider", lambda **_kwargs: json.dumps(DIAGNOSIS)
    )

    result = await nodes.diagnose(_state())

    assert result["memory_context"] is None
    assert result["diagnosis"]["recommended_action"] == "restart_deployment"
    unavailable = [
        fields for event, fields in events if event == "agent.memory.unavailable"
    ]
    assert len(unavailable) == 1
    assert "memory is down" in unavailable[0]["error"]


async def test_scenario_without_a_fault_type_never_recalls(monkeypatch: Any) -> None:
    fake = _FakeMemory([_incident(1)])
    monkeypatch.setattr(nodes, "memory", fake)
    monkeypatch.setattr(
        nodes, "diagnosis_provider", lambda **_kwargs: json.dumps(DIAGNOSIS)
    )
    scenario = {key: value for key, value in SCENARIO.items() if key != "fault_type"}

    result = await nodes.diagnose(_state(scenario=scenario))

    assert fake.calls == []
    assert result["memory_context"] is None


async def test_heal_plan_requires_approval_for_high_and_medium(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(nodes, "log_event", lambda *_args, **_kwargs: None)

    for risk in ("high", "medium"):
        result = await nodes.heal_plan(_state(diagnosis={**DIAGNOSIS, "risk": risk}))
        assert result["approval_status"] == "pending"


async def test_heal_plan_declares_low_risk_not_required(monkeypatch: Any) -> None:
    monkeypatch.setattr(nodes, "log_event", lambda *_args, **_kwargs: None)

    # Adapted for F2: `stop_scenario` is the one action whose policy floor is
    # `low`, so it is the only action that can still auto-execute on a `low`
    # proposal. The old body used `restart_deployment` with `risk: low`, which
    # encoded the pass-through bug itself and must not be asserted any more.
    result = await nodes.heal_plan(
        _state(diagnosis={**DIAGNOSIS, "recommended_action": "stop_scenario", "risk": "low"})
    )

    assert result["approval_status"] == "not_required"
    assert result["risk"] == "low"


async def test_heal_plan_escalates_a_low_proposed_restart_to_medium(
    monkeypatch: Any,
) -> None:
    # The F2 regression: a model that says `low` for a disruptive action must
    # not authorise itself. Before the policy module this returned `not_required`.
    monkeypatch.setattr(nodes, "log_event", lambda *_args, **_kwargs: None)

    result = await nodes.heal_plan(
        _state(diagnosis={**DIAGNOSIS, "recommended_action": "restart_deployment", "risk": "low"})
    )

    assert result["approval_status"] == "pending"
    assert result["risk"] == "medium"


async def test_heal_plan_escalates_a_low_proposed_scale_to_high(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(nodes, "log_event", lambda *_args, **_kwargs: None)

    result = await nodes.heal_plan(
        _state(diagnosis={**DIAGNOSIS, "recommended_action": "scale_deployment", "risk": "low"})
    )

    assert result["approval_status"] == "pending"
    assert result["risk"] == "high"


async def test_heal_plan_records_why_the_risk_was_raised(monkeypatch: Any) -> None:
    monkeypatch.setattr(nodes, "log_event", lambda *_args, **_kwargs: None)

    result = await nodes.heal_plan(
        _state(diagnosis={**DIAGNOSIS, "recommended_action": "restart_deployment", "risk": "low"})
    )

    assert result["risk_policy"] == {
        "proposed_risk": "low",
        "effective_risk": "medium",
        "minimum_risk": "medium",
        "raised": True,
    }


async def test_heal_plan_fails_closed_on_an_unknown_action(monkeypatch: Any) -> None:
    monkeypatch.setattr(nodes, "log_event", lambda *_args, **_kwargs: None)

    result = await nodes.heal_plan(
        _state(diagnosis={**DIAGNOSIS, "recommended_action": "delete_everything", "risk": "low"})
    )

    assert result["approval_status"] == "pending"
    assert result["risk"] == "high"


async def test_heal_plan_emits_the_pending_event(monkeypatch: Any) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(nodes, "log_event", lambda *args, **kwargs: calls.append((args, kwargs)))

    await nodes.heal_plan(_state(diagnosis=dict(DIAGNOSIS)))

    assert calls
    assert any("kubeheal|sc-1|heal_plan" in repr(call) for call in calls)


async def test_heal_plan_publishes_the_effective_risk_the_human_approves(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(
        nodes, "log_event", lambda *args, **kwargs: calls.append((args, kwargs))
    )

    await nodes.heal_plan(
        _state(diagnosis={**DIAGNOSIS, "recommended_action": "restart_deployment", "risk": "low"})
    )

    _, fields = calls[-1]
    assert fields["risk"] == "medium"
    assert fields["proposed_risk"] == "low"
    assert fields["approval_status"] == "pending"


async def test_execute_refuses_an_unknown_action(monkeypatch: Any) -> None:
    invoked: list[tuple[str, Any]] = []

    async def stop(scenario_id: str) -> dict[str, Any]:
        invoked.append(("stop_scenario", scenario_id))
        return {"id": scenario_id, "status": "stopping"}

    async def restart(*args: Any, **_kwargs: Any) -> dict[str, Any]:
        invoked.append(("restart_deployment", args))
        return {}

    async def scale(*args: Any, **_kwargs: Any) -> dict[str, Any]:
        invoked.append(("scale_deployment", args))
        return {}

    monkeypatch.setattr(nodes, "stop_scenario", stop)
    monkeypatch.setattr(nodes, "restart_deployment", restart)
    monkeypatch.setattr(nodes, "scale_deployment", scale)

    state = _state(
        approval_status="approved",
        diagnosis={**DIAGNOSIS, "recommended_action": "delete_everything"},
    )

    result = await nodes.execute(state)

    assert [name for name, _ in invoked] == ["stop_scenario"]
    assert "sc-1" in repr(invoked[0][1])
    assert result["action_result"]


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
        self.slept = 0.0
        self.ticks = 0

    def now(self) -> datetime:
        self.ticks += 1
        if self.ticks > 500:
            raise AssertionError("verify never stopped polling")
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.value += timedelta(seconds=seconds)


def _install_clock(monkeypatch: Any) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(nodes, "now", clock.now)
    monkeypatch.setattr(nodes, "sleep", clock.sleep)
    return clock


def _verify_state(**overrides: Any) -> Any:
    return _state(diagnosis={"action_target": "edge/api"}, **overrides)


def _health(
    desired: int = 2,
    ready: int = 2,
    available: int | None = None,
) -> dict[str, Any]:
    if available is None:
        available = ready
    return {
        "namespace": "edge",
        "deployment": "api",
        "desired": desired,
        "ready": ready,
        "available": available,
        "ready_pods": ready,
        "observed_at": "2026-11-22T12:00:00Z",
    }


async def test_verify_polls_until_the_workload_recovers_and_computes_mttr(
    monkeypatch: Any,
) -> None:
    _install_clock(monkeypatch)
    scenarios = [
        {"id": "sc-1", "status": "running"},
        {"id": "sc-1", "status": "stopping"},
        {"id": "sc-1", "status": "stopped", "ended_at": "2026-11-22T12:01:40+00:00"},
    ]
    health = [
        _health(ready=0, available=0),
        _health(ready=1, available=1),
        _health(ready=2, available=2),
    ]

    async def get_scenario(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return scenarios.pop(0)

    async def get_workload_health(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return health.pop(0)

    monkeypatch.setattr(nodes, "get_scenario", get_scenario)
    monkeypatch.setattr(nodes, "get_workload_health", get_workload_health)

    result = await nodes.verify(_verify_state())

    assert result["verify_result"] == "recovered"
    assert result["mttr_seconds"] == 100.0
    assert not scenarios
    assert not health


async def test_verify_fails_when_the_workload_never_becomes_ready(
    monkeypatch: Any,
) -> None:
    # F5 negative control: the scenario can report whatever it likes; without
    # ready replicas the run is a failure and MTTR stays unknown.
    clock = _install_clock(monkeypatch)
    stop_calls: list[str] = []

    async def get_scenario(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "sc-1", "status": "running"}

    async def get_workload_health(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return _health(desired=2, ready=0, available=0)

    async def stop_scenario(scenario_id: str) -> dict[str, Any]:
        stop_calls.append(scenario_id)
        return {"id": scenario_id, "status": "stopped"}

    monkeypatch.setattr(nodes, "get_scenario", get_scenario)
    monkeypatch.setattr(nodes, "get_workload_health", get_workload_health)
    monkeypatch.setattr(nodes, "stop_scenario", stop_scenario)

    result = await nodes.verify(_verify_state())

    assert result["verify_result"] == "failed"
    assert result["mttr_seconds"] is None
    assert stop_calls == []
    assert clock.slept >= 55.0


async def test_verify_recovers_on_workload_health_while_the_scenario_still_runs(
    monkeypatch: Any,
) -> None:
    # A scenario that is still running must not block a genuine recovery, and
    # verify must not stop it: the run gets exactly one approved write.
    _install_clock(monkeypatch)
    stop_calls: list[str] = []
    health = [_health(ready=0, available=0), _health(ready=2, available=2)]

    async def get_scenario(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "sc-1", "status": "running"}

    async def get_workload_health(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return health.pop(0)

    async def stop_scenario(scenario_id: str) -> dict[str, Any]:
        stop_calls.append(scenario_id)
        return {"id": scenario_id, "status": "stopped"}

    monkeypatch.setattr(nodes, "get_scenario", get_scenario)
    monkeypatch.setattr(nodes, "get_workload_health", get_workload_health)
    monkeypatch.setattr(nodes, "stop_scenario", stop_scenario)

    result = await nodes.verify(_verify_state())

    assert result["verify_result"] == "recovered"
    assert result["mttr_seconds"] == 5.0
    assert stop_calls == []
    assert not health


async def test_verify_does_not_trust_a_stopped_scenario_without_ready_replicas(
    monkeypatch: Any,
) -> None:
    # Workload truth outranks the scenario document: `stopped` is not recovery.
    clock = _install_clock(monkeypatch)

    async def get_scenario(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "id": "sc-1",
            "status": "stopped",
            "ended_at": "2026-11-22T12:01:40+00:00",
        }

    async def get_workload_health(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return _health(desired=2, ready=0, available=0)

    monkeypatch.setattr(nodes, "get_scenario", get_scenario)
    monkeypatch.setattr(nodes, "get_workload_health", get_workload_health)

    result = await nodes.verify(_verify_state())

    assert result["verify_result"] == "failed"
    assert result["mttr_seconds"] is None
    assert clock.slept >= 55.0


@pytest.mark.parametrize("target", [None, "", "edge", "/api", "edge/", "edge/api/x"])
async def test_verify_fails_closed_on_a_missing_or_malformed_target(
    monkeypatch: Any, target: Any
) -> None:
    touched: list[str] = []

    async def get_scenario(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        touched.append("scenario")
        return {}

    async def get_workload_health(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        touched.append("health")
        return _health()

    monkeypatch.setattr(nodes, "get_scenario", get_scenario)
    monkeypatch.setattr(nodes, "get_workload_health", get_workload_health)

    result = await nodes.verify(_state(diagnosis={"action_target": target}))

    assert result["verify_result"] == "failed"
    assert result["mttr_seconds"] is None
    assert touched == []


async def test_verify_fails_closed_without_a_diagnosis(monkeypatch: Any) -> None:
    result = await nodes.verify(_state())

    assert result["verify_result"] == "failed"
    assert result["mttr_seconds"] is None


async def test_verify_survives_a_transient_scenario_read_error(
    monkeypatch: Any,
) -> None:
    # One failed read is not evidence that the system did not recover: the
    # backend can blip while the fault is being cleaned up.
    _install_clock(monkeypatch)
    responses: list[Any] = [
        RuntimeError("chaos unreachable"),
        {"id": "sc-1", "status": "running"},
    ]
    health = [_health(ready=0, available=0), _health()]

    async def get_scenario(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def get_workload_health(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return health.pop(0)

    monkeypatch.setattr(nodes, "get_scenario", get_scenario)
    monkeypatch.setattr(nodes, "get_workload_health", get_workload_health)

    result = await nodes.verify(_verify_state())

    assert result["verify_result"] == "recovered"
    assert result["mttr_seconds"] == 5.0
    assert not responses


async def test_verify_survives_a_transient_health_read_error(monkeypatch: Any) -> None:
    _install_clock(monkeypatch)
    health: list[Any] = [RuntimeError("kube api unreachable"), _health()]

    async def get_scenario(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "sc-1", "status": "running"}

    async def get_workload_health(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        item = health.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(nodes, "get_scenario", get_scenario)
    monkeypatch.setattr(nodes, "get_workload_health", get_workload_health)

    result = await nodes.verify(_verify_state())

    assert result["verify_result"] == "recovered"
    assert result["mttr_seconds"] == 5.0
    assert not health


async def test_verify_fails_at_the_deadline_naming_the_workload_state(
    monkeypatch: Any,
) -> None:
    clock = _install_clock(monkeypatch)
    reasons: list[str] = []

    async def get_scenario(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "sc-1", "status": "running"}

    async def get_workload_health(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return _health(desired=2, ready=1, available=1)

    def log_event(_event: str, **fields: Any) -> None:
        reasons.append(str(fields.get("reason")))

    monkeypatch.setattr(nodes, "get_scenario", get_scenario)
    monkeypatch.setattr(nodes, "get_workload_health", get_workload_health)
    monkeypatch.setattr(nodes, "log_event", log_event)

    result = await nodes.verify(_verify_state())

    assert result["verify_result"] == "failed"
    assert 55.0 <= clock.slept <= 70.0
    assert reasons and reasons[-1].startswith(
        "workload not ready: 1/2 replicas after 60s"
    )


async def test_verify_fails_at_the_deadline_naming_the_last_read_errors(
    monkeypatch: Any,
) -> None:
    clock = _install_clock(monkeypatch)
    reasons: list[str] = []

    async def get_scenario(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("chaos unreachable")

    async def get_workload_health(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("kube api unreachable")

    def log_event(_event: str, **fields: Any) -> None:
        reasons.append(str(fields.get("reason")))

    monkeypatch.setattr(nodes, "get_scenario", get_scenario)
    monkeypatch.setattr(nodes, "get_workload_health", get_workload_health)
    monkeypatch.setattr(nodes, "log_event", log_event)

    result = await nodes.verify(_verify_state())

    assert result["verify_result"] == "failed"
    assert 55.0 <= clock.slept <= 70.0
    assert reasons and "workload not ready" in reasons[-1]
    assert "kube api unreachable" in reasons[-1]
    assert "chaos unreachable" in reasons[-1]


async def test_report_writes_memory_and_closes_the_run_done(monkeypatch: Any) -> None:
    recorded: list[dict[str, Any]] = []

    class FakeMemory:
        async def record(self, run: dict[str, Any]) -> None:
            recorded.append(run)

    class FakeStore:
        def __init__(self) -> None:
            self.saved: list[dict[str, Any]] = []

        async def save(self, run: dict[str, Any]) -> None:
            self.saved.append(run)

    store = FakeStore()
    monkeypatch.setattr(nodes, "memory", FakeMemory())
    monkeypatch.setattr(nodes, "store", store)

    state = _state(
        node="verify",
        approval_status="approved",
        verify_result="recovered",
        mttr_seconds=100.0,
    )

    result = await nodes.report(state)

    assert recorded and recorded[0]["outcome"] == "success"
    assert store.saved and store.saved[-1]["status"] == "done"
    assert result.get("status") == "done"


# The two stop-at-the-deadline tests that lived here were removed by decision:
# verify no longer stops the fault, so a test asserting the stop call would
# contradict CONTRACT D2. The replacement is
# `test_verify_recovers_on_workload_health_while_the_scenario_still_runs`
# (the positive case) and `test_verify_fails_when_the_workload_never_becomes_ready`
# (the F5 negative control).
