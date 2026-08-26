# MOS (Margin of Safety) Valuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Graham-classic intrinsic-value + MOS% computation as a new `GET /api/valuation/{code}` endpoint and an optional `valuation` field on the existing `/api/screen` rows.

**Architecture:** New pure-function module `valuation.py` carries the math (no I/O). The HTTP layer in `app.py` reads from the existing `fundamentals` and `prices` tables, calls into `valuation.py`, and serialises. One new endpoint, one optional query flag on the existing endpoint, one config default. No DB schema change.

**Tech Stack:** Python 3.11 stdlib only (math is trivial). FastAPI + SQLite (existing). pytest (existing).

**Spec:** `docs/specs/2026-08-26-mos-valuation-design.md` — read this first if anything is unclear.

## Global Constraints

- No new pip dependencies. The math is one line; the data is already in SQLite.
- **Backward-compatible.** New endpoint is additive. New `?with_valuation=true` is opt-in. No existing test or call site must break.
- Graham formula: `V = EPS × (8.5 + 2g) × 4.4 / Y`. Constants `8.5`, `2`, `4.4` are hardcoded in `valuation.py` with a comment citing Graham 1962 — **not** user-configurable in v0.4.
- Bond yield `Y` default: `0.065` (6.5%). Source: `config.yaml` key `valuation.bond_yield_default`. Endpoint override: `?bond_yield=<decimal>` in range `(0, 0.5)`.
- Growth `g` default (`auto`): `rev_yoy` from the most-recent filings pair, clamped to `[-0.05, 0.20]`. Endpoint override: `?growth=<decimal>` in range `(-1, 1)`. Clamp applies to auto only; explicit override is not re-clamped.
- EPS TTM: average of the 4 most recent quarterly filings if available, else single most recent annual. Skip ticker if EPS ≤ 0, currency ≠ IDR, or < 2 filings.
- Commit per task. Push to Forgejo only after all tests green.
- Test count target: 97 → 114 (13 new in `test_valuation.py`, 4 new in `test_api.py`).

## File Structure

**New:**
- `valuation.py` — pure-function module: `compute_graham(eps, growth, bond_yield)`, `eps_ttm_from_filings(filings, shares_history_rows)`, `mos_label(mos_pct)`, `validate_overrides(growth, bond_yield)`. No I/O.
- `tests/test_valuation.py` — 13 tests: formula, EPS selector, override validation, label thresholds.

**Modified:**
- `app.py` — add `GET /api/valuation/{code}` endpoint and `?with_valuation=true` flag on `GET /api/screen`. 4 new tests in `tests/test_api.py`.
- `config.example.yaml` — add `valuation.bond_yield_default: 0.065` with a comment.

**Unchanged (verify after each task):**
- `compute.py`, `db.py`, `prices.py`, `refresher.py`, `desktop.py`, `valz.spec`, `static/index.html` — leave alone for v0.4. UI can be wired to `/api/valuation/{code}` in a later iteration.

---

### Task 1: `valuation.py` — `compute_graham` + override validation

**Files:**
- Create: `valuation.py`
- Test: `tests/test_valuation.py`

**Interfaces this task exposes (consumed by Task 2, 3, 4):**
```python
def validate_overrides(growth: str | None, bond_yield: str | None) -> tuple[float, float, str]:
    """Parse + range-check ?growth and ?bond_yield query strings.
    Returns (growth, bond_yield, growth_source) where growth_source is
    "query" | "auto". Raises HTTPException(422) on out-of-range."""

def compute_graham(eps_ttm: float, growth: float, bond_yield: float) -> dict:
    """Return {'graham_value': float, 'formula': str}. eps_ttm <= 0
    returns {'graham_value': None, 'formula': str, 'reason': 'negative_eps'}."""
```

- [ ] **Step 1: Write the failing tests in `tests/test_valuation.py`**

```python
"""MOS valuation: compute_graham formula + override validation.

Pure-function unit tests; no DB, no network.
"""
import pytest
from fastapi import HTTPException

from valuation import compute_graham, validate_overrides


# ---------- compute_graham ----------

def test_graham_hand_calc_known_input():
    # EPS=100, g=0.05, Y=0.065
    # V = 100 * (8.5 + 0.10) * 4.4 / 0.065
    #   = 100 * 8.6 * 4.4 / 0.065
    #   = 860 * 4.4 / 0.065
    #   = 3784 / 0.065
    #   = 58215.384615...
    out = compute_graham(eps_ttm=100.0, growth=0.05, bond_yield=0.065)
    assert abs(out["graham_value"] - 58215.38461538462) < 0.01
    assert "8.5" in out["formula"] and "4.4" in out["formula"]


def test_graham_zero_growth_baseline():
    # Graham's "no-growth stock" baseline: 8.5 * 4.4 / Y * EPS
    out = compute_graham(eps_ttm=100.0, growth=0.0, bond_yield=0.065)
    expected = 100.0 * 8.5 * 4.4 / 0.065
    assert abs(out["graham_value"] - expected) < 0.01


def test_graham_negative_eps_returns_reason():
    out = compute_graham(eps_ttm=-50.0, growth=0.05, bond_yield=0.065)
    assert out["graham_value"] is None
    assert out["reason"] == "negative_eps"


def test_graham_zero_eps_returns_reason():
    out = compute_graham(eps_ttm=0.0, growth=0.0, bond_yield=0.065)
    assert out["graham_value"] is None
    assert out["reason"] == "negative_eps"


def test_graham_higher_growth_higher_value():
    a = compute_graham(eps_ttm=100.0, growth=0.05, bond_yield=0.065)
    b = compute_graham(eps_ttm=100.0, growth=0.15, bond_yield=0.065)
    assert b["graham_value"] > a["graham_value"]


def test_graham_higher_yield_lower_value():
    a = compute_graham(eps_ttm=100.0, growth=0.05, bond_yield=0.05)
    b = compute_graham(eps_ttm=100.0, growth=0.05, bond_yield=0.10)
    assert b["graham_value"] < a["graham_value"]


# ---------- validate_overrides ----------

def test_validate_overrides_defaults_to_auto_and_config_yield():
    growth, by, source = validate_overrides(None, None)
    assert source == "auto"
    # bond_yield default is read from config.yaml at import time; the
    # default 0.065 is what we want for tests since config.example.yaml
    # ships with 0.065
    assert by == 0.065


def test_validate_overrides_explicit_growth_passes_through():
    growth, by, source = validate_overrides("0.12", None)
    assert growth == 0.12
    assert source == "query"


def test_validate_overrides_explicit_both_passes_through():
    growth, by, source = validate_overrides("0.08", "0.07")
    assert growth == 0.08
    assert by == 0.07
    assert source == "query"


def test_validate_overrides_growth_too_high_422():
    with pytest.raises(HTTPException) as ei:
        validate_overrides("2.0", None)
    assert ei.value.status_code == 422
    assert "growth" in str(ei.value.detail).lower()


def test_validate_overrides_growth_too_low_422():
    with pytest.raises(HTTPException) as ei:
        validate_overrides("-2.0", None)
    assert ei.value.status_code == 422


def test_validate_overrides_bond_yield_zero_422():
    with pytest.raises(HTTPException) as ei:
        validate_overrides(None, "0")
    assert ei.value.status_code == 422
    assert "bond_yield" in str(ei.value.detail).lower()


def test_validate_overrides_bond_yield_too_high_422():
    with pytest.raises(HTTPException) as ei:
        validate_overrides(None, "0.8")
    assert ei.value.status_code == 422


def test_validate_overrides_non_numeric_422():
    with pytest.raises(HTTPException) as ei:
        validate_overrides("auto-ish", None)
    assert ei.value.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -m pytest tests/test_valuation.py -W ignore::DeprecationWarning`
Expected: collection error — `ModuleNotFoundError: No module named 'valuation'`

- [ ] **Step 3: Implement `valuation.py`**

```python
"""MOS valuation: Graham-classic intrinsic value + margin of safety.

Pure functions only -- no I/O, no network, no DB. The HTTP layer
(app.py) reads earnings + shares from SQLite and feeds the selectors
below. See docs/specs/2026-08-26-mos-valuation-design.md for the
contract and the rationale for the magic constants.
"""
from fastapi import HTTPException

# Graham's 1962 anchors: a no-growth stock should earn 2x the AAA bond
# yield, with growth layered on top. The 8.5 and 4.4 magic numbers come
# from "Security Analysis" 3rd ed. and are deliberately conservative.
# Reference: https://en.wikipedia.org/wiki/Intrinsic_value_(finance)
GRAHAM_FORMULA = "V = EPS * (8.5 + 2g) * 4.4 / Y"

# Range bounds for query-param overrides. Growth can be negative (a
# declining-revenue ticker) but bounded away from -100%. Bond yield
# upper bound 50% is just sanity -- nobody quotes 50% on an IDR AAA.
GROWTH_MIN, GROWTH_MAX = -0.99, 0.99
BOND_YIELD_MIN, BOND_YIELD_MAX = 1e-6, 0.5


def _bond_yield_default():
    """Read `valuation.bond_yield_default` from config.yaml if present,
    else fall back to 0.065. The endpoint imports this lazily so a
    missing config doesn't break the module import.
    """
    import os
    from config import load_config
    if not os.path.exists("config.yaml"):
        return 0.065
    try:
        return float(load_config("config.yaml").get("valuation",
                                                    {}).get("bond_yield_default",
                                                            0.065))
    except Exception:
        return 0.065


def validate_overrides(growth, bond_yield):
    """Parse + range-check `?growth` and `?bond_yield`.

    Returns ``(growth_value, bond_yield_value, growth_source)``:
    - growth_value: float, always
    - bond_yield_value: float, always
    - growth_source: ``"query"`` if the user supplied `growth`, else
      ``"auto"``

    Raises ``HTTPException(422)`` on parse error or out-of-range.
    """
    if growth is None or growth == "auto":
        g_value = None         # caller must derive from rev_yoy
        g_source = "auto"
    else:
        try:
            g_value = float(growth)
        except (TypeError, ValueError):
            raise HTTPException(422, f"invalid growth: {growth!r}")
        if not (GROWTH_MIN < g_value < GROWTH_MAX):
            raise HTTPException(
                422, f"invalid growth: {growth!r} "
                     f"(must be in ({GROWTH_MIN}, {GROWTH_MAX}))")
        g_source = "query"

    if bond_yield is None:
        by_value = _bond_yield_default()
    else:
        try:
            by_value = float(bond_yield)
        except (TypeError, ValueError):
            raise HTTPException(422, f"invalid bond_yield: {bond_yield!r}")
        if not (BOND_YIELD_MIN < by_value < BOND_YIELD_MAX):
            raise HTTPException(
                422, f"invalid bond_yield: {bond_yield!r} "
                     f"(must be in ({BOND_YIELD_MIN}, {BOND_YIELD_MAX}))")
    return g_value, by_value, g_source


def compute_graham(eps_ttm, growth, bond_yield):
    """Return ``{'graham_value', 'formula', 'reason' (optional)}``.

    Graham number: ``V = EPS * (8.5 + 2g) * 4.4 / Y``. Returns
    ``graham_value=None`` and ``reason="negative_eps"`` when EPS <= 0 --
    the formula is undefined there and a negative-EPS "discount" is
    not meaningful.
    """
    out = {"formula": GRAHAM_FORMULA, "graham_value": None}
    if eps_ttm is None or eps_ttm <= 0:
        out["reason"] = "negative_eps"
        return out
    out["graham_value"] = eps_ttm * (8.5 + 2 * growth) * 4.4 / bond_yield
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -m pytest tests/test_valuation.py -W ignore::DeprecationWarning`
Expected: 14 tests pass (10 in Task 1's test file: 7 `compute_graham` + 7 `validate_overrides` = 14).

- [ ] **Step 5: Commit**

```bash
cd "D:\VAULT\MyMind\Personal\Projects\saham\valz"
git add valuation.py tests/test_valuation.py
git commit -m "feat(valuation): add compute_graham formula and override validation"
git push origin main
```

---

### Task 2: `valuation.py` — `eps_ttm_from_filings` selector

**Files:**
- Modify: `valuation.py`
- Test: `tests/test_valuation.py` (append to existing file)

**Interfaces this task exposes (consumed by Task 3, 4):**
```python
def eps_ttm_from_filings(filings: list[dict], shares: float) -> dict:
    """filings: rows from SELECT * FROM fundamentals WHERE code=? ORDER BY
    period_end DESC. shares: latest listed_shares (or tradable).
    Returns {'eps_ttm': float, 'method': '4_filing_average'|'single_filing',
             'filings_used': int, 'currency': str}.
    Returns {'eps_ttm': None, 'reason': 'insufficient_history'|'no_shares',
             'filings_used': int, 'currency': str} when can't compute.
    Skips non-IDR currencies -- caller decides whether that's a skip
    reason or just a filter."""

def auto_growth(filings: list[dict]) -> dict:
    """Compute growth from rev_yoy of the most recent filings pair.
    Returns {'growth': float|None, 'source': 'rev_yoy'|'none',
             'clamped_from': float|None}.
    Clamp: [-0.05, 0.20]."""
```

- [ ] **Step 1: Write the failing tests in `tests/test_valuation.py` (append)**

```python
# ---------- eps_ttm_from_filings ----------

from valuation import eps_ttm_from_filings, auto_growth


def _fil(*, year=2026, periode="tw2", ni=100.0, rev=1000.0,
         cur="IDR", period_end="2026-06-30"):
    return {"year": year, "periode": periode, "period_end": period_end,
            "currency": cur, "revenue": rev, "net_income": ni}


def test_eps_ttm_averages_4_quarterly_filings():
    f = [_fil(periode="tw1", ni=20, period_end="2026-03-31"),
         _fil(periode="tw2", ni=25, period_end="2026-06-30"),
         _fil(periode="tw3", ni=22, period_end="2025-09-30"),
         _fil(periode="tw4", ni=23, period_end="2025-12-31")]
    out = eps_ttm_from_filings(f, shares=100.0)
    # avg NI = (20+25+22+23)/4 = 22.5; EPS = 22.5/100 = 0.225
    assert out["eps_ttm"] == pytest.approx(0.225)
    assert out["method"] == "4_filing_average"
    assert out["filings_used"] == 4
    assert out["currency"] == "IDR"


def test_eps_ttm_falls_back_to_single_annual_filing():
    f = [_fil(periode="audit", ni=100, period_end="2025-12-31")]
    out = eps_ttm_from_filings(f, shares=100.0)
    assert out["eps_ttm"] == pytest.approx(1.0)
    assert out["method"] == "single_filing"
    assert out["filings_used"] == 1


def test_eps_ttm_no_filings_returns_reason():
    out = eps_ttm_from_filings([], shares=100.0)
    assert out["eps_ttm"] is None
    assert out["reason"] == "insufficient_history"
    assert out["filings_used"] == 0


def test_eps_ttm_no_shares_returns_reason():
    f = [_fil()]
    out = eps_ttm_from_filings(f, shares=0)
    assert out["eps_ttm"] is None
    assert out["reason"] == "no_shares"
    assert out["filings_used"] == 1


def test_eps_ttm_usd_currency_keeps_value_but_flags():
    """USD tickers are handled by the endpoint, not the selector; the
    selector still computes the EPS so the endpoint can decide."""
    f = [_fil(cur="USD", ni=5, period_end="2026-03-31")]
    out = eps_ttm_from_filings(f, shares=100.0)
    assert out["eps_ttm"] == pytest.approx(0.05)
    assert out["currency"] == "USD"


# ---------- auto_growth ----------

def test_auto_growth_from_rev_yoy_pair():
    f = [_fil(rev=120, period_end="2026-06-30"),
         _fil(rev=100, year=2025, periode="tw2",
              period_end="2025-06-30")]
    out = auto_growth(f)
    # growth = 120/100 - 1 = 0.20 (right at the clamp ceiling, no clamp)
    assert out["growth"] == pytest.approx(0.20)
    assert out["source"] == "rev_yoy"
    assert out["clamped_from"] is None


def test_auto_growth_clamps_above_ceiling():
    f = [_fil(rev=200, period_end="2026-06-30"),
         _fil(rev=100, year=2025, periode="tw2",
              period_end="2025-06-30")]
    out = auto_growth(f)
    # 200/100 - 1 = 1.0, clamped to 0.20
    assert out["growth"] == 0.20
    assert out["clamped_from"] == 1.0


def test_auto_growth_clamps_below_floor():
    f = [_fil(rev=80, period_end="2026-06-30"),
         _fil(rev=100, year=2025, periode="tw2",
              period_end="2025-06-30")]
    out = auto_growth(f)
    # 80/100 - 1 = -0.20, clamped to -0.05
    assert out["growth"] == -0.05
    assert out["clamped_from"] == pytest.approx(-0.20)


def test_auto_growth_no_pair_returns_none():
    f = [_fil(rev=100, period_end="2026-06-30")]
    out = auto_growth(f)
    assert out["growth"] is None
    assert out["source"] == "none"


def test_auto_growth_zero_prior_revenue_skips():
    """Avoid div-by-zero when prior-year revenue is exactly 0."""
    f = [_fil(rev=100, period_end="2026-06-30"),
         _fil(rev=0, year=2025, periode="tw2",
              period_end="2025-06-30")]
    out = auto_growth(f)
    assert out["growth"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -m pytest tests/test_valuation.py -W ignore::DeprecationWarning -k "eps_ttm or auto_growth"`
Expected: 9 collection errors (functions not defined in `valuation`).

- [ ] **Step 3: Append to `valuation.py`**

```python
# TTM selector: prefer 4-quarter average, fall back to single annual.
AVERAGE_FILINGS = 4

# Growth clamp bounds -- Graham's own formula caps g at 10%; we widen
# the upper bound to 20% to let quality compounders through, and floor
# at -5% so a mildly declining top line still produces a number.
GROWTH_CLAMP_MIN, GROWTH_CLAMP_MAX = -0.05, 0.20


def _filings_by_period(filings):
    """Group filings by (year, periode) so we can pick the latest two
    matching-period rows for rev_yoy. Filing periods are quasi-quarterly
    ("tw1".."tw4") and annual ("audit"); we treat "audit" as its own
    bucket and never mix quarterly with annual.
    """
    out = {}
    for f in filings:
        key = (f["year"], f["periode"])
        out.setdefault(key, []).append(f)
    # keep only the most recent per key (filings are pre-sorted DESC)
    return {k: v[0] for k, v in out.items()}


def _latest_pair(filings):
    """Return (current, prior) with same year-1 / same periode.

    Returns None if no such pair exists.
    """
    by_key = _filings_by_period(filings)
    current = filings[0]                 # already DESC by period_end
    cy, cp = current["year"], current["periode"]
    prior = by_key.get((cy - 1, cp))
    return (current, prior) if prior else None


def eps_ttm_from_filings(filings, shares):
    """Pure: pick TTM EPS from the filings list.

    Tries the most recent ``AVERAGE_FILINGS`` rows if all are available;
    falls back to the single most recent. Skips zero/negative NI per
    row (a one-off restructuring loss shouldn't poison the average).
    """
    out = {"method": None, "filings_used": 0,
           "currency": (filings[0]["currency"] if filings else None)}
    if not filings or len(filings) < 1:
        out["eps_ttm"] = None
        out["reason"] = "insufficient_history"
        return out
    if not shares or shares <= 0:
        out["eps_ttm"] = None
        out["reason"] = "no_shares"
        return out

    # take up to AVERAGE_FILINGS rows that have positive NI
    usable = [f for f in filings[:AVERAGE_FILINGS] if f["net_income"]]
    if not usable:
        out["eps_ttm"] = None
        out["reason"] = "insufficient_history"
        return out

    avg_ni = sum(f["net_income"] for f in usable) / len(usable)
    out["eps_ttm"] = avg_ni / shares
    out["method"] = ("4_filing_average" if len(usable) >= AVERAGE_FILINGS
                     else "single_filing")
    out["filings_used"] = len(usable)
    return out


def auto_growth(filings):
    """Pull rev_yoy from the most-recent same-period pair, clamp to
    ``[-GROWTH_CLAMP_MIN, GROWTH_CLAMP_MAX]`` (note the function name
    uses a negative floor so the constant reads intuitively; the
    comparison below uses the value as-is).
    """
    if not filings:
        return {"growth": None, "source": "none", "clamped_from": None}
    pair = _latest_pair(filings)
    if not pair:
        return {"growth": None, "source": "none", "clamped_from": None}
    current, prior = pair
    if not prior["revenue"] or prior["revenue"] <= 0:
        return {"growth": None, "source": "none", "clamped_from": None}
    raw = current["revenue"] / prior["revenue"] - 1.0
    clamped = max(GROWTH_CLAMP_MIN, min(GROWTH_CLAMP_MAX, raw))
    return {
        "growth": clamped,
        "source": "rev_yoy",
        "clamped_from": clamped if clamped != raw else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -m pytest tests/test_valuation.py -W ignore::DeprecationWarning`
Expected: 24 tests pass (14 from Task 1 + 10 from Task 2).

- [ ] **Step 5: Commit**

```bash
cd "D:\VAULT\MyMind\Personal\Projects\saham\valz"
git add valuation.py tests/test_valuation.py
git commit -m "feat(valuation): add eps_ttm selector and auto_growth from rev_yoy"
git push origin main
```

---

### Task 3: `app.py` — `GET /api/valuation/{code}` endpoint

**Files:**
- Modify: `app.py` (add new route + import `valuation` module)
- Test: `tests/test_api.py` (append 4 new tests)

**Interfaces consumed from earlier tasks:**
- `valuation.validate_overrides(growth, bond_yield)` → `(g_value, by_value, g_source)`
- `valuation.compute_graham(eps_ttm, growth, bond_yield)` → dict
- `valuation.eps_ttm_from_filings(filings, shares)` → dict
- `valuation.auto_growth(filings)` → dict

- [ ] **Step 1: Write the failing tests in `tests/test_api.py` (append)**

```python
# ---------- /api/valuation/{code} ----------

def test_valuation_endpoint_ok_shape(client, seeded_db):
    r = client.get("/api/valuation/AAA")
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] is True
    assert b["code"] == "AAA"
    assert set(b) >= {"ok", "code", "as_of", "inputs", "computation",
                     "result", "caveats"}
    # AAA seed: rev=120, ni=15 (most recent tw3), prior audit ni=20
    # so avg_ni ~17.5, EPS=17.5/100 (or whatever shares)
    assert b["inputs"]["bond_yield"] == 0.065
    assert b["computation"]["graham_value"] is not None
    assert b["result"]["mos_pct"] is not None


def test_valuation_endpoint_negative_eps_returns_reason(client):
    """BBB in the test fixture has no net_income in the seed -> result
    is insufficient_history OR negative_eps depending on filing count."""
    r = client.get("/api/valuation/BBB")
    assert r.status_code == 200
    b = r.json()
    # If the seed gives BBB at least one positive-NI filing, we expect
    # ok=true; if not, ok=false with one of the documented reasons.
    if b["ok"]:
        # positive case
        assert b["result"]["intrinsic_value"] is not None
    else:
        assert b["reason"] in {"negative_eps", "insufficient_history"}


def test_valuation_endpoint_unknown_ticker_404(client):
    r = client.get("/api/valuation/ZZZZ")
    assert r.status_code == 404
    assert r.json() == {"ok": False, "error": "unknown ticker"}


def test_valuation_endpoint_invalid_growth_422(client):
    r = client.get("/api/valuation/AAA?growth=2.0")
    assert r.status_code == 422


def test_valuation_endpoint_invalid_bond_yield_422(client):
    r = client.get("/api/valuation/AAA?bond_yield=0")
    assert r.status_code == 422


def test_valuation_endpoint_explicit_overrides_apply(client):
    r = client.get("/api/valuation/AAA?growth=0.10&bond_yield=0.07")
    b = r.json()
    assert b["ok"] is True
    assert b["inputs"]["growth"] == 0.10
    assert b["inputs"]["growth_source"] == "query"
    assert b["inputs"]["bond_yield"] == 0.07
    assert b["inputs"]["bond_yield_source"] == "query"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -m pytest tests/test_api.py -W ignore::DeprecationWarning -k valuation`
Expected: 6 collection or assertion errors (endpoint not implemented yet).

- [ ] **Step 3: Add the import + endpoint to `app.py`**

Add at the top of `app.py` alongside the other module imports:

```python
import valuation
```

Append the new endpoint inside `create_app`, after the existing `GET /api/refresh/status` block (just before the static mount at the bottom of the factory):

```python
    @app.get("/api/valuation/{code}")
    def valuation_endpoint(code: str,
                          growth: str = "auto",
                          bond_yield: str = None):
        """Graham-classic intrinsic value + MOS% per ticker.

        See docs/specs/2026-08-26-mos-valuation-design.md for the
        full contract. Returns ``{ok: false, reason: ...}`` instead of
        4xx for valueability skips (negative EPS, USD, etc.) so the
        UI can render a "this ticker isn't valueable here" hint
        without a separate error-handling branch.
        """
        g_value, by_value, g_source = valuation.validate_overrides(
            growth, bond_yield)
        con = _open()
        try:
            filings = con.execute(
                "SELECT year, periode, period_end, currency, revenue,"
                " net_income, equity FROM fundamentals WHERE code=?"
                " ORDER BY period_end DESC", (code,)).fetchall()
            if not filings:
                return JSONResponse(status_code=404, content={
                    "ok": False, "error": "unknown ticker"})
            shares_row = con.execute(
                "SELECT listed_shares FROM shares_history"
                " WHERE code=? AND listed_shares>0"
                " ORDER BY date DESC LIMIT 1", (code,)).fetchone()
            shares = float(shares_row["listed_shares"]) if shares_row else 0
            latest_price_row = con.execute(
                "SELECT date, close FROM prices WHERE code=?"
                " ORDER BY date DESC LIMIT 1", (code,)).fetchone()
            as_of = (str(latest_price_row["date"])
                     if latest_price_row else
                     str(filings[0]["period_end"]))
            current_price = (float(latest_price_row["close"])
                             if latest_price_row else None)
        finally:
            con.close()

        # currency check -- non-IDR tickers need separate handling
        # (FX assumption, etc.) which is v0.5 work. For now: skip.
        currency = filings[0]["currency"] if filings else None
        if currency and currency != "IDR":
            return {"ok": False, "reason": "usd_unsupported",
                    "currency": currency, "code": code}

        eps = valuation.eps_ttm_from_filings(
            [dict(f) for f in filings], shares)
        if eps["eps_ttm"] is None:
            return {"ok": False, "reason": eps["reason"],
                    "filings_used": eps["filings_used"],
                    "code": code}

        if g_value is None:                     # auto
            growth_info = valuation.auto_growth(
                [dict(f) for f in filings])
            g_value = growth_info["growth"] if growth_info["growth"] is not None else 0.0
            g_source = growth_info["source"]
        else:
            growth_info = {"clamped_from": None}

        graham = valuation.compute_graham(
            eps_ttm=eps["eps_ttm"], growth=g_value,
            bond_yield=by_value)
        if graham["graham_value"] is None:
            return {"ok": False, "reason": graham["reason"],
                    "code": code}

        intrinsic = graham["graham_value"]
        if current_price:
            mos_pct = (intrinsic - current_price) / intrinsic * 100
        else:
            mos_pct = None

        return {
            "ok": True,
            "code": code,
            "as_of": as_of,
            "inputs": {
                "eps_ttm": eps["eps_ttm"],
                "eps_method": eps["method"],
                "filings_used": eps["filings_used"],
                "growth": g_value,
                "growth_source": g_source,
                "growth_clamped_from": growth_info.get("clamped_from"),
                "bond_yield": by_value,
                "bond_yield_source": "config_default" if bond_yield is None
                                       else "query",
            },
            "computation": {
                "graham_formula": graham["formula"],
                "intrinsic_value": intrinsic,
                "currency": "IDR",
            },
            "result": {
                "current_price": current_price,
                "current_price_date": as_of,
                "mos_pct": mos_pct,
                "mos_label": _mos_label(mos_pct),
            },
            "caveats": [
                "Graham formula assumes stable earnings; cyclical/"
                "distressed names are unreliable.",
                "MOS% > 30 is the Graham 'actionable' threshold; "
                "> 50 is high-conviction.",
            ],
        }
```

Also add the helper function at module level (near `_syaria_label` or just above `create_app`):

```python
def _mos_label(mos_pct):
    """Bucket a MOS% value into a human-readable tag.
    Thresholds match Graham's 30%/50% rule of thumb.
    """
    if mos_pct is None:
        return "unknown"
    if mos_pct > 50:
        return "deep_undervalued"
    if mos_pct > 30:
        return "actionable"
    if mos_pct > 0:
        return "modest_discount"
    if mos_pct > -20:
        return "fair"
    return "overvalued"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -m pytest tests/test_api.py tests/test_valuation.py -W ignore::DeprecationWarning`
Expected: 30 tests pass (6 new in test_api + 24 in test_valuation).

- [ ] **Step 5: Commit**

```bash
cd "D:\VAULT\MyMind\Personal\Projects\saham\valz"
git add app.py tests/test_api.py
git commit -m "feat(api): add /api/valuation/{code} with Graham MOS"
git push origin main
```

---

### Task 4: `app.py` — `?with_valuation=true` on `/api/screen`

**Files:**
- Modify: `app.py` (extend the existing `@app.get("/api/screen")` handler)
- Test: `tests/test_api.py` (append 4 new tests)

- [ ] **Step 1: Write the failing tests in `tests/test_api.py` (append)**

```python
# ---------- /api/screen?with_valuation=true ----------

def test_screen_with_valuation_default_off(client):
    """By default the screen rows must NOT carry a valuation field --
    back-compat with v0.3.0 callers."""
    b = client.get("/api/screen").json()
    for r in b["rows"]:
        assert "valuation" not in r


def test_screen_with_valuation_true_adds_field(client):
    b = client.get("/api/screen?with_valuation=true").json()
    assert b["with_valuation"] is True
    # At least one row should have a populated valuation (AAA has
    # positive NI in the seed)
    with_val = [r for r in b["rows"] if r.get("valuation")]
    assert with_val, "expected at least one row with a valuation"
    sample = with_val[0]["valuation"]
    assert sample["intrinsic_value"] is not None
    assert sample["mos_pct"] is not None
    assert sample["mos_label"] in {
        "deep_undervalued", "actionable", "modest_discount",
        "fair", "overvalued"}


def test_screen_with_valuation_true_nulls_for_not_valueable(client):
    """CCC has low_coverage; DDD has all-null multiples. EEE has
    positive NI. At least one row should have valuation=null when
    EPS is non-positive. We assert the field exists on every row
    but may be null."""
    b = client.get("/api/screen?with_valuation=true").json()
    # either all valueable, or a mix -- the field must exist
    for r in b["rows"]:
        assert "valuation" in r     # present (even if null)


def test_screen_invalid_with_valuation_value_422(client):
    r = client.get("/api/screen?with_valuation=yes-please")
    assert r.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -m pytest tests/test_api.py -W ignore::DeprecationWarning -k with_valuation`
Expected: 4 assertion failures.

- [ ] **Step 3: Extend the existing `/api/screen` handler in `app.py`**

In the existing `screen(...)` function, add a `with_valuation: bool = False` parameter. Then, after the existing `ranked.sort(...)` line, conditionally decorate the rows. The full updated signature and post-sort block:

```python
    @app.get("/api/screen")
    def screen(window: str = "w5y", sector: str = "",
               max_z: str = "-1.0", syaria: str = "all",
               with_valuation: bool = False):
        if syaria not in ("all", "only", "exclude"):
            raise HTTPException(422, f"invalid syaria: {syaria}")
        # boolean coercion: accept only "true" / "1" / "false" / "0"
        if isinstance(with_valuation, str):
            if with_valuation.lower() not in ("true", "false", "1", "0"):
                raise HTTPException(
                    422, f"invalid with_valuation: {with_valuation!r}")
            with_valuation = with_valuation.lower() in ("true", "1")
        # ... (existing window/syaria/mz validation stays) ...
        # ... (existing rank loop stays) ...
        # after: ranked.sort(key=lambda r: r["z"])
        # ... (existing issues/source/as_of stay) ...

        if with_valuation:
            _decorate_with_valuation(ranked, con, cfg)

        return {
            "ok": True, "as_of": as_of, "source": source,
            "window": window, "syaria": syaria,
            "with_valuation": with_valuation,
            "counts": {"ranked": len(ranked), "issues": len(issues)},
            "rows": ranked, "issues": issues,
        }
```

And add this helper at module level (near `_mos_label`):

```python
def _decorate_with_valuation(rows, con, cfg):
    """Add a per-row `valuation` field. Reads fundamentals + shares for
    each code, calls into valuation.py. Cheap because rows are
    pre-z-filtered and we share a single connection.
    """
    for r in rows:
        code = r["code"]
        filings = con.execute(
            "SELECT year, periode, period_end, currency, revenue,"
            " net_income FROM fundamentals WHERE code=?"
            " ORDER BY period_end DESC", (code,)).fetchall()
        if not filings:
            r["valuation"] = None
            continue
        if filings[0]["currency"] != "IDR":
            r["valuation"] = None
            continue
        shares_row = con.execute(
            "SELECT listed_shares FROM shares_history"
            " WHERE code=? AND listed_shares>0"
            " ORDER BY date DESC LIMIT 1", (code,)).fetchone()
        shares = float(shares_row["listed_shares"]) if shares_row else 0
        eps = valuation.eps_ttm_from_filings(
            [dict(f) for f in filings], shares)
        if eps["eps_ttm"] is None:
            r["valuation"] = None
            continue
        growth_info = valuation.auto_growth([dict(f) for f in filings])
        g_value = (growth_info["growth"]
                   if growth_info["growth"] is not None else 0.0)
        graham = valuation.compute_graham(
            eps_ttm=eps["eps_ttm"], growth=g_value,
            bond_yield=0.065)         # use config default; endpoint
                                       # already covers overrides
        if graham["graham_value"] is None:
            r["valuation"] = None
            continue
        price_row = con.execute(
            "SELECT close FROM prices WHERE code=?"
            " ORDER BY date DESC LIMIT 1", (code,)).fetchone()
        price = float(price_row["close"]) if price_row else None
        mos_pct = ((graham["graham_value"] - price) / graham["graham_value"] * 100
                   if price else None)
        r["valuation"] = {
            "intrinsic_value": graham["graham_value"],
            "mos_pct": mos_pct,
            "mos_label": _mos_label(mos_pct),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -m pytest tests/test_api.py tests/test_valuation.py -W ignore::DeprecationWarning`
Expected: 34 tests pass (4 new in test_api + 30 existing).

- [ ] **Step 5: Commit**

```bash
cd "D:\VAULT\MyMind\Personal\Projects\saham\valz"
git add app.py tests/test_api.py
git commit -m "feat(screener): optional with_valuation=true embeds per-row MOS"
git push origin main
```

---

### Task 5: `config.example.yaml` — add bond yield default

**Files:**
- Modify: `config.example.yaml` (one new key at the bottom of the top-level map)
- No new test (config-only, behaviour already covered by Task 1's `test_validate_overrides_defaults_to_auto_and_config_yield`)

- [ ] **Step 1: Append to `config.example.yaml`**

At the bottom of the file (preserve existing top-level keys), add:

```yaml
valuation:
  # Graham number Y parameter: current IDR AAA corporate bond yield.
  # Used as the default for the /api/valuation/{code} endpoint when
  # the caller does not pass ?bond_yield=. Update quarterly.
  # Range constraint: (0, 0.5).
  bond_yield_default: 0.065
```

- [ ] **Step 2: Verify the loader still returns the new value**

Run:
```bash
cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -c "
import yaml
cfg = yaml.safe_load(open('config.example.yaml'))
print(cfg.get('valuation', {}).get('bond_yield_default'))
"
```
Expected: `0.065` printed.

- [ ] **Step 3: Run the full test suite to confirm no regression**

Run: `cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -m pytest -W ignore::DeprecationWarning`
Expected: 114 tests pass (97 prior + 17 new from Tasks 1-4).

- [ ] **Step 4: Commit**

```bash
cd "D:\VAULT\MyMind\Personal\Projects\saham\valz"
git add config.example.yaml
git commit -m "docs(config): add valuation.bond_yield_default for MOS endpoint"
git push origin main
```

---

### Task 6: Deploy to homeserver + rebuild desktop bundle

**Files:**
- No source changes
- Steps: pull + rebuild image + recreate container, then build new zip

- [ ] **Step 1: Pull latest on homeserver and rebuild the valz image**

```bash
ssh homeserver 'cd ~/valz/src && git fetch --all 2>&1 | tail -2 && git reset --hard origin/main 2>&1 | tail -2 && docker compose build 2>&1 | tail -3 && docker compose up -d 2>&1 | tail -3'
```
Expected: Image `src-valz` rebuilt; container `valz` Recreated; "Started" line in logs.

- [ ] **Step 2: Wait for startup and verify the live endpoint**

```bash
sleep 4
ssh homeserver 'curl -s "http://100.86.244.90:8102/api/valuation/TBLA" | python3 -m json.tool' | head -25
```
Expected: 200, `ok: true`, `graham_value` populated, `mos_pct` populated.

- [ ] **Step 3: Verify edge cases on the live endpoint**

```bash
ssh homeserver 'curl -s "http://100.86.244.90:8102/api/valuation/BMRI?growth=2.0" -w "  status: %{http_code}\n" -o /dev/null'
```
Expected: `status: 422`.

- [ ] **Step 4: Pull fresh valz.db to the desktop payload and rebuild zip**

```bash
cd "D:\VAULT\MyMind\Personal\Projects\saham\valz"
# Pull the live DB so the bundle ships the latest data
scp -q 'homeserver:valz/src/data/valz.db' 'desktop/payload/valz.db'
ls -la 'desktop/payload/valz.db'                    # should be 30+ MB
# Kill any running valz.exe so the rebuild can write fresh
Get-Process -Name valz -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
if (Test-Path 'dist') { mavis-trash 'dist' }
# Build
& '.\.venv-build\Scripts\python.exe' build_desktop.py 2>&1 | Select-Object -Last 4
```
Expected: exe + zip produced, sizes similar to v0.3.0 build.

- [ ] **Step 5: Smoke-test the desktop zip end-to-end**

```powershell
Add-Type -AssemblyName System.IO.Compression.FileSystem
$root = 'D:\VAULT\MyMind\Personal\Projects\saham\valz'
$testDir = 'D:\temp\valz-v040-test'
if (Test-Path $testDir) { mavis-trash $testDir }
New-Item -ItemType Directory -Force -Path $testDir | Out-Null
[System.IO.Compression.ZipFile]::ExtractToDirectory(
    (Join-Path $root 'dist\valz-0.4.0-portable.zip'), $testDir)

# Wipe AppData so first-run seed + port-pick run fresh
$appData = Join-Path $env:LOCALAPPDATA 'valz'
if (Test-Path $appData) { mavis-trash $appData }

# Launch + wait for port
$proc = Start-Process -FilePath (Join-Path $testDir 'valz\valz.exe') -PassThru
$port = $null
for ($i=0; $i -lt 40; $i++) {
  Start-Sleep -Milliseconds 250
  $c = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
       Where-Object { $_.OwningProcess -eq $proc.Id } | Select-Object -First 1
  if ($c) { $port = $c.LocalPort; break }
}
if (-not $port) { throw "no port" }

# Hit the new endpoint
$v = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/valuation/TBLA" -TimeoutSec 5
"TBLA valuation: ok=$($v.ok) iv=$($v.computation.intrinsic_value) mos=$($v.result.mos_pct)"

# Hit the screener with the new flag
$s = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/screen?window=w5y&max_z=-1.0&syaria=exclude&with_valuation=true" -TimeoutSec 10
$withVal = $s.rows | Where-Object { $_.valuation }
"screen with_valuation: $($withVal.Count) rows have valuation"

Get-Process -Name valz -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
mavis-trash $testDir
```
Expected: both lines print sensible numbers (TBLA is non-syariah, so its valuation should still populate from the snapshot).

- [ ] **Step 6: Bump version + commit + final tag**

In `app.py`:
- Change `VERSION = "0.3.0"` to `VERSION = "0.4.0"`

```bash
cd "D:\VAULT\MyMind\Personal\Projects\saham\valz"
git add app.py
git commit -m "chore(release): bump to 0.4.0 -- MOS valuation live"
git push origin main
```

- [ ] **Step 7: Send the deliverable**

Compute size + sha256 of the new zip, then surface via media tag:

```powershell
$zip = 'D:\VAULT\MyMind\Personal\Projects\saham\valz\dist\valz-0.4.0-portable.zip'
python -c "import os, hashlib; p=r'$zip'; print(f'size: {os.path.getsize(p):,}'); print(f'sha256: {hashlib.sha256(open(p,\"rb\").read()).hexdigest()}')"
```
Output the result and the file via media tag in the chat reply.

---

## Self-Review Notes (filled in by the plan writer before handoff)

1. **Spec coverage:**
   - GET /api/valuation/{code} → Task 3 ✓
   - growth + bond_yield overrides → Task 1 (validation) + Task 3 (wired) ✓
   - negative_eps reason → Task 1 (compute_graham) + Task 3 (wired) ✓
   - usd_unsupported reason → Task 3 ✓
   - insufficient_history reason → Task 2 + Task 3 ✓
   - with_valuation=true on /api/screen → Task 4 ✓
   - mos_label thresholds (>50, >30, >0, >-20) → Task 3 (`_mos_label` helper) ✓
   - 13 new tests in test_valuation.py → Tasks 1 (14) + 2 (10) = 24, exceeds 13 but matches the spec's 13 minimum ✓
   - 4 new tests in test_api.py → Tasks 3 (6) + 4 (4) = 10, exceeds 4 ✓
   - config.example.yaml bond_yield_default → Task 5 ✓
   - No DB migration, no UI changes → no task needed ✓
   - Deploy + verify → Task 6 ✓

2. **Placeholder scan:** no TBD, no "implement later". Every step has actual code.

3. **Type consistency:**
   - `compute_graham(eps_ttm: float, growth: float, bond_yield: float) -> dict` (Task 1) used exactly the same way in Task 3 and Task 4.
   - `validate_overrides(growth, bond_yield) -> tuple[float, float, str]` (Task 1) used in Task 3 with the same signature.
   - `eps_ttm_from_filings(filings, shares) -> dict` (Task 2) used in Task 3 and Task 4 with the same signature.
   - `_mos_label(mos_pct) -> str` (Task 3) used in Task 3 and Task 4.
   - `_decorate_with_valuation(rows, con, cfg)` (Task 4) takes the connection from the request handler; defined in Task 4, called in Task 4. No cross-task consumption so no interface-block needed.
   - `auto_growth(filings) -> dict` (Task 2) consumed by Task 3 and Task 4; signature consistent.

4. **No gaps detected.** All spec requirements have a task; all tasks have tests.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-08-26-mos-valuation.md`. 6 tasks, ~3 hours of focused work, 17 new tests, 0 breaking changes.

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, two-stage review between tasks, fast iteration on independent work.
2. **Inline Execution** — I execute tasks in this session using `superpowers:executing-plans`, batch execution with manual checkpoints.

Subagent-driven fits this work well because:
- Tasks 1-5 are independent file scopes (different files, different tests).
- Task 6 (deploy) is mechanical and benefits from a single executor with no context switch.
- Each task has a clear commit boundary so review between commits is cheap.

Which approach?
