"""Scenario endpoints: generate and run. Both are 200-or-422 by contract."""

from fastapi import APIRouter, HTTPException

from engine import generate_scenarios, run_scenario
from events import log_event
from models import GenerateRequest, RunRequest, RunResult, Scenario

router = APIRouter(tags=["scenarios"])


@router.post("/scenarios/generate")
def generate(request: GenerateRequest) -> list[Scenario]:
    try:
        scenarios = generate_scenarios(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    log_event(
        "scenarios.generated",
        count=len(scenarios),
        journey=request.journey,
        seed=request.seed,
    )
    return scenarios


@router.post("/scenarios/run")
def run(request: RunRequest) -> RunResult:
    result = run_scenario(request)
    log_event(
        "scenario.ran",
        scenario=result.scenario.name,
        status=result.status,
        aborted=result.aborted,
    )
    return result
