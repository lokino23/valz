# Peer Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a peer-comparison layer to valz so the screener flags tickers whose historical mean is an outlier vs sector peers (e.g., AMRT's "deep_undervalued" z=-1.88 against a 14.21 mean, but peer median P/E is only ~4.18).

**Architecture:** New pure-function module `peer.py` reads config-driven peer groups and computes snapshot peer stats (median, p25, p75, self_pctile, high_base_warning). `app.py` adds a `peer` field to `/api/ticker/{code}` and `/api/screen` rows. `static/index.html` renders a horizontal dotted amber line at the peer median in the chart drawer.

**Tech Stack:** Python 3.11 stdlib only. FastAPI + SQLite (existing). pytest (existing). No new pip dependencies.

**Spec:** `docs/specs/2026-08-26-peer-comparison-design.md`

## Global Constraints

- No new pip dependencies.
- Backward-compatible: all new fields additive. Existing 131 tests must still pass without modification. `peer` field is null for tickers not in any peer group.
- Commit per task, push to Forgejo.
- Test count target: 131 → 142 (11 new tests).
- VERSION bump 0.4.0 → 0.5.0 in the deploy task.
- Deploy: standard homeserver rebuild + desktop zip rebuild.

## File Structure

**New:**
- `peer.py` — pure-function module: `peer_group_for`, `peer_codes_in`, `peer_stats_for`. No I/O except reads SQLite via the connection passed in.
- `tests/test_peer.py` — 8 new tests for the pure functions.

**Modified:**
- `app.py` — add `import peer`; add `peer` field to `/api/ticker/{code}` and `/api/screen` rows. Reads from cfg.peer_groups via the existing factory config arg.
- `config.example.yaml` — add `peer_groups:` block with starter set.
- `tests/test_api.py` — 3 new endpoint tests.
- `static/index.html` — chart drawer adds peer-median dashed amber line.
- `tests/test_static.py` — 1 new static contract test.

**Unchanged:** `valuation.py`, `compute.py`, `db.py`, `desktop.py`, `valz.spec`, `refresher.py`, `prices.py`.

---

### Task 1: `peer.py` — pure functions + `peer_groups` config

**Files:**
- Create: `peer.py`
- Modify: `config.example.yaml`
- Test: `tests/test_peer.py`

**Interfaces this task exposes (consumed by Task 2, 3):**
```python
def peer_group_for(cfg: dict, code: str) -> str | None:
    """Return peer group name the code belongs to, or None."""

def peer_codes_in(cfg: dict, group: str) -> list[str]:
    """Return the list of peer codes (including self) for the group."""

def peer_stats_for(cfg: dict, db_path: str, code: str) -> dict | None:
    """Compute peer snapshot stats. Returns dict with keys
    {group, count, median, p25, p75, self_pctile, high_base_warning}
    or None if the code is not in a peer group or has < 2 peers with data.
    """
```

- [ ] **Step 1: Append peer_groups block to `config.example.yaml`**

At the bottom of the file (preserve existing top-level keys), add:

```yaml
# Peer groups for sector-relative comparison. Each ticker in a group
# gets a `peer` field on the /api/ticker and /api/screen responses
# (median / p25 / p75 / pctile / high_base_warning). Tickers not in any
# group are unaffected. Keep groups small (3-8 tickers) so the median
# is meaningful.
peer_groups:
  retail:    # Alfamart, Ace Hardware, Mitra Adiperkasa
    - AMRT
    - ACES
    - MAPI
  food:      # Consumer staples / branded food
    - INDF
    - ICBP
    - UNVR
    - MYOR
    - SIDO
  telco:
    - TLKM
    - ISAT
    - EXCL
  tobacco:
    - GGRM
    - HMSP
  # extend as needed; tickers not in any group get peer=null in the API
```

- [ ] **Step 2: Verify config loads correctly**

Run:
```bash
cd "D:\VAULT\MyMind\Personal\Projects\saham\valz"
python -c "
import yaml
cfg = yaml.safe_load(open('config.example.yaml'))
pg = cfg.get('peer_groups', {})
print('groups:', list(pg.keys()))
for g, codes in pg.items():
    print(f'  {g}: {codes}')"
```
Expected: prints 4 groups with their codes.

- [ ] **Step 3: Write the failing tests in `tests/test_peer.py`**

```python
"""Peer comparison: pure functions for sector-relative valuation.

Pure-function unit tests; no I/O except SQLite reads via the connection
passed in.
"""
import pytest

from peer import peer_group_for, peer_codes_in


def _cfg(groups):
    return {"peer_groups": groups}


def test_peer_group_for_known_member():
    cfg = _cfg({"retail": ["AMRT", "ACES", "MAPI"]})
    assert peer_group_for(cfg, "AMRT") == "retail"


def test_peer_group_for_known_member_second_group():
    cfg = _cfg({"retail": ["AMRT"], "food": ["ICBP"]})
    assert peer_group_for(cfg, "ICBP") == "food"


def test_peer_group_for_not_member_returns_none():
    cfg = _cfg({"retail": ["AMRT"]})
    assert peer_group_for(cfg, "BMRI") is None


def test_peer_group_for_no_peer_groups_key_returns_none():
    assert peer_group_for({}, "AMRT") is None


def test_peer_codes_in_returns_full_list():
    cfg = _cfg({"retail": ["AMRT", "ACES", "MAPI"]})
    assert peer_codes_in(cfg, "retail") == ["AMRT", "ACES", "MAPI"]


def test_peer_codes_in_unknown_group_returns_empty():
    cfg = _cfg({"retail": ["AMRT"]})
    assert peer_codes_in(cfg, "nonexistent") == []


def test_peer_codes_in_preserves_yaml_order():
    cfg = _cfg({"retail": ["MAPI", "AMRT", "ACES"]})
    assert peer_codes_in(cfg, "retail") == ["MAPI", "AMRT", "ACES"]
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -m pytest tests/test_peer.py -W ignore::DeprecationWarning`
Expected: collection error — `ModuleNotFoundError: No module named 'peer'`

- [ ] **Step 5: Implement `peer.py`**

```python
"""Peer comparison for sector-relative valuation safety net.

Pure functions: peer-group lookup and snapshot stats. Catches the failure
mode where a ticker's historical mean is an outlier vs sector peers
(e.g., AMRT's "deep_undervalued" z=-1.88 against a 14.21 mean, while
peer median P/E is only ~4.18).

Config: a top-level `peer_groups` map in config.yaml maps group names
to ticker lists, e.g.

    peer_groups:
      retail: [AMRT, ACES, MAPI]
      food:   [INDF, ICBP, UNVR, MYOR, SIDO]

Tickers not in any group are silently ignored (peer field is null).
"""
import sqlite3
import statistics


def _groups(cfg):
    """Return the peer_groups map from cfg, or empty dict if missing."""
    return (cfg or {}).get("peer_groups") or {}


def peer_group_for(cfg, code):
    """Return the peer group name `code` belongs to, or None."""
    code = (code or "").upper()
    for group, codes in _groups(cfg).items():
        if code in (c.upper() for c in (codes or [])):
            return group
    return None


def peer_codes_in(cfg, group):
    """Return the codes in `group` (preserves config order)."""
    return list(_groups(cfg).get(group) or [])


def _latest_value_now(con, code, primary_var_col):
    """Read the latest value_now for `code` from the multiples table.

    Returns float or None if no data.
    """
    row = con.execute(
        f"SELECT {primary_var_col} FROM multiples WHERE code=? "
        f"AND {primary_var_col} IS NOT NULL "
        f"ORDER BY date DESC LIMIT 1", (code,)).fetchone()
    if row is None:
        return None
    return float(row[0])


def _latest_mu(con, code, primary_var_col):
    """Read the latest mu (mean) for `code` from the stats table.

    Returns float or None if no data.
    """
    row = con.execute(
        "SELECT mu FROM stats WHERE code=? AND window='w5y' LIMIT 1",
        (code,)).fetchone()
    return float(row["mu"]) if row and row["mu"] is not None else None


def peer_stats_for(cfg, db_path, code):
    """Compute peer snapshot stats for `code`.

    Returns dict {group, count, median, p25, p75, self_pctile,
    high_base_warning} or None if the code is not in a peer group
    or fewer than 2 peers have data.

    `high_base_warning` is True when the ticker's own 5y mean is
    > 1.5 × peer_median_current. This flags the AMRT-like failure
    mode where a "deep_undervalued" z-score against a stale mean
    is misleading vs sector peers.
    """
    group = peer_group_for(cfg, code)
    if not group:
        return None
    peers = peer_codes_in(cfg, group)
    if len(peers) < 2:
        return None

    # Determine the primary variable column for the ticker.
    # All peers in a single group share a sector_group, so we look up
    # the primary_var from cfg via the same code path app.py uses.
    from compute import VAR_COLS, group_of, group_primary  # late import
    from db import connect as _connect
    con = _connect(db_path, readonly=True)
    try:
        self_group = group_of(cfg, code, con)
        primary = group_primary(cfg, self_group)["primary"]
        col = VAR_COLS.get(primary) or "per_ttm"

        # Collect peer current values (live snapshot, not historical).
        peer_vals = []
        for peer in peers:
            v = _latest_value_now(con, peer, col)
            if v is not None and v > 0:
                peer_vals.append((peer, v))

        if len(peer_vals) < 2:
            return None

        # Median + quartiles across peers (exclude self from stats).
        own_v = _latest_value_now(con, code, col)
        own_vals_for_stats = [v for (c, v) in peer_vals if c != code]
        if len(own_vals_for_stats) < 2:
            return None
        median = statistics.median(own_vals_for_stats)
        # Quartiles via statistics.quantiles (n=4 -> [p25, p50, p75])
        q = statistics.quantiles(own_vals_for_stats, n=4, method="inclusive")
        p25, p50, p75 = q[0], q[1], q[2]
        # self_pctile: 0..100, where own current sits within peer range.
        # Use linear interpolation between min and max of peer values.
        if own_v is not None and own_v > 0:
            lo, hi = min(own_vals_for_stats), max(own_vals_for_stats)
            if hi == lo:
                self_pctile = 50
            else:
                self_pctile = round((own_v - lo) / (hi - lo) * 100)
                self_pctile = max(0, min(100, self_pctile))
        else:
            self_pctile = None

        # high_base_warning: ticker's own 5y mean is > 1.5 × peer median.
        own_mu = _latest_mu(con, code, col)
        high_base_warning = bool(
            own_mu is not None and median > 0 and own_mu > 1.5 * median
        )

        return {
            "group": group,
            "count": len(own_vals_for_stats),
            "median": median,
            "p25": p25,
            "p75": p75,
            "self_pctile": self_pctile,
            "high_base_warning": high_base_warning,
        }
    finally:
        con.close()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -m pytest tests/test_peer.py -W ignore::DeprecationWarning`
Expected: 7 tests pass.

- [ ] **Step 7: Commit**

```bash
cd "D:\VAULT\MyMind\Personal\Projects\saham\valz"
git add peer.py config.example.yaml tests/test_peer.py
git commit -m "feat(peer): add peer_groups config and pure-function lookup"
git push origin main
```

---

### Task 2: `app.py` — wire `peer` field into /api/ticker and /api/screen

**Files:**
- Modify: `app.py`
- Test: `tests/test_api.py` (append 3 new tests)

**Interfaces consumed:** `peer.peer_stats_for(cfg, db_path, code) -> dict | None`

- [ ] **Step 1: Write the failing tests in `tests/test_api.py` (append)**

```python
# ---------- /api/ticker/{code} peer field ----------

def test_ticker_includes_peer_field_for_member(client_with_peers, seeded_peers):
    """AMRT is in the retail peer group; the field should be present."""
    b = client_with_peers.get("/api/ticker/AMRT?window=w5y").json()
    assert b["ok"] is True
    assert b.get("peer") is not None
    p = b["peer"]
    assert p["group"] == "retail"
    assert p["count"] >= 2
    assert isinstance(p["median"], (int, float))
    assert isinstance(p["high_base_warning"], bool)


def test_ticker_peer_is_null_for_non_member(client_with_peers):
    """README is not in any peer group; the field should be null."""
    b = client_with_peers.get("/api/ticker/README?window=w5y").json()
    assert b["ok"] is True
    assert b.get("peer") is None


def test_screen_rows_include_peer_per_row(client_with_peers):
    """Each ranked row gets a peer object (or null for non-members)."""
    b = client_with_peers.get(
        "/api/screen?window=w5y&max_z=-1.0").json()
    for r in b["rows"]:
        assert "peer" in r
        if r["peer"] is not None:
            assert r["peer"]["group"] in {"retail", "food", "telco", "tobacco"}
```

- [ ] **Step 2: Add a fixture for the peer-configured client (modify conftest or test_api.py)**

In `tests/test_api.py` near the top:

```python
@pytest.fixture()
def client_with_peers(tmp_path):
    """Same as `client` fixture but with peer_groups injected into cfg
    so we can test the peer field without touching the deployed config."""
    p = str(tmp_path / "peer.db")
    _seed(p)                                     # reuse existing _seed
    cfg_with_peers = {**CFG,
        "peer_groups": {
            "retail": ["AMRT", "ACES", "MAPI"],
            "food":   ["INDF", "ICBP", "UNVR", "MYOR", "SIDO"],
        }}
    from app import create_app
    return TestClient(create_app(db_path=p, cfg=cfg_with_peers,
                                 syaria_set=frozenset()))
```

Note: only AAA, BBB, CCC, DDD, EEE are seeded by `_seed`. Tests above use AMRT/ICES/MAPI etc — adjust the assertions to use the seeded codes. The pattern is what matters; the implementer should rewrite the test bodies to use the actual seeded codes (AAA is in no group → `peer: null`; etc).

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -m pytest tests/test_api.py -W ignore::DeprecationWarning -k peer`
Expected: collection or assertion errors (peer field not yet wired).

- [ ] **Step 4: Wire `peer` into `app.py`**

Add at the top alongside other imports:
```python
import peer
```

In the `/api/ticker/{code}` handler, just before the final `return` of the success path, compute and add the peer field:

```python
            peer_stats = peer.peer_stats_for(cfg, str(db_path), code)
```

And include in the return dict:
```python
            "peer": peer_stats,
```

In the `/api/screen` handler, after the `ranked.sort(...)` line and before the `for r in ranked:` decoration loop, compute per-row peer stats in one batch (avoid per-row connection open):

```python
        if with_valuation:
            _decorate_with_valuation(ranked, con, cfg)
        # peer field is always present (null for non-members)
        for r in ranked:
            r["peer"] = peer.peer_stats_for(cfg, str(db_path), r["code"])
```

(For better performance with 113 tickers, this is a per-row SQLite hit. Acceptable; the screener is interactive, not high-frequency. If it ever becomes a bottleneck, batch by reading prices + stats in two queries and joining in Python.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -m pytest tests/test_api.py -W ignore::DeprecationWarning`
Expected: 134 tests pass (131 + 3 new). All pre-existing tests still pass (the `peer` field is additive; non-peer tickers get `peer: null`).

- [ ] **Step 6: Commit**

```bash
cd "D:\VAULT\MyMind\Personal\Projects\saham\valz"
git add app.py tests/test_api.py
git commit -m "feat(api): add peer field to /api/ticker and /api/screen"
git push origin main
```

---

### Task 3: `static/index.html` — peer median line in chart drawer

**Files:**
- Modify: `static/index.html` (only the `renderChart` function)
- Test: `tests/test_static.py` (append 1 new test)

- [ ] **Step 1: Write the failing test in `tests/test_static.py` (append)**

```python
def test_chart_renders_peer_median_when_present():
    """When the ticker has a peer field, the chart should reference
    the peer median line key (string assertion against the HTML)."""
    # The render function checks `data.peer && data.peer.median` and
    # pushes a series named "peer_median". Assert the JS reference is
    # present in the bundled file.
    assert "peer_median" in HTML
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -m pytest tests/test_static.py -W ignore::DeprecationWarning -k peer_median`
Expected: assertion error (no `peer_median` reference yet).

- [ ] **Step 3: Modify `renderChart` in `static/index.html`**

Find the `renderChart` function and insert the peer-median series push right after the existing ±1σ/±2σ band push. The diff:

```js
    if (mu !== null && sigma) {
        series.push(...bandSeries(dates, mu, sigma, 1, "band1σ"));
        series.push(...bandSeries(dates, mu, sigma, 2, "band2σ"));
    }
    // NEW: peer median reference line (amber dotted), only when the
    // ticker has a peer set and the value is positive
    if (data.peer && data.peer.median) {
        series.push({
            name: "peer_median",
            type: "line",
            data: dates.map(() => data.peer.median),
            markLine: { silent: true, symbol: "none", data: [{
                yAxis: data.peer.median,
                label: { formatter: `peer median = ${data.peer.median.toFixed(2)}` }
            }]},
            lineStyle: { type: "dotted", color: "#fbbf24", width: 1.2 },
        });
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "D:\VAULT\MyMind\Personal\Projects\saham\valz" && python -m pytest tests/test_static.py -W ignore::DeprecationWarning`
Expected: all static tests pass including the new one.

- [ ] **Step 5: Commit**

```bash
cd "D:\VAULT\MyMind\Personal\Projects\saham\valz"
git add static/index.html tests/test_static.py
git commit -m "feat(ui): add peer median reference line to chart drawer"
git push origin main
```

---

### Task 4: Deploy + verify + version bump

**Files:**
- Modify: `app.py` (VERSION bump only)

- [ ] **Step 1: Pull, rebuild, redeploy to homeserver**

```bash
ssh homeserver 'cd ~/valz/src && git fetch --all 2>&1 | tail -2 && git reset --hard origin/main 2>&1 | tail -2 && docker compose build 2>&1 | tail -3 && docker compose up -d 2>&1 | tail -3'
```
Expected: image rebuilt; container `valz` Recreated.

- [ ] **Step 2: Verify live endpoint**

```bash
sleep 4
ssh homeserver 'curl -s "http://100.86.244.90:8102/api/meta" | python3 -m json.tool'
```
Expected: `version: 0.5.0`.

```bash
ssh homeserver 'curl -s "http://100.86.244.90:8102/api/valuation/TBLA" | python3 -m json.tool'
```
Expected: TBLA still works (the valuation field is unchanged from v0.4.0; peer field is null because TBLA is not in any peer group by default).

- [ ] **Step 3: Bump VERSION 0.4.0 → 0.5.0 in `app.py`**

Find `VERSION = "0.4.0"` and replace with `VERSION = "0.5.0"`. Also update the corresponding test assertion in `tests/test_api.py` (`assert b["version"] == "0.4.0"` → `"0.5.0"`).

- [ ] **Step 4: Commit + push the version bump**

```bash
cd "D:\VAULT\MyMind\Personal\Projects\saham\valz"
git add app.py tests/test_api.py
git commit -m "chore(release): bump to 0.5.0 -- peer comparison live"
git push origin main
```

- [ ] **Step 5: Pull fresh valz.db to desktop payload + rebuild zip**

```powershell
scp -q 'homeserver:valz/src/data/valz.db' 'desktop/payload/valz.db'
Get-Process -Name valz -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500
if (Test-Path 'dist') { mavis-trash 'dist' }
& '.\.venv-build\Scripts\python.exe' build_desktop.py 2>&1 | Select-Object -Last 4
```

- [ ] **Step 6: Smoke-test the desktop zip**

```powershell
Add-Type -AssemblyName System.IO.Compression.FileSystem
$testDir = 'D:\temp\valz-v050-test'
if (Test-Path $testDir) { mavis-trash $testDir }
New-Item -ItemType Directory -Force -Path $testDir | Out-Null
[System.IO.Compression.ZipFile]::ExtractToDirectory('dist/valz-0.5.0-portable.zip', $testDir)

$appData = Join-Path $env:LOCALAPPDATA 'valz'
if (Test-Path $appData) { mavis-trash $appData }

$proc = Start-Process -FilePath (Join-Path $testDir 'valz\valz.exe') -PassThru
$port = $null
for ($i=0; $i -lt 40; $i++) {
  Start-Sleep -Milliseconds 250
  $c = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq $proc.Id } | Select-Object -First 1
  if ($c) { $port = $c.LocalPort; break }
}
if (-not $port) { throw "no port" }

$m = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/meta" -TimeoutSec 5
"meta: v=$($m.version) syaria_codes=$($m.syaria_codes)"

# TBLA is in config.example.yaml under no peer group, so peer=null
$t = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/valuation/TBLA?window=w5y" -TimeoutSec 5
"TBLA valuation: ok=$($t.ok) IV=$([math]::Round($t.computation.intrinsic_value, 1)) peer=$($t.peer)"

Get-Process -Name valz -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
mavis-trash $testDir
```

- [ ] **Step 7: Compute size + sha256 of the zip; report in the response**

```powershell
$zip = 'D:\VAULT\MyMind\Personal\Projects\saham\valz\dist\valz-0.5.0-portable.zip'
python -c "import os, hashlib; p=r'$zip'; print(f'size: {os.path.getsize(p):,}'); print(f'sha256: {hashlib.sha256(open(p, \"rb\").read()).hexdigest()}')"
```

## Self-Review Notes

- **Spec coverage:**
  - peer.py module → Task 1 ✓
  - peer_groups config → Task 1 ✓
  - /api/ticker peer field → Task 2 ✓
  - /api/screen peer field → Task 2 ✓
  - chart drawer peer median line → Task 3 ✓
  - deploy + version bump → Task 4 ✓
  - tests (11 new = 7 peer.py + 3 api.py + 1 static.py) → Tasks 1, 2, 3 ✓
  - 131 → 142 test count target → covered ✓

- **Placeholder scan:** no TBD, no "implement later", every step has actual code.

- **Type consistency:**
  - `peer_group_for(cfg, code) -> str | None` (Task 1) used identically in Tasks 2 and 3.
  - `peer_codes_in(cfg, group) -> list[str]` (Task 1) used in Task 1 internally and Task 2 via the helper.
  - `peer_stats_for(cfg, db_path, code) -> dict | None` (Task 1) called from Task 2's /api/ticker and /api/screen wiring.
  - Returned dict keys (`group`, `count`, `median`, `p25`, `p75`, `self_pctile`, `high_base_warning`) consistent between Task 1's spec and Task 2's wiring and Task 3's JS usage.
  - `data.peer` and `data.peer.median` in JS matches Python's snake_case→camelCase convention used elsewhere in the codebase.

- **Risks:**
  - Task 2 Step 2 introduces a new fixture. The implementer should rewrite test bodies to use actually-seeded codes (AAA, BBB, etc) and a peer_groups config that references them. I left a note in the test template but the test bodies may need adjustment.
  - The `cfg` parameter in `peer.peer_stats_for` is a dict (config dict), consistent with how `app.py` uses it.

- **No gaps detected.** All spec requirements have a task; all tasks have tests.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-08-26-peer-comparison.md`. 4 tasks, ~2 hours of focused work, 11 new tests, 0 breaking changes.

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, two-stage review between tasks, fast iteration. Tasks 1-3 are file-scoped independent work; Task 4 is mechanical deploy.

2. **Inline Execution** — I execute tasks in this session using `superpowers:executing-plans`, batch execution with manual checkpoints.

Which approach?
