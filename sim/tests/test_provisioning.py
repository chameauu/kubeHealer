"""Lifecycle and fault behaviour, asserted through the service layer.

Transitions are awaited directly here instead of being raced through the HTTP
client, so the tests are deterministic.
"""

import asyncio

import pytest

from config import config
from faults import FaultRuleCreateRequest, injector
from models import (
    InstanceProvisionRequest,
    ResourceType,
    SubnetCreateRequest,
    VolumeAttachRequest,
    VolumeCreateRequest,
)
from provisioning import (
    InvalidStateError,
    QuotaExceededError,
    ResourceNotFoundError,
    service,
)
from store import store


async def _wait_for_state(resource_id: str, *states: str, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        resource = await store.get(resource_id)
        if resource is not None and resource.state in states:
            return
        if loop.time() > deadline:
            last = resource.state if resource else "missing"
            raise AssertionError(f"{resource_id} did not reach {states} within {timeout}s (last: {last})")
        await asyncio.sleep(0.01)


class TestVolumeLifecycle:
    async def test_create_attach_detach_delete(self) -> None:
        instance = await service.provision_instance(InstanceProvisionRequest(name="host"))
        await _wait_for_state(instance.id, "running")

        volume = await service.create_volume(VolumeCreateRequest(name="vol", size_gb=50))
        assert volume.state == "creating"
        await _wait_for_state(volume.id, "available")

        await service.attach_volume(volume.id, VolumeAttachRequest(instance_id=instance.id))
        await _wait_for_state(volume.id, "in_use")
        attached = await store.get(volume.id)
        assert attached is not None
        assert attached.attributes["attached_to"] == instance.id

        await service.detach_volume(volume.id)
        await _wait_for_state(volume.id, "available")
        detached = await store.get(volume.id)
        assert detached is not None
        assert detached.attributes["attached_to"] is None

        await service.delete_volume(volume.id)
        await _wait_for_state(volume.id, "deleted")

    async def test_attach_requires_existing_instance(self) -> None:
        volume = await service.create_volume(VolumeCreateRequest(name="vol", size_gb=10))
        await _wait_for_state(volume.id, "available")

        with pytest.raises(ResourceNotFoundError):
            await service.attach_volume(volume.id, VolumeAttachRequest(instance_id="inst-ghost"))

    async def test_cannot_delete_twice(self) -> None:
        volume = await service.create_volume(VolumeCreateRequest(name="vol", size_gb=10))
        await _wait_for_state(volume.id, "available")
        await service.delete_volume(volume.id)
        await _wait_for_state(volume.id, "deleted")

        with pytest.raises(InvalidStateError):
            await service.delete_volume(volume.id)


class TestSubnetLifecycle:
    async def test_create_then_delete(self) -> None:
        subnet = await service.create_subnet(
            SubnetCreateRequest(name="app", cidr="10.0.2.0/24", vlan_id=200)
        )
        assert subnet.state == "creating"
        await _wait_for_state(subnet.id, "active")

        await service.delete_subnet(subnet.id)
        await _wait_for_state(subnet.id, "deleted")


class TestInstanceLifecycle:
    async def test_provision_then_deprovision(self) -> None:
        instance = await service.provision_instance(InstanceProvisionRequest(name="host"))
        assert instance.state == "provisioning"
        await _wait_for_state(instance.id, "running")

        await service.deprovision_instance(instance.id)
        await _wait_for_state(instance.id, "terminated")

    async def test_provision_into_subnet_records_link(self) -> None:
        subnet = await service.create_subnet(
            SubnetCreateRequest(name="app", cidr="10.0.3.0/24", vlan_id=300)
        )
        await _wait_for_state(subnet.id, "active")

        instance = await service.provision_instance(
            InstanceProvisionRequest(name="host", subnet_id=subnet.id)
        )

        assert instance.attributes["subnet_id"] == subnet.id


class TestFaultInjection:
    async def test_transient_error_drives_resource_to_error(self) -> None:
        injector.register(
            FaultRuleCreateRequest(
                fault_type="transient_error", resource_type="volume", operation="create"
            )
        )
        volume = await service.create_volume(VolumeCreateRequest(name="vol", size_gb=10))

        await _wait_for_state(volume.id, "error")

    async def test_partial_failure_stalls_mid_transition(self) -> None:
        injector.register(
            FaultRuleCreateRequest(
                fault_type="partial_failure", resource_type="instance", operation="provision"
            )
        )
        instance = await service.provision_instance(InstanceProvisionRequest(name="host"))
        await _wait_for_state(instance.id, "degraded")

        await asyncio.sleep(0.05)
        stalled = await store.get(instance.id)
        assert stalled is not None
        assert stalled.state == "degraded", "partial failure never reaches a terminal state"

    async def test_latency_extends_delay_but_still_completes(self) -> None:
        injector.register(
            FaultRuleCreateRequest(
                fault_type="latency",
                resource_type="subnet",
                operation="create",
                params={"extra_seconds": 0.05},
            )
        )
        subnet = await service.create_subnet(
            SubnetCreateRequest(name="slow", cidr="10.0.4.0/24", vlan_id=400)
        )

        await _wait_for_state(subnet.id, "active", timeout=3.0)

    async def test_quota_limit_fault_rejects_before_resource_exists(self) -> None:
        injector.register(FaultRuleCreateRequest(fault_type="quota_limit", resource_type="volume"))

        with pytest.raises(QuotaExceededError):
            await service.create_volume(VolumeCreateRequest(name="vol", size_gb=10))

        assert await store.count(ResourceType.VOLUME) == 0

    async def test_baseline_quota_rejects_once_limit_reached(self, monkeypatch) -> None:
        monkeypatch.setattr(config, "VOLUME_QUOTA", 1)
        first = await service.create_volume(VolumeCreateRequest(name="v1", size_gb=10))
        await _wait_for_state(first.id, "available")

        with pytest.raises(QuotaExceededError):
            await service.create_volume(VolumeCreateRequest(name="v2", size_gb=10))

        assert await store.count(ResourceType.VOLUME) == 1

    async def test_fault_rules_only_match_their_target(self) -> None:
        injector.register(
            FaultRuleCreateRequest(
                fault_type="transient_error", resource_type="instance", operation="provision"
            )
        )
        volume = await service.create_volume(VolumeCreateRequest(name="vol", size_gb=10))

        await _wait_for_state(volume.id, "available")
