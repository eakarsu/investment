# Governed investment paper trading

The supported journey is licensed source registration → market-event ingestion → deterministic exposure/liquidity/loss/cash/kill-switch checks → immutable paper-order request → independent review → custody-scoped partial/full paper fills → signed cash/position ledger updates → custody reconciliation, corporate actions, and compensating corrections. No production route sends a live broker order or uses an LLM for a trading decision.

Production mounts only authentication and `/api/governed/*`; historical research algorithms, AI narration, mock valuation, strategy marketplace, and live-broker prototype routes are not mounted. Public registration is disabled in production. Provision named users explicitly with `backend.scripts.provision_user` and separate `INVESTOR`, `REVIEWER`, `DATA_OPS`, `BROKER_OPS`, and `ADMIN` duties.

## Deploy

Apply checksum-verified migrations as a separate release step. Startup never installs dependencies, creates or drops a database, migrates, seeds, or kills an existing listener.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
npm --prefix web ci
DB_URL=postgresql+asyncpg://... ./start.sh --migrate
npm --prefix web run build
./start.sh --api
```

Container deployment uses PostgreSQL 17 plus separate migration, unprivileged API, and unprivileged web services:

```bash
docker compose -f compose.yml config
docker compose -f compose.yml up --build
```

## Verify

```bash
.venv/bin/python -m compileall -q backend tests
RUN_DATABASE_TESTS=1 .venv/bin/pytest -q
npm --prefix web run build
npm --prefix web audit --omit=dev --audit-level=low
```

Database integration tests require an explicitly disposable PostgreSQL database. The repository enforces provider identity, evidence host, contract expiry, timestamps, idempotency, and conflicting-replay rejection; it cannot prove a commercial license or evidence signature by itself. See [SECURITY.md](SECURITY.md), [operations](docs/OPERATIONS.md), and [provider contracts](docs/PROVIDER_CONTRACTS.md) before production use.
