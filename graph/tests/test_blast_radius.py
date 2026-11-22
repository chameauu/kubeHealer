"""Unit contract for `compute_blast_radius`.

Rows 11-22 of the M3 behaviour list. The function is synchronous and takes the
already-built topology, so these tests are the clearest statement of the
direction rule: an edge points from the dependent to its dependency, and the
blast radius walks it backwards from the matched nodes.
"""

from __future__ import annotations

from datetime import datetime

from blast_radius import compute_blast_radius
from models import Edge, Node, TopologyResponse


def _node(node_id: str, **labels: str) -> Node:
    namespace, name = node_id.split("/", 1)
    return Node(id=node_id, name=name, namespace=namespace, labels=dict(labels))


def _edge(source: str, target: str, edge_type: str = "env_ref") -> Edge:
    return Edge(source=source, target=target, edge_type=edge_type)


def _topology(
    nodes: list[Node],
    edges: list[Edge],
    sources: list[str] | None = None,
) -> TopologyResponse:
    return TopologyResponse(
        nodes=nodes,
        edges=edges,
        topology_sources=list(sources or ["kubernetes"]),
        node_count=len(nodes),
        edge_count=len(edges),
    )


def test_blast_radius_of_an_empty_topology_is_empty() -> None:
    response = compute_blast_radius(_topology([], []), "shop", {"app": "api"}, "pod_kill")

    assert response.matched_nodes == []
    assert response.affected_nodes == []


def test_selector_matching_nothing_returns_empty_lists() -> None:
    topology = _topology(
        [_node("shop/a", app="a"), _node("shop/b", app="b")],
        [_edge("shop/a", "shop/b")],
    )

    response = compute_blast_radius(topology, "shop", {"app": "ghost"}, "pod_kill")

    assert response.matched_nodes == []
    assert response.affected_nodes == []


def test_target_with_only_dependencies_has_no_affected_nodes() -> None:
    topology = _topology(
        [_node("shop/a", app="a"), _node("shop/b", app="b")],
        [_edge("shop/a", "shop/b")],
    )

    response = compute_blast_radius(topology, "shop", {"app": "a"}, "pod_kill")

    assert response.matched_nodes == ["shop/a"]
    assert response.affected_nodes == []


def test_direct_dependent_appears_once_at_one_hop() -> None:
    topology = _topology(
        [_node("shop/a", app="a"), _node("shop/b", app="b")],
        [_edge("shop/b", "shop/a", "env_ref"), _edge("shop/b", "shop/a", "flow_observed")],
    )

    response = compute_blast_radius(topology, "shop", {"app": "a"}, "pod_kill")

    hits = [affected for affected in response.affected_nodes if affected.node_id == "shop/b"]
    assert len(hits) == 1
    assert hits[0].distance_hops == 1
    assert hits[0].severity == "high"
    assert "shop/a" not in [affected.node_id for affected in response.affected_nodes]


def test_multiple_direct_dependents_are_all_one_hop() -> None:
    topology = _topology(
        [
            _node("shop/a", app="a"),
            _node("shop/b", app="b"),
            _node("shop/c", app="c"),
            _node("shop/d", app="d"),
        ],
        [_edge("shop/b", "shop/a"), _edge("shop/c", "shop/a"), _edge("shop/d", "shop/a")],
    )

    response = compute_blast_radius(topology, "shop", {"app": "a"}, "pod_kill")

    hops = {affected.node_id: affected.distance_hops for affected in response.affected_nodes}
    assert hops == {"shop/b": 1, "shop/c": 1, "shop/d": 1}


def test_two_hop_dependent_is_medium() -> None:
    topology = _topology(
        [_node("shop/a", app="a"), _node("shop/b", app="b"), _node("shop/c", app="c")],
        [_edge("shop/b", "shop/a"), _edge("shop/c", "shop/b")],
    )

    response = compute_blast_radius(topology, "shop", {"app": "a"}, "pod_kill")

    by_id = {affected.node_id: affected for affected in response.affected_nodes}
    assert by_id["shop/b"].distance_hops == 1
    assert by_id["shop/b"].severity == "high"
    assert by_id["shop/c"].distance_hops == 2
    assert by_id["shop/c"].severity == "medium"


def test_three_hop_dependent_is_low() -> None:
    topology = _topology(
        [
            _node("shop/a", app="a"),
            _node("shop/b", app="b"),
            _node("shop/c", app="c"),
            _node("shop/d", app="d"),
        ],
        [_edge("shop/d", "shop/c"), _edge("shop/c", "shop/b"), _edge("shop/b", "shop/a")],
    )

    response = compute_blast_radius(topology, "shop", {"app": "a"}, "pod_kill")

    by_id = {affected.node_id: affected for affected in response.affected_nodes}
    assert by_id["shop/b"].distance_hops == 1
    assert by_id["shop/b"].severity == "high"
    assert by_id["shop/c"].distance_hops == 2
    assert by_id["shop/c"].severity == "medium"
    assert by_id["shop/d"].distance_hops == 3
    assert by_id["shop/d"].severity == "low"


def test_empty_selector_matches_every_node_in_the_namespace() -> None:
    topology = _topology(
        [
            _node("shop/a", app="a"),
            _node("shop/b", app="b"),
            _node("prod/c", app="a"),
        ],
        [],
    )

    response = compute_blast_radius(topology, "shop", {}, "pod_kill")

    assert set(response.matched_nodes) == {"shop/a", "shop/b"}
    affected_ids = {affected.node_id for affected in response.affected_nodes}
    assert affected_ids.isdisjoint(set(response.matched_nodes))


def test_selector_filters_by_exact_label_and_bfs_continues() -> None:
    topology = _topology(
        [
            _node("shop/api", app="api"),
            _node("shop/api-v2", app="api-v2"),
            _node("shop/mid", app="mid"),
            _node("shop/leaf", app="leaf"),
            _node("shop/noise", app="noise"),
        ],
        [_edge("shop/mid", "shop/api"), _edge("shop/leaf", "shop/mid")],
    )

    response = compute_blast_radius(topology, "shop", {"app": "api"}, "pod_kill")

    assert response.matched_nodes == ["shop/api"]
    by_id = {affected.node_id: affected for affected in response.affected_nodes}
    assert set(by_id) == {"shop/mid", "shop/leaf"}
    assert by_id["shop/mid"].distance_hops == 1
    assert by_id["shop/leaf"].distance_hops == 2


def test_two_node_cycle_terminates() -> None:
    topology = _topology(
        [_node("shop/a", app="a"), _node("shop/b", app="b")],
        [_edge("shop/a", "shop/b"), _edge("shop/b", "shop/a")],
    )

    response = compute_blast_radius(topology, "shop", {"app": "a"}, "pod_kill")

    assert response.matched_nodes == ["shop/a"]
    assert [affected.node_id for affected in response.affected_nodes] == ["shop/b"]
    assert response.affected_nodes[0].distance_hops == 1


def test_response_echoes_the_target_parameters() -> None:
    topology = _topology([_node("shop/api", app="api")], [])

    response = compute_blast_radius(topology, "shop", {"app": "api"}, "network_latency")

    assert response.target_namespace == "shop"
    assert response.target_selector == {"app": "api"}
    assert response.fault_type == "network_latency"
    computed_at = datetime.fromisoformat(response.computed_at.replace("Z", "+00:00"))
    assert computed_at.tzinfo is not None


def test_topology_sources_are_copied_through_in_order() -> None:
    topology = _topology(
        [_node("shop/api", app="api")],
        [],
        sources=["hubble", "kubernetes"],
    )

    response = compute_blast_radius(topology, "shop", {"app": "api"}, "pod_kill")

    assert response.topology_sources == ["hubble", "kubernetes"]


def test_affected_nodes_are_ordered_by_distance_then_id() -> None:
    topology = _topology(
        [
            _node("shop/a", app="a"),
            _node("shop/zeta", app="dependent"),
            _node("shop/alpha", app="dependent"),
            _node("shop/deep", app="deep"),
        ],
        [
            _edge("shop/zeta", "shop/a"),
            _edge("shop/alpha", "shop/a"),
            _edge("shop/deep", "shop/zeta"),
        ],
    )

    response = compute_blast_radius(topology, "shop", {"app": "a"}, "pod_kill")

    ordered = [(affected.node_id, affected.distance_hops) for affected in response.affected_nodes]
    assert ordered == [("shop/alpha", 1), ("shop/zeta", 1), ("shop/deep", 2)]


def test_via_edge_types_is_sorted_and_deduplicated() -> None:
    topology = _topology(
        [
            _node("shop/a", app="a"),
            _node("shop/b", app="b"),
            _node("shop/c", app="c"),
            _node("shop/d", app="d"),
        ],
        [
            _edge("shop/b", "shop/a", "env_ref"),
            _edge("shop/c", "shop/b", "flow_observed"),
            _edge("shop/d", "shop/c", "flow_observed"),
        ],
    )

    response = compute_blast_radius(topology, "shop", {"app": "a"}, "pod_kill")

    by_id = {affected.node_id: affected for affected in response.affected_nodes}
    assert by_id["shop/b"].via_edge_types == ["env_ref"]
    assert by_id["shop/c"].via_edge_types == ["env_ref", "flow_observed"]
    assert by_id["shop/d"].via_edge_types == ["env_ref", "flow_observed"]
