"""HTTP contract for `/scenarios`: envelopes, status codes, filters, error paths.

Rows 1-18 and 61 of the M2 behaviour list. Everything is asserted through the
public API; the engine is only used to arrange state where the API cannot.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.fakes import ExplodingChaosMeshClient, FailingDeleteChaosMeshClient

NAMESPACE = "argus-system"


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "pod-kill-orders",
        "correlation_id": "corr-abc",
        "domain": "chaos_mesh",
        "fault_type": "pod_kill",
        "target": {
            "namespace": NAMESPACE,
            "label_selector": {"app": "orders"},
            "mode": "one",
        },
        "duration_seconds": 30,
        "params": {},
    }
    payload.update(overrides)
    return payload


def _simulator_payload(**overrides: Any) -> dict[str, Any]:
    payload = _payload()
    payload.update(
        {
            "domain": "simulator",
            "fault_type": "latency",
            "target": {"resource_type": "volume", "operation": "attach"},
            "params": {"extra_seconds": 1},
            "probability": 0.4,
        }
    )
    payload.update(overrides)
    return payload


def test_health_envelope(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "chaos"


def test_create_chaos_mesh_scenario(client: TestClient, fake_chaos_mesh: Any) -> None:
    response = client.post("/scenarios", json=_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "running"
    assert body["domain"] == "chaos_mesh"
    assert body["backend_ref"] == "kubeheal-chaos-" + body["id"]
    assert len(fake_chaos_mesh.apply_calls) == 1
    assert fake_chaos_mesh.apply_calls[0]["spec"]["action"] == "pod-kill"


def test_create_simulator_scenario(client: TestClient, fake_simulator: Any) -> None:
    response = client.post("/scenarios", json=_simulator_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "running"
    assert body["backend_ref"] in fake_simulator.rules
    rule = fake_simulator.rules[body["backend_ref"]]
    assert rule["resource_type"] == "volume"


def test_explicit_correlation_id_is_echoed(client: TestClient) -> None:
    response = client.post("/scenarios", json=_payload(correlation_id="incident-42"))

    assert response.status_code == 201
    assert response.json()["correlation_id"] == "incident-42"


def test_unknown_chaos_mesh_fault_type_is_422(client: TestClient) -> None:
    response = client.post("/scenarios", json=_payload(fault_type="pod_bounce"))

    assert response.status_code == 422
    assert "unknown chaos_mesh fault_type" in str(response.json()["detail"])
    assert client.get("/scenarios").json()["total"] == 0


def test_fixed_mode_without_value_is_422(client: TestClient) -> None:
    response = client.post(
        "/scenarios",
        json=_payload(target={"namespace": NAMESPACE, "mode": "fixed"}),
    )

    assert response.status_code == 422
    assert "target.value is required" in response.text
    assert client.get("/scenarios").json()["total"] == 0


def test_missing_name_is_422(client: TestClient) -> None:
    payload = _payload()
    payload.pop("name")

    response = client.post("/scenarios", json=payload)

    assert response.status_code == 422


def test_zero_duration_is_422(client: TestClient) -> None:
    response = client.post("/scenarios", json=_payload(duration_seconds=0))

    assert response.status_code == 422


def test_apply_failure_is_502_and_persists_failed(
    make_engine: Any, make_client: Any
) -> None:
    engine = make_engine(chaos_mesh=ExplodingChaosMeshClient())
    client = make_client(engine)

    response = client.post("/scenarios", json=_payload())

    assert response.status_code == 502
    assert "admission webhook denied the request" in response.json()["detail"]
    body = client.get("/scenarios").json()
    assert body["total"] == 1
    assert body["scenarios"][0]["status"] == "failed"
    assert "admission webhook denied the request" in body["scenarios"][0]["error"]


def test_get_unknown_scenario_is_404(client: TestClient) -> None:
    assert client.get("/scenarios/scn-0000000000").status_code == 404


def test_list_envelope_and_total(client: TestClient) -> None:
    client.post("/scenarios", json=_payload())
    client.post("/scenarios", json=_payload(name="second"))

    body = client.get("/scenarios").json()

    assert body["total"] == len(body["scenarios"]) == 2


def test_list_filters_by_status(client: TestClient) -> None:
    stopped = client.post("/scenarios", json=_payload()).json()
    client.post("/scenarios", json=_payload(name="second"))
    assert client.post(f"/scenarios/{stopped['id']}/stop").status_code == 200

    running = client.get("/scenarios", params={"status": "running"}).json()
    only_stopped = client.get("/scenarios", params={"status": "stopped"}).json()

    assert running["total"] == 1
    assert running["scenarios"][0]["status"] == "running"
    assert only_stopped["total"] == 1
    assert only_stopped["scenarios"][0]["id"] == stopped["id"]


def test_get_refreshes_live_status_and_drops_instances(client: TestClient) -> None:
    created = client.post("/scenarios", json=_payload()).json()

    detail = client.get(f"/scenarios/{created['id']}").json()

    assert detail["live_status"] is not None
    assert set(detail["live_status"]) == {"experiment", "conditions"}


def test_stop_then_delete_then_get(client: TestClient, fake_chaos_mesh: Any) -> None:
    created = client.post("/scenarios", json=_payload()).json()

    stopped = client.post(f"/scenarios/{created['id']}/stop")

    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert fake_chaos_mesh.objects == {}

    assert client.delete(f"/scenarios/{created['id']}").status_code == 204
    assert client.get(f"/scenarios/{created['id']}").status_code == 404


def test_stop_unknown_scenario_is_404(client: TestClient) -> None:
    assert client.post("/scenarios/scn-0000000000/stop").status_code == 404


def test_stop_backend_failure_is_502_and_marks_failed(
    make_engine: Any, make_client: Any
) -> None:
    engine = make_engine(chaos_mesh=FailingDeleteChaosMeshClient())
    client = make_client(engine)
    created = client.post("/scenarios", json=_payload()).json()

    response = client.post(f"/scenarios/{created['id']}/stop")

    assert response.status_code == 502
    assert "apiserver unavailable" in response.json()["detail"]
    assert client.get(f"/scenarios/{created['id']}").json()["status"] == "failed"


def test_stop_stopped_scenario_is_409(client: TestClient) -> None:
    created = client.post("/scenarios", json=_payload()).json()
    client.post(f"/scenarios/{created['id']}/stop")

    response = client.post(f"/scenarios/{created['id']}/stop")

    assert response.status_code == 409
    assert "not running" in response.json()["detail"]


def test_delete_running_scenario_is_409(client: TestClient) -> None:
    created = client.post("/scenarios", json=_payload()).json()

    response = client.delete(f"/scenarios/{created['id']}")

    assert response.status_code == 409
    assert "still running" in response.json()["detail"]


def test_delete_unknown_scenario_is_404(client: TestClient) -> None:
    assert client.delete("/scenarios/scn-0000000000").status_code == 404
