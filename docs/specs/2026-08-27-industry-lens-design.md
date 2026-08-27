# Industry lens for sector-aware valuation

**Date:** 2026-08-27
**Status:** Draft (awaiting user review)
**Author:** Mavis
**Project:** valz screener v0.6.0 (post-peer-comparison)

## Context

The v0.5.0 peer-comparison feature flagged AMRT's false positive (z=-1.88 against outlier μ=14.21, peer median ~4.18). But that was a *peer-relative* check inside one lens (P/E). The deeper problem remains: **P/E that looks "cheap" for a bank means something different than P/E that looks "cheap" for a coal company**. Banks trade at 2-3× P/B and 15-25× P/E as their *normal* range; coal companies might trade at 4-5× P/E but 5-8× EV/EBITDA as normalcy. A single-multiple screener conflates these and produces noisy cross-sector comparisons.

The user (finance professional) explicitly approved the following at the v0.6.0 brainstorm:
1. **Per-sector primary lens** — bank uses P/B, commodity uses EV/EBITDA, consumer uses P/E, etc.
2. **Multi-metric transparency** — every sector exposes supporting metrics (ROE, DER) alongside the primary, so the user can see *why* a verdict fires, not just the verdict.
3. **Verdict rule (heuristic, not composite score)** — simple `if primary_z <= -1.0 AND roe >= 0.15` rules. Explainable.
4. **BVPS (Book Value Per Share)** — top-level field in `/api/ticker/{code}` for quick reference (P/B sanity check).

DCF is **deferred to v0.6.1 / v0.7**. v0.6.0 ships industry-specific multiples first as the foundation.

## Goal

Add a sector-aware valuation layer to the v0.5.0 screener. Three surfaces:

1. **`/api/ticker/{code}` and `/api/screen` rows** gain `industry_lens` (object) when the ticker's sector is in the new `industry_lenses` config block. Shape:
   - `sector`: string (e.g., `"bank"`)
   - `label`: string (e.g., `"bank_value"`)
   - `primary`: string (the dominant metric, e.g., `"pbv"`)
   - `available_metrics`: dict of `{value, z, pctile}` per metric (omit when no data)
   - `verdict`: string (`"undervalued_quality"`, `"cheap_but_deteriorating"`, `"expensive"`, `"fair"`, or `null`)

2. **`/api/ticker/{code}` (only)** gains top-level `book_value_per_share: float | null` — computed from `fundamentals.equity / shares_history.listed_shares`. Always present when computable.

3. **Screener** gains two new query params: `?lens=<label_substring>` (filter by lens label) and `?verdict=<verdict_string>` (filter by exact verdict). Both compose with existing filters via AND.

## Non-Goals (out of scope for v0.6.0)

- DCF / explicit fair-value bands (deferred to v0.6.1 / v0.7)
- Dividend yield, NIM, FCF metrics (not in `fundamentals` schema; would require new columns)
- Per-ticker `industry_lenses` override (config-driven only)
- Time-series of industry_lens verdict (current snapshot only, like MOS)
- Industry lens line in the chart drawer (peer's amber line is enough)
- Sector heatmap, per-sector ranking board (v0.6.1+)
- Custom verdict rules via UI (config-only; runtime overrides in v0.7+)

## Design

### Configuration

A new top-level `industry_lenses` map in `config.example.yaml` (append-only, preserves all existing keys):

```yaml
industry_lenses:
  bank:
    label: bank_value
    primary: pbv
    supporting:
      roe_min: 0.15
      der_max: 5.0
    verdict_rules:
      undervalued_quality:
        - primary_z: <= -1.0
        - roe:      >= 0.15
        - der:      <= 5.0
      cheap_but_deteriorating:
        - primary_z: <= -1.0
        - roe:      <  0.15
      expensive:
        - primary_z: >= 1.5
      fair: default

  commodity:
    label: commodity_value
    primary: ev_ebitda
    supporting:
      roe_min: 0.10
    verdict_rules:
      undervalued_quality:
        - primary_z: <= -1.0
        - roe:      >= 0.10
      expensive:
        - primary_z: >= 1.5
      fair: default

  consumer:
    label: consumer_value
    primary: per
    supporting:
      roe_min: 0.15
      pbv_max: 5.0
    verdict_rules:
      undervalued_quality:
        - primary_z: <= -1.5
        - roe:      >= 0.15
      expensive:
        - primary_z: >= 1.5
      fair: default

  property:
    label: property_value
    primary: pbv
    supporting:
      der_max: 2.0
    verdict_rules:
      undervalued_quality:
        - primary_z: <= -1.0
        - der:      <= 2.0
      expensive:
        - primary_z: >= 1.5
      fair: default

  general:
    label: general_value
    primary: per
    supporting:
      roe_min: 0.10
    verdict_rules:
      undervalued_quality:
        - primary_z: <= -1.5
        - roe:      >= 0.10
      expensive:
        - primary_z: >= 1.5
      fair: default
```

**Threshold semantics:**
- Supporting keys are `metric_min` or `metric_max` (name encodes direction).
- Verdict rule conditions reference the same metric name: `roe: >= 0.15` checks the current ROE value (not z-score).
- Rules within a verdict are AND-joined.
- Rule priority: `undervalued_quality` > `cheap_but_deteriorating` > `expensive` > `fair` (first match wins).
- `fair: default` is the catch-all when no other rule fires.
- **Skipped silently when a metric is missing** (no crash, just doesn't fire).

**Coverage:** 5 sectors (bank, commodity, consumer, property, general). The `financial` sector in existing `cfg["groups"]` falls through to `general` lens unless a separate `financial:` block is added.

**Why not auto-detect the lens from existing `cfg["groups"]`?** Self-contained. `industry_lenses` is the source of truth for v0.6.0; the existing `groups` config stays for screener sort logic. Slight duplication (bank→pbv in both) is acceptable for clarity.

### Module: `lens.py`

A new pure-function module with one I/O function. Late imports of `compute` and `db` keep unit tests import-clean (mirror `peer.py`).

```python
# Pure functions (no I/O)

def lens_for(cfg, code) -> str | None:
    """Return the sector name for a code, or None if not in sector_map."""

def lens_cfg_for(cfg, sector) -> dict | None:
    """Return cfg['industry_lenses'][sector] or None if no lens defined."""

def list_lens_labels(cfg) -> list[str]:
    """Return all lens labels across sectors (for ?lens= filter validation)."""

def list_supported_sectors(cfg) -> list[str]:
    """Return all sectors that have a non-None industry_lenses entry."""

def evaluate_verdict(lens_cfg, primary_z, supporting_values) -> str:
    """Pure: walk verdict_rules in priority order, return first matching
    rule's name, or 'fair' if none match. supporting_values is
    {metric_name: current_value}. Skips a rule if a required metric is
    missing (no crash). Returns 'fair' if no rule fires.
    """

# I/O function (one only, mirrors peer_stats_for)

def lens_metrics_for(cfg, db_path, code) -> dict | None:
    """Compute industry_lens for one ticker. Returns None if:
    - code not in sector_map
    - sector not in industry_lenses
    - no primary metric data (z or value is null)

    Otherwise returns the assembled industry_lens dict.

    SQL queries (all read-only):
    - primary current value: SELECT <col> FROM multiples WHERE code=? ORDER BY date DESC LIMIT 1
    - primary z-score: SELECT mu, sigma FROM stats WHERE code=? AND window='w5y' LIMIT 1
    - supporting ROE: SELECT net_income, equity FROM fundamentals
                      WHERE code=? ORDER BY year DESC, periode DESC LIMIT 1
    - supporting DER: same query (uses total_debt, equity)

    All SQL parameterized. No user input in column names; only fixed
    column-name strings from cfg.
    """
```

### BVPS computation (inline in app.py)

Not in `lens.py` — BVPS is a basic fundamental, not a sector-relative lens. Inlined in the `/api/ticker/{code}` endpoint:

```python
# 3-line SQL pattern, mirrors existing inline queries in the endpoint
row_f = con.execute(
    "SELECT equity FROM fundamentals WHERE code=? "
    "ORDER BY year DESC, periode DESC LIMIT 1", (code,)).fetchone()
row_s = con.execute(
    "SELECT listed_shares FROM shares_history WHERE code=? "
    "ORDER BY date DESC LIMIT 1", (code,)).fetchone()
if row_f and row_s and row_f["equity"] and row_s["listed_shares"]:
    equity = float(row_f["equity"])
    shares = float(row_s["listed_shares"])
    bvps = (equity * 1_000_000) / shares if shares > 0 else None
    # multiply equity by 1M because fundamentals.equity is in millions
else:
    bvps = None
```

**Unit normalization:** `fundamentals.equity` unit is **TBD pending verification** during Task 1 implementation — check `backfill.py` and existing v0.4.0 valuation code to confirm whether equity is stored in millions of IDR, full IDR, or another unit. The spec assumes millions-of-IDR (consistent with how `equity` flows into the v0.4.0 valuation math as a raw ratio). The Task 1 implementer MUST verify this and adjust the multiplier accordingly. If the unit is different, update this section in a spec revision before Task 1 lands.

`shares_history.listed_shares` is in absolute share count (no scaling needed).

**Edge cases:**
- equity ≤ 0 → `bvps = null` (don't show negative book value per share as if it's meaningful)
- shares = 0 or null → `bvps = null`
- Either row missing → `bvps = null`
- No crash, no warning, just `null`.

### API contract

#### `GET /api/ticker/{code}`

Additive response:
```json
{
    "code": "BBCA",
    "ok": true,
    "as_of": "2026-08-26",
    "book_value_per_share": 2000.0,    // NEW (always present when computable)
    "...": "...",
    "peer": { ... },
    "industry_lens": {                // NEW (null when no lens)
        "sector": "bank",
        "label": "bank_value",
        "primary": "pbv",
        "available_metrics": {
            "pbv":  {"value": 4.20, "z": -1.20, "pctile": 0.12},
            "per":  {"value": 23.5, "z":  0.40, "pctile": 0.65},
            "roe":  {"value": 0.215, "z": null, "pctile": null},  // current value only
            "der":  {"value": 5.10, "z": null, "pctile": null}
        },
        "verdict": "undervalued_quality"
    }
}
```

- `industry_lens` is `null` for tickers not in any `industry_lenses` block.
- `book_value_per_share` is `null` when not computable.
- Supporting metrics (ROE, DER) carry `value` only (no z/pctile) — these are threshold-based, not z-score.
- Primary metrics carry `value, z, pctile` — z from `stats` table, pctile derived.

#### `GET /api/screen`

Each row gains `industry_lens` (same shape). `book_value_per_share` is **NOT** included in screener rows (drawer detail only, would add noise to list view).

#### `?lens=<label_substring>`

Filter screen results to only tickers whose `industry_lens.label` contains the substring (case-insensitive). Examples:
- `?lens=bank` → matches `bank_value`
- `?lens=cons` → matches `consumer_value`
- `?lens=bank_value` → exact match (specific)
- `?lens=invalid_label` → empty rows (no error)

#### `?verdict=<verdict_string>`

Filter to tickers with matching exact verdict. Examples:
- `?verdict=undervalued_quality`
- `?verdict=expensive`
- Compound: `?lens=bank&verdict=undervalued_quality`

#### Edge cases

- **Sector not in any `industry_lenses` block** → `industry_lens: null` (don't 404).
- **Primary metric missing data** → `industry_lens: null` for the whole ticker.
- **Supporting metrics missing** → omitted from `available_metrics`; verdict rules evaluate against present metrics only.
- **Config has sector entry but missing `verdict_rules`** → use built-in `fair: default` only.
- **BVPS missing data** → `book_value_per_share: null`, no crash.

### UI changes

#### Screener row badge

Small pill to the left of the ticker, color-coded by verdict:
- `undervalued_quality` → green (`#10b981`)
- `cheap_but_deteriorating` → amber (`#f59e0b`)
- `fair` → neutral gray (`#6b7280`)
- `expensive` → red (`#ef4444`)
- `null` (no lens) → no badge, no padding

Pill text: short sector name (max 6 chars: `bank`, `cons`, `prop`, `comm`, `gen`). Hover tooltip: full `label` + verdict explanation.

#### Ticker drawer — new "Industry Lens" section

Position: after existing "Peer Comparison" section, before "MOS Valuation". Three sections now flow: **Valuation Z-Score → Peer Comparison → Industry Lens → MOS Valuation**.

Components:
- Header: `label` (e.g., `bank_value`) + sector name
- Verdict line: large text, color-coded (same scheme as badge). Hover tooltip: explanation (e.g., "primary z=-1.20 + ROE 21.5% + DER 5.1")
- Primary metric row: name, current value, z-score, pctile
- Supporting metrics list: one row per supporting metric, with current value, threshold, and check/cross mark (✓ if passes, ✗ if fails or near-boundary)
- No data: "No industry lens data for this ticker" in muted text

#### Ticker drawer header — BVPS

Inline next to price: `Price: 8,400 (Aug 26) | BVPS: 2,000`. When BVPS is null, hide the BVPS portion (don't show "BVPS: null"). Always visible regardless of `industry_lens` (BVPS is independent).

#### Filter dropdown

In screener controls, two new dropdowns:
- `Lens: [All ▼]` — populated from `cfg["industry_lenses"]` sector list, value drives `?lens=bank` etc.
- `Verdict: [Any ▼]` — `Any / undervalued_quality / cheap_but_deteriorating / expensive / fair`

Both trigger screener reload via `onchange`. No JS framework; same pattern as existing controls.

### Backward compatibility

- All new fields are additive. `industry_lens` may be `null` for tickers not in any lens. `book_value_per_share` may be `null` when not computable.
- Existing 142 tests must still pass without modification. The `TICKER_KEYS` and `ROW_KEYS` exact-match contract tests are updated to include the new field names — this is a test-contract update, not a code change.
- Deployments without `industry_lenses` config block: `industry_lens: null` for all tickers, no errors. Same opt-in pattern as v0.5.0 `peer_groups`.
- `book_value_per_share` is always computed (no opt-in needed) — it's a derived value from existing tables.

### Testing

**1. Pure-function tests** in `tests/test_lens.py` (8 tests):
- `lens_for` returns sector or None
- `lens_cfg_for` returns config block or None
- `list_lens_labels` returns all labels across sectors
- `list_supported_sectors` returns sectors with non-None lens
- `evaluate_verdict` matches first rule in priority order
- `evaluate_verdict` skips rule if required metric is missing
- `evaluate_verdict` returns "fair" when no rule matches
- `evaluate_verdict` correctly handles threshold vs z-score conditions

**2. Integration tests** in `tests/test_api.py` (3 tests, mirror v0.5.0 pattern):
- `test_ticker_includes_industry_lens_for_member` — ICBP in consumer → industry_lens present
- `test_ticker_industry_lens_is_null_for_non_member` — EEE in no sector → null
- `test_screen_rows_include_industry_lens_per_row` — per-row, filterable

**3. BVPS endpoint tests** in `tests/test_api.py` (2 tests):
- `test_ticker_includes_bvps_for_equity_positive` — AAA with positive equity → bvps present
- `test_ticker_bvps_is_null_for_zero_equity` — code with zero equity → bvps null

**4. Screener filter tests** in `tests/test_api.py` (2 tests, optional but recommended):
- `test_screen_lens_filter_narrows_results` — `?lens=bank` returns only bank rows
- `test_screen_verdict_filter_narrows_results` — `?verdict=undervalued_quality` filters correctly

**5. Contract test updates** (mirror v0.5.0 scope-creep):
- `tests/test_api.py` — `TICKER_KEYS` +add `industry_lens, book_value_per_share`; `ROW_KEYS` +add `industry_lens`
- `tests/test_syaria.py` — same `TICKER_KEYS` update (defensible contract-preserving scope-creep, expected)

**6. Static test** in `tests/test_static.py` (1 test):
- `index.html` references the `industry_lens` UI marker string

**Test count target:** 142 → 158 (8 pure + 3 integration + 2 BVPS + 2 filter + 1 static = 16 new tests).

### Migration / deployment

- **No DB migration.** All fields are derived from existing tables.
- **No schema change to existing endpoints.** All new fields are additive.
- `config.example.yaml` ships with a starter set of 5 sectors. `config.yaml` deployed copies need to be updated manually (or copy the block from `config.example.yaml`). Without the new block, `industry_lens: null` for all tickers — no errors.
- **Homeserver deploy**: two-stage (pre-bump + post-bump), standard pull + rebuild image + recreate container. Mirror v0.5.0 Task 4.
- **Desktop bundle**: rebuild onedir + zip, mirror v0.5.0.
- **VERSION bump**: 0.5.0 → 0.6.0 in `app.py:35` and `tests/test_api.py:278` (single chore commit).

## Acceptance criteria

- [ ] `pytest` passes 142 → 158 tests, no regression.
- [ ] `GET /api/ticker/ICBP?window=w5y` includes `industry_lens: { sector: "consumer", label: "consumer_value", primary: "per", available_metrics: {per: {...}, roe: {...}, pbv: {...}}, verdict: ... }`.
- [ ] `GET /api/ticker/ICBP?window=w5y` includes `book_value_per_share: 1850.0` (or similar IDR value).
- [ ] `GET /api/ticker/BBCA?window=w5y` includes `industry_lens: { sector: "bank", primary: "pbv", verdict: "undervalued_quality" }` (assuming BBCA primary_z <= -1.0 and ROE >= 0.15).
- [ ] `GET /api/ticker/ICBP?window=w5y` (consumer) and `GET /api/ticker/AMRT?window=w5y` (retail, in peer but no industry_lens) → AMRT gets `industry_lens: null` if retail sector is not in `industry_lenses` config.
- [ ] `GET /api/screen?lens=bank` returns only tickers with `industry_lens.label` containing "bank".
- [ ] `GET /api/screen?verdict=undervalued_quality` returns only tickers with matching verdict.
- [ ] `GET /api/screen?lens=bank&verdict=undervalued_quality&max_z=-1.0` → AND'd filter, narrowed correctly.
- [ ] Ticker drawer renders BVPS in header and Industry Lens section (visible via Playwright snapshot or static test).
- [ ] Screener row shows color-coded verdict badge.
- [ ] Desktop bundle ships with v0.6.0 zip; smoke-test passes.
- [ ] Homeserver at `:8102` reports `version: "0.6.0"`.
- [ ] No v0.5.0 caller broken: existing tests + desktop bundle still load. The peer comparison AMRT `peer: { high_base_warning: true }` still works as before.

## Open questions for the user

None. Design follows the user's explicit approval of the hybrid option 3+1 lens shape, BVPS addition, and the v0.6.0 + DCF sequencing (industry lens first, DCF later).
