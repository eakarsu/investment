# Completeness Review: investment

**Review date:** 2026-07-18

## Assessment basis

Static inspection of project-owned source and configuration only; no dependency installation, build, database migration, external-service call, or runtime launch was performed. The scan considered 61 project files (48 source files), 2 manifest(s), 2 test-like file(s), and 0 CI workflow(s), excluding dependency/generated directories.

## Classification

**Functional but incomplete**

This is a substantive but unfinished finance/trading application, not just an empty scaffold. Inspection found 48 source files across `backend/`, `web/`, `tests/` using Next.js, React, Rails, Python; however, the checked-in workflow and delivery controls do not yet demonstrate a complete, production-operable product.

## Why it is not complete

- Mock, demo, sample, fixture, or placeholder behavior remains in executable/product paths.
- Only 2 test-like file(s) were found, too little evidence for the breadth of the implemented workflow.
- No checked-in CI workflow proves builds, tests, migrations, and security checks on every change.
- No clear deployment/container configuration demonstrates a reproducible production topology.

## Needed features

1. Integrate licensed market/bank/broker data with idempotent ingestion, reconciliation, and explicit source timestamps.
2. Add deterministic exposure, liquidity, loss, approval, and kill-switch limits outside any LLM decision path.
3. Implement ledger-grade transaction history, corporate-action/error correction, custody boundaries, and audit exports.
4. Backtest and paper-trade realistic failure, stale-data, duplicate-order, and partial-fill scenarios before live use.
5. Add risk-based unit, integration, and end-to-end tests in CI, including migration and failure-path coverage.

## Risks or launch blockers

- Automation contains destructive process, filesystem, or database operations; do not run it on a shared machine without review.
- Startup appears coupled to seed/migration behavior, risking data mutation or non-repeatable launches.
- AI-provider availability, cost, privacy, prompt injection, and unvalidated output are launch risks until bounded and evaluated.
- No CI evidence prevents broken or insecure changes from reaching a release.

## Evidence inspected

- `codex-custom-viz-and-ops.html:15`
- `backend/brokerage.py:3`
- `backend/auth.py`
- `backend/main.py`
- `tests/__init__.py`
- `pyproject.toml`

## Recommended next action

Choose one real finance/trading journey, define acceptance criteria and external contracts, then close its persistence, permission, integration, failure, and test gaps before expanding features.

## Implementation progress (2026-07-20)

Implemented one bounded, paper-only trading journey. Registered source contracts now distinguish market data, paper broker, custody/bank, and corporate-action evidence and bind each source to an exact HTTPS host, license reference, expiry, stable external event identity, canonical payload hash, and one-way audited disable. Market events, paper fills, and custody snapshots are typed, timestamped, idempotent, and reject altered replays. Real provider licensing, transport authentication, and evidence-signature verification remain production acceptance work rather than being simulated in source.

Orders now use integer cents and millionths of a share with deterministic stale/future-data, order notional, reserved/current exposure, liquidity participation, available cash, daily realized loss, long-only, limit-price, and kill-switch blockers outside any LLM path. Controls are rerun before independent approval and at fill time. Order authors cannot approve their own work; investor, reviewer, data-operations, broker-operations, and administrator duties are separate; broker/data operators require revocable account-specific custody grants. Production mounts only the governed/authentication API, and the generated AI/research/mock/live-broker routes and UI were removed from the executable product surface.

Paper fills atomically update signed cash and position ledger evidence, positions, order versions, and partial/full status. Append-only provider evidence, custody snapshots, ledger entries, corporate actions, compensating error corrections, reconciliation runs, deterministic backtests, and hash-chained audit events are protected by PostgreSQL triggers. Corporate actions preserve exact fractional-unit rules; corrections must reference and oppose an original fill and cannot cumulatively exceed it. Reconciliation compares order totals, ledger-derived positions/cash, current state, and the latest authoritative custody snapshot; audit export includes its chain-verification result and the retained orders, ledger, custody, reconciliation, and backtest evidence.

Added two checksum-verified migrations, deliberate user provisioning, non-mutating startup, a retired destructive seeder, safe runtime/CORS/JWT/source-host checks, a focused paper-trading console, pinned Python and patched Next.js dependencies, unprivileged read-only API/web containers, PostgreSQL Compose topology with a separate migration job, CI, environment documentation, provider/security/operations guidance, and current-tree/full-history secret scanning.

Verification used a fresh disposable PostgreSQL 17 cluster: both migrations applied and a second pass reported both already applied; all 8 tests passed, including source/custody replay conflicts, provider rejection, role and grant boundaries/revocation, reserved risk and kill-switch checks, independent approval, duplicate and partial fills, signed cash/position ledger updates, custody reconciliation, corporate action, bounded correction, deterministic stale/loss backtests, audit-chain verification, and database append-only rejection. Python compilation, the Next.js 15.5.20 production build, Compose rendering, shell syntax, and diff checks passed. Python and production npm dependency audits reported no known vulnerabilities, and Gitleaks passed for both Git history and the current tree. A live migrated API smoke returned readiness/health `200`, a retired legacy route `404`, unauthenticated governed state `401`, and disallowed-origin preflight `400`. Local image construction could not run because the configured Docker/Colima daemon is stopped; CI retains both image-build gates.

Launch still requires organization-selected licensed market, broker, custody/bank, and corporate-action providers; verified provider signatures and raw-evidence retention; legal/compliance/custody approval; calibrated account limits and opening cash; representative backtest and paper-trading datasets; load/concurrency, outage, clock-skew, backup/restore/failover, surveillance, and incident exercises; accessibility/security review; and named operational owners. No source change in this implementation authorizes real-money execution.

## Isolated startup and login verification (2026-07-20)

`start.sh` now requires distinct assigned API/web ports, refuses conflicts, binds to `127.0.0.1`, removes hardcoded Next.js ports, and maps only the disposable validator database, JWT, and CORS values in test mode. Its API, web, combined, and migration modes remain explicit. The existing administrative user command is now acknowledgement-gated, consumes the validator identity fields, and refuses to replace an existing username or email. The validator recognizes the repository's locked root virtual environment and runs its explicit migration/provision modules only against the disposable database.

Against PostgreSQL `55665`, the API started on `6138` and passed on its first acceptance attempt: the provisioned persisted administrator completed password login, received the constrained bearer token, and passed authenticated `/api/auth/me`, yielding `API_VERIFIED/startup_login_session_api`. Python compilation, all 8/8 deterministic/database/API tests, and the Next.js 15.5.20 production build passed; the optional web mode is pinned to `6139`. The assigned triple was free after cleanup.
