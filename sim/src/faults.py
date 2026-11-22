"""Fault rules: the deterministic switch that makes the simulator misbehave.

A rule is a filter plus a probability. `resolve` walks the rules in insertion
order and returns the first live match whose dice roll passes. Nothing here
touches a resource; the lifecycle engine decides what a rule does.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from models import ResourceType, new_id, utc_now


class FaultType(str, Enum):
    LATENCY = "latency"
    TRANSIENT_ERROR = "transient_error"
    PARTIAL_FAILURE = "partial_failure"
    QUOTA_LIMIT = "quota_limit"


class FaultRuleCreateRequest(BaseModel):
    fault_type: FaultType
    resource_type: ResourceType | None = None
    operation: str | None = None
    probability: float = Field(1.0, ge=0.0, le=1.0)
    params: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float | None = Field(None, gt=0)


class FaultRule(BaseModel):
    id: str
    fault_type: FaultType
    resource_type: ResourceType | None = None
    operation: str | None = None
    probability: float = 1.0
    params: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    expires_at: str | None = None
    hits: int = 0

    def matches(self, resource_type: ResourceType | None, operation: str | None) -> bool:
        """Return True when this rule applies to the given resource and operation."""
        if self.expired():
            return False
        if self.resource_type is not None and self.resource_type != resource_type:
            return False
        if self.operation is not None and self.operation != operation:
            return False
        return True

    def expired(self, now: datetime | None = None) -> bool:
        """Return True when the rule's time window has closed."""
        if self.expires_at is None:
            return False
        moment = now or datetime.now(timezone.utc)
        expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        return expiry <= moment


class FaultInjector:
    """Registry of active fault rules."""

    def __init__(self) -> None:
        self._rules: dict[str, FaultRule] = {}

    def register(self, request: FaultRuleCreateRequest) -> FaultRule:
        """Register a rule and return it."""
        expires_at = None
        if request.duration_seconds is not None:
            deadline = datetime.now(timezone.utc) + timedelta(seconds=request.duration_seconds)
            expires_at = deadline.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        rule = FaultRule(
            id=new_id("fault"),
            fault_type=request.fault_type,
            resource_type=request.resource_type,
            operation=request.operation,
            probability=request.probability,
            params=dict(request.params),
            created_at=utc_now(),
            expires_at=expires_at,
        )
        self._rules[rule.id] = rule
        return rule

    def list(self) -> list[FaultRule]:
        """Return every registered rule, oldest first."""
        return list(self._rules.values())

    def get(self, rule_id: str) -> FaultRule | None:
        """Return one rule, or None when unknown."""
        return self._rules.get(rule_id)

    def clear(self, rule_id: str) -> bool:
        """Delete one rule; return whether it existed."""
        return self._rules.pop(rule_id, None) is not None

    def clear_all(self) -> int:
        """Delete every rule and return how many were removed."""
        removed = len(self._rules)
        self._rules.clear()
        return removed

    def resolve(
        self,
        resource_type: ResourceType,
        operation: str,
        fault_types: set[str] | None = None,
    ) -> FaultRule | None:
        """Return the first live rule that matches and passes its dice roll."""
        wanted = _normalise(fault_types)
        for rule in self._rules.values():
            if wanted is not None and rule.fault_type.value not in wanted:
                continue
            if not rule.matches(resource_type, operation):
                continue
            if random.random() <= rule.probability:
                rule.hits += 1
                return rule
        return None


def _normalise(fault_types: set[str] | None) -> set[str] | None:
    if fault_types is None:
        return None
    return {item.value if isinstance(item, FaultType) else str(item) for item in fault_types}


injector = FaultInjector()
