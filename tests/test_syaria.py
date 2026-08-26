"""Syaria (DES) field + ?syaria=only|exclude|all filter contract.

Tests use an injected syaria_set so we stay network- and disk-free. A
dedicated des_snapshot.json loader test is included for the on-disk
fallback (covered separately so the API tests don't fight the file
system).
"""
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import (_load_syaria_default, _syaria_filter_ok, _syaria_flag,
                 create_app)
from db import init_db

TICKER_KEYS = {"ok", "meta", "stats", "filings", "series", "source",
               "as_of", "syaria", "peer"}

CFG = {
    "universe": ["AAA", "BBB", "CCC", "DDD", "EEE"],
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


def _seed(p):
    init_db(p)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.executemany("INSERT INTO prices VALUES(?,?,?,?,?)", [
        ("AAA", "2026-08-20", 100.0, None, "yahoo"),
        ("BBB", "2026-08-20", 55.0, None, "yahoo"),
        ("CCC", "2026-08-20", 200.0, None, "accumulator"),
        ("DDD", "2026-08-20", 70.0, None, "yahoo"),
        ("EEE", "2026-08-20", 80.0, None, "arjum"),
    ])
    con.executemany("INSERT INTO multiples VALUES(?,?,?,?,?,?)", [
        ("AAA", "2026-08-20", 8.0, 1.0, 5.0, 0.5),
        ("BBB", "2026-08-20", 11.0, 2.0, 6.0, 0.6),
        ("CCC", "2026-08-20", 9.0, 0.5, 7.0, 0.7),
        ("DDD", "2026-08-20", 5.0, 1.0, 4.0, 4.0),
        ("EEE", "2026-08-20", 6.0, 1.0, 4.5, 0.45),
    ])
    con.executemany("INSERT INTO stats VALUES(?,?,?,?,?)", [
        ("AAA", "w5y", 10.0, 2.0, 1100),
        ("BBB", "w5y", 10.0, 2.0, 520),
        ("CCC", "w5y", 2.0, 0.5, 50),
        ("DDD", "w5y", 10.0, 2.0, 100),
        ("EEE", "w5y", 10.0, 2.0, 600),
    ])
    con.execute("INSERT INTO coverage_issues VALUES(?,?,?,?)",
                ("CCC", "low_coverage:w5y", "{}", "2026-08-25T00:00:00"))
    con.commit()
    con.close()


@pytest.fixture()
def client(tmp_path):
    p = str(tmp_path / "sy.db")
    _seed(p)
    return TestClient(create_app(
        db_path=p, cfg=CFG,
        syaria_set=frozenset({"AAA", "BBB", "DDD", "EEE"})))  # CCC = non-syariah


# -------------------------------------------- /api/screen syaria param

def test_screen_syaria_default_is_all(client):
    """No ?syaria= -> all rows returned, syaria flag attached."""
    b = client.get("/api/screen").json()
    assert b["syaria"] == "all"
    rows = {r["code"]: r for r in b["rows"]}
    # AAA/EEE/CCC are ranked; syaria flag must reflect set membership.
    assert rows["AAA"]["syaria"] is True
    assert rows["CCC"]["syaria"] is False
    assert rows["EEE"]["syaria"] is True


def test_screen_syaria_only_excludes_non_syariah(client):
    b = client.get("/api/screen?syaria=only").json()
    assert b["syaria"] == "only"
    codes = {r["code"] for r in b["rows"]}
    # CCC is non-syariah -> must not appear
    assert "CCC" not in codes


def test_screen_syaria_exclude_keeps_only_non_syariah(client):
    b = client.get("/api/screen?syaria=exclude").json()
    assert b["syaria"] == "exclude"
    codes = {r["code"] for r in b["rows"]}
    assert codes == {"CCC"}


def test_screen_syaria_invalid_returns_422_before_db(client):
    assert client.get("/api/screen?syaria=mixed").status_code == 422


# -------------------------------------------- /api/ticker syaria field

def test_ticker_syaria_field_in_set(client):
    b = client.get("/api/ticker/AAA").json()
    assert "syaria" in b
    assert b["syaria"] is True
    assert set(b) == TICKER_KEYS


def test_ticker_syaria_field_not_in_set(client):
    b = client.get("/api/ticker/CCC").json()
    assert b["syaria"] is False


def test_ticker_syaria_null_when_set_empty(tmp_path):
    """An empty syaria_set means 'no snapshot loaded' -> null, not False."""
    p = str(tmp_path / "no.db")
    _seed(p)
    c = TestClient(create_app(db_path=p, cfg=CFG, syaria_set=frozenset()))
    b = c.get("/api/ticker/AAA").json()
    assert b["syaria"] is None
    b2 = c.get("/api/screen").json()
    for r in b2["rows"]:
        assert r["syaria"] is None


# -------------------------------------------- /api/meta

def test_meta_reports_syaria_count(client):
    b = client.get("/api/meta").json()
    assert b["syaria_codes"] == 4   # AAA BBB DDD EEE


# -------------------------------------------- helpers

def test_syaria_flag_three_state():
    """None when set is empty, True/False otherwise."""
    assert _syaria_flag(frozenset(), "AAA") is None
    assert _syaria_flag(frozenset({"AAA"}), "AAA") is True
    assert _syaria_flag(frozenset({"AAA"}), "BBB") is False


def test_syaria_filter_ok_truth_table():
    """all  -> everything passes; only -> True; exclude -> False."""
    for f in (None, True, False):
        assert _syaria_filter_ok("all", f) is True
    assert _syaria_filter_ok("only", True) is True
    assert _syaria_filter_ok("only", False) is False
    assert _syaria_filter_ok("only", None) is False     # unknown drops out
    assert _syaria_filter_ok("exclude", False) is True
    assert _syaria_filter_ok("exclude", True) is False
    assert _syaria_filter_ok("exclude", None) is False   # unknown drops out
    # unknown filter: pass through (422 catches it earlier)
    assert _syaria_filter_ok("weird", True) is True


# -------------------------------------------- on-disk loader

def test_load_syaria_default_reads_json(tmp_path, monkeypatch):
    """When data/des_snapshot.json exists, _load_syaria_default reads it."""
    p = tmp_path / "des_snapshot.json"
    p.write_text(json.dumps({"codes": ["AAA", "BBB", "CCC"]}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _load_syaria_default() == frozenset({"AAA", "BBB", "CCC"})


def test_load_syaria_default_handles_missing(monkeypatch, tmp_path):
    """No file in any of the candidate paths -> empty frozenset."""
    monkeypatch.chdir(tmp_path)
    assert _load_syaria_default() == frozenset()


def test_load_syaria_default_handles_malformed(tmp_path, monkeypatch):
    p = tmp_path / "des_snapshot.json"
    p.write_text("not-json-{", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _load_syaria_default() == frozenset()
