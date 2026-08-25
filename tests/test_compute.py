import datetime as dt
from db import init_db, connect
from compute import compute_all, check

CFG = {
    "universe": ["GOOD", "BAD"], "sector_map": {},
    "groups": {"general": {"primary": "per", "secondary": "pbv"}},
    "windows_days": {"w3y": 300, "w5y": 600},   # small windows for determinism
    "min_coverage": 0.8, "filing_lag_days": 90,
}

def _seed(p):
    init_db(p); con = connect(p)
    d0 = dt.date(2023, 1, 2)
    con.executemany("INSERT INTO prices VALUES(?,?,?,?,?)",
        [("GOOD", (d0 + dt.timedelta(days=i)).isoformat(), 100 + i * 0.05, None, "yahoo")
         for i in range(1000)])
    frows = []
    for q in range(8):                                   # 8 quarters, PK-safe years
        pe = dt.date(2023, 3, 31) + dt.timedelta(days=91 * q)
        frows.append(("GOOD", 2023 + q // 4, ("tw1", "tw2", "tw3", "audit")[q % 4],
                      pe.isoformat(), "IDR", "consumer",
                      100.0 + q, 10.0 + q, 400.0 + 10 * q, 50.0, 20.0,
                      15.0 + q, 3.0, "{}", pe.isoformat()))
    con.executemany("INSERT INTO fundamentals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", frows)
    d1 = dt.date(2025, 1, 2)
    con.executemany("INSERT INTO prices VALUES(?,?,?,?,?)",
        [("BAD", (d1 + dt.timedelta(days=i)).isoformat(), 5.0, None, "yahoo")
         for i in range(300)])                           # zero filings -> all multiples None
    con.commit()
    return con

def test_compute_all_stats_and_issues(tmp_path):
    p = str(tmp_path / "t.db"); _seed(p)
    r = compute_all(p, CFG)
    con = connect(p, readonly=True)
    st = {(x["code"], x["window"]): x["n_obs"]
          for x in con.execute("SELECT * FROM stats")}
    assert ("GOOD", "w5y") in st and st[("GOOD", "w5y")] >= 480
    issues = {x["code"]: x["reason"] for x in con.execute("SELECT * FROM coverage_issues")}
    assert "BAD" in issues
    assert r["ok"] == 1 and r["issues"] == 1
    last = con.execute("SELECT value FROM meta WHERE key='last_compute'").fetchone()
    assert last is not None

def test_check_clean(tmp_path):
    p = str(tmp_path / "t.db"); _seed(p); compute_all(p, CFG)
    assert check(p, CFG) == []

def test_compute_isolates_per_code_failure(tmp_path, monkeypatch):
    """Per-code isolation: a code whose pipeline raises lands in
    coverage_issues as compute_error:* without aborting the other codes."""
    p = str(tmp_path / "t.db"); _seed(p)
    import compute as cmod
    real = cmod.build_multiples

    def boom(pr, fr, shares, ovr, lag, code=None):
        if code == "BAD":
            raise RuntimeError("boom")
        return real(pr, fr, shares, ovr, lag, code=code)

    monkeypatch.setattr(cmod, "build_multiples", boom)
    r = compute_all(p, CFG)                              # must NOT raise
    assert r["ok"] == 1 and r["issues"] == 1
    con = connect(p, readonly=True)
    issues = {x["code"]: x["reason"]
              for x in con.execute("SELECT * FROM coverage_issues")}
    assert issues["BAD"].startswith("compute_error:")
    assert "GOOD" not in issues


def test_unknown_group_label_falls_back_to_general(tmp_path):
    """sector_map pointing at an unknown group label no longer KeyErrors:
    group_primary falls back to general config so the code still computes
    (final-review finding 3 follow-up)."""
    p = str(tmp_path / "t.db"); _seed(p)
    cfg = dict(CFG, sector_map={"GOOD": "nonexistent_group"})
    r = compute_all(p, cfg)                              # must NOT raise
    assert r["ok"] >= 1                                  # GOOD computed fine


def test_sector_fallback_via_fundamentals(tmp_path):
    """No sector_map entry -> fundamentals.sector decides the group label;
    with a matching groups entry the issuer takes its sector lane."""
    p = str(tmp_path / "t.db"); _seed(p)                 # GOOD has sector=consumer
    cfg = dict(CFG, groups=dict(CFG["groups"],
                                consumer={"primary": "pbv", "secondary": "per"}))
    compute_all(p, dict(cfg, sector_map={}))
    con = connect(p, readonly=True)
    row = con.execute(
        "SELECT n_obs FROM stats WHERE code='GOOD' AND window='w5y'").fetchone()
    assert row is not None and row["n_obs"] > 0
