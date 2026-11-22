from fastapi.testclient import TestClient

from main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "journeys",
        "version": "0.1.0",
        "mode": "cluster-free",
    }


def test_health_reports_cluster_free_mode() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["mode"] == "cluster-free"
