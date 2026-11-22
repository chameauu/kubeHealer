"""Subnet endpoints."""

from fastapi import APIRouter, status
from pydantic import BaseModel

from models import Resource, ResourceType, SubnetCreateRequest
from provisioning import ResourceNotFoundError, service
from store import store

router = APIRouter(prefix="/subnets", tags=["subnets"])


class SubnetList(BaseModel):
    total: int
    subnets: list[Resource]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=Resource)
async def create_subnet(request: SubnetCreateRequest) -> Resource:
    """Start creating a subnet; it is `creating` until the engine lands it."""
    return await service.create_subnet(request)


@router.get("", response_model=SubnetList)
async def list_subnets() -> SubnetList:
    subnets = await store.list(ResourceType.SUBNET)
    return SubnetList(total=len(subnets), subnets=subnets)


@router.get("/{subnet_id}", response_model=Resource)
async def get_subnet(subnet_id: str) -> Resource:
    subnet = await store.get(subnet_id)
    if subnet is None or subnet.type != ResourceType.SUBNET:
        raise ResourceNotFoundError(f"subnet {subnet_id} not found")
    return subnet


@router.delete("/{subnet_id}", status_code=status.HTTP_202_ACCEPTED, response_model=Resource)
async def delete_subnet(subnet_id: str) -> Resource:
    """Delete an active, failed or degraded subnet."""
    return await service.delete_subnet(subnet_id)
