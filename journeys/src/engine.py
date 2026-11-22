"""Scenario generation and execution.

Everything here is deterministic and runs offline: a scenario's `seed` fully
determines the run, and the same seed produces the same report. Nothing in this
module contacts a cluster.
"""

import secrets
from random import Random

from models import (
    CONCURRENCY_BY_PROFILE,
    HIGH_RISK_FAULTS,
    JOURNEYS,
    FaultType,
    GenerateRequest,
    LoadProfile,
    RiskLevel,
    RunRequest,
    RunResult,
    SafetyBudget,
    Scenario,
    StepEvidence,
)

MODE_NOTE = (
    "cluster-free deterministic model, not measured traffic; "
    "no cluster was contacted and no measured figure is claimed"
)

MAX_DURATION_SECONDS = 120
MIN_DURATION_SECONDS = 20


def generate_scenarios(request: GenerateRequest) -> list[Scenario]:
    """Expand one request into `count` scenarios, deterministically when seeded."""

    if request.journey is not None and request.journey not in JOURNEYS:
        raise ValueError(f"unknown journey: {request.journey!r}")

    root = request.seed if request.seed is not None else secrets.randbits(63)
    scenarios: list[Scenario] = []

    for index in range(request.count):
        seed = root + index
        rng = Random(seed)
        # When a journey is requested explicitly, the choice draw is not consumed.
        journey = request.journey or rng.choice(sorted(JOURNEYS))
        load_profile = rng.choice(list(LoadProfile))
        fault_type = rng.choice(list(FaultType))
        risk = (
            RiskLevel.HIGH if fault_type in HIGH_RISK_FAULTS else RiskLevel.MEDIUM
        )
        duration_seconds = rng.randint(MIN_DURATION_SECONDS, MAX_DURATION_SECONDS)
        steps = [step.model_copy() for step in JOURNEYS[journey]]
        fault_step = rng.randrange(len(steps))
        fault_intensity = round(rng.uniform(0.1, 0.8), 2)

        scenarios.append(
            Scenario(
                seed=seed,
                name=f"{journey}-{fault_type.value}-{seed}",
                journey=journey,
                steps=steps,
                load_profile=load_profile,
                concurrency=CONCURRENCY_BY_PROFILE[load_profile],
                duration_seconds=duration_seconds,
                fault_type=fault_type,
                fault_step=fault_step,
                fault_intensity=fault_intensity,
                risk=risk,
                requires_approval=risk == RiskLevel.HIGH,
                safety=SafetyBudget(),
            )
        )

    return scenarios


def _awaiting_approval(scenario: Scenario) -> RunResult:
    return RunResult(
        scenario=scenario,
        status="awaiting_approval",
        aborted=False,
        approval_required=True,
        availability=0.0,
        recovery_rate=0.0,
        mttr_ms=None,
        error_budget_consumed=0.0,
        original_journey_verified=False,
        evidence=[],
        explanation=(
            f"{MODE_NOTE}. This high-risk scenario waits for a human decision: "
            "nothing has been executed and no recovery is claimed."
        ),
    )


def run_scenario(request: RunRequest) -> RunResult:
    """Simulate one scenario. This never raises and never touches a backend."""

    scenario = request.scenario
    if scenario.requires_approval and not request.approved:
        return _awaiting_approval(scenario)

    rng = Random(scenario.seed)
    requests_per_step = max(10, scenario.concurrency * 4)
    evidence: list[StepEvidence] = []
    failed_total = 0
    recovered_steps = 0
    peak_fault_latency = 0

    for index, step in enumerate(scenario.steps):
        baseline = int(step.latency_slo_ms * rng.uniform(0.35, 0.75))
        on_fault_step = index == scenario.fault_step
        fault_factor = 8.0 if on_fault_step else 0.2
        fault_latency = int(
            baseline * (1 + scenario.fault_intensity * fault_factor)
        )
        failure_ratio = 0.45 if on_fault_step else 0.01
        failed = int(requests_per_step * scenario.fault_intensity * failure_ratio)
        recovered = int(baseline * rng.uniform(0.9, 1.15))

        if recovered <= step.latency_slo_ms:
            recovered_steps += 1
        failed_total += failed
        peak_fault_latency = max(peak_fault_latency, fault_latency)
        evidence.append(
            StepEvidence(
                step=step.name,
                baseline_latency_ms=baseline,
                fault_latency_ms=fault_latency,
                recovered_latency_ms=recovered,
                requests=requests_per_step,
                failed_requests=failed,
            )
        )

    step_count = len(scenario.steps)
    error_rate = failed_total / (step_count * requests_per_step)
    aborted = (
        error_rate >= scenario.safety.abort_error_rate
        or peak_fault_latency >= scenario.safety.abort_latency_ms
    )
    availability = round(1 - error_rate, 4)
    recovery_rate = round(recovered_steps / step_count, 4)
    mttr_ms = None if aborted else int(400 + scenario.fault_intensity * 2600)
    error_budget_consumed = round(error_rate / 0.01, 2)
    verified = recovered_steps == step_count and not aborted

    if aborted:
        explanation = (
            f"{MODE_NOTE}. Execution aborted: simulated error rate {error_rate:.4f} "
            f"and peak latency {peak_fault_latency} ms exceeded the safety budget "
            f"(error rate {scenario.safety.abort_error_rate}, latency "
            f"{scenario.safety.abort_latency_ms} ms), so no MTTR is claimed."
        )
    else:
        explanation = (
            f"{MODE_NOTE}. The {scenario.journey} journey completed with "
            f"availability {availability} and recovery rate {recovery_rate}."
        )

    return RunResult(
        scenario=scenario,
        status="aborted" if aborted else "completed",
        aborted=aborted,
        approval_required=scenario.requires_approval,
        availability=availability,
        recovery_rate=recovery_rate,
        mttr_ms=mttr_ms,
        error_budget_consumed=error_budget_consumed,
        original_journey_verified=verified,
        evidence=evidence,
        explanation=explanation,
    )
