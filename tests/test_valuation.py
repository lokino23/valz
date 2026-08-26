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
