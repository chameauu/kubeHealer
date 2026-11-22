"""Runs, approvals and incident memory.

`graph`, `store` and `memory` are module globals read on every request, so a
test or an operator can replace them without rebuilding the app.

Ordering (CONTRACT D2, rule 1): `resume_approval` makes the LangGraph
checkpoint commit; only after it returns does this router write the
`agent_runs` projection. The checkpoint is the source of truth, the projection
is the read model.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from graph import NotPending, is_pending, resume_approval, start_run, thread_config
from memory import memory
from state import utcnow
from store import store

TERMINAL_STATUSES = frozenset({"done", "aborted", "error"})

# Replaced by the lifespan (or by a test); read per request, never captured.
graph: Any = None

router = APIRouter()


class StartRunRequest(BaseModel):
    """A scenario to diagnose and, if needed, remediate."""

    scenario: dict[str, Any] = Field(default_factory=dict)
    scenario_id: str | None = None


def _derive_status(document: dict[str, Any]) -> str:
    status = document.get("status")
    if status in TERMINAL_STATUSES:
        return str(status)
    node = document.get("node")
    if node == "aborted":
        return "aborted"
    if node == "report":
        return "done"
    if node == "error":
        return "error"
    if node == "approve":
        return "awaiting_approval"
    return "running"


def _not_found(scenario_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"run {scenario_id} not found")


def _not_pending(scenario_id: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=f"run {scenario_id} is not waiting for approval",
    )


@router.get("/runs")
async def list_runs(status: str | None = None) -> dict[str, Any]:
    runs = await store.list(status) if status else await store.list()
    return {"runs": runs, "total": len(runs)}


@router.get("/runs/{scenario_id}")
async def get_run(scenario_id: str) -> dict[str, Any]:
    document = await store.get(scenario_id)
    if document is None:
        raise _not_found(scenario_id)
    return document


async def _resume(
    scenario_id: str, *, approved: bool, reason: str
) -> dict[str, Any]:
    active_graph = graph
    document = await store.get(scenario_id)
    if document is None:
        raise _not_found(scenario_id)
    if active_graph is None:
        raise HTTPException(status_code=503, detail="agent graph is not ready")
    if not is_pending(active_graph, scenario_id):
        raise _not_pending(scenario_id)
    try:
        final = await asyncio.to_thread(
            resume_approval,
            active_graph,
            scenario_id,
            approved=approved,
            reason=reason,
        )
    except NotPending:
        raise _not_pending(scenario_id)
    merged = {**document, **final}
    merged["status"] = _derive_status(merged)
    merged["updated_at"] = final.get("updated_at") or utcnow()
    # The checkpoint above has committed; now the projection may be written.
    await store.save(merged)
    return merged


@router.post("/runs/{scenario_id}/approve")
async def approve_run(scenario_id: str) -> dict[str, Any]:
    return await _resume(scenario_id, approved=True, reason="operator")


@router.post("/runs/{scenario_id}/reject")
async def reject_run(scenario_id: str) -> dict[str, Any]:
    return await _resume(scenario_id, approved=False, reason="operator")


@router.get("/memory/{fault_type}")
async def recall_memory(fault_type: str) -> dict[str, Any]:
    return await memory.recall(fault_type)


async def _run_in_background(scenario_id: str, scenario: dict[str, Any]) -> None:
    """Start a run, then project the state it stopped at."""
    active_graph = graph
    if active_graph is None:
        return
    started_at = utcnow()
    projection: dict[str, Any] = {
        "scenario_id": scenario_id,
        "scenario": scenario,
        "status": "running",
        "node": "detect",
        "approval_status": "not_required",
        "started_at": started_at,
        "updated_at": started_at,
    }
    await store.save(projection)
    try:
        await asyncio.to_thread(start_run, active_graph, scenario_id, scenario)
    except Exception as exc:
        await store.save(
            {
                **projection,
                "status": "error",
                "node": "error",
                "error": str(exc),
                "updated_at": utcnow(),
            }
        )
        return

    snapshot = active_graph.get_state(thread_config(scenario_id))
    values = dict(snapshot.values or {})
    pending = "approve" in (snapshot.next or ())
    document = {**values, "scenario_id": scenario_id}
    document.setdefault("scenario", scenario)
    document["status"] = "awaiting_approval" if pending else _derive_status(document)
    document["updated_at"] = values.get("updated_at") or utcnow()
    await store.save(document)


@router.post("/runs", status_code=202)
async def create_run(
    payload: StartRunRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    scenario_id = payload.scenario_id or str(payload.scenario.get("id") or "")
    if not scenario_id:
        raise HTTPException(
            status_code=422, detail="scenario_id or scenario.id is required"
        )
    background_tasks.add_task(
        _run_in_background, scenario_id, dict(payload.scenario)
    )
    return {"scenario_id": scenario_id, "status": "running"}
