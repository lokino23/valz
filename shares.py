"""Implied share-count series from fundamentals filings + CA overrides.

Deviation vs task-5-brief reference code (authorized): in REAL captured
idx_fundamentals payloads (tests/fixtures/bbca_2021_audit.json) "summary" is
a VERDICT STRING ("PASS: 0 fail, 0 warn dari 1 cek"), NOT a numeric dict --
reading summary.bvps would raise AttributeError on .get. Numeric fields live
in nested dicts: balance-sheet items under "raw" (equity_parent, ...),
per-share metrics under "market" (bvps, shares, pbv, ...). So bvps is
resolved with a fundamentals_fetch._pick-style lookup across locations in
order: raw -> payload top-level -> market -> recomputed. The contract is
unchanged: implied = equity / bvps when both present & positive; single
current-shares anchor fallback; dedup by date keeping last; ascending.
"""

import json

# None means the payload's top level itself. "market" is included because
# bvps only exists there in real payloads; "recomputed" stays last resort.
_LOCATIONS = ("raw", None, "market", "recomputed")


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _hint(payload, key):
    """First numeric value for `key` across payload locations, else None.

    Mirrors fundamentals_fetch._pick semantics; non-dict containers (a
    string summary, missing raw/recomputed) are skipped gracefully."""
    if not isinstance(payload, dict):
        return None
    for loc in _LOCATIONS:
        d = payload if loc is None else payload.get(loc)
        if isinstance(d, dict):
            v = _num(d.get(key))
            if v is not None:
                return v
    return None


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
    """[(date_iso, shares)] ascending; implied = equity / bvps per filing.

    bvps comes from the stored full-payload raw_json (never an explicit
    shares field -- division against reported bvps is the cross-checkable
    spec). Rows without a usable bvps/period_end are skipped; when nothing
    is derivable the series falls back to [("1900-01-01", current_shares)].
    """
    out = {}
    for r in con.execute(
        "SELECT period_end, equity, raw_json FROM fundamentals "
        "WHERE code=? AND equity IS NOT NULL ORDER BY period_end", (code,)):
        try:
            payload = json.loads(r["raw_json"] or "{}")
        except ValueError:
            payload = {}
        bvps = _hint(payload, "bvps")
        if bvps and bvps > 0 and r["period_end"]:
            out[r["period_end"]] = float(r["equity"]) / bvps
    if not out and current_shares:
        out = {"1900-01-01": float(current_shares)}
    return sorted(out.items())
