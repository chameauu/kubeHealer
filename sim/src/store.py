"""In-memory resource store.

One process, one dict, one lock: the simulator is a fake cloud, so there is no
durability requirement. Copies are handed out so callers cannot mutate state
the store believes it owns; every write goes through `put`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from models import Resource, ResourceType

_TYPE_ORDER: tuple[ResourceType, ...] = (
    ResourceType.VOLUME,
    ResourceType.SUBNET,
    ResourceType.INSTANCE,
)


class ResourceStore:
    """Async-safe collection of resources keyed by id."""

    def __init__(self) -> None:
        self._resources: dict[str, Resource] = {}
        self._lock = asyncio.Lock()

    async def put(self, resource: Resource) -> Resource:
        """Insert or replace a resource."""
        async with self._lock:
            self._resources[resource.id] = resource.model_copy(deep=True)
        return resource

    async def get(self, resource_id: str) -> Resource | None:
        """Return a copy of one resource, or None when unknown."""
        async with self._lock:
            stored = self._resources.get(resource_id)
            return stored.model_copy(deep=True) if stored is not None else None

    async def list(self, resource_type: ResourceType | None = None) -> list[Resource]:
        """Return copies of every resource, oldest first, optionally filtered."""
        async with self._lock:
            selected = [
                resource
                for resource in self._ordered()
                if resource_type is None or resource.type == resource_type
            ]
            return [resource.model_copy(deep=True) for resource in selected]

    async def count(self, resource_type: ResourceType) -> int:
        """Return how many resources of one type exist."""
        async with self._lock:
            return sum(1 for resource in self._resources.values() if resource.type == resource_type)

    async def remove(self, resource_id: str) -> Resource | None:
        """Delete one resource and return the removed document, if any."""
        async with self._lock:
            stored = self._resources.pop(resource_id, None)
            return stored.model_copy(deep=True) if stored is not None else None

    async def clear(self) -> None:
        """Drop every resource."""
        async with self._lock:
            self._resources.clear()

    async def snapshot(self) -> dict[str, Any]:
        """Return totals plus resources grouped by type."""
        async with self._lock:
            totals = {resource_type.value: 0 for resource_type in _TYPE_ORDER}
            grouped: dict[str, list[Resource]] = {
                resource_type.value: [] for resource_type in _TYPE_ORDER
            }
            for resource in self._ordered():
                totals[resource.type.value] += 1
                grouped[resource.type.value].append(resource.model_copy(deep=True))
        return {"totals": totals, "resources": grouped}

    def _ordered(self) -> list[Resource]:
        """Resources sorted by creation time, then id, for a stable listing."""
        return sorted(
            self._resources.values(),
            key=lambda resource: (resource.created_at, resource.id),
        )


store = ResourceStore()
