"""Contract tests for the cluster-free journey model.

The arithmetic is frozen: these fixtures are the parity gate, so every number is
asserted literally instead of being recomputed from the implementation.
"""

from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from main import app
from models import JOURNEYS, FaultType, LoadProfile, RiskLevel


def generate(client: TestClient, **payload: Any) -> list[dict[str, Any]]:
    response = client.post("/scenarios/generate", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list)
    return body


def run(
    client: TestClient, scenario: dict[str, Any], approved: bool = False
) -> dict[str, Any]:
    response = client.post(
        "/scenarios/run", json={"scenario": scenario, "approved": approved}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_journeys_catalogue_matches_contract() -> None:
    assert sorted(JOURNEYS) == ["checkout", "provision", "sign-in"]

    expected = {
        "checkout": [
            ("browse", "GET /catalog", "catalog", 250),
            ("cart", "POST /cart", "cart", 350),
            ("pay", "POST /payments", "payments", 800),
            ("confirm", "GET /orders/{id}", "orders", 400),
        ],
        "sign-in": [
            ("login", "POST /sessions", "identity", 500),
            ("profile", "GET /me", "profile", 300),
        ],
        "provision": [
            ("allocate", "POST /volumes", "provisioning", 900),
            ("attach", "POST /instances", "compute", 1200),
            ("verify", "GET /instances/{id}", "compute", 500),
        ],
    }

    for journey, steps in expected.items():
        actual = [
            (step.name, step.operation, step.target, step.latency_slo_ms)
            for step in JOURNEYS[journey]
        ]
        assert actual == steps, f"catalogue drift in {journey}"


def test_generate_parity_seed_42() -> None:
    with TestClient(app) as client:
        scenarios = generate(client, seed=42, count=1)

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario["seed"] == 42
    assert scenario["name"] == "sign-in-latency-42"
    assert scenario["journey"] == "sign-in"
    assert scenario["load_profile"] == "steady"
    assert scenario["concurrency"] == 5
    assert scenario["duration_seconds"] == 114
    assert scenario["fault_type"] == "latency"
    assert scenario["fault_step"] == 1
    assert scenario["fault_intensity"] == 0.27
    assert scenario["risk"] == "medium"
    assert scenario["requires_approval"] is False
    assert scenario["safety"] == {
        "max_duration_seconds": 300,
        "max_concurrency": 25,
        "abort_error_rate": 0.25,
        "abort_latency_ms": 5000,
    }
    assert scenario["steps"] == [step.model_dump() for step in JOURNEYS["sign-in"]]


def test_run_parity_seed_42_approved() -> None:
    with TestClient(app) as client:
        scenario = generate(client, seed=42, count=1)[0]
        result = run(client, scenario, approved=True)

    assert result["scenario"] == scenario
    assert result["status"] == "completed"
    assert result["aborted"] is False
    assert result["approval_required"] is False
    assert result["availability"] == 0.95
    assert result["recovery_rate"] == 1.0
    assert result["mttr_ms"] == 1102
    assert result["error_budget_consumed"] == 5.0
    assert result["original_journey_verified"] is True
    assert result["evidence"] == [
        {
            "step": "login",
            "baseline_latency_ms": 302,
            "fault_latency_ms": 318,
            "recovered_latency_ms": 273,
            "requests": 20,
            "failed_requests": 0,
        },
        {
            "step": "profile",
            "baseline_latency_ms": 138,
            "fault_latency_ms": 436,
            "recovered_latency_ms": 131,
            "requests": 20,
            "failed_requests": 2,
        },
    ]


def test_generate_count_five_uses_contiguous_seeds_and_distinct_names() -> None:
    with TestClient(app) as client:
        scenarios = generate(client, seed=42, count=5)

    assert [scenario["seed"] for scenario in scenarios] == [42, 43, 44, 45, 46]
    assert len({scenario["name"] for scenario in scenarios}) == 5
    assert all(str(scenario["seed"]) in scenario["name"] for scenario in scenarios)


def test_seed_one_first_scenario_is_high_risk_dependency_outage() -> None:
    with TestClient(app) as client:
        scenarios = generate(client, seed=1, count=100)
        assert len(scenarios) == 100

        first = scenarios[0]
        assert first["name"] == "checkout-dependency_outage-1"
        assert first["fault_type"] == "dependency_outage"
        assert first["risk"] == "high"
        assert first["requires_approval"] is True

        result = run(client, first, approved=False)

    assert result["status"] == "awaiting_approval"
    assert result["aborted"] is False
    assert result["approval_required"] is True
    assert result["availability"] == 0
    assert result["recovery_rate"] == 0
    assert result["mttr_ms"] is None
    assert result["error_budget_consumed"] == 0
    assert result["original_journey_verified"] is False
    assert result["evidence"] == []
    assert "human" in result["explanation"].lower()


def test_generation_is_deterministic_for_the_same_seed_and_count() -> None:
    with TestClient(app) as client:
        first = generate(client, seed=7, count=10)
        second = generate(client, seed=7, count=10)

    assert first == second


def test_budget_boundary_concurrency_and_duration() -> None:
    with TestClient(app) as client:
        base = generate(client, seed=42, count=1)[0]

        accepted = client.post(
            "/scenarios/run", json={"scenario": {**base, "concurrency": 25}}
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["scenario"]["concurrency"] == 25

        rejected = client.post(
            "/scenarios/run", json={"scenario": {**base, "concurrency": 26}}
        )
        assert rejected.status_code == 422
        assert "concurrency exceeds safety budget" in rejected.text

        longest = client.post(
            "/scenarios/run", json={"scenario": {**base, "duration_seconds": 300}}
        )
        assert longest.status_code == 200, longest.text
        assert longest.json()["scenario"]["duration_seconds"] == 300

        too_long = client.post(
            "/scenarios/run", json={"scenario": {**base, "duration_seconds": 301}}
        )
        assert too_long.status_code == 422
        assert "duration exceeds safety budget" in too_long.text


def test_medium_risk_scenario_executes_without_approval() -> None:
    with TestClient(app) as client:
        scenario = generate(client, seed=42, count=1)[0]
        result = run(client, scenario, approved=False)

    assert result["approval_required"] is False
    assert result["status"] == "completed"
    assert result["aborted"] is False
    assert result["mttr_ms"] == 1102


def test_unknown_journey_is_422_with_a_readable_detail() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/scenarios/generate", json={"seed": 1, "count": 1, "journey": "teleport"}
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, str)
    assert "unknown journey" in detail


def test_generate_request_bounds_are_422() -> None:
    with TestClient(app) as client:
        assert client.post("/scenarios/generate", json={"seed": 1, "count": 0}).status_code == 422
        assert client.post("/scenarios/generate", json={"seed": 1, "count": 101}).status_code == 422


def test_evidence_has_one_record_per_step_in_order() -> None:
    seeds = [1, 42, 52]
    with TestClient(app) as client:
        for seed in seeds:
            scenario = generate(client, seed=seed, count=1)[0]
            result = run(client, scenario, approved=True)

            steps = [step["name"] for step in scenario["steps"]]
            assert [entry["step"] for entry in result["evidence"]] == steps
            assert len(result["evidence"]) == len(steps)


def test_approval_flag_is_ignored_for_non_approval_scenarios() -> None:
    with TestClient(app) as client:
        scenario = generate(client, seed=42, count=1)[0]
        without_flag = run(client, scenario, approved=False)
        with_flag = run(client, scenario, approved=True)

    assert without_flag == with_flag


def test_approved_high_risk_scenario_executes() -> None:
    with TestClient(app) as client:
        scenario = generate(client, seed=1, count=1)[0]
        result = run(client, scenario, approved=True)

    assert result["status"] == "completed"
    assert result["aborted"] is False
    assert result["approval_required"] is True
    assert len(result["evidence"]) == len(scenario["steps"])


def test_run_request_requires_a_full_scenario() -> None:
    with TestClient(app) as client:
        response = client.post("/scenarios/run", json={"scenario": {"seed": 1}})

    assert response.status_code == 422


def test_risk_level_low_is_declared_but_never_generated() -> None:
    assert RiskLevel.LOW.value == "low"

    with TestClient(app) as client:
        scenarios = generate(client, seed=1, count=100)

    # Exhaustive over the 100 generated scenarios: generation can only emit the
    # two risks the contract derives from the fault type.
    risks = {scenario["risk"] for scenario in scenarios}
    assert risks == {"medium", "high"}

    fault_types = {scenario["fault_type"] for scenario in scenarios}
    assert fault_types <= {fault_type.value for fault_type in FaultType}
    profiles = {scenario["load_profile"] for scenario in scenarios}
    assert profiles <= {profile.value for profile in LoadProfile}


@pytest.mark.parametrize(
    ("seed", "mutate", "message"),
    [
        (
            42,
            lambda s: {**s, "fault_step": len(s["steps"])},
            "fault_step must identify a journey step",
        ),
        (
            42,
            lambda s: {**s, "concurrency": 26},
            "concurrency exceeds safety budget",
        ),
        (
            42,
            lambda s: {**s, "duration_seconds": 301},
            "duration exceeds safety budget",
        ),
        (
            1,
            lambda s: {**s, "requires_approval": False},
            "high-risk scenarios require human approval",
        ),
    ],
)
def test_scenario_budget_validators(
    seed: int, mutate: Callable[[dict[str, Any]], dict[str, Any]], message: str
) -> None:
    with TestClient(app) as client:
        base = generate(client, seed=seed, count=1)[0]
        response = client.post("/scenarios/run", json={"scenario": mutate(base)})

    assert response.status_code == 422, response.text
    assert message in response.text


def test_aborted_run_claims_no_mttr() -> None:
    with TestClient(app) as client:
        scenario = generate(client, seed=52, count=1)[0]
        assert scenario["risk"] == "high"
        assert scenario["requires_approval"] is True

        result = run(client, scenario, approved=True)

    assert result["status"] == "aborted"
    assert result["aborted"] is True
    assert result["mttr_ms"] is None
    assert result["original_journey_verified"] is False
    assert 0 <= result["availability"] < 1
    assert "aborted" in result["explanation"].lower()


def test_every_explanation_says_model_not_measured_traffic() -> None:
    with TestClient(app) as client:
        completed = run(client, generate(client, seed=42, count=1)[0], approved=True)
        aborted = run(client, generate(client, seed=52, count=1)[0], approved=True)
        waiting = run(client, generate(client, seed=1, count=1)[0], approved=False)

    for result in (completed, aborted, waiting):
        explanation = result["explanation"].lower()
        assert "model" in explanation
        assert "not measured traffic" in explanation
        assert "cluster-free" in explanation
