"""`nodes.py` — the pipeline stages, exercised with fakes only.

Seams (module globals, monkeypatched like `routers.runs`):
`get_blast_radius`, `get_catalog_entry`, `get_scenario`, `stop_scenario`,
`restart_deployment`, `scale_deployment`, `diagnosis_provider`, `log_event`,
`now`, `sleep`, `memory`, `store`. No node may call the network or wait on a
real clock in these tests.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import nodes
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


async def test_heal_plan_requires_approval_for_high_and_medium(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(nodes, "log_event", lambda *_args, **_kwargs: None)

    for risk in ("high", "medium"):
        result = await nodes.heal_plan(_state(diagnosis={**DIAGNOSIS, "risk": risk}))
        assert result["approval_status"] == "pending"


async def test_heal_plan_declares_low_risk_not_required(monkeypatch: Any) -> None:
    monkeypatch.setattr(nodes, "log_event", lambda *_args, **_kwargs: None)

    result = await nodes.heal_plan(_state(diagnosis={**DIAGNOSIS, "risk": "low"}))

    assert result["approval_status"] == "not_required"


async def test_heal_plan_emits_the_pending_event(monkeypatch: Any) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(nodes, "log_event", lambda *args, **kwargs: calls.append((args, kwargs)))

    await nodes.heal_plan(_state(diagnosis=dict(DIAGNOSIS)))

    assert calls
    assert any("kubeheal|sc-1|heal_plan" in repr(call) for call in calls)


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


async def test_verify_polls_until_terminal_and_computes_mttr(monkeypatch: Any) -> None:
    _install_clock(monkeypatch)
    statuses = [
        {"id": "sc-1", "status": "running"},
        {"id": "sc-1", "status": "stopping"},
        {"id": "sc-1", "status": "stopped", "ended_at": "2026-11-22T12:01:40+00:00"},
    ]

    async def get_scenario(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return statuses.pop(0)

    monkeypatch.setattr(nodes, "get_scenario", get_scenario)

    result = await nodes.verify(_state())

    assert result["verify_result"] in {"recovered", "stopped", "completed"}
    assert result["mttr_seconds"] == 100.0
    assert not statuses


async def test_verify_fails_at_the_deadline_while_still_running(monkeypatch: Any) -> None:
    clock = _install_clock(monkeypatch)
    calls: list[str] = []

    async def get_scenario(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "sc-1", "status": "running"}

    async def stop_scenario(scenario_id: str) -> dict[str, Any]:
        calls.append(scenario_id)
        raise RuntimeError("stop refused")

    monkeypatch.setattr(nodes, "get_scenario", get_scenario)
    monkeypatch.setattr(nodes, "stop_scenario", stop_scenario)

    result = await nodes.verify(_state())

    assert result["verify_result"] == "failed"
    assert calls == ["sc-1"]
    assert clock.slept >= 55.0


async def test_verify_survives_a_transient_read_error(monkeypatch: Any) -> None:
    # One failed read is not evidence that the system did not recover: the
    # backend can blip while the fault is being cleaned up.
    _install_clock(monkeypatch)
    responses: list[Any] = [
        RuntimeError("chaos unreachable"),
        {"id": "sc-1", "status": "completed", "ended_at": "2026-11-22T12:01:40+00:00"},
    ]

    async def get_scenario(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(nodes, "get_scenario", get_scenario)

    result = await nodes.verify(_state())

    assert result["verify_result"] in {"recovered", "stopped", "completed"}
    assert result["mttr_seconds"] == 100.0
    assert not responses


async def test_verify_fails_at_the_deadline_naming_the_last_read_error(
    monkeypatch: Any,
) -> None:
    clock = _install_clock(monkeypatch)
    reasons: list[str] = []

    async def get_scenario(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("chaos unreachable")

    def log_event(_event: str, **fields: Any) -> None:
        reasons.append(str(fields.get("reason")))

    monkeypatch.setattr(nodes, "get_scenario", get_scenario)
    monkeypatch.setattr(nodes, "log_event", log_event)

    result = await nodes.verify(_state())

    assert result["verify_result"] == "failed"
    assert 55.0 <= clock.slept <= 70.0
    assert reasons and "chaos unreachable" in reasons[-1]


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


async def test_verify_stops_its_own_fault_at_the_deadline(monkeypatch: Any) -> None:
    # A pod-kill has nothing to restore, so Chaos Mesh never reports recovery and
    # the scenario would stay `running` forever. The fault is ours: stop it, and
    # let the stop produce the terminal state the MTTR is measured from.
    _install_clock(monkeypatch)
    stopped = {"done": False}
    calls: list[str] = []

    async def get_scenario(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        if stopped["done"]:
            return {
                "id": "sc-1",
                "status": "stopped",
                "ended_at": "2026-11-22T12:02:00+00:00",
            }
        return {"id": "sc-1", "status": "running"}

    async def stop_scenario(scenario_id: str) -> dict[str, Any]:
        calls.append(scenario_id)
        stopped["done"] = True
        return {"id": scenario_id, "status": "stopped"}

    monkeypatch.setattr(nodes, "get_scenario", get_scenario)
    monkeypatch.setattr(nodes, "stop_scenario", stop_scenario)

    result = await nodes.verify(_state())

    assert calls == ["sc-1"]
    assert result["verify_result"] == "recovered"
    assert result["mttr_seconds"] == 120.0


async def test_verify_fails_closed_when_the_stop_does_not_help(monkeypatch: Any) -> None:
    clock = _install_clock(monkeypatch)
    calls: list[str] = []

    async def get_scenario(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "sc-1", "status": "running"}

    async def stop_scenario(scenario_id: str) -> dict[str, Any]:
        calls.append(scenario_id)
        raise RuntimeError("chaos unreachable")

    monkeypatch.setattr(nodes, "get_scenario", get_scenario)
    monkeypatch.setattr(nodes, "stop_scenario", stop_scenario)

    result = await nodes.verify(_state())

    assert calls == ["sc-1"]
    assert result["verify_result"] == "failed"
    assert clock.slept >= 55.0
