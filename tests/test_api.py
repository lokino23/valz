"""Task 10 contract tests: read-only REST API over a hand-seeded sqlite db.

Fixture style follows tests/test_compute.py (_seed helper + tmp_path), but
rows are inserted directly so every derived number asserted here is
computable by hand -- no dependence on pipeline internals, zero network.
"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import create_app
from db import init_db, connect

CFG = {
    "universe": [],
    "sector_map": {"CCC": "bank"},
    "groups": {
        "general": {"primary": "per", "secondary": "pbv"},
        "bank": {"primary": "pbv", "secondary": "per"},
    },
    "windows_days": {"w3y": 300, "w5y": 600},
    "min_coverage": 0.8,
    "filing_lag_days": 90,
    "thresholds": {"watch": -1.0, "deep": -2.0},
}

ROW_KEYS = {"code", "sector_group", "primary_var", "value_now", "mean",
            "sigma", "z", "disc_pct", "streak_days", "roe_ttm", "rev_yoy",
            "der", "flags"}
SCREEN_KEYS = {"ok", "as_of", "source", "window", "counts", "rows", "issues"}
TICKER_KEYS = {"ok", "meta", "stats", "filings", "series", "source", "as_of"}
META_KEYS = {"ok", "last_compute", "universe_count", "coverage", "version"}


def _seed(p):
    init_db(p)
    con = connect(p)
    # --- prices (source variety drives the mixed/single source rules) ---
    con.executemany("INSERT INTO prices VALUES(?,?,?,?,?)", [
        ("AAA", "2026-08-20", 100.0, None, "yahoo"),
        ("BBB", "2026-08-19", 55.0, None, "yahoo"),
        ("BBB", "2026-08-20", 56.0, None, "idx_accum"),
        ("CCC", "2026-08-20", 200.0, None, "accumulator"),
        ("DDD", "2026-08-20", 70.0, None, "yahoo"),
        ("EEE", "2026-08-20", 80.0, None, "arjum"),
    ])
    # --- fundamentals ---
    # AAA: latest=2025-tw2 (ni 15, eq 500, debt 250, rev 120);
    # prev=2024-audit (ni 20) -> roe=(15+20)/500=0.07; der=250/500=0.5;
    # rev_yoy vs (2024,tw2) rev 100 -> (120-100)/100=0.2
    con.executemany("INSERT INTO fundamentals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("AAA", 2024, "tw2",   "2024-06-30", "IDR", "consumer",
         100.0, 8.0, 400.0, 160.0, 40.0, 12.0, 3.0, "{}", "2024-07-31"),
        ("AAA", 2024, "audit", "2024-12-31", "IDR", "consumer",
         200.0, 20.0, 400.0, 200.0, 60.0, 14.0, 3.5, "{}", "2025-04-30"),
        ("AAA", 2025, "tw2",   "2025-06-30", "IDR", "consumer",
         120.0, 15.0, 500.0, 250.0, 45.0, 13.0, 3.2, "{}", "2025-07-31"),
        # CCC: single filing -> prev-NI missing -> roe/rev_yoy must be null
        ("CCC", 2025, "audit", "2025-12-31", "USD", "bank",
         500.0, 60.0, 1000.0, 800.0, 90.0, 10.0, 2.0, "{}", "2026-03-31"),
    ])
    # --- multiples (per_ttm series; CCC primary is pbv) ---
    con.executemany("INSERT INTO multiples VALUES(?,?,?,?,?,?)", [
        # AAA per_ttm history vs mu=10 sigma=2:
        # z = +1.0, -1.0, -1.5, -1.0 -> streak(watch=-1)=3, latest z=-1.0
        ("AAA", "2026-08-17", 12.0, 1.0, 5.0, 0.5),
        ("AAA", "2026-08-18", 8.0, 1.0, 5.0, 0.5),
        ("AAA", "2026-08-19", 7.0, 1.0, 5.0, 0.5),
        ("AAA", "2026-08-20", 8.0, 1.0, 5.0, 0.5),
        # BBB latest z = (11-10)/2 = +0.5 > max_z(-1) -> skipped from rows
        ("BBB", "2026-08-20", 11.0, 2.0, 6.0, 0.6),
        # CCC pbv latest: z = (0.5-2)/0.5 = -3.0 -> ranked first
        ("CCC", "2026-08-20", 9.0, 0.5, 7.0, 0.7),
        # DDD stats are all-null -> z None -> skipped
        ("DDD", "2026-08-20", 5.0, 1.0, 4.0, 0.4),
        # EEE latest: z = (6-10)/2 = -2.0 -> ranked between CCC and AAA
        ("EEE", "2026-08-20", 6.0, 1.2, 4.5, 0.45),
    ])
    # --- stats (w3y row on AAA proves window isolation) ---
    con.executemany("INSERT INTO stats VALUES(?,?,?,?,?)", [
        ("AAA", "w5y", 10.0, 2.0, 1100),   # 1100 >= .8*600 -> no low_coverage
        ("AAA", "w3y", 99.0, 1.0, 10),
        ("BBB", "w5y", 10.0, 2.0, 520),
        ("CCC", "w5y", 2.0, 0.5, 50),      # 50 < 480 -> low_coverage flag
        ("DDD", "w5y", None, None, 0),
        ("EEE", "w5y", 10.0, 2.0, 600),
    ])
    con.execute("INSERT INTO coverage_issues VALUES(?,?,?,?)",
                ("EEE", "low_coverage:w3y", "{}", "2026-08-25T00:00:00"))
    con.execute("INSERT INTO meta VALUES('last_compute',?)",
                ("2026-08-26T00:00:00",))
    con.commit()
    con.close()


@pytest.fixture()
def client(tmp_path):
    p = str(tmp_path / "api.db")
    _seed(p)
    return TestClient(create_app(db_path=p, cfg=CFG))


# ---------------------------------------------------------------- /api/screen

def test_screen_contract_keys(client):
    b = client.get("/api/screen").json()
    assert set(b) == SCREEN_KEYS
    assert b["ok"] is True and b["window"] == "w5y"
    assert set(b["counts"]) == {"ranked", "issues"}
    assert b["rows"], "fixture must rank at least one code"
    for row in b["rows"]:
        assert set(row) == ROW_KEYS
    for iss in b["issues"]:
        assert set(iss) == {"code", "reason"}


def test_screen_ranking_values_and_flags(client):
    b = client.get("/api/screen").json()
    # z <= max_z(-1.0) only; ascending by z; boundary z == -1.0 is kept
    assert [r["code"] for r in b["rows"]] == ["CCC", "EEE", "AAA"]
    assert b["counts"] == {"ranked": 3, "issues": 1}
    ccc, aaa = b["rows"][0], b["rows"][2]
    assert ccc["z"] == pytest.approx(-3.0)
    assert ccc["sector_group"] == "bank"
    assert ccc["primary_var"] == "pbv"
    assert set(ccc["flags"]) == {"usd", "low_coverage"}
    assert aaa["primary_var"] == "per"
    assert (aaa["value_now"], aaa["mean"], aaa["sigma"]) == (8.0, 10.0, 2.0)
    assert aaa["z"] == pytest.approx(-1.0)
    assert aaa["disc_pct"] == pytest.approx(-20.0)
    assert aaa["streak_days"] == 3          # 8,7,8 below watch then break
    assert set(aaa["flags"]) == set()
    assert b["issues"] == [{"code": "EEE", "reason": "low_coverage:w3y"}]


def test_screen_ratios_null_safe(client):
    rows = {r["code"]: r for r in client.get("/api/screen").json()["rows"]}
    aaa = rows["AAA"]
    assert aaa["roe_ttm"] == pytest.approx((15.0 + 20.0) / 500.0)
    assert aaa["rev_yoy"] == pytest.approx((120.0 - 100.0) / 100.0)
    assert aaa["der"] == pytest.approx(250.0 / 500.0)
    # single filing -> no prior NI / no prior-year quarter -> nulls, not crash
    assert rows["CCC"]["roe_ttm"] is None
    assert rows["CCC"]["rev_yoy"] is None
    assert rows["CCC"]["der"] == pytest.approx(800.0 / 1000.0)


def test_screen_sector_filter_and_scope(client):
    b = client.get("/api/screen?sector=bank").json()
    assert [r["code"] for r in b["rows"]] == ["CCC"]
    assert b["source"] == "accumulator"     # scoped to sector codes
    assert b["as_of"] == "2026-08-20"


def test_screen_source_mixed_vs_single(client):
    b = client.get("/api/screen").json()
    assert b["source"] == "mixed"           # yahoo+idx_accum+accumulator+arjum
    assert b["as_of"] == "2026-08-20"
    one = client.get("/api/ticker/AAA").json()
    assert one["source"] == "yahoo"


def test_screen_invalid_window_422_before_db(client, tmp_path):
    assert client.get("/api/screen?window=q9y").status_code == 422


def test_screen_bad_max_z_422(client):
    assert client.get("/api/screen?max_z=abc").status_code == 422


def test_validation_precedes_db_access(tmp_path):
    """422 must fire before any sqlite touch: a nonexistent db file would
    explode on connect(), so any response at all proves ordering."""
    c = TestClient(create_app(db_path=str(tmp_path / "nope.db"), cfg=CFG))
    assert c.get("/api/screen?window=bogus").status_code == 422
    assert c.get("/api/screen?max_z=nope").status_code == 422
    assert c.get("/api/ticker/AAA?window=bogus").status_code == 422


# ------------------------------------------------------- /api/ticker/{code}

def test_ticker_contract_keys(client):
    b = client.get("/api/ticker/AAA").json()
    assert set(b) == TICKER_KEYS
    assert set(b["meta"]) == {"code", "sector_group", "primary_var",
                              "secondary_var"}
    assert set(b["stats"]) == {"mu", "sigma", "n_obs"}
    for pt in b["series"]:
        assert set(pt) == {"date", "v", "z"}
    assert b["ok"] is True


def test_ticker_series_stats_filings(client):
    b = client.get("/api/ticker/AAA").json()
    assert b["meta"] == {"code": "AAA", "sector_group": "consumer",
                         "primary_var": "per", "secondary_var": "pbv"}
    assert b["stats"] == {"mu": 10.0, "sigma": 2.0, "n_obs": 1100}
    assert [(p["date"], p["v"], p["z"]) for p in b["series"]] == [
        ("2026-08-17", 12.0, pytest.approx(1.0)),
        ("2026-08-18", 8.0, pytest.approx(-1.0)),
        ("2026-08-19", 7.0, pytest.approx(-1.5)),
        ("2026-08-20", 8.0, pytest.approx(-1.0)),
    ]
    assert b["filings"] == ["2024-06-30", "2024-12-31", "2025-06-30"]
    assert b["as_of"] == "2026-08-20"
    assert b["source"] == "yahoo"


def test_ticker_unknown_code_404_exact_body(client):
    r = client.get("/api/ticker/ZZZZ")
    assert r.status_code == 404
    assert r.json() == {"ok": False, "error": "unknown ticker"}


# ----------------------------------------------------------------- /api/meta

def test_meta_contract(client):
    r = client.get("/api/meta")
    assert r.status_code == 200
    b = r.json()
    assert set(b) == META_KEYS
    assert b["ok"] is True
    assert b["last_compute"] == "2026-08-26T00:00:00"
    assert b["universe_count"] == 5         # distinct codes in stats
    assert b["coverage"] == {"ok": 4, "issues": 1}
    assert b["version"] == "0.2.0"
