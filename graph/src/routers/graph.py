"""Topology and blast-radius endpoints.

The clients and the runtime config are module-level objects and are read on
every request, so a test or an operator can replace either without rebuilding
the app. A blind source is reported by `topology_sources`, never turned into a
5xx here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from blast_radius import compute_blast_radius
from config import config
from demo_topology import build_demo_topology
from hubble_client import HubbleClient
from k8s_client import K8sClient
from models import BlastRadiusResponse, TopologyResponse
from topology_builder import build_topology

router = APIRouter(tags=["graph"])

k8s = K8sClient()
hubble = HubbleClient()


def _parse_selectors(selectors: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in selectors:
        if "=" not in item:
            raise HTTPException(
                status_code=422,
                detail=f"selector {item!r} must look like key=value",
            )
        key, value = item.split("=", 1)
        if not key:
            raise HTTPException(
                status_code=422,
                detail=f"selector {item!r} has an empty key",
            )
        parsed[key] = value
    return parsed


async def _current_topology() -> TopologyResponse:
    if config.LOCAL_DEMO:
        return build_demo_topology()
    return await build_topology(k8s, hubble)


@router.get("/topology", response_model=TopologyResponse)
async def get_topology() -> TopologyResponse:
    return await _current_topology()


@router.get("/blast-radius", response_model=BlastRadiusResponse)
async def get_blast_radius(
    target_namespace: str,
    fault_type: str,
    selector: Annotated[list[str], Query()] = [],
) -> BlastRadiusResponse:
    parsed = _parse_selectors(selector)
    topology = await _current_topology()
    return compute_blast_radius(topology, target_namespace, parsed, fault_type)
