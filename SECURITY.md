# Security and financial-control boundary

This source code is not a broker, custodian, investment adviser, or compliance program. Production is paper-only until licensed providers, broker/custody agreements, legal/compliance approval, surveillance, reconciliations, incident response, and controlled promotion are independently accepted.

JWTs use fixed HS256 issuer/audience, 30-minute maximum expiry, and a per-user token version. Every request reloads active user role and token version from PostgreSQL. Production public registration is disabled and separation of duties prevents an order author from reviewing the same order.

Evidence requires a registered, active, unexpired typed source contract and an HTTPS URL whose host matches both that contract and the runtime allowlist. Conflicting replays fail. Orders use integer cents and millionths of a share; stale/future data, notional, position, reserved exposure, daily loss, liquidity, cash, sell quantity, limit price, and kill switch are deterministic blockers. Fill-time controls are rerun. A broker/data operator also needs an active custody grant for the investor account.

Market events, fills, custody snapshots, ledger entries, corporate actions, corrections, reconciliations, backtests, and hash-chained audit events are append-only through PostgreSQL triggers. Order terms are immutable; status/fill/version changes are monotonic. Orders and audit evidence default to seven-year retention, and ordinary writes cannot shorten retention or release legal hold.

On suspected compromise: activate the account kill switch, deactivate affected users and increment token versions, rotate signing/database/source credentials, revoke custody grants, preserve evidence, verify audit chains, reconcile every account, and append referenced corrections rather than rewriting records. Complete legal/compliance notification decisions before resuming paper operations.
