"""HTTP contract: status codes, validation and response shapes.

Behaviour beyond the envelope (state sequences, fault effects) is asserted in
`test_provisioning.py` and `test_lifecycle.py`, where transitions can be
awaited instead of raced through the client.
"""

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "sim"


def test_state_snapshot_starts_empty() -> None:
    response = client.get("/state")

    assert response.status_code == 200
    body = response.json()
    assert body["totals"] == {"volume": 0, "subnet": 0, "instance": 0}
    assert body["resources"]["volume"] == []


class TestVolumes:
    def test_create_returns_transitional_state(self) -> None:
        response = client.post("/volumes", json={"name": "data-vol", "size_gb": 100})

        assert response.status_code == 202
        body = response.json()
        assert body["type"] == "volume"
        assert body["state"] == "creating"
        assert body["attributes"]["size_gb"] == 100
        assert body["attributes"]["attached_to"] is None
        assert body["id"].startswith("vol-")

    def test_get_unknown_volume_404(self) -> None:
        assert client.get("/volumes/vol-doesnotexist").status_code == 404

    def test_attach_unknown_volume_404(self) -> None:
        response = client.post("/volumes/vol-doesnotexist/attach", json={"instance_id": "inst-x"})

        assert response.status_code == 404

    def test_create_then_list(self) -> None:
        client.post("/volumes", json={"name": "v1", "size_gb": 10})
        client.post("/volumes", json={"name": "v2", "size_gb": 20})

        body = client.get("/volumes").json()

        assert body["total"] == 2
        assert {volume["name"] for volume in body["volumes"]} == {"v1", "v2"}

    def test_attach_rejects_volume_not_available(self) -> None:
        created = client.post("/volumes", json={"name": "v1", "size_gb": 10}).json()

        response = client.post(f"/volumes/{created['id']}/attach", json={"instance_id": "inst-x"})

        assert response.status_code == 409

    def test_create_validation_rejects_oversized_volume(self) -> None:
        response = client.post("/volumes", json={"name": "huge", "size_gb": 999999})

        assert response.status_code == 422


class TestSubnets:
    def test_create_returns_transitional_state(self) -> None:
        response = client.post(
            "/subnets", json={"name": "app-subnet", "cidr": "10.0.1.0/24", "vlan_id": 100}
        )

        assert response.status_code == 202
        body = response.json()
        assert body["type"] == "subnet"
        assert body["state"] == "creating"
        assert body["attributes"]["vlan_id"] == 100
        assert body["id"].startswith("snet-")

    def test_delete_unknown_subnet_404(self) -> None:
        assert client.delete("/subnets/snet-doesnotexist").status_code == 404

    def test_create_validation_rejects_bad_vlan_id(self) -> None:
        response = client.post(
            "/subnets", json={"name": "bad", "cidr": "10.0.1.0/24", "vlan_id": 9000}
        )

        assert response.status_code == 422


class TestInstances:
    def test_provision_returns_transitional_state(self) -> None:
        response = client.post("/instances", json={"name": "api-host"})

        assert response.status_code == 202
        body = response.json()
        assert body["type"] == "instance"
        assert body["state"] == "provisioning"
        assert body["id"].startswith("inst-")

    def test_provision_with_unknown_subnet_404(self) -> None:
        response = client.post(
            "/instances", json={"name": "api-host", "subnet_id": "snet-doesnotexist"}
        )

        assert response.status_code == 404

    def test_deprovision_unknown_instance_404(self) -> None:
        assert client.delete("/instances/inst-doesnotexist").status_code == 404

    def test_deprovision_rejects_instance_not_running(self) -> None:
        created = client.post("/instances", json={"name": "api-host"}).json()

        assert client.delete(f"/instances/{created['id']}").status_code == 409


class TestFaultRules:
    def test_register_list_and_clear(self) -> None:
        response = client.post(
            "/faults",
            json={
                "fault_type": "latency",
                "resource_type": "volume",
                "operation": "create",
                "params": {"extra_seconds": 2},
            },
        )

        assert response.status_code == 201
        rule = response.json()
        assert rule["fault_type"] == "latency"
        assert rule["hits"] == 0
        assert rule["id"].startswith("fault-")

        listed = client.get("/faults").json()
        assert listed["total"] == 1
        assert listed["rules"][0]["id"] == rule["id"]

        assert client.delete(f"/faults/{rule['id']}").status_code == 204
        assert client.get("/faults").json()["total"] == 0

    def test_clear_unknown_rule_404(self) -> None:
        assert client.delete("/faults/fault-doesnotexist").status_code == 404

    def test_clear_all(self) -> None:
        client.post("/faults", json={"fault_type": "transient_error"})
        client.post("/faults", json={"fault_type": "quota_limit", "resource_type": "instance"})

        response = client.delete("/faults")

        assert response.status_code == 200
        assert response.json()["cleared"] == 2
        assert client.get("/faults").json()["total"] == 0
