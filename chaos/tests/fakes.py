"""In-memory doubles for the two chaos backends.

The engine only ever sees the client seams, so these doubles are the whole
backend contract: what is applied, what is deleted, what comes back. They
record every call because several behaviours are about *when* a call happens,
not only about what it returns.

All client methods are coroutines: the engine performs backend I/O and the
real clients await httpx, so the doubles must be awaitable too.
"""

from __future__ import annotations

import copy
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

Manifest = dict[str, Any]
ApplyHook = Callable[[Manifest], Awaitable[None] | None]


class FakeChaosMeshClient:
    """A Chaos Mesh apiserver that keeps objects in a dict keyed by (namespace, name)."""

    def __init__(self, *, on_apply: ApplyHook | None = None) -> None:
        self.apply_calls: list[Manifest] = []
        self.get_calls: list[tuple[str, str, str]] = []
        self.delete_calls: list[tuple[str, str, str]] = []
        self.objects: dict[tuple[str, str], Manifest] = {}
        self.on_apply = on_apply

    async def apply(self, manifest: Manifest) -> tuple[str, Manifest]:
        stored_request = copy.deepcopy(manifest)
        self.apply_calls.append(stored_request)
        if self.on_apply is not None:
            observed = self.on_apply(stored_request)
            if inspect.isawaitable(observed):
                await observed
        metadata = stored_request["metadata"]
        name = metadata["name"]
        stored = copy.deepcopy(stored_request)
        stored["status"] = {
            "experiment": {"phase": "Running"},
            "conditions": [{"type": "Injected", "status": "True"}],
            "instances": ["orders-6f7c9-abcde"],
        }
        self.objects[(metadata["namespace"], name)] = stored
        return name, copy.deepcopy(stored)

    async def get(self, fault_type: str, name: str, namespace: str) -> Manifest | None:
        self.get_calls.append((fault_type, name, namespace))
        stored = self.objects.get((namespace, name))
        return copy.deepcopy(stored) if stored is not None else None

    async def delete(self, fault_type: str, name: str, namespace: str) -> None:
        self.delete_calls.append((fault_type, name, namespace))
        self.objects.pop((namespace, name), None)

    def forget(self, namespace: str, name: str) -> None:
        """Drop an object without recording a call: the CR vanished on its own."""
        self.objects.pop((namespace, name), None)

    def recover(self, namespace: str, name: str) -> None:
        """Report full recovery on a CR that stays in the cluster.

        Chaos Mesh reports `AllRecovered: True` at the end of an experiment but
        keeps the object until someone deletes it; that gap is the defect these
        tests pin down.
        """
        stored = self.objects.get((namespace, name))
        if stored is not None:
            stored.setdefault("status", {})["conditions"] = [
                {"type": "Injected", "status": "True"},
                {"type": "AllRecovered", "status": "True"},
            ]


class ExplodingChaosMeshClient(FakeChaosMeshClient):
    """Every apply is refused by the admission webhook."""

    async def apply(self, manifest: Manifest) -> tuple[str, Manifest]:
        self.apply_calls.append(copy.deepcopy(manifest))
        raise RuntimeError("admission webhook denied the request")


class FailingDeleteChaosMeshClient(FakeChaosMeshClient):
    """The apiserver disappears between apply and delete."""

    async def delete(self, fault_type: str, name: str, namespace: str) -> None:
        self.delete_calls.append((fault_type, name, namespace))
        raise RuntimeError("apiserver unavailable")


class FakeSimulatorClient:
    """The `sim` fault registry: ids are `fault-fakeNNNN`, clearing is idempotent."""

    def __init__(self) -> None:
        self.register_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []
        self.clear_calls: list[str] = []
        self.rules: dict[str, dict[str, Any]] = {}
        self._counter = 0

    async def register_fault(self, request: dict[str, Any]) -> dict[str, Any]:
        self.register_calls.append(copy.deepcopy(request))
        self._counter += 1
        rule: dict[str, Any] = {
            "id": f"fault-fake{self._counter:04d}",
            "fault_type": request.get("fault_type"),
            "resource_type": request.get("resource_type"),
            "operation": request.get("operation"),
            "probability": request.get("probability", 1.0),
            "params": copy.deepcopy(request.get("params") or {}),
            "created_at": "2026-01-01T00:00:00.000Z",
            "expires_at": None,
            "hits": 0,
        }
        self.rules[rule["id"]] = rule
        return copy.deepcopy(rule)

    async def get_fault(self, rule_id: str) -> dict[str, Any] | None:
        self.get_calls.append(rule_id)
        rule = self.rules.get(rule_id)
        return copy.deepcopy(rule) if rule is not None else None

    async def clear_fault(self, rule_id: str) -> None:
        self.clear_calls.append(rule_id)
        self.rules.pop(rule_id, None)

    def forget(self, rule_id: str) -> None:
        """Expire a rule without recording a call: the simulator dropped it by itself."""
        self.rules.pop(rule_id, None)


class ExplodingSimulatorClient(FakeSimulatorClient):
    """The simulator is unreachable."""

    async def register_fault(self, request: dict[str, Any]) -> dict[str, Any]:
        self.register_calls.append(copy.deepcopy(request))
        raise RuntimeError("simulator unreachable")
