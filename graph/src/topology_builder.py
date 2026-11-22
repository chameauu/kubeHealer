"""Build the Service topology from Kubernetes metadata and Hubble flows.

Edges always point from the dependent workload to the workload it depends on,
so the blast radius can walk them backwards. A workload is resolved from its Pod
through the owning ReplicaSet's deployment name; the workload-to-Service index is
the only way an environment reference or a flow becomes an edge.

A source that raises is reported, never fatal: the response keeps whatever the
other source produced and `topology_sources` omits the blind one.
"""

from __future__ import annotations

from typing import Any, Protocol

from config import config
from env_parser import parse_service_refs
from events import log_event
from models import Edge, Node, TopologyResponse


class K8sSource(Protocol):
    """The slice of the Kubernetes API the builder reads."""

    async def list_services(self) -> list[dict[str, Any]]: ...

    async def list_pods(self) -> list[dict[str, Any]]: ...

    async def list_replicasets(self) -> list[dict[str, Any]]: ...


class FlowSource(Protocol):
    """The slice of Hubble the builder reads."""

    async def get_flows(self, max_flows: int = 1000) -> list[dict[str, Any]]: ...


def _pod_workload(
    pod: dict[str, Any], deployments: dict[tuple[str, str], str | None]
) -> str | None:
    namespace = pod.get("namespace", "")
    owner = pod.get("owner_name")
    if not owner:
        return None
    if pod.get("owner_kind") == "ReplicaSet":
        return deployments.get((namespace, owner))
    if pod.get("owner_kind") == "Deployment":
        return owner
    return None


def _workload_index(
    services: list[dict[str, Any]],
    pods: list[dict[str, Any]],
    deployments: dict[tuple[str, str], str | None],
) -> dict[tuple[str, str], str]:
    """Map `(namespace, workload)` to the Service name that fronts it."""
    service_names = {(row["namespace"], row["name"]) for row in services}
    by_namespace: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(services, key=lambda item: (item["namespace"], item["name"])):
        by_namespace.setdefault(row["namespace"], []).append(row)

    index: dict[tuple[str, str], str] = {}
    for pod in sorted(pods, key=lambda item: (item.get("namespace", ""), item.get("name", ""))):
        workload = _pod_workload(pod, deployments)
        if not workload:
            continue
        key = (pod.get("namespace", ""), workload)
        if key in service_names:
            index[key] = workload
            continue
        labels = pod.get("labels") or {}
        for service in by_namespace.get(pod.get("namespace", ""), []):
            selector = service.get("selector") or {}
            if selector and all(labels.get(name) == value for name, value in selector.items()):
                index[key] = service["name"]
                break
    return index


def _service_of(index: dict[tuple[str, str], str], namespace: str, workload: str) -> str | None:
    return index.get((namespace, workload))


def _env_edges(
    pods: list[dict[str, Any]],
    deployments: dict[tuple[str, str], str | None],
    index: dict[tuple[str, str], str],
    service_names: set[tuple[str, str]],
) -> list[Edge]:
    edges: dict[tuple[str, str, str, str | None], Edge] = {}
    for pod in pods:
        source = _service_of(
            index,
            pod.get("namespace", ""),
            _pod_workload(pod, deployments) or "",
        )
        if source is None:
            continue
        for ref in parse_service_refs(pod.get("env") or {}):
            if (ref["namespace"], ref["name"]) not in service_names:
                continue
            target = ref["name"]
            edge = Edge(
                source=f"{pod['namespace']}/{source}",
                target=f"{ref['namespace']}/{target}",
                edge_type="env_ref",
                env_var=ref["env_var"],
            )
            edges[(edge.source, edge.target, edge.edge_type, edge.env_var)] = edge
    return list(edges.values())


def _flow_edges(
    flows: list[dict[str, Any]], index: dict[tuple[str, str], str]
) -> list[Edge]:
    edges: dict[tuple[str, str, str, str | None], Edge] = {}
    for flow in flows:
        source = _service_of(
            index, flow.get("source_ns", ""), flow.get("source_workload", "")
        )
        target = _service_of(index, flow.get("dest_ns", ""), flow.get("dest_workload", ""))
        if source is None or target is None:
            continue
        source_id = f"{flow['source_ns']}/{source}"
        target_id = f"{flow['dest_ns']}/{target}"
        key = (source_id, target_id, "flow_observed", None)
        existing = edges.get(key)
        if existing is not None:
            existing.flow_count += 1
            continue
        edges[key] = Edge(
            source=source_id,
            target=target_id,
            edge_type="flow_observed",
            flow_count=1,
        )
    return list(edges.values())


async def build_topology(k8s: K8sSource, hubble: FlowSource) -> TopologyResponse:
    """Read both sources and return a deterministic Service topology."""
    k8s_ok = True
    try:
        services = await k8s.list_services()
        pods = await k8s.list_pods()
        replicasets = await k8s.list_replicasets()
    except Exception as exc:  # noqa: BLE001 - a blind source is reported, not fatal
        k8s_ok = False
        services, pods, replicasets = [], [], []
        log_event("topology.source_unavailable", source="kubernetes", error=str(exc))

    hubble_ok = True
    try:
        flows = await hubble.get_flows(max_flows=config.HUBBLE_MAX_FLOWS)
    except Exception as exc:  # noqa: BLE001 - a blind source is reported, not fatal
        hubble_ok = False
        flows = []
        log_event("topology.source_unavailable", source="hubble", error=str(exc))

    deployments = {
        (row.get("namespace", ""), row.get("name", "")): row.get("deployment_name")
        for row in replicasets
    }
    index = _workload_index(services, pods, deployments)
    service_names = {(row["namespace"], row["name"]) for row in services}

    nodes = sorted(
        (
            Node(
                id=f"{row['namespace']}/{row['name']}",
                name=row["name"],
                namespace=row["namespace"],
                labels=dict(row.get("labels") or {}),
                cluster_ip=row.get("cluster_ip"),
            )
            for row in services
        ),
        key=lambda node: node.id,
    )

    edges = _env_edges(pods, deployments, index, service_names) + _flow_edges(flows, index)
    edges.sort(key=lambda edge: (edge.source, edge.target, edge.edge_type))

    sources: list[str] = []
    if k8s_ok:
        sources.append("kubernetes")
    if hubble_ok and flows:
        sources.append("hubble")

    return TopologyResponse(
        nodes=nodes,
        edges=edges,
        topology_sources=sources,
        node_count=len(nodes),
        edge_count=len(edges),
    )
