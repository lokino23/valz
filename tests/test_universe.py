import yaml

from universe import seed_universe, write_universe

def _codes(*cs):
    return [{"code": c, "market_cap": 1} for c in cs]

class FakeClient:
    """Asserts the REAL idx-mcp arg shape: year(int)/periode/limit — no 'period'
    key (passing 'period' returns 0 rows silently on the real server)."""
    def __init__(self, table):
        self.table = table                      # {(year, periode): [rows]}
        self.calls = []

    def call(self, name, args):
        assert name == "idx_fundamentals_screen"
        assert set(args) == {"year", "periode", "limit"}
        assert isinstance(args["year"], int)
        assert args["periode"] in ("tw1", "tw2", "tw3", "audit")
        assert args["limit"] == 500
        self.calls.append((args["year"], args["periode"]))
        rows = self.table.get((args["year"], args["periode"]), [])
        return {"count": len(rows), "rows": rows}

SCREEN_ROWS = _codes("BBCA", "BBRI", "TLKM")

def test_seed_universe_union_with_watchlist(tmp_path):
    (tmp_path / "_x.md").write_text("private note", encoding="utf-8")
    (tmp_path / "ARCI.md").write_text("# ARCI", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not md", encoding="utf-8")
    got = seed_universe(FakeClient({(2026, "tw2"): SCREEN_ROWS}), str(tmp_path))
    assert got == ["ARCI", "BBCA", "BBRI", "TLKM"]   # union; _x.md never enters

def test_seed_universe_screen_only_when_watchlist_none():
    assert seed_universe(FakeClient({(2026, "tw2"): SCREEN_ROWS}), None) == \
        ["BBCA", "BBRI", "TLKM"]

def test_seed_universe_screen_only_when_dir_missing(tmp_path):
    got = seed_universe(FakeClient({(2026, "tw2"): SCREEN_ROWS}),
                        str(tmp_path / "nope"))
    assert got == ["BBCA", "BBRI", "TLKM"]

def test_seed_universe_ladder_descends_until_ge50():
    thin = _codes(*[f"A{i:02d}" for i in range(10)])
    rich = _codes(*[f"B{i:03d}" for i in range(50)])
    fc = FakeClient({(2026, "tw2"): thin, (2026, "tw1"): rich})
    got = seed_universe(fc, None)
    assert fc.calls == [(2026, "tw2"), (2026, "tw1")]  # stopped at >=50 rung
    assert got == [f"B{i:03d}" for i in range(50)]     # codes from that rung only

def test_write_universe_round_trip_preserves_other_keys(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("filing_lag_days: 90\nwindows_days:\n  w5y: 1260\n",
                 encoding="utf-8")
    write_universe(str(p), ["BBRI", "BBCA"])
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert cfg["universe"] == ["BBRI", "BBCA"]
    assert cfg["filing_lag_days"] == 90
    assert cfg["windows_days"] == {"w5y": 1260}

def test_write_universe_creates_missing_file(tmp_path):
    p = tmp_path / "fresh.yaml"
    write_universe(str(p), ["TLKM"])
    assert yaml.safe_load(p.read_text(encoding="utf-8")) == {"universe": ["TLKM"]}
