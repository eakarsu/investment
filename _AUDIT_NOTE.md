# Audit Note — investment

## Bucket: DETECTOR_FALSE_POSITIVE

## True state (verified 2026-05-06)
- Source files (.js .ts .tsx .jsx .py): **43**.
- LLM-reference scan: **7 files** match `openrouter|openai|anthropic|claude|chat/completions`.
- Stack: Python (FastAPI backend in `backend/`) + Next.js front (`web/`).

## Files with LLM references (already wired)
- `backend/openrouter.py` — dedicated provider client.
- `backend/ai_helpers.py` — rate limiter (20/hr), 3-strategy JSON parser,
  `log_ai_result` audit-log helper.
- `backend/main.py` — FastAPI app; mounts `/api/auth`, `/api/portfolio`, 5
  theme routers (hbm/networking/energy/inference/photonics), and exposes
  `/api/run/...`, `/api/narrate/...`, `/api/overview`, `/api/ai-results`,
  `/api/backtest/hbm`, `/api/portfolio/whatif`, `/api/leaderboard/...`,
  `/api/reproducibility/...`.
- `backend/config.py` (settings).
- `backend/seed.py`.
- `web/components/algo-section.tsx`, `web/app/hbm/page.tsx`.

## Original audit reference
Per `_AUDIT/reports/batch_10.md` §19: "Stack: Python. Backend: No. Routes: 0. AI: 0 endpoints. … Python stub with no API. Verdict: SKELETON." That measurement is stale — the FastAPI backend is real, with auth, 5 theme routers, portfolio module, audit-log persistence, and a per-user AI rate limiter.

## Audit recommendations applied this batch

The previous audit note flagged that `.env.example` claimed
`OPENROUTER_MODEL=anthropic/claude-3.5-sonnet` but the code default in
`backend/config.py` is `anthropic/claude-3-5-sonnet-20241022`. Operators
copying `.env.example` would silently use a slightly different model id
(possibly resolving to a different OpenRouter SKU). This is a clean
MECHANICAL fix.

### MECHANICAL items implemented

1. **Align `.env.example` with the canonical model id** — `.env.example`
   now uses `anthropic/claude-3-5-sonnet-20241022` (matches
   `backend/config.py:21`). Also added documentation comments for
   `AI_RATE_LIMIT_PER_HOUR`, `CORS_ORIGINS`, and `NEXTAUTH_URL` so the
   OpenRouter `HTTP-Referer` resolution path is obvious to operators.
   No runtime code changes.

## Backlog (deferred, prioritised)

1. **Per-route audit-log assertions** — `log_ai_result` is best-effort and
   already used by every theme router; add a smoke-test that asserts a
   row is written for each `/api/run/*` and `/api/narrate/*` call.
2. **Rate-limit headers** — middleware enforces 20/min for AI but does
   not currently emit `X-RateLimit-*` headers; add for client UX.
3. **Worker-side LLM calls** — `backend/workers/` contains background
   tasks; verify they too log to `ai_results` (not yet audited).
4. **Aggregated AI cost endpoint** — `ai_results` rows have model + duration
   but no token counts; extend `openrouter.chat()` to return `usage` and
   persist `prompt_tokens`/`completion_tokens` to the AiResult row.
5. **Portfolio whatif extensions** — `/api/portfolio/whatif` exists but
   could grow scenario library + reproducibility hashes.

## Files touched this batch

- `.env.example` — corrected `OPENROUTER_MODEL` default and added optional-env documentation.

## Apply pass 3 (frontend)

LEFT-AS-IS. `web/lib/api.ts` is a single typed client wrapping every FastAPI endpoint (`auth.{login,register,me}`, `dashboard.overview`, `algorithms.{run,narrate,source}`, `backtestHbm`, `backtest`, `portfolioWhatIf`, `leaderboard`, `priceLastRefresh`, `reproducibility`, `aiResults`). Bearer JWT stored in `localStorage` under `investment.jwt`; `authHeader()` injects `Authorization: Bearer …` on every fetch. Pages: `web/app/{hbm,networking,energy,inference,photonics,ai-suite,login}/page.tsx`. No FE changes needed.

## Apply pass 4 (mechanical backlog)

Implemented two MECHANICAL items from the backlog (rate-limit headers, AI usage summary). Skipped backlog items 3 (worker-side LLM verification — auditing-only, no code change required) and 4 (token-count persistence — TOO-RISKY without DB migration); item 5 (portfolio whatif scenario library) is NEEDS-PRODUCT-DECISION.

- BE: `backend/main.py`
  - middleware now emits `X-RateLimit-{Limit,Remaining,Reset,Bucket}` on every `/api/*` response (and the 429 path) so the FE/clients can surface freshness without timing out.
  - new `GET /api/ai-results/summary` aggregates `ai_results` by feature + model, returning total_calls / total_errors / error_rate_pct / avg_duration_ms and per-feature breakdown. JWT-required (`require_auth`). Read-only over existing rows — no schema change.
- FE: `web/lib/api.ts` exposes `aiResultsSummary()`; `web/app/ai-suite/page.tsx` adds an `AiUsageSummaryPanel` that auto-loads on mount, shows headline KPIs + per-feature table, and surfaces 503 messages verbatim.
- Syntax-checked: BE via `python3 -m ast` (PASS); FE balance-checked.
- Backlog deferred: token-count persistence in `ai_results` (NEEDS-MIGRATION → TOO-RISKY); per-route audit-log smoke tests (test infra not part of mechanical apply); worker-side LLM audit verification (read-only check, deferred).
