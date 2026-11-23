"""The agent state (CONTRACT D2, frozen).

Every key here is part of the contract: `GET /runs/{id}` serialises this
document, and the sweeper and the resume endpoint read `approval_status`,
`approval_reason`, `node` and `updated_at`. Adding a key means amending
`CONTRACT.md` first.

`risk` and `risk_policy` are the exception that proves the rule: they record the
decision `policy.py` made — the effective risk and why it is what it is — and
they need the dated D2 amendment entry that the freeze requires before they can
be treated as frozen.
"""

from datetime import datetime, timezone
from typing import Any, TypedDict


class AgentState(TypedDict):
    scenario_id: str
    scenario: dict[str, Any]
    blast_radius: dict[str, Any] | None
    catalog_entry: dict[str, Any] | None
    memory_context: str | None
    diagnosis: dict[str, Any] | None
    risk: str | None  # effective risk, decided by `policy`, not by the diagnosis
    risk_policy: dict[str, Any] | None  # proposed vs minimum vs effective, and why
    approval_status: str  # not_required | pending | approved | rejected
    approval_reason: str | None  # "timeout" | "operator" | "policy"
    action_result: str | None
    verify_result: str | None
    node: str
    error: str | None
    mttr_seconds: float | None
    started_at: str
    updated_at: str


def utcnow() -> str:
    """ISO-8601 UTC timestamp, the format every service emits."""
    return datetime.now(timezone.utc).isoformat()


def new_state(scenario_id: str, scenario: dict[str, Any]) -> AgentState:
    """Initial state for a run: nothing known yet, no approval required."""
    timestamp = utcnow()
    return AgentState(
        scenario_id=scenario_id,
        scenario=scenario,
        blast_radius=None,
        catalog_entry=None,
        memory_context=None,
        diagnosis=None,
        risk=None,
        risk_policy=None,
        approval_status="not_required",
        approval_reason=None,
        action_result=None,
        verify_result=None,
        node="detect",
        error=None,
        mttr_seconds=None,
        started_at=timestamp,
        updated_at=timestamp,
    )
