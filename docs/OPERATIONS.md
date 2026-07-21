# Paper-trading operations

1. Put every approved market, paper-broker, custody/bank, and corporate-action hostname in `MARKET_DATA_ALLOWED_HOSTS`, then register its typed contract through `/api/governed/sources`. Verify provider authentication and evidence signatures at ingress.
2. Provision separate named identities for investing, review, data operations, broker operations, and administration. Revoke a session by disabling its user or incrementing `token_version`.
3. Grant named broker/data operators to each investor custody account. Configure account limits through an approved database/change workflow and keep the kill switch enabled until owners approve a staging exercise.
4. Run reconciliation after every fill batch and before/after corporate actions. Missing custody evidence or a cash, position, ledger, or order discrepancy produces `FAIL`; append a referenced correction rather than editing evidence.
5. Back up PostgreSQL with encryption and equivalent retention/hold controls. Rehearse restore to an isolated database, rerun migration checksums, verify trigger counts and every account audit chain, and record recovery evidence.
6. Exercise stale/future events, conflicting replays, partial fills, source expiry/outage, delayed/duplicate fill, exposure/loss/liquidity/cash blocks, grant and reviewer revocation, kill switch, split/correction, and restore before launch.

Live brokerage endpoints are not mounted. Enabling real-money execution requires a separately reviewed implementation and release; changing an environment URL is not authorization to trade.
