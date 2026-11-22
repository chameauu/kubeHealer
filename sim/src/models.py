"""Typed documents for the faultable infrastructure simulator.

`sim` is a fake cloud: volumes, subnets and instances that move through a
scripted lifecycle. Every document is a Pydantic model so routers can hand it
straight to FastAPI and tests can assert on it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ResourceType(str, Enum):
    VOLUME = "volume"
    SUBNET = "subnet"
    INSTANCE = "instance"


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 timestamp ending in `Z`."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    """Return a short opaque identifier such as `vol-1a2b3c4d5e`."""
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class Resource(BaseModel):
    """One simulated cloud resource."""

    id: str
    type: ResourceType
    name: str
    state: str
    zone: str
    created_at: str
    updated_at: str
    attributes: dict[str, Any] = Field(default_factory=dict)

    def touch(self, state: str | None = None, **attrs: Any) -> None:
        """Apply a state change and/or attribute updates in place."""
        if state is not None:
            self.state = state
        self.attributes.update(attrs)
        self.updated_at = utc_now()


class VolumeCreateRequest(BaseModel):
    name: str
    size_gb: int = Field(gt=0, le=16384)
    volume_type: str = "standard"
    zone: str = "zone-a"


class VolumeAttachRequest(BaseModel):
    instance_id: str
    device: str = "/dev/sdb"


class SubnetCreateRequest(BaseModel):
    name: str
    cidr: str
    vlan_id: int = Field(ge=1, le=4094)
    zone: str = "zone-a"


class InstanceProvisionRequest(BaseModel):
    name: str
    instance_type: str = "standard.small"
    subnet_id: str | None = None
    zone: str = "zone-a"


def new_volume(request: VolumeCreateRequest) -> Resource:
    """Build a volume in its `creating` state."""
    now = utc_now()
    return Resource(
        id=new_id("vol"),
        type=ResourceType.VOLUME,
        name=request.name,
        state="creating",
        zone=request.zone,
        created_at=now,
        updated_at=now,
        attributes={
            "size_gb": request.size_gb,
            "volume_type": request.volume_type,
            "attached_to": None,
            "device": None,
        },
    )


def new_subnet(request: SubnetCreateRequest) -> Resource:
    """Build a subnet in its `creating` state."""
    now = utc_now()
    return Resource(
        id=new_id("snet"),
        type=ResourceType.SUBNET,
        name=request.name,
        state="creating",
        zone=request.zone,
        created_at=now,
        updated_at=now,
        attributes={"cidr": request.cidr, "vlan_id": request.vlan_id},
    )


def new_instance(request: InstanceProvisionRequest) -> Resource:
    """Build an instance in its `provisioning` state."""
    now = utc_now()
    return Resource(
        id=new_id("inst"),
        type=ResourceType.INSTANCE,
        name=request.name,
        state="provisioning",
        zone=request.zone,
        created_at=now,
        updated_at=now,
        attributes={
            "instance_type": request.instance_type,
            "subnet_id": request.subnet_id,
        },
    )
