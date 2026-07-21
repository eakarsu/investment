from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from passlib.context import CryptContext
from sqlalchemy import text

from backend.governed_trading import _canonical, evaluate_risk, verify_audit_chain


NOW = datetime.now(timezone.utc)


def risk(**overrides):
    values = {
        "side": "BUY", "quantity_micros": 10_000_000, "price_cents": 10_000,
        "available_micros": 1_000_000_000, "position_micros": 0,
        "observed_at": NOW, "now": NOW, "max_order_notional_cents": 500_000,
        "max_position_notional_cents": 2_500_000, "max_daily_loss_cents": 100_000,
        "daily_loss_cents": 0, "max_liquidity_participation_bps": 1_000,
        "kill_switch": False, "available_cash_cents": 10_000_000,
    }
    return evaluate_risk(**(values | overrides))


def test_deterministic_risk_accepts_a_bounded_fresh_paper_order():
    assert risk() == []


def test_deterministic_risk_blocks_stale_and_future_evidence():
    assert risk(observed_at=NOW - timedelta(minutes=6)) == ["stale_market_data"]
    assert risk(observed_at=NOW + timedelta(minutes=6)) == ["future_market_data"]


def test_deterministic_risk_blocks_notional_position_short_sale_and_reserved_exposure():
    assert "order_notional_limit" in risk(max_order_notional_cents=10_000)
    assert "position_exposure_limit" in risk(max_position_notional_cents=10_000)
    assert "insufficient_position" in risk(side="SELL")
    assert "position_exposure_limit" in risk(
        reserved_buy_micros=250_000_000, max_position_notional_cents=2_500_000,
    )


def test_deterministic_risk_blocks_loss_liquidity_cash_and_kill_switch():
    blockers = risk(
        daily_loss_cents=100_000, available_micros=20_000_000,
        max_liquidity_participation_bps=1_000, kill_switch=True,
        available_cash_cents=50_000,
    )
    assert blockers == [
        "kill_switch_active", "daily_loss_limit", "insufficient_cash",
        "liquidity_participation_limit",
    ]


def test_audit_verifier_detects_payload_and_linkage_tampering():
    rows = []
    prior = None
    for event_id, action in [("1", "ORDER_CREATED"), ("1", "ORDER_APPROVED")]:
        content = {
            "account_id": 1, "actor_id": 2, "action": action,
            "entity_type": "paper_order", "entity_id": event_id,
            "payload": {"ok": True}, "created_at": NOW.isoformat(),
        }
        event_hash = hashlib.sha256(f"{prior or ''}:{_canonical(content)}".encode()).hexdigest()
        rows.append(content | {"previous_hash": prior, "event_hash": event_hash, "created_at": NOW})
        prior = event_hash
    assert verify_audit_chain(rows)
    rows[1]["payload"] = {"ok": False}
    assert not verify_audit_chain(rows)


database_test = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1", reason="requires disposable PostgreSQL",
)


async def _login(client: AsyncClient, username: str) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
@database_test
async def test_governed_workflow_replays_partial_fills_custody_corrections_and_backtest():
    from backend.db import SessionLocal
    from backend.main import app

    password = CryptContext(schemes=["bcrypt"], deprecated="auto").hash("correct-horse-battery")
    async with SessionLocal() as db, db.begin():
        await db.execute(text("""
            TRUNCATE governed_audit_events,governed_backtest_runs,governed_reconciliation_runs,
              governed_error_corrections,governed_custody_snapshots,governed_ledger_entries,
              governed_paper_fills,governed_paper_orders,governed_corporate_actions,
              governed_positions,governed_risk_profiles,governed_market_events,
              governed_custody_grants,governed_sources,app_users RESTART IDENTITY CASCADE
        """))
        for username, role in [
            ("investor", "INVESTOR"), ("reviewer", "REVIEWER"),
            ("dataops", "DATA_OPS"), ("broker", "BROKER_OPS"), ("admin", "ADMIN"),
        ]:
            await db.execute(
                text("INSERT INTO app_users(username,email,hashed_password,role) VALUES (:username,:email,:password,:role)"),
                {"username": username, "email": f"{username}@example.test", "password": password, "role": role},
            )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        investor = await _login(client, "investor")
        reviewer = await _login(client, "reviewer")
        dataops = await _login(client, "dataops")
        broker = await _login(client, "broker")
        admin = await _login(client, "admin")

        source_specs = [
            ("licensed-market", "MARKET_DATA"),
            ("paper-broker", "PAPER_BROKER"),
            ("licensed-custody", "CUSTODY"),
            ("corporate-actions", "CORPORATE_ACTION"),
        ]
        for source_key, source_type in source_specs:
            payload = {
                "source_key": source_key, "source_type": source_type,
                "evidence_host": "licensed.example.test",
                "license_reference": f"contract-{source_key}-2026",
                "valid_until": (datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),
            }
            created = await client.post("/api/governed/sources", json=payload, headers=admin)
            assert created.status_code == 200, created.text
            replay = await client.post("/api/governed/sources", json=payload, headers=admin)
            assert replay.status_code == 200 and replay.json()["idempotent"] is True

        for operator_id in (3, 4):
            grant = await client.put(
                "/api/governed/custody-grants",
                json={
                    "account_id": 1, "operator_id": operator_id, "active": True,
                    "reason": "Approved operator assignment for the governed paper custody acceptance test.",
                },
                headers=admin,
            )
            assert grant.status_code == 200, grant.text

        market_payload = {
            "source": "licensed-market", "external_event_id": "quote-NVDA-001",
            "symbol": "NVDA", "price_cents": 10_000, "available_quantity": "1000",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "evidence_url": "https://licensed.example.test/quotes/001",
        }
        market = await client.post("/api/governed/market-events", json=market_payload, headers=dataops)
        assert market.status_code == 200, market.text
        assert (await client.post("/api/governed/market-events", json=market_payload, headers=dataops)).json()["idempotent"] is True
        conflict = await client.post(
            "/api/governed/market-events", json=market_payload | {"price_cents": 10_001}, headers=dataops,
        )
        assert conflict.status_code == 409

        order_payload = {
            "client_order_id": "client-order-0001", "market_event_id": market.json()["id"],
            "side": "BUY", "quantity": "10", "limit_price_cents": 10_000,
        }
        order_response = await client.post("/api/governed/orders", json=order_payload, headers=investor)
        assert order_response.status_code == 200, order_response.text
        order = order_response.json()["order"]
        assert order["status"] == "PENDING_REVIEW" and order["blockers"] == []
        assert (await client.post("/api/governed/orders", json=order_payload, headers=investor)).json()["idempotent"] is True
        assert (await client.post(
            "/api/governed/orders", json=order_payload | {"quantity": "11"}, headers=investor,
        )).status_code == 409

        decision = {
            "decision": "APPROVE",
            "attestation": "I independently reviewed the market evidence and deterministic risk checks",
            "reason": "", "expected_version": 1,
        }
        assert (await client.post(
            f"/api/governed/orders/{order['id']}/decision", json=decision, headers=investor,
        )).status_code == 403
        approved = await client.post(
            f"/api/governed/orders/{order['id']}/decision", json=decision, headers=reviewer,
        )
        assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"

        unavailable = await client.post(
            f"/api/governed/orders/{order['id']}/fills",
            json={
                "provider": "missing-broker", "external_event_id": "paper-fill-missing-001",
                "quantity": "1", "price_cents": 9_990,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "evidence_url": "https://licensed.example.test/paper-fills/missing",
            },
            headers=broker,
        )
        assert unavailable.status_code == 422

        fill_base = {
            "provider": "paper-broker", "external_event_id": "paper-fill-event-0001",
            "quantity": "4", "price_cents": 9_990,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "evidence_url": "https://licensed.example.test/paper-fills/001",
        }
        first_fill = await client.post(f"/api/governed/orders/{order['id']}/fills", json=fill_base, headers=broker)
        assert first_fill.status_code == 200 and first_fill.json()["status"] == "PARTIALLY_FILLED"
        assert (await client.post(
            f"/api/governed/orders/{order['id']}/fills", json=fill_base, headers=broker,
        )).json()["idempotent"] is True
        assert (await client.post(
            f"/api/governed/orders/{order['id']}/fills",
            json=fill_base | {"quantity": "5"}, headers=broker,
        )).status_code == 409
        second_fill = await client.post(
            f"/api/governed/orders/{order['id']}/fills",
            json=fill_base | {"external_event_id": "paper-fill-event-0002", "quantity": "6"},
            headers=broker,
        )
        assert second_fill.status_code == 200 and second_fill.json()["status"] == "FILLED"

        custody_base = {
            "account_id": 1, "source": "licensed-custody",
            "external_event_id": "custody-snapshot-0001",
            "as_of": datetime.now(timezone.utc).isoformat(), "cash_cents": 9_900_100,
            "daily_realized_loss_cents": 0, "positions": {"NVDA": "10"},
            "evidence_url": "https://licensed.example.test/custody/001",
        }
        custody = await client.post("/api/governed/custody-snapshots", json=custody_base, headers=dataops)
        assert custody.status_code == 200, custody.text
        assert (await client.post(
            "/api/governed/custody-snapshots", json=custody_base, headers=dataops,
        )).json()["idempotent"] is True
        assert (await client.post(
            "/api/governed/custody-snapshots",
            json=custody_base | {"cash_cents": 9_900_101}, headers=dataops,
        )).status_code == 409
        reconciliation = await client.post("/api/governed/reconcile", headers=investor)
        assert reconciliation.status_code == 200 and reconciliation.json()["status"] == "PASS", reconciliation.text

        action_payload = {
            "source": "corporate-actions", "external_event_id": "corporate-action-0001",
            "symbol": "NVDA", "action_type": "SPLIT", "numerator": 2, "denominator": 1,
            "effective_at": datetime.now(timezone.utc).isoformat(),
            "evidence_url": "https://licensed.example.test/actions/001",
            "reason": "Authoritative two-for-one split adjustment from the licensed corporate-action feed.",
        }
        action = await client.post("/api/governed/corporate-actions", json=action_payload, headers=dataops)
        assert action.status_code == 200 and action.json()["positions_updated"] == 1, action.text
        assert (await client.post(
            "/api/governed/corporate-actions", json=action_payload, headers=dataops,
        )).json()["idempotent"] is True

        custody_after_split = custody_base | {
            "external_event_id": "custody-snapshot-0002", "as_of": datetime.now(timezone.utc).isoformat(),
            "positions": {"NVDA": "20"}, "evidence_url": "https://licensed.example.test/custody/002",
        }
        assert (await client.post(
            "/api/governed/custody-snapshots", json=custody_after_split, headers=dataops,
        )).status_code == 200

        async with SessionLocal() as db:
            first_ledger_id = (
                await db.execute(text("SELECT id FROM governed_ledger_entries WHERE entry_type='PAPER_FILL' ORDER BY id LIMIT 1"))
            ).scalar_one()
        correction_payload = {
            "account_id": 1, "source": "paper-broker",
            "external_event_id": "broker-correction-0001",
            "original_ledger_entry_id": first_ledger_id, "symbol": "NVDA",
            "quantity_delta": "-1", "amount_delta_cents": 9_990,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "evidence_url": "https://licensed.example.test/corrections/001",
            "reason": "Compensating correction for one duplicated paper unit in provider settlement evidence.",
        }
        correction = await client.post(
            "/api/governed/error-corrections", json=correction_payload, headers=broker,
        )
        assert correction.status_code == 200, correction.text
        assert (await client.post(
            "/api/governed/error-corrections", json=correction_payload, headers=broker,
        )).json()["idempotent"] is True
        assert (await client.post(
            "/api/governed/error-corrections",
            json=correction_payload | {"amount_delta_cents": 9_991}, headers=broker,
        )).status_code == 409

        custody_after_correction = custody_base | {
            "external_event_id": "custody-snapshot-0003", "as_of": datetime.now(timezone.utc).isoformat(),
            "cash_cents": 9_910_090, "positions": {"NVDA": "19"},
            "evidence_url": "https://licensed.example.test/custody/003",
        }
        assert (await client.post(
            "/api/governed/custody-snapshots", json=custody_after_correction, headers=dataops,
        )).status_code == 200
        reconciliation = await client.post("/api/governed/reconcile", headers=investor)
        assert reconciliation.status_code == 200 and reconciliation.json()["status"] == "PASS", reconciliation.text

        backtest_payload = {
            "scenario_id": "acceptance-scenario-0001", "as_of": datetime.now(timezone.utc).isoformat(),
            "cases": [
                {
                    "case_id": "fresh", "quantity": "10", "price_cents": 10_000,
                    "available_quantity": "1000", "position": "0", "observed_age_seconds": 0,
                    "max_order_notional_cents": 500_000, "max_position_notional_cents": 2_500_000,
                    "max_daily_loss_cents": 100_000, "daily_loss_cents": 0,
                    "max_liquidity_participation_bps": 1_000, "available_cash_cents": 10_000_000,
                    "expected_blockers": [],
                },
                {
                    "case_id": "stale", "quantity": "10", "price_cents": 10_000,
                    "available_quantity": "1000", "position": "0", "observed_age_seconds": 360,
                    "max_order_notional_cents": 500_000, "max_position_notional_cents": 2_500_000,
                    "max_daily_loss_cents": 100_000, "daily_loss_cents": 0,
                    "max_liquidity_participation_bps": 1_000, "available_cash_cents": 10_000_000,
                    "expected_blockers": ["stale_market_data"],
                },
                {
                    "case_id": "halted", "quantity": "10", "price_cents": 10_000,
                    "available_quantity": "1000", "position": "0", "observed_age_seconds": 0,
                    "max_order_notional_cents": 500_000, "max_position_notional_cents": 2_500_000,
                    "max_daily_loss_cents": 100_000, "daily_loss_cents": 100_000,
                    "max_liquidity_participation_bps": 1_000, "available_cash_cents": 10_000_000,
                    "kill_switch": True, "expected_blockers": ["kill_switch_active", "daily_loss_limit"],
                },
            ],
        }
        backtest = await client.post("/api/governed/backtests", json=backtest_payload, headers=investor)
        assert backtest.status_code == 200 and backtest.json()["run"]["passed"] is True, backtest.text
        assert (await client.post(
            "/api/governed/backtests", json=backtest_payload, headers=investor,
        )).json()["idempotent"] is True

        kill = await client.post(
            "/api/governed/risk/kill-switch",
            json={
                "account_id": 1, "active": True,
                "reason": "Operator-enforced stop after the scheduled paper-trading control exercise.",
            },
            headers=admin,
        )
        assert kill.status_code == 200
        blocked = await client.post(
            "/api/governed/orders",
            json=order_payload | {"client_order_id": "client-order-0002"}, headers=investor,
        )
        assert blocked.status_code == 200 and "kill_switch_active" in blocked.json()["order"]["blockers"]

        export = await client.get("/api/governed/audit/export", headers=investor)
        assert export.status_code == 200 and export.json()["audit_chain_valid"] is True
        assert {entry["entry_type"] for entry in export.json()["ledger"]} == {
            "PAPER_FILL", "CORPORATE_ACTION", "ERROR_CORRECTION",
        }

        revoked = await client.put(
            "/api/governed/custody-grants",
            json={
                "account_id": 1, "operator_id": 4, "active": False,
                "reason": "Immediate access revocation after completion of the broker acceptance exercise.",
            },
            headers=admin,
        )
        assert revoked.status_code == 200
        denied = await client.post(
            "/api/governed/error-corrections",
            json=correction_payload | {"external_event_id": "broker-correction-0002"}, headers=broker,
        )
        assert denied.status_code == 403

    async with SessionLocal() as db:
        events = [dict(row) for row in (
            await db.execute(text("SELECT * FROM governed_audit_events WHERE account_id=1 ORDER BY id"))
        ).mappings().all()]
        assert verify_audit_chain(events)
        position = (
            await db.execute(text("SELECT quantity_micros FROM governed_positions WHERE account_id=1 AND symbol='NVDA'"))
        ).scalar_one()
        cash = (
            await db.execute(text("SELECT cash_balance_cents FROM governed_risk_profiles WHERE account_id=1"))
        ).scalar_one()
        assert position == 19_000_000
        assert cash == 9_910_090
    async with SessionLocal() as db:
        with pytest.raises(Exception, match="append-only"):
            await db.execute(text("UPDATE governed_paper_fills SET price_cents=1"))
            await db.commit()

