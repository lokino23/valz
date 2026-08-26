# MOS (Margin of Safety) valuation feature

**Date:** 2026-08-26
**Status:** Approved (brainstorming → this spec)
**Author:** Mavis
**Project:** valz (screener, currently v0.3.0)

## Context

valz is a stock screener for IHSG that surfaces mean-reversion candidates: stocks trading at a multiple (P/E, P/B, EV/EBITDA, P/S) significantly below their own 5-year mean. The user (a finance professional) confirmed the tool is useful as a pre-screen but asked whether the same pipeline can answer the deeper question: **"is this stock cheap relative to its intrinsic value?"** That is the classic Graham/Buffett margin-of-safety question, and the current z-score alone does not answer it.

Today's z-score answers: *how cheap is the multiple vs its own history?* — a relative, statistical measure. It does not answer: *how cheap is the multiple vs what the earnings justify?* — an absolute, fundamental measure.

The two views are complementary, not substitutes. A stock can be 3σ below its 5y mean and still be overvalued (declining earnings distort the historical mean). The other way around is also true: a stock can trade near its 5y mean and still be cheap if its earnings have grown.

This spec adds a small, focused MOS layer on top of the existing screener so the user can run the relative screen and the absolute screen in one place.

## Goal

Add a Graham-classic intrinsic-value + MOS% computation per ticker, exposed as:
1. A dedicated detail endpoint (`GET /api/valuation/{code}`) that returns the full assumption breakdown so the user can verify the inputs and adjust mentally.
2. An optional embedded field on the existing screener rows so the per-row signal is visible without a second round-trip.

## Non-Goals (out of scope for this version)

- **DCF / explicit cash-flow discounting.** Considered, deferred. Graham classic is the floor; DCF would be the ceiling and is a separate feature.
- **5-year normalized EPS for cyclical smoothing.** Considered, deferred. The user's stocks of interest (banks, plantation, consumer staples) are not deeply cyclical at the P/E level; if a future iteration needs it, the EPS source can be swapped without breaking the contract.
- **User-editable per-ticker assumptions via query params** (e.g. `?growth=0.10&bond_yield=0.07`). The endpoints do accept these as overrides but the UI does not surface them. A future iteration can wire a slider/drawer without changing the contract.
- **USD-ticker handling.** The screener already flags `currency != IDR` in row output. The valuation endpoint assumes IDR-denominated EPS and price; for non-IDR we return `ok: false, reason: "usd_unsupported"`.
- **"5y EPS history" derived stats** (max drawdown of EPS, mean of last 5 filings, etc). The endpoint can include the raw series; no derived stats yet.

## Design

### Architecture

A new pure-function module `valuation.py` carries the computation. The HTTP layer in `app.py` calls it and serialises. There is no I/O, no network, no DB writes — the function reads from the existing `fundamentals` and `prices` tables, both of which are already populated by the backfill pipeline.

Components:
- `valuation.compute_graham(eps_ttm, growth, bond_yield)` — single-purpose pure function returning `(graham_value, caveats)`.
- `valuation.eps_ttm_from_filings(filings)` — selector that returns the trailing-twelve-month EPS estimate from the 4 most recent filings, plus a `confidence` flag based on filing count.
- `app.create_app` — extended with `GET /api/valuation/{code}?growth=auto&bond_yield=0.065` endpoint.
- `app.screen` — extended with optional `?with_valuation=true` query parameter that adds a per-row `valuation` field.
- `compute_all` / `static/index.html` — out of scope. The valuation is computed on demand so it does not need to live in the daily compute cycle. The UI gets a new drawer or a column in a future UI iteration; for v0.4 the endpoint is enough to wire to anything.

### Graham formula

```
graham_value = eps_ttm × (8.5 + 2 × g) × 4.4 / Y
```

Where:
- `eps_ttm` = trailing-twelve-month earnings per share, in IDR
- `g` = expected annual growth, decimal (0.05 = 5%)
- `Y` = current AAA corporate bond yield, decimal (0.065 = 6.5%)
- `graham_value` = intrinsic value per share, in IDR

Constants `8.5`, `2`, and `4.4` are Graham's 1962-era anchors and are deliberately conservative. They are not configurable in this version; documented as constants in `valuation.py` with a comment explaining the source.

### Inputs

#### EPS selection

`eps_ttm` is computed as follows:
1. Pull all filings for the code from the `fundamentals` table, ordered by `period_end` descending.
2. Take the most recent filing's `net_income` and divide by the latest `listed_shares` (or `tradable_shares` from `shares_history` if listed is missing).
3. **Smooth** by averaging the most recent 4 quarterly filings' EPS if available, falling back to the single most recent (annual) filing if quarterly history is sparse.
4. If the resulting `eps_ttm` is `<= 0`, the ticker is **not valueable**; endpoint returns `ok: false, reason: "negative_eps"`. Rationale: Graham's formula is undefined at zero or negative earnings, and a negative-EPS "discount" is not a discount at all.

#### Growth (g)

Default (`growth=auto`): use `rev_yoy` from the most recent filings pair (current revenue / prior-year-same-quarter revenue − 1), clamped to `[-0.05, 0.20]` to prevent wild inputs. Rationale: Graham's own formula uses `g` between 0% and 10%; we widen to 20% to allow quality compounders through, and floor at −5% to allow mild top-line declines through (because declining revenue is itself a signal worth seeing).

Override (`growth=<decimal>`): any decimal in `(-1, 1)`. Out-of-range returns 422.

The clamped auto value is the v0.4 default. The clamp is documented in the response as `growth_clamped_from` so the user can see when the cap fired.

#### Bond yield (Y)

Default `bond_yield=0.065` (6.5%, a conservative proxy for the BI reference rate and the 5y-7y corporate AAA yield in IDR). This is a config value (`config.yaml: valuation.bond_yield_default`); the endpoint mirrors the config default if the query param is omitted.

Override (`bond_yield=<decimal>`): any decimal in `(0, 0.5)`. Out-of-range returns 422.

Rationale for not fetching live: the BI rate changes weekly; the AAA corporate spread changes monthly; baking either into the screener output would mean the same ticker shows a different intrinsic value on Monday vs Friday. The user can override per request if they want a live view. The next iteration can add a cached `bond_yield_current` field on `/api/meta` once per day.

### Edge cases and their responses

| Case | Response shape |
|---|---|
| Ticker not in DB | `404 {"ok": false, "error": "unknown ticker"}` (matches existing `/api/ticker/{code}`) |
| EPS TTM ≤ 0 | `200 {"ok": false, "reason": "negative_eps", "eps_ttm": <num>, "filings_used": <n>}` |
| < 2 filings (insufficient history) | `200 {"ok": false, "reason": "insufficient_history", "filings": <n>}` |
| `currency != "IDR"` | `200 {"ok": false, "reason": "usd_unsupported", "currency": <code>}` |
| `growth` out of range | `422 {"detail": "invalid growth: <val>"}` |
| `bond_yield` out of range | `422 {"detail": "invalid bond_yield: <val>"}` |
| Window param other than "w5y" or "w3y" (if added) | `422` (matches existing pattern) |

The pattern `{"ok": true/false, ...}` is consistent with the existing ticker endpoint, so clients can reuse the same parser.

### API contracts

#### `GET /api/valuation/{code}`

Query params (all optional):
- `growth` (default `auto`): auto-derived from rev_yoy, or override as decimal.
- `bond_yield` (default = config value): override as decimal.

Success response (`200`):
```json
{
  "ok": true,
  "code": "TBLA",
  "as_of": "2026-08-26",
  "inputs": {
    "eps_ttm": 87.0,
    "eps_method": "4_filing_average",
    "filings_used": 4,
    "growth": 0.108,
    "growth_source": "rev_yoy",
    "growth_clamped_from": null,
    "bond_yield": 0.065,
    "bond_yield_source": "config_default"
  },
  "computation": {
    "graham_formula": "V = EPS × (8.5 + 2g) × 4.4 / Y",
    "intrinsic_value": 50900.0,
    "currency": "IDR"
  },
  "result": {
    "current_price": 625.0,
    "current_price_date": "2026-08-26",
    "mos_pct": 98.8,
    "mos_label": "deep_undervalued"
  },
  "caveats": [
    "Graham formula assumes stable earnings; cyclical/distressed names are unreliable.",
    "MOS% > 30 is the Graham 'actionable' threshold; > 50 is high-conviction."
  ]
}
```

`mos_pct` semantics: `(V − P) / V × 100`. **Positive = undervalued**, **negative = overvalued**. The user explicitly asked for the sign to read "discount to intrinsic" so a high MOS is the green-light direction.

`mos_label` thresholds:
- `> 50` → `"deep_undervalued"`
- `> 30` → `"actionable"`
- `> 0` → `"modest_discount"`
- `> −20` → `"fair"`
- `else` → `"overvalued"`

These thresholds match Graham's 30%/50% rule of thumb; the v0.4 cutoffs are documented in the response so the user can replicate.

#### `GET /api/screen?with_valuation=true`

Adds a per-row field when the flag is set. Off by default to keep the existing /api/screen contract and call-site performance unchanged.

When `with_valuation=true`:
```json
{
  "code": "TBLA",
  "z": -1.05,
  "...": "...",
  "valuation": {
    "intrinsic_value": 50900.0,
    "mos_pct": 98.8,
    "mos_label": "deep_undervalued"
  }
}
```

When the ticker is not valueable (negative EPS, USD, insufficient history), `valuation: null` is returned and the row stays in the screener — the MOS field is a bonus, not a hard filter.

The `with_valuation=true` path executes the same `compute_graham` per row; for 113 tickers that is 113 in-process SQL queries (one per code) plus the in-memory math. Worst case 113 × ~5ms = ~0.6s; expected ~200ms because the existing `_open()` reuses a single connection per request. Acceptable for an interactive UI. If the user later complains, we can cache the per-row valuation in a `valuation_cache` table refreshed on `compute_all`.

### Testing

A new `tests/test_valuation.py` covers:

1. **Pure formula tests** for `compute_graham`:
   - known-output hand calc (EPS=100, g=0.05, Y=0.065 → 100 × (8.5+0.1) × 4.4/0.065 = 58153.85)
   - zero growth → Graham's "no-growth stock" baseline
   - negative EPS → returns (None, "negative_eps")
   - zero bond yield → 422 (out of range)
   - growth outside (-1, 1) → 422

2. **EPS selector tests** for `eps_ttm_from_filings`:
   - 4 quarterly filings → 4-quarter average
   - 1 annual filing → single-filing EPS
   - 0 filings → returns None
   - missing `listed_shares` → falls back to `tradable_shares` from `shares_history`
   - mix of USD and IDR filings → pick the most recent IDR (or skip if all USD)

3. **Endpoint tests** (`test_api.py` extension):
   - 200 + valid shape on a known ticker (TBLA or seeded fixture)
   - `ok: false, reason: "negative_eps"` for a fixture with negative net_income
   - `ok: false, reason: "usd_unsupported"` for a USD-coded fixture
   - `?growth=2.0` → 422
   - `?bond_yield=0` → 422
   - 404 on unknown code

4. **Screener integration test**:
   - `?with_valuation=true` returns `valuation` field on rows that have positive EPS
   - `valuation: null` on rows with negative EPS
   - Default response (no flag) is unchanged from v0.3.0

The existing test fixtures in `tests/test_api.py` and `tests/test_syaria.py` are reused; one new `_seed_valuation_fixtures` helper is added so the EPS-selector unit tests don't depend on the full app context.

### Migration / deployment

- **No DB migration.** Reads only from `fundamentals` and `prices`. Both tables are already populated by the existing backfill.
- **No config change required** for the user. `valuation.bond_yield_default: 0.065` is added to `config.example.yaml` with a comment; deployed `config.yaml` is unchanged unless the user wants to override the default.
- **Backwards-compatible.** New endpoint is additive; new `?with_valuation=true` is opt-in. No existing test, no existing client, no existing call site breaks.
- **Homeserver deploy.** Standard pull + rebuild image + recreate container; same routine as v0.3.0 deployment.
- **Desktop bundle.** Build the new `valz-0.4.0-portable.zip`; the new module is a single pure-function file that PyInstaller picks up automatically (no hiddenimports change).

## Open questions for the user (none blocking)

None. The clarifying-questions loop closed on the Standard scope + Graham-classic formula. Any later enhancement (DCF, normalized EPS, BI rate fetch) is a separate spec.

## Acceptance criteria

- [ ] `pytest` passes 97 → 114 tests (13 new for `test_valuation.py` + 4 new for screener integration).
- [ ] `GET /api/valuation/TBLA` returns 200 with the full breakdown shape above.
- [ ] `GET /api/valuation/CCC?growth=auto&bond_yield=0.065` returns 200 with `ok: true` (or a documented skip reason).
- [ ] `GET /api/screen?with_valuation=true` adds `valuation` to every row where computable; null where not.
- [ ] `?growth=2.0` and `?bond_yield=0` return 422 with informative error.
- [ ] Desktop bundle (`dist/valz-0.4.0-portable.zip`) ships the new module without size regression > 500KB.
- [ ] Homeserver at `:8102` returns the same responses as the local app.
- [ ] No test or call site from v0.3.0 is broken (existing 97 tests still green).
