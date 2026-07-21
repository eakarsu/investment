"""Governed paper-trading workflow.

This is the only production trading surface. It accepts licensed market-data
evidence, applies deterministic risk limits, requires independent review, and
records typed paper fills, corrections, reconciliation, and hash-chained audit
evidence. It never sends a live broker order.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from .auth import require_auth
from .config import settings
from .db import SessionLocal


router = APIRouter(prefix="/api/governed", tags=["governed-paper-trading"])

REVIEW_ATTESTATION = "I independently reviewed the market evidence and deterministic risk checks"
ALLOWED_ROLES = {"INVESTOR", "REVIEWER", "DATA_OPS", "BROKER_OPS", "ADMIN"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def verify_audit_chain(rows: list[dict]) -> bool:
    previous: str | None = None
    for row in rows:
        created_at = row["created_at"]
        content = {
            "account_id": int(row["account_id"]), "actor_id": int(row["actor_id"]),
            "action": row["action"], "entity_type": row["entity_type"],
            "entity_id": row["entity_id"], "payload": row["payload"],
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
        }
        expected = hashlib.sha256(f"{previous or ''}:{_canonical(content)}".encode()).hexdigest()
        if row["previous_hash"] != previous or row["event_hash"] != expected:
            return False
        previous = row["event_hash"]
    return True


def _micros(value: Decimal) -> int:
    try:
        scaled = value * 1_000_000
        integral = scaled.to_integral_exact()
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(422, "quantity supports at most six decimal places") from exc
    if integral <= 0 or integral > 10_000_000_000_000:
        raise HTTPException(422, "quantity is outside the supported paper-trading range")
    return int(integral)


def _signed_micros(value: Decimal) -> int:
    try:
        scaled = value * 1_000_000
        integral = scaled.to_integral_exact()
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(422, "quantity supports at most six decimal places") from exc
    if abs(integral) > 10_000_000_000_000:
        raise HTTPException(422, "quantity is outside the supported paper-trading range")
    return int(integral)


def _role(user: dict, *allowed: str) -> None:
    role = str(user.get("role", ""))
    if role not in ALLOWED_ROLES or role not in allowed:
        raise HTTPException(403, "role is not permitted for this action")


def _evidence_url(value: str, expected_host: str | None = None) -> str:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or hostname not in settings.market_data_host_list
        or (expected_host is not None and hostname != expected_host.lower())
    ):
        raise HTTPException(422, "evidence URL must use HTTPS and an allowlisted licensed-data host")
    return value


async def _source(db, source_key: str, source_type: str, evidence_url: str) -> tuple[dict, str]:
    row = (
        await db.execute(
            text("SELECT * FROM governed_sources WHERE source_key=:source"),
            {"source": source_key},
        )
    ).mappings().first()
    if not row or row["source_type"] != source_type:
        raise HTTPException(422, f"an active {source_type} source contract is required")
    if not row["active"] or row["valid_until"] <= datetime.now(timezone.utc):
        raise HTTPException(409, "source contract is disabled or expired")
    return dict(row), _evidence_url(evidence_url, str(row["evidence_host"]))


async def _custody_access(db, account_id: int, user: dict) -> None:
    if int(user["user_id"]) == account_id or user.get("role") == "ADMIN":
        return
    grant = (
        await db.execute(
            text("""
                SELECT active FROM governed_custody_grants
                WHERE account_id=:account AND operator_id=:operator
            """),
            {"account": account_id, "operator": int(user["user_id"])},
        )
    ).scalar_one_or_none()
    if grant is not True:
        raise HTTPException(403, "an active account custody grant is required")


def evaluate_risk(
    *, side: str, quantity_micros: int, price_cents: int, available_micros: int,
    position_micros: int, observed_at: datetime, now: datetime,
    max_order_notional_cents: int, max_position_notional_cents: int,
    max_daily_loss_cents: int, daily_loss_cents: int,
    max_liquidity_participation_bps: int, kill_switch: bool,
    reserved_buy_micros: int = 0, reserved_sell_micros: int = 0,
    available_cash_cents: int | None = None, reserved_cash_cents: int = 0,
) -> list[str]:
    """Return stable machine-readable blockers; no model output is involved."""
    blockers: list[str] = []
    notional = (quantity_micros * price_cents + 999_999) // 1_000_000
    effective_position = position_micros + reserved_buy_micros - reserved_sell_micros
    resulting_position = effective_position + quantity_micros if side == "BUY" else effective_position - quantity_micros
    if kill_switch:
        blockers.append("kill_switch_active")
    if now - observed_at > settings.market_data_max_age_seconds_delta:
        blockers.append("stale_market_data")
    if observed_at > now.replace(microsecond=now.microsecond) + settings.future_event_tolerance_delta:
        blockers.append("future_market_data")
    if notional > max_order_notional_cents:
        blockers.append("order_notional_limit")
    if resulting_position < 0:
        blockers.append("insufficient_position")
    if resulting_position * price_cents // 1_000_000 > max_position_notional_cents:
        blockers.append("position_exposure_limit")
    if daily_loss_cents >= max_daily_loss_cents:
        blockers.append("daily_loss_limit")
    if side == "BUY" and available_cash_cents is not None:
        if notional > max(0, available_cash_cents - reserved_cash_cents):
            blockers.append("insufficient_cash")
    if available_micros <= 0 or quantity_micros * 10_000 > available_micros * max_liquidity_participation_bps:
        blockers.append("liquidity_participation_limit")
    return blockers


async def _audit(db, *, account_id: int, actor_id: int, action: str, entity_type: str, entity_id: str, payload: dict) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(:account_id)"), {"account_id": account_id})
    prior = (
        await db.execute(
            text("SELECT event_hash FROM governed_audit_events WHERE account_id=:account_id ORDER BY id DESC LIMIT 1"),
            {"account_id": account_id},
        )
    ).scalar_one_or_none()
    created_at = datetime.now(timezone.utc)
    content = {
        "account_id": account_id, "actor_id": actor_id, "action": action,
        "entity_type": entity_type, "entity_id": entity_id, "payload": payload,
        "created_at": created_at.isoformat(),
    }
    event_hash = hashlib.sha256(f"{prior or ''}:{_canonical(content)}".encode()).hexdigest()
    await db.execute(
        text("""
            INSERT INTO governed_audit_events
              (account_id,actor_id,action,entity_type,entity_id,payload,previous_hash,event_hash,created_at)
            VALUES (:account_id,:actor_id,:action,:entity_type,:entity_id,CAST(:payload AS jsonb),:previous_hash,:event_hash,:created_at)
        """),
        {
            "account_id": account_id, "actor_id": actor_id, "action": action,
            "entity_type": entity_type, "entity_id": entity_id,
            "payload": _canonical(payload), "previous_hash": prior,
            "event_hash": event_hash, "created_at": created_at,
        },
    )


class MarketEventIn(BaseModel):
    source: str = Field(min_length=2, max_length=64)
    external_event_id: str = Field(min_length=4, max_length=128)
    symbol: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9.-]{0,14}$")
    price_cents: int = Field(gt=0, le=100_000_000)
    available_quantity: Decimal = Field(gt=0)
    observed_at: datetime
    evidence_url: str = Field(max_length=500)


class OrderIn(BaseModel):
    client_order_id: str = Field(min_length=8, max_length=128)
    market_event_id: int = Field(gt=0)
    side: Literal["BUY", "SELL"]
    quantity: Decimal = Field(gt=0)
    limit_price_cents: int = Field(gt=0, le=100_000_000)


class DecisionIn(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    attestation: str = Field(min_length=1, max_length=300)
    reason: str = Field(default="", max_length=1000)
    expected_version: int = Field(gt=0)


class FillIn(BaseModel):
    provider: str = Field(min_length=2, max_length=64)
    external_event_id: str = Field(min_length=8, max_length=128)
    quantity: Decimal = Field(gt=0)
    price_cents: int = Field(gt=0, le=100_000_000)
    occurred_at: datetime
    evidence_url: str = Field(max_length=500)


class CorporateActionIn(BaseModel):
    source: str = Field(min_length=2, max_length=64)
    external_event_id: str = Field(min_length=8, max_length=128)
    symbol: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9.-]{0,14}$")
    action_type: Literal["SPLIT"]
    numerator: int = Field(gt=0, le=1000)
    denominator: int = Field(gt=0, le=1000)
    effective_at: datetime
    evidence_url: str = Field(max_length=500)
    reason: str = Field(min_length=20, max_length=1000)


class KillSwitchIn(BaseModel):
    account_id: int = Field(gt=0)
    active: bool
    reason: str = Field(min_length=20, max_length=1000)


class SourceIn(BaseModel):
    source_key: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,63}$")
    source_type: Literal["MARKET_DATA", "PAPER_BROKER", "CUSTODY", "CORPORATE_ACTION"]
    evidence_host: str = Field(pattern=r"^[A-Za-z0-9.-]+$", max_length=255)
    license_reference: str = Field(min_length=8, max_length=255)
    valid_until: datetime


class CustodyGrantIn(BaseModel):
    account_id: int = Field(gt=0)
    operator_id: int = Field(gt=0)
    active: bool
    reason: str = Field(min_length=20, max_length=1000)


class CustodySnapshotIn(BaseModel):
    account_id: int = Field(gt=0)
    source: str = Field(min_length=2, max_length=64)
    external_event_id: str = Field(min_length=8, max_length=128)
    as_of: datetime
    cash_cents: int = Field(ge=0)
    daily_realized_loss_cents: int = Field(ge=0)
    positions: dict[str, Decimal]
    evidence_url: str = Field(max_length=500)


class CorrectionIn(BaseModel):
    account_id: int = Field(gt=0)
    source: str = Field(min_length=2, max_length=64)
    external_event_id: str = Field(min_length=8, max_length=128)
    original_ledger_entry_id: int = Field(gt=0)
    symbol: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9.-]{0,14}$")
    quantity_delta: Decimal
    amount_delta_cents: int
    occurred_at: datetime
    evidence_url: str = Field(max_length=500)
    reason: str = Field(min_length=20, max_length=1000)


class BacktestCase(BaseModel):
    case_id: str = Field(min_length=2, max_length=64)
    side: Literal["BUY", "SELL"] = "BUY"
    quantity: Decimal = Field(gt=0)
    price_cents: int = Field(gt=0, le=100_000_000)
    available_quantity: Decimal = Field(gt=0)
    position: Decimal = Field(ge=0)
    observed_age_seconds: int = Field(ge=-3600, le=604800)
    max_order_notional_cents: int = Field(gt=0)
    max_position_notional_cents: int = Field(gt=0)
    max_daily_loss_cents: int = Field(gt=0)
    daily_loss_cents: int = Field(ge=0)
    max_liquidity_participation_bps: int = Field(ge=1, le=10000)
    kill_switch: bool = False
    available_cash_cents: int = Field(ge=0)
    expected_blockers: list[str] = Field(max_length=16)


class BacktestIn(BaseModel):
    scenario_id: str = Field(min_length=8, max_length=128)
    as_of: datetime
    cases: list[BacktestCase] = Field(min_length=1, max_length=100)


@router.post("/sources")
async def register_source(body: SourceIn, user: Annotated[dict, Depends(require_auth)]):
    _role(user, "ADMIN")
    if body.valid_until.tzinfo is None or body.valid_until <= datetime.now(timezone.utc) + timedelta(days=1):
        raise HTTPException(422, "source validity must be timezone-aware and extend beyond one day")
    host = body.evidence_host.lower()
    if host not in settings.market_data_host_list:
        raise HTTPException(422, "source evidence host is not in MARKET_DATA_ALLOWED_HOSTS")
    normalized = body.model_dump(mode="json") | {"evidence_host": host}
    config_hash = _hash(normalized)
    actor = int(user["user_id"])
    async with SessionLocal() as db, db.begin():
        existing = (
            await db.execute(
                text("SELECT * FROM governed_sources WHERE source_key=:source"),
                {"source": body.source_key},
            )
        ).mappings().first()
        if existing:
            if existing["config_hash"] != config_hash:
                raise HTTPException(409, "source key already identifies a different immutable contract")
            return {"source": dict(existing), "idempotent": True}
        source = (
            await db.execute(
                text("""
                    INSERT INTO governed_sources
                      (source_key,source_type,evidence_host,license_reference,valid_until,config_hash,created_by)
                    VALUES (:source_key,:source_type,:evidence_host,:license_reference,:valid_until,:config_hash,:actor)
                    RETURNING *
                """),
                normalized | {"valid_until": body.valid_until, "config_hash": config_hash, "actor": actor},
            )
        ).mappings().one()
        await _audit(
            db, account_id=actor, actor_id=actor, action="SOURCE_REGISTERED",
            entity_type="source", entity_id=str(source["id"]),
            payload={"source_key": body.source_key, "source_type": body.source_type, "config_hash": config_hash},
        )
    return {"source": dict(source), "idempotent": False}


@router.post("/sources/{source_key}/disable")
async def disable_source(
    source_key: str,
    reason: Annotated[str, Query(min_length=20, max_length=1000)],
    user: Annotated[dict, Depends(require_auth)],
):
    _role(user, "ADMIN")
    actor = int(user["user_id"])
    async with SessionLocal() as db, db.begin():
        source = (
            await db.execute(
                text("SELECT * FROM governed_sources WHERE source_key=:source FOR UPDATE"),
                {"source": source_key},
            )
        ).mappings().first()
        if not source:
            raise HTTPException(404, "source not found")
        if not source["active"]:
            return {"source_key": source_key, "active": False, "idempotent": True}
        await db.execute(
            text("""
                UPDATE governed_sources SET active=FALSE,disabled_at=NOW(),disabled_by=:actor,
                  disable_reason=:reason,version=version+1 WHERE id=:id
            """),
            {"actor": actor, "reason": reason, "id": source["id"]},
        )
        await _audit(
            db, account_id=actor, actor_id=actor, action="SOURCE_DISABLED",
            entity_type="source", entity_id=str(source["id"]), payload={"reason": reason},
        )
    return {"source_key": source_key, "active": False, "idempotent": False}


@router.put("/custody-grants")
async def set_custody_grant(body: CustodyGrantIn, user: Annotated[dict, Depends(require_auth)]):
    _role(user, "ADMIN")
    if body.account_id == body.operator_id:
        raise HTTPException(422, "account owner does not require a custody grant")
    actor = int(user["user_id"])
    async with SessionLocal() as db, db.begin():
        roles = {
            int(row["id"]): row["role"]
            for row in (
                await db.execute(
                    text("SELECT id,role FROM app_users WHERE id IN (:account,:operator) AND is_active=TRUE"),
                    {"account": body.account_id, "operator": body.operator_id},
                )
            ).mappings().all()
        }
        if roles.get(body.account_id) != "INVESTOR":
            raise HTTPException(422, "custody account must identify an active investor")
        if roles.get(body.operator_id) not in {"BROKER_OPS", "DATA_OPS", "REVIEWER"}:
            raise HTTPException(422, "operator must have an operational or reviewer role")
        existing = (
            await db.execute(
                text("""
                    SELECT * FROM governed_custody_grants
                    WHERE account_id=:account AND operator_id=:operator FOR UPDATE
                """),
                {"account": body.account_id, "operator": body.operator_id},
            )
        ).mappings().first()
        if existing and bool(existing["active"]) == body.active and existing["reason"] == body.reason:
            return {"account_id": body.account_id, "operator_id": body.operator_id, "active": body.active, "idempotent": True}
        if existing:
            await db.execute(
                text("""
                    UPDATE governed_custody_grants SET active=:active,reason=:reason,granted_by=:actor,
                      updated_at=NOW(),version=version+1 WHERE account_id=:account AND operator_id=:operator
                """),
                body.model_dump() | {"actor": actor, "account": body.account_id, "operator": body.operator_id},
            )
        else:
            await db.execute(
                text("""
                    INSERT INTO governed_custody_grants(account_id,operator_id,active,reason,granted_by)
                    VALUES (:account_id,:operator_id,:active,:reason,:actor)
                """),
                body.model_dump() | {"actor": actor},
            )
        await _audit(
            db, account_id=body.account_id, actor_id=actor, action="CUSTODY_GRANT_CHANGED",
            entity_type="custody_grant", entity_id=f"{body.account_id}:{body.operator_id}",
            payload={"active": body.active, "reason": body.reason},
        )
    return {"account_id": body.account_id, "operator_id": body.operator_id, "active": body.active, "idempotent": False}


@router.post("/market-events")
async def ingest_market_event(body: MarketEventIn, user: Annotated[dict, Depends(require_auth)]):
    _role(user, "DATA_OPS", "ADMIN")
    if body.observed_at.tzinfo is None:
        raise HTTPException(422, "observed_at must include a timezone")
    if body.observed_at > datetime.now(timezone.utc) + settings.future_event_tolerance_delta:
        raise HTTPException(422, "observed_at is too far in the future")
    available_micros = _micros(body.available_quantity)
    async with SessionLocal() as db, db.begin():
        source, evidence_url = await _source(db, body.source, "MARKET_DATA", body.evidence_url)
        normalized = {
            "source": body.source, "source_system_id": source["id"],
            "external_event_id": body.external_event_id,
            "symbol": body.symbol.upper(), "price_cents": body.price_cents,
            "available_micros": available_micros, "observed_at": body.observed_at.isoformat(),
            "evidence_url": evidence_url,
        }
        payload_hash = _hash(normalized)
        existing = (
            await db.execute(
                text("SELECT * FROM governed_market_events WHERE source=:source AND external_event_id=:event"),
                {"source": body.source, "event": body.external_event_id},
            )
        ).mappings().first()
        if existing:
            if existing["payload_hash"] != payload_hash:
                raise HTTPException(409, "market event replay payload conflicts with recorded evidence")
            return {"id": existing["id"], "idempotent": True}
        event_id = (
            await db.execute(
                text("""
                    INSERT INTO governed_market_events
                      (source,source_system_id,external_event_id,symbol,price_cents,available_quantity_micros,observed_at,evidence_url,payload_hash)
                    VALUES (:source,:source_system_id,:external_event_id,:symbol,:price_cents,:available_micros,:observed_at,:evidence_url,:payload_hash)
                    RETURNING id
                """), normalized | {"payload_hash": payload_hash, "observed_at": body.observed_at},
            )
        ).scalar_one()
        await _audit(db, account_id=int(user["user_id"]), actor_id=int(user["user_id"]), action="MARKET_EVENT_INGESTED", entity_type="market_event", entity_id=str(event_id), payload=normalized)
    return {"id": event_id, "idempotent": False}


@router.post("/orders")
async def create_order(body: OrderIn, user: Annotated[dict, Depends(require_auth)]):
    _role(user, "INVESTOR")
    account_id = int(user["user_id"])
    quantity_micros = _micros(body.quantity)
    request_hash = _hash(body.model_dump(mode="json"))
    async with SessionLocal() as db, db.begin():
        existing = (
            await db.execute(
                text("SELECT * FROM governed_paper_orders WHERE account_id=:account AND client_order_id=:client"),
                {"account": account_id, "client": body.client_order_id},
            )
        ).mappings().first()
        if existing:
            if existing["request_hash"] != request_hash:
                raise HTTPException(409, "client_order_id was already used with different terms")
            return {"order": dict(existing), "idempotent": True}
        market = (
            await db.execute(text("SELECT * FROM governed_market_events WHERE id=:id"), {"id": body.market_event_id})
        ).mappings().first()
        if not market:
            raise HTTPException(404, "market event not found")
        await db.execute(
            text("INSERT INTO governed_risk_profiles(account_id) VALUES (:account) ON CONFLICT (account_id) DO NOTHING"),
            {"account": account_id},
        )
        risk = (
            await db.execute(text("SELECT * FROM governed_risk_profiles WHERE account_id=:account FOR UPDATE"), {"account": account_id})
        ).mappings().one()
        position = (
            await db.execute(
                text("SELECT quantity_micros FROM governed_positions WHERE account_id=:account AND symbol=:symbol"),
                {"account": account_id, "symbol": market["symbol"]},
            )
        ).scalar_one_or_none() or 0
        reservations = (
            await db.execute(
                text("""
                    SELECT
                      COALESCE(SUM(CASE WHEN side='BUY' AND symbol=:symbol
                        THEN quantity_micros-filled_quantity_micros ELSE 0 END),0)::bigint AS buys,
                      COALESCE(SUM(CASE WHEN side='SELL' AND symbol=:symbol
                        THEN quantity_micros-filled_quantity_micros ELSE 0 END),0)::bigint AS sells,
                      COALESCE(SUM(CASE WHEN side='BUY' THEN
                        ((quantity_micros-filled_quantity_micros)*limit_price_cents+999999)/1000000
                        ELSE 0 END),0)::bigint AS cash
                    FROM governed_paper_orders
                    WHERE account_id=:account AND status IN ('PENDING_REVIEW','APPROVED','PARTIALLY_FILLED')
                """),
                {"account": account_id, "symbol": market["symbol"]},
            )
        ).mappings().one()
        now = datetime.now(timezone.utc)
        blockers = evaluate_risk(
            side=body.side, quantity_micros=quantity_micros,
            price_cents=int(market["price_cents"]), available_micros=int(market["available_quantity_micros"]),
            position_micros=int(position), observed_at=market["observed_at"], now=now,
            max_order_notional_cents=int(risk["max_order_notional_cents"]),
            max_position_notional_cents=int(risk["max_position_notional_cents"]),
            max_daily_loss_cents=int(risk["max_daily_loss_cents"]), daily_loss_cents=int(risk["daily_loss_cents"]),
            max_liquidity_participation_bps=int(risk["max_liquidity_participation_bps"]),
            kill_switch=bool(risk["kill_switch"]),
            reserved_buy_micros=int(reservations["buys"]),
            reserved_sell_micros=int(reservations["sells"]),
            available_cash_cents=int(risk["cash_balance_cents"]),
            reserved_cash_cents=int(reservations["cash"]),
        )
        if body.side == "BUY" and int(market["price_cents"]) > body.limit_price_cents:
            blockers.append("price_above_limit")
        if body.side == "SELL" and int(market["price_cents"]) < body.limit_price_cents:
            blockers.append("price_below_limit")
        status = "BLOCKED" if blockers else "PENDING_REVIEW"
        order = (
            await db.execute(
                text("""
                    INSERT INTO governed_paper_orders
                      (account_id,client_order_id,request_hash,market_event_id,symbol,side,quantity_micros,limit_price_cents,status,blockers,created_by)
                    VALUES (:account,:client,:request_hash,:market_event_id,:symbol,:side,:quantity,:limit_price,:status,CAST(:blockers AS jsonb),:actor)
                    RETURNING *
                """),
                {"account": account_id, "client": body.client_order_id, "request_hash": request_hash,
                 "market_event_id": body.market_event_id, "symbol": market["symbol"], "side": body.side,
                 "quantity": quantity_micros, "limit_price": body.limit_price_cents, "status": status,
                 "blockers": _canonical(blockers), "actor": account_id},
            )
        ).mappings().one()
        await _audit(db, account_id=account_id, actor_id=account_id, action="ORDER_CREATED", entity_type="paper_order", entity_id=str(order["id"]), payload={"status": status, "blockers": blockers, "request_hash": request_hash})
    return {"order": dict(order), "idempotent": False}


@router.post("/orders/{order_id}/decision")
async def decide_order(order_id: int, body: DecisionIn, user: Annotated[dict, Depends(require_auth)]):
    _role(user, "REVIEWER")
    actor = int(user["user_id"])
    if body.attestation != REVIEW_ATTESTATION:
        raise HTTPException(422, "exact independent-review attestation is required")
    if body.decision == "REJECT" and len(body.reason.strip()) < 20:
        raise HTTPException(422, "rejection requires a substantive reason")
    async with SessionLocal() as db, db.begin():
        order = (
            await db.execute(text("SELECT * FROM governed_paper_orders WHERE id=:id FOR UPDATE"), {"id": order_id})
        ).mappings().first()
        if not order:
            raise HTTPException(404, "order not found")
        if int(order["created_by"]) == actor:
            raise HTTPException(403, "order author cannot review their own order")
        if order["status"] != "PENDING_REVIEW" or order["blockers"]:
            raise HTTPException(409, "only an unblocked pending order can be decided")
        if int(order["version"]) != body.expected_version:
            raise HTTPException(409, "stale order version")
        market = (
            await db.execute(
                text("SELECT * FROM governed_market_events WHERE id=:id"),
                {"id": order["market_event_id"]},
            )
        ).mappings().one()
        if body.decision == "APPROVE":
            await _source(db, str(market["source"]), "MARKET_DATA", str(market["evidence_url"]))
        risk = (
            await db.execute(
                text("SELECT * FROM governed_risk_profiles WHERE account_id=:account FOR UPDATE"),
                {"account": order["account_id"]},
            )
        ).mappings().one()
        position = (
            await db.execute(
                text("SELECT quantity_micros FROM governed_positions WHERE account_id=:account AND symbol=:symbol"),
                {"account": order["account_id"], "symbol": order["symbol"]},
            )
        ).scalar_one_or_none() or 0
        reservations = (
            await db.execute(
                text("""
                    SELECT
                      COALESCE(SUM(CASE WHEN side='BUY' AND symbol=:symbol
                        THEN quantity_micros-filled_quantity_micros ELSE 0 END),0)::bigint AS buys,
                      COALESCE(SUM(CASE WHEN side='SELL' AND symbol=:symbol
                        THEN quantity_micros-filled_quantity_micros ELSE 0 END),0)::bigint AS sells,
                      COALESCE(SUM(CASE WHEN side='BUY' THEN
                        ((quantity_micros-filled_quantity_micros)*limit_price_cents+999999)/1000000
                        ELSE 0 END),0)::bigint AS cash
                    FROM governed_paper_orders WHERE account_id=:account AND id<>:id
                      AND status IN ('PENDING_REVIEW','APPROVED','PARTIALLY_FILLED')
                """),
                {"account": order["account_id"], "symbol": order["symbol"], "id": order_id},
            )
        ).mappings().one()
        blockers = evaluate_risk(
            side=str(order["side"]), quantity_micros=int(order["quantity_micros"]),
            price_cents=int(market["price_cents"]),
            available_micros=int(market["available_quantity_micros"]),
            position_micros=int(position), observed_at=market["observed_at"],
            now=datetime.now(timezone.utc),
            max_order_notional_cents=int(risk["max_order_notional_cents"]),
            max_position_notional_cents=int(risk["max_position_notional_cents"]),
            max_daily_loss_cents=int(risk["max_daily_loss_cents"]),
            daily_loss_cents=int(risk["daily_loss_cents"]),
            max_liquidity_participation_bps=int(risk["max_liquidity_participation_bps"]),
            kill_switch=bool(risk["kill_switch"]),
            reserved_buy_micros=int(reservations["buys"]),
            reserved_sell_micros=int(reservations["sells"]),
            available_cash_cents=int(risk["cash_balance_cents"]),
            reserved_cash_cents=int(reservations["cash"]),
        )
        if body.decision == "APPROVE" and blockers:
            raise HTTPException(409, {"message": "deterministic risk checks no longer pass", "blockers": blockers})
        status = "APPROVED" if body.decision == "APPROVE" else "REJECTED"
        updated = (
            await db.execute(
                text("""UPDATE governed_paper_orders SET status=:status,reviewed_by=:actor,
                     review_attestation=:attestation,review_reason=:reason,reviewed_at=NOW(),version=version+1
                     WHERE id=:id RETURNING *"""),
                {"status": status, "actor": actor, "attestation": body.attestation, "reason": body.reason, "id": order_id},
            )
        ).mappings().one()
        await _audit(db, account_id=int(order["account_id"]), actor_id=actor, action=f"ORDER_{status}", entity_type="paper_order", entity_id=str(order_id), payload={"reason": body.reason, "version": updated["version"]})
    return dict(updated)


@router.post("/orders/{order_id}/fills")
async def record_fill(order_id: int, body: FillIn, user: Annotated[dict, Depends(require_auth)]):
    _role(user, "BROKER_OPS")
    actor = int(user["user_id"])
    if body.occurred_at.tzinfo is None:
        raise HTTPException(422, "occurred_at must include a timezone")
    if body.occurred_at > datetime.now(timezone.utc) + settings.future_event_tolerance_delta:
        raise HTTPException(422, "fill timestamp is too far in the future")
    if datetime.now(timezone.utc) - body.occurred_at > settings.market_data_max_age_seconds_delta:
        raise HTTPException(422, "fill timestamp is stale")
    quantity_micros = _micros(body.quantity)
    async with SessionLocal() as db, db.begin():
        source, evidence_url = await _source(db, body.provider, "PAPER_BROKER", body.evidence_url)
        payload = body.model_dump(mode="json") | {
            "source_system_id": source["id"], "quantity_micros": quantity_micros,
            "evidence_url": evidence_url,
        }
        payload_hash = _hash(payload)
        existing = (
            await db.execute(
                text("SELECT * FROM governed_paper_fills WHERE provider=:provider AND external_event_id=:event"),
                {"provider": body.provider, "event": body.external_event_id},
            )
        ).mappings().first()
        if existing:
            if existing["payload_hash"] != payload_hash or int(existing["order_id"]) != order_id:
                raise HTTPException(409, "fill replay conflicts with recorded provider evidence")
            return {"fill": dict(existing), "idempotent": True}
        order = (
            await db.execute(text("SELECT * FROM governed_paper_orders WHERE id=:id FOR UPDATE"), {"id": order_id})
        ).mappings().first()
        if not order:
            raise HTTPException(404, "order not found")
        await _custody_access(db, int(order["account_id"]), user)
        if order["status"] not in {"APPROVED", "PARTIALLY_FILLED"}:
            raise HTTPException(409, "order is not fillable")
        remaining = int(order["quantity_micros"]) - int(order["filled_quantity_micros"])
        if quantity_micros > remaining:
            raise HTTPException(422, "fill exceeds remaining paper quantity")
        if order["side"] == "BUY" and body.price_cents > int(order["limit_price_cents"]):
            raise HTTPException(422, "paper fill violates buy limit")
        if order["side"] == "SELL" and body.price_cents < int(order["limit_price_cents"]):
            raise HTTPException(422, "paper fill violates sell limit")
        if quantity_micros * body.price_cents % 1_000_000:
            raise HTTPException(422, "paper fill notional must settle to a whole cent")
        market = (
            await db.execute(
                text("SELECT * FROM governed_market_events WHERE id=:id"),
                {"id": order["market_event_id"]},
            )
        ).mappings().one()
        await _source(db, str(market["source"]), "MARKET_DATA", str(market["evidence_url"]))
        risk = (
            await db.execute(
                text("SELECT * FROM governed_risk_profiles WHERE account_id=:account FOR UPDATE"),
                {"account": order["account_id"]},
            )
        ).mappings().one()
        current_position = (
            await db.execute(
                text("SELECT quantity_micros FROM governed_positions WHERE account_id=:account AND symbol=:symbol"),
                {"account": order["account_id"], "symbol": order["symbol"]},
            )
        ).scalar_one_or_none() or 0
        blockers = evaluate_risk(
            side=str(order["side"]), quantity_micros=quantity_micros,
            price_cents=body.price_cents, available_micros=int(market["available_quantity_micros"]),
            position_micros=int(current_position), observed_at=market["observed_at"],
            now=datetime.now(timezone.utc),
            max_order_notional_cents=int(risk["max_order_notional_cents"]),
            max_position_notional_cents=int(risk["max_position_notional_cents"]),
            max_daily_loss_cents=int(risk["max_daily_loss_cents"]),
            daily_loss_cents=int(risk["daily_loss_cents"]),
            max_liquidity_participation_bps=int(risk["max_liquidity_participation_bps"]),
            kill_switch=bool(risk["kill_switch"]),
            available_cash_cents=int(risk["cash_balance_cents"]),
        )
        if blockers:
            raise HTTPException(409, {"message": "fill-time deterministic risk checks failed", "blockers": blockers})
        fill = (
            await db.execute(
                text("""
                    INSERT INTO governed_paper_fills
                      (order_id,source_system_id,provider,external_event_id,payload_hash,quantity_micros,price_cents,occurred_at,evidence_url)
                    VALUES (:order_id,:source_system_id,:provider,:external_event_id,:payload_hash,:quantity,:price,:occurred_at,:evidence_url)
                    RETURNING *
                """),
                {"order_id": order_id, "source_system_id": source["id"],
                 "provider": body.provider, "external_event_id": body.external_event_id,
                 "payload_hash": payload_hash, "quantity": quantity_micros, "price": body.price_cents,
                 "occurred_at": body.occurred_at, "evidence_url": evidence_url},
            )
        ).mappings().one()
        signed = quantity_micros if order["side"] == "BUY" else -quantity_micros
        current = (
            await db.execute(
                text("SELECT * FROM governed_positions WHERE account_id=:account AND symbol=:symbol FOR UPDATE"),
                {"account": order["account_id"], "symbol": order["symbol"]},
            )
        ).mappings().first()
        current_qty = int(current["quantity_micros"]) if current else 0
        new_qty = current_qty + signed
        if new_qty < 0:
            raise HTTPException(409, "position changed after approval; sell fill would become short")
        await db.execute(
            text("""
                INSERT INTO governed_positions(account_id,symbol,quantity_micros,version)
                VALUES (:account,:symbol,:quantity,1)
                ON CONFLICT (account_id,symbol) DO UPDATE SET quantity_micros=:quantity,version=governed_positions.version+1,updated_at=NOW()
            """),
            {"account": order["account_id"], "symbol": order["symbol"], "quantity": new_qty},
        )
        new_filled = int(order["filled_quantity_micros"]) + quantity_micros
        new_status = "FILLED" if new_filled == int(order["quantity_micros"]) else "PARTIALLY_FILLED"
        amount_cents = quantity_micros * body.price_cents // 1_000_000
        cash_delta_cents = -amount_cents if order["side"] == "BUY" else amount_cents
        cash_balance = int(risk["cash_balance_cents"]) + cash_delta_cents
        if cash_balance < 0:
            raise HTTPException(409, "paper cash changed after approval; fill would overdraw custody")
        await db.execute(
            text("UPDATE governed_paper_orders SET filled_quantity_micros=:filled,status=:status,version=version+1,updated_at=NOW() WHERE id=:id"),
            {"filled": new_filled, "status": new_status, "id": order_id},
        )
        await db.execute(
            text("""
                UPDATE governed_risk_profiles SET cash_balance_cents=:cash,updated_at=NOW()
                WHERE account_id=:account
            """),
            {"cash": cash_balance, "account": order["account_id"]},
        )
        await db.execute(
            text("""INSERT INTO governed_ledger_entries
              (account_id,order_id,entry_type,quantity_micros,amount_cents,source_event_id,metadata)
              VALUES (:account,:order_id,'PAPER_FILL',:quantity,:amount,:source,CAST(:metadata AS jsonb))"""),
            {"account": order["account_id"], "order_id": order_id, "quantity": signed,
             "amount": cash_delta_cents, "source": f"{body.provider}:{body.external_event_id}",
             "metadata": _canonical({"symbol": order["symbol"], "side": order["side"],
                                      "provider": body.provider, "evidence_url": evidence_url})},
        )
        await _audit(db, account_id=int(order["account_id"]), actor_id=actor, action="PAPER_FILL_RECORDED", entity_type="paper_fill", entity_id=str(fill["id"]), payload={"order_id": order_id, "status": new_status, "payload_hash": payload_hash})
    return {"fill": dict(fill), "status": new_status, "idempotent": False}


@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: int, reason: Annotated[str, Query(min_length=20, max_length=1000)], user: Annotated[dict, Depends(require_auth)]):
    actor = int(user["user_id"])
    async with SessionLocal() as db, db.begin():
        order = (
            await db.execute(text("SELECT * FROM governed_paper_orders WHERE id=:id FOR UPDATE"), {"id": order_id})
        ).mappings().first()
        if not order:
            raise HTTPException(404, "order not found")
        if actor != int(order["account_id"]) and user.get("role") != "ADMIN":
            raise HTTPException(403, "order ownership required")
        if order["status"] not in {"PENDING_REVIEW", "APPROVED", "PARTIALLY_FILLED"}:
            raise HTTPException(409, "order is not cancellable")
        await db.execute(text("UPDATE governed_paper_orders SET status='CANCELLED',version=version+1,updated_at=NOW() WHERE id=:id"), {"id": order_id})
        await _audit(db, account_id=int(order["account_id"]), actor_id=actor, action="ORDER_CANCELLED", entity_type="paper_order", entity_id=str(order_id), payload={"reason": reason})
    return {"id": order_id, "status": "CANCELLED"}


@router.post("/corporate-actions")
async def corporate_action(body: CorporateActionIn, user: Annotated[dict, Depends(require_auth)]):
    _role(user, "DATA_OPS", "ADMIN")
    actor = int(user["user_id"])
    if body.effective_at.tzinfo is None:
        raise HTTPException(422, "effective_at must include a timezone")
    async with SessionLocal() as db, db.begin():
        source, evidence_url = await _source(db, body.source, "CORPORATE_ACTION", body.evidence_url)
        payload = body.model_dump(mode="json") | {
            "source_system_id": source["id"], "symbol": body.symbol.upper(),
            "evidence_url": evidence_url,
        }
        payload_hash = _hash(payload)
        existing = (
            await db.execute(text("SELECT * FROM governed_corporate_actions WHERE source=:source AND external_event_id=:event"), {"source": body.source, "event": body.external_event_id})
        ).mappings().first()
        if existing:
            if existing["payload_hash"] != payload_hash:
                raise HTTPException(409, "corporate-action replay conflicts with recorded evidence")
            return {"id": existing["id"], "idempotent": True}
        action_id = (
            await db.execute(
                text("""INSERT INTO governed_corporate_actions
                  (source,source_system_id,external_event_id,payload_hash,symbol,action_type,numerator,denominator,effective_at,evidence_url,reason,recorded_by)
                  VALUES (:source,:source_system_id,:external_event_id,:payload_hash,:symbol,:action_type,:numerator,:denominator,:effective_at,:evidence_url,:reason,:actor)
                  RETURNING id"""),
                payload | {"actor": actor, "effective_at": body.effective_at, "payload_hash": payload_hash},
            )
        ).scalar_one()
        positions = (
            await db.execute(text("SELECT * FROM governed_positions WHERE symbol=:symbol FOR UPDATE"), {"symbol": body.symbol.upper()})
        ).mappings().all()
        for position in positions:
            old_qty = int(position["quantity_micros"])
            if old_qty * body.numerator % body.denominator:
                raise HTTPException(409, "corporate action would create unsupported fractional paper units")
            new_qty = old_qty * body.numerator // body.denominator
            await db.execute(text("UPDATE governed_positions SET quantity_micros=:quantity,version=version+1,updated_at=NOW() WHERE id=:id"), {"quantity": new_qty, "id": position["id"]})
            await db.execute(
                text("""INSERT INTO governed_ledger_entries
                  (account_id,entry_type,quantity_micros,amount_cents,source_event_id,metadata)
                  VALUES (:account,'CORPORATE_ACTION',:delta,0,:source,CAST(:metadata AS jsonb))"""),
                {"account": position["account_id"], "delta": new_qty - old_qty, "source": str(action_id),
                 "metadata": _canonical({"symbol": body.symbol.upper(), "reason": body.reason,
                                          "numerator": body.numerator, "denominator": body.denominator,
                                          "evidence_url": evidence_url})},
            )
            await _audit(db, account_id=int(position["account_id"]), actor_id=actor, action="CORPORATE_ACTION_APPLIED", entity_type="corporate_action", entity_id=str(action_id), payload={"symbol": body.symbol.upper(), "old_quantity_micros": old_qty, "new_quantity_micros": new_qty})
    return {"id": action_id, "positions_updated": len(positions), "idempotent": False}


@router.post("/error-corrections")
async def error_correction(body: CorrectionIn, user: Annotated[dict, Depends(require_auth)]):
    _role(user, "BROKER_OPS")
    actor = int(user["user_id"])
    if body.occurred_at.tzinfo is None:
        raise HTTPException(422, "occurred_at must include a timezone")
    if body.occurred_at > datetime.now(timezone.utc) + settings.future_event_tolerance_delta:
        raise HTTPException(422, "correction timestamp is too far in the future")
    quantity_delta = _signed_micros(body.quantity_delta)
    if quantity_delta == 0 and body.amount_delta_cents == 0:
        raise HTTPException(422, "a correction must include a quantity or cash delta")
    async with SessionLocal() as db, db.begin():
        await _custody_access(db, body.account_id, user)
        source, evidence_url = await _source(db, body.source, "PAPER_BROKER", body.evidence_url)
        normalized = body.model_dump(mode="json") | {
            "source_system_id": source["id"], "symbol": body.symbol.upper(),
            "quantity_delta_micros": quantity_delta, "evidence_url": evidence_url,
        }
        payload_hash = _hash(normalized)
        existing = (
            await db.execute(
                text("""
                    SELECT * FROM governed_error_corrections
                    WHERE account_id=:account AND source_system_id=:source AND external_event_id=:event
                """),
                {"account": body.account_id, "source": source["id"], "event": body.external_event_id},
            )
        ).mappings().first()
        if existing:
            if existing["payload_hash"] != payload_hash:
                raise HTTPException(409, "correction replay conflicts with recorded provider evidence")
            return {"correction": dict(existing), "idempotent": True}
        original = (
            await db.execute(
                text("SELECT * FROM governed_ledger_entries WHERE id=:id"),
                {"id": body.original_ledger_entry_id},
            )
        ).mappings().first()
        if not original or int(original["account_id"]) != body.account_id:
            raise HTTPException(422, "original ledger entry is outside the custody account")
        if original["entry_type"] != "PAPER_FILL":
            raise HTTPException(422, "corrections must reference an original paper-fill ledger entry")
        original_metadata = dict(original["metadata"])
        if original_metadata.get("symbol") != body.symbol.upper():
            raise HTTPException(422, "correction symbol does not match the original ledger evidence")
        prior_corrections = (
            await db.execute(
                text("""
                    SELECT COALESCE(SUM(quantity_delta_micros),0)::bigint AS quantity,
                      COALESCE(SUM(amount_delta_cents),0)::bigint AS amount
                    FROM governed_error_corrections WHERE original_ledger_entry_id=:id
                """),
                {"id": body.original_ledger_entry_id},
            )
        ).mappings().one()
        total_quantity_delta = int(prior_corrections["quantity"]) + quantity_delta
        total_amount_delta = int(prior_corrections["amount"]) + body.amount_delta_cents
        original_quantity = int(original["quantity_micros"])
        original_amount = int(original["amount_cents"])
        if (
            abs(total_quantity_delta) > abs(original_quantity)
            or total_quantity_delta * original_quantity > 0
            or abs(total_amount_delta) > abs(original_amount)
            or total_amount_delta * original_amount > 0
        ):
            raise HTTPException(409, "cumulative correction cannot exceed or reinforce the original ledger entry")
        position = (
            await db.execute(
                text("SELECT * FROM governed_positions WHERE account_id=:account AND symbol=:symbol FOR UPDATE"),
                {"account": body.account_id, "symbol": body.symbol.upper()},
            )
        ).mappings().first()
        current_qty = int(position["quantity_micros"]) if position else 0
        corrected_qty = current_qty + quantity_delta
        if corrected_qty < 0:
            raise HTTPException(409, "correction would create a short position")
        risk = (
            await db.execute(
                text("SELECT * FROM governed_risk_profiles WHERE account_id=:account FOR UPDATE"),
                {"account": body.account_id},
            )
        ).mappings().first()
        if not risk:
            raise HTTPException(409, "custody account has no initialized risk profile")
        corrected_cash = int(risk["cash_balance_cents"]) + body.amount_delta_cents
        if corrected_cash < 0:
            raise HTTPException(409, "correction would overdraw paper custody cash")
        correction = (
            await db.execute(
                text("""
                    INSERT INTO governed_error_corrections
                      (account_id,source_system_id,external_event_id,payload_hash,original_ledger_entry_id,
                       symbol,quantity_delta_micros,amount_delta_cents,reason,evidence_url,occurred_at,recorded_by)
                    VALUES (:account_id,:source_system_id,:external_event_id,:payload_hash,:original_ledger_entry_id,
                       :symbol,:quantity_delta_micros,:amount_delta_cents,:reason,:evidence_url,:occurred_at,:actor)
                    RETURNING *
                """),
                normalized | {
                    "account_id": body.account_id, "source_system_id": source["id"],
                    "payload_hash": payload_hash, "occurred_at": body.occurred_at, "actor": actor,
                },
            )
        ).mappings().one()
        await db.execute(
            text("""
                INSERT INTO governed_positions(account_id,symbol,quantity_micros,version)
                VALUES (:account,:symbol,:quantity,1)
                ON CONFLICT (account_id,symbol) DO UPDATE SET quantity_micros=:quantity,
                  version=governed_positions.version+1,updated_at=NOW()
            """),
            {"account": body.account_id, "symbol": body.symbol.upper(), "quantity": corrected_qty},
        )
        await db.execute(
            text("UPDATE governed_risk_profiles SET cash_balance_cents=:cash,updated_at=NOW() WHERE account_id=:account"),
            {"cash": corrected_cash, "account": body.account_id},
        )
        await db.execute(
            text("""
                INSERT INTO governed_ledger_entries
                  (account_id,entry_type,quantity_micros,amount_cents,source_event_id,metadata)
                VALUES (:account,'ERROR_CORRECTION',:quantity,:amount,:source,CAST(:metadata AS jsonb))
            """),
            {
                "account": body.account_id, "quantity": quantity_delta,
                "amount": body.amount_delta_cents,
                "source": f"{body.source}:{body.external_event_id}",
                "metadata": _canonical({"symbol": body.symbol.upper(),
                    "original_ledger_entry_id": body.original_ledger_entry_id,
                    "reason": body.reason, "evidence_url": evidence_url}),
            },
        )
        await _audit(
            db, account_id=body.account_id, actor_id=actor, action="ERROR_CORRECTION_RECORDED",
            entity_type="error_correction", entity_id=str(correction["id"]),
            payload={"payload_hash": payload_hash, "original_ledger_entry_id": body.original_ledger_entry_id,
                     "quantity_delta_micros": quantity_delta, "amount_delta_cents": body.amount_delta_cents},
        )
    return {"correction": dict(correction), "idempotent": False}


@router.post("/custody-snapshots")
async def ingest_custody_snapshot(body: CustodySnapshotIn, user: Annotated[dict, Depends(require_auth)]):
    _role(user, "DATA_OPS", "BROKER_OPS")
    actor = int(user["user_id"])
    if body.as_of.tzinfo is None:
        raise HTTPException(422, "as_of must include a timezone")
    if body.as_of > datetime.now(timezone.utc) + settings.future_event_tolerance_delta:
        raise HTTPException(422, "custody snapshot timestamp is too far in the future")
    if len(body.positions) > 500:
        raise HTTPException(422, "custody snapshot exceeds 500 positions")
    normalized_positions: dict[str, int] = {}
    for symbol, quantity in body.positions.items():
        upper = symbol.upper()
        if not upper or len(upper) > 15 or not upper[0].isalpha() or any(
            not (character.isalnum() or character in ".-") for character in upper
        ):
            raise HTTPException(422, f"invalid custody symbol: {symbol}")
        micros = _signed_micros(quantity)
        if micros < 0:
            raise HTTPException(422, "custody positions cannot be short")
        if upper in normalized_positions:
            raise HTTPException(422, "custody symbols must be unique after normalization")
        normalized_positions[upper] = micros
    async with SessionLocal() as db, db.begin():
        await _custody_access(db, body.account_id, user)
        source, evidence_url = await _source(db, body.source, "CUSTODY", body.evidence_url)
        normalized = {
            "account_id": body.account_id, "source": body.source,
            "source_system_id": source["id"], "external_event_id": body.external_event_id,
            "as_of": body.as_of.isoformat(), "cash_cents": body.cash_cents,
            "daily_realized_loss_cents": body.daily_realized_loss_cents,
            "positions": normalized_positions, "evidence_url": evidence_url,
        }
        payload_hash = _hash(normalized)
        existing = (
            await db.execute(
                text("""
                    SELECT * FROM governed_custody_snapshots
                    WHERE account_id=:account AND source_system_id=:source AND external_event_id=:event
                """),
                {"account": body.account_id, "source": source["id"], "event": body.external_event_id},
            )
        ).mappings().first()
        if existing:
            if existing["payload_hash"] != payload_hash:
                raise HTTPException(409, "custody replay conflicts with recorded provider evidence")
            return {"snapshot": dict(existing), "idempotent": True}
        snapshot = (
            await db.execute(
                text("""
                    INSERT INTO governed_custody_snapshots
                      (account_id,source_system_id,external_event_id,payload_hash,as_of,cash_cents,
                       daily_realized_loss_cents,positions,evidence_url,recorded_by)
                    VALUES (:account_id,:source_system_id,:external_event_id,:payload_hash,:as_of,:cash_cents,
                       :daily_realized_loss_cents,CAST(:positions AS jsonb),:evidence_url,:actor)
                    RETURNING *
                """),
                normalized | {
                    "as_of": body.as_of, "positions": _canonical(normalized_positions),
                    "payload_hash": payload_hash, "actor": actor,
                },
            )
        ).mappings().one()
        await db.execute(
            text("""
                INSERT INTO governed_risk_profiles(account_id,daily_loss_cents,last_custody_as_of)
                VALUES (:account,:loss,:as_of)
                ON CONFLICT (account_id) DO UPDATE SET daily_loss_cents=:loss,
                  last_custody_as_of=:as_of,updated_at=NOW()
                WHERE governed_risk_profiles.last_custody_as_of IS NULL
                   OR governed_risk_profiles.last_custody_as_of<:as_of
            """),
            {"account": body.account_id, "loss": body.daily_realized_loss_cents, "as_of": body.as_of},
        )
        await _audit(
            db, account_id=body.account_id, actor_id=actor, action="CUSTODY_SNAPSHOT_INGESTED",
            entity_type="custody_snapshot", entity_id=str(snapshot["id"]),
            payload={"payload_hash": payload_hash, "as_of": body.as_of.isoformat()},
        )
    return {"snapshot": dict(snapshot), "idempotent": False}


@router.post("/backtests")
async def run_backtest(body: BacktestIn, user: Annotated[dict, Depends(require_auth)]):
    _role(user, "INVESTOR", "REVIEWER", "ADMIN")
    if body.as_of.tzinfo is None:
        raise HTTPException(422, "as_of must include a timezone")
    case_ids = [case.case_id for case in body.cases]
    if len(case_ids) != len(set(case_ids)):
        raise HTTPException(422, "backtest case identifiers must be unique")
    account_id = int(user["user_id"])
    normalized = body.model_dump(mode="json")
    request_hash = _hash(normalized)
    results: list[dict] = []
    for case in body.cases:
        actual = evaluate_risk(
            side=case.side, quantity_micros=_micros(case.quantity),
            price_cents=case.price_cents, available_micros=_micros(case.available_quantity),
            position_micros=_signed_micros(case.position),
            observed_at=body.as_of - timedelta(seconds=case.observed_age_seconds), now=body.as_of,
            max_order_notional_cents=case.max_order_notional_cents,
            max_position_notional_cents=case.max_position_notional_cents,
            max_daily_loss_cents=case.max_daily_loss_cents,
            daily_loss_cents=case.daily_loss_cents,
            max_liquidity_participation_bps=case.max_liquidity_participation_bps,
            kill_switch=case.kill_switch, available_cash_cents=case.available_cash_cents,
        )
        expected = list(case.expected_blockers)
        results.append({"case_id": case.case_id, "expected": expected, "actual": actual, "passed": actual == expected})
    passed = all(result["passed"] for result in results)
    async with SessionLocal() as db, db.begin():
        existing = (
            await db.execute(
                text("SELECT * FROM governed_backtest_runs WHERE account_id=:account AND scenario_id=:scenario"),
                {"account": account_id, "scenario": body.scenario_id},
            )
        ).mappings().first()
        if existing:
            if existing["request_hash"] != request_hash:
                raise HTTPException(409, "scenario_id was already used for different deterministic inputs")
            return {"run": dict(existing), "idempotent": True}
        run = (
            await db.execute(
                text("""
                    INSERT INTO governed_backtest_runs
                      (account_id,scenario_id,request_hash,as_of,cases,results,passed,created_by)
                    VALUES (:account,:scenario,:request_hash,:as_of,CAST(:cases AS jsonb),
                      CAST(:results AS jsonb),:passed,:actor) RETURNING *
                """),
                {"account": account_id, "scenario": body.scenario_id, "request_hash": request_hash,
                 "as_of": body.as_of, "cases": _canonical(normalized["cases"]),
                 "results": _canonical(results), "passed": passed, "actor": account_id},
            )
        ).mappings().one()
        await _audit(
            db, account_id=account_id, actor_id=account_id, action="BACKTEST_RECORDED",
            entity_type="backtest", entity_id=str(run["id"]),
            payload={"scenario_id": body.scenario_id, "request_hash": request_hash,
                     "passed": passed, "case_count": len(results)},
        )
    return {"run": dict(run), "idempotent": False}


@router.post("/risk/kill-switch")
async def set_kill_switch(body: KillSwitchIn, user: Annotated[dict, Depends(require_auth)]):
    _role(user, "ADMIN")
    actor = int(user["user_id"])
    async with SessionLocal() as db, db.begin():
        await db.execute(
            text("""INSERT INTO governed_risk_profiles(account_id,kill_switch,kill_switch_reason)
              VALUES (:account,:active,:reason) ON CONFLICT (account_id) DO UPDATE
              SET kill_switch=:active,kill_switch_reason=:reason,updated_at=NOW()"""),
            {"account": body.account_id, "active": body.active, "reason": body.reason},
        )
        await _audit(db, account_id=body.account_id, actor_id=actor, action="KILL_SWITCH_CHANGED", entity_type="risk_profile", entity_id=str(body.account_id), payload={"active": body.active, "reason": body.reason})
    return {"account_id": body.account_id, "active": body.active}


@router.post("/reconcile")
async def reconcile(account_id: int | None = None, user: Annotated[dict, Depends(require_auth)] = None):
    actor = int(user["user_id"])
    target = account_id or actor
    if target != actor:
        _role(user, "REVIEWER", "ADMIN")
    async with SessionLocal() as db, db.begin():
        discrepancies: list[dict] = [
            dict(row) | {"kind": "order_fill_total"}
            for row in (
            await db.execute(
                text("""
                    SELECT o.id,o.filled_quantity_micros,COALESCE(SUM(f.quantity_micros),0)::bigint AS recorded_fills
                    FROM governed_paper_orders o LEFT JOIN governed_paper_fills f ON f.order_id=o.id
                    WHERE o.account_id=:account GROUP BY o.id HAVING o.filled_quantity_micros<>COALESCE(SUM(f.quantity_micros),0)
                """), {"account": target},
            )
            ).mappings().all()
        ]
        position_rows = (
            await db.execute(
                text("""
                    WITH symbols AS (
                      SELECT symbol FROM governed_positions WHERE account_id=:account
                      UNION
                      SELECT metadata->>'symbol' AS symbol FROM governed_ledger_entries
                        WHERE account_id=:account AND metadata ? 'symbol'
                    ), ledger AS (
                      SELECT metadata->>'symbol' AS symbol,COALESCE(SUM(quantity_micros),0)::bigint AS quantity
                      FROM governed_ledger_entries WHERE account_id=:account AND metadata ? 'symbol'
                      GROUP BY metadata->>'symbol'
                    )
                    SELECT symbols.symbol,COALESCE(p.quantity_micros,0)::bigint AS recorded_quantity,
                      COALESCE(ledger.quantity,0)::bigint AS ledger_quantity
                    FROM symbols LEFT JOIN governed_positions p
                      ON p.account_id=:account AND p.symbol=symbols.symbol
                    LEFT JOIN ledger ON ledger.symbol=symbols.symbol
                    WHERE COALESCE(p.quantity_micros,0)<>COALESCE(ledger.quantity,0)
                """),
                {"account": target},
            )
        ).mappings().all()
        discrepancies.extend(dict(row) | {"kind": "position_ledger"} for row in position_rows)
        cash = (
            await db.execute(
                text("""
                    SELECT r.opening_cash_cents,r.cash_balance_cents,
                      (r.opening_cash_cents+COALESCE(SUM(l.amount_cents),0))::bigint AS ledger_cash_cents,
                      r.daily_loss_cents
                    FROM governed_risk_profiles r LEFT JOIN governed_ledger_entries l ON l.account_id=r.account_id
                    WHERE r.account_id=:account
                    GROUP BY r.account_id
                """),
                {"account": target},
            )
        ).mappings().first()
        if not cash:
            discrepancies.append({"kind": "missing_risk_profile"})
        elif int(cash["cash_balance_cents"]) != int(cash["ledger_cash_cents"]):
            discrepancies.append({
                "kind": "cash_ledger", "recorded_cash_cents": int(cash["cash_balance_cents"]),
                "ledger_cash_cents": int(cash["ledger_cash_cents"]),
            })
        snapshot = (
            await db.execute(
                text("""
                    SELECT * FROM governed_custody_snapshots WHERE account_id=:account
                    ORDER BY as_of DESC,id DESC LIMIT 1
                """),
                {"account": target},
            )
        ).mappings().first()
        if not snapshot:
            discrepancies.append({"kind": "missing_custody_snapshot"})
        else:
            internal_positions = {
                row["symbol"]: int(row["quantity_micros"])
                for row in (
                    await db.execute(
                        text("SELECT symbol,quantity_micros FROM governed_positions WHERE account_id=:account"),
                        {"account": target},
                    )
                ).mappings().all()
                if int(row["quantity_micros"]) != 0
            }
            custody_positions = {key: int(value) for key, value in dict(snapshot["positions"]).items() if int(value) != 0}
            if internal_positions != custody_positions:
                discrepancies.append({
                    "kind": "custody_positions", "recorded": internal_positions,
                    "custody": custody_positions, "snapshot_id": int(snapshot["id"]),
                })
            if cash and int(cash["cash_balance_cents"]) != int(snapshot["cash_cents"]):
                discrepancies.append({
                    "kind": "custody_cash", "recorded_cash_cents": int(cash["cash_balance_cents"]),
                    "custody_cash_cents": int(snapshot["cash_cents"]), "snapshot_id": int(snapshot["id"]),
                })
            if cash and int(cash["daily_loss_cents"]) != int(snapshot["daily_realized_loss_cents"]):
                discrepancies.append({
                    "kind": "custody_daily_loss", "recorded_loss_cents": int(cash["daily_loss_cents"]),
                    "custody_loss_cents": int(snapshot["daily_realized_loss_cents"]),
                    "snapshot_id": int(snapshot["id"]),
                })
        run_id = (
            await db.execute(
                text("INSERT INTO governed_reconciliation_runs(account_id,run_by,status,discrepancies) VALUES (:account,:actor,:status,CAST(:data AS jsonb)) RETURNING id"),
                {"account": target, "actor": actor, "status": "PASS" if not discrepancies else "FAIL", "data": _canonical([dict(row) for row in discrepancies])},
            )
        ).scalar_one()
        await _audit(db, account_id=target, actor_id=actor, action="RECONCILIATION_COMPLETED", entity_type="reconciliation", entity_id=str(run_id), payload={"discrepancy_count": len(discrepancies)})
    return {"id": run_id, "status": "PASS" if not discrepancies else "FAIL", "discrepancies": [dict(row) for row in discrepancies]}


@router.get("/audit/export")
async def audit_export(account_id: int | None = None, user: Annotated[dict, Depends(require_auth)] = None):
    actor = int(user["user_id"])
    target = account_id or actor
    if target != actor:
        _role(user, "REVIEWER", "ADMIN")
    async with SessionLocal() as db:
        rows = (
            await db.execute(text("SELECT * FROM governed_audit_events WHERE account_id=:account ORDER BY id"), {"account": target})
        ).mappings().all()
        ledger = (
            await db.execute(text("SELECT * FROM governed_ledger_entries WHERE account_id=:account ORDER BY id"), {"account": target})
        ).mappings().all()
        orders = (
            await db.execute(text("SELECT * FROM governed_paper_orders WHERE account_id=:account ORDER BY id"), {"account": target})
        ).mappings().all()
        positions = (
            await db.execute(text("SELECT * FROM governed_positions WHERE account_id=:account ORDER BY symbol"), {"account": target})
        ).mappings().all()
        custody = (
            await db.execute(text("SELECT * FROM governed_custody_snapshots WHERE account_id=:account ORDER BY as_of,id"), {"account": target})
        ).mappings().all()
        reconciliations = (
            await db.execute(text("SELECT * FROM governed_reconciliation_runs WHERE account_id=:account ORDER BY id"), {"account": target})
        ).mappings().all()
        backtests = (
            await db.execute(text("SELECT * FROM governed_backtest_runs WHERE account_id=:account ORDER BY id"), {"account": target})
        ).mappings().all()
    event_dicts = [dict(row) for row in rows]
    return {
        "account_id": target, "generated_at": datetime.now(timezone.utc),
        "audit_chain_valid": verify_audit_chain(event_dicts), "events": event_dicts,
        "ledger": [dict(row) for row in ledger], "orders": [dict(row) for row in orders],
        "positions": [dict(row) for row in positions], "custody_snapshots": [dict(row) for row in custody],
        "reconciliations": [dict(row) for row in reconciliations],
        "backtests": [dict(row) for row in backtests],
    }


@router.get("/state")
async def governed_state(account_id: int | None = None, user: Annotated[dict, Depends(require_auth)] = None):
    actor = int(user["user_id"])
    target = account_id or actor
    if target != actor:
        _role(user, "REVIEWER", "ADMIN")
    async with SessionLocal() as db:
        risk = (
            await db.execute(text("SELECT * FROM governed_risk_profiles WHERE account_id=:account"), {"account": target})
        ).mappings().first()
        positions = (
            await db.execute(text("SELECT * FROM governed_positions WHERE account_id=:account ORDER BY symbol"), {"account": target})
        ).mappings().all()
        orders = (
            await db.execute(text("SELECT * FROM governed_paper_orders WHERE account_id=:account ORDER BY id DESC LIMIT 100"), {"account": target})
        ).mappings().all()
        reconciliation = (
            await db.execute(text("SELECT * FROM governed_reconciliation_runs WHERE account_id=:account ORDER BY id DESC LIMIT 1"), {"account": target})
        ).mappings().first()
    return {"account_id": target, "risk": dict(risk) if risk else None,
            "positions": [dict(row) for row in positions], "orders": [dict(row) for row in orders],
            "latest_reconciliation": dict(reconciliation) if reconciliation else None}
