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
            "der", "flags", "syaria", "peer"}
SCREEN_KEYS = {"ok", "as_of", "source", "window", "syaria", "counts",
               "rows", "issues"}
TICKER_KEYS = {"ok", "meta", "stats", "filings", "series", "source",
               "as_of", "syaria", "peer"}
META_KEYS = {"ok", "last_compute", "universe_count", "coverage",
             "syaria_codes", "version"}


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
    # make syaria membership explicit in tests: AAA, BBB, DDD, EEE = syaria;
    # CCC = non-syariah. Unknown codes (e.g. ticker not in set) = None.
    return TestClient(create_app(
        db_path=p, cfg=CFG,
        syaria_set=frozenset({"AAA", "BBB", "DDD", "EEE"})))


@pytest.fixture()
def seeded_db(client):
    """Augments the client fixture's DB with the extra rows the
    /api/valuation endpoint needs: ``shares_history`` (so EPS can be
    computed) plus a single BBB filing with NI=0 (so the
    ``negative_eps_returns_reason`` test can exercise the
    insufficient_history branch). The existing ``_seed`` is left
    untouched per the brief.
    """
    p = client.app.state.refresher.db_path
    con = connect(p)
    con.executemany(
        "INSERT INTO shares_history VALUES(?,?,?,?)",
        [("AAA", "2026-08-20", 100.0, "yahoo"),
         ("BBB", "2026-08-20", 100.0, "yahoo"),
         ("CCC", "2026-08-20", 100.0, "yahoo"),
         ("DDD", "2026-08-20", 100.0, "yahoo"),
         ("EEE", "2026-08-20", 100.0, "yahoo")])
    con.executemany(
        "INSERT INTO fundamentals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("BBB", 2025, "tw2", "2025-06-30", "IDR", "consumer",
          50.0, 0.0, 200.0, 100.0, 30.0, 8.0, 2.0, "{}", "2025-07-31")])
    con.commit()
    con.close()
    return client


@pytest.fixture()
def client_with_peers(tmp_path):
    """Same seed as ``client`` but with ``peer_groups`` injected into
    the cfg so we can exercise the ``peer`` field without touching
    the deployed config. ``peer_groups: {consumer: [AAA, BBB, DDD]}``
    leaves EEE and CCC outside any group -- giving us both the
    "member" and "non-member" cases from the same fixture.
    """
    p = str(tmp_path / "peer.db")
    _seed(p)
    cfg_with_peers = {**CFG,
        "peer_groups": {"consumer": ["AAA", "BBB", "DDD"]}}
    return TestClient(create_app(
        db_path=p, cfg=cfg_with_peers,
        syaria_set=frozenset({"AAA", "BBB", "DDD", "EEE"})))


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
    assert b["version"] == "0.5.0"


# ------------------------------------------------------- /api/valuation/{code}


def test_valuation_endpoint_ok_shape(seeded_db):
    r = seeded_db.get("/api/valuation/AAA")
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] is True
    assert b["code"] == "AAA"
    assert set(b) >= {"ok", "code", "as_of", "inputs", "computation",
                     "result", "caveats"}
    # AAA seed: 3 positive-NI filings (8, 20, 15) -> avg 14.33, EPS=14.33/100
    assert b["inputs"]["bond_yield"] == 0.065
    assert b["computation"]["graham_value"] is not None
    assert b["result"]["mos_pct"] is not None


def test_valuation_endpoint_negative_eps_returns_reason(seeded_db):
    """BBB has one filing with NI=0 (per seeded_db) so eps_ttm_from_filings
    filters it out -> reason='insufficient_history'."""
    r = seeded_db.get("/api/valuation/BBB")
    assert r.status_code == 200
    b = r.json()
    if b["ok"]:
        # positive case
        assert b["result"]["intrinsic_value"] is not None
    else:
        assert b["reason"] in {"negative_eps", "insufficient_history"}


def test_valuation_endpoint_unknown_ticker_404(seeded_db):
    r = seeded_db.get("/api/valuation/ZZZZ")
    assert r.status_code == 404
    assert r.json() == {"ok": False, "error": "unknown ticker"}


def test_valuation_endpoint_invalid_growth_422(seeded_db):
    r = seeded_db.get("/api/valuation/AAA?growth=2.0")
    assert r.status_code == 422


def test_valuation_endpoint_invalid_bond_yield_422(seeded_db):
    r = seeded_db.get("/api/valuation/AAA?bond_yield=0")
    assert r.status_code == 422


def test_valuation_endpoint_explicit_overrides_apply(seeded_db):
    r = seeded_db.get("/api/valuation/AAA?growth=0.10&bond_yield=0.07")
    b = r.json()
    assert b["ok"] is True
    assert b["inputs"]["growth"] == 0.10
    assert b["inputs"]["growth_source"] == "query"
    assert b["inputs"]["bond_yield"] == 0.07
    assert b["inputs"]["bond_yield_source"] == "query"


# ----------------------------------------- /api/screen?with_valuation=true --


def test_screen_with_valuation_default_off(client):
    """By default the screen rows must NOT carry a valuation field --
    back-compat with v0.3.0 callers."""
    b = client.get("/api/screen").json()
    for r in b["rows"]:
        assert "valuation" not in r


def test_screen_with_valuation_true_adds_field(seeded_db):
    """``with_valuation=true`` embeds a ``valuation`` object on each row.
    Uses ``seeded_db`` (adds ``shares_history``) so AAA's positive-NI
    filings produce a populated valuation -- the default ``client``
    fixture has no shares rows and would yield only null valuations.
    """
    b = seeded_db.get("/api/screen?with_valuation=true").json()
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
    """Default ``client`` has no ``shares_history`` rows, so every
    screener row is "not valueable" (eps_ttm_from_filings returns
    ``no_shares``). The field must still exist on every row -- callers
    rely on a stable schema, not on every value being populated."""
    b = client.get("/api/screen?with_valuation=true").json()
    for r in b["rows"]:
        assert "valuation" in r     # present (even if null)


def test_screen_invalid_with_valuation_value_422(client):
    r = client.get("/api/screen?with_valuation=yes-please")
    assert r.status_code == 422


# ---------------------------------------------------- /api/ticker + /api/screen
#                                              + peer field (Task 2 / v0.5.0)


def test_ticker_includes_peer_field_for_member(client_with_peers):
    """AAA is in the test peer group; the field should be present and
    carry the expected shape (group, count, median, high_base_warning)."""
    b = client_with_peers.get("/api/ticker/AAA?window=w5y").json()
    assert b["ok"] is True
    assert b.get("peer") is not None
    p = b["peer"]
    assert p["group"] == "consumer"
    assert p["count"] >= 2
    assert isinstance(p["median"], (int, float))
    assert isinstance(p["high_base_warning"], bool)


def test_ticker_peer_is_null_for_non_member(client_with_peers):
    """EEE is in no peer group in the test fixture; the field should
    be null. (CCC is also not in any group -- this is the EEE branch.)"""
    b = client_with_peers.get("/api/ticker/EEE?window=w5y").json()
    assert b["ok"] is True
    assert b.get("peer") is None


def test_screen_rows_include_peer_per_row(client_with_peers):
    """Each ranked row gets a peer object (or null for non-members)."""
    b = client_with_peers.get(
        "/api/screen?window=w5y&max_z=-1.0").json()
    assert b["rows"], "fixture must rank at least one code"
    by_code = {r["code"]: r for r in b["rows"]}
    # AAA is in the consumer peer group -> non-null peer object
    assert by_code["AAA"].get("peer") is not None
    assert by_code["AAA"]["peer"]["group"] == "consumer"
    # EEE and CCC are not in any peer group -> null peer
    assert by_code.get("EEE", {}).get("peer") is None
    assert by_code.get("CCC", {}).get("peer") is None
