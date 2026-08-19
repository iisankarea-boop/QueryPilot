import pytest
from fastapi.testclient import TestClient
from querypilot.application.readiness import ReadinessChecker
from querypilot.transport import http


def test_ready_endpoint_reports_healthy_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def healthy() -> None:
        return None

    checker = ReadinessChecker({"arangodb": healthy}, timeout_seconds=0.1)
    http._readiness.cache_clear()
    monkeypatch.setattr(http, "_readiness", lambda: checker)

    response = TestClient(http.create_app()).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"arangodb": "ready"},
    }


def test_ready_endpoint_returns_503_when_dependency_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken() -> None:
        raise RuntimeError("secret detail")

    checker = ReadinessChecker({"milvus": broken}, timeout_seconds=0.1)
    http._readiness.cache_clear()
    monkeypatch.setattr(http, "_readiness", lambda: checker)

    response = TestClient(http.create_app()).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "dependencies": {"milvus": "unavailable"},
    }
