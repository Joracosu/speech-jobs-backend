"""Basic tests for the public health endpoint."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_returns_ok_status() -> None:
    """Health endpoint should return HTTP 200 and status ok payload."""
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
