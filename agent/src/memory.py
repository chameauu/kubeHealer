"""Incident memory — CONTRACT D8.

`recall(fault_type)` returns the 20 most recent incidents of that fault type,
newest first. `aborted` is not a success but is not an action failure either:
it is excluded from the confidence denominator and from the success rate, and
mean MTTR is averaged over successful runs only.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import config

MAX_INCIDENTS = 20

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    scenario_id TEXT PRIMARY KEY,
    fault_type TEXT,
    ended_at TEXT,
    document TEXT
)
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fault_type(run: dict[str, Any]) -> str:
    scenario = run.get("scenario") or {}
    return str(run.get("fault_type") or scenario.get("fault_type") or "unknown")


def _outcome(run: dict[str, Any]) -> str:
    outcome = run.get("outcome")
    if outcome:
        return str(outcome)
    if run.get("status") == "done":
        return "success"
    if run.get("status") == "aborted":
        return "aborted"
    return "failed"


class IncidentMemory:
    """SQLite incident log, one row per scenario."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            parent = Path(self.db_path).expanduser().parent
            parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.db_path, check_same_thread=False)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute(_SCHEMA)
            connection.commit()
            self._conn = connection
        return self._conn

    async def record(self, run: dict[str, Any]) -> None:
        async with self._lock:
            connection = self._connection()
            scenario_id = str(run["scenario_id"])
            ended_at = str(run.get("ended_at") or run.get("updated_at") or _now())
            connection.execute(
                """
                INSERT INTO incidents (scenario_id, fault_type, ended_at, document)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scenario_id) DO UPDATE SET
                    fault_type = excluded.fault_type,
                    ended_at = excluded.ended_at,
                    document = excluded.document
                """,
                (scenario_id, _fault_type(run), ended_at, json.dumps(run, sort_keys=True)),
            )
            connection.commit()

    async def recall(self, fault_type: str, limit: int = MAX_INCIDENTS) -> dict[str, Any]:
        bounded = max(1, min(int(limit), MAX_INCIDENTS))
        async with self._lock:
            connection = self._connection()
            rows = connection.execute(
                "SELECT document FROM incidents WHERE fault_type = ?"
                " ORDER BY ended_at DESC LIMIT ?",
                (fault_type, bounded),
            ).fetchall()
        incidents = [json.loads(row[0]) for row in rows]

        judged = [incident for incident in incidents if _outcome(incident) != "aborted"]
        successes = [
            incident for incident in judged if _outcome(incident) == "success"
        ]
        mttrs = [
            float(incident["mttr_seconds"])
            for incident in successes
            if incident.get("mttr_seconds") is not None
        ]
        confidence = round(100 * len(successes) / len(judged)) if judged else 0
        success_rate = len(successes) / len(judged) if judged else 0.0

        return {
            "incidents": incidents,
            "success_rate": success_rate,
            "confidence": confidence,
            "mean_mttr_seconds": (sum(mttrs) / len(mttrs)) if mttrs else None,
        }

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


memory = IncidentMemory(config.AGENT_DB_PATH)
