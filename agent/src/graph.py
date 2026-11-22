"""The agent graph, with the approval interrupt between `heal_plan` and `execute`.

Shape (CONTRACT D2):

    detect → diagnose → heal_plan ─┬─(risk=low)────────────────────────┐
                                   └─(risk=high)→ approve (interrupt) ─┤
                                                                       ▼
                                              execute → verify → report → END
                                                        rejected → aborted → END

`interrupt()` is never inside a node that also performs work: the `approve`
node only reads the decision and records it, so re-running it on resume is
side-effect free.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from state import AgentState, new_state, utcnow

DiagnosisProvider = Callable[..., dict[str, Any]]
ExecuteFn = Callable[[AgentState], str]

HIGH_RISK = "high"

# Config for one run: thread_id == scenario_id (CONTRACT D2, rule 2).
def thread_config(scenario_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": scenario_id}}


class NotPending(Exception):
    """The run is not waiting at the approval interrupt.

    The router maps this to `409`: a second resume would otherwise duplicate
    the approved side effect.
    """


def stub_diagnosis(**_context: Any) -> dict[str, Any]:
    """Deterministic diagnosis used by tests and by the offline demo."""
    return {
        "causal_chain": ["pod terminated", "deployment unavailable", "downstream 503s"],
        "recommended_action": "restart_deployment",
        "action_target": "edge/api",
        "risk": HIGH_RISK,
        "rationale": "the deployment has no ready replicas",
    }


def _detect(state: AgentState) -> dict[str, Any]:
    return {"node": "detect", "updated_at": utcnow()}


def _diagnose(state: AgentState, provider: DiagnosisProvider) -> dict[str, Any]:
    diagnosis = provider(
        scenario=state["scenario"],
        blast_radius=state["blast_radius"],
        catalog_entry=state["catalog_entry"],
        memory_context=state["memory_context"],
    )
    return {"node": "diagnose", "diagnosis": diagnosis, "updated_at": utcnow()}


def _heal_plan(state: AgentState) -> dict[str, Any]:
    risk = (state["diagnosis"] or {}).get("risk", HIGH_RISK)
    if risk == HIGH_RISK:
        return {"node": "heal_plan", "approval_status": "pending", "updated_at": utcnow()}
    return {"node": "heal_plan", "approval_status": "not_required", "updated_at": utcnow()}


def _approve(state: AgentState) -> dict[str, Any]:
    """The interrupt lives here and nowhere else."""
    decision = interrupt(
        {
            "type": "approval_required",
            "scenario_id": state["scenario_id"],
            "diagnosis": state["diagnosis"],
            "risk": (state["diagnosis"] or {}).get("risk"),
        }
    )
    if not isinstance(decision, dict):
        decision = {"approved": False, "reason": "invalid_resume_payload"}

    approved = bool(decision.get("approved"))
    return {
        "node": "approve",
        "approval_status": "approved" if approved else "rejected",
        "approval_reason": str(decision.get("reason") or ("operator" if approved else "policy")),
        "updated_at": utcnow(),
    }


def _route_after_heal_plan(state: AgentState) -> str:
    return "approve" if state["approval_status"] == "pending" else "execute"


def _route_after_approve(state: AgentState) -> str:
    return "execute" if state["approval_status"] == "approved" else "aborted"


def _execute(state: AgentState, run_action: ExecuteFn) -> dict[str, Any]:
    return {"node": "execute", "action_result": run_action(state), "updated_at": utcnow()}


def _verify(state: AgentState) -> dict[str, Any]:
    return {"node": "verify", "verify_result": "recovered", "updated_at": utcnow()}


def _report(state: AgentState) -> dict[str, Any]:
    started = datetime.fromisoformat(state["started_at"])
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    mttr = (datetime.now(timezone.utc) - started).total_seconds()
    return {"node": "report", "mttr_seconds": mttr, "updated_at": utcnow()}


def _aborted(state: AgentState) -> dict[str, Any]:
    return {"node": "aborted", "updated_at": utcnow()}


def build_graph(
    *,
    checkpointer: Any = None,
    diagnosis_provider: DiagnosisProvider | None = None,
    execute_fn: ExecuteFn | None = None,
) -> Any:
    """Compile the run graph.

    `checkpointer` is required for the approval interrupt; without it LangGraph
    cannot suspend and `interrupt()` raises.
    """
    provider = diagnosis_provider or stub_diagnosis
    run_action: ExecuteFn = execute_fn or (lambda _state: "ok")

    builder: StateGraph = StateGraph(AgentState)
    builder.add_node("detect", _detect)
    builder.add_node("diagnose", lambda state: _diagnose(state, provider))
    builder.add_node("heal_plan", _heal_plan)
    builder.add_node("approve", _approve)
    builder.add_node("execute", lambda state: _execute(state, run_action))
    builder.add_node("verify", _verify)
    builder.add_node("report", _report)
    builder.add_node("aborted", _aborted)

    builder.set_entry_point("detect")
    builder.add_edge("detect", "diagnose")
    builder.add_edge("diagnose", "heal_plan")
    builder.add_conditional_edges(
        "heal_plan", _route_after_heal_plan, {"approve": "approve", "execute": "execute"}
    )
    builder.add_conditional_edges(
        "approve", _route_after_approve, {"execute": "execute", "aborted": "aborted"}
    )
    builder.add_edge("execute", "verify")
    builder.add_edge("verify", "report")
    builder.add_edge("report", END)
    builder.add_edge("aborted", END)

    return builder.compile(checkpointer=checkpointer)


def start_run(graph: Any, scenario_id: str, scenario: dict[str, Any]) -> dict[str, Any]:
    """Invoke the graph from the initial state; returns at the interrupt."""
    return graph.invoke(new_state(scenario_id, scenario), thread_config(scenario_id))


def is_pending(graph: Any, scenario_id: str) -> bool:
    snapshot = graph.get_state(thread_config(scenario_id))
    return "approve" in (snapshot.next or ())


def resume_approval(
    graph: Any,
    scenario_id: str,
    *,
    approved: bool,
    reason: str,
) -> dict[str, Any]:
    """Resume a suspended run.

    Raises `NotPending` when the run is not at the interrupt — already executed,
    already aborted, or unknown — which the API returns as `409`.
    """
    if not is_pending(graph, scenario_id):
        raise NotPending(scenario_id)
    return graph.invoke(
        Command(resume={"approved": approved, "reason": reason}),
        thread_config(scenario_id),
    )
