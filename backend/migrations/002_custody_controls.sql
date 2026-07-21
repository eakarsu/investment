-- Strengthen the bounded paper-trading workflow with licensed source records,
-- custody boundaries, cash/position reconciliation, compensating corrections,
-- and immutable deterministic backtest evidence.

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'app_user_role'
  ) THEN
    ALTER TABLE app_users ADD CONSTRAINT app_user_role
      CHECK (role IN ('INVESTOR','REVIEWER','DATA_OPS','BROKER_OPS','ADMIN'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'app_user_token_version'
  ) THEN
    ALTER TABLE app_users ADD CONSTRAINT app_user_token_version
      CHECK (token_version > 0);
  END IF;
END $$;

CREATE TABLE governed_sources (
  id BIGSERIAL PRIMARY KEY,
  source_key VARCHAR(64) NOT NULL UNIQUE,
  source_type VARCHAR(24) NOT NULL
    CHECK (source_type IN ('MARKET_DATA','PAPER_BROKER','CUSTODY','CORPORATE_ACTION')),
  evidence_host VARCHAR(255) NOT NULL,
  license_reference VARCHAR(255) NOT NULL,
  valid_until TIMESTAMPTZ NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  config_hash CHAR(64) NOT NULL,
  created_by BIGINT NOT NULL REFERENCES app_users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  disabled_at TIMESTAMPTZ,
  disabled_by BIGINT REFERENCES app_users(id),
  disable_reason TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0)
);

ALTER TABLE governed_market_events
  ADD COLUMN source_system_id BIGINT REFERENCES governed_sources(id);
ALTER TABLE governed_paper_fills
  ADD COLUMN source_system_id BIGINT REFERENCES governed_sources(id);
ALTER TABLE governed_paper_fills
  DROP CONSTRAINT IF EXISTS governed_paper_fills_provider_check;
ALTER TABLE governed_paper_fills
  ADD CONSTRAINT governed_paper_fills_provider_nonempty CHECK (length(trim(provider)) >= 2);
ALTER TABLE governed_corporate_actions
  ADD COLUMN source_system_id BIGINT REFERENCES governed_sources(id);

ALTER TABLE governed_risk_profiles
  ADD COLUMN opening_cash_cents BIGINT NOT NULL DEFAULT 10000000
    CHECK (opening_cash_cents >= 0),
  ADD COLUMN cash_balance_cents BIGINT NOT NULL DEFAULT 10000000
    CHECK (cash_balance_cents >= 0),
  ADD COLUMN last_custody_as_of TIMESTAMPTZ;

CREATE TABLE governed_custody_grants (
  account_id BIGINT NOT NULL REFERENCES app_users(id),
  operator_id BIGINT NOT NULL REFERENCES app_users(id),
  active BOOLEAN NOT NULL DEFAULT TRUE,
  reason TEXT NOT NULL,
  granted_by BIGINT NOT NULL REFERENCES app_users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  PRIMARY KEY (account_id, operator_id),
  CHECK (account_id <> operator_id)
);

CREATE TABLE governed_custody_snapshots (
  id BIGSERIAL PRIMARY KEY,
  account_id BIGINT NOT NULL REFERENCES app_users(id),
  source_system_id BIGINT NOT NULL REFERENCES governed_sources(id),
  external_event_id VARCHAR(128) NOT NULL,
  payload_hash CHAR(64) NOT NULL,
  as_of TIMESTAMPTZ NOT NULL,
  cash_cents BIGINT NOT NULL CHECK (cash_cents >= 0),
  daily_realized_loss_cents BIGINT NOT NULL CHECK (daily_realized_loss_cents >= 0),
  positions JSONB NOT NULL CHECK (jsonb_typeof(positions) = 'object'),
  evidence_url TEXT NOT NULL,
  recorded_by BIGINT NOT NULL REFERENCES app_users(id),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (account_id, source_system_id, external_event_id)
);

CREATE TABLE governed_error_corrections (
  id BIGSERIAL PRIMARY KEY,
  account_id BIGINT NOT NULL REFERENCES app_users(id),
  source_system_id BIGINT NOT NULL REFERENCES governed_sources(id),
  external_event_id VARCHAR(128) NOT NULL,
  payload_hash CHAR(64) NOT NULL,
  original_ledger_entry_id BIGINT NOT NULL REFERENCES governed_ledger_entries(id),
  symbol VARCHAR(16) NOT NULL,
  quantity_delta_micros BIGINT NOT NULL,
  amount_delta_cents BIGINT NOT NULL,
  reason TEXT NOT NULL,
  evidence_url TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  recorded_by BIGINT NOT NULL REFERENCES app_users(id),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (quantity_delta_micros <> 0 OR amount_delta_cents <> 0),
  UNIQUE (account_id, source_system_id, external_event_id)
);

CREATE TABLE governed_backtest_runs (
  id BIGSERIAL PRIMARY KEY,
  account_id BIGINT NOT NULL REFERENCES app_users(id),
  scenario_id VARCHAR(128) NOT NULL,
  request_hash CHAR(64) NOT NULL,
  as_of TIMESTAMPTZ NOT NULL,
  cases JSONB NOT NULL CHECK (jsonb_typeof(cases) = 'array'),
  results JSONB NOT NULL CHECK (jsonb_typeof(results) = 'array'),
  passed BOOLEAN NOT NULL,
  created_by BIGINT NOT NULL REFERENCES app_users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (account_id, scenario_id)
);

CREATE INDEX governed_market_events_symbol_observed_idx
  ON governed_market_events(symbol, observed_at DESC);
CREATE INDEX governed_orders_account_status_idx
  ON governed_paper_orders(account_id, status);
CREATE INDEX governed_ledger_account_created_idx
  ON governed_ledger_entries(account_id, created_at, id);
CREATE INDEX governed_custody_account_asof_idx
  ON governed_custody_snapshots(account_id, as_of DESC, id DESC);

CREATE OR REPLACE FUNCTION guard_governed_source_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.source_key <> OLD.source_key OR NEW.source_type <> OLD.source_type
     OR NEW.evidence_host <> OLD.evidence_host OR NEW.license_reference <> OLD.license_reference
     OR NEW.valid_until <> OLD.valid_until OR NEW.config_hash <> OLD.config_hash
     OR NEW.created_by <> OLD.created_by OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'source contract fields are immutable; register a new source key';
  END IF;
  IF NEW.version <> OLD.version + 1 OR OLD.active = FALSE THEN
    RAISE EXCEPTION 'source version must advance once and disabled sources cannot be re-enabled';
  END IF;
  IF NEW.active OR NEW.disabled_at IS NULL OR NEW.disabled_by IS NULL
     OR length(trim(COALESCE(NEW.disable_reason, ''))) < 20 THEN
    RAISE EXCEPTION 'source updates may only perform an evidenced disable';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER governed_source_update_guard BEFORE UPDATE ON governed_sources
FOR EACH ROW EXECUTE FUNCTION guard_governed_source_update();

CREATE OR REPLACE FUNCTION guard_custody_grant_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.account_id <> OLD.account_id OR NEW.operator_id <> OLD.operator_id
     OR NEW.created_at <> OLD.created_at OR NEW.version <> OLD.version + 1 THEN
    RAISE EXCEPTION 'custody grant identity is immutable and version must advance';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER governed_custody_grant_update_guard BEFORE UPDATE ON governed_custody_grants
FOR EACH ROW EXECUTE FUNCTION guard_custody_grant_update();

CREATE TRIGGER governed_custody_snapshots_immutable
BEFORE UPDATE OR DELETE ON governed_custody_snapshots
FOR EACH ROW EXECUTE FUNCTION reject_governed_evidence_mutation();
CREATE TRIGGER governed_error_corrections_immutable
BEFORE UPDATE OR DELETE ON governed_error_corrections
FOR EACH ROW EXECUTE FUNCTION reject_governed_evidence_mutation();
CREATE TRIGGER governed_backtest_runs_immutable
BEFORE UPDATE OR DELETE ON governed_backtest_runs
FOR EACH ROW EXECUTE FUNCTION reject_governed_evidence_mutation();
