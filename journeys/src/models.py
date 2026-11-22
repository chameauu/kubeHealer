"""Typed contracts for the cluster-free journey model.

`journeys` is a deterministic simulator: no cluster, no network, no LLM. The
generated scenarios and the results they produce are a model, never measured
traffic.
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class LoadProfile(str, Enum):
    STEADY = "steady"
    SPIKE = "spike"
    BURST = "burst"
    SOAK = "soak"
    CONCURRENCY_RAMP = "concurrency_ramp"


class FaultType(str, Enum):
    LATENCY = "latency"
    TRANSIENT_ERROR = "transient_error"
    PARTIAL_FAILURE = "partial_failure"
    QUOTA_LIMIT = "quota_limit"
    PACKET_LOSS = "packet_loss"
    POD_KILL = "pod_kill"
    DEPENDENCY_OUTAGE = "dependency_outage"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


CONCURRENCY_BY_PROFILE: dict[LoadProfile, int] = {
    LoadProfile.STEADY: 5,
    LoadProfile.SPIKE: 20,
    LoadProfile.BURST: 25,
    LoadProfile.SOAK: 8,
    LoadProfile.CONCURRENCY_RAMP: 15,
}

HIGH_RISK_FAULTS: frozenset[FaultType] = frozenset(
    {FaultType.POD_KILL, FaultType.DEPENDENCY_OUTAGE}
)


class JourneyStep(BaseModel):
    name: str
    operation: str
    target: str
    latency_slo_ms: int = Field(gt=0)


class SafetyBudget(BaseModel):
    max_duration_seconds: int = Field(default=300, ge=1, le=3600)
    max_concurrency: int = Field(default=25, ge=1, le=500)
    abort_error_rate: float = Field(default=0.25, gt=0, le=1)
    abort_latency_ms: int = Field(default=5000, ge=1)


class Scenario(BaseModel):
    seed: int
    name: str
    journey: str
    steps: list[JourneyStep]
    load_profile: LoadProfile
    concurrency: int = Field(ge=1)
    duration_seconds: int = Field(ge=1)
    fault_type: FaultType
    fault_step: int = Field(ge=0)
    fault_intensity: float = Field(gt=0, le=1)
    risk: RiskLevel
    requires_approval: bool
    safety: SafetyBudget

    @model_validator(mode="after")
    def _validate_safety(self) -> "Scenario":
        if self.fault_step >= len(self.steps):
            raise ValueError("fault_step must identify a journey step")
        if self.concurrency > self.safety.max_concurrency:
            raise ValueError("concurrency exceeds safety budget")
        if self.duration_seconds > self.safety.max_duration_seconds:
            raise ValueError("duration exceeds safety budget")
        if self.risk == RiskLevel.HIGH and not self.requires_approval:
            raise ValueError("high-risk scenarios require human approval")
        return self


class GenerateRequest(BaseModel):
    seed: int | None = None
    count: int = Field(default=1, ge=1, le=100)
    journey: str | None = None


class StepEvidence(BaseModel):
    step: str
    baseline_latency_ms: int
    fault_latency_ms: int
    recovered_latency_ms: int
    requests: int
    failed_requests: int


class RunRequest(BaseModel):
    scenario: Scenario
    approved: bool = False


class RunResult(BaseModel):
    scenario: Scenario
    status: Literal["completed", "aborted", "awaiting_approval"]
    aborted: bool
    approval_required: bool
    availability: float
    recovery_rate: float
    mttr_ms: int | None
    error_budget_consumed: float
    original_journey_verified: bool
    evidence: list[StepEvidence]
    explanation: str


def _step(name: str, operation: str, target: str, latency_slo_ms: int) -> JourneyStep:
    return JourneyStep(
        name=name,
        operation=operation,
        target=target,
        latency_slo_ms=latency_slo_ms,
    )


JOURNEYS: dict[str, list[JourneyStep]] = {
    "checkout": [
        _step("browse", "GET /catalog", "catalog", 250),
        _step("cart", "POST /cart", "cart", 350),
        _step("pay", "POST /payments", "payments", 800),
        _step("confirm", "GET /orders/{id}", "orders", 400),
    ],
    "sign-in": [
        _step("login", "POST /sessions", "identity", 500),
        _step("profile", "GET /me", "profile", 300),
    ],
    "provision": [
        _step("allocate", "POST /volumes", "provisioning", 900),
        _step("attach", "POST /instances", "compute", 1200),
        _step("verify", "GET /instances/{id}", "compute", 500),
    ],
}
