"""Persistence contract: WAL, document fidelity, corrupt rows, restart reconciliation.

Rows 54-58 of the M2 behaviour list. Real files are used throughout: an
in-memory database cannot prove that a document survives a process boundary,
and the whole point of the store is that it does.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from engine import ScenarioEngine
from models import K8sTarget, Scenario
from store import ScenarioStore
from tests.fakes import FakeChaosMeshClient, FakeSimulatorClient

SCENARIO_ID = "scn-0123456789"
BACKEND_REF = "kubeheal-chaos-" + SCENARIO_ID


def _scenario(**overrides: Any) -> Scenario:
    values: dict[str, Any] = {
        "id": SCENARIO_ID,
        "name": "pod-kill-orders",
        "correlation_id": "corr-abc",
        "domain": "chaos_mesh",
        "fault_type": "pod_kill",
        "target": K8sTarget(namespace="argus-system", label_selector={"app": "orders"}),
        "duration_seconds": 30,
        "params": {},
        "backend_ref": BACKEND_REF,
    }
    values.update(overrides)
    return Scenario(**values)


async def test_document_round_trips_byte_identical(tmp_path: Path) -> None:
    db_path = tmp_path / "chaos.db"
    scenario = _scenario()

    writer = ScenarioStore(db_path=db_path)
    await writer.save(scenario)
    writer.close()

    reader = ScenarioStore(db_path=db_path)
    try:
        loaded = await reader.get(SCENARIO_ID)
        listed = await reader.list()
    finally:
        reader.close()

    assert loaded is not None
    assert loaded.model_dump_json() == scenario.model_dump_json()
    assert [item.correlation_id for item in listed] == ["corr-abc"]


async def test_status_update_survives_and_removal_is_permanent(tmp_path: Path) -> None:
    db_path = tmp_path / "chaos.db"
    scenario = _scenario()
    scenario.touch_status("running")

    first = ScenarioStore(db_path=db_path)
    await first.save(scenario)
    await first.save(scenario.model_copy(update={"status": "stopped"}))
    first.close()

    second = ScenarioStore(db_path=db_path)
    try:
        reloaded = await second.get(SCENARIO_ID)
        assert reloaded is not None
        assert reloaded.status == "stopped"
        await second.delete(SCENARIO_ID)
    finally:
        second.close()

    third = ScenarioStore(db_path=db_path)
    try:
        assert await third.get(SCENARIO_ID) is None
        assert await third.list() == []
    finally:
        third.close()


async def test_corrupt_document_row_is_skipped(tmp_path: Path) -> None:
    db_path = tmp_path / "chaos.db"
    empty = ScenarioStore(db_path=db_path)
    empty.close()

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO scenarios (id, document) VALUES (?, ?)",
            ("scn-broken0000", "{not json"),
        )
        connection.commit()
    finally:
        connection.close()

    store = ScenarioStore(db_path=db_path)
    try:
        assert await store.list() == []
    finally:
        store.close()


async def test_wal_and_busy_timeout_are_configured(tmp_path: Path) -> None:
    db_path = tmp_path / "chaos.db"
    store = ScenarioStore(db_path=db_path)
    try:
        await store.save(_scenario())
        probe = sqlite3.connect(db_path)
        try:
            journal_mode = probe.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            probe.close()
        busy_timeout = store.connection.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        store.close()

    assert str(journal_mode).lower() == "wal"
    assert busy_timeout > 0


async def test_reopened_running_scenario_is_not_auto_failed_and_sweeps(tmp_path: Path) -> None:
    db_path = tmp_path / "chaos.db"
    scenario = _scenario()
    scenario.touch_status("running")

    writer = ScenarioStore(db_path=db_path)
    await writer.save(scenario)
    writer.close()

    reopened = ScenarioStore(db_path=db_path)
    try:
        reloaded = await reopened.get(SCENARIO_ID)
        assert reloaded is not None
        assert reloaded.status == "running"
        assert reloaded.ended_at is None

        events: list[dict[str, Any]] = []
        engine = ScenarioEngine(
            store=reopened,
            chaos_mesh=FakeChaosMeshClient(),
            simulator=FakeSimulatorClient(),
            sweep_interval_seconds=0.05,
            event_sink=events.append,
        )
        await engine.start_sweeper()
        try:
            await asyncio.sleep(0.2)
            swept = await engine.get(SCENARIO_ID, refresh=False)
        finally:
            await engine.stop_sweeper()

        assert swept.status == "completed"
    finally:
        reopened.close()
