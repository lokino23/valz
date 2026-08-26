"""MOS valuation: compute_graham formula + override validation.

Pure-function unit tests; no DB, no network.
"""
import pytest
from fastapi import HTTPException

from valuation import (
    compute_graham,
    validate_overrides,
    eps_ttm_from_filings,
    auto_growth,
)


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


# ---------- eps_ttm_from_filings ----------

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
