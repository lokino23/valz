import datetime as dt, requests

_YURL = "https://query1.finance.yahoo.com/v8/finance/chart/{c}.JK?range={r}y&interval=1d"
_H = {"User-Agent": "Mozilla/5.0 (valz/1.0)"}


def fetch_yahoo(code, years=6):
    r = requests.get(_YURL.format(c=code, r=f"{years}"), headers=_H, timeout=30)
    r.raise_for_status()
    return parse_yahoo(r.json())


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
    import os
    key = os.environ.get("ARJUM_API_KEY")
    n = {}
    for code in codes:
        rows = []
        try: rows = fetch_yahoo(code, cfg["yahoo_years"])
        except Exception: pass
        src = "yahoo"
        if not rows:
            rows = fetch_arjum(code, key); src = "arjum"
        con.executemany("INSERT OR REPLACE INTO prices VALUES(?,?,?,?,?)",
                        [(code, d, c, c, src) for d, c in rows])
        con.commit(); n[code] = (len(rows), src)
    return n
