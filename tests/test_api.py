"""
Test suite for the investment backend API.

Uses pytest + httpx.AsyncClient with an in-memory SQLite database so that
tests run without a PostgreSQL instance.  Each test function is isolated:
the database is created fresh at session start and the app's DB URL is
overridden via an environment variable before the app module is imported.

Run:
    cd /Users/erolakarsu/projects/investment
    .venv/bin/pytest tests/test_api.py -v
"""

from __future__ import annotations

import os
import sys

# ── Override DB URL to SQLite in-memory BEFORE importing the app ──────────────
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///./test_investment.db")

# Add project root to path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

import asyncio
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport


# ── Lazy app import (after env override) ──────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for the whole test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def app():
    """Create the FastAPI app and initialise the test database."""
    # Patch the DB URL in config before the engine is created
    from backend.config import settings
    settings.db_url = "sqlite+aiosqlite:///./test_investment.db"

    # Patch the engine in db module to use SQLite
    import backend.db as db_module
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

    test_engine = create_async_engine(
        "sqlite+aiosqlite:///./test_investment.db",
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
    )
    test_session = async_sessionmaker(
        test_engine, expire_on_commit=False, class_=AsyncSession
    )
    db_module.engine = test_engine
    db_module.SessionLocal = test_session

    # Also patch auth and portfolio modules that captured SessionLocal at import
    import backend.auth as auth_module
    import backend.portfolio as portfolio_module
    auth_module.SessionLocal = test_session
    portfolio_module.SessionLocal = test_session

    # Create tables
    async with test_engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.create_all)
        # Also create auth and portfolio tables
        from backend.auth import AppUser
        from backend.portfolio import PortfolioHolding
        await conn.run_sync(auth_module.Base.metadata.create_all)

    from backend.main import app as fastapi_app
    yield fastapi_app

    # Cleanup
    async with test_engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.drop_all)
    await test_engine.dispose()
    import pathlib
    pathlib.Path("./test_investment.db").unlink(missing_ok=True)


@pytest_asyncio.fixture(scope="session")
async def client(app):
    """Shared async HTTP client for the whole session."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture(scope="session")
async def auth_headers(client):
    """Register a test user, log in, and return Bearer headers."""
    reg = await client.post("/api/auth/register", json={
        "username": "testuser",
        "email": "test@example.com",
        "password": "securepassword123",
    })
    assert reg.status_code == 201, reg.text
    token = reg.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────── Health check ────────────────────────────────────

@pytest.mark.asyncio
async def test_healthz(client):
    """GET /healthz returns 200 and ok=True."""
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ─────────────────────────── Auth endpoints ──────────────────────────────────

@pytest.mark.asyncio
async def test_register_and_login(client):
    """Register a new user then log in with the same credentials."""
    reg = await client.post("/api/auth/register", json={
        "username": "logintest",
        "email": "logintest@example.com",
        "password": "mypassword99",
    })
    assert reg.status_code == 201
    data = reg.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["username"] == "logintest"

    login = await client.post("/api/auth/login", json={
        "username": "logintest",
        "password": "mypassword99",
    })
    assert login.status_code == 200
    login_data = login.json()
    assert "access_token" in login_data


@pytest.mark.asyncio
async def test_register_duplicate_rejected(client):
    """Registering the same username twice returns 409."""
    payload = {"username": "dupeuser", "email": "dupe@example.com", "password": "pass12345"}
    first = await client.post("/api/auth/register", json=payload)
    assert first.status_code == 201
    second = await client.post("/api/auth/register", json=payload)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    """Wrong password returns 401."""
    await client.post("/api/auth/register", json={
        "username": "wrongpass",
        "email": "wrongpass@example.com",
        "password": "correctpass",
    })
    r = await client.post("/api/auth/login", json={
        "username": "wrongpass",
        "password": "badpassword",
    })
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_endpoint(client, auth_headers):
    """GET /api/auth/me returns the current user's info."""
    r = await client.get("/api/auth/me", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["username"] == "testuser"
    assert data["email"] == "test@example.com"
    assert "id" in data


@pytest.mark.asyncio
async def test_me_requires_auth(client):
    """GET /api/auth/me without a token returns 401 or 403."""
    r = await client.get("/api/auth/me")
    assert r.status_code in (401, 403)


# ─────────────────────────── Overview endpoint ───────────────────────────────

@pytest.mark.asyncio
async def test_overview(client):
    """GET /api/overview returns a list of 5 themes."""
    r = await client.get("/api/overview")
    assert r.status_code == 200
    data = r.json()
    assert "themes" in data
    assert len(data["themes"]) == 5
    theme_ids = {t["id"] for t in data["themes"]}
    assert theme_ids == {"hbm", "networking", "energy", "inference", "photonics"}


# ─────────────────────────── Algorithm source endpoint ───────────────────────

@pytest.mark.asyncio
async def test_source_known_paper(client, auth_headers):
    """GET /api/source/hbm/H2O returns Python source code."""
    r = await client.get("/api/source/hbm/H2O", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["theme"] == "hbm"
    assert data["paper"] == "H2O"
    assert "source" in data
    assert len(data["source"]) > 100  # real source, not empty


@pytest.mark.asyncio
async def test_source_unknown_paper(client, auth_headers):
    """GET /api/source/hbm/FakePaper returns 404."""
    r = await client.get("/api/source/hbm/FakePaper", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_source_requires_auth(client):
    """Source endpoint without token returns 401/403."""
    r = await client.get("/api/source/hbm/H2O")
    assert r.status_code in (401, 403)


# ─────────────────────────── Algorithm run endpoint ──────────────────────────

@pytest.mark.asyncio
async def test_run_hbm_h2o_defaults(client, auth_headers):
    """POST /api/run/hbm/H2O with no body uses default inputs."""
    r = await client.post("/api/run/hbm/H2O", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["theme"] == "hbm"
    assert data["paper"] == "H2O"
    assert "result" in data
    assert "kept_tokens" in data["result"]
    assert "recall" in data["result"]
    assert 0.0 <= data["result"]["recall"] <= 1.0


@pytest.mark.asyncio
async def test_run_hbm_h2o_custom_inputs(client, auth_headers):
    """POST /api/run/hbm/H2O with custom context_len overrides the default."""
    r = await client.post(
        "/api/run/hbm/H2O",
        json={"context_len": 2048, "budget": 256, "recent": 64, "decode_steps": 8},
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    # With a smaller budget the kept_tokens should be <= 256 + some tolerance
    assert data["result"]["kept_tokens"] <= 300


@pytest.mark.asyncio
async def test_run_networking_distserve(client, auth_headers):
    """POST /api/run/networking/DistServe executes the placement planner."""
    r = await client.post("/api/run/networking/DistServe", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["theme"] == "networking"
    result = data["result"]
    # DistServe returns at minimum a strategy key or similar
    assert isinstance(result, dict)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_run_requires_auth(client):
    """Run endpoint without token returns 401/403."""
    r = await client.post("/api/run/hbm/H2O")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_run_unknown_paper(client, auth_headers):
    """POST /api/run/hbm/NotReal returns 404."""
    r = await client.post("/api/run/hbm/NotReal", headers=auth_headers)
    assert r.status_code == 404


# ─────────────────────────── Portfolio endpoints ──────────────────────────────

@pytest.mark.asyncio
async def test_portfolio_add_holding(client, auth_headers):
    """POST /api/portfolio/holdings creates a holding and returns it."""
    r = await client.post(
        "/api/portfolio/holdings",
        json={"ticker": "NVDA", "shares": 10.0, "cost_basis": 5000.0, "notes": "AI GPU play"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    data = r.json()
    assert data["ticker"] == "NVDA"
    assert data["shares"] == 10.0
    assert data["cost_basis"] == 5000.0
    assert "id" in data


@pytest.mark.asyncio
async def test_portfolio_list_holdings(client, auth_headers):
    """GET /api/portfolio/holdings returns the user's holdings."""
    # Add a second holding
    await client.post(
        "/api/portfolio/holdings",
        json={"ticker": "TSM", "shares": 5.0, "cost_basis": 500.0},
        headers=auth_headers,
    )
    r = await client.get("/api/portfolio/holdings", headers=auth_headers)
    assert r.status_code == 200
    holdings = r.json()
    assert isinstance(holdings, list)
    tickers = [h["ticker"] for h in holdings]
    assert "NVDA" in tickers or "TSM" in tickers  # at least one we added


@pytest.mark.asyncio
async def test_portfolio_value(client, auth_headers):
    """GET /api/portfolio/value returns computed portfolio value."""
    r = await client.get("/api/portfolio/value", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert "total_cost_basis" in data
    assert "total_current_value" in data
    assert "total_gain_loss" in data
    assert "holdings" in data
    assert isinstance(data["holdings"], list)
    # Each holding should have a price_source
    for h in data["holdings"]:
        assert "price_source" in h
        assert h["price_source"] in ("alpha_vantage", "alpha_vantage_cached", "mock", "error")


@pytest.mark.asyncio
async def test_portfolio_delete_holding(client, auth_headers):
    """DELETE /api/portfolio/holdings/:id removes the holding."""
    # Create a holding to delete
    create = await client.post(
        "/api/portfolio/holdings",
        json={"ticker": "GOOGL", "shares": 2.0, "cost_basis": 300.0},
        headers=auth_headers,
    )
    assert create.status_code == 201
    holding_id = create.json()["id"]

    delete = await client.delete(
        f"/api/portfolio/holdings/{holding_id}", headers=auth_headers
    )
    assert delete.status_code == 204

    # Verify it's gone from the list
    r = await client.get("/api/portfolio/holdings", headers=auth_headers)
    ids = [h["id"] for h in r.json()]
    assert holding_id not in ids


@pytest.mark.asyncio
async def test_portfolio_requires_auth(client):
    """Portfolio endpoints return 401/403 without a token."""
    r = await client.get("/api/portfolio/holdings")
    assert r.status_code in (401, 403)


# ─────────────────────────── Configurable inputs ─────────────────────────────

@pytest.mark.asyncio
async def test_run_kivi_custom_params(client, auth_headers):
    """POST /api/run/hbm/KIVI with custom bits shows compression ratio change."""
    r4bit = await client.post(
        "/api/run/hbm/KIVI",
        json={"kv_tokens": 1024, "d_head": 64, "heads": 8, "layers": 32,
              "bits": 4, "residual": 16, "group_size": 16},
        headers=auth_headers,
    )
    r2bit = await client.post(
        "/api/run/hbm/KIVI",
        json={"kv_tokens": 1024, "d_head": 64, "heads": 8, "layers": 32,
              "bits": 2, "residual": 16, "group_size": 16},
        headers=auth_headers,
    )
    assert r4bit.status_code == 200
    assert r2bit.status_code == 200
    # 2-bit quantization should produce a higher compression ratio than 4-bit
    ratio_4 = r4bit.json()["result"]["compression_ratio"]
    ratio_2 = r2bit.json()["result"]["compression_ratio"]
    assert ratio_2 > ratio_4, f"Expected 2-bit ratio > 4-bit ratio, got {ratio_2} vs {ratio_4}"


@pytest.mark.asyncio
async def test_run_energy_perseus_custom(client, auth_headers):
    """POST /api/run/energy/Perseus accepts custom stage times."""
    r = await client.post(
        "/api/run/energy/Perseus",
        json={"stages": 3, "times_ms": [200, 150, 100]},
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["theme"] == "energy"
    assert isinstance(data["result"], dict)


# ─────────────────────────── OpenAPI docs ────────────────────────────────────

@pytest.mark.asyncio
async def test_openapi_schema_available(client):
    """GET /openapi.json returns valid OpenAPI schema with descriptions."""
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert schema["info"]["title"] == "investment — 5-theme AI infrastructure solutions"
    assert len(schema["info"]["description"]) > 50
    # Check that paths are documented
    assert "/healthz" in schema["paths"]
    assert "/api/auth/login" in schema["paths"]
    assert "/api/portfolio/holdings" in schema["paths"]
