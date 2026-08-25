"""Daily TTM valuation multiples with filing-lag alignment.

For each price date t: filings become available at period_end +
filing_lag_days; the latest available filing is the anchor. TTM flow
items (net_income/revenue/ebitda) sum the trailing window of up to 4
consecutive quarters (require >=2 present); stock items (equity /
total_debt / cash) come from the latest filing directly. Denominators
<= 0 or missing data yield None fields -- never an exception and never
a fabricated multiple.

Deviations vs task-6-brief reference code (authorized):
- Overrides were applied without matching o["code"] -- same defect fixed
  in Task 5's shares_at. build_multiples takes an optional code kwarg;
  when provided, the override list is pre-filtered to entries whose
  o["code"] matches case-insensitively, so a multi-ticker override list
  cannot cross-contaminate another ticker's share count. code=None keeps
  legacy behavior (apply all), matching the brief's golden tests.
- EBITDA TTM is all-or-nothing per the locked algorithm ("if any of the
  4 quarters lacks ebitda but has revenue, mark ebitda unavailable"):
  a filed quarter that reports revenue but no ebitda makes TTM ebitda
  unavailable (EV/EBITDA None). The reference's generic _ttm would have
  summed the remaining partial quarters, understating EBITDA and
  inflating the multiple.
"""

import bisect
import datetime as dt


def _d(s):
    return dt.date.fromisoformat(s)


def _plus(s, days):
    return (_d(s) + dt.timedelta(days=days)).isoformat()


def _ttm(window, field):
    """Trailing sum of a flow item over the filing window (>=2 required)."""
    vals = [w.get(field) for w in window if w.get(field) is not None]
    if len(vals) < 2:
        return None
    return sum(vals)


def _ttm_ebitda(window):
    """EBITDA TTM, all-or-nothing: a quarter that has revenue but lacks
    ebitda marks the whole TTM unavailable rather than partially summed."""
    vals = []
    for w in window:
        e = w.get("ebitda")
        if e is not None:
            vals.append(e)
        elif w.get("revenue") is not None:
            return None
    if len(vals) < 2:
        return None
    return sum(vals)


def build_multiples(price_rows, frows, shares_series, overrides,
                    filing_lag_days=90, code=None):
    """One row per price date: {date, per_ttm, pbv, ev_ebitda, ps_ttm}."""
    fs = sorted((f for f in frows if f.get("period_end")),
                key=lambda f: f["period_end"])
    avail = [_plus(f["period_end"], filing_lag_days) for f in fs]
    sh_dates = [s[0] for s in shares_series]

    # Authorized deviation: scope CA overrides to this ticker when code
    # is given (None applies all -- backward compatible with the brief).
    want = str(code).upper() if code is not None else None
    ov = sorted(
        (o for o in overrides
         if want is None or str(o.get("code", "")).upper() == want),
        key=lambda o: str(o.get("date", "")))

    out = []
    for d, close in price_rows:
        row = {"date": d, "per_ttm": None, "pbv": None,
               "ev_ebitda": None, "ps_ttm": None}
        i = bisect.bisect_right(avail, d) - 1
        j = bisect.bisect_right(sh_dates, d) - 1
        if i >= 0 and j >= 0:
            shares = shares_series[j][1]
            for o in ov:
                if str(o.get("date", "")) <= d:
                    shares *= float(o["mult"])
            if shares > 0:
                win = fs[max(0, i - 3):i + 1]     # trailing <=4 quarters
                last = fs[i]
                ni = _ttm(win, "net_income")
                rev = _ttm(win, "revenue")
                eq = last.get("equity")
                debt = last.get("total_debt")
                cash = last.get("cash")
                if ni is not None and ni > 0:
                    row["per_ttm"] = close / (ni / shares)
                if eq is not None and eq > 0:
                    row["pbv"] = (close * shares) / eq
                if rev is not None and rev > 0:
                    row["ps_ttm"] = (close * shares) / rev
                ebitda = _ttm_ebitda(win)
                if ebitda is not None and ebitda > 0 \
                        and debt is not None and cash is not None:
                    ev = close * shares + debt - cash
                    if ev > 0:
                        row["ev_ebitda"] = ev / ebitda
        out.append(row)
    return out
