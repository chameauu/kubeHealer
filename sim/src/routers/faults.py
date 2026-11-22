"""Fault-rule endpoints.

Rules are not resources: registering one returns 201, deleting one returns 204
with no body.
"""

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from faults import FaultRule, FaultRuleCreateRequest, injector
from provisioning import ResourceNotFoundError

router = APIRouter(prefix="/faults", tags=["faults"])


class FaultRuleList(BaseModel):
    total: int
    rules: list[FaultRule]


class ClearedResponse(BaseModel):
    cleared: int


@router.post("", status_code=status.HTTP_201_CREATED, response_model=FaultRule)
async def register_fault(request: FaultRuleCreateRequest) -> FaultRule:
    """Register a rule that the injector will consider for every matching call."""
    return injector.register(request)


@router.get("", response_model=FaultRuleList)
async def list_faults() -> FaultRuleList:
    rules = injector.list()
    return FaultRuleList(total=len(rules), rules=rules)


@router.delete("", response_model=ClearedResponse)
async def clear_faults() -> ClearedResponse:
    """Remove every rule."""
    return ClearedResponse(cleared=injector.clear_all())


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def clear_fault(rule_id: str) -> Response:
    """Remove one rule."""
    if not injector.clear(rule_id):
        raise ResourceNotFoundError(f"fault rule {rule_id} not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
