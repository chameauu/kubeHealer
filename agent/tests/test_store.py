"""`store.py` — the `agent_runs` projection and checkpoint-aware reconciliation.

CONTRACT D2 fixes two rules these tests enforce:

- the checkpointer commits before the projection is written (rule 1);
- `reconcile_interrupted()` never blanket-fails a pending interrupt: a thread
  with a pending interrupt stays approvable, everything else fails closed.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any

from store import AgentRunStore


def _doc(
    scenario_id: str = "sc-1",
    status: str = "done",
    node: str = "report",
    **extra: Any,
) -> dict[str, Any]:
    document = {
        "scenario_id": scenario_id,
        "scenario": {"id": scenario_id, "fault_type": "pod_kill"},
        "status": status,
        "node": node,
        "approval_status": "not_required",
        "started_at": "2026-11-22T12:00:00+00:00",
        "updated_at": "2026-11-22T12:01:00+00:00",
        "mttr_seconds": 60.0,
    }
    document.update(extra)
    return document


class _Checkpoint:
    """Stands in for `graph.get_state` at the reconciliation boundary.

    The boolean answer is exactly what `graph.is_pending` returns; the recorded
    `next` tuple keeps the "execute was in flight" case explicit.
    """

    def __init__(self, next_nodes: tuple[str, ...]) -> None:
        self.next_nodes = next_nodes
        self.seen: list[str] = []

    def is_pending(self, scenario_id: str) -> bool:
        self.seen.append(scenario_id)
        return "approve" in self.next_nodes


class _NoCheckpoint:
    """A thread that was never written: LangGraph raises for it."""

    def is_pending(self, scenario_id: str) -> bool:
        raise KeyError(scenario_id)


async def test_save_get_and_list_round_trip(run_store: AgentRunStore) -> None:
    document = _doc()

    await run_store.save(document)

    assert await run_store.get("sc-1") == document
    listed = await run_store.list()
    assert [item["scenario_id"] for item in listed] == ["sc-1"]


async def test_save_is_an_upsert(run_store: AgentRunStore) -> None:
    await run_store.save(_doc(status="running", node="diagnose"))
    await run_store.save(_doc(status="done", node="report"))

    stored = await run_store.get("sc-1")

    assert stored is not None
    assert stored["status"] == "done"
    assert stored["node"] == "report"
    assert len(await run_store.list()) == 1


async def test_run_document_survives_reopening_the_database(tmp_path: Any) -> None:
    path = str(tmp_path / "memory.db")
    document = _doc()
    first = AgentRunStore(path)
    await first.save(document)
    await first.close()

    second = AgentRunStore(path)
    try:
        assert await second.get("sc-1") == document
    finally:
        await second.close()


def _table_columns(path: str) -> list[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("PRAGMA table_info(agent_runs)").fetchall()
    return [row[1] for row in rows]


async def test_projection_table_holds_the_read_model(
    run_store: AgentRunStore, tmp_path: Any
) -> None:
    await run_store.save(_doc())

    columns = _table_columns(str(tmp_path / "memory.db"))

    assert columns[:4] == ["scenario_id", "status", "document", "updated_at"]


class _RecordingStore:
    def __init__(self, order: list[str], run: dict[str, Any]) -> None:
        self.order = order
        self.run = run
        self.saved: list[dict[str, Any]] = []

    async def save(self, run: dict[str, Any]) -> None:
        self.order.append("projection")
        self.saved.append(run)

    async def get(self, scenario_id: str) -> dict[str, Any] | None:
        return dict(self.run) if scenario_id == self.run["scenario_id"] else None

    async def list(self) -> list[dict[str, Any]]:
        return [dict(self.run)]


class _RecordingGraph:
    """A checkpoint whose `get_state` is observed during the resume flow."""

    def __init__(self, order: list[str], run: dict[str, Any]) -> None:
        self.order = order
        self.run = run
        self.next: tuple[str, ...] = ("approve",)

    def get_state(self, config: dict[str, Any]) -> SimpleNamespace:
        self.order.append("checkpoint")
        return SimpleNamespace(next=self.next, values=dict(self.run))

    def invoke(self, payload: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
        self.order.append("invoke")
        self.next = ()
        self.run = {
            **self.run,
            "status": "done",
            "node": "report",
            "approval_status": "approved",
        }
        return dict(self.run)


def test_projection_is_written_after_the_checkpoint(
    client: Any,
    monkeypatch: Any,
) -> None:
    """D2 rule 1: the checkpoint observation precedes the projection write.

    The ordering is observed on the real resume path (`POST /approve`), where
    the graph is consulted for the checkpoint and the store records the
    read model afterwards. A store that wrote first would invert the order.
    """
    from routers import runs as runs_router

    order: list[str] = []
    run = _doc(status="awaiting_approval", node="approve", approval_status="pending")
    graph = _RecordingGraph(order, run)
    store = _RecordingStore(order, run)
    monkeypatch.setattr(runs_router, "graph", graph)
    monkeypatch.setattr(runs_router, "store", store)
    monkeypatch.setattr(runs_router, "memory", SimpleNamespace(record=_noop, recall=_noop))

    response = client.post("/runs/sc-1/approve")

    assert response.status_code == 200
    assert "checkpoint" in order
    assert "projection" in order
    assert order.index("checkpoint") < order.index("projection")
    assert store.saved[-1]["approval_status"] == "approved"


async def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


async def test_reconcile_keeps_a_pending_interrupt(run_store: AgentRunStore) -> None:
    pending = _doc(
        status="awaiting_approval",
        node="approve",
        approval_status="pending",
    )
    await run_store.save(pending)
    checkpoint = _Checkpoint(("approve",))

    changed = await run_store.reconcile_interrupted(checkpoint.is_pending)

    assert changed == []
    assert await run_store.get("sc-1") == pending
    assert checkpoint.seen == ["sc-1"]


async def test_reconcile_fails_a_run_without_a_checkpoint(run_store: AgentRunStore) -> None:
    await run_store.save(_doc(status="running", node="diagnose"))

    changed = await run_store.reconcile_interrupted(_NoCheckpoint().is_pending)

    stored = await run_store.get("sc-1")
    assert stored is not None
    assert stored["status"] == "error"
    assert "manual review required" in (stored.get("error") or "")
    assert changed == ["sc-1"]


async def test_reconcile_fails_an_execute_that_was_in_flight(run_store: AgentRunStore) -> None:
    await run_store.save(_doc(status="executing", node="execute"))
    checkpoint = _Checkpoint(("execute",))

    changed = await run_store.reconcile_interrupted(checkpoint.is_pending)

    stored = await run_store.get("sc-1")
    assert stored is not None
    assert stored["status"] == "error"
    assert "manual review required" in (stored.get("error") or "")
    assert changed == ["sc-1"]
    assert checkpoint.seen == ["sc-1"]


async def test_reconcile_leaves_terminal_runs_alone(run_store: AgentRunStore) -> None:
    terminal = [
        _doc("sc-done", status="done"),
        _doc("sc-aborted", status="aborted", node="aborted"),
        _doc("sc-error", status="error", node="error"),
    ]
    for document in terminal:
        await run_store.save(document)

    changed = await run_store.reconcile_interrupted(_Checkpoint(()).is_pending)

    assert changed == []
    for document in terminal:
        assert await run_store.get(document["scenario_id"]) == document


async def test_reconcile_returns_exactly_the_runs_it_changed(run_store: AgentRunStore) -> None:
    await run_store.save(_doc("sc-pending", status="awaiting_approval", node="approve"))
    await run_store.save(_doc("sc-running", status="running", node="diagnose"))
    await run_store.save(_doc("sc-done", status="done"))

    changed = await run_store.reconcile_interrupted(
        lambda scenario_id: scenario_id == "sc-pending"
    )

    assert changed == ["sc-running"]
