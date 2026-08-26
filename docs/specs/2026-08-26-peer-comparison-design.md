# Peer comparison for relative-valuation safety net

**Date:** 2026-08-26
**Status:** Approved (informal — "ok adjustment semua" on 2026-08-26)
**Author:** Mavis
**Project:** valz screener v0.4.0 (post-MOS)

## Context

The v0.4.0 MOS feature exposed a real failure mode of the z-score mean-reversion model: a screener flag like "deep_undervalued" can be misleading when the historical mean is itself an outlier relative to sector peers. Concrete example: AMRT has μ=14.21, z=-1.88, P/E=6.77, but peer median is ~4.18 — so AMRT is actually 1.6× the peer median, not "deep_undervalued." The chart drawer visualizes AMRT's own history but offers no peer reference, so the user has no way to catch this in the UI.

The user (finance professional) explicitly approved two adjustments:
1. **Peer reference line on the chart drawer** — so the AMRT-like misreads are visible in the UI.
2. **`high_base_warning` flag in the screener row** — so the false positive is flagged before the user opens the chart.

## Goal

Add a peer-comparison layer to the v0.4.0 screener. Two surfaces:

1. **`/api/ticker/{code}` and `/api/screen` rows** gain peer-context fields when the ticker is in a peer group:
   - `peer_group`: string name of the peer set the ticker belongs to (e.g. `"retail"`)
   - `peer_count`: int, number of peer tickers (excluding self)
   - `peer_median`: float, current-snapshot P/E (or whichever primary var) median across peers
   - `peer_p25`, `peer_p75`: float quartiles
   - `peer_pctile`: int 0-100, where the ticker's current value sits within the peer distribution
   - `high_base_warning`: bool, true when μ > 1.5× peer_median (likely stale mean, regime change risk)

2. **Chart drawer** in `static/index.html` renders a horizontal dashed line at `peer_median` and labels it "peer median = N.NN" so the comparison is visible alongside the ticker's own history.

## Non-Goals (out of scope for v0.5)

- DCF / explicit fair-value bands (different feature, deferred)
- User-editable peer groups via API (config-driven only for v0.5)
- Historical peer distributions (only current-snapshot is exposed)
- PCTILE computed against historical peer mean; v0.5 only does current snapshot
- Per-ticker `sector_map` overrides; only config-driven `peer_groups` is honoured
- Migration of existing API callers (additive fields; old clients ignore them)

## Design

### Configuration

A new top-level `peer_groups` map in `config.example.yaml` (and deployed `config.yaml`):

```yaml
peer_groups:
  retail:    # Alfamart, Ace Hardware, Mitra Adiperkasa
    - AMRT
    - ACES
    - MAPI
  food:      # Indofood, Unilever, Mayora, Sido Muncul
    - INDF
    - ICBP
    - UNVR
    - MYOR
    - SIDO
  tobacco:   # Gudang Garam, HM Sampoerna
    - GGRM
    - HMSP
  telco:      # Telkom, Indosat, XL Axiata
    - TLKM
    - ISAT
    - EXCL
  # ... extend as needed
```

Tickers not in any peer group get no peer fields (nulls). This avoids the fallback of "use all 113 non-bank tickers" which is a meaningless peer set.

### Module: `peer.py`

A new pure-function module carrying three responsibilities:

```python
def peer_group_for(cfg, code) -> str | None:
    """Return the peer group name the code belongs to, or None."""

def peer_codes_in(cfg, group) -> list[str]:
    """Return the list of peer codes (including self) for the group."""

def peer_stats_for(cfg, db_path, code) -> dict | None:
    """Compute peer snapshot stats. Reads value_now per peer from the
    prices table (latest close joined to the latest per-peer primary_var
    multiple). Returns:
        {
            "group": str,
            "count": int,                    # excluding self
            "median": float,
            "p25": float,
            "p75": float,
            "self_pctile": int,             # 0..100, computed by linear interp
            "high_base_warning": bool,     # μ > 1.5 × peer_median
        }
    or None if the code is not in a peer group or has < 2 peers.
    """
```

### API contract

#### `GET /api/ticker/{code}`

When the code is in a peer group, the response gains:
```json
{
    "...": "...",
    "peer": {
        "group": "retail",
        "count": 2,
        "median": 4.32,
        "p25": 3.86,
        "p75": 5.31,
        "self_pctile": 80,
        "high_base_warning": true
    }
}
```

`peer` is `null` if the code is not in a peer group.

#### `GET /api/screen`

Each row gains a `peer` field (object or null), same shape as above.

#### Edge cases

- `< 2 peers in the group` (group has only 1 ticker including self) → `peer: null`. The flag needs a minimum of 2 other tickers to be meaningful.
- `value_now` missing for a peer (no current data) → exclude that peer from the median/p25/p75 calculation but still count it as missing in the response (`count` reflects only tickers with data).
- `μ` missing for self → `high_base_warning` returns false (no historical baseline to compare).

### Chart drawer UI

In `static/index.html`, the existing `renderChart(data)` function draws horizontal lines for the mean (`μ`) and the ±1σ/±2σ bands. Add:

```js
if (data.peer && data.peer.median) {
    series.push({
        name: "peer_median",
        type: "line",
        data: dates.map(() => data.peer.median),
        markLine: { data: [{ yAxis: data.peer.median,
                              label: { formatter: `peer median = ${data.peer.median.toFixed(2)}` } }] },
        lineStyle: { type: "dotted", color: "#fbbf24", width: 1.2 },
        silent: true
    });
}
```

`#fbbf24` (amber) chosen to distinguish from the existing white `μ` line.

### Backward compatibility

- All new fields are additive. `peer` may be `null` for tickers not in any group.
- Existing tests (131/131 in v0.4.0) must still pass without modification. The peer field is opt-in; tickers with no peer group simply omit it.
- `config.yaml` deployed values: existing deployments do not have `peer_groups`. Without that key, no peer fields are exposed for any ticker. This is intentional — peer grouping is opt-in, no surprises on existing deployments.

### Testing

A new `tests/test_peer.py` covers:

1. **Pure-function tests** for `peer_group_for` and `peer_codes_in`:
   - code in a group → group name returned
   - code not in any group → `None`
   - group with N tickers → list of N codes

2. **Integration tests** for `peer_stats_for` via a fixture:
   - known peer set with seeded prices → assert exact median, p25, p75, self_pctile, high_base_warning
   - ticker with μ > 1.5× peer_median → `high_base_warning: true`
   - ticker with μ ≤ 1.5× peer_median → `high_base_warning: false`
   - group with < 2 peers → `None`
   - one peer with no current data → excluded from stats, `count` reflects missing

3. **Endpoint tests** in `tests/test_api.py`:
   - `GET /api/ticker/AMRT?window=w5y` → response includes `peer` with group=retail
   - `GET /api/ticker/ZZZZ` → still 404, no peer field
   - `/api/screen` rows for AMRT include `peer: { group, median, ... }`
   - `/api/screen` rows for non-peer tickers (e.g. `README`) include `peer: null`
   - Default response (no `?with_valuation`) still works as before

4. **Static test** in `tests/test_static.py`:
   - `index.html` references the peer median line key (string assertion)

5. **Test count target**: 131 → 142 (11 new tests).

### Migration / deployment

- **No DB migration.** Peer groups are config-only.
- **No schema change to existing endpoints.** All new fields are additive.
- `config.example.yaml` ships with a starter set of peer groups. `config.yaml` deployed copies need to be updated manually (or copy the block from `config.example.yaml`).
- **Backwards-compatible**: deployments without `peer_groups` show no peer field. No regression.
- **Homeserver deploy**: standard pull + rebuild image + recreate container.
- **Desktop bundle**: rebuild the onedir + zip.

## Acceptance criteria

- [ ] `pytest` passes 131 → 142 tests, no regression.
- [ ] `GET /api/ticker/AMRT?window=w5y` includes `peer: { group: "retail", median: ~4.18, high_base_warning: true }`.
- [ ] `GET /api/ticker/ICBP?window=w5y` includes `peer: { group: "food", median: ~3.8 }`.
- [ ] `GET /api/ticker/BMRI?window=w5y` (bank) → no `peer` field or `peer: null` (banks not in any peer group by default).
- [ ] `GET /api/screen` rows include `peer` on a per-row basis (null for non-peer tickers).
- [ ] Chart drawer renders a horizontal dotted amber line at `peer_median` with label "peer median = N.NN".
- [ ] Desktop bundle ships with v0.5.0 zip; smoke-test passes.
- [ ] Homeserver at `:8102` returns the new fields; TBLA still works.
- [ ] No v0.4.0 caller broken: existing tests + desktop bundle still load.

## Open questions for the user

None. The design follows the two adjustments the user explicitly approved.
