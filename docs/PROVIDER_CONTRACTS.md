# Provider evidence contracts

Every source has a stable key, one type (`MARKET_DATA`, `PAPER_BROKER`, `CUSTODY`, or `CORPORATE_ACTION`), an exact HTTPS evidence host, a license/contract reference, and an expiry. Disabling is one-way and audited. A reused source/event identifier is accepted only when its canonical payload hash is identical.

Market evidence carries integer cents, available quantity, observation time, and an evidence URL. Paper fills carry order identity, integer cents, quantity, occurrence time, and broker evidence. Custody snapshots carry account identity, cash, positions, realized daily loss, and an `as_of` timestamp. Corporate actions and compensating corrections retain their original evidence and never rewrite prior ledger rows.

The application validates typed shape, host, contract state, timestamps, custody grants, and replay identity. Production ingress must additionally authenticate provider transport, verify signatures and evidence contents, archive licensed evidence under approved retention, monitor outages, and document provider retry/SLA semantics. Those external controls cannot be established by source code alone.
