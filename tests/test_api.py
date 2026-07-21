from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.config import settings, validate_runtime_config
from backend.main import app


@pytest.mark.asyncio
async def test_liveness_and_supported_openapi_surface():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        health = await client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["service"] == "governed-paper-trading"
        schema = (await client.get("/openapi.json")).json()
    paths = set(schema["paths"])
    assert "/api/governed/orders" in paths
    assert "/api/governed/custody-snapshots" in paths
    assert "/api/governed/backtests" in paths
    assert not any("narrate" in path or "portfolio" in path or "/themes/" in path for path in paths)


def test_production_runtime_configuration_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret_key", "change-me-in-production-32chars!!!")
    monkeypatch.setattr(settings, "cors_origins", "*")
    monkeypatch.setattr(settings, "market_data_allowed_hosts", "")
    monkeypatch.setattr(settings, "db_url", "postgresql+asyncpg://localhost/investment")
    with pytest.raises(RuntimeError) as error:
        validate_runtime_config()
    message = str(error.value)
    assert "JWT_SECRET_KEY" in message
    assert "CORS_ORIGINS" in message
    assert "MARKET_DATA_ALLOWED_HOSTS" in message
    assert "external database" in message
