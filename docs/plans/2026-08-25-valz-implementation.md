# valz (Valuation Z-Score Screener) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Standalone web screener that ranks LQ45+IDX80 stocks by how far their sector-appropriate valuation multiple (PER/PBV/EV-EBITDA) sits below its own 3Y/5Y mean in standard deviations, with a drill-down chart per ticker.

**Architecture:** Precompute pipeline (`backfill.py` pulls prices + quarterly XBRL fundamentals into SQLite; `compute.py` builds daily TTM multiple series and z-scores) behind a thin read-only FastAPI app serving a static ECharts frontend. Mirrors saham-dashboard patterns: Docker `network_mode: host`, port 8096, nightly cron, provenance fields on every response.

**Tech Stack:** Python 3.12 (stdlib sqlite3/statistics), FastAPI + uvicorn, requests, PyYAML, pytest; vanilla JS + ECharts (vendored). No pandas, no yfinance.

**Spec:** `docs/specs/2026-08-25-valuation-zscore-design.md` (in this repo — read it first).

## Global Constraints

- Port **8096**, Docker Compose `network_mode: host`, working dir on homeserver `~/valz`.
- DB file at `data/valz.db` — regenerable, NEVER committed (`.gitignore` already covers `*.db`, `data/`).
- Every API response carries `source` (one of `yahoo | arjum | accumulator | mixed`) and `as_of` (ISO date or `null`; client renders null as "Tanggal data tidak tersedia").
- USD-reporting tickers are excluded from valuation ranking with a flag, never crash.
- Commit after every step that changes code/docs (conventional messages: `feat:`, `test:`, `fix:`, `chore:`); push to Forgejo at least after each task (standing user rule).
- idx-mcp endpoint URL comes from env `IDX_MCP_URL` (default `http://[IP_ADDRESS]:8001/mcp`). Never hardcode IPs anywhere else.
- Arjum calls need env `ARJUM_API_KEY` + header `User-Agent: saham-dashboard/1.0` (Cloudflare blocks default python UA). Absent key ⇒ arjum fallback silently skipped.
- Windows: trading-day counts `w3y=756`, `w5y=1260`. Min coverage ratio 0.8. Filing lag 90 days. Winsorize percentiles [0.01, 0.99].
- PowerShell is the local shell (no `&&`, no bash-isms); homeserver commands run via `ssh homeserver '<cmd>'`.

## File Structure (final state)

```
valz/
├── config.example.yaml      # template; real config.yaml untracked
├── requirements.txt         # fastapi, uvicorn, requests, pyyaml; pytest in requirements-dev.txt
├── schema.sql               # all CREATE TABLEs
├── db.py                    # init_db(path), connect(path, readonly=False)
├── config.py                # load_config(path) -> dict with defaults merged
├── mcp_client.py            # McpClient(url).call(tool, args) -> dict; _parse_sse(text)
├── universe.py              # seed_universe(client, watchlist_dir) -> sorted list
├── fundamentals_fetch.py    # parse_fundamentals(payload) -> row|None; backfill_fundamentals(...)
├── prices.py                # fetch_yahoo(code) -> rows; fetch_arjum(code, key); merge_prices(con, cfg, codes)
├── shares.py                # implied_shares_series(frows) -> [(date, shares)]; shares_at(series, overrides, d)
├── multiples.py             # build_multiples(prices, frows, shares_rows, overrides) -> rows
├── zstats.py                # winsorize(vals, lo, hi); fit(series, window) -> (mu, sigma, n); streak(rows, mu, sigma, thr)
├── compute.py               # CLI: recompute everything from tables; --check selftest
├── app.py                   # FastAPI: GET /api/screen /api/ticker/{code} /api/meta; mounts static/
├── static/index.html        # filter bar + ranked table + drawer chart (ECharts)
├── static/vendor/echarts.min.js
├── backfill.py              # CLI orchestrating universe seed + fundamentals + prices backfill (--tickers, --dry-run)
├── refresh.sh               # nightly incremental: prices update + season check + compute
├── Dockerfile               # python:3.12-slim
├── docker-compose.yml       # network_mode: host, port 8096, ./data volume
├── tests/
│   ├── fixtures/bbca_2021_audit.json   # REAL captured idx_fundamentals payload
│   ├── fixtures/yahoo_bbca.json        # canned Yahoo chart JSON (trimmed)
│   └── test_*.py            # one file per module
└── docs/specs/…, docs/plans/…
```

---

### Task 1: Scaffold + config loader + DB layer

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `config.example.yaml`, `schema.sql`, `config.py`, `db.py`
- Test: `tests/test_config.py`, `tests/test_db.py`

**Interfaces:**
- Produces: `load_config(path: str|None) -> dict` (merges `config.example.yaml` defaults when path None); `init_db(path: str) -> None`; `connect(path: str, readonly: bool = False) -> sqlite3.Connection` (row_factory=sqlite3.Row).

- [ ] **Step 1: Write deps files**

`requirements.txt`:
```
fastapi>=0.110
uvicorn>=0.29
requests>=2.31
PyYAML>=6.0
```
`requirements-dev.txt`:
```
-r requirements.txt
pytest>=8.0
httpx>=0.27
```

- [ ] **Step 2: Write config.example.yaml**

```yaml
universe: []                 # filled by backfill.py --seed
sector_map: {}               # ticker -> group; seeded manually/by script later
groups:
  bank:      {primary: pbv,      secondary: per}
  financial: {primary: pbv,      secondary: per}
  consumer:  {primary: per,      secondary: pbv}
  general:   {primary: per,      secondary: pbv}
  commodity: {primary: ev_ebitda, secondary: ps}
  property:  {primary: pbv,      secondary: per}
windows_days: {w3y: 756, w5y: 1260}
min_coverage: 0.8
filing_lag_days: 90
winsor_pct: [0.01, 0.99]
thresholds: {watch: -1.0, deep: -2.0}
ca_overrides: []             # [{code: BBRI, date: "2024-06-10", mult: 0.83}]
yahoo_years: 6
```

- [ ] **Step 3: Write failing tests**

`tests/test_config.py`:
```python
from config import load_config

def test_defaults_when_no_file():
    cfg = load_config(None)
    assert cfg["windows_days"]["w5y"] == 1260
    assert cfg["groups"]["bank"]["primary"] == "pbv"
    assert cfg["filing_lag_days"] == 90

def test_user_overrides_win(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("filing_lag_days: 60\n", encoding="utf-8")
    assert load_config(str(p))["filing_lag_days"] == 60
```

`tests/test_db.py`:
```python
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
```

- [ ] **Step 4: Run tests → expect FAIL (modules missing)**

Run: `python -m pytest tests/test_config.py tests/test_db.py -v`

- [ ] **Step 5: Implement `config.py` and `db.py` + `schema.sql`**

`config.py`:
```python
import os, yaml
_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = yaml.safe_load(open(os.path.join(_DIR, "config.example.yaml"), encoding="utf-8"))

def load_config(path=None):
    cfg = dict(DEFAULTS)
    if path:
        cfg.update(yaml.safe_load(open(path, encoding="utf-8")) or {})
    return cfg
```

`schema.sql`:
```sql
CREATE TABLE IF NOT EXISTS prices(
  code TEXT NOT NULL, date TEXT NOT NULL, close REAL,
  adj_close REAL, source TEXT, PRIMARY KEY(code,date));
CREATE TABLE IF NOT EXISTS fundamentals(
  code TEXT NOT NULL, year INTEGER NOT NULL, periode TEXT NOT NULL,
  period_end TEXT, currency TEXT, sector TEXT,
  revenue REAL, net_income REAL, equity REAL, total_debt REAL, cash REAL,
  ebitda REAL, da REAL, raw_json TEXT, fetched_at TEXT,
  PRIMARY KEY(code,year,periode));
CREATE TABLE IF NOT EXISTS shares_history(
  code TEXT NOT NULL, date TEXT NOT NULL, listed_shares REAL, source TEXT,
  PRIMARY KEY(code,date));
CREATE TABLE IF NOT EXISTS multiples(
  code TEXT NOT NULL, date TEXT NOT NULL,
  per_ttm REAL, pbv REAL, ev_ebitda REAL, ps_ttm REAL, PRIMARY KEY(code,date));
CREATE TABLE IF NOT EXISTS stats(
  code TEXT NOT NULL, window TEXT NOT NULL,
  mu REAL, sigma REAL, n_obs INTEGER, PRIMARY KEY(code,window));
CREATE TABLE IF NOT EXISTS coverage_issues(
  code TEXT PRIMARY KEY, reason TEXT, detail TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
```

`db.py`:
```python
import pathlib, sqlite3

def init_db(path):
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    schema = pathlib.Path(__file__).parent.joinpath("schema.sql").read_text(encoding="utf-8")
    con = sqlite3.connect(path)
    con.executescript(schema); con.commit(); con.close()

def connect(path, readonly=False):
    uri = f"file:{path}?mode=ro" if readonly else f"file:{path}?mode=rwc"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con
```

- [ ] **Step 6: Run tests → PASS**

Run: `python -m pytest tests/test_config.py tests/test_db.py -v`

- [ ] **Step 7: Commit + push**

```bash
git add requirements.txt requirements-dev.txt config.example.yaml schema.sql config.py db.py tests/test_config.py tests/test_db.py
git commit -m "feat: scaffold valz core - config defaults + sqlite schema"
git push
```

---

### Task 2: MCP client wrapper

**Files:**
- Create: `mcp_client.py`
- Test: `tests/test_mcp_client.py`

**Interfaces:**
- Consumes: nothing (standalone).
- Produces: `McpClient(url).call(name: str, args: dict) -> dict` raising `RuntimeError` on error payloads; `_parse_sse(text: str) -> dict|None`.

- [ ] **Step 1: Failing test**

`tests/test_mcp_client.py`:
```python
from mcp_client import _parse_sse

def test_parse_sse_extracts_last_data():
    raw = 'event: message\ndata: {"id":1,"result":{"a":1}}\n\n'
    assert _parse_sse(raw)["result"]["a"] == 1

def test_parse_sse_none_on_empty():
    assert _parse_sse("retry: 1000\n\n") is None
```

- [ ] **Step 2: Run → FAIL**

Run: `python -m pytest tests/test_mcp_client.py -v`

- [ ] **Step 3: Implement**

```python
import json, threading, urllib.request

class McpClient:
    """Minimal streamable-HTTP MCP client for one idx-mcp endpoint."""
    def __init__(self, url, timeout=60):
        self.url, self.timeout, self._sid, self._lock = url, timeout, None, threading.Lock()
        self._init()

    def _post(self, payload):
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self._sid: h["Mcp-Session-Id"] = self._sid
        req = urllib.request.Request(self.url, json.dumps(payload).encode(), h)
        r = urllib.request.urlopen(req, timeout=self.timeout)
        self._sid = r.headers.get("mcp-session-id") or self._sid
        return r.read().decode("utf-8", "replace")

    def _init(self):
        self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "valz", "version": "1"}}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def call(self, name, args):
        body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": name, "arguments": args}}
        d = _parse_sse(self._post(body))
        if d is None: raise RuntimeError(f"mcp: no data for {name}")
        res = d.get("result", {})
        if isinstance(res.get("structuredContent"), dict):
            return res["structuredContent"]
        out = json.loads(res["content"][0]["text"])
        if isinstance(out, dict) and "error" in out:
            raise RuntimeError(f"mcp tool error: {out['error'][:200]}")
        return out

def _parse_sse(text):
    data = None
    for ln in text.splitlines():
        if ln.startswith("data:"):
            try: data = json.loads(ln[5:].strip())
            except json.JSONDecodeError: pass
    return data
```

- [ ] **Step 4: Run → PASS. Step 5: Commit**

```bash
git add mcp_client.py tests/test_mcp_client.py && git commit -m "feat: minimal streamable-http mcp client" && git push
```

---

### Task 3: Capture real fixture + fundamentals parser

**Files:**
- Create: `tests/fixtures/bbca_2021_audit.json` (captured live), `fundamentals_fetch.py`
- Test: `tests/test_fundamentals_parse.py`

**Interfaces:**
- Consumes: `McpClient.call`.
- Produces: `parse_fundamentals(payload: dict) -> dict` returning `{code, year, periode, period_end, currency, sector, revenue, net_income, equity, total_debt, cash, ebitda, da}` (values may be None when absent); `backfill_fundamentals(con, client, cfg, codes: list[str], years: list[int]) -> dict` returning `{fetched, cached, missing}`.

Field-mapping rule: numbers are read from the payload by trying keys in this priority order inside `summary` then top-level then `recomputed` (first numeric wins): revenue ← `revenue`,`penjualan`,`sales_ttm`,`revenue_ttm`; net_income ← `net_income`,`laba_rugi`,`ni_ttm`,`net_profit`; equity ← `equity`,`total_equity`,`ekuitas`; total_debt ← `total_debt`,`debt`,`utang`; cash ← `cash`,`kas`; ebitda ← `ebitda`,`ebitda_ttm`; da ← `da`,`depreciation`,`depresiasi`. `period_end` ← `period_end` or `periode_end`. Currency from `currency` (contains "USD" ⇒ currency="USD"). Sector from `sector`.

- [ ] **Step 1: Capture the real payload (run once, commit output)**

Create `scripts/capture_fixture.py`:
```python
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_client import McpClient
cli = McpClient(os.environ.get("IDX_MCP_URL", "http://localhost:8001/mcp"))
p = cli.call("idx_fundamentals", {"code": "BBCA", "year": 2021, "periode": "audit"})
with open("tests/fixtures/bbca_2021_audit.json", "w", encoding="utf-8") as f:
    json.dump(p, f, ensure_ascii=False, indent=1)
print("keys:", sorted(p.keys()))
print(json.dumps(p.get("summary", {}), ensure_ascii=False)[:600])
print(json.dumps(p.get("recomputed", {}), ensure_ascii=False)[:600])
```
Run locally (resolve IDX_MCP_URL the same way as earlier probes — parse HostName from `%USERPROFILE%\.ssh\config` block `Host homeserver` into `$env:IDX_MCP_URL`): `python scripts/capture_fixture.py`.
If a field name doesn't match the priority lists above, UPDATE the priority lists in this task's implementation AND note the correction in the commit message.

- [ ] **Step 2: Failing parser test (uses captured fixture)**

`tests/test_fundamentals_parse.py`:
```python
import json, pathlib
from fundamentals_fetch import parse_fundamentals

FIX = json.load(open(pathlib.Path(__file__).parent / "fixtures/bbca_2021_audit.json", encoding="utf-8"))

def test_parse_real_bbca():
    row = parse_fundamentals(FIX)
    assert row["code"] == "BBCA" and row["year"] == 2021 and row["periode"] == "audit"
    assert row["sector"] == "bank"
    assert row["currency"] == "IDR"
    assert row["net_income"] and row["net_income"] > 20e12   # BBCA FY2021 ~ Rp28T+ scale sanity
    assert row["equity"] and row["equity"] > 80e12

def test_missing_fields_are_none():
    row = parse_fundamentals({"code":"X","year":2024,"periode":"tw1","summary":{}})
    assert row["net_income"] is None and row["ebitda"] is None
```
(Adjust the two scale asserts to the REAL captured values in Step 1 before committing — they are sanity anchors, not guesses.)

- [ ] **Step 3: Run → FAIL. Step 4: Implement parser**

```python
import datetime as dt, json

_PRI = {
 "revenue": ["revenue","penjualan","sales_ttm","revenue_ttm"],
 "net_income": ["net_income","laba_rugi","ni_ttm","net_profit"],
 "equity": ["equity","total_equity","ekuitas"],
 "total_debt": ["total_debt","debt","utang"],
 "cash": ["cash","kas"],
 "ebitda": ["ebitda","ebitda_ttm"],
 "da": ["da","depreciation","depresiasi"],
}

def _num(v):
    return float(v) if isinstance(v,(int,float)) else None

def _pick(d, keys):
    for k in keys:
        v = _num((d or {}).get(k))
        if v is not None: return v
    return None

def parse_fundamentals(p):
    summ = p.get("summary") or {}
    rec  = p.get("recomputed") or {}
    cur  = str(p.get("currency") or "")
    row = {"code": str(p.get("code","")).upper(),
           "year": int(p.get("year",0)), "periode": p.get("periode",""),
           "period_end": p.get("period_end") or p.get("periode_end"),
           "currency": "USD" if "USD" in cur.upper() else ("IDR" if cur else None),
           "sector": p.get("sector")}
    for field, keys in _PRI.items():
        row[field] = _pick(summ, keys) or _pick(p, keys) or _pick(rec, keys)
    return row
```

- [ ] **Step 5: Run → PASS. Step 6: Backfill loop with resume (add to same file)**

```python
PERIODES = ("tw1","tw2","tw3","audit")

def backfill_fundamentals(con, client, cfg_unused, codes, years):
    got = cached = missing = 0
    now_y = dt.date.today().year
    have = {(r["code"], r["year"], r["periode"])
            for r in con.execute("SELECT code,year,periode FROM fundamentals")}
    for code in codes:
        for y in years:
            for pd_ in PERIODES:
                if y > now_y: continue
                if (code, y, pd_) in have: cached += 1; continue
                try:
                    p = client.call("idx_fundamentals",
                                    {"code": code, "year": y, "periode": pd_})
                except Exception:
                    missing += 1; continue
                row = parse_fundamentals(p)
                if row["period_end"] is None:          # filing truly absent
                    missing += 1; continue
                con.execute(
                  "INSERT OR REPLACE INTO fundamentals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (row["code"],row["year"],row["periode"],row["period_end"],row["currency"],
                   row["sector"],row["revenue"],row["net_income"],row["equity"],
                   row["total_debt"],row["cash"],row["ebitda"],row["da"],
                   json.dumps(p,ensure_ascii=False), dt.datetime.now().isoformat(timespec="seconds")))
                con.commit(); got += 1
    return {"fetched": got, "cached": cached, "missing": missing}
```
Test it minimally against an in-memory-style temp db with a FakeClient returning the fixture twice + raising once:
```python
def test_backfill_resume_and_missing(tmp_path):
    from db import init_db, connect
    from fundamentals_fetch import backfill_fundamentals
    p = str(tmp_path/"t.db"); init_db(p); con = connect(p)
    class Fake:
        def __init__(self): self.n = 0
        def call(self, name, args):
            self.n += 1
            if self.n == 2: raise RuntimeError("boom")
            return FIX
    r = backfill_fundamentals(con, Fake(), {}, ["BBCA"], [2021])
    assert r["fetched"] == 2 and r["missing"] >= 1
    r2 = backfill_fundamentals(con, Fake(), {}, ["BBCA"], [2021])
    assert r2["cached"] >= 2 and r2["fetched"] == 0     # resume skips existing
```

- [ ] **Step 7: Run all → PASS. Commit (+push)**

```bash
git add fundamentals_fetch.py scripts/ tests/ && git commit -m "feat: xbrl fundamentals parser + resumable backfill" && git push
```

---

### Task 4: Price fetchers (Yahoo primary, Arjum fallback)

**Files:**
- Create: `prices.py`, `tests/fixtures/yahoo_bbca.json`
- Test: `tests/test_prices.py`

**Interfaces:**
- Consumes: `db.connect`, config `yahoo_years`.
- Produces: `fetch_yahoo(code: str, years: int=6) -> list[tuple[date,str]]` (ISO date strings, close floats, split-adjusted `adjclose` used as close); `fetch_arjum(code: str, api_key: str|None, limit: int=500) -> list[...]` ([] when no key/failure); `merge_prices(con, cfg, codes: list[str]) -> dict` writing `prices` table with `source` tag per row (`yahoo` base; arjum fills gaps only where Yahoo returned nothing for that code entirely; accumulator not needed in MVP because Yahoo covers through today).

Yahoo request: `GET https://query1.finance.yahoo.com/v8/finance/chart/{code}.JK?range={n}y&interval=1d` with header `User-Agent: Mozilla/5.0 (valz/1.0)`. Parse `chart.result[0].timestamp[]` + `indicators.quote[0].close[]` + prefer `indicators.adjclose[0].adjclose[]`; skip null closes; timestamps → UTC date ISO.

- [ ] **Step 1: Capture trimmed Yahoo fixture**

`scripts/capture_yahoo_fixture.py`: fetch BBCA via the same URL above, save ONLY `{"chart":{"result":[{"meta":{"symbol":...},"timestamp":[...last 50...],"indicators":{"quote":[{"close":[...]}],"adjclose":[{"adjclose":[...]}]}}]}}` to `tests/fixtures/yahoo_bbca.json`.

- [ ] **Step 2: Failing tests**

```python
import json, pathlib
import prices

FIX = json.load(open(pathlib.Path(__file__).parent/"fixtures/yahoo_bbca.json", encoding="utf-8"))

def test_parse_yahoo_rows():
    rows = prices.parse_yahoo(FIX)
    assert len(rows) == 50
    d, c = rows[-1]
    assert d >= "2026-01-01" and c > 1000      # IDX price scale sanity

def test_parse_yahoo_garbage():
    assert prices.parse_yahoo({"chart":{"result":[]}}) == []

def test_arjum_skips_without_key(monkeypatch):
    monkeypatch.setattr(prices, "_arjum_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert prices.fetch_arjum("BBCA", None) == []
```

- [ ] **Step 3: Run → FAIL. Step 4: Implement**

```python
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
```

- [ ] **Step 5: Run → PASS. Step 6: Commit**

```bash
git add prices.py tests/test_prices.py tests/fixtures/yahoo_bbca.json scripts/ && git commit -m "feat: yahoo .JK price fetcher + arjum gap fallback" && git push
```

---

### Task 5: Implied shares series + corporate-action overrides

**Files:**
- Create: `shares.py`
- Test: `tests/test_shares.py`

**Interfaces:**
- Consumes: `fundamentals` rows (dicts with `period_end`, `equity`, plus optional per-share hints in raw_json summary: `bvps`, `eps`).
- Produces: `implied_shares_series(con, code) -> list[(date_iso, shares)]` sorted ascending — implied = equity ÷ bvps when both present (bvps read from stored raw_json `summary.bvps`), falling back to current anchor via `idx_shares` MCP call passed in as `current_shares: float|None` argument; `shares_at(series: list, overrides: list[dict], d: str) -> float|None` — latest implied ≤ d, multiplied by cumulative override mults with `o["date"] <= d` where o["code"] matches; returns None when no data.

- [ ] **Step 1: Failing tests**

```python
from shares import shares_at

SERIES = [("2021-12-31", 123.0e9), ("2023-06-30", 124.0e9)]
OVR = [{"code": "BBRI", "date": "2022-06-10", "mult": 2.0}]

def test_latest_le_date():
    assert shares_at(SERIES, [], "2022-01-05") == 123.0e9
    assert shares_at(SERIES, [], "2019-01-01") is None
    assert shares_at(SERIES, [], "2024-01-01") == 124.0e9

def test_override_multiplies():
    assert shares_at(SERIES, OVR, "2022-06-11") == 246.0e9   # 123e9 * 2 after event
    assert shares_at(SERIES, OVR, "2022-01-05") == 123.0e9   # unaffected before
```

- [ ] **Step 2: Run → FAIL. Step 3: Implement**

```python
import json


def shares_at(series, overrides, d):
    """Latest implied share count on/before date d, times cumulative CA mults."""
    best = None
    for sd, s in series:
        if sd <= d:
            best = s
    if best is None:
        return None
    for o in sorted(overrides, key=lambda o: str(o.get("date", ""))):
        if str(o.get("date", "")) <= d:
            best *= float(o["mult"])
    return best


def implied_shares_series(con, code, current_shares=None):
    """[(date_iso, shares)] ascending, implied = equity / bvps per filing.
    Falls back to a single current-shares anchor when nothing derivable."""
    out = []
    for r in con.execute(
        "SELECT period_end, equity, raw_json FROM fundamentals "
        "WHERE code=? AND equity IS NOT NULL ORDER BY period_end", (code,)):
        summ = (json.loads(r["raw_json"] or "{}").get("summary") or {})
        bvps = summ.get("bvps")
        try:
            bvps = float(bvps)
        except (TypeError, ValueError):
            bvps = None
        if bvps and bvps > 0:
            out.append((r["period_end"], float(r["equity"]) / bvps))
    if not out and current_shares:
        out = [("1900-01-01", float(current_shares))]
    dedup = {}
    for d, s in out:
        dedup[d] = s
    return sorted(dedup.items())
```

- [ ] **Step 4: Run → PASS. Step 5: Commit**

```bash
git add shares.py tests/test_shares.py && git commit -m "feat: implied share series from filings + ca overrides" && git push
```

---

### Task 6: Multiples builder (TTM alignment)

**Files:**
- Create: `multiples.py`
- Test: `tests/test_multiples.py`

**Interfaces:**
- Consumes: `prices` rows `[(date, close)]` ascending; `fundamentals` rows (parsed columns incl. `period_end`, flow items, stock items); `shares.implied_shares_series` output; `cfg["filing_lag_days"]`; overrides list.
- Produces: `build_multiples(price_rows, frows, shares_series, overrides, filing_lag_days=90) -> list[dict]` — one row per price date: `{date, per_ttm, pbv, ev_ebitda, ps_ttm}` (None where denominator ≤ 0 or data missing).

Algorithm (locked): availability(date) = period_end + lag days. For each price date t pick latest filing with availability ≤ t. TTM flow value = trailing sum of last 4 quarters of that flow item using consecutive filings ordered by period_end (if <4 quarters exist, sum what exists and require ≥2). Stock items (equity/debt/cash) = latest filing's value directly. EBITDA per-quarter preferred; if any of the 4 quarters lacks ebitda but has revenue, mark ebitda unavailable for those quarters (EV/EBITDA None there).

- [ ] **Step 1: Failing golden test**

```python
from multiples import build_multiples

# 2 quarters of filings, simple numbers
FR = [
 {"code":"X","year":2025,"periode":"tw1","period_end":"2025-03-31",
  "revenue":100.0,"net_income":10.0,"equity":400.0,"total_debt":100.0,"cash":50.0,"ebitda":20.0},
 {"code":"X","year":2025,"periode":"tw2","period_end":"2025-06-30",
  "revenue":120.0,"net_income":12.0,"equity":410.0,"total_debt":90.0,"cash":60.0,"ebitda":24.0},
]
SH = [("1900-01-01", 10.0)]

def test_golden_alignment():
    # tw1 available 2025-06-29 (period_end+90d); tw2 available 2025-09-28
    PX3 = [("2025-08-01", 24.4), ("2025-09-15", 30.0), ("2025-10-01", 30.0)]
    rows = build_multiples(PX3, FR, SH, [], filing_lag_days=90)
    aug = next(r for r in rows if r["date"] == "2025-08-01")
    assert aug["per_ttm"] is None            # only tw1 available: TTM needs >=2 quarters
    oct_ = next(r for r in rows if r["date"] == "2025-10-01")
    # NI TTM = 10+12 = 22; shares 10 => EPS 2.2; price 30 => PER 13.63..
    assert abs(oct_["per_ttm"] - 30.0 / (22.0 / 10.0)) < 1e-9
    # EV = mcap 300 + debt 90 - cash 60 = 330; EBITDA TTM = 20+24 = 44
    assert abs(oct_["ev_ebitda"] - 330.0 / 44.0) < 1e-9
    assert abs(oct_["pbv"] - (30.0 * 10.0) / 410.0) < 1e-9

def test_negative_denominator_excluded():
    FRN = [dict(FR[0], net_income=-5.0)]
    rows = build_multiples([("2025-08-01", 24.4)], FRN, SH, [])
    assert rows[0]["per_ttm"] is None
```

- [ ] **Step 2: Run → FAIL. Step 3: Implement**

```python
import bisect, datetime as dt

def _d(s): return dt.date.fromisoformat(s)
def _plus(s, days): return (_d(s) + dt.timedelta(days=days)).isoformat()

def build_multiples(price_rows, frows, shares_series, overrides,
                    filing_lag_days=90):
    fs = [f for f in frows if f.get("period_end")] 
    fs.sort(key=lambda f: f["period_end"])
    avail = [_plus(f["period_end"], filing_lag_days) for f in fs]
    dates = [p[0] for p in price_rows]
    sh_dates = [s[0] for s in shares_series]
    ov = sorted(overrides, key=lambda o: o.get("date",""))
    out = []
    for d, close in price_rows:
        i = bisect.bisect_right(avail, d) - 1
        if i < 0:
            out.append({"date": d, "per_ttm": None, "pbv": None,
                        "ev_ebitda": None, "ps_ttm": None}); continue
        win = fs[max(0, i-3):i+1]                       # up to last 4 filings
        shares = None
        j = bisect.bisect_right(sh_dates, d) - 1
        if j >= 0:
            shares = shares_series[j][1]
            for o in ov:
                if o.get("date","") <= d: shares *= float(o["mult"])
        last = fs[i]
        row = {"date": d, "per_ttm": None, "pbv": None,
               "ev_ebitda": None, "ps_ttm": None}
        if shares and shares > 0:
            ni = _ttm(win, "net_income"); rev = _ttm(win, "revenue")
            ebitda = _ttm(win, "ebitda")
            eq = last.get("equity"); debt = last.get("total_debt"); cash = last.get("cash")
            if ni is not None and ni > 0:
                row["per_ttm"] = close / (ni / shares)
            if eq is not None and eq > 0:
                row["pbv"] = (close * shares) / eq
            if rev and rev > 0:
                row["ps_ttm"] = (close * shares) / rev
            if ebitda and ebitda > 0 and debt is not None and cash is not None:
                ev = close * shares + debt - cash
                row["ev_ebitda"] = ev / ebitda if ev > 0 else None
        out.append(row)
    return out

def _ttm(window, field):
    vals = [w.get(field) for w in window if w.get(field) is not None]
    if len(vals) < 2: return None
    return sum(vals)
```

- [ ] **Step 4: Run → PASS. Step 5: Commit**

```bash
git add multiples.py tests/test_multiples.py && git commit -m "feat: daily ttm multiples builder with filing-lag alignment" && push
```

---

### Task 7: Z-stats (winsorize, rolling fit, streak)

**Files:**
- Create: `zstats.py`
- Test: `tests/test_zstats.py`

**Interfaces:**
- Produces: `winsorize(vals: list[float], lo=0.01, hi=0.99) -> list[float]`; `fit(values: list[float], window: int) -> tuple[mu, sigma, n]` over the LAST `window` observations (σ=population std via statistics.pstdev); `streak(values_with_dates: list[(date,v)], mu, sigma, thr) -> int` — count of consecutive trailing observations with z ≤ thr ending at the last element (0 if last obs fails).

- [ ] **Step 1: Failing tests**

```python
from zstats import winsorize, fit, streak

def test_winsorize_clips_tails():
    vals = [float(i) for i in range(100)] + [1e9]
    w = winsorize(vals, 0.01, 0.99)
    assert max(w) < 1e8 and min(w) >= 0

def test_fit_window():
    mu, sg, n = fit([float(i) for i in range(10)], 5)
    assert n == 5 and abs(mu - 7.0) < 1e-9 and abs(sg - 1.4142135) < 1e-6

def test_streak_counts_trailing():
    ser = [("d1",-1.0),("d2",-2.5),("d3",-3.0),("d4",-0.1)]
    assert streak(ser, 0.0, 1.0, -1.0) == 0          # last obs z=-0.1 fails thr
    ser2 = [("d1",-0.5),("d2",-1.5),("d3",-2.5)]
    assert streak(ser2, 0.0, 1.0, -1.0) == 2
```

- [ ] **Step 2: Run → FAIL. Step 3: Implement**

```python
import statistics

def winsorize(vals, lo=0.01, hi=0.99):
    if not vals: return []
    s = sorted(vals); n = len(s)
    lo_v = s[max(0, int(lo*(n-1)))]; hi_v = s[min(n-1, int(hi*(n-1)))]
    return [min(max(v, lo_v), hi_v) for v in vals]

def fit(values, window):
    v = [x for x in values if x is not None][-window:]
    if len(v) < 2: return (None, None, len(v))
    return (statistics.fmean(v), statistics.pstdev(v), len(v))

def streak(ser, mu, sigma, thr):
    if mu is None or not sigma or not ser: return 0
    n = 0
    for _, v in reversed(ser):
        z = (v - mu) / sigma
        if z <= thr: n += 1
        else: break
    return n
```

- [ ] **Step 4: Run → PASS. Step 5: Commit**

```bash
git add zstats.py tests/test_zstats.py && git commit -m "feat: winsorized rolling z-fit + discount-zone streak" && git push
```

---

### Task 8: compute.py orchestration + eligibility + coverage

**Files:**
- Create: `compute.py`
- Test: `tests/test_compute.py`

**Interfaces:**
- Consumes: all prior modules; config.
- Produces: `compute_all(db_path, cfg) -> dict` — for every universe code with prices: rebuild `multiples` rows (delete+insert per code), write `stats` rows for windows `w3y`/`w5y` on the PRIMARY variable series, evaluate eligibility (≥min_coverage×window non-null primary obs; IDR only; PER-primary requires latest per_ttm present) writing failures into `coverage_issues`, and set `meta.last_compute`. Returns counts `{ok, issues}`. Also `check(db_path, cfg) -> list[str]` selftest asserting invariants (every stats row's n_obs ≥ 2; every multiples row has ≥1 non-null metric; meta.last_compute exists) returning violation strings.

Sector group lookup: `group_of(cfg, code)` = `cfg["sector_map"].get(code, "general")`; primary var = `cfg["groups"][g]["primary"]`; secondary = `["secondary"]`.

- [ ] **Step 1: Failing test (small synthetic db)**

`tests/test_compute.py`:
```python
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
```

- [ ] **Step 2: Run → FAIL**

Run: `python -m pytest tests/test_compute.py -v` — expected FAIL (`No module named 'compute'`).

- [ ] **Step 3: Implement `compute.py`**

```python
import datetime as dt
import json
import sys

from db import connect
from multiples import build_multiples
from shares import implied_shares_series
from zstats import fit

VAR_COLS = {"per": "per_ttm", "pbv": "pbv",
            "ev_ebitda": "ev_ebitda", "ps": "ps_ttm"}


def group_of(cfg, code):
    return cfg.get("sector_map", {}).get(code, "general")


def _merge_anchor(series, con, code):
    """Fold shares_history anchors (written by backfill via idx_shares)."""
    extra = [(r["date"], float(r["listed_shares"])) for r in con.execute(
        "SELECT date, listed_shares FROM shares_history "
        "WHERE code=? AND listed_shares>0 ORDER BY date", (code,))]
    dedup = dict(series)
    for d, s in extra:
        dedup[d] = s
    return sorted(dedup.items())


def compute_all(db_path, cfg):
    con = connect(db_path)
    codes = [r["code"] for r in con.execute("SELECT DISTINCT code FROM prices")]
    con.execute("DELETE FROM coverage_issues")
    ok = 0
    for code in codes:
        pr = [(r["date"], r["close"]) for r in con.execute(
            "SELECT date, close FROM prices WHERE code=? AND close>0 ORDER BY date",
            (code,))]
        fr = [dict(r) for r in con.execute(
            "SELECT * FROM fundamentals WHERE code=? ORDER BY period_end", (code,))]
        series_sh = _merge_anchor(implied_shares_series(con, code), con, code)
        ovr = [o for o in cfg.get("ca_overrides", []) if o.get("code") == code]
        rows = build_multiples(pr, fr, series_sh, ovr, cfg["filing_lag_days"])
        con.execute("DELETE FROM multiples WHERE code=?", (code,))
        con.executemany("INSERT OR REPLACE INTO multiples VALUES(?,?,?,?,?,?)",
            [(code, r["date"], r["per_ttm"], r["pbv"],
              r["ev_ebitda"], r["ps_ttm"]) for r in rows])
        g = group_of(cfg, code)
        prim = VAR_COLS[cfg["groups"][g]["primary"]]
        obs = [(r["date"], r[prim]) for r in rows if r[prim] is not None]
        reasons = []
        if fr and fr[-1].get("currency") not in (None, "IDR"):
            reasons.append("usd")
        for wk, wd in cfg["windows_days"].items():
            mu, sg, n = fit([v for _, v in obs], int(wd))
            con.execute("INSERT OR REPLACE INTO stats VALUES(?,?,?,?,?)",
                        (code, wk, mu, sg, n))
            if n < cfg["min_coverage"] * int(wd):
                reasons.append(f"low_coverage:{wk}")
        if reasons:
            con.execute("INSERT OR REPLACE INTO coverage_issues VALUES(?,?,?,?)",
                        (code, ";".join(reasons),
                         json.dumps({"n_primary_obs": len(obs)}),
                         dt.datetime.now().isoformat(timespec="seconds")))
        else:
            ok += 1
    con.execute("INSERT OR REPLACE INTO meta VALUES('last_compute',?)",
                (dt.datetime.now().isoformat(timespec="seconds"),))
    con.commit(); con.close()
    return {"ok": ok, "issues": len(codes) - ok}


def check(db_path, cfg_unused=None):
    con = connect(db_path, readonly=True)
    bad = []
    for r in con.execute("SELECT code, window, n_obs FROM stats"):
        if r["n_obs"] is None or r["n_obs"] < 2:
            bad.append(f"stats {r['code']}/{r['window']} n={r['n_obs']}")
    if not con.execute(
            "SELECT value FROM meta WHERE key='last_compute'").fetchone():
        bad.append("meta.last_compute missing")
    return bad


if __name__ == "__main__":
    import argparse
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--db", default="data/valz.db")
    a = ap.parse_args()
    from config import load_config
    cfg = load_config(a.config if os.path.exists(a.config) else None)
    print(json.dumps(compute_all(a.db, cfg)))
    violations = check(a.db, cfg)
    for b in violations:
        print("CHECK:", b, file=sys.stderr)
    sys.exit(1 if violations else 0)
```

- [ ] **Step 4: Run → PASS**

Run: `python -m pytest tests/test_compute.py -v`

- [ ] **Step 5: Commit**

```bash
git add compute.py tests/test_compute.py && git commit -m "feat: compute orchestration, eligibility gates, selftest" && git push
```

---

### Task 9: Universe seeder

**Files:**
- Create: `universe.py`
- Test: `tests/test_universe.py`

**Interfaces:**
- Produces: `seed_universe(client, watchlist_dir: str|None) -> list[str]` — union of `idx_fundamentals_screen(period="<current>", limit=500)` codes + `*.md` stems in watchlist dir (skip `_` prefixed); `write_universe(cfg_path, codes)` merging into `universe:` key preserving other content (round-trip via yaml.safe_load/dump).
Current period resolution: try `"2026-tw2"` then fall back one quarter down to `"2025-audit"` until rows ≥ 50 returned (hardcode this ladder in a constant `PERIOD_LADDER`).

- [ ] **Step 1: Failing test** (FakeClient returning fixed rows; tmp watchlist dir with `_x.md`, `ARCI.md`)
Assert result contains screen codes ∪ {"ARCI"} and excludes nothing underscore-prefixed (they never enter).

- [ ] **Step 2: Run → FAIL. Step 3: Implement. Step 4: Run → PASS. Step 5: Commit + push** (`feat: universe seeder from fundamentals_screen + watchlist union`)

---

### Task 10: FastAPI app (read-only endpoints)

**Files:**
- Create: `app.py`
- Test: `tests/test_api.py` (httpx TestClient against fixture db built like Task 8's)

**Interfaces:**
- Consumes: `db.connect(readonly=True)`, config, `zstats.streak` for live streak calc, `group_of` mapping duplicated from compute via import.
- Produces exactly the spec shapes:

`GET /api/screen?window=w5y&sector=&max_z=-1.0` →
```json
{"ok": true, "as_of": "<max(multiples.date)>", "source": "mixed",
 "window": "w5y", "counts": {"ranked": N, "issues": M},
 "rows": [{"code","sector_group","primary_var","value_now","mean","sigma","z",
           "disc_pct","streak_days","roe_ttm","rev_yoy","der","flags":[]}],
 "issues": [{"code","reason"}]}
```
Row construction: join stats(window) ↔ latest multiples row; `z=(value_now-mean)/sigma`; skip if z is None or z > max_z; sort ascending by z. `flags` includes `"usd"` (currency≠IDR), `"low_coverage"` (n_obs < min_coverage×window). roe_ttm/rev_yoy/der computed from the two most recent fundamentals rows (NI_TTM/equity; revenue YoY = last vs same quarter prior year; DER = total_debt/equity) — null-safe.

`GET /api/ticker/{code}?window=w5y` →
```json
{"ok": true, "meta": {"code","sector_group","primary_var","secondary_var"},
 "stats": {"mu","sigma","n_obs"}, "filings": ["2025-03-31", ...],
 "series": [{"date","v","z"}], "source": "mixed", "as_of": "..."}
```
Series = primary-variable multiples history for that code (non-null points only), z recomputed per point against the SAME window μ/σ.

`GET /api/meta` → `{"ok":true,"last_compute","universe_count","coverage":{"ok":N,"issues":M},"version":"0.1.0"}`

Invalid `window` (not in windows_days) or unparsable max_z → HTTP 422 via explicit raise `HTTPException(422)` BEFORE touching the db.

- [ ] **Step 1: Failing contract tests** (assert full JSON key sets for all three endpoints + 422 case + unknown ticker → `{"ok":false,"error":"unknown ticker"}` with 404)
- [ ] **Step 2: Run → FAIL. Step 3: Implement app.py (FastAPI, uvicorn entry `app = create_app()`). Step 4: Run → PASS. Step 5: Commit + push** (`feat: read-only screen/ticker/meta api`)

---

### Task 11: Frontend (table + drawer chart)

**Files:**
- Create: `static/index.html`, `static/vendor/echarts.min.js` (download once: `https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js`, commit vendored)
- Test: `tests/test_static.py` — pytest reads index.html and asserts: contains `id="filter-window"` select with options `w3y|w5y`, `id="threshold"`, table id `tbl`, drawer id `drawer`, canvas container id `chart`, and that fetch paths `/api/screen` & `/api/ticker/` appear verbatim. (Honest static smoke; visual QA is manual.)

Behaviour contract: load → fetch `/api/screen?window=<sel>&max_z=<slider>` → render rows (columns: Code, Group, Primary, Now, Mean, σ, Z, Disc%, Streak, Flags); row click → fetch `/api/ticker/CODE?window=` → open right drawer (width 60%) with ECharts line chart: x=date, y=value; markLine μ; shaded bands ±1σ ±2σ (stacked area trick via three series with `areaStyle` between bounds computed client-side from stats); markPoint on last point; red markAreas for filing dates (2px vertical spans). Dark palette: bg `#0f172a`, panel `#1e293b`, text `#e2e8f0`, accent `#38bdf8`, danger `#f87171`. Header shows `source` + `as_of` (render literal `Tanggal data tidak tersedia` when null). Threshold slider −0.5…−3.0 step 0.25 highlights rows z ≤ threshold with class `deep` (bg tint).

- [ ] **Step 1: Vendor echarts + write index.html (~350 lines vanilla JS/CSS). Step 2: Run pytest static test. Step 3: Manual visual check: `uvicorn app:app --port 8096` + browser localhost. Step 4: Commit + push** (`feat: dark screener ui with valuation band drill-down`)

---

### Task 12: backfill CLI + first real run (3-ticker smoke)

**Files:**
- Create: `backfill.py`
- Test: `tests/test_backfill_cli.py` (arg parsing + dry-run mode calls nothing when universe empty)

CLI contract: `python backfill.py --seed` (universe → config.yaml), `--tickers BBCA,BBRI,ANTM` restrict, `--dry-run` (all writes go to a throwaway temp db, real db untouched), default full universe. Also populates `shares_history` anchors via `idx_shares` (compute's `_merge_anchor` consumes them).

- [ ] **Step 1: Implement `backfill.py`**

```python
import argparse
import datetime as dt
import json
import os
import sys
import tempfile

from config import load_config
from db import connect, init_db
from fundamentals_fetch import backfill_fundamentals
from mcp_client import McpClient
from prices import merge_prices
from universe import seed_universe, write_universe


def _years():
    return list(range(2020, dt.date.today().year + 1))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--tickers", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--db", default="data/valz.db")
    a = ap.parse_args(argv)
    cfg = load_config(a.config if os.path.exists(a.config) else None)
    client = McpClient(os.environ.get("IDX_MCP_URL", "http://localhost:8001/mcp"))

    if a.seed:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        wl = os.path.join(root, "..", "watchlist")   # sibling in saham workspace
        codes = seed_universe(client, wl if os.path.isdir(wl) else None)
        target = a.config if os.path.exists(a.config) else "config.yaml"
        write_universe(target, codes)
        print(json.dumps({"seeded": len(codes)}))
        return 0

    codes = [c.strip().upper() for c in a.tickers.split(",") if c.strip()] \
        or cfg["universe"]
    if not codes:
        raise SystemExit("universe empty - run python backfill.py --seed first")
    dbp = a.db
    if a.dry_run:
        dbp = os.path.join(tempfile.mkdtemp(prefix="valz-dry-"), "dry.db")
    init_db(dbp)
    con = connect(dbp)
    fr = backfill_fundamentals(con, client, cfg, codes, _years())
    n_px = merge_prices(con, cfg, codes)
    sh = 0
    for c in codes:
        try:
            r = client.call("idx_shares", {"code": c})
            ls = r.get("listed_shares")
            if ls:
                con.execute("INSERT OR REPLACE INTO shares_history VALUES(?,?,?,?)",
                            (c, r.get("date") or "1900-01-01", float(ls), "accumulator"))
                sh += 1
        except Exception:
            pass
    con.commit()
    print(json.dumps({"fundamentals": fr,
                      "price_rows_total": sum(v[0] for v in n_px.values()),
                      "shares_anchors": sh,
                      "codes": len(codes),
                      "dry_run": bool(a.dry_run),
                      "db": dbp}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Unit test with fakes**

`tests/test_backfill_cli.py`:
```python
import backfill

class FakeResp:
    def __init__(self): self.n = 0
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
```
Run: `python -m pytest tests/test_backfill_cli.py -v` → PASS (backfill.main must call `merge_prices` through the `prices` module attribute so the monkeypatch above is effective).

⚠️ **Known environment fact (probed 2026-08-25):** `localhost:8001` is REFUSED on the homeserver host — idx-mcp is only reachable at its LAN/Tailscale IP. ALWAYS pass `IDX_MCP_URL` explicitly in every homeserver command; never rely on the code default.

- [ ] **Step 3: REAL smoke on homeserver**

PowerShell on Windows (resolves the homeserver address at runtime from ssh config - never hardcode):

```powershell
$lines = Get-Content "$env:USERPROFILE\.ssh\config"
$idx = [array]::IndexOf($lines, ($lines | Where-Object { $_ -match '^Host homeserver' } | Select-Object -First 1))
$hn = (($lines[($idx+1)..($idx+2)] | Where-Object { $_.Trim() -match '^HostName' } | Select-Object -First 1).Trim() -replace '^HostName\s*','').Trim()
ssh homeserver 'mkdir -p ~/valz'
git push
ssh homeserver "git clone ssh://git@${hn}:2222/gitadmin/valz.git ~/valz/src 2>`$null; if (`$LASTEXITCODE -ne 0) { git -C `$HOME/valz/src pull }"
scp valz/config.yaml homeserver:~/valz/src/config.yaml     # setelah seed lokal
ssh homeserver "cd ~/valz/src && python3 -m pip install --user -q -r requirements-dev.txt && IDX_MCP_URL=http://${hn}:8001/mcp python3 backfill.py --tickers BBCA,BBRI,ANTM --dry-run"
```
Expected: fetched≈72, cached=0, missing small; price_rows_total ≈ 4500. If BBCA missing-count explodes (>20), STOP — probe one failing call manually and fix parser key priorities before proceeding.

- [ ] **Step 4: Full-seed run (background, ~30-60 min):** remove `--dry-run` and `--tickers`, run inside `nohup`, verify progress by re-checking `SELECT COUNT(*) FROM fundamentals` growth. Step 5: `python compute.py` on homeserver → inspect `/api` shapes via curl. Step 6: Commit any fixes made during smoke + push.

---

### Task 13: Docker deploy + cron + README

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `refresh.sh`, `README.md`

Dockerfile:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8096
CMD ["uvicorn", "app:app", "--host", "[IP_ADDRESS]", "--port", "8096"]   # network_mode:host => exposed on all host IPs (LAN + Tailscale)
```
docker-compose.yml:
```yaml
services:
  valz:
    build: .
    container_name: valz
    network_mode: host
    restart: unless-stopped
    volumes:
      - ./data:/app/data
      - ./config.yaml:/app/config.yaml:ro
    environment:
      - IDX_MCP_URL=${IDX_MCP_URL:?set IDX_MCP_URL in .env - localhost refused on host}
      - ARJUM_API_KEY=${ARJUM_API_KEY:-}
```
refresh.sh: price merge for full universe + fundamentals season probe (only periods whose availability window opened since last meta.season_check) + compute_all; log to data/refresh.log.

README.md sections: What/Why (user's original idea paraphrase), runbook (clone → config.yaml from example → seed → backfill → compute → compose up → cron line `5 19 * * 1-5 cd ~/valz/src && ./refresh.sh`), provenance philosophy, ca_overrides how-to.

- [ ] **Step 1: Write all four files. Step 2: Deploy: `ssh homeserver 'cd ~/valz/src && docker compose up -d --build'`. Step 3: Verify `curl http://<homeserver-ip>:8096/api/meta` from LAN + open UI in browser. Step 4: Add crontab line (show user the command; user confirms). Step 5: Final commit + push** (`feat: docker deploy + nightly refresh`)

---

## Self-Review notes (already applied)

- Spec coverage check: methodology (Task 6/7/8), sector mapping (Task 8 via config groups), anti-value-trap context cols (Task 10 roe/rev/der/streak), provenance (Tasks 10/11), error chains (Task 4), testing pyramid (all tasks), deploy/ops (Task 13), out-of-scope respected (no alerts/backtest/auth tasks).
- Type consistency: `fit()` returns 3-tuple everywhere; `build_multiples` consumes `(price_rows, frows, shares_series, overrides)` consistently in Tasks 6→8; API field names match spec section verbatim.
- Known intentional simplifications vs spec (documented, not silent): shares history derives from filing-implied BVPS instead of idx_accum.db listed_shares mount (fewer infra dependencies; ca_overrides compensates around rights issues) — spec's Risk table anticipated this mitigation path.
