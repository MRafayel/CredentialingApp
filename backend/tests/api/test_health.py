from fastapi.testclient import TestClient

from credentialing.api.app import create_app


def test_health_reports_ok() -> None:
    client = TestClient(create_app(expose_docs=False))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_docs_are_hidden_unless_exposed() -> None:
    client = TestClient(create_app(expose_docs=False))

    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
