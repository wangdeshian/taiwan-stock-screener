from __future__ import annotations

from fastapi.testclient import TestClient

from taiwan_stock_screener.api.main import app


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_candidates_auto_seed_sample_data() -> None:
    client = TestClient(app)
    response = client.get("/candidates")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
