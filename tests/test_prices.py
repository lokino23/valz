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


def test_fetch_yahoo_429_retries_with_backoff(monkeypatch):
    """fetch_yahoo should retry on 429 (up to 3 times) and eventually return data."""
    calls = {"n": 0}
    sleeps = []

    def fake_get(url, headers, timeout):
        calls["n"] += 1
        class R:
            status_code = 429 if calls["n"] < 3 else 200
            headers = {"Retry-After": "1"} if calls["n"] < 3 else {}
            def json(self_inner): return FIX
            def raise_for_status(self_inner): pass
        return R()

    def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(prices.requests, "get", fake_get)
    monkeypatch.setattr(prices.time, "sleep", fake_sleep)
    rows = prices.fetch_yahoo("BBCA", years=6)
    assert len(rows) == 50                                  # eventually succeeded
    assert calls["n"] == 3                                  # 2 retries + 1 success
    # Every 429 retry must include the Retry-After=1 sleep (plus the
    # inter-attempt backoff from _429_BACKOFF). Check the Retry-After
    # sleeps are honored: 1s after the first 429 and 1s after the second.
    retry_after_sleeps = [s for s in sleeps if s == 1]
    assert len(retry_after_sleeps) >= 2, f"expected at least 2 Retry-After=1 sleeps, got {sleeps}"


def test_fetch_yahoo_gives_up_after_4_attempts(monkeypatch):
    """fetch_yahoo should raise after 4 failed attempts (1 initial + 3 backoff)."""
    calls = {"n": 0}

    def fake_get(url, headers, timeout):
        calls["n"] += 1
        class R:
            status_code = 429
            headers = {"Retry-After": "0"}
            def raise_for_status(self_inner): pass
            def json(self_inner): return {}
        return R()

    monkeypatch.setattr(prices.requests, "get", fake_get)
    monkeypatch.setattr(prices.time, "sleep", lambda s: None)
    try:
        prices.fetch_yahoo("BBCA", years=6)
    except RuntimeError as e:
        assert "429" in str(e)
        assert calls["n"] == 4                              # 1 initial + 3 retries
    else:
        raise AssertionError("expected RuntimeError after 4 failed attempts")


def test_merge_prices_sleeps_between_tickers(monkeypatch):
    """merge_prices should call time.sleep between each ticker (not before the first)."""
    sleeps = []
    monkeypatch.setattr(prices.time, "sleep", lambda s: sleeps.append(s))

    # fake fetch_yahoo: return empty so it falls through to arjum (also fake empty)
    monkeypatch.setattr(prices, "fetch_yahoo", lambda code, years=6: [])
    monkeypatch.setattr(prices, "fetch_arjum", lambda code, key, limit=500: [])

    # in-memory sqlite
    import sqlite3, tempfile, os
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE prices(code TEXT, date TEXT, close REAL, adj_close REAL, source TEXT)")

    try:
        prices.merge_prices(con, {"yahoo_years": 6}, ["A", "B", "C", "D"])
        # 4 tickers → 3 sleeps between them (not before the first)
        assert len(sleeps) == 3
        assert all(abs(s - 0.5) < 1e-9 for s in sleeps)
    finally:
        con.close()
        os.unlink(path)
