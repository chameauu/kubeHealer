"""HTTP contract for `/topology` and `/blast-radius`.

Rows 1-10 of the M3 behaviour list, plus the cases the contract decides for us:
a blind Kubernetes API or a blind Hubble still answers 200 and reports the
missing source instead of inventing data, demo mode serves the fixture, and a
selector is parsed on its first `=` before any topology work happens.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import routers.graph as graph_router  # noqa: F401  (a missing router fails collection)
from fastapi.testclient import TestClient

from tests.fakes import FakeHubble, FakeK8s, service, workload

SHOP = "shop"


def _blast_params(*selectors: str, namespace: str = SHOP) -> list[tuple[str, str]]:
    params = [("target_namespace", namespace), ("fault_type", "pod_kill")]
    params.extend(("selector", selector) for selector in selectors)
    return params


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "graph", "version": "0.1.0"}


def test_demo_mode_serves_the_synthetic_fixture(
    client: TestClient,
    demo_mode: None,
    install_backends: Any,
) -> None:
    install_backends(k8s=FakeK8s(), hubble=FakeHubble())

    response = client.get("/topology")

    assert response.status_code == 200
    body = response.json()
    assert len(body["nodes"]) == 12
    assert len(body["edges"]) == 14
    assert body["node_count"] == 12
    assert body["edge_count"] == 14
    assert body["topology_sources"] == ["synthetic_fixture"]
    assert all(node["labels"]["demo-data"] == "synthetic" for node in body["nodes"])
    observed_at = datetime.fromisoformat(body["observed_at"].replace("Z", "+00:00"))
    assert observed_at.tzinfo is not None


def test_empty_cluster_returns_an_empty_topology(
    client: TestClient,
    install_backends: Any,
) -> None:
    install_backends(k8s=FakeK8s(), hubble=FakeHubble())

    response = client.get("/topology")

    assert response.status_code == 200
    body = response.json()
    assert body["nodes"] == []
    assert body["edges"] == []
    assert body["node_count"] == 0
    assert body["edge_count"] == 0


def test_env_derived_edge_over_http(client: TestClient, install_backends: Any) -> None:
    replicaset_row, pod_row = workload(
        SHOP,
        "orders",
        env={"PAYMENTS_URL": "http://payments.shop.svc.cluster.local"},
    )
    install_backends(
        k8s=FakeK8s(
            services=[service("orders", SHOP), service("payments", SHOP)],
            pods=[pod_row],
            replicasets=[replicaset_row],
        ),
        hubble=FakeHubble(),
    )

    response = client.get("/topology")

    assert response.status_code == 200
    edges = [edge for edge in response.json()["edges"] if edge["edge_type"] == "env_ref"]
    assert len(edges) == 1
    assert edges[0]["source"] == "shop/orders"
    assert edges[0]["target"] == "shop/payments"
    assert edges[0]["edge_type"] == "env_ref"


def test_topology_is_recomputed_on_every_call(
    client: TestClient,
    install_backends: Any,
) -> None:
    fake_k8s = FakeK8s(services=[service("api", SHOP)])
    install_backends(k8s=fake_k8s, hubble=FakeHubble())

    first = client.get("/topology")
    second = client.get("/topology")

    assert first.status_code == 200
    assert second.status_code == 200
    assert fake_k8s.service_calls == 2


def test_blast_radius_without_query_params_is_rejected(client: TestClient) -> None:
    response = client.get("/blast-radius")

    assert response.status_code == 422


def test_blast_radius_rejects_a_selector_without_equals(
    client: TestClient,
    install_backends: Any,
) -> None:
    fake_k8s = FakeK8s(services=[service("api", SHOP)])
    install_backends(k8s=fake_k8s, hubble=FakeHubble())

    response = client.get("/blast-radius", params=_blast_params("app"))

    assert response.status_code == 422
    assert fake_k8s.service_calls == 0


def test_blast_radius_rejects_a_selector_with_an_empty_key(client: TestClient) -> None:
    response = client.get("/blast-radius", params=_blast_params("=api"))

    assert response.status_code == 422


def test_blast_radius_treats_an_empty_selector_value_as_no_match(
    client: TestClient,
    install_backends: Any,
) -> None:
    install_backends(
        k8s=FakeK8s(services=[service("api", SHOP, labels={"app": "api"})]),
        hubble=FakeHubble(),
    )

    response = client.get("/blast-radius", params=_blast_params("app="))

    assert response.status_code == 200
    body = response.json()
    assert body["target_selector"] == {"app": ""}
    assert body["matched_nodes"] == []
    assert body["affected_nodes"] == []


def test_blast_radius_splits_a_selector_on_the_first_equals(
    client: TestClient,
    install_backends: Any,
) -> None:
    install_backends(
        k8s=FakeK8s(services=[service("api", SHOP, labels={"expr": "a=b"})]),
        hubble=FakeHubble(),
    )

    response = client.get("/blast-radius", params=_blast_params("expr=a=b"))

    assert response.status_code == 200
    assert "shop/api" in response.json()["matched_nodes"]


def test_blast_radius_on_an_empty_cluster_returns_empty_lists(
    client: TestClient,
    install_backends: Any,
) -> None:
    install_backends(k8s=FakeK8s(), hubble=FakeHubble())

    response = client.get("/blast-radius", params=_blast_params("app=chaos"))

    assert response.status_code == 200
    body = response.json()
    assert body["matched_nodes"] == []
    assert body["affected_nodes"] == []


def test_blast_radius_finds_the_direct_dependent_end_to_end(
    client: TestClient,
    install_backends: Any,
) -> None:
    replicaset_row, pod_row = workload(
        SHOP,
        "web",
        env={"CHAOS_URL": "http://chaos.shop.svc"},
    )
    install_backends(
        k8s=FakeK8s(
            services=[
                service("chaos", SHOP, labels={"app": "chaos"}),
                service("web", SHOP),
            ],
            pods=[pod_row],
            replicasets=[replicaset_row],
        ),
        hubble=FakeHubble(),
    )

    response = client.get("/blast-radius", params=_blast_params("app=chaos"))

    assert response.status_code == 200
    body = response.json()
    assert "shop/chaos" in body["matched_nodes"]
    affected = {node["node_id"]: node for node in body["affected_nodes"]}
    assert affected["shop/web"]["distance_hops"] == 1
    assert affected["shop/web"]["severity"] == "high"
    computed_at = datetime.fromisoformat(body["computed_at"].replace("Z", "+00:00"))
    assert computed_at.tzinfo is not None


def test_topology_survives_a_blind_kubernetes_api(
    client: TestClient,
    install_backends: Any,
) -> None:
    install_backends(
        k8s=FakeK8s(services=[service("api", SHOP)], fail=True),
        hubble=FakeHubble(),
    )

    response = client.get("/topology")

    assert response.status_code == 200
    body = response.json()
    assert body["nodes"] == []
    assert body["node_count"] == 0
    assert "kubernetes" not in body["topology_sources"]


def test_topology_survives_a_blind_hubble(client: TestClient, install_backends: Any) -> None:
    replicaset_row, pod_row = workload(
        SHOP,
        "api",
        env={"DB_URL": "http://db.shop.svc"},
    )
    install_backends(
        k8s=FakeK8s(
            services=[service("api", SHOP), service("db", SHOP)],
            pods=[pod_row],
            replicasets=[replicaset_row],
        ),
        hubble=FakeHubble(fail=True),
    )

    response = client.get("/topology")

    assert response.status_code == 200
    body = response.json()
    assert len(body["nodes"]) == 2
    assert "kubernetes" in body["topology_sources"]
    assert "hubble" not in body["topology_sources"]
    assert [edge["edge_type"] for edge in body["edges"]] == ["env_ref"]


def test_demo_mode_blast_radius_works_against_the_fixture(
    client: TestClient,
    demo_mode: None,
    install_backends: Any,
) -> None:
    install_backends(k8s=FakeK8s(), hubble=FakeHubble())
    topology = client.get("/topology").json()
    namespace = topology["nodes"][0]["namespace"]

    response = client.get(
        "/blast-radius",
        params=[("target_namespace", namespace), ("fault_type", "pod_kill"),
                ("selector", "demo-data=synthetic")],
    )

    assert response.status_code == 200
    matched = response.json()["matched_nodes"]
    assert matched
    assert all(node_id.startswith(f"{namespace}/") for node_id in matched)
