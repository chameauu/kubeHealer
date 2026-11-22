"""Unified scenario model: one shape for both chaos backends.

A scenario is either a Chaos Mesh experiment or a rule on the provisioning
simulator. The control surface is identical for both; the domain-specific
shape of `target` and `params` is validated by `chaos_mesh.py` or forwarded to
the simulator, so nothing here guesses what a backend will accept.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


def new_id() -> str:
    """A scenario id: stable, URL-safe and valid as a DNS label."""
    return f"scn-{uuid4().hex[:10]}"


def utc_now() -> str:
    """Timestamp in the format every service in the platform emits."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ScenarioDomain(str, Enum):
    """Which backend actually runs the fault."""

    CHAOS_MESH = "chaos_mesh"
    SIMULATOR = "simulator"


class ScenarioStatus(str, Enum):
    """Lifecycle of a scenario.

    `completed` is deduced only when the backend object disappears; a
    scenario's duration never completes it on its own.
    """

    PENDING = "pending"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


class ChaosMeshFaultType(str, Enum):
    """The four Chaos Mesh faults this service is allowed to inject."""

    POD_KILL = "pod_kill"
    NETWORK_LATENCY = "network_latency"
    PACKET_LOSS = "packet_loss"
    IO_DELAY = "io_delay"


class SimulatorFaultType(str, Enum):
    """Mirrors the simulator's fault enumeration; duplicated on purpose."""

    LATENCY = "latency"
    TRANSIENT_ERROR = "transient_error"
    PARTIAL_FAILURE = "partial_failure"
    QUOTA_LIMIT = "quota_limit"


_MODE_PATTERN = "^(one|all|fixed|fixed-percent|random-max-percent)$"
_VALUE_MODES = ("fixed", "fixed-percent", "random-max-percent")


class K8sTarget(BaseModel):
    """The pod selector a Chaos Mesh experiment runs against."""

    namespace: str
    label_selector: dict[str, str] = Field(default_factory=dict)
    mode: str = Field(default="one", pattern=_MODE_PATTERN)
    value: str | None = None

    @model_validator(mode="after")
    def _value_required_for_relative_modes(self) -> K8sTarget:
        if self.mode in _VALUE_MODES and not self.value:
            raise ValueError(f"target.value is required when mode is '{self.mode}'")
        return self


class ScenarioCreateRequest(BaseModel):
    """What a caller sends to launch either kind of scenario."""

    name: str
    correlation_id: str | None = None
    domain: ScenarioDomain
    fault_type: str
    target: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float | None = Field(default=None, gt=0)
    probability: float = Field(default=1.0, ge=0.0, le=1.0)
    params: dict[str, Any] = Field(default_factory=dict)


class Scenario(BaseModel):
    """The record every list/get/stop call returns, whatever the backend."""

    id: str = Field(default_factory=new_id)
    correlation_id: str | None = None
    name: str
    domain: ScenarioDomain
    fault_type: str
    # A k8s selector arrives as an object in tests and as a dict over HTTP;
    # both must land on the same document so the store round-trips exactly.
    target: K8sTarget | dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    status: ScenarioStatus = ScenarioStatus.PENDING
    backend_ref: str | None = None
    live_status: dict[str, Any] | None = None
    error: str | None = None

    created_at: str = Field(default_factory=utc_now)
    started_at: str | None = None
    ended_at: str | None = None

    def touch_status(self, status: ScenarioStatus | str, *, error: str | None = None) -> None:
        """Apply a lifecycle transition and its timestamp rules.

        `stopping` is deliberately windowless: it is a request in flight, not
        an end. Only terminal states stamp `ended_at`.
        """
        target = ScenarioStatus(status)
        self.status = target
        if error is not None:
            self.error = error
        now = utc_now()
        if target is ScenarioStatus.RUNNING and self.started_at is None:
            self.started_at = now
        if target in (ScenarioStatus.STOPPED, ScenarioStatus.COMPLETED, ScenarioStatus.FAILED):
            self.ended_at = now
