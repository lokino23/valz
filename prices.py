import datetime as dt, time
import requests

_YURL = "https://query1.finance.yahoo.com/v8/finance/chart/{c}.JK?range={r}y&interval=1d"
_H = {"User-Agent": "Mozilla/5.0 (valz/1.0)"}

# Between-ticker rate limit. Yahoo's anonymous chart endpoint tolerates
# ~2000 req/hour but starts 429-throttling around 500 req/hour for the
# heavy `range=6y&interval=1d` payload. 0.5s/ticker keeps a 113-ticker
# refresh under ~60s total while staying well clear of the limit.
_INTER_TICKER_SLEEP = 0.5

# Per-ticker 429 backoff. Yahoo sets `Retry-After` on 429 responses;
# respect it when present, otherwise fall back to the schedule below.
_429_BACKOFF = (5, 15, 60)


def fetch_yahoo(code, years=6):
    """Fetch daily prices for `code` from Yahoo. Retries 429 with backoff."""
    url = _YURL.format(c=code, r=f"{years}")
    last_err = None
    for attempt, sleep_s in enumerate((0, *_429_BACKOFF)):
        if sleep_s:
            time.sleep(sleep_s)
        try:
            r = requests.get(url, headers=_H, timeout=30)
        except requests.RequestException as e:
            last_err = e
            continue
        if r.status_code == 429:
            # honor server-supplied Retry-After if present
            try:
                ra = int(float(r.headers.get("Retry-After", "") or sleep_s))
            except ValueError:
                ra = sleep_s
            last_err = RuntimeError(f"yahoo 429 for {code}, sleeping {ra}s")
            time.sleep(ra)
            continue
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            last_err = e
            continue
        try:
            return parse_yahoo(r.json())
        except (ValueError, KeyError, TypeError) as e:
            last_err = e
            continue
    if last_err is not None:
        raise last_err
    return []


def parse_yahoo(doc):
    res = (doc.get("chart", {}).get("result") or [None])[0]
    if not res: return []
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    adj = ((res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")) or q
    out = []
    for t, c in zip(ts, adj):
        if c is None: continue
        out.append((dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat(), float(c)))
    return out


def _arjum_get(url, params, key):
    return requests.get(url, params=params, timeout=30,
                        headers={"X-API-Key": key or "", "User-Agent": "saham-dashboard/1.0"}).json()


def fetch_arjum(code, key, limit=500):
    if not key: return []                      # graceful skip, no quota use
    try:
        doc = _arjum_get(f"https://stock.arjum.com/api/history/{code}",
                         {"limit": limit}, key)
        rows = doc.get("rows") or []
        return [(r["date"], float(r["close"])) for r in rows if r.get("close")]
    except Exception:
        return []


def merge_prices(con, cfg, codes):
    """Fetch prices for `codes`, write to `con`, return {code: (n, src)}.

    Sleeps `_INTER_TICKER_SLEEP` seconds between tickers to stay below
    Yahoo's anonymous throttle. Per-ticker 429s are retried with backoff
    inside `fetch_yahoo`; the inter-ticker sleep also helps Yahoo's
    server-side rate limiter recover between bursts.
    """
    import os
    key = os.environ.get("ARJUM_API_KEY")
    n = {}
    for i, code in enumerate(codes):
        if i > 0:
            time.sleep(_INTER_TICKER_SLEEP)
        rows = []
        try: rows = fetch_yahoo(code, cfg["yahoo_years"])
        except (requests.RequestException, ValueError, RuntimeError): pass
        src = "yahoo"
        if not rows:
            rows = fetch_arjum(code, key); src = "arjum"
        con.executemany("INSERT OR REPLACE INTO prices VALUES(?,?,?,?,?)",
                        [(code, d, c, c, src) for d, c in rows])
        con.commit(); n[code] = (len(rows), src)
    return n
