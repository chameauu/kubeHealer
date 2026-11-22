"""The lifecycle table and the engine that walks it.

A transition is announced twice, once when it starts and once when the resource
actually lands: the transitional state is written before the caller returns, and
the terminal state, its event and any fault event are written after the scripted
delay elapses. Faults bend the landing, never the table.
"""

from __future__ import annotations

import asyncio
import random
from typing import NamedTuple

from config import config
from events import Severity, publish_event
from faults import FaultRule, FaultType
from models import Resource, ResourceType
from store import store

TRANSITIONS: dict[tuple[ResourceType, str], tuple[str, str, tuple[float, float]]] = {
    (ResourceType.VOLUME, "create"): ("creating", "available", (0.4, 1.5)),
    (ResourceType.VOLUME, "attach"): ("attaching", "in_use", (0.2, 0.8)),
    (ResourceType.VOLUME, "detach"): ("detaching", "available", (0.2, 0.8)),
    (ResourceType.VOLUME, "delete"): ("deleting", "deleted", (0.2, 0.6)),
    (ResourceType.SUBNET, "create"): ("creating", "active", (0.4, 1.2)),
    (ResourceType.SUBNET, "delete"): ("deleting", "deleted", (0.2, 0.6)),
    (ResourceType.INSTANCE, "provision"): ("provisioning", "running", (0.8, 2.5)),
    (ResourceType.INSTANCE, "deprovision"): ("deprovisioning", "terminated", (0.4, 1.5)),
}

FAULT_SEVERITY: dict[FaultType, Severity] = {
    FaultType.LATENCY: Severity.LOW,
    FaultType.TRANSIENT_ERROR: Severity.MEDIUM,
    FaultType.PARTIAL_FAILURE: Severity.HIGH,
}


class Transition(NamedTuple):
    """One announced transition, waiting for its delay to elapse."""

    resource: Resource
    operation: str
    final_state: str
    delays: tuple[float, ...]
    fault: FaultRule | None = None


class LifecycleEngine:
    """Drives one resource through one transition, faults included."""

    async def start(
        self, resource: Resource, operation: str, fault: FaultRule | None = None
    ) -> asyncio.Task[None]:
        """Persist and announce the transition, then schedule the landing."""
        transitional, terminal, (low, high) = TRANSITIONS[(resource.type, operation)]
        fault_type = fault.fault_type if fault is not None else None

        previous_state = resource.state
        resource.touch(transitional)
        await store.put(resource)
        publish_event(
            "sim.lifecycle.transition",
            severity=Severity.INFO,
            component=f"sim.{resource.type.value}",
            payload={
                "resource_id": resource.id,
                "resource_type": resource.type.value,
                "operation": operation,
                "state": transitional,
                "previous_state": previous_state,
            },
        )

        base_delay = random.uniform(low, high) * config.LIFECYCLE_SPEED
        delays: list[float] = [base_delay]
        final_state = terminal
        if fault_type is FaultType.TRANSIENT_ERROR:
            delays = [base_delay * 0.5]
            final_state = "error"
        elif fault_type is FaultType.PARTIAL_FAILURE:
            final_state = "degraded"
        elif fault is not None and fault_type is FaultType.LATENCY:
            delays.append(float(fault.params.get("extra_seconds", 5.0)))

        return asyncio.create_task(
            self._settle(Transition(resource, operation, final_state, tuple(delays), fault))
        )

    async def run(self, resource: Resource, operation: str, fault: FaultRule | None = None) -> None:
        """Run one transition to completion; for callers that must wait."""
        task = await self.start(resource, operation, fault)
        await task

    async def _settle(self, transition: Transition) -> None:
        """Wait out the transition's delays, then apply and announce its landing."""
        for delay in transition.delays:
            await asyncio.sleep(delay)

        resource = transition.resource
        transitional = TRANSITIONS[(resource.type, transition.operation)][0]
        resource.touch(transition.final_state)
        await store.put(resource)

        fault = transition.fault
        if fault is not None and fault.fault_type in FAULT_SEVERITY:
            publish_event(
                "sim.fault.triggered",
                severity=FAULT_SEVERITY[fault.fault_type],
                component=f"sim.{resource.type.value}",
                payload={
                    "resource_id": resource.id,
                    "resource_type": resource.type.value,
                    "operation": transition.operation,
                    "fault_type": fault.fault_type.value,
                    "state": transition.final_state,
                },
                caused_by=fault.id,
                reason=fault.fault_type.value,
            )

        publish_event(
            "sim.lifecycle.transition",
            severity=Severity.INFO,
            component=f"sim.{resource.type.value}",
            payload={
                "resource_id": resource.id,
                "resource_type": resource.type.value,
                "operation": transition.operation,
                "state": transition.final_state,
                "previous_state": transitional,
            },
        )


engine = LifecycleEngine()
