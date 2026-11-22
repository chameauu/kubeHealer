"""Approval timeout — owned by KubeHeal, not by LangGraph (CONTRACT D2).

The sweeper runs every 5 s and may only ever produce `aborted`: fail closed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from graph import NotPending, is_pending, resume_approval

APPROVE_TIMEOUT_SECONDS = 300


def is_expired(updated_at: str, *, now: datetime, timeout_seconds: int = APPROVE_TIMEOUT_SECONDS) -> bool:
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
