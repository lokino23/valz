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
