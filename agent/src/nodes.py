"""Pipeline stages as async functions, one per graph node.

Every collaborator is a module global so a test can replace it without a
network, a cluster or a real clock: the tools, the diagnosis provider, the
event log, `now`, `sleep`, `memory` and `store`. No node may fabricate a field
for a source that did not answer, and no node may turn a blind source into a
success.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import providers
from config import config
from events import log_event
from memory import memory
from state import AgentState, utcnow
from store import store
from tools import (
    ToolError,
    get_blast_radius,
    get_catalog_entry,
    get_scenario,
    restart_deployment,
    scale_deployment,
    stop_scenario,
)

TERMINAL_SCENARIO_STATUSES = frozenset({"stopped", "completed"})
SUCCESS_VERIFY_RESULTS = frozenset({"recovered", "stopped", "completed"})
ALLOWED_ACTIONS = frozenset({"restart_deployment", "stop_scenario", "scale_deployment"})
POLL_INTERVAL_SECONDS = 5.0
# After the verify deadline the fault is stopped and polled briefly: a pod-kill
# CR never reports recovery, so the stop is what produces the terminal state.
VERIFY_GRACE_SECONDS = 15.0
DEFAULT_DOMAIN = "chaos_mesh"


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_provider(**_context: Any) -> Any:
    """Build the configured provider at call time (CONTRACT D7).

    Resolving here, not at import, means `LLM_MODE` is honoured and a test can
    still replace `nodes.diagnosis_provider` with a plain callable.
    """
    return providers.build_provider(config).diagnose(_context)


def _diagnosis_payload(state: AgentState) -> dict[str, Any]:
    """The canonical payload: every key always present, values as stored."""
    return {
        "scenario": state.get("scenario"),
        "blast_radius": state.get("blast_radius"),
        "catalog_entry": state.get("catalog_entry"),
        "memory_context": state.get("memory_context"),
    }


def _resolve_provider(
    provider: Any, payload: dict[str, Any]
) -> tuple[Callable[[], Any], str | None]:
    """Return a zero-argument invoker and the model it reports, if any.

    The seam callable takes keyword arguments (`**payload`); a provider
    instance is accepted too and is invoked with the payload dict it declares.
    """
    selected = provider if provider is not None else diagnosis_provider
    if selected is _default_provider:
        implementation = providers.build_provider(config)
        return (
            lambda: implementation.diagnose(payload),
            getattr(implementation, "model", None),
        )
    if not callable(selected) and hasattr(selected, "diagnose"):
        return (
            lambda: selected.diagnose(payload),
            getattr(selected, "model", None),
        )
    return lambda: selected(**payload), getattr(selected, "model", None)


# The seams. Values are read at call time, on every node invocation.
diagnosis_provider: Callable[..., Any] = _default_provider
now: Callable[[], datetime] = _default_now
sleep: Callable[[float], Any] = asyncio.sleep


async def _safe_source(awaitable: Any) -> dict[str, Any]:
    try:
        value = await awaitable
    except Exception as exc:
        log_event("agent.detect.source_failed", error=str(exc))
        return {}
    return value if isinstance(value, dict) else {}


async def detect(state: AgentState) -> dict[str, Any]:
    """Gather blast radius and catalogue concurrently; a blind source is `{}`."""
    scenario = state.get("scenario") or {}
    target = scenario.get("target") or {}
    labels = target.get("label_selector") or {}
    domain = scenario.get("domain") or DEFAULT_DOMAIN
    fault_type = scenario.get("fault_type") or ""

    blast_radius, catalog_entry = await asyncio.gather(
        _safe_source(get_blast_radius(scenario, labels)),
        _safe_source(get_catalog_entry(domain, fault_type)),
    )
    return {
        "node": "detect",
        "blast_radius": blast_radius,
        "catalog_entry": catalog_entry,
        "updated_at": utcnow(),
    }


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_diagnosis(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    if isinstance(raw, dict):
        return dict(raw), None
    if raw is None:
        return None, "provider returned no diagnosis"
    text = _strip_fences(raw if isinstance(raw, str) else str(raw))
    try:
        parsed = json.loads(text)
    except Exception as exc:
        return None, f"unparseable diagnosis: {exc}"
    if not isinstance(parsed, dict):
        return None, "diagnosis is not a JSON object"
    return parsed, None


def _fallback_diagnosis(
    scenario_id: str,
    failure: str,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed: stop the injection at low risk, never restart."""
    return {
        "causal_chain": [],
        "recommended_action": "stop_scenario",
        "action_target": scenario_id,
        "risk": "low",
        "rationale": "diagnosis unavailable; stopping the scenario is the safe action",
        "diagnosis_provenance": provenance,
        "parse_error": failure,
    }


async def diagnose(
    state: AgentState, *, provider: Callable[..., Any] | None = None
) -> dict[str, Any]:
    """Ask the provider for strict JSON; unparseable output fails closed."""
    payload = _diagnosis_payload(state)
    raw: Any = None
    failure: str | None = None
    model: str | None = None
    try:
        function, model = _resolve_provider(provider, payload)
        raw = function()
        if inspect.isawaitable(raw):
            raw = await raw
    except Exception as exc:
        failure = f"provider error: {exc}"
    provenance = {
        "mode": config.LLM_MODE,
        "model": model,
        "prompt_sha256": providers.prompt_sha256(payload),
    }
    parsed, parse_error = _parse_diagnosis(raw)
    if parsed is None:
        diagnosis = _fallback_diagnosis(
            str(state.get("scenario_id") or ""),
            failure or parse_error or "unknown",
            provenance,
        )
    else:
        diagnosis = parsed
        diagnosis["diagnosis_provenance"] = provenance
    return {"node": "diagnose", "diagnosis": diagnosis, "updated_at": utcnow()}


async def heal_plan(state: AgentState) -> dict[str, Any]:
    """High and medium risk park for approval; low risk executes directly."""
    scenario_id = state.get("scenario_id")
    diagnosis = state.get("diagnosis") or {}
    risk = str(diagnosis.get("risk") or "high").lower()
    requires_approval = risk in {"high", "medium"}
    status = "pending" if requires_approval else "not_required"
    log_event(
        f"kubeheal|{scenario_id}|heal_plan|{'approval_required' if requires_approval else 'auto'}",
        scenario_id=scenario_id,
        risk=risk,
        approval_status=status,
        action=diagnosis.get("recommended_action"),
    )
    return {
        "node": "heal_plan",
        "approval_status": status,
        "updated_at": utcnow(),
    }


def _split_target(target: str, state: AgentState) -> tuple[str | None, str | None]:
    if "/" in target:
        namespace, name = target.split("/", 1)
        return namespace or None, name or None
    scenario_target = (state.get("scenario") or {}).get("target") or {}
    return scenario_target.get("namespace"), target or None


async def execute(state: AgentState) -> dict[str, Any]:
    """Run exactly one approved action; anything unknown is contained by stop."""
    scenario_id = str(state.get("scenario_id") or "")
    diagnosis = state.get("diagnosis") or {}
    action = diagnosis.get("recommended_action")
    approval = state.get("approval_status")

    if approval == "rejected":
        return {
            "node": "execute",
            "action_result": "refused: approval was rejected",
            "updated_at": utcnow(),
        }
    if approval not in {"approved", "not_required"}:
        return {
            "node": "execute",
            "action_result": f"refused: approval_status={approval!r} does not authorize",
            "updated_at": utcnow(),
        }

    try:
        if action == "stop_scenario":
            await stop_scenario(scenario_id)
            result = f"stopped scenario {scenario_id}"
        elif action == "restart_deployment":
            namespace, name = _split_target(str(diagnosis.get("action_target") or ""), state)
            if not namespace or not name:
                result = "refused: restart target is incomplete"
            else:
                await restart_deployment(namespace, name)
                result = f"restarted deployment {namespace}/{name}"
        elif action == "scale_deployment":
            namespace, name = _split_target(str(diagnosis.get("action_target") or ""), state)
            replicas = int(diagnosis.get("replicas") or 1)
            if not namespace or not name:
                result = "refused: scale target is incomplete"
            else:
                await scale_deployment(namespace, name, replicas)
                result = f"scaled deployment {namespace}/{name} to {replicas}"
        else:
            # Unknown write: refuse it, and contain by stopping the injection.
            await stop_scenario(scenario_id)
            result = (
                f"refused unknown action {action!r}; stopped scenario {scenario_id} "
                "as containment"
            )
    except ToolError as exc:
        result = f"failed: {exc}"

    return {"node": "execute", "action_result": result, "updated_at": utcnow()}


def _mttr_seconds(state: AgentState, scenario: dict[str, Any]) -> float | None:
    scenario_doc = state.get("scenario") or {}
    started_raw = scenario_doc.get("started_at") or state.get("started_at")
    ended_raw = scenario.get("ended_at")
    if not started_raw or not ended_raw:
        return None
    try:
        started = datetime.fromisoformat(str(started_raw))
        ended = datetime.fromisoformat(str(ended_raw))
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=timezone.utc)
    return (ended - started).total_seconds()


def _describe_error(exc: Exception) -> str:
    """Name the exception when its message is empty — httpx timeouts are."""
    text = str(exc).strip()
    return text or exc.__class__.__name__


def _failed_verify(reason: str) -> dict[str, Any]:
    log_event("agent.verify.failed", reason=reason)
    return {
        "node": "verify",
        "verify_result": "failed",
        "mttr_seconds": None,
        "updated_at": now().isoformat(),
    }


async def verify(state: AgentState) -> dict[str, Any]:
    """Poll the backend up to the deadline; a timeout while running fails."""
    scenario_id = str(state.get("scenario_id") or "")
    deadline = now() + timedelta(seconds=int(config.VERIFY_TIMEOUT_SECONDS))
    last_read_error: str | None = None
    status = ""
    stop_requested = False
    while True:
        try:
            scenario = await get_scenario(scenario_id)
            last_read_error = None
        except Exception as exc:
            # One failed read is not evidence that the system did not recover:
            # the backend can blip while the fault is being cleaned up, and
            # treating that as terminal turned a recovering incident into a
            # failed run. Keep polling and only report it if it is all we saw.
            scenario = None
            last_read_error = _describe_error(exc)
        if scenario is not None:
            status = str((scenario or {}).get("status") or "")
        if scenario is not None and status in TERMINAL_SCENARIO_STATUSES:
            mttr = _mttr_seconds(state, scenario or {})
            return {
                "node": "verify",
                "verify_result": "recovered",
                "mttr_seconds": mttr,
                "updated_at": now().isoformat(),
            }
        if now() >= deadline:
            if not stop_requested and status == "running":
                # The fault is ours to end. A pod-kill has nothing to restore,
                # so the backend never reports recovery; polling longer would
                # turn a recoverable incident into a failed run.
                stop_requested = True
                deadline = now() + timedelta(seconds=int(VERIFY_GRACE_SECONDS))
                try:
                    await stop_scenario(scenario_id)
                except Exception as exc:
                    last_read_error = _describe_error(exc)
                continue
            if last_read_error is not None and not status:
                return _failed_verify(f"scenario unreadable: {last_read_error}")
            return _failed_verify(f"deadline reached while {status or 'unknown'}")
        await sleep(POLL_INTERVAL_SECONDS)


async def report(state: AgentState) -> dict[str, Any]:
    """Write the incident row and close the run; the projection is `done`."""
    success = state.get("verify_result") in SUCCESS_VERIFY_RESULTS
    outcome = "success" if success else "failed"
    status = "done" if success else "error"
    mttr = state.get("mttr_seconds") if success else None
    scenario = state.get("scenario") or {}
    timestamp = now().isoformat()

    document: dict[str, Any] = {
        "scenario_id": state.get("scenario_id"),
        "scenario": scenario,
        "fault_type": scenario.get("fault_type") or "unknown",
        "outcome": outcome,
        "status": status,
        "node": "report",
        "approval_status": state.get("approval_status"),
        "verify_result": state.get("verify_result"),
        "action_result": state.get("action_result"),
        "mttr_seconds": mttr,
        "started_at": state.get("started_at"),
        "ended_at": timestamp,
        "updated_at": timestamp,
    }
    await memory.record(document)
    await store.save(document)

    return {
        "node": "report",
        "mttr_seconds": mttr,
        "updated_at": timestamp,
        "status": status,
    }
