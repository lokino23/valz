# Valuation Z-Score Screener (valz)

**Date:** 2026-08-25
**Status:** Draft → User Review
**Owner:** Mavis
**Affected system:** NEW standalone service `valz` (homeserver, port `:8096`)
**Data sources:** idx-mcp (`:8001`, audited XBRL fundamentals + local accumulator),
Yahoo Finance `.JK` (long price history), Arjum (price fallback only)

## Context & Goals

Idea (from the user, paraphrased): model each stock's *own* trading-history
valuation — mean and standard deviation of PE / PBV / EV/EBITDA over a multi-year
window — then screen for names currently deviating far below their own mean
(e.g., normally trades at PBV 0.9, now at −2σ). Variable must fit the sector
(a bank should be screened on PBV/PER, never EV/EBITDA). Purpose: sharpen entry
decisions and stop treating "barang murah semakin murah" as automatically cheap.

This is a **display/screening tool**, same philosophy as saham-dashboard:
monitoring context, never an automatic buy call.

**Success criteria (one sentence each):**

1. A ranked web table shows LQ45+IDX80 names ordered by z-score of their
   sector-appropriate primary multiple, filterable by window/sector/threshold.
2. Clicking a row opens a chart of that multiple's daily history with mean,
   ±1σ and ±2σ bands, filing-date markers, and today's position marked.
3. Nightly refresh runs unattended (prices daily, fundamentals incrementally
   each reporting season) with no manual SSH.
4. Every response carries `source` and `as_of`; unknown freshness renders as
   "Tanggal data tidak tersedia", never as live.
5. Coverage gaps degrade gracefully (ticker flagged, service never breaks).

## Feasibility findings (probed 2026-08-25)

| Probe | Result | Consequence |
|---|---|---|
| `idx_accum_status` | daily_history covers **2024-11-01 → 2026-08-24**, 424 trading days | Accumulator alone cannot feed a 5Y window → Yahoo `.JK` is the long-window price source |
| `idx_fundamentals(BBCA, 2021, audit)` | Works; audited XBRL, sector-aware (bank), IDR-aware | Historical quarterly fundamentals ARE obtainable per ticker/period |
| `idx_fundamentals_screen(period=2021-audit)` | 0 rows | Screener table is current-snapshot only; not usable for history (per-ticker fetch instead) |

Universe seed: union of `idx_fundamentals_screen` current-period rows
(IDX80-biased liquidity universe, ~hundreds of names) with the user's
watchlist tickers. Constituent-list auto-refresh is out of scope for MVP.

## Chosen approach

Standalone **FastAPI + precomputed SQLite + vanilla JS/ECharts static
frontend** — deliberately mirrors the mature saham-dashboard stack so deploy,
ops and maintenance patterns are reused.

Rejected alternatives:

- **Streamlit** — faster to prototype but heavier runtime, weak layout control
  for the drill-down chart UX, different runtime pattern from the rest of the
  fleet.
- **Extend saham-dashboard** — user explicitly chose a separate app;
  `app.py` there is already 65KB.

## Architecture

```
Yahoo .JK daily 5-6Y ──┐
Arjum history (fallback)├─> backfill.py ─> valz.db (SQLite)
accumulator (recent)   │                     │
idx-mcp XBRL quarterly ┘                     ▼
(cached permanently,                compute.py: TTM multiples
 resumable, ~4.8k calls once)       + rolling z-scores (3Y & 5Y)
                                          │
                                          ▼
                          app.py (FastAPI, read-only DB) + static/
                          ranked table -> drill-down chart (ECharts)
```

Components (each single-purpose):

| Unit | Responsibility |
|---|---|
| `config.yaml` | Universe list, sector→variable mapping, windows (default 3Y+5Y), thresholds, filing-lag days, winsorize bounds, corporate-action overrides, endpoint URLs |
| `backfill.py` | Pull daily closes (Yahoo primary; arjum 500-bar fallback; accumulator overlay for 2024-11+); fetch `idx_fundamentals` per (code, year∈[2020..now], periode∈{tw1,tw2,tw3,audit}) with permanent caching, resume support, polite rate limit + jitter; record shares history |
| `compute.py` | Build daily TTM multiple series per ticker; rolling mean/σ per window; z-scores; eligibility + coverage flags; write `multiples`, `stats`, `coverage_issues`, `meta` |
| `app.py` | FastAPI, read-only SQLite access, serves `/api/*` + `static/index.html` |
| `static/index.html` | Filter bar + ranked table + drill-down drawer chart (ECharts), dark theme consistent with saham-dashboard |
| `refresh.sh` | Cron 19:05 WIB: incremental price update + fundamentals season-check + recompute |

## Data model (valz.db)

```
prices          (code, date, close, adj_close, source)            PK(code,date)
fundamentals    (code, year, periode, period_end, currency, sector,
                 revenue, net_income, equity, total_debt, cash,
                 ebitda, da, raw_json, fetched_at)                PK(code,year,periode)
shares_history  (code, date, listed_shares, source)               PK(code,date)
multiples       (code, date, per_ttm, pbv, ev_ebitda, ps_ttm)     PK(code,date)
stats           (code, window, mu, sigma, n_obs)                  PK(code,window)
coverage_issues (code, reason, detail, updated_at)                PK(code)
meta            (key, value)                                      PK(key)
```

`raw_json` keeps the full idx-mcp payload forever so methodology fixes never
require re-fetching XBRL. Screens are NOT snapshotted in MVP — "days in
discount zone" streak computes live from `multiples` + `stats` (YAGNI).

## Methodology

**TTM alignment.** For date `t`: use the latest filing whose *availability
date* ≤ t. Availability = filing date when known, else `period_end + 90`
days (conservative default; configurable). Flow items (revenue, net income,
EBITDA, D&A) are rolling 4-quarter sums; stock items (equity, debt, cash) are
latest balance-sheet values.

**Per-share denominators.** Shares outstanding at `t` = latest
`listed_shares ≤ t` from `shares_history` (accumulator supplies 2024-11+;
current count anchors earlier dates). Multiples use aggregate fundamentals ÷
shares-at-t — this correctly reprices historical earnings against today's
share count, and corporate-action distortions are handled via explicit
`ca_overrides` in config (rights issue / split multiplier + effective date),
never silently.

**Multiples.**

- `PER_TTM = close_t / (NI_TTM / shares_t)`
- `PBV = (close_t × shares_t) / equity_latest`
- `EV = mcap_t + total_debt_latest − cash_latest`;
  `EV_EBITDA = EV / EBITDA_TTM` (EBITDA = operating profit + D&A where the
  cash-flow statement reports D&A; else absent → P/S fallback flagged)
- Days with a non-positive denominator (negative EPS/EBITDA) are excluded
  from that multiple's statistics (count reported).

**Z-score.** For window W ∈ {3Y, 5Y}: μ and σ of the daily multiple series
over trailing W, winsorized at the 1%/99% percentiles (configurable) to tame
one-off earnings outliers. `z = (x_now − μ) / σ`.

**Sector → variable mapping (config-driven, defaults):**

| Sector group | Primary | Secondary |
|---|---|---|
| Banks / insurance / financials | PBV | PER |
| Consumer / general industry | PER | PBV |
| Commodity / cyclical (mining, energy, CPO) | EV/EBITDA | P/S |
| Property | PBV | PER |

**Eligibility for ranking:** ≥80% window coverage, IDR reporting currency
(USD names skipped with flag), PER-primary names need positive TTM NI today.

**Context columns (anti-value-trap, display-only):** ROE TTM trend,
revenue YoY, DER, days-in-discount-zone streak. These exist so the user can
see the difference between "momentarily discounted" and "perpetually cheap".
The tool ranks and displays; it never verdicts.

## API contract

- `GET /api/screen?window=5y&sector=&max_z=-1.0`
  → `{as_of, source, window, rows: [{code, sector, primary_var, value_now,
  mean, sigma, z, disc_pct, streak_days, roe_ttm, rev_yoy, der, flags[]}],
  issues: [...], counts}` — rows sorted by z ascending. `disc_pct =
  (value_now − mean) / mean × 100` (negative = trading below own mean).
  `source` enumerates `yahoo | arjum | accumulator | mixed`.
- `GET /api/ticker/{code}?window=5y`
  → `{meta: {code, sector, primary_var, secondary_var}, stats: {mu, sigma,
  n_obs}, series: [{date, v, z}], filings: [dates], source, as_of}`
  — bands computed client-side from `stats` (small payloads).
- `GET /api/meta` → `{last_compute, universe_count, coverage, versions}`

Invalid query params → HTTP 422 before any DB/compute work.

## Frontend

Single route. Filter bar (window toggle 3Y/5Y, sector select, threshold slider
−0.5…−3σ highlighting the discount zone). Ranked table with the columns from
`/api/screen`. Row click opens a right-side drawer: ECharts line of the
primary multiple, shaded band mean±1σ and mean±2σ, markPoint on the latest
value, light markAreas on filing dates, secondary-variable overlay toggle.
Dark palette consistent with saham-dashboard (`#0f172a` family). Every panel
shows `source` + `as_of`; missing freshness shows "Tanggal data tidak tersedia".

## Error handling

- Price chain per ticker: Yahoo → Arjum (`limit≤500`) → accumulator (recent).
  Failure of any link degrades coverage, recorded in `coverage_issues`.
- XBRL fetch: 2 retries with backoff+jitter, then recorded missing; compute
  proceeds with what exists.
- USD reporters: valuation metrics skipped + flagged (mirrors idx-mcp
  behaviour).
- All endpoints fail soft: `{ok:false, error}` shape, service stays up.

## Testing

- **Unit (pytest):** TTM alignment golden cases (quarter boundaries, missing
  quarter), filing-lag availability, winsorization bounds, sector mapping,
  z-score math on fixed fixtures, streak calculation, ca_overrides application.
- **Contract:** fixture `valz.db` → `/api/screen` and `/api/ticker/{code}`
  return the documented shapes exactly.
- **Smoke:** `python backfill.py --tickers BBCA,BBRI,ANTM --dry-run`
  end-to-end before any full-universe run; `compute.py --check` selftest
  (schema + invariant assertions) following the dashboard `sync.py --check`
  pattern.

## Deployment & operations

- Repo: `ssh://git@[IP_ADDRESS]:2222/gitadmin/valz.git` (private), pushed
  incrementally per change (standing user workflow rule).
- Homeserver `~/valz`, Docker Compose with `network_mode: host` (same as
  saham-dashboard — container reaches idx-mcp `[IP]:8001` and localhost
  directly), port **8096** published.
- One-time backfill ≈ 200 tickers × 6 years × 4 periodes ≈ up to ~4,800 XBRL
  calls, rate-limited with jitter (~30–60 min), fully resumable; subsequent
  seasons add ~800 calls/quarter inside the nightly job's season-check.
- Nightly cron 19:05 WIB: prices update + recompute (fast; app reads
  precomputed tables only).
- Watchdog/TG-alert integration deferred (out of scope MVP).

## Out of scope (explicit non-goals)

1. Telegram alerts (natural v2 addition reusing `alerts.py` pattern).
2. Full-IDX ~900-name expansion — architecture accepts it later purely via
   `config.yaml` universe growth.
3. Auto-refresh of LQ45/IDX80 constituents.
4. Strategy backtesting engine (this ships a screener, not a PnL study).
5. Auth layer — Tailscale/LAN exposure only, like saham-dashboard.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Share-count distortion around rights issues pre-2024 (no shares history) | `ca_overrides` config + distortion flag; multiples recomputed instantly after edit (aggregates cached in `raw_json`) |
| Yahoo `.JK` split/adjust inconsistencies vs local data | Sanity cross-check on the 2024-11+ overlap vs accumulator during backfill; mismatch > tolerance → coverage flag |
| XBRL line-item variance across years/templates | Parser tolerant, per-field presence optional, `raw_json` retained for re-extraction |
| "Cheap because broken" value traps dominate bottom ranks | Context columns (ROE trend, DER, streak) displayed next to every rank; tool never verdicts |

## Resolved decisions (with user, 2026-08-25)

Runtime = new standalone homeserver app · universe = LQ45+IDX80 liquid set ·
output = web table + drill-down chart (no alerts in MVP) · stack = FastAPI +
ECharts · methodology approved including 90-day filing lag and winsorization.
