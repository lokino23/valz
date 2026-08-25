import json, pathlib
import pytest
from fundamentals_fetch import parse_fundamentals

FIX = json.load(open(pathlib.Path(__file__).parent / "fixtures/bbca_2021_audit.json", encoding="utf-8"))

def test_parse_real_bbca():
    row = parse_fundamentals(FIX)
    assert row["code"] == "BBCA" and row["year"] == 2021 and row["periode"] == "audit"
    assert row["sector"] == "bank"
    assert row["currency"] == "IDR"
    # anchors = REAL captured values (BBCA FY2021 audit, interest-income basis)
    assert row["net_income"] == pytest.approx(31422660000000.0, rel=1e-9)
    assert row["equity"] == pytest.approx(202712762000000.0, rel=1e-9)
    assert row["revenue"] == pytest.approx(65626976000000.0, rel=1e-9)
    assert row["cash"] == pytest.approx(177268685000000.0, rel=1e-9)
    # not present in this filing payload -> must be None, never mis-mapped
    assert row["total_debt"] is None and row["ebitda"] is None and row["da"] is None

def test_missing_fields_are_none():
    row = parse_fundamentals({"code":"X","year":2024,"periode":"tw1","summary":{}})
    assert row["net_income"] is None and row["ebitda"] is None

def test_year_derived_from_period_end_when_absent():
    row = parse_fundamentals({"code":"bbca","periode":"audit","currency":"RUPIAH / IDR",
                              "period_end":"2019-12-31"})
    assert row["year"] == 2019 and row["code"] == "BBCA" and row["currency"] == "IDR"

def test_backfill_resume_and_missing(tmp_path):
    from db import init_db, connect
    from fundamentals_fetch import backfill_fundamentals
    p = str(tmp_path/"t.db"); init_db(p); con = connect(p)
    class Fake:
        def call(self, name, args):
            if args["periode"] == "tw2": raise RuntimeError("boom")   # filing absent, deterministically
            d = dict(FIX); d["periode"] = args["periode"]             # real server echoes requested periode
            return d
    r = backfill_fundamentals(con, Fake(), {}, ["BBCA"], [2021])
    assert r == {"fetched": 3, "cached": 0, "missing": 1}
    r2 = backfill_fundamentals(con, Fake(), {}, ["BBCA"], [2021])
    assert r2["cached"] == 3 and r2["fetched"] == 0                   # resume skips existing
    assert r2["missing"] == 1                                         # absent filing stays missing
