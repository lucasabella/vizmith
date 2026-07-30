from fastapi.testclient import TestClient

from vizmith.api import app


def test_health_reports_ok():
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
