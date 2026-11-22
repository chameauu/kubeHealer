"""The lifecycle table and its ordering guarantees."""

import asyncio

import pytest

from config import config
from models import ResourceType
from lifecycle import TRANSITIONS
from provisioning import service
from models import VolumeCreateRequest
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
            raise AssertionError(f"{resource_id} did not reach {states} (last: {last})")
        await asyncio.sleep(0.01)


EXPECTED = {
    (ResourceType.VOLUME, "create"): ("creating", "available"),
    (ResourceType.VOLUME, "attach"): ("attaching", "in_use"),
    (ResourceType.VOLUME, "detach"): ("detaching", "available"),
    (ResourceType.VOLUME, "delete"): ("deleting", "deleted"),
    (ResourceType.SUBNET, "create"): ("creating", "active"),
    (ResourceType.SUBNET, "delete"): ("deleting", "deleted"),
    (ResourceType.INSTANCE, "provision"): ("provisioning", "running"),
    (ResourceType.INSTANCE, "deprovision"): ("deprovisioning", "terminated"),
}


def test_transition_table_is_complete() -> None:
    assert set(TRANSITIONS) == set(EXPECTED)
    for key, (transitional, terminal) in EXPECTED.items():
        assert TRANSITIONS[key][0] == transitional
        assert TRANSITIONS[key][1] == terminal


async def test_transitional_state_is_visible_before_terminal(monkeypatch) -> None:
    monkeypatch.setattr(config, "LIFECYCLE_SPEED", 1.0)

    volume = await service.create_volume(VolumeCreateRequest(name="slow", size_gb=10))

    assert volume.state == "creating"


async def test_every_state_change_emits_an_event(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        "lifecycle.publish_event", lambda *args, **kwargs: events.append((args, kwargs))
    )

    volume = await service.create_volume(VolumeCreateRequest(name="vol", size_gb=10))

    # The transitional event accompanies the transitional write, nothing more.
    assert [kwargs["payload"]["state"] for _, kwargs in events] == ["creating"]

    await _wait_for_state(volume.id, "available")

    assert [kwargs["payload"]["state"] for _, kwargs in events] == ["creating", "available"]
    assert all(args[0] == "sim.lifecycle.transition" for args, _ in events)
