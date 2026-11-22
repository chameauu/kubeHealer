"""HTTP contract tests for the fault library (rows 16-26 of the parity gate).

The chaos engine is never contacted: rankings tests install a deterministic
double on ``routers.library`` and assert the payload, the status codes and how
many times the upstream was asked.
"""

from typing import Any, Callable

import httpx
from fastapi.testclient import TestClient

from chaos_client import ChaosClientError
from fakes import ExplodingChaosClient, FakeChaosClient, scenario
from models import TaxonomyCategory

InstallChaos = Callable[[Any], Any]

SIM_TARGET = {"namespace": "demo-ns", "label_selector": {"app": "sim"}}
VOLUME_TARGET = {"resource_type": "volume", "operation": "create"}


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "faultlib"
    assert body["version"]


class TestCatalogEndpoint:
    def test_lists_every_entry_by_default(self, client: TestClient) -> None:
        response = client.get("/catalog")

        assert response.status_code == 200
        body = response.json()
        assert len(body["entries"]) == 8
        assert body["total"] == 8
        assert body["total"] == len(body["entries"])

    def test_filters_by_domain(self, client: TestClient) -> None:
        response = client.get("/catalog", params={"domain": "simulator"})

        assert response.status_code == 200
        body = response.json()
        assert len(body["entries"]) == 4
        assert body["total"] == 4
        assert {entry["domain"] for entry in body["entries"]} == {"simulator"}

    def test_get_single_entry(self, client: TestClient) -> None:
        response = client.get("/catalog/chaos_mesh/pod_kill")

        assert response.status_code == 200
        body = response.json()
        assert body["domain"] == "chaos_mesh"
        assert body["fault_type"] == "pod_kill"
        assert body["taxonomy_category"] == TaxonomyCategory.CASCADING.value
        assert body["typical_symptoms"]

    def test_unknown_entry_is_404_not_a_fabricated_stand_in(self, client: TestClient) -> None:
        response = client.get("/catalog/chaos_mesh/does-not-exist")

        assert response.status_code == 404
        body = response.json()
        assert "fault_type" not in body
        assert body["detail"]


class TestClassifyEndpoint:
    def test_classifies_a_known_fault_type(self, client: TestClient) -> None:
        response = client.post(
            "/classify", params={"domain": "simulator", "fault_type": "quota_limit"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["domain"] == "simulator"
        assert body["fault_type"] == "quota_limit"
        assert body["taxonomy_category"] == TaxonomyCategory.QUOTA_LIMIT.value
        assert body["rationale"].strip()

    def test_unknown_fault_type_is_404_not_a_guess(self, client: TestClient) -> None:
        response = client.post(
            "/classify", params={"domain": "chaos_mesh", "fault_type": "made-up"}
        )

        assert response.status_code == 404
        assert "no fault library entry" in response.json()["detail"]


class TestRankingsEndpoint:
    def test_empty_chaos_history_yields_empty_rankings(
        self,
        client: TestClient,
        fake_chaos: FakeChaosClient,
        install_chaos: InstallChaos,
    ) -> None:
        install_chaos(fake_chaos)

        response = client.get("/rankings")

        assert response.status_code == 200
        body = response.json()
        assert body["rankings"] == []
        assert body["scenarios_considered"] == 0
        assert body["scenarios_excluded"] == 0
        assert fake_chaos.list_calls == 1

    def test_rankings_reflect_real_chaos_history(
        self,
        client: TestClient,
        fake_chaos: FakeChaosClient,
        install_chaos: InstallChaos,
    ) -> None:
        fake_chaos.scenarios = [
            scenario(scenario_id="sim-a", fault_type="pod_kill", target=SIM_TARGET),
            scenario(scenario_id="sim-b", fault_type="pod_kill", target=SIM_TARGET),
            scenario(
                scenario_id="quota",
                domain="simulator",
                fault_type="quota_limit",
                target=VOLUME_TARGET,
            ),
        ]
        install_chaos(fake_chaos)

        response = client.get("/rankings")

        assert response.status_code == 200
        body = response.json()
        assert body["scenarios_considered"] == 3
        assert body["scenarios_excluded"] == 0
        by_component = {ranking["component"]: ranking for ranking in body["rankings"]}
        assert set(by_component) == {"demo-ns/app=sim", "volume/create"}
        sim = by_component["demo-ns/app=sim"]
        assert sim["total"] == 2
        assert sim["tally"]["cascading"] == 2
        quota = by_component["volume/create"]
        assert quota["total"] == 1
        assert quota["tally"]["quota_limit"] == 1
        assert fake_chaos.list_calls == 1

    def test_rankings_are_recomputed_live_on_every_call(
        self,
        client: TestClient,
        fake_chaos: FakeChaosClient,
        install_chaos: InstallChaos,
    ) -> None:
        first_scenario = scenario(scenario_id="first", target=SIM_TARGET)
        fake_chaos.scenarios = [first_scenario]
        install_chaos(fake_chaos)

        first_response = client.get("/rankings")
        assert first_response.status_code == 200
        assert first_response.json()["scenarios_considered"] == 1

        fake_chaos.scenarios.append(scenario(scenario_id="second", target=SIM_TARGET))
        second_response = client.get("/rankings")

        assert second_response.status_code == 200
        body = second_response.json()
        assert body["scenarios_considered"] == 2
        assert sum(ranking["total"] for ranking in body["rankings"]) == 2
        assert fake_chaos.list_calls == 2

    def test_backend_error_surfaces_as_502(
        self,
        client: TestClient,
        install_chaos: InstallChaos,
    ) -> None:
        exploding = ExplodingChaosClient()
        install_chaos(exploding)

        response = client.get("/rankings")

        assert response.status_code == 502
        assert response.json()["detail"] == "chaos unreachable"
        assert exploding.list_calls == 1


def test_chaos_client_transport_error_is_502(
    client: TestClient,
    fake_chaos: FakeChaosClient,
    install_chaos: InstallChaos,
) -> None:
    """A socket-level failure is the engine being unreachable, so it is a 502."""
    transport = httpx.ConnectError("connection refused")
    error = ChaosClientError("chaos unreachable")
    error.__cause__ = transport
    assert isinstance(error.__cause__, httpx.ConnectError)

    fake_chaos.error = error
    install_chaos(fake_chaos)

    response = client.get("/rankings")

    assert response.status_code == 502, response.text
    assert response.json()["detail"] == "chaos unreachable"
    assert fake_chaos.list_calls == 1


def test_rankings_payload_includes_excluded_count(
    client: TestClient,
    fake_chaos: FakeChaosClient,
    install_chaos: InstallChaos,
) -> None:
    fake_chaos.scenarios = [
        scenario(scenario_id="ran", target=SIM_TARGET),
        scenario(scenario_id="queued", target=SIM_TARGET, started=False),
    ]
    install_chaos(fake_chaos)

    response = client.get("/rankings")

    assert response.status_code == 200
    body = response.json()
    assert body["scenarios_excluded"] == 1
    assert body["scenarios_considered"] == 1
    assert body["rankings"][0]["total"] == 1
