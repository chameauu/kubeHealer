"""Approval timeout — owned by KubeHeal, not by LangGraph (CONTRACT D2).

The sweeper runs every 5 s and may only ever produce `aborted`: fail closed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable

from config import config
from events import log_event
from graph import NotPending, is_pending, resume_approval
from store import store as run_store

APPROVE_TIMEOUT_SECONDS = 300
SWEEP_INTERVAL_SECONDS = 5.0


def is_expired(
    updated_at: str,
    *,
    now: datetime,
    timeout_seconds: int = APPROVE_TIMEOUT_SECONDS,
) -> bool:
    timestamp = datetime.fromisoformat(updated_at)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - timestamp).total_seconds() >= timeout_seconds


def abort_if_expired(
    graph: Any,
    scenario_id: str,
    updated_at: str,
    *,
    now: datetime,
    timeout_seconds: int = APPROVE_TIMEOUT_SECONDS,
) -> bool:
    """Abort a run whose approval window closed. Returns True when it acted."""
    if not is_expired(updated_at, now=now, timeout_seconds=timeout_seconds):
        return False
    if not is_pending(graph, scenario_id):
        return False
    try:
        resume_approval(graph, scenario_id, approved=False, reason="timeout")
    except NotPending:
        return False
    return True


async def sweep_once(
    graph: Any,
    *,
    now: Callable[[], datetime] | None = None,
    timeout_seconds: int = APPROVE_TIMEOUT_SECONDS,
) -> list[str]:
    """Abort expired approvals and reconcile the projection. Returns changed ids."""
    current = (now or (lambda: datetime.now(timezone.utc)))()
    changed: list[str] = []
    for document in await run_store.list():
        if document.get("approval_status") != "pending":
            continue
        updated_at = document.get("updated_at")
        if not updated_at:
            continue
        if not is_expired(updated_at, now=current, timeout_seconds=timeout_seconds):
            continue
        scenario_id = str(document["scenario_id"])
        if not is_pending(graph, scenario_id):
            continue
        try:
            final = await asyncio.to_thread(
                resume_approval,
                graph,
                scenario_id,
                approved=False,
                reason="timeout",
            )
        except NotPending:
            continue
        merged = {**document, **final}
        merged["status"] = "aborted"
        await run_store.save(merged)
        changed.append(scenario_id)
    return changed


async def sweep_forever(
    graph: Any,
    *,
    interval_seconds: float = SWEEP_INTERVAL_SECONDS,
    timeout_seconds: int | None = None,
) -> None:
    """The background loop started by the lifespan and cancelled on shutdown."""
    timeout = (
        config.APPROVAL_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    )
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            changed = await sweep_once(graph, timeout_seconds=timeout)
            if changed:
                log_event("agent.sweeper.aborted", scenario_ids=changed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_event("agent.sweeper.error", error=str(exc))
