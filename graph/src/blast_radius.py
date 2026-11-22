"""Reverse breadth-first search from the selector-matched nodes.

An edge points from the dependent workload to its dependency, so the blast
radius of a fault on `a` is every node that can still reach `a` through those
edges. Distance decides severity: one hop is high, two medium, three or more low.
"""

from __future__ import annotations

from collections import deque

from models import (
    AffectedNode,
    BlastRadiusResponse,
    Edge,
    Node,
    Severity,
    TopologyResponse,
)


def _severity(distance_hops: int) -> Severity:
    if distance_hops == 1:
        return "high"
    if distance_hops == 2:
        return "medium"
    return "low"


def _matches(node: Node, selector: dict[str, str]) -> bool:
    return all(node.labels.get(key) == value for key, value in selector.items())


def compute_blast_radius(
    topology: TopologyResponse,
    target_namespace: str,
    target_selector: dict[str, str],
    fault_type: str,
) -> BlastRadiusResponse:
    """Return the matched nodes and every dependent reachable from them."""
    matched = sorted(
        node.id
        for node in topology.nodes
        if node.namespace == target_namespace and _matches(node, target_selector)
    )
    matched_set = set(matched)

    dependents: dict[str, list[Edge]] = {}
    for edge in topology.edges:
        dependents.setdefault(edge.target, []).append(edge)
    for edges in dependents.values():
        edges.sort(key=lambda edge: (edge.source, edge.edge_type))

    distance: dict[str, int] = {}
    via: dict[str, set[str]] = {}
    queue: deque[str] = deque()
    for node_id in matched:
        distance[node_id] = 0
        via[node_id] = set()
        queue.append(node_id)

    while queue:
        current = queue.popleft()
        for edge in dependents.get(current, []):
            dependent = edge.source
            if dependent in matched_set or dependent in distance:
                continue
            distance[dependent] = distance[current] + 1
            via[dependent] = via[current] | {edge.edge_type}
            queue.append(dependent)

    by_id = {node.id: node for node in topology.nodes}
    affected = [
        AffectedNode(
            node_id=node_id,
            name=by_id[node_id].name,
            namespace=by_id[node_id].namespace,
            distance_hops=hops,
            severity=_severity(hops),
            via_edge_types=sorted(via[node_id]),
        )
        for node_id, hops in distance.items()
        if node_id not in matched_set
    ]
    affected.sort(key=lambda item: (item.distance_hops, item.node_id))

    return BlastRadiusResponse(
        target_namespace=target_namespace,
        target_selector=dict(target_selector),
        fault_type=fault_type,
        matched_nodes=matched,
        affected_nodes=affected,
        topology_sources=list(topology.topology_sources),
    )
