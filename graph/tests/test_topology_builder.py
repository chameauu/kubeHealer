"""Unit contract for `build_topology` and `parse`-driven edge construction.

Rows 4 and 34-45 of the M3 behaviour list, plus the deterministic ordering the
contract fixes for nodes and edges. A workload is resolved from its Pod through
the owning ReplicaSet's deployment name; the workload-to-Service index is the
only way an environment reference or a Hubble flow becomes an edge.
"""

from __future__ import annotations

from topology_builder import build_topology

from tests.fakes import FakeHubble, FakeK8s, flow, pod, service, workload

SHOP = "shop"


async def test_one_node_per_service() -> None:
    k8s = FakeK8s(services=[service("api", SHOP), service("db", SHOP)])

    topology = await build_topology(k8s, FakeHubble())

    assert {node.name for node in topology.nodes} == {"api", "db"}
    assert {node.id for node in topology.nodes} == {"shop/api", "shop/db"}


async def test_no_services_yield_no_nodes_and_no_edges() -> None:
    topology = await build_topology(FakeK8s(), FakeHubble())

    assert topology.nodes == []
    assert topology.edges == []
    assert topology.node_count == 0
    assert topology.edge_count == 0


async def test_services_without_pods_yield_one_node_each_and_no_edges() -> None:
    k8s = FakeK8s(services=[service("api", SHOP), service("db", SHOP)])

    topology = await build_topology(k8s, FakeHubble())

    assert len(topology.nodes) == 2
    assert topology.edges == []


async def test_sources_are_kubernetes_without_flows() -> None:
    k8s = FakeK8s(services=[service("api", SHOP)])

    topology = await build_topology(k8s, FakeHubble())

    assert topology.topology_sources == ["kubernetes"]


async def test_hubble_source_is_added_when_flows_exist() -> None:
    k8s, hubble = _flow_cluster([flow(SHOP, "frontend", SHOP, "orders")])

    topology = await build_topology(k8s, hubble)

    assert "kubernetes" in topology.topology_sources
    assert "hubble" in topology.topology_sources


async def test_hubble_source_is_absent_without_flows() -> None:
    k8s, hubble = _flow_cluster([])

    topology = await build_topology(k8s, hubble)

    assert "hubble" not in topology.topology_sources


async def test_env_ref_creates_an_edge_from_dependent_to_dependency() -> None:
    k8s = _orders_referencing_payments({"PAYMENTS_URL": "http://payments.shop.svc"})

    topology = await build_topology(k8s, FakeHubble())

    assert len(topology.edges) == 1
    edge = topology.edges[0]
    assert edge.source == "shop/orders"
    assert edge.target == "shop/payments"
    assert edge.edge_type == "env_ref"


async def test_env_ref_to_an_unknown_service_is_dropped() -> None:
    k8s = _orders_referencing_payments({"GHOST_URL": "http://ghost.shop.svc"})

    topology = await build_topology(k8s, FakeHubble())

    assert topology.edges == []


async def test_env_ref_edge_carries_the_env_var() -> None:
    k8s = _orders_referencing_payments({"PAYMENTS_URL": "http://payments.shop.svc"})

    topology = await build_topology(k8s, FakeHubble())

    assert len(topology.edges) == 1
    assert topology.edges[0].env_var == "PAYMENTS_URL"


async def test_two_pods_of_one_workload_yield_one_edge() -> None:
    replicaset_row, first_pod = workload(
        SHOP,
        "orders",
        env={"DB_URL": "http://db.shop.svc"},
        pod_name="orders-6f7c9d4b8-aaaaa",
    )
    second_pod = pod(
        "orders-6f7c9d4b8-bbbbb",
        SHOP,
        owner_name=replicaset_row["name"],
        env={"DB_URL": "http://db.shop.svc"},
        labels={"app": "orders"},
    )
    k8s = FakeK8s(
        services=[service("orders", SHOP), service("db", SHOP)],
        pods=[first_pod, second_pod],
        replicasets=[replicaset_row],
    )

    topology = await build_topology(k8s, FakeHubble())

    assert len(topology.edges) == 1
    assert (topology.edges[0].source, topology.edges[0].target) == ("shop/orders", "shop/db")


async def test_hubble_flow_maps_to_exactly_one_observed_edge() -> None:
    k8s, hubble = _flow_cluster([flow(SHOP, "frontend", SHOP, "orders")])

    topology = await build_topology(k8s, hubble)

    assert len(topology.edges) == 1
    edge = topology.edges[0]
    assert edge.source == "shop/frontend"
    assert edge.target == "shop/orders"
    assert edge.edge_type == "flow_observed"


async def test_repeated_flows_for_one_pair_are_summed() -> None:
    repeated = [flow(SHOP, "frontend", SHOP, "orders") for _ in range(3)]
    k8s, hubble = _flow_cluster(repeated)

    topology = await build_topology(k8s, hubble)

    observed = [edge for edge in topology.edges if edge.edge_type == "flow_observed"]
    assert len(observed) == 1
    assert observed[0].flow_count == 3


async def test_flow_to_an_unmapped_workload_is_dropped() -> None:
    k8s, hubble = _flow_cluster([flow(SHOP, "frontend", SHOP, "ghost")])

    topology = await build_topology(k8s, hubble)

    assert topology.edges == []


async def test_nodes_are_sorted_by_id() -> None:
    k8s = FakeK8s(
        services=[
            service("zeta", SHOP),
            service("alpha", SHOP),
            service("middleware", SHOP),
        ]
    )

    topology = await build_topology(k8s, FakeHubble())

    ids = [node.id for node in topology.nodes]
    assert ids == sorted(ids)
    assert ids == ["shop/alpha", "shop/middleware", "shop/zeta"]


async def test_edges_are_sorted_by_source_target_and_type() -> None:
    b_replicaset, b_pod = workload(SHOP, "b", env={"A_URL": "http://a.shop.svc"})
    d_replicaset, d_pod = workload(
        SHOP,
        "d",
        env={"C_URL": "http://c.shop.svc", "A_URL": "http://a.shop.svc"},
    )
    k8s = FakeK8s(
        services=[service(name, SHOP) for name in ("a", "b", "c", "d")],
        pods=[d_pod, b_pod],
        replicasets=[d_replicaset, b_replicaset],
    )

    topology = await build_topology(k8s, FakeHubble())

    observed = [(edge.source, edge.target, edge.edge_type) for edge in topology.edges]
    assert observed == [
        ("shop/b", "shop/a", "env_ref"),
        ("shop/d", "shop/a", "env_ref"),
        ("shop/d", "shop/c", "env_ref"),
    ]


def _orders_referencing_payments(env: dict[str, str]) -> FakeK8s:
    replicaset_row, pod_row = workload(SHOP, "orders", env=env)
    return FakeK8s(
        services=[service("orders", SHOP), service("payments", SHOP)],
        pods=[pod_row],
        replicasets=[replicaset_row],
    )


def _flow_cluster(flows: list[dict[str, object]]) -> tuple[FakeK8s, FakeHubble]:
    frontend_replicaset, frontend_pod = workload(SHOP, "frontend")
    orders_replicaset, orders_pod = workload(SHOP, "orders")
    k8s = FakeK8s(
        services=[service("frontend", SHOP), service("orders", SHOP)],
        pods=[frontend_pod, orders_pod],
        replicasets=[frontend_replicaset, orders_replicaset],
    )
    return k8s, FakeHubble(flows=flows)
