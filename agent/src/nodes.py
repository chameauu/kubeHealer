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
import policy
from config import config
from events import log_event
from memory import MAX_INCIDENTS, memory
from state import AgentState, utcnow
from store import store
from tools import (
    ToolError,
    find_deployments,
    get_blast_radius,
    get_catalog_entry,
    get_scenario,
    get_workload_health,
    restart_deployment,
    scale_deployment,
    stop_scenario,
)

SUCCESS_VERIFY_RESULTS = frozenset({"recovered", "stopped", "completed"})
ALLOWED_ACTIONS = frozenset({"restart_deployment", "stop_scenario", "scale_deployment"})
POLL_INTERVAL_SECONDS = 5.0
DEFAULT_DOMAIN = "chaos_mesh"
# Faults that keep degrading the workload for as long as their scenario runs.
# Healthy replicas do not mean recovery while one of these is still active.
DURABLE_FAULT_TYPES = frozenset({"network_latency", "packet_loss", "io_delay"})

# The only fields memory may contribute to a prompt. The stored incident is a
# whole report, scenario document included; the prompt gets the projection and
# nothing else, so the payload stays small and the cassette hash stays stable.
MEMORY_INCIDENT_KEYS = (
    "scenario_id",
    "fault_type",
    "outcome",
    "risk",
    "approval_status",
    "mttr_seconds",
    "action",
)


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_provider(**_context: Any) -> Any:
    """Build the configured provider at call time (CONTRACT D7).

    Resolving here, not at import, means `LLM_MODE` is honoured and a test can
    still replace `nodes.diagnosis_provider` with a plain callable.
    """
    return providers.build_provider(config).diagnose(_context)


def _diagnosis_payload(
    state: AgentState, memory_context: dict[str, Any] | None
) -> dict[str, Any]:
    """The canonical payload: every key always present, values as stored."""
    return {
        "scenario": state.get("scenario"),
        "blast_radius": state.get("blast_radius"),
        "catalog_entry": state.get("catalog_entry"),
        "memory_context": memory_context,
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


def _bounded_memory(result: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce one memory lookup to what a prompt may carry, or `None` if empty.

    Each incident keeps the `MEMORY_INCIDENT_KEYS` (the action it recorded
    included), the list keeps at most `MAX_INCIDENTS`, and the aggregates travel
    beside it. `by_action` is the store's own per-action view of that same
    bounded incident set, passed through unchanged; there is no second cap. The
    answer is `None` rather than `{}` when memory is empty, so a first run
    hashes exactly as it did before incident memory reached the diagnosis.
    """
    if not isinstance(result, dict):
        return None
    raw = result.get("incidents")
    if not isinstance(raw, list):
        return None
    summaries = [
        {key: incident.get(key) for key in MEMORY_INCIDENT_KEYS}
        for incident in raw[:MAX_INCIDENTS]
        if isinstance(incident, dict)
    ]
    if not summaries:
        return None
    by_action = result.get("by_action")
    return {
        "incidents": summaries,
        "success_rate": result.get("success_rate"),
        "confidence": result.get("confidence"),
        "mean_mttr_seconds": result.get("mean_mttr_seconds"),
        "by_action": by_action if isinstance(by_action, dict) else {},
    }


async def diagnose(
    state: AgentState, *, provider: Callable[..., Any] | None = None
) -> dict[str, Any]:
    """Ask the provider for strict JSON; unparseable output fails closed."""
    fault_type = (state.get("scenario") or {}).get("fault_type")
    memory_context: dict[str, Any] | None = None
    if fault_type:
        try:
            memory_context = _bounded_memory(await memory.recall(str(fault_type)))
        except Exception as exc:
            # Memory is context: a broken store must not fail the diagnosis.
            log_event("agent.memory.unavailable", error=str(exc))
    payload = _diagnosis_payload(state, memory_context)
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
        "mode": providers.selected_mode(config),
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
    return {
        "node": "diagnose",
        "memory_context": memory_context,
        "diagnosis": diagnosis,
        "updated_at": utcnow(),
    }


async def heal_plan(state: AgentState) -> dict[str, Any]:
    """The policy table decides the risk and the gate; the diagnosis only proposes."""
    scenario_id = state.get("scenario_id")
    diagnosis = state.get("diagnosis") or {}
    action = diagnosis.get("recommended_action")
    proposed = diagnosis.get("risk")
    risk = policy.effective_risk(action, proposed)
    approval_required = policy.requires_approval(risk)
    status = "pending" if approval_required else "not_required"
    risk_policy = policy.explain(action, proposed)
    log_event(
        f"kubeheal|{scenario_id}|heal_plan|"
        f"{'approval_required' if approval_required else 'auto'}",
        scenario_id=scenario_id,
        # The effective risk is what the human is asked to accept, and what
        # `execute` may act on: the proposal is recorded beside it, never instead.
        risk=risk,
        proposed_risk=risk_policy["proposed_risk"],
        risk_raised=risk_policy["raised"],
        approval_status=status,
        action=action,
    )
    return {
        "node": "heal_plan",
        "approval_status": status,
        "risk": risk,
        "risk_policy": risk_policy,
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


def _parse_timestamp(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _mttr_seconds(
    state: AgentState, scenario: dict[str, Any] | None
) -> float | None:
    """Scenario start to its recorded end, else to now; `None` if unparseable."""
    document = scenario or {}
    scenario_doc = state.get("scenario") or {}
    started = _parse_timestamp(
        document.get("started_at") or scenario_doc.get("started_at")
    )
    if started is None:
        return None
    ended = _parse_timestamp(document.get("ended_at")) or now()
    return round((ended - started).total_seconds(), 2)


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


def _parse_action_target(diagnosis: dict[str, Any] | None) -> tuple[str, str] | None:
    """Split a diagnosis `action_target` (`<namespace>/<deployment>`), if it is one."""
    text = str((diagnosis or {}).get("action_target") or "").strip()
    parts = text.split("/")
    if len(parts) != 2:
        return None
    namespace, deployment = (part.strip() for part in parts)
    if not namespace or not deployment:
        return None
    return namespace, deployment


def _selector_pairs(selector: Any) -> list[tuple[str, str]]:
    if not isinstance(selector, dict):
        return []
    return sorted((str(key), str(value)) for key, value in selector.items())


def _selector_text(selector: Any) -> str:
    pairs = _selector_pairs(selector)
    return ",".join(f"{key}={value}" for key, value in pairs) or "(all deployments)"


async def _resolve_workload(
    state: AgentState,
) -> tuple[tuple[str, str] | None, str | None]:
    """Resolve the faulted workload from the scenario, never from the model.

    The scenario's namespace and label selector are the anchor: the candidates
    are the deployments whose pod template carries every selector pair. A
    parseable `action_target` may break a tie between candidates, and any other
    parseable target is a scope violation. A target that is not
    `<namespace>/<name>` (a scenario id, empty, garbage) is ignored, so the
    fail-closed `stop_scenario` fallback still verifies.
    """
    scenario = state.get("scenario") or {}
    target = scenario.get("target") or {}
    namespace = str(target.get("namespace") or "").strip()
    selector = target.get("label_selector") or {}
    if not namespace:
        return None, "no namespace in the scenario target"
    try:
        candidates = await find_deployments(namespace, dict(selector))
    except Exception as exc:
        return None, _describe_error(exc)
    named = _parse_action_target(state.get("diagnosis"))
    if not candidates:
        return None, f"no deployment matches {namespace} {_selector_text(selector)}"
    if len(candidates) == 1:
        resolved = (namespace, candidates[0])
    elif named is not None and named[0] == namespace and named[1] in candidates:
        resolved = named
    else:
        return None, (
            f"ambiguous workload: {namespace} {_selector_text(selector)} "
            f"matches {', '.join(candidates)}"
        )
    if named is not None and named != resolved:
        return None, (
            f"action_target {named[0]}/{named[1]} is outside the faulted workload "
            f"{resolved[0]}/{resolved[1]}"
        )
    return resolved, None


def _workload_healthy(health: dict[str, Any]) -> bool:
    """The workload is back only when replicas are actually serving."""
    desired = int(health.get("desired") or 0)
    available = int(health.get("available") or 0)
    ready = int(health.get("ready") or 0)
    return desired > 0 and (available >= desired or ready >= desired)


async def verify(state: AgentState) -> dict[str, Any]:
    """Poll the faulted workload; recovery is the workload's, never our own write.

    The workload is resolved from the scenario, so the model cannot choose what
    "recovered" is measured against. A durable fault that is still running is
    not recovery, however healthy the replicas look.
    """
    target, failure = await _resolve_workload(state)
    if target is None:
        return _failed_verify(failure or "no workload target to verify")
    namespace, deployment = target
    scenario_id = str(state.get("scenario_id") or "")
    scenario_doc = state.get("scenario") or {}
    timeout = int(config.VERIFY_TIMEOUT_SECONDS)
    deadline = now() + timedelta(seconds=timeout)
    last_scenario_error: str | None = None
    last_health_error: str | None = None
    scenario: dict[str, Any] | None = None
    health: dict[str, Any] | None = None
    while True:
        try:
            scenario = await get_scenario(scenario_id)
            last_scenario_error = None
        except Exception as exc:
            # One failed read is not evidence that the system did not recover:
            # the backend can blip while the fault is being cleaned up, and
            # treating that as terminal turned a recovering incident into a
            # failed run. Keep polling and only report it if it is all we saw.
            scenario = None
            last_scenario_error = _describe_error(exc)
        try:
            health = await get_workload_health(namespace, deployment)
            last_health_error = None
        except Exception as exc:
            health = None
            last_health_error = _describe_error(exc)
        effective = scenario or {}
        fault_type = str(
            effective.get("fault_type") or scenario_doc.get("fault_type") or ""
        )
        status = str(effective.get("status") or scenario_doc.get("status") or "")
        durable_active = fault_type in DURABLE_FAULT_TYPES and status == "running"
        if health is not None and _workload_healthy(health) and not durable_active:
            return {
                "node": "verify",
                "verify_result": "recovered",
                "mttr_seconds": _mttr_seconds(state, scenario),
                "updated_at": now().isoformat(),
            }
        if now() >= deadline:
            if durable_active:
                return _failed_verify(f"durable fault still active: {fault_type}")
            desired = int((health or {}).get("desired") or 0)
            available = int((health or {}).get("available") or 0)
            reason = (
                f"workload not ready: {available}/{desired} replicas after {timeout}s"
            )
            if last_health_error is not None:
                reason += f" (last health error: {last_health_error})"
            if last_scenario_error is not None:
                reason += f" (last scenario error: {last_scenario_error})"
            return _failed_verify(reason)
        await sleep(POLL_INTERVAL_SECONDS)


async def report(state: AgentState) -> dict[str, Any]:
    """Write the incident row and close the run; the projection is `done`."""
    success = state.get("verify_result") in SUCCESS_VERIFY_RESULTS
    outcome = "success" if success else "failed"
    status = "done" if success else "error"
    mttr = state.get("mttr_seconds") if success else None
    scenario = state.get("scenario") or {}
    diagnosis = state.get("diagnosis") or {}
    proposed_action = diagnosis.get("recommended_action")
    # Record which action ran, not just what it did. Only the diagnosis can
    # name it: no diagnosis means `None`, never a string guessed from
    # `action_result`, and never a default action.
    action = (
        proposed_action
        if isinstance(proposed_action, str) and proposed_action
        else None
    )
    timestamp = now().isoformat()

    document: dict[str, Any] = {
        "scenario_id": state.get("scenario_id"),
        "scenario": scenario,
        "fault_type": scenario.get("fault_type") or "unknown",
        "outcome": outcome,
        "status": status,
        "node": "report",
        "approval_status": state.get("approval_status"),
        "risk": state.get("risk"),
        "risk_policy": state.get("risk_policy"),
        "verify_result": state.get("verify_result"),
        "action": action,
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
