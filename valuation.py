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
