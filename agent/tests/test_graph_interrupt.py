"""The three contract tests that decide whether D2 holds (CONTRACT D2, timebox gate):

1. crash mid-interrupt → restart → approve → the graph resumes exactly once;
2. the timeout sweep produces `aborted`;
3. an approve after `execute` started is refused (the API maps it to `409`).
"""

import sqlite3
from datetime import datetime, timedelta

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from graph import NotPending, build_graph, resume_approval, start_run, thread_config
from sweeper import abort_if_expired, is_expired

SCENARIO = {
    "id": "sc-1",
    "fault_type": "pod_kill",
    "target_namespace": "edge",
    "status": "running",
}


def _make(path, *, risk: str = "high", calls: list[str] | None = None):
    calls = [] if calls is None else calls

    def execute(_state) -> str:
        calls.append("execute")
        return "restarted"

    graph = build_graph(
        checkpointer=SqliteSaver(sqlite3.connect(str(path), check_same_thread=False)),
        diagnosis_provider=lambda **_: {
            "causal_chain": ["pod terminated", "no ready replicas"],
            "recommended_action": "restart_deployment",
            "action_target": "edge/api",
            "risk": risk,
            "rationale": "test",
        },
        execute_fn=execute,
    )
    return graph, calls


def test_crash_mid_interrupt_then_approve_resumes_exactly_once(tmp_path):
    db = tmp_path / "checkpoints.db"
    first, calls = _make(db)

    paused = start_run(first, "sc-1", SCENARIO)

    assert paused["__interrupt__"], "a high-risk run must suspend before executing"
    assert first.get_state(thread_config("sc-1")).next == ("approve",)
    assert calls == [], "nothing may execute before approval"

    # Simulated crash: the process that owned `first` is gone. A new one opens
    # the same checkpoint file and finds the run still approvable.
    second, _ = _make(db, calls=calls)

    done = resume_approval(second, "sc-1", approved=True, reason="operator")

    assert done["approval_status"] == "approved"
    assert done["node"] == "report"
    assert calls == ["execute"]

    # A second resume must not duplicate the side effect.
    with pytest.raises(NotPending):
        resume_approval(second, "sc-1", approved=True, reason="operator")
    assert calls == ["execute"]


def test_timeout_sweep_aborts_and_never_executes(tmp_path):
    db = tmp_path / "checkpoints.db"
    graph, calls = _make(db)
    start_run(graph, "sc-1", SCENARIO)

    updated_at = graph.get_state(thread_config("sc-1")).values["updated_at"]
    later = datetime.fromisoformat(updated_at) + timedelta(seconds=301)

    assert abort_if_expired(graph, "sc-1", updated_at, now=later) is True

    final = graph.get_state(thread_config("sc-1")).values
    assert final["node"] == "aborted"
    assert final["approval_status"] == "rejected"
    assert final["approval_reason"] == "timeout"
    assert calls == [], "a timeout may only ever abort"


def test_approve_after_execute_started_is_rejected(tmp_path):
    db = tmp_path / "checkpoints.db"
    graph, calls = _make(db, risk="low")

    done = start_run(graph, "sc-1", SCENARIO)

    assert done["node"] == "report"
    assert calls == ["execute"]

    with pytest.raises(NotPending):
        resume_approval(graph, "sc-1", approved=True, reason="operator")
    assert calls == ["execute"]


def test_window_is_300_seconds():
    now = datetime(2026, 11, 22, 12, 0, 0)
    assert is_expired((now - timedelta(seconds=299)).isoformat(), now=now) is False
    assert is_expired((now - timedelta(seconds=300)).isoformat(), now=now) is True
