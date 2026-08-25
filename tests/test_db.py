import sqlite3
from db import init_db, connect

def test_init_creates_tables(tmp_path):
    p = str(tmp_path / "t.db")
    init_db(p)
    con = connect(p)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"prices","fundamentals","shares_history","multiples","stats",
            "coverage_issues","meta"} <= names

def test_pk_conflict(tmp_path):
    p = str(tmp_path / "t.db"); init_db(p)
    con = connect(p)
    con.execute("INSERT INTO meta VALUES('k','v')")
    try:
        con.execute("INSERT INTO meta VALUES('k','x')"); assert False
    except sqlite3.IntegrityError: pass
