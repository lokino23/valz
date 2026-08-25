import pytest

from multiples import build_multiples

# 2 quarters of filings, simple numbers
FR = [
 {"code":"X","year":2025,"periode":"tw1","period_end":"2025-03-31",
  "revenue":100.0,"net_income":10.0,"equity":400.0,"total_debt":100.0,"cash":50.0,"ebitda":20.0},
 {"code":"X","year":2025,"periode":"tw2","period_end":"2025-06-30",
  "revenue":120.0,"net_income":12.0,"equity":410.0,"total_debt":90.0,"cash":60.0,"ebitda":24.0},
]
SH = [("1900-01-01", 10.0)]


def test_golden_alignment():
    # tw1 available 2025-06-29 (period_end+90d); tw2 available 2025-09-28
    PX3 = [("2025-08-01", 24.4), ("2025-09-15", 30.0), ("2025-10-01", 30.0)]
    rows = build_multiples(PX3, FR, SH, [], filing_lag_days=90)
    aug = next(r for r in rows if r["date"] == "2025-08-01")
    assert aug["per_ttm"] is None            # only tw1 available: TTM needs >=2 quarters
    oct_ = next(r for r in rows if r["date"] == "2025-10-01")
    # NI TTM = 10+12 = 22; shares 10 => EPS 2.2; price 30 => PER 13.63..
    assert abs(oct_["per_ttm"] - 30.0 / (22.0 / 10.0)) < 1e-9
    # EV = mcap 300 + debt 90 - cash 60 = 330; EBITDA TTM = 20+24 = 44
    assert abs(oct_["ev_ebitda"] - 330.0 / 44.0) < 1e-9
    assert abs(oct_["pbv"] - (30.0 * 10.0) / 410.0) < 1e-9


def test_negative_denominator_excluded():
    FRN = [dict(FR[0], net_income=-5.0)]
    rows = build_multiples([("2025-08-01", 24.4)], FRN, SH, [])
    assert rows[0]["per_ttm"] is None


def test_override_code_filter():
    """Authorized deviation (mirrors the Task 5 shares_at fix): a foreign
    ticker's CA override must not multiply this ticker's share count."""
    ovr = [{"code": "OTHER", "date": "2025-07-01", "mult": 2.0}]
    px = [("2025-08-01", 24.4)]
    base = build_multiples(px, FR, SH, [], filing_lag_days=90)[0]
    assert base["pbv"] == pytest.approx(24.4 * 10.0 / 400.0)
    mine = build_multiples(px, FR, SH, ovr, filing_lag_days=90, code="X")[0]
    assert mine["pbv"] == base["pbv"]              # OTHER's event ignored for X
    theirs = build_multiples(px, FR, SH, ovr,
                             filing_lag_days=90, code="OTHER")[0]
    assert theirs["pbv"] == pytest.approx(24.4 * 20.0 / 400.0)  # doubled shares
    legacy = build_multiples(px, FR, SH, ovr, filing_lag_days=90)[0]
    assert legacy["pbv"] == theirs["pbv"]          # code=None applies all (compat)


def test_none_denominators_emit_none_fields():
    """Bank reality (Task 3): total_debt/cash/ebitda are frequently None in
    IDX payloads -> only ev_ebitda goes None; PER/PBV/P-S still compute."""
    FRB = [dict(FR[0], total_debt=None, cash=None, ebitda=None),
           dict(FR[1], total_debt=None, cash=None, ebitda=None)]
    r = build_multiples([("2025-10-01", 30.0)], FRB, SH, [])[0]
    assert r["ev_ebitda"] is None
    assert r["per_ttm"] == pytest.approx(30.0 / (22.0 / 10.0))
    assert r["pbv"] == pytest.approx(300.0 / 410.0)
    assert r["ps_ttm"] == pytest.approx(300.0 / 220.0)
