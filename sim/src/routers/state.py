"""Whole-simulator state snapshot."""

from typing import Any

from fastapi import APIRouter

from store import store

router = APIRouter(tags=["state"])


@router.get("/state")
async def get_state() -> dict[str, Any]:
    """Return totals plus every resource, grouped by type."""
    return await store.snapshot()
