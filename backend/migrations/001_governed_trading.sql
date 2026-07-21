CREATE TABLE IF NOT EXISTS app_users (
  id BIGSERIAL PRIMARY KEY,
  username VARCHAR(64) NOT NULL UNIQUE,
  email VARCHAR(128) NOT NULL UNIQUE,
  hashed_password VARCHAR(256) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  role VARCHAR(24) NOT NULL DEFAULT 'INVESTOR',
  token_version INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT app_user_role CHECK (role IN ('INVESTOR','REVIEWER','DATA_OPS','BROKER_OPS','ADMIN')),
  CONSTRAINT app_user_token_version CHECK (token_version > 0)
);
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS role VARCHAR(24) NOT NULL DEFAULT 'INVESTOR';
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 1;

CREATE TABLE governed_market_events (
  id BIGSERIAL PRIMARY KEY,
  source VARCHAR(64) NOT NULL,
  external_event_id VARCHAR(128) NOT NULL,
  symbol VARCHAR(16) NOT NULL,
  price_cents BIGINT NOT NULL CHECK (price_cents > 0),
  available_quantity_micros BIGINT NOT NULL CHECK (available_quantity_micros > 0),
  observed_at TIMESTAMPTZ NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  evidence_url TEXT NOT NULL,
  payload_hash CHAR(64) NOT NULL,
  UNIQUE (source, external_event_id)
);

CREATE TABLE governed_risk_profiles (
  account_id BIGINT PRIMARY KEY REFERENCES app_users(id),
  max_order_notional_cents BIGINT NOT NULL DEFAULT 500000 CHECK (max_order_notional_cents > 0),
  max_position_notional_cents BIGINT NOT NULL DEFAULT 2500000 CHECK (max_position_notional_cents > 0),
  max_daily_loss_cents BIGINT NOT NULL DEFAULT 100000 CHECK (max_daily_loss_cents > 0),
  daily_loss_cents BIGINT NOT NULL DEFAULT 0 CHECK (daily_loss_cents >= 0),
  max_liquidity_participation_bps INTEGER NOT NULL DEFAULT 1000 CHECK (max_liquidity_participation_bps BETWEEN 1 AND 10000),
  kill_switch BOOLEAN NOT NULL DEFAULT FALSE,
  kill_switch_reason TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE governed_positions (
  id BIGSERIAL PRIMARY KEY,
  account_id BIGINT NOT NULL REFERENCES app_users(id),
  symbol VARCHAR(16) NOT NULL,
  quantity_micros BIGINT NOT NULL DEFAULT 0 CHECK (quantity_micros >= 0),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (account_id, symbol)
);

CREATE TABLE governed_paper_orders (
  id BIGSERIAL PRIMARY KEY,
  account_id BIGINT NOT NULL REFERENCES app_users(id),
  client_order_id VARCHAR(128) NOT NULL,
  request_hash CHAR(64) NOT NULL,
  market_event_id BIGINT NOT NULL REFERENCES governed_market_events(id),
  symbol VARCHAR(16) NOT NULL,
  side VARCHAR(4) NOT NULL CHECK (side IN ('BUY','SELL')),
  quantity_micros BIGINT NOT NULL CHECK (quantity_micros > 0),
  filled_quantity_micros BIGINT NOT NULL DEFAULT 0 CHECK (filled_quantity_micros >= 0 AND filled_quantity_micros <= quantity_micros),
  limit_price_cents BIGINT NOT NULL CHECK (limit_price_cents > 0),
  status VARCHAR(24) NOT NULL CHECK (status IN ('BLOCKED','PENDING_REVIEW','APPROVED','REJECTED','PARTIALLY_FILLED','FILLED','CANCELLED')),
  blockers JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(blockers)='array'),
  created_by BIGINT NOT NULL REFERENCES app_users(id),
  reviewed_by BIGINT REFERENCES app_users(id),
  review_attestation TEXT,
  review_reason TEXT,
  reviewed_at TIMESTAMPTZ,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  retain_until TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 years'),
  legal_hold BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE (account_id, client_order_id)
);

CREATE TABLE governed_paper_fills (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL REFERENCES governed_paper_orders(id),
  provider VARCHAR(32) NOT NULL CHECK (provider='paper-simulator'),
  external_event_id VARCHAR(128) NOT NULL,
  payload_hash CHAR(64) NOT NULL,
  quantity_micros BIGINT NOT NULL CHECK (quantity_micros > 0),
  price_cents BIGINT NOT NULL CHECK (price_cents > 0),
  occurred_at TIMESTAMPTZ NOT NULL,
  evidence_url TEXT NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (provider, external_event_id)
);

CREATE TABLE governed_ledger_entries (
  id BIGSERIAL PRIMARY KEY,
  account_id BIGINT NOT NULL REFERENCES app_users(id),
  order_id BIGINT REFERENCES governed_paper_orders(id),
  entry_type VARCHAR(32) NOT NULL CHECK (entry_type IN ('PAPER_FILL','CORPORATE_ACTION','ERROR_CORRECTION')),
  quantity_micros BIGINT NOT NULL,
  amount_cents BIGINT NOT NULL,
  source_event_id VARCHAR(128) NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (entry_type, source_event_id, account_id)
);

CREATE TABLE governed_corporate_actions (
  id BIGSERIAL PRIMARY KEY,
  source VARCHAR(64) NOT NULL,
  external_event_id VARCHAR(128) NOT NULL,
  payload_hash CHAR(64) NOT NULL,
  symbol VARCHAR(16) NOT NULL,
  action_type VARCHAR(32) NOT NULL CHECK (action_type IN ('SPLIT','ERROR_CORRECTION')),
  numerator INTEGER NOT NULL CHECK (numerator > 0),
  denominator INTEGER NOT NULL CHECK (denominator > 0),
  effective_at TIMESTAMPTZ NOT NULL,
  evidence_url TEXT NOT NULL,
  reason TEXT NOT NULL,
  recorded_by BIGINT NOT NULL REFERENCES app_users(id),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (source, external_event_id)
);

CREATE TABLE governed_reconciliation_runs (
  id BIGSERIAL PRIMARY KEY,
  account_id BIGINT NOT NULL REFERENCES app_users(id),
  run_by BIGINT NOT NULL REFERENCES app_users(id),
  status VARCHAR(8) NOT NULL CHECK (status IN ('PASS','FAIL')),
  discrepancies JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE governed_audit_events (
  id BIGSERIAL PRIMARY KEY,
  account_id BIGINT NOT NULL REFERENCES app_users(id),
  actor_id BIGINT NOT NULL REFERENCES app_users(id),
  action VARCHAR(64) NOT NULL,
  entity_type VARCHAR(64) NOT NULL,
  entity_id VARCHAR(128) NOT NULL,
  payload JSONB NOT NULL,
  previous_hash CHAR(64),
  event_hash CHAR(64) NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL,
  retain_until TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 years'),
  legal_hold BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE OR REPLACE FUNCTION reject_governed_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% records are append-only', TG_TABLE_NAME;
END $$;

DROP TRIGGER IF EXISTS governed_market_events_immutable ON governed_market_events;
CREATE TRIGGER governed_market_events_immutable BEFORE UPDATE OR DELETE ON governed_market_events
FOR EACH ROW EXECUTE FUNCTION reject_governed_evidence_mutation();
DROP TRIGGER IF EXISTS governed_paper_fills_immutable ON governed_paper_fills;
CREATE TRIGGER governed_paper_fills_immutable BEFORE UPDATE OR DELETE ON governed_paper_fills
FOR EACH ROW EXECUTE FUNCTION reject_governed_evidence_mutation();
DROP TRIGGER IF EXISTS governed_ledger_entries_immutable ON governed_ledger_entries;
CREATE TRIGGER governed_ledger_entries_immutable BEFORE UPDATE OR DELETE ON governed_ledger_entries
FOR EACH ROW EXECUTE FUNCTION reject_governed_evidence_mutation();
DROP TRIGGER IF EXISTS governed_corporate_actions_immutable ON governed_corporate_actions;
CREATE TRIGGER governed_corporate_actions_immutable BEFORE UPDATE OR DELETE ON governed_corporate_actions
FOR EACH ROW EXECUTE FUNCTION reject_governed_evidence_mutation();
DROP TRIGGER IF EXISTS governed_reconciliation_runs_immutable ON governed_reconciliation_runs;
CREATE TRIGGER governed_reconciliation_runs_immutable BEFORE UPDATE OR DELETE ON governed_reconciliation_runs
FOR EACH ROW EXECUTE FUNCTION reject_governed_evidence_mutation();
DROP TRIGGER IF EXISTS governed_audit_events_immutable ON governed_audit_events;
CREATE TRIGGER governed_audit_events_immutable BEFORE UPDATE OR DELETE ON governed_audit_events
FOR EACH ROW EXECUTE FUNCTION reject_governed_evidence_mutation();

CREATE OR REPLACE FUNCTION enforce_governed_order_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.account_id<>OLD.account_id OR NEW.client_order_id<>OLD.client_order_id OR NEW.request_hash<>OLD.request_hash
     OR NEW.market_event_id<>OLD.market_event_id OR NEW.symbol<>OLD.symbol OR NEW.side<>OLD.side
     OR NEW.quantity_micros<>OLD.quantity_micros OR NEW.limit_price_cents<>OLD.limit_price_cents
     OR NEW.created_by<>OLD.created_by OR NEW.created_at<>OLD.created_at THEN
    RAISE EXCEPTION 'paper order terms are immutable';
  END IF;
  IF NEW.version<>OLD.version+1 OR NEW.filled_quantity_micros<OLD.filled_quantity_micros THEN
    RAISE EXCEPTION 'paper order version/fill must advance monotonically';
  END IF;
  IF NEW.retain_until<OLD.retain_until OR (OLD.legal_hold AND NOT NEW.legal_hold) THEN
    RAISE EXCEPTION 'retention cannot be shortened and legal hold cannot be released by ordinary update';
  END IF;
  IF NOT ((OLD.status='PENDING_REVIEW' AND NEW.status IN ('APPROVED','REJECTED','CANCELLED'))
       OR (OLD.status='APPROVED' AND NEW.status IN ('PARTIALLY_FILLED','FILLED','CANCELLED'))
       OR (OLD.status='PARTIALLY_FILLED' AND NEW.status IN ('PARTIALLY_FILLED','FILLED','CANCELLED'))) THEN
    RAISE EXCEPTION 'invalid paper order transition % -> %', OLD.status, NEW.status;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS governed_order_update_guard ON governed_paper_orders;
CREATE TRIGGER governed_order_update_guard BEFORE UPDATE ON governed_paper_orders
FOR EACH ROW EXECUTE FUNCTION enforce_governed_order_update();

CREATE OR REPLACE FUNCTION enforce_governed_order_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.legal_hold OR OLD.retain_until>NOW() THEN RAISE EXCEPTION 'paper order is retained or on legal hold'; END IF;
  RETURN OLD;
END $$;
DROP TRIGGER IF EXISTS governed_order_delete_guard ON governed_paper_orders;
CREATE TRIGGER governed_order_delete_guard BEFORE DELETE ON governed_paper_orders
FOR EACH ROW EXECUTE FUNCTION enforce_governed_order_delete();

