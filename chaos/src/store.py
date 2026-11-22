"""Restart-safe SQLite repository for scenarios.

One row per scenario, the complete document in `document`, rewritten on every
transition. The store is the source of truth: a scenario that was running when
the process died is still running when it restarts, and the sweeper resumes
polling it. Mutations are serialised with one asyncio lock.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path

from models import Scenario, ScenarioStatus

_BUSY_TIMEOUT_MS = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scenarios (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    document TEXT NOT NULL
)
"""


class ScenarioStore:
    """Async façade over one SQLite connection.

    `sqlite3` is synchronous, so each operation runs to completion without
    yielding; the lock keeps writers from interleaving if a caller ever awaits
    between two calls.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        if self._db_path != ":memory:":
            Path(os.path.abspath(self._db_path)).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        self._connection.execute(_SCHEMA)
        self._connection.commit()
        self._lock = asyncio.Lock()

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the connection for diagnostics; callers never mutate through it."""
        return self._connection

    async def save(self, scenario: Scenario) -> Scenario:
        """Insert or replace the whole document for a scenario."""
        if not isinstance(scenario.status, ScenarioStatus):
            # `model_copy(update=...)` bypasses validation; normalise so the
            # document always carries the enum's value and no serializer warning.
            scenario = scenario.model_copy(update={"status": ScenarioStatus(scenario.status)})
        document = scenario.model_dump_json()
        async with self._lock:
            self._connection.execute(
                """
                INSERT INTO scenarios (id, status, created_at, document)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    document = excluded.document
                """,
                (scenario.id, ScenarioStatus(scenario.status).value, scenario.created_at, document),
            )
            self._connection.commit()
        return scenario

    async def get(self, scenario_id: str) -> Scenario | None:
        """Return one scenario, or None when it is unknown."""
        async with self._lock:
            row = self._connection.execute(
                "SELECT document FROM scenarios WHERE id = ?", (scenario_id,)
            ).fetchone()
        if row is None:
            return None
        return _parse(row["document"])

    async def list(self, status: ScenarioStatus | str | None = None) -> list[Scenario]:
        """Return scenarios oldest first, optionally filtered by status.

        Rows that no longer parse are skipped rather than poisoning the whole
        listing: one bad document must not make the API blind.
        """
        query = "SELECT document FROM scenarios"
        parameters: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            parameters = (ScenarioStatus(status).value,)
        query += " ORDER BY created_at, id"
        async with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        scenarios: list[Scenario] = []
        for row in rows:
            scenario = _parse(row["document"])
            if scenario is not None:
                scenarios.append(scenario)
        return scenarios

    async def delete(self, scenario_id: str) -> None:
        """Remove one scenario; deleting an unknown id is a no-op."""
        async with self._lock:
            self._connection.execute("DELETE FROM scenarios WHERE id = ?", (scenario_id,))
            self._connection.commit()

    def close(self) -> None:
        """Close the connection. The store is unusable afterwards."""
        self._connection.close()


def _parse(document: str) -> Scenario | None:
    try:
        return Scenario.model_validate_json(document)
    except ValueError:
        return None
