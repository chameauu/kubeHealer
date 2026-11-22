"""Outbound calls: graph, faultlib, chaos, kubectl.

The graph query is frozen by CONTRACT D1:

    GET /blast-radius?target_namespace=<ns>&fault_type=<type>
                     &selector=k1=v1&selector=k2=v2

Labels are sorted by key, each becomes its own repeated `selector` pair, and
httpx percent-encodes the values. A blind source is reported (logged) and
degraded to an empty mapping, never fabricated and never raised, so detection
can continue with partial evidence.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from config import config
from events import log_event

HTTP_TIMEOUT_SECONDS = 3.0

# Tests and operators replace these; `None` means "open a short-lived client".
graph_client: Any = None
faultlib_client: Any = None
chaos_client: Any = None


class ToolError(Exception):
    """A write path failed; the caller records it and does not retry blindly."""


async def _request(
    method: str,
    client_name: str,
    base_url: str,
    path: str,
    *,
    params: Any = None,
    payload: Any = None,
) -> Any:
    """Issue one request through the replaceable client global."""
    client = globals()[client_name]
    owned = client is None
    if owned:
        client = httpx.AsyncClient(base_url=base_url, timeout=HTTP_TIMEOUT_SECONDS)
    try:
        if method == "GET":
            return await client.get(path, params=params)
        return await client.post(path, json=payload)
    finally:
        if owned:
            await client.aclose()


def _selectors(labels: dict[str, Any]) -> list[str]:
    return [f"{key}={value}" for key, value in sorted(labels.items())]


async def get_blast_radius(
    scenario: dict[str, Any], blast: dict[str, Any]
) -> dict[str, Any]:
    """Read the blast radius for a scenario's namespace and labels (D1)."""
    target = scenario.get("target") or {}
    labels = blast or target.get("label_selector") or {}
    params: dict[str, Any] = {
        "target_namespace": target.get("namespace")
        or scenario.get("target_namespace")
        or "",
        "fault_type": scenario.get("fault_type") or "",
    }
    selectors = _selectors(labels)
    if selectors:
        params["selector"] = selectors
    try:
        response = await _request(
            "GET", "graph_client", config.GRAPH_URL, "/blast-radius", params=params
        )
    except Exception as exc:  # a blind source is reported, never guessed
        log_event("agent.tool.graph_unreachable", error=str(exc))
        return {}
    if response.status_code != 200:
        log_event(
            "agent.tool.graph_rejected",
            status_code=response.status_code,
            detail=getattr(response, "text", ""),
        )
        return {}
    try:
        payload = response.json()
    except Exception as exc:
        log_event("agent.tool.graph_unparseable", error=str(exc))
        return {}
    return payload if isinstance(payload, dict) else {}


async def get_catalog_entry(domain: str, fault_type: str) -> dict[str, Any]:
    """Read one faultlib catalogue entry. A 404 is a valid empty answer."""
    path = f"/catalog/{quote(domain, safe='')}/{quote(fault_type, safe='')}"
    try:
        response = await _request(
            "GET", "faultlib_client", config.FAULTLIB_URL, path
        )
    except Exception as exc:
        log_event("agent.tool.faultlib_unreachable", error=str(exc))
        return {}
    if response.status_code != 200:
        log_event(
            "agent.tool.faultlib_missing",
            status_code=response.status_code,
            domain=domain,
            fault_type=fault_type,
        )
        return {}
    try:
        payload = response.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


async def stop_scenario(scenario_id: str) -> dict[str, Any]:
    """Ask chaos to stop a scenario. A 502 is an unreachable backend."""
    path = f"/scenarios/{quote(scenario_id, safe='')}/stop"
    try:
        response = await _request("POST", "chaos_client", config.CHAOS_URL, path)
    except Exception as exc:
        raise ToolError(f"chaos unreachable: {exc}") from exc
    if response.status_code == 502:
        raise ToolError(f"chaos unreachable: {getattr(response, 'text', '')}")
    if response.status_code != 200:
        raise ToolError(
            f"chaos stop failed with {response.status_code}: "
            f"{getattr(response, 'text', '')}"
        )
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def get_scenario(scenario_id: str) -> dict[str, Any]:
    """Read the chaos document; verification polls this."""
    path = f"/scenarios/{quote(scenario_id, safe='')}"
    try:
        response = await _request("GET", "chaos_client", config.CHAOS_URL, path)
    except Exception as exc:
        raise ToolError(f"chaos unreachable: {exc}") from exc
    if response.status_code != 200:
        raise ToolError(
            f"chaos read failed with {response.status_code}: "
            f"{getattr(response, 'text', '')}"
        )
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _run_kubectl(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=True, capture_output=True, text=True)


async def restart_deployment(namespace: str, name: str) -> dict[str, Any]:
    """Roll a deployment by stamping the pod template, not just the object."""
    restarted_at = datetime.now(timezone.utc).isoformat()
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": restarted_at,
                        "kubeheal-agent/restarted-by": "kubeheal-agent",
                    }
                }
            }
        }
    }
    argv = [
        "kubectl",
        "-n",
        namespace,
        "patch",
        "deployment",
        name,
        "--type=merge",
        "-p",
        json.dumps(patch),
    ]
    try:
        _run_kubectl(argv)
    except Exception as exc:
        raise ToolError(f"kubectl patch failed: {exc}") from exc
    return {"namespace": namespace, "deployment": name, "restarted_at": restarted_at}


async def scale_deployment(
    namespace: str, name: str, replicas: int
) -> dict[str, Any]:
    """Set a deployment's replica count."""
    argv = [
        "kubectl",
        "-n",
        namespace,
        "scale",
        "deployment",
        name,
        "--replicas",
        str(replicas),
    ]
    try:
        _run_kubectl(argv)
    except Exception as exc:
        raise ToolError(f"kubectl scale failed: {exc}") from exc
    return {"namespace": namespace, "deployment": name, "replicas": replicas}
