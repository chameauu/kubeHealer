"""`policy.py` — the authorisation boundary the diagnosis cannot argue with.

The diagnosis payload's `risk` is a proposal. These tests pin the floor each
action carries, the fail-closed treatment of anything that is not a level this
system accepts, and the record kept for the run document.
"""

from __future__ import annotations

import pytest

import policy


def test_a_restart_proposed_as_low_is_escalated_to_medium() -> None:
    # F2: a model saying `low` must not make a disruptive action self-authorise.
    assert policy.effective_risk("restart_deployment", "low") == "medium"


def test_a_scale_proposed_as_low_is_escalated_to_high() -> None:
    assert policy.effective_risk("scale_deployment", "low") == "high"


def test_a_stop_proposed_as_low_stays_low() -> None:
    # Stopping the injection is how the fault is removed: the safe action.
    assert policy.effective_risk("stop_scenario", "low") == "low"


def test_a_proposal_above_the_minimum_is_never_lowered() -> None:
    assert policy.effective_risk("stop_scenario", "high") == "high"
    assert policy.effective_risk("stop_scenario", "medium") == "medium"
    assert policy.effective_risk("restart_deployment", "high") == "high"


@pytest.mark.parametrize("action", [None, "", "delete_everything", "Restart_Deployment", 7])
def test_an_unknown_action_fails_closed_to_high(action: object) -> None:
    assert policy.effective_risk(action, "low") == "high"


@pytest.mark.parametrize("proposed", [None, "LOW", "High", "", "critical", 7, True, ["low"]])
def test_a_proposal_that_is_not_a_level_fails_closed_to_high(proposed: object) -> None:
    # Case-sensitive on purpose: `"LOW"` is not a level this system accepts, and
    # a value it cannot read is never evidence that the risk is low.
    assert policy.effective_risk("stop_scenario", proposed) == "high"


@pytest.mark.parametrize(
    ("risk", "expected"),
    [("low", False), ("medium", True), ("high", True)],
)
def test_approval_is_required_from_medium(risk: str, expected: bool) -> None:
    assert policy.requires_approval(risk) is expected


def test_approval_is_required_for_a_level_it_does_not_recognise() -> None:
    assert policy.requires_approval("LOW") is True
    assert policy.requires_approval("") is True


def test_explain_records_the_escalation() -> None:
    assert policy.explain("restart_deployment", "low") == {
        "proposed_risk": "low",
        "effective_risk": "medium",
        "minimum_risk": "medium",
        "raised": True,
    }


def test_explain_marks_raised_only_when_the_value_changed() -> None:
    assert policy.explain("stop_scenario", "low")["raised"] is False
    assert policy.explain("stop_scenario", "high")["raised"] is False
    assert policy.explain("scale_deployment", "low")["raised"] is True


def test_explain_normalises_an_unacceptable_proposal_to_high() -> None:
    record = policy.explain("restart_deployment", "LOW")

    assert record["proposed_risk"] == "high"
    assert record["effective_risk"] == "high"
    assert record["raised"] is False


def test_the_table_covers_exactly_the_three_actions_of_d2() -> None:
    # New fault actions are a scope change; this test is the tripwire.
    assert set(policy.ACTION_MINIMUM_RISK) == {
        "restart_deployment",
        "stop_scenario",
        "scale_deployment",
    }
