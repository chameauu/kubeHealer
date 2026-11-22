"""The `agent_runs` projection and checkpoint-aware reconciliation.

CONTRACT D2 fixes the order: the LangGraph checkpoint commits first, and this
read model is written after it. `reconcile_interrupted()` therefore asks the
checkpointer what is still resumable instead of blanket-failing every
non-terminal row: a pending interrupt survives a restart, everything else
fails closed.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from config import config

TERMINAL_STATUSES = frozenset({"done", "aborted", "error"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    scenario_id TEXT PRIMARY KEY,
    status TEXT,
    document TEXT,
    updated_at TEXT
)
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentRunStore:
    """SQLite read model for runs, written only after the checkpoint commits."""

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

    async def save(self, run: dict[str, Any]) -> None:
        """Upsert the full document. The projection never overwrites history."""
        async with self._lock:
            connection = self._connection()
            document = json.dumps(run, sort_keys=True)
            status = str(run.get("status") or "")
            updated_at = str(run.get("updated_at") or utcnow())
            connection.execute(
                """
                INSERT INTO agent_runs (scenario_id, status, document, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scenario_id) DO UPDATE SET
                    status = excluded.status,
                    document = excluded.document,
                    updated_at = excluded.updated_at
                """,
                (str(run["scenario_id"]), status, document, updated_at),
            )
            connection.commit()

    async def get(self, scenario_id: str) -> dict[str, Any] | None:
        async with self._lock:
            connection = self._connection()
            row = connection.execute(
                "SELECT document FROM agent_runs WHERE scenario_id = ?",
                (scenario_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    async def list(self, status: str | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            connection = self._connection()
            if status is None:
                rows = connection.execute(
                    "SELECT document FROM agent_runs ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT document FROM agent_runs WHERE status = ?"
                    " ORDER BY updated_at DESC",
                    (status,),
                ).fetchall()
        return [json.loads(row[0]) for row in rows]

    async def reconcile_interrupted(
        self, is_pending: Callable[[str], bool]
    ) -> list[str]:
        """Fail-closed reconciliation of runs interrupted by a restart.

        `is_pending(scenario_id)` asks the checkpointer whether the thread is
        parked at the approval interrupt. It may raise when no checkpoint
        exists for the thread; that is treated as "not resumable".
        """
        changed: list[str] = []
        for document in await self.list():
            if document.get("status") in TERMINAL_STATUSES:
                continue
            scenario_id = str(document["scenario_id"])
            try:
                pending = bool(is_pending(scenario_id))
            except Exception:
                pending = False
            if pending:
                continue
            reason = (
                "manual review required: execute was interrupted"
                if document.get("node") == "execute"
                else "manual review required: no resumable checkpoint"
            )
            reconciled = {
                **document,
                "status": "error",
                "node": "error",
                "error": reason,
                "updated_at": utcnow(),
            }
            await self.save(reconciled)
            changed.append(scenario_id)
        return changed

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


store = AgentRunStore(config.AGENT_DB_PATH)
