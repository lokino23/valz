import json, pathlib

import pytest

from db import connect, init_db
from shares import implied_shares_series, shares_at

SERIES = [("2021-12-31", 123.0e9), ("2023-06-30", 124.0e9)]
OVR = [{"code": "BBRI", "date": "2022-06-10", "mult": 2.0}]

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "bbca_2021_audit.json"


def _db(tmp_path):
    p = str(tmp_path / "t.db")
    init_db(p)
    return connect(p)


def test_latest_le_date():
    assert shares_at(SERIES, [], "2022-01-05") == 123.0e9
    assert shares_at(SERIES, [], "2019-01-01") is None
    assert shares_at(SERIES, [], "2024-01-01") == 124.0e9


def test_override_multiplies():
    assert shares_at(SERIES, OVR, "2022-06-11") == 246.0e9   # 123e9 * 2 after event
    assert shares_at(SERIES, OVR, "2022-01-05") == 123.0e9   # unaffected before


def test_override_code_filter():
    # Overrides carry a code field: only matching tickers may apply.
    assert shares_at(SERIES, OVR, "2022-06-11",
                     code="BBCA") == 123.0e9   # BBRI override must NOT apply
    assert shares_at(SERIES, OVR, "2022-06-11",
                     code="BBRI") == 246.0e9   # own override applies


def test_implied_from_real_fixture(tmp_path):
    """Pin the real idx_fundamentals payload shape: summary is a verdict
    STRING; numerics live under raw (equity_parent) and market (bvps)."""
    con = _db(tmp_path)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    con.execute(
        "INSERT INTO fundamentals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (payload["code"], 2021, payload["periode"], payload["period_end"],
         "IDR", payload["sector"],
         payload["raw"]["revenue"], payload["raw"]["net_income"],
         payload["raw"]["equity_parent"], None, None, None, None,
         json.dumps(payload, ensure_ascii=False), None))
    con.commit()
    series = implied_shares_series(con, "BBCA")
    # implied = equity_parent / market.bvps == ~122.04bn listed shares
    assert series == [("2021-12-31",
                       pytest.approx(122042299500.0, rel=1e-3))]


def test_current_shares_fallback(tmp_path):
    """No derivable bvps anywhere -> single current-shares anchor at
    1900-01-01; no anchor -> empty series."""
    con = _db(tmp_path)
    payload = {"code": "BBRI", "summary": "PASS: 0 fail, 0 warn dari 1 cek",
               "raw": {"equity_parent": 123456789000000.0}}
    con.execute(
        "INSERT INTO fundamentals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("BBRI", 2023, "audit", "2023-12-31", "IDR", "bank",
         None, None, 123456789000000.0, None, None, None, None,
         json.dumps(payload, ensure_ascii=False), None))
    con.commit()
    assert implied_shares_series(con, "BBRI",
                                 current_shares=120.5e9) == [("1900-01-01", 120.5e9)]
    assert implied_shares_series(con, "BBRI", current_shares=None) == []
