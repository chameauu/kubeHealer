"""HTTP surface of the fault library."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from aggregator import build_rankings
from catalog import all_entries, for_domain, lookup
from chaos_client import ChaosClientError, chaos
from classifier import classify
from models import (
    CatalogResponse,
    Classification,
    FaultCatalogEntry,
    RankingsResponse,
    ScenarioDomain,
)

router = APIRouter(tags=["library"])


@router.get("/catalog", response_model=CatalogResponse)
async def list_catalog(domain: ScenarioDomain | None = None) -> CatalogResponse:
    """Every catalogued entry, optionally restricted to one domain."""
    entries = all_entries() if domain is None else for_domain(domain)
    return CatalogResponse(entries=entries, total=len(entries))


@router.get("/catalog/{domain}/{fault_type}", response_model=FaultCatalogEntry)
async def get_entry(domain: ScenarioDomain, fault_type: str) -> FaultCatalogEntry:
    """One catalogue entry; 404 when the pair is not catalogued."""
    entry = lookup(domain, fault_type)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"no fault library entry for {domain.value}/{fault_type}",
        )
    return entry


@router.post("/classify", response_model=Classification)
async def classify_fault(
    domain: Annotated[ScenarioDomain, Query()],
    fault_type: Annotated[str, Query()],
) -> Classification:
    """Classify a pair by lookup; 404 rather than a heuristic guess."""
    result = classify(domain, fault_type)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"no fault library entry for {domain.value}/{fault_type}",
        )
    return result


@router.get("/rankings", response_model=RankingsResponse)
async def rankings() -> RankingsResponse:
    """Rank components from live chaos history; a dead engine is a 502."""
    try:
        scenarios = await chaos.list_scenarios()
    except ChaosClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return build_rankings(scenarios)
