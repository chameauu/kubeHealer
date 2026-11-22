"""Minimal Kubernetes read client for the topology builder.

Only the three list endpoints the graph needs are implemented. Requests use the
in-cluster ServiceAccount token and CA when they exist; anything else (a missing
token, an unreachable API, a non-200) raises `K8sReadError`, which the builder
reports as a blind source instead of turning into a 500.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from config import config

SERVICE_ACCOUNT_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
TOKEN_FILE = SERVICE_ACCOUNT_DIR / "token"
CA_FILE = SERVICE_ACCOUNT_DIR / "ca.crt"


class K8sReadError(RuntimeError):
    """The Kubernetes API could not answer a topology read."""


def _service_row(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    return {
        "name": metadata.get("name", ""),
        "namespace": metadata.get("namespace", ""),
        "labels": dict(metadata.get("labels") or {}),
        "selector": dict(spec.get("selector") or {}),
        "cluster_ip": spec.get("clusterIP"),
    }


def _pod_env(spec: dict[str, Any]) -> dict[str, str]:
    env: dict[str, str] = {}
    for container in spec.get("containers") or []:
        for entry in container.get("env") or []:
            name = entry.get("name")
            if name and "value" in entry:
                env.setdefault(name, str(entry["value"]))
    return env


def _pod_row(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    owners = metadata.get("ownerReferences") or []
    owner = owners[0] if owners else {}
    return {
        "name": metadata.get("name", ""),
        "namespace": metadata.get("namespace", ""),
        "labels": dict(metadata.get("labels") or {}),
        "owner_name": owner.get("name", ""),
        "owner_kind": owner.get("kind", ""),
        "node_name": spec.get("nodeName", ""),
        "env": _pod_env(spec),
    }


def _replicaset_row(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    owners = metadata.get("ownerReferences") or []
    deployment = next(
        (owner.get("name") for owner in owners if owner.get("kind") == "Deployment"),
        None,
    )
    return {
        "name": metadata.get("name", ""),
        "namespace": metadata.get("namespace", ""),
        "deployment_name": deployment,
    }


class K8sClient:
    """Read Services, Pods and ReplicaSets from the Kubernetes API."""

    def __init__(self, api_url: str | None = None, timeout: float | None = None) -> None:
        self.api_url = (api_url or config.K8S_API_URL).rstrip("/")
        self.timeout = timeout or config.SOURCE_TIMEOUT_SECONDS

    def _headers(self) -> dict[str, str]:
        try:
            token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return {}
        return {"Authorization": f"Bearer {token}"}

    def _verify(self) -> str | bool:
        return str(CA_FILE) if CA_FILE.exists() else True

    async def _list(self, path: str) -> list[dict[str, Any]]:
        url = f"{self.api_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=self._verify()) as client:
                response = await client.get(url, headers=self._headers())
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise K8sReadError(f"GET {url} failed: {exc}") from exc
        return list(payload.get("items") or [])

    async def list_services(self) -> list[dict[str, Any]]:
        rows = await self._list("/api/v1/services")
        return [_service_row(row) for row in rows]

    async def list_pods(self) -> list[dict[str, Any]]:
        rows = await self._list("/api/v1/pods")
        return [_pod_row(row) for row in rows]

    async def list_replicasets(self) -> list[dict[str, Any]]:
        rows = await self._list("/apis/apps/v1/replicasets")
        return [_replicaset_row(row) for row in rows]
