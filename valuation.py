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
        out["filings_used"] = len(filings)
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
        "clamped_from": (raw if clamped != raw else None),
    }
