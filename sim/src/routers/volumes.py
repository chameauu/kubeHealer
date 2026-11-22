"""Volume endpoints."""

from fastapi import APIRouter, status
from pydantic import BaseModel

from models import Resource, ResourceType, VolumeAttachRequest, VolumeCreateRequest
from provisioning import ResourceNotFoundError, service
from store import store

router = APIRouter(prefix="/volumes", tags=["volumes"])


class VolumeList(BaseModel):
    total: int
    volumes: list[Resource]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=Resource)
async def create_volume(request: VolumeCreateRequest) -> Resource:
    """Start creating a volume; it is `creating` until the engine lands it."""
    return await service.create_volume(request)


@router.get("", response_model=VolumeList)
async def list_volumes() -> VolumeList:
    volumes = await store.list(ResourceType.VOLUME)
    return VolumeList(total=len(volumes), volumes=volumes)


@router.get("/{volume_id}", response_model=Resource)
async def get_volume(volume_id: str) -> Resource:
    volume = await store.get(volume_id)
    if volume is None or volume.type != ResourceType.VOLUME:
        raise ResourceNotFoundError(f"volume {volume_id} not found")
    return volume


@router.post("/{volume_id}/attach", status_code=status.HTTP_202_ACCEPTED, response_model=Resource)
async def attach_volume(volume_id: str, request: VolumeAttachRequest) -> Resource:
    """Attach an available volume to an existing instance."""
    return await service.attach_volume(volume_id, request)


@router.post("/{volume_id}/detach", status_code=status.HTTP_202_ACCEPTED, response_model=Resource)
async def detach_volume(volume_id: str) -> Resource:
    """Detach an in-use volume."""
    return await service.detach_volume(volume_id)


@router.delete("/{volume_id}", status_code=status.HTTP_202_ACCEPTED, response_model=Resource)
async def delete_volume(volume_id: str) -> Resource:
    """Delete an available, failed or degraded volume."""
    return await service.delete_volume(volume_id)
