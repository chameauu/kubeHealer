"""The agent graph, with the approval interrupt between `heal_plan` and `execute`.

Shape (CONTRACT D2):

    detect -> diagnose -> heal_plan
        +--(risk=low)----------------------------> execute
        +--(risk=high)--> approve (interrupt) ---> execute
    execute -> verify -> report -> END
    rejected at approve ------------------------> aborted -> END

The node bodies live in `nodes.py`; this module owns the wiring and the
interrupt. `interrupt()` is never inside a node that also performs work: the
`approve` node only reads the decision and records it, so re-running it on
resume is side-effect free.

LangGraph runs the graph synchronously (`invoke`) and the node bodies are
async (they do HTTP and polling), so each wrapper drives its coroutine to
completion. When `invoke` is called from inside a running event loop the
coroutine is driven in a worker thread instead of a nested loop.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Coroutine

from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

import nodes
from state import AgentState, new_state, utcnow

DiagnosisProvider = Callable[..., dict[str, Any]]
ExecuteFn = Callable[[AgentState], str]

HIGH_RISK = "high"

_STATE_KEYS = frozenset(AgentState.__annotations__)


# Config for one run: thread_id == scenario_id (CONTRACT D2, rule 2).
def thread_config(scenario_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": scenario_id}}


class NotPending(Exception):
    """The run is not waiting at the approval interrupt.

    The router maps this to `409`: a second resume would otherwise duplicate
    the approved side effect.
    """


def stub_diagnosis(**context: Any) -> dict[str, Any]:
    """Deterministic diagnosis used by tests and by the offline demo.

    The action targets the workload the scenario actually faulted, when the
    scenario carries one: a fixed name could never be right in a real cluster,
    and a remediation aimed at a deployment that does not exist fails for a
    reason that says nothing about the system. The demo string is the fallback
    for callers that pass no scenario (the unit tests do).
    """
    scenario = context.get("scenario") or {}
    target = scenario.get("target") or {}
    namespace = target.get("namespace")
    labels = target.get("label_selector") or {}
    name = labels.get("app") or labels.get("k8s-app")
    action_target = f"{namespace}/{name}" if namespace and name else "edge/api"
    return {
        "causal_chain": ["pod terminated", "deployment unavailable", "downstream 503s"],
        "recommended_action": "restart_deployment",
        "action_target": action_target,
        "risk": HIGH_RISK,
        "rationale": "the deployment has no ready replicas",
    }


def _run_async(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Run one node coroutine from the synchronous graph runtime."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def _clean(partial: dict[str, Any]) -> dict[str, Any]:
    """Keep only keys the frozen `AgentState` declares."""
    return {key: value for key, value in partial.items() if key in _STATE_KEYS}


def _detect(state: AgentState) -> dict[str, Any]:
    return _clean(_run_async(nodes.detect(state)))


def _diagnose(state: AgentState, provider: DiagnosisProvider | None) -> dict[str, Any]:
    return _clean(_run_async(nodes.diagnose(state, provider=provider)))


def _heal_plan(state: AgentState) -> dict[str, Any]:
    return _clean(_run_async(nodes.heal_plan(state)))


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


def _execute(state: AgentState, run_action: ExecuteFn | None) -> dict[str, Any]:
    if run_action is not None:
        return {
            "node": "execute",
            "action_result": run_action(state),
            "updated_at": utcnow(),
        }
    return _clean(_run_async(nodes.execute(state)))


def _verify(state: AgentState) -> dict[str, Any]:
    return _clean(_run_async(nodes.verify(state)))


def _report(state: AgentState) -> dict[str, Any]:
    return _clean(_run_async(nodes.report(state)))


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
    cannot suspend and `interrupt()` raises. A `None` provider means the
    `nodes.diagnosis_provider` seam; a `None` execute function means the real
    tool dispatch in `nodes.execute`.
    """
    builder: StateGraph = StateGraph(AgentState)
    builder.add_node("detect", _detect)
    builder.add_node("diagnose", lambda state: _diagnose(state, diagnosis_provider))
    builder.add_node("heal_plan", _heal_plan)
    builder.add_node("approve", _approve)
    builder.add_node("execute", lambda state: _execute(state, execute_fn))
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
