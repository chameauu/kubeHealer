"""Outbound calls: graph, faultlib, chaos, and the Kubernetes write path.

The graph query is frozen by CONTRACT D1:

    GET /blast-radius?target_namespace=<ns>&fault_type=<type>
                     &selector=k1=v1&selector=k2=v2

Labels are sorted by key, each becomes its own repeated `selector` pair, and
httpx percent-encodes the values. A blind source is reported (logged) and
degraded to an empty mapping, never fabricated and never raised, so detection
can continue with partial evidence.

The write path talks to the Kubernetes API directly, authenticated with the
pod's ServiceAccount; it never shells out to `kubectl`, which the image does
not ship.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from config import config
from events import log_event

HTTP_TIMEOUT_SECONDS = 15.0
# 15 s, not 3 s: inside a cluster pod an AF_UNSPEC resolution of a Service name
# has been measured at ~2 s per call (the AAAA lookup waits for an upstream that
# does not answer IPv6, and CoreDNS's search domains widen the wait). With 3 s the
# first call could time out and detection silently degraded to `{}`, which then
# read as "no evidence" instead of "backend unreachable".

# Tests and operators replace these; `None` means "open a short-lived client".
graph_client: Any = None
faultlib_client: Any = None
chaos_client: Any = None

# The Kubernetes clients are built once per process; tests inject fakes here.
_apps_api: Any = None
_core_api: Any = None


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


def _api_clients() -> tuple[Any, Any]:
    """Return the apps and core clients, building them once from the pod config."""
    global _apps_api, _core_api
    if _apps_api is None or _core_api is None:
        import kubernetes

        try:
            kubernetes.config.load_incluster_config()
        except Exception:
            kubernetes.config.load_kube_config()
        if _apps_api is None:
            _apps_api = kubernetes.client.AppsV1Api()
        if _core_api is None:
            _core_api = kubernetes.client.CoreV1Api()
    return _apps_api, _core_api


async def restart_deployment(namespace: str, name: str) -> dict[str, Any]:
    """Roll a deployment by stamping the pod template, not just the object."""
    restarted_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
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
    try:
        apps_api, _ = _api_clients()
        apps_api.patch_namespaced_deployment(
            name=name, namespace=namespace, body=patch
        )
    except Exception as exc:
        raise ToolError(f"deployment patch failed: {exc}") from exc
    return {"namespace": namespace, "deployment": name, "restarted_at": restarted_at}


async def scale_deployment(
    namespace: str, name: str, replicas: int
) -> dict[str, Any]:
    """Set a deployment's replica count."""
    body = {"spec": {"replicas": int(replicas)}}
    try:
        apps_api, _ = _api_clients()
        apps_api.patch_namespaced_deployment_scale(
            name=name, namespace=namespace, body=body
        )
    except Exception as exc:
        raise ToolError(f"deployment scale failed: {exc}") from exc
    return {"namespace": namespace, "deployment": name, "replicas": int(replicas)}


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _selector_text(selector: Any) -> str | None:
    """Render a deployment's `matchLabels` as a `k=v,k=v` label selector."""
    labels = getattr(selector, "match_labels", None) or {}
    if not labels:
        return None
    return ",".join(f"{key}={labels[key]}" for key in sorted(labels))


def _count_ready_pods(pods: Any) -> int:
    """Count pods that are `Running` with a `Ready=True` condition."""
    count = 0
    for pod in getattr(pods, "items", None) or []:
        status = getattr(pod, "status", None)
        if getattr(status, "phase", None) != "Running":
            continue
        conditions = getattr(status, "conditions", None) or []
        ready = any(
            getattr(condition, "type", None) == "Ready"
            and str(getattr(condition, "status", "")).lower() == "true"
            for condition in conditions
        )
        if ready:
            count += 1
    return count


async def get_workload_health(namespace: str, deployment: str) -> dict[str, Any]:
    """Read the deployment's replica truth for verification.

    The deployment status is the authoritative signal: if the pod list fails,
    `ready_pods` falls back to `status.readyReplicas` instead of failing the
    whole read. Only an unreadable deployment is a `ToolError`.
    """
    try:
        apps_api, core_api = _api_clients()
        document = apps_api.read_namespaced_deployment(
            name=deployment, namespace=namespace
        )
        spec = getattr(document, "spec", None)
        status = getattr(document, "status", None)
        desired = _as_int(getattr(spec, "replicas", None))
        ready = _as_int(getattr(status, "ready_replicas", None))
        available = _as_int(getattr(status, "available_replicas", None))
        try:
            label_selector = _selector_text(getattr(spec, "selector", None))
            if label_selector is None:
                ready_pods = ready
            else:
                pods = core_api.list_namespaced_pod(
                    namespace, label_selector=label_selector
                )
                ready_pods = _count_ready_pods(pods)
        except Exception:
            ready_pods = ready
    except Exception as exc:
        detail = str(exc) or exc.__class__.__name__
        raise ToolError(f"deployment health read failed: {detail}") from exc
    return {
        "namespace": namespace,
        "deployment": deployment,
        "desired": desired,
        "ready": ready,
        "available": available,
        "ready_pods": ready_pods,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

