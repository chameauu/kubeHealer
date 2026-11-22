"""Provisioning verbs: the only place that decides whether a request may start.

Routers translate the three exceptions below into HTTP status codes; nothing
else in the service raises them. Quota is checked before anything is written so
a rejected request leaves no trace.
"""

from __future__ import annotations

import asyncio

from config import config
from events import Severity, publish_event
from faults import injector
from lifecycle import engine
from models import (
    InstanceProvisionRequest,
    Resource,
    ResourceType,
    SubnetCreateRequest,
    VolumeAttachRequest,
    VolumeCreateRequest,
    new_instance,
    new_subnet,
    new_volume,
)
from store import store

IN_FLIGHT_FAULTS: set[str] = {"latency", "transient_error", "partial_failure"}
QUOTA_FAULTS: set[str] = {"quota_limit"}


class ResourceNotFoundError(Exception):
    """The resource (or a resource it references) does not exist."""


class InvalidStateError(Exception):
    """The resource is not in a state that allows this operation."""


class QuotaExceededError(Exception):
    """A count or injected quota limit refuses the request."""


class ProvisioningService:
    """Creates resources and hands them to the lifecycle engine."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    async def create_volume(self, request: VolumeCreateRequest) -> Resource:
        await self._check_create_quota(ResourceType.VOLUME)
        volume = new_volume(request)
        await store.put(volume)
        await self._launch(volume, "create")
        return volume

    async def attach_volume(self, volume_id: str, request: VolumeAttachRequest) -> Resource:
        volume = await self._require(volume_id, ResourceType.VOLUME)
        if volume.state != "available":
            raise InvalidStateError(
                f"volume {volume.id} is {volume.state}; attach requires available"
            )
        await self._require(request.instance_id, ResourceType.INSTANCE)
        volume.touch(attached_to=request.instance_id, device=request.device)
        await store.put(volume)
        await self._launch(volume, "attach")
        return volume

    async def detach_volume(self, volume_id: str) -> Resource:
        volume = await self._require(volume_id, ResourceType.VOLUME)
        if volume.state != "in_use":
            raise InvalidStateError(
                f"volume {volume.id} is {volume.state}; detach requires in_use"
            )
        volume.touch(attached_to=None, device=None)
        await store.put(volume)
        await self._launch(volume, "detach")
        return volume

    async def delete_volume(self, volume_id: str) -> Resource:
        volume = await self._require(volume_id, ResourceType.VOLUME)
        if volume.state not in {"available", "error", "degraded"}:
            raise InvalidStateError(
                f"volume {volume.id} is {volume.state}; cannot delete"
            )
        await self._launch(volume, "delete")
        return volume

    async def create_subnet(self, request: SubnetCreateRequest) -> Resource:
        await self._check_create_quota(ResourceType.SUBNET)
        subnet = new_subnet(request)
        await store.put(subnet)
        await self._launch(subnet, "create")
        return subnet

    async def delete_subnet(self, subnet_id: str) -> Resource:
        subnet = await self._require(subnet_id, ResourceType.SUBNET)
        if subnet.state not in {"active", "error", "degraded"}:
            raise InvalidStateError(f"subnet {subnet.id} is {subnet.state}; cannot delete")
        await self._launch(subnet, "delete")
        return subnet

    async def provision_instance(self, request: InstanceProvisionRequest) -> Resource:
        await self._check_create_quota(ResourceType.INSTANCE)
        if request.subnet_id is not None:
            await self._require(request.subnet_id, ResourceType.SUBNET)
        instance = new_instance(request)
        await store.put(instance)
        await self._launch(instance, "provision")
        return instance

    async def deprovision_instance(self, instance_id: str) -> Resource:
        instance = await self._require(instance_id, ResourceType.INSTANCE)
        if instance.state not in {"running", "error", "degraded"}:
            raise InvalidStateError(
                f"instance {instance.id} is {instance.state}; cannot deprovision"
            )
        await self._launch(instance, "deprovision")
        return instance

    async def _require(self, resource_id: str, resource_type: ResourceType) -> Resource:
        """Return a resource of the expected type or raise ResourceNotFoundError."""
        resource = await store.get(resource_id)
        if resource is None or resource.type != resource_type:
            raise ResourceNotFoundError(f"{resource_type.value} {resource_id} not found")
        return resource

    async def _check_create_quota(self, resource_type: ResourceType) -> None:
        """Reject before writing anything when quota is exhausted or injected."""
        operation = "provision" if resource_type is ResourceType.INSTANCE else "create"
        rule = injector.resolve(resource_type, operation, fault_types=QUOTA_FAULTS)
        if rule is not None:
            publish_event(
                "sim.fault.triggered",
                severity=Severity.MEDIUM,
                component=f"sim.{resource_type.value}",
                payload={
                    "resource_type": resource_type.value,
                    "operation": operation,
                    "fault_type": rule.fault_type.value,
                },
                caused_by=rule.id,
                reason="quota_limit",
            )
            raise QuotaExceededError(f"{resource_type.value} quota is exhausted (injected)")

        quota = _quota_for(resource_type)
        current = await store.count(resource_type)
        if current >= quota:
            publish_event(
                "sim.lifecycle.rejected",
                severity=Severity.LOW,
                component=f"sim.{resource_type.value}",
                payload={
                    "resource_type": resource_type.value,
                    "operation": operation,
                    "count": current,
                    "quota": quota,
                },
                reason="quota_exceeded",
            )
            raise QuotaExceededError(
                f"{resource_type.value} quota reached ({current}/{quota})"
            )

    async def _launch(self, resource: Resource, operation: str) -> None:
        """Announce the transition and return without waiting for the delay."""
        fault = injector.resolve(resource.type, operation, fault_types=IN_FLIGHT_FAULTS)
        task = await engine.start(resource, operation, fault)
        self._tasks.add(task)
        task.add_done_callback(self._retire)

    def _retire(self, task: asyncio.Task[None]) -> None:
        """Forget a finished transition and surface any real failure."""
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            publish_event(
                "sim.lifecycle.error",
                severity=Severity.HIGH,
                component="sim.lifecycle",
                payload={"error": str(error), "error_type": type(error).__name__},
            )


def _quota_for(resource_type: ResourceType) -> int:
    return {
        ResourceType.VOLUME: config.VOLUME_QUOTA,
        ResourceType.SUBNET: config.SUBNET_QUOTA,
        ResourceType.INSTANCE: config.INSTANCE_QUOTA,
    }[resource_type]


service = ProvisioningService()
