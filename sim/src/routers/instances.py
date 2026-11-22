"""Instance endpoints."""

from fastapi import APIRouter, status
from pydantic import BaseModel

from models import InstanceProvisionRequest, Resource, ResourceType
from provisioning import ResourceNotFoundError, service
from store import store

router = APIRouter(prefix="/instances", tags=["instances"])


class InstanceList(BaseModel):
    total: int
    instances: list[Resource]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=Resource)
async def provision_instance(request: InstanceProvisionRequest) -> Resource:
    """Start provisioning an instance; it is `provisioning` until it is `running`."""
    return await service.provision_instance(request)


@router.get("", response_model=InstanceList)
async def list_instances() -> InstanceList:
    instances = await store.list(ResourceType.INSTANCE)
    return InstanceList(total=len(instances), instances=instances)


@router.get("/{instance_id}", response_model=Resource)
async def get_instance(instance_id: str) -> Resource:
    instance = await store.get(instance_id)
    if instance is None or instance.type != ResourceType.INSTANCE:
        raise ResourceNotFoundError(f"instance {instance_id} not found")
    return instance


@router.delete("/{instance_id}", status_code=status.HTTP_202_ACCEPTED, response_model=Resource)
async def deprovision_instance(instance_id: str) -> Resource:
    """Deprovision a running, failed or degraded instance."""
    return await service.deprovision_instance(instance_id)
