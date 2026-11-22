"""`routers/runs.py` — the HTTP envelopes and the resume-once rule.

CONTRACT D2: approve/reject resume through `Command(resume=...)`, return 409
when the run is not at a pending interrupt and 404 when the id is unknown, so
a double resume cannot duplicate the approved side effect. The router also
reads `graph`, `store` and `memory` from module globals per request, so a test
or an operator can replace them without rebuilding the app.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from routers import runs as runs_router


def _run(
    scenario_id: str = "sc-1",
    status: str = "awaiting_approval",
    **extra: Any,
) -> dict[str, Any]:
    document = {
        "scenario_id": scenario_id,
        "scenario": {"id": scenario_id, "fault_type": "pod_kill"},
        "status": status,
        "node": "approve",
        "approval_status": "pending",
        "started_at": "2026-11-22T12:00:00+00:00",
        "updated_at": "2026-11-22T12:00:05+00:00",
    }
    document.update(extra)
    return document


class FakeStore:
    def __init__(self, documents: list[dict[str, Any]] | None = None) -> None:
        self.documents = {doc["scenario_id"]: dict(doc) for doc in (documents or [])}
        self.saved: list[dict[str, Any]] = []
        self.list_calls = 0

    async def save(self, run: dict[str, Any]) -> None:
        self.saved.append(dict(run))
        self.documents[run["scenario_id"]] = dict(run)

    async def get(self, scenario_id: str) -> dict[str, Any] | None:
        document = self.documents.get(scenario_id)
        return dict(document) if document is not None else None

    async def list(self) -> list[dict[str, Any]]:
        self.list_calls += 1
        return [dict(document) for document in self.documents.values()]


class FakeGraph:
    def __init__(self, pending: bool = True, final: dict[str, Any] | None = None) -> None:
        self.next: tuple[str, ...] = ("approve",) if pending else ()
        self.values = _run()
        self.final = final or _run(
            status="done",
            node="report",
            approval_status="approved",
        )
        self.invocations: list[Any] = []
        self.state_reads = 0

    def get_state(self, config: dict[str, Any]) -> SimpleNamespace:
        self.state_reads += 1
        return SimpleNamespace(next=self.next, values=dict(self.values))

    def invoke(self, payload: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
        self.invocations.append(payload)
        self.next = ()
        self.values = dict(self.final)
        return dict(self.values)


class FakeMemory:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {
            "incidents": [],
            "success_rate": 0.0,
            "confidence": 0,
            "mean_mttr_seconds": None,
        }
        self.recorded: list[dict[str, Any]] = []

    async def record(self, run: dict[str, Any]) -> None:
        self.recorded.append(run)

    async def recall(self, fault_type: str, limit: int = 20) -> dict[str, Any]:
        return dict(self.payload)


def _install(monkeypatch: Any, store: FakeStore, graph: FakeGraph) -> FakeMemory:
    memory = FakeMemory()
    monkeypatch.setattr(runs_router, "store", store)
    monkeypatch.setattr(runs_router, "graph", graph)
    monkeypatch.setattr(runs_router, "memory", memory)
    return memory


def test_list_envelope(client: Any, monkeypatch: Any) -> None:
    store = FakeStore([_run("sc-a"), _run("sc-b", status="done")])
    _install(monkeypatch, store, FakeGraph())

    response = client.get("/runs")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"runs", "total"}
    assert body["total"] == 2
    assert {run["scenario_id"] for run in body["runs"]} == {"sc-a", "sc-b"}


def test_unknown_run_is_404(client: Any, monkeypatch: Any) -> None:
    _install(monkeypatch, FakeStore(), FakeGraph())

    response = client.get("/runs/sc-missing")

    assert response.status_code == 404
    assert "sc-missing" in response.json()["detail"]


def test_approve_resumes_exactly_once(client: Any, monkeypatch: Any) -> None:
    store = FakeStore([_run("sc-1")])
    graph = FakeGraph(pending=True)
    _install(monkeypatch, store, graph)

    first = client.post("/runs/sc-1/approve")
    second = client.post("/runs/sc-1/approve")

    assert first.status_code == 200
    assert second.status_code == 409
    assert len(graph.invocations) == 1
    assert store.saved[-1]["approval_status"] == "approved"
    assert store.saved[-1]["status"] == "done"


def test_reject_aborts_the_run(client: Any, monkeypatch: Any) -> None:
    store = FakeStore([_run("sc-1")])
    final = _run("sc-1", status="aborted", node="aborted", approval_status="rejected")
    graph = FakeGraph(pending=True, final=final)
    _install(monkeypatch, store, graph)

    response = client.post("/runs/sc-1/reject")

    assert response.status_code == 200
    assert store.saved[-1]["status"] == "aborted"
    assert store.saved[-1]["approval_status"] == "rejected"


def test_approve_unknown_run_is_404(client: Any, monkeypatch: Any) -> None:
    graph = FakeGraph()
    _install(monkeypatch, FakeStore(), graph)

    response = client.post("/runs/sc-missing/approve")

    assert response.status_code == 404
    assert graph.invocations == []
    assert graph.state_reads == 0


def test_approve_after_execute_is_409(client: Any, monkeypatch: Any) -> None:
    store = FakeStore([_run("sc-1", status="done", node="report", approval_status="approved")])
    graph = FakeGraph(pending=False)
    _install(monkeypatch, store, graph)

    response = client.post("/runs/sc-1/approve")

    assert response.status_code == 409
    assert graph.invocations == []


def test_memory_payload_shape(client: Any, monkeypatch: Any) -> None:
    payload = {
        "incidents": [{"scenario_id": "sc-1", "outcome": "success"}],
        "success_rate": 1.0,
        "confidence": 100,
        "mean_mttr_seconds": 42.0,
    }
    memory = FakeMemory(payload)
    monkeypatch.setattr(runs_router, "store", FakeStore())
    monkeypatch.setattr(runs_router, "graph", FakeGraph())
    monkeypatch.setattr(runs_router, "memory", memory)

    response = client.get("/memory/pod_kill")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"incidents", "success_rate", "confidence", "mean_mttr_seconds"}
    assert body["confidence"] == 100


def test_router_reads_the_globals_per_request(client: Any, monkeypatch: Any) -> None:
    first = FakeStore([_run("sc-a")])
    second = FakeStore([_run("sc-b")])
    _install(monkeypatch, first, FakeGraph())
    monkeypatch.setattr(runs_router, "memory", FakeMemory())

    response_a = client.get("/runs")

    monkeypatch.setattr(runs_router, "store", second)
    response_b = client.get("/runs")

    assert [run["scenario_id"] for run in response_a.json()["runs"]] == ["sc-a"]
    assert [run["scenario_id"] for run in response_b.json()["runs"]] == ["sc-b"]
    assert first.list_calls == 1
    assert second.list_calls == 1
