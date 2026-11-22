"""Scenario orchestration: validation, state machine, events and sweeper.

The engine owns every transition. A backend call tells it what happened; the
engine never infers completion from a duration elapsing. The store is written
before each backend call and again after it, so a crash leaves a documented
state rather than a silent one.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, Callable

import structlog

from chaos_mesh import build_manifest, parse_params, summarize_status
from config import config
from events import Severity, build_event, publish_event
from models import (
    ChaosMeshFaultType,
    K8sTarget,
    Scenario,
    ScenarioCreateRequest,
    ScenarioDomain,
    ScenarioStatus,
    SimulatorFaultType,
)
from store import ScenarioStore

log = structlog.get_logger()

COMPONENT = "chaos.scenario"

CREATED = "chaos.scenario.created"
STARTED = "chaos.scenario.started"
STOPPED = "chaos.scenario.stopped"
COMPLETED = "chaos.scenario.completed"
FAILED = "chaos.scenario.failed"

_SIMULATOR_RESOURCE_TYPES = {"volume", "subnet", "instance"}

_TERMINAL = {ScenarioStatus.STOPPED, ScenarioStatus.COMPLETED, ScenarioStatus.FAILED}

EventSink = Callable[[dict[str, Any]], None]


class ScenarioNotFoundError(Exception):
    """No scenario with that id exists."""


class InvalidScenarioStateError(Exception):
    """The scenario is not in a state that allows the requested transition."""


class BackendError(Exception):
    """The request was well formed but the backend refused or was unreachable."""


def _parse_enum(enum_cls: type, value: str, label: str) -> Any:
    try:
        return enum_cls(value)
    except ValueError:
        valid = ", ".join(member.value for member in enum_cls)
        raise ValueError(
            f"unknown {label} fault_type '{value}' — expected one of: {valid}"
        ) from None


class ScenarioEngine:
    """Control surface for both backends."""

    def __init__(
        self,
        *,
        store: ScenarioStore,
        chaos_mesh: Any,
        simulator: Any,
        sweep_interval_seconds: float | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self._store = store
        self._chaos_mesh = chaos_mesh
        self._simulator = simulator
        self._sweep_interval = (
            sweep_interval_seconds
            if sweep_interval_seconds is not None
            else config.SWEEP_INTERVAL_SECONDS
        )
        self._event_sink = event_sink
        self._sweeper_task: asyncio.Task[None] | None = None

    # -- engine lifecycle ---------------------------------------------------

    async def start_sweeper(self) -> None:
        """Start the reconciliation loop; starting twice is a no-op."""
        if self._sweeper_task is None or self._sweeper_task.done():
            self._sweeper_task = asyncio.create_task(self._sweep_loop())

    async def stop_sweeper(self) -> None:
        """Stop the reconciliation loop and wait for it to unwind."""
        task, self._sweeper_task = self._sweeper_task, None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self._sweep_interval)
            for scenario in await self._store.list(status=ScenarioStatus.RUNNING):
                try:
                    await self._sync(scenario)
                except Exception as exc:  # a bad backend read must not stop the loop
                    log.warning(
                        "chaos_sweep_failed",
                        scenario_id=scenario.id,
                        error=str(exc),
                    )

    # -- public operations --------------------------------------------------

    async def start(self, request: ScenarioCreateRequest) -> Scenario:
        """Validate, record and apply a scenario.

        Anything malformed is rejected before a record exists. A backend that
        refuses the apply leaves one `failed` scenario behind, so the failure
        is visible instead of vanishing.
        """
        scenario = self._build_scenario(request)
        manifest: dict[str, Any] | None = None

        if scenario.domain is ScenarioDomain.CHAOS_MESH:
            # Built before the record exists: a manifest that cannot be built
            # is a validation error, not a failed scenario.
            manifest = build_manifest(scenario)

        await self._store.save(scenario)
        self._emit(
            CREATED,
            scenario,
            severity=Severity.INFO,
            payload={
                "scenario_id": scenario.id,
                "name": scenario.name,
                "domain": scenario.domain.value,
                "fault_type": scenario.fault_type,
                "target": _as_dict(scenario.target),
                "duration_seconds": scenario.duration_seconds,
            },
            reason="scenario requested",
        )

        try:
            if scenario.domain is ScenarioDomain.CHAOS_MESH:
                if manifest is None:  # pragma: no cover - built above
                    raise ValueError("chaos_mesh manifest was not built")
                backend_ref, _ = await self._chaos_mesh.apply(manifest)
            else:
                rule = await self._simulator.register_fault(
                    self._simulator_request(request)
                )
                backend_ref = rule["id"]
        except Exception as exc:
            scenario.touch_status(ScenarioStatus.FAILED, error=str(exc))
            await self._store.save(scenario)
            self._emit(
                FAILED,
                scenario,
                severity=Severity.HIGH,
                payload={
                    "scenario_id": scenario.id,
                    "stage": "apply",
                    "error": str(exc),
                },
                reason=str(exc),
            )
            raise BackendError(str(exc)) from exc

        scenario.backend_ref = backend_ref
        scenario.touch_status(ScenarioStatus.RUNNING)
        await self._store.save(scenario)
        self._emit(
            STARTED,
            scenario,
            severity=Severity.MEDIUM,
            payload={
                "scenario_id": scenario.id,
                "domain": scenario.domain.value,
                "backend_ref": backend_ref,
            },
            reason="applied to backend",
        )
        return scenario

    async def stop(self, scenario_id: str) -> Scenario:
        """Tear a running scenario down before its natural end."""
        scenario = await self._require(scenario_id)
        if scenario.status != ScenarioStatus.RUNNING:
            raise InvalidScenarioStateError(
                f"scenario '{scenario_id}' is {scenario.status.value}, not running"
            )

        scenario.touch_status(ScenarioStatus.STOPPING)
        await self._store.save(scenario)

        try:
            if scenario.domain is ScenarioDomain.CHAOS_MESH:
                await self._chaos_mesh.delete(
                    scenario.fault_type,
                    scenario.backend_ref,
                    _namespace(scenario),
                )
            else:
                await self._simulator.clear_fault(scenario.backend_ref)
        except Exception as exc:
            scenario.touch_status(ScenarioStatus.FAILED, error=str(exc))
            await self._store.save(scenario)
            self._emit(
                FAILED,
                scenario,
                severity=Severity.HIGH,
                payload={
                    "scenario_id": scenario.id,
                    "stage": "stop",
                    "error": str(exc),
                },
                reason=str(exc),
            )
            raise BackendError(str(exc)) from exc

        scenario.touch_status(ScenarioStatus.STOPPED)
        await self._store.save(scenario)
        self._emit(
            STOPPED,
            scenario,
            severity=Severity.INFO,
            payload={"scenario_id": scenario.id},
            reason="stopped on request",
        )
        return scenario

    async def get(self, scenario_id: str, *, refresh: bool = True) -> Scenario:
        """Return one scenario, optionally asking the backend for fresh status.

        Only a running scenario has a backend worth polling.
        """
        scenario = await self._require(scenario_id)
        if refresh and scenario.status == ScenarioStatus.RUNNING:
            scenario = await self._sync(scenario)
        return scenario

    async def list(self, *, status: ScenarioStatus | str | None = None) -> list[Scenario]:
        return await self._store.list(status=status)

    async def remove(self, scenario_id: str) -> None:
        """Delete a terminal scenario; a live one must be stopped first."""
        scenario = await self._require(scenario_id)
        if scenario.status == ScenarioStatus.RUNNING:
            raise InvalidScenarioStateError(
                f"scenario '{scenario_id}' is still running — stop it first"
            )
        if scenario.status not in _TERMINAL:
            raise InvalidScenarioStateError(
                f"scenario '{scenario_id}' is {scenario.status.value}, not removable"
            )
        await self._store.delete(scenario_id)

    # -- validation ---------------------------------------------------------

    def _build_scenario(self, request: ScenarioCreateRequest) -> Scenario:
        if request.domain is ScenarioDomain.CHAOS_MESH:
            fault_type = _parse_enum(ChaosMeshFaultType, request.fault_type, "chaos_mesh")
            target = K8sTarget.model_validate(request.target)
            parse_params(fault_type, request.params)
            return Scenario(
                name=request.name,
                correlation_id=request.correlation_id,
                domain=request.domain,
                fault_type=fault_type.value,
                target=target.model_dump(),
                duration_seconds=request.duration_seconds,
                params=request.params,
            )

        fault_type = _parse_enum(SimulatorFaultType, request.fault_type, "simulator")
        self._validate_simulator_target(request.target)
        return Scenario(
            name=request.name,
            correlation_id=request.correlation_id,
            domain=request.domain,
            fault_type=fault_type.value,
            target=request.target,
            duration_seconds=request.duration_seconds,
            params=request.params,
        )

    @staticmethod
    def _validate_simulator_target(target: dict[str, Any]) -> None:
        resource_type = target.get("resource_type")
        if resource_type is not None and resource_type not in _SIMULATOR_RESOURCE_TYPES:
            valid = ", ".join(sorted(_SIMULATOR_RESOURCE_TYPES))
            raise ValueError(
                f"unknown simulator resource_type '{resource_type}' — expected one of: {valid}"
            )
        operation = target.get("operation")
        if operation is not None and not isinstance(operation, str):
            raise ValueError("simulator target.operation must be a string")

    @staticmethod
    def _simulator_request(request: ScenarioCreateRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "fault_type": request.fault_type,
            "probability": request.probability,
            "params": request.params,
        }
        for key in ("resource_type", "operation"):
            value = request.target.get(key)
            if value is not None:
                body[key] = value
        if request.duration_seconds is not None:
            body["duration_seconds"] = request.duration_seconds
        return body

    # -- reconciliation -----------------------------------------------------

    async def _require(self, scenario_id: str) -> Scenario:
        scenario = await self._store.get(scenario_id)
        if scenario is None:
            raise ScenarioNotFoundError(f"scenario '{scenario_id}' not found")
        return scenario

    async def _sync(self, scenario: Scenario) -> Scenario:
        """Ask the backend whether the fault is still live.

        Completion is deduced only from the backend object's absence.
        """
        if scenario.status != ScenarioStatus.RUNNING:
            return scenario

        if scenario.domain is ScenarioDomain.CHAOS_MESH:
            try:
                resource = await self._chaos_mesh.get(
                    scenario.fault_type,
                    scenario.backend_ref,
                    _namespace(scenario),
                )
            except Exception as exc:
                raise BackendError(str(exc)) from exc
            if resource is None:
                await self._mark_completed(
                    scenario,
                    reason="Chaos Mesh experiment no longer exists — it ran its course",
                )
                return scenario
            scenario.live_status = summarize_status(resource.get("status"))
            if _reports_recovery(scenario.live_status):
                await self._delete_recovered(scenario)
        else:
            try:
                rule = await self._simulator.get_fault(scenario.backend_ref)
            except Exception as exc:
                raise BackendError(str(exc)) from exc
            if rule is None:
                await self._mark_completed(
                    scenario,
                    reason="simulator fault rule expired or was cleared",
                )
                return scenario
            scenario.live_status = {
                "hits": rule.get("hits"),
                "expires_at": rule.get("expires_at"),
            }

        await self._store.save(scenario)
        return scenario

    async def _delete_recovered(self, scenario: Scenario) -> None:
        """Remove a CR that reports recovery but is still in the cluster.

        Chaos Mesh marks a finished experiment `AllRecovered` and then leaves
        the object behind; completion is only deduced from absence, so nothing
        would ever look again. Deleting it here is best-effort: the scenario
        stays running and the next sweep concludes through the existing path.
        """
        try:
            await self._chaos_mesh.delete(
                scenario.fault_type,
                scenario.backend_ref,
                _namespace(scenario),
            )
        except Exception as exc:
            log.warning(
                "chaos_mesh_recovered_delete_failed",
                scenario_id=scenario.id,
                backend_ref=scenario.backend_ref,
                error=str(exc),
            )

    async def _mark_completed(self, scenario: Scenario, *, reason: str) -> None:
        scenario.touch_status(ScenarioStatus.COMPLETED)
        await self._store.save(scenario)
        self._emit(
            COMPLETED,
            scenario,
            severity=Severity.INFO,
            payload={"scenario_id": scenario.id, "reason": reason},
            reason=reason,
        )

    # -- events -------------------------------------------------------------

    def _emit(
        self,
        event_type: str,
        scenario: Scenario,
        *,
        severity: Severity,
        payload: dict[str, Any],
        reason: str | None = None,
    ) -> None:
        if self._event_sink is None:
            publish_event(
                event_type,
                severity=severity,
                component=COMPONENT,
                caused_by=scenario.id,
                payload=payload,
                reason=reason,
            )
            return
        self._event_sink(
            build_event(
                event_type,
                severity=severity,
                component=COMPONENT,
                caused_by=scenario.id,
                payload=payload,
                reason=reason,
            )
        )


def _as_dict(target: Any) -> dict[str, Any]:
    if isinstance(target, dict):
        return dict(target)
    return target.model_dump()


def _reports_recovery(status: dict[str, Any] | None) -> bool:
    """True when Chaos Mesh says the experiment has fully recovered.

    The CR stays in the cluster until it is deleted, so a truthful
    `AllRecovered: True` is the signal to remove it and let the next sweep
    deduce completion from absence.
    """
    if not status:
        return False
    return any(
        condition.get("type") == "AllRecovered" and condition.get("status") == "True"
        for condition in status.get("conditions") or []
    )


def _namespace(scenario: Scenario) -> str:
    target = K8sTarget.model_validate(scenario.target)
    return target.namespace
