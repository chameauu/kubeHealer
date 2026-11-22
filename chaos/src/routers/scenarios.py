"""Scenario control API: one surface for both backends."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ValidationError

from engine import (
    BackendError,
    InvalidScenarioStateError,
    ScenarioEngine,
    ScenarioNotFoundError,
)
from models import Scenario, ScenarioCreateRequest, ScenarioStatus

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


class ScenarioList(BaseModel):
    total: int
    scenarios: list[Scenario]


def _engine(request: Request) -> ScenarioEngine:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="scenario engine is not running")
    return engine


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Scenario)
async def start_scenario(request: Request, payload: ScenarioCreateRequest) -> Scenario:
    try:
        return await _engine(request).start(payload)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("", response_model=ScenarioList)
async def list_scenarios(
    request: Request,
    scenario_status: Annotated[ScenarioStatus | None, Query(alias="status")] = None,
) -> ScenarioList:
    scenarios = await _engine(request).list(status=scenario_status)
    return ScenarioList(total=len(scenarios), scenarios=scenarios)


@router.get("/{scenario_id}", response_model=Scenario)
async def get_scenario(request: Request, scenario_id: str) -> Scenario:
    try:
        return await _engine(request).get(scenario_id)
    except ScenarioNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{scenario_id}/stop", response_model=Scenario)
async def stop_scenario(request: Request, scenario_id: str) -> Scenario:
    try:
        return await _engine(request).stop(scenario_id)
    except ScenarioNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidScenarioStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete(
    "/{scenario_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def remove_scenario(request: Request, scenario_id: str) -> Response:
    try:
        await _engine(request).remove(scenario_id)
    except ScenarioNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidScenarioStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
