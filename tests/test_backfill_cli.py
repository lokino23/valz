"""Task 12 CLI contract tests: dry-run wiring with fakes, zero network."""
import backfill


class FakeResp:
    def __init__(self):
        self.n = 0

    def call(self, name, args):
        self.n += 1
        if name == "idx_shares":
            return {"listed_shares": 1e9, "date": "2026-08-24"}
        return {"code": args["code"], "year": args["year"], "periode": args["periode"],
                "period_end": "2025-03-31", "currency": "IDR", "sector": "consumer",
                "summary": {"revenue": 100, "net_income": 5, "equity": 400}}


def test_main_dry_run(tmp_path, monkeypatch):
    import prices
    monkeypatch.setattr(backfill, "McpClient", lambda url: FakeResp())
    monkeypatch.setattr(prices, "merge_prices",
                        lambda con, cfg, codes: {c: (10, "yahoo") for c in codes})
    rc = backfill.main(["--tickers", "BBCA", "--db", str(tmp_path / "t.db"),
                        "--dry-run"])
    assert rc == 0


def test_main_dry_run_empty_universe_fails(monkeypatch, tmp_path):
    """No tickers arg + empty config universe -> SystemExit guard."""
    import prices
    monkeypatch.setattr(backfill, "McpClient", lambda url: FakeResp())
    monkeypatch.setattr(prices, "merge_prices", lambda con, cfg, codes: {})
    cfgfile = tmp_path / "empty.yaml"
    cfgfile.write_text("universe: []\n", encoding="utf-8")
    try:
        backfill.main(["--db", str(tmp_path / "t.db"), "--dry-run",
                       "--config", str(cfgfile)])
        raised = False
    except SystemExit:
        raised = True
    assert raised, "empty universe must raise SystemExit"


def test_tickers_normalized_uppercase(monkeypatch, tmp_path):
    """Codes are stripped + uppercased before hitting the pipeline."""
    seen = {}
    import prices

    def fake_merge(con, cfg, codes):
        seen["codes"] = list(codes)
        return {c: (1, "yahoo") for c in codes}

    monkeypatch.setattr(backfill, "McpClient", lambda url: FakeResp())
    monkeypatch.setattr(prices, "merge_prices", fake_merge)
    backfill.main(["--tickers", " bbca , Bbri,", "--db",
                   str(tmp_path / "t.db"), "--dry-run"])
    assert seen["codes"] == ["BBCA", "BBRI"]
