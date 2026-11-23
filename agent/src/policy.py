"""The authorisation boundary: what an action may cost is decided here.

`ACTION_MINIMUM_RISK` is the floor for every remediation the system can perform,
and it is the only place the approval gate gets its answer. The diagnosis
payload's `risk` is a proposal; a model that claims `low` for a restart cannot
lower the floor, because the effective risk is the higher of the two. Anything
this module does not recognise — an unknown or empty action, a proposal that is
not one of `low`, `medium`, `high` (wrong type, wrong case, missing) — is
treated as `high` rather than defaulted down: fail closed.

Only the three actions of CONTRACT D2 exist, so the table is complete by
construction, and an action that is not in it is unknown and therefore `high`.

Case-sensitive on purpose: `"LOW"` is not a risk level this system accepts.
"""

from __future__ import annotations

from typing import Any

ACTION_MINIMUM_RISK = {
    "stop_scenario": "low",  # removes the fault we injected
    "restart_deployment": "medium",  # disruptive; brief unavailability
    "scale_deployment": "high",  # changes capacity and can mask the fault
}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
APPROVAL_REQUIRED_FROM = "medium"
UNRECOGNISED_RISK = "high"


def _as_risk(value: Any) -> str:
    """The proposal as a level this system accepts, else `high`."""
    return value if isinstance(value, str) and value in RISK_ORDER else UNRECOGNISED_RISK


def _minimum_risk(action: str | None) -> str:
    """The floor for an action; unknown or empty means the highest floor."""
    if not isinstance(action, str):
        return UNRECOGNISED_RISK
    return ACTION_MINIMUM_RISK.get(action, UNRECOGNISED_RISK)


def effective_risk(action: str | None, proposed: Any) -> str:
    """The higher of the proposal and the action's floor; never lower than either."""
    floor = _minimum_risk(action)
    requested = _as_risk(proposed)
    if RISK_ORDER[requested] > RISK_ORDER[floor]:
        return requested
    return floor


def requires_approval(risk: str) -> bool:
    """`medium` and above park for a human; anything unreadable does too."""
    return RISK_ORDER[_as_risk(risk)] >= RISK_ORDER[APPROVAL_REQUIRED_FROM]


def explain(action: str | None, proposed: Any) -> dict[str, Any]:
    """The record the run document keeps about the gate decision.

    `proposed_risk` is the proposal after normalisation: a value that is not an
    accepted level is recorded as `high`, so the document never presents a
    string the gate could not read as if it had been honoured. `raised` is true
    only when the effective risk is above the proposal, never when it merely
    equalled the floor.
    """
    floor = _minimum_risk(action)
    requested = _as_risk(proposed)
    effective = effective_risk(action, proposed)
    return {
        "proposed_risk": requested,
        "effective_risk": effective,
        "minimum_risk": floor,
        "raised": RISK_ORDER[effective] > RISK_ORDER[requested],
    }
