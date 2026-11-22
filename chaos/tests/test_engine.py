"""Engine contract: state machine, events, sweeper and backend reconciliation.

Rows 19-44 plus 59-60 of the M2 behaviour list. The engine is always driven
through `start` / `stop` / `get` / `list` / `remove`; the fakes are inspected,
never the engine's internals.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from engine import (
    BackendError,
    InvalidScenarioStateError,
    ScenarioNotFoundError,
)
from models import (
    ChaosMeshFaultType,
    K8sTarget,
    Scenario,
    ScenarioCreateRequest,
    ScenarioDomain,
    ScenarioStatus,
    SimulatorFaultType,
)
from tests.fakes import (
    ExplodingChaosMeshClient,
    ExplodingSimulatorClient,
    FailingDeleteChaosMeshClient,
)

NAMESPACE = "argus-system"
CREATED = "chaos.scenario.created"
STARTED = "chaos.scenario.started"
STOPPED = "chaos.scenario.stopped"
COMPLETED = "chaos.scenario.completed"
FAILED = "chaos.scenario.failed"


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "pod-kill-orders",
        "correlation_id": "corr-abc",
        "domain": "chaos_mesh",
        "fault_type": "pod_kill",
        "target": {
            "namespace": NAMESPACE,
            "label_selector": {"app": "orders"},
            "mode": "one",
        },
        "duration_seconds": 30,
        "params": {},
    }
    payload.update(overrides)
    return payload


def _request(**overrides: Any) -> ScenarioCreateRequest:
    return ScenarioCreateRequest(**_payload(**overrides))


def _simulator_request(**overrides: Any) -> ScenarioCreateRequest:
    payload = _payload()
    payload.update(
        {
            "domain": "simulator",
            "fault_type": "latency",
            "target": {"resource_type": "volume", "operation": "attach"},
            "params": {"extra_seconds": 1},
            "probability": 0.4,
        }
    )
    payload.update(overrides)
    return ScenarioCreateRequest(**payload)


def _scenario(**overrides: Any) -> Scenario:
    values: dict[str, Any] = {
        "id": "scn-0123456789",
        "name": "pod-kill-orders",
        "correlation_id": "corr-abc",
        "domain": "chaos_mesh",
        "fault_type": "pod_kill",
        "target": K8sTarget(namespace=NAMESPACE, label_selector={"app": "orders"}),
        "duration_seconds": 30,
        "params": {},
    }
    values.update(overrides)
    return Scenario(**values)


def _event_types(events: list[dict[str, Any]]) -> list[str]:
    return [event["event_type"] for event in events]


def test_contract_enums() -> None:
    assert {domain.value for domain in ScenarioDomain} == {"chaos_mesh", "simulator"}
    assert {status.value for status in ScenarioStatus} == {
        "pending",
        "running",
        "stopping",
        "stopped",
        "completed",
        "failed",
    }
    assert {fault.value for fault in ChaosMeshFaultType} == {
        "pod_kill",
        "network_latency",
        "packet_loss",
        "io_delay",
    }
    assert {fault.value for fault in SimulatorFaultType} == {
        "latency",
        "transient_error",
        "partial_failure",
        "quota_limit",
    }


async def test_start_chaos_mesh_sets_state_and_events(
    engine: Any, fake_chaos_mesh: Any, events: list[dict[str, Any]]
) -> None:
    scenario = await engine.start(_request())

    stored = await engine.get(scenario.id, refresh=False)
    assert stored.status == "running"
    assert stored.started_at is not None
    assert stored.ended_at is None
    assert _event_types(events) == [CREATED, STARTED]
    assert events[0]["caused_by"] == scenario.id
    assert events[1]["payload"]["backend_ref"] == scenario.backend_ref


async def test_scenario_is_in_the_store_when_apply_runs(
    make_engine: Any, scenario_store: Any
) -> None:
    observed: list[tuple[str, str | None]] = []

    async def _observe(manifest: dict[str, Any]) -> None:
        scenario_id = manifest["metadata"]["labels"]["kubeheal.io/scenario-id"]
        stored = await scenario_store.get(scenario_id)
        observed.append((scenario_id, None if stored is None else stored.status))

    from tests.fakes import FakeChaosMeshClient

    engine = make_engine(chaos_mesh=FakeChaosMeshClient(on_apply=_observe))

    scenario = await engine.start(_request())

    assert observed, "apply was never called"
    assert observed[0][0] == scenario.id
    assert observed[0][1] in {"pending", "running"}


async def test_unknown_chaos_mesh_fault_type_rejected_before_recording(
    engine: Any, fake_chaos_mesh: Any
) -> None:
    with pytest.raises(ValueError, match="unknown chaos_mesh fault_type"):
        await engine.start(_request(fault_type="pod_bounce"))

    assert await engine.list() == []
    assert fake_chaos_mesh.apply_calls == []


async def test_malformed_target_raises_validation_error(
    engine: Any, fake_chaos_mesh: Any
) -> None:
    with pytest.raises(ValidationError, match="target.value is required"):
        await engine.start(
            ScenarioCreateRequest(**_payload(target={"namespace": NAMESPACE, "mode": "fixed"}))
        )

    assert await engine.list() == []
    assert fake_chaos_mesh.apply_calls == []


async def test_malformed_params_raise_validation_error(
    engine: Any, fake_chaos_mesh: Any
) -> None:
    with pytest.raises(ValidationError):
        await engine.start(_request(fault_type="io_delay", params={"percent": 999}))

    assert await engine.list() == []
    assert fake_chaos_mesh.apply_calls == []


async def test_apply_failure_persists_one_failed_scenario(
    make_engine: Any, events: list[dict[str, Any]]
) -> None:
    engine = make_engine(chaos_mesh=ExplodingChaosMeshClient())

    with pytest.raises(BackendError, match="admission webhook denied the request"):
        await engine.start(_request())

    scenarios = await engine.list()
    assert len(scenarios) == 1
    assert scenarios[0].status == "failed"
    assert "admission webhook denied the request" in (scenarios[0].error or "")
    assert scenarios[0].ended_at is not None
    failures = [event for event in events if event["event_type"] == FAILED]
    assert failures, events
    assert failures[-1]["payload"]["stage"] == "apply"


async def test_start_simulator_registers_rule(
    engine: Any, fake_simulator: Any, events: list[dict[str, Any]]
) -> None:
    scenario = await engine.start(_simulator_request())

    assert scenario.status == "running"
    rule = fake_simulator.rules[scenario.backend_ref]
    assert rule["resource_type"] == "volume"
    assert rule["operation"] == "attach"
    assert rule["probability"] == 0.4
    assert _event_types(events) == [CREATED, STARTED]


async def test_unknown_simulator_fault_type_rejected(engine: Any) -> None:
    with pytest.raises(ValueError, match="unknown simulator fault_type"):
        await engine.start(_simulator_request(fault_type="meteor_strike"))

    assert await engine.list() == []


async def test_simulator_register_failure_persists_failed(
    make_engine: Any, events: list[dict[str, Any]]
) -> None:
    engine = make_engine(simulator=ExplodingSimulatorClient())

    with pytest.raises(BackendError, match="simulator unreachable"):
        await engine.start(_simulator_request())

    scenarios = await engine.list()
    assert len(scenarios) == 1
    assert scenarios[0].status == "failed"
    assert "simulator unreachable" in (scenarios[0].error or "")
    assert [event["event_type"] for event in events][-1] == FAILED


async def test_stop_chaos_mesh_deletes_backend(
    engine: Any, fake_chaos_mesh: Any, events: list[dict[str, Any]]
) -> None:
    scenario = await engine.start(_request())

    stopped = await engine.stop(scenario.id)

    assert stopped.status == "stopped"
    assert stopped.ended_at is not None
    assert fake_chaos_mesh.delete_calls == [("pod_kill", scenario.backend_ref, NAMESPACE)]
    assert events[-1]["event_type"] == STOPPED


async def test_stop_simulator_clears_rule(engine: Any, fake_simulator: Any) -> None:
    scenario = await engine.start(_simulator_request())

    stopped = await engine.stop(scenario.id)

    assert stopped.status == "stopped"
    assert fake_simulator.clear_calls == [scenario.backend_ref]


async def test_stop_unknown_scenario_raises(engine: Any) -> None:
    with pytest.raises(ScenarioNotFoundError):
        await engine.stop("scn-0000000000")


async def test_stop_stopped_scenario_raises_invalid_state(engine: Any) -> None:
    scenario = await engine.start(_request())
    await engine.stop(scenario.id)

    with pytest.raises(InvalidScenarioStateError, match="is stopped, not running"):
        await engine.stop(scenario.id)


async def test_stop_delete_failure_marks_failed(
    make_engine: Any, events: list[dict[str, Any]]
) -> None:
    engine = make_engine(chaos_mesh=FailingDeleteChaosMeshClient())
    scenario = await engine.start(_request())

    with pytest.raises(BackendError, match="apiserver unavailable"):
        await engine.stop(scenario.id)

    stored = await engine.get(scenario.id, refresh=False)
    assert stored.status == "failed"
    assert "apiserver unavailable" in (stored.error or "")
    failures = [event for event in events if event["event_type"] == FAILED]
    assert failures[-1]["payload"]["stage"] == "stop"


async def test_get_unknown_scenario_raises(engine: Any) -> None:
    with pytest.raises(ScenarioNotFoundError):
        await engine.get("scn-0000000000")


async def test_get_running_scenario_refreshes_live_status(engine: Any) -> None:
    scenario = await engine.start(_request())

    refreshed = await engine.get(scenario.id)

    assert refreshed.live_status is not None
    assert set(refreshed.live_status) == {"experiment", "conditions"}


async def test_get_terminal_scenario_does_not_call_backend(
    engine: Any, fake_chaos_mesh: Any
) -> None:
    scenario = await engine.start(_request())
    await engine.stop(scenario.id)
    fake_chaos_mesh.get_calls.clear()

    await engine.get(scenario.id)

    assert fake_chaos_mesh.get_calls == []


async def test_list_filters_by_status(engine: Any) -> None:
    first = await engine.start(_request())
    await engine.start(_request(name="second", fault_type="network_latency"))
    await engine.stop(first.id)

    everything = await engine.list()
    running = await engine.list(status="running")
    stopped = await engine.list(status="stopped")

    assert len(everything) == 2
    assert [scenario.status for scenario in running] == ["running"]
    assert [scenario.id for scenario in stopped] == [first.id]


async def test_remove_stopped_scenario(engine: Any) -> None:
    scenario = await engine.start(_request())
    await engine.stop(scenario.id)

    await engine.remove(scenario.id)

    with pytest.raises(ScenarioNotFoundError):
        await engine.get(scenario.id)


async def test_remove_running_scenario_raises(engine: Any) -> None:
    scenario = await engine.start(_request())

    with pytest.raises(InvalidScenarioStateError, match="still running"):
        await engine.remove(scenario.id)

    still_there = await engine.get(scenario.id, refresh=False)
    assert still_there.status == "running"


async def test_remove_unknown_scenario_raises(engine: Any) -> None:
    with pytest.raises(ScenarioNotFoundError):
        await engine.remove("scn-0000000000")


async def test_vanished_cr_completes_with_course_reason(
    engine: Any, fake_chaos_mesh: Any, events: list[dict[str, Any]]
) -> None:
    scenario = await engine.start(_request())
    fake_chaos_mesh.forget(NAMESPACE, scenario.backend_ref)

    refreshed = await engine.get(scenario.id, refresh=True)

    assert refreshed.status == "completed"
    assert refreshed.ended_at is not None
    completions = [event for event in events if event["event_type"] == COMPLETED]
    assert completions, events
    assert "ran its course" in completions[-1]["payload"]["reason"]


async def test_vanished_simulator_rule_completes(
    engine: Any, fake_simulator: Any, events: list[dict[str, Any]]
) -> None:
    scenario = await engine.start(_simulator_request())
    fake_simulator.forget(scenario.backend_ref)

    refreshed = await engine.get(scenario.id)

    assert refreshed.status == "completed"
    completions = [event for event in events if event["event_type"] == COMPLETED]
    assert completions, events
    assert "expired or was cleared" in completions[-1]["payload"]["reason"]


async def test_sweeper_completes_a_vanished_backend(
    engine: Any, fake_chaos_mesh: Any, events: list[dict[str, Any]]
) -> None:
    scenario = await engine.start(_request())
    fake_chaos_mesh.forget(NAMESPACE, scenario.backend_ref)

    await engine.start_sweeper()
    try:
        await asyncio.sleep(0.2)
        swept = await engine.get(scenario.id, refresh=False)
        assert swept.status == "completed"
        assert COMPLETED in _event_types(events)
    finally:
        await engine.stop_sweeper()


async def test_sweeper_never_queries_a_non_running_scenario(
    engine: Any, fake_chaos_mesh: Any
) -> None:
    scenario = await engine.start(_request())
    await engine.stop(scenario.id)
    fake_chaos_mesh.get_calls.clear()

    await engine.start_sweeper()
    try:
        await asyncio.sleep(0.2)
        assert fake_chaos_mesh.get_calls == []
    finally:
        await engine.stop_sweeper()


async def test_duration_never_completes_a_live_backend(
    engine: Any, fake_chaos_mesh: Any
) -> None:
    scenario = await engine.start(_request(duration_seconds=0.01))

    await engine.get(scenario.id, refresh=True)
    await engine.start_sweeper()
    try:
        await asyncio.sleep(0.2)
        still_running = await engine.get(scenario.id, refresh=False)
        assert still_running.status == "running"
    finally:
        await engine.stop_sweeper()


def test_stopping_never_sets_ended_at() -> None:
    scenario = _scenario()
    scenario.touch_status("running")
    scenario.touch_status("stopping")

    assert scenario.status == "stopping"
    assert scenario.started_at is not None
    assert scenario.ended_at is None


def test_scenario_document_uses_ended_at_and_has_no_blast_radius() -> None:
    scenario = _scenario()
    scenario.touch_status("stopped")

    document = scenario.model_dump_json()

    assert "ended_at" in document
    assert "stopped_at" not in document
    assert "blast_radius" not in scenario.model_dump()
