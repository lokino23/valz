import json, pathlib
import prices

FIX = json.load(open(pathlib.Path(__file__).parent / "fixtures/yahoo_bbca.json", encoding="utf-8"))


def test_parse_yahoo_rows():
    rows = prices.parse_yahoo(FIX)
    assert len(rows) == 50
    d, c = rows[-1]
    assert d >= "2026-01-01" and c > 1000      # IDX price scale sanity


def test_parse_yahoo_garbage():
    assert prices.parse_yahoo({"chart": {"result": []}}) == []


def test_arjum_skips_without_key(monkeypatch):
    monkeypatch.setattr(prices, "_arjum_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert prices.fetch_arjum("BBCA", None) == []
