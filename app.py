"""valz API: /api/screen, /api/ticker/{code}, /api/meta, plus the desktop
refresh endpoints POST /api/refresh + GET /api/refresh/status.

Design notes:
- ``create_app(db_path=None, cfg=None, refresher=None, syaria_set=None)``
  factory; module-level ``app`` is the uvicorn entry. Construction never opens
  sqlite, so request-time validation is observable against a nonexistent db
  file (422-before-db contract).
- All endpoints are strictly read-only and null-safe by design: malformed or
  missing data yields nulls / skips, never a 500 from data shape.
- ``source``: distinct non-null ``prices.source`` over the codes represented
  in the response scope; exactly one distinct value -> that value, otherwise
  "mixed" (empty scope included).
- ``as_of``: max(multiples.date) over response-scope codes; absent ->
  "Tanggal data tidak tersedia".
- ``coverage_issues`` are compute-time facts about the run, so /api/screen
  reports them globally regardless of window/sector filters.
"""
import os
import pathlib
import sys

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from compute import VAR_COLS, group_of, group_primary
from config import load_config
from db import connect
from refresher import Refresher
from zstats import streak
import valuation
import peer

VERSION = "0.4.0"

# DES (Daftar Efek Syariah) snapshot loader. The set is injected by the
# factory so tests stay network-free; the on-disk fallback path is used in
# production (homeserver, desktop bundle). A missing or malformed file
# yields an empty set -- syaria-related fields then render as null rather
# than crashing the screen response.
_SYARIA_FILENAMES = ("valz/syaria/des_snapshot.json",
                     "data/des_snapshot.json", "../data/des_snapshot.json",
                     "des_snapshot.json")


def _load_syaria_default():
    """Look in (in order): repo-relative paths, then sys._MEIPASS for
    PyInstaller-bundled exes whose CWD is unrelated to the install dir.
    Returns frozenset or empty frozenset on any failure.
    """
    import json
    candidates = list(_SYARIA_FILENAMES)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "valz/syaria/des_snapshot.json"))
        candidates.append(os.path.join(meipass, "valz", "syaria", "des_snapshot.json"))
    for name in candidates:
        if os.path.exists(name):
            try:
                with open(name, encoding="utf-8") as f:
                    return frozenset((json.load(f) or {}).get("codes") or [])
            except Exception:
                return frozenset()
    return frozenset()


def _syaria_flag(syaria_set, code):
    """True / False / None -- None when the snapshot is empty (caller
    should not infer anything if we never loaded a DES list)."""
    if not syaria_set:
        return None
    return code in syaria_set


def _syaria_filter_ok(syaria_filter, syaria_flag):
    """Apply ?syaria=only|exclude|all on a single row's flag."""
    if syaria_filter == "all":
        return True
    if syaria_filter == "only":
        return syaria_flag is True
    if syaria_filter == "exclude":
        return syaria_flag is False
    return True    # unknown value: pass through (the 422 check catches it)


def _mos_label(mos_pct):
    """Bucket a MOS% value into a human-readable tag.
    Thresholds match Graham's 30%/50% rule of thumb.
    """
    if mos_pct is None:
        return "unknown"
    if mos_pct > 50:
        return "deep_undervalued"
    if mos_pct > 30:
        return "actionable"
    if mos_pct > 0:
        return "modest_discount"
    if mos_pct > -20:
        return "fair"
    return "overvalued"


def _decorate_with_valuation(rows, con, cfg):
    """Add a per-row ``valuation`` field. Reads fundamentals + shares for
    each code, calls into valuation.py. Cheap because rows are
    pre-z-filtered and we share a single connection.
    """
    for r in rows:
        code = r["code"]
        filings = con.execute(
            "SELECT year, periode, period_end, currency, revenue,"
            " net_income FROM fundamentals WHERE code=?"
            " ORDER BY period_end DESC", (code,)).fetchall()
        if not filings:
            r["valuation"] = None
            continue
        if filings[0]["currency"] != "IDR":
            r["valuation"] = None
            continue
        shares_row = con.execute(
            "SELECT listed_shares FROM shares_history"
            " WHERE code=? AND listed_shares>0"
            " ORDER BY date DESC LIMIT 1", (code,)).fetchone()
        shares = float(shares_row["listed_shares"]) if shares_row else 0
        eps = valuation.eps_ttm_from_filings(
            [dict(f) for f in filings], shares)
        if eps["eps_ttm"] is None:
            r["valuation"] = None
            continue
        growth_info = valuation.auto_growth([dict(f) for f in filings])
        g_value = (growth_info["growth"]
                   if growth_info["growth"] is not None else 0.0)
        graham = valuation.compute_graham(
            eps_ttm=eps["eps_ttm"], growth=g_value,
            bond_yield=0.065)         # use config default; endpoint
                                       # already covers overrides
        if graham["graham_value"] is None:
            r["valuation"] = None
            continue
        price_row = con.execute(
            "SELECT close FROM prices WHERE code=?"
            " ORDER BY date DESC LIMIT 1", (code,)).fetchone()
        price = float(price_row["close"]) if price_row else None
        mos_pct = ((graham["graham_value"] - price) / graham["graham_value"] * 100
                   if price else None)
        r["valuation"] = {
            "intrinsic_value": graham["graham_value"],
            "mos_pct": mos_pct,
            "mos_label": _mos_label(mos_pct),
        }


NO_DATA_AS_OF = "Tanggal data tidak tersedia"


def _z(val, mu, sigma):
    if val is None or mu is None or not sigma:
        return None
    return (val - mu) / sigma


def _ratios(frows):
    """roe_ttm / rev_yoy / der from period_end-ordered filings, null-safe.

    roe_ttm = sum(NI of two most recent filings) / latest equity (contract
    proxy); needs >=2 filings with both NI present. rev_yoy compares the
    latest revenue against the same quarter one year earlier. der uses only
    the latest filing.
    """
    out = {"roe_ttm": None, "rev_yoy": None, "der": None}
    if not frows:
        return out
    last = frows[-1]
    eq = last["equity"]
    if len(frows) >= 2 and eq:
        nis = [last["net_income"], frows[-2]["net_income"]]
        if all(n is not None for n in nis):
            out["roe_ttm"] = sum(nis) / eq
    prev = next((r for r in frows if r["year"] == last["year"] - 1
                 and r["periode"] == last["periode"]), None)
    if prev and last["revenue"] is not None and prev["revenue"]:
        out["rev_yoy"] = (last["revenue"] - prev["revenue"]) / prev["revenue"]
    if eq and last["total_debt"] is not None:
        out["der"] = last["total_debt"] / eq
    return out


def create_app(db_path=None, cfg=None, refresher=None, syaria_set=None):
    if cfg is None:
        # mirror the CLI: honor a repo-local/deployed config.yaml when present
        cfg = load_config("config.yaml" if os.path.exists("config.yaml")
                          else None)
    if db_path is None:
        db_path = "data/valz.db"
    # construction never opens sqlite: Refresher only stores path + config
    refresher = refresher or Refresher(db_path, cfg)
    if syaria_set is None:
        syaria_set = _load_syaria_default()

    app = FastAPI(title="valz", version=VERSION)

    def _open():
        return connect(db_path, readonly=True)

    def _window_days(window):
        days = cfg.get("windows_days", {}).get(window)
        if days is None:
            raise HTTPException(422, f"invalid window: {window}")
        return days

    def _max_z(max_z):
        try:
            return float(max_z)
        except (TypeError, ValueError):
            raise HTTPException(422, f"invalid max_z: {max_z}")

    def _fundamentals(con, code):
        return con.execute(
            "SELECT year, periode, period_end, currency, revenue, net_income,"
            " equity, total_debt FROM fundamentals WHERE code=?"
            " ORDER BY period_end", (code,)).fetchall()

    def _scope_facts(con, codes):
        """(source, as_of) derived over the codes visible in the response."""
        if not codes:
            return "mixed", NO_DATA_AS_OF
        qmarks = ",".join("?" * len(codes))
        srcs = [r["source"] for r in con.execute(
            f"SELECT DISTINCT source FROM prices WHERE code IN ({qmarks})"
            " AND source IS NOT NULL", codes)]
        row = con.execute(
            f"SELECT MAX(date) AS d FROM multiples WHERE code IN ({qmarks})",
            codes).fetchone()
        return (srcs[0] if len(srcs) == 1 else "mixed",
                row["d"] or NO_DATA_AS_OF)

    @app.get("/api/screen")
    def screen(window: str = "w5y", sector: str = "",
               max_z: str = "-1.0", syaria: str = "all",
               with_valuation: bool = False):
        if syaria not in ("all", "only", "exclude"):
            raise HTTPException(422, f"invalid syaria: {syaria}")
        # boolean coercion: accept only "true" / "1" / "false" / "0"
        if isinstance(with_valuation, str):
            if with_valuation.lower() not in ("true", "false", "1", "0"):
                raise HTTPException(
                    422, f"invalid with_valuation: {with_valuation!r}")
            with_valuation = with_valuation.lower() in ("true", "1")
        wdays = _window_days(window)
        mz = _max_z(max_z)
        min_cov = cfg.get("min_coverage", 0.8)
        watch = cfg.get("thresholds", {}).get("watch", -2.0)

        con = _open()
        try:
            ranked = []
            for s in con.execute("SELECT * FROM stats WHERE window=?",
                                 (window,)).fetchall():
                grp = group_of(cfg, s["code"], con)
                gcfg = group_primary(cfg, grp)
                primary = gcfg["primary"]
                col = VAR_COLS.get(primary)
                m = None if col is None else con.execute(
                    f"SELECT date, {col} FROM multiples WHERE code=?"
                    " ORDER BY date DESC LIMIT 1", (s["code"],)).fetchone()
                val = m[col] if (m is not None and col is not None) else None
                z = _z(val, s["mu"], s["sigma"])
                # sector filter applies to every considered code, ranked or not
                if sector and grp != sector:
                    continue
                if z is None or z > mz:
                    continue
                frows = _fundamentals(con, s["code"])
                cur = frows[-1]["currency"] if frows else None
                flags = []
                if cur and cur != "IDR":
                    flags.append("usd")
                if s["n_obs"] is not None and s["n_obs"] < min_cov * wdays:
                    flags.append("low_coverage")
                ser = [(r["date"], r[col]) for r in con.execute(
                    f"SELECT date, {col} FROM multiples WHERE code=? AND"
                    f" {col} IS NOT NULL ORDER BY date", (s["code"],))]
                mu, sig = s["mu"], s["sigma"]
                disc = ((val - mu) / mu * 100
                        if val is not None and mu is not None and mu > 0
                        else None)
                sflag = _syaria_flag(syaria_set, s["code"])
                if not _syaria_filter_ok(syaria, sflag):
                    continue
                ranked.append({
                    "code": s["code"], "sector_group": grp,
                    "primary_var": primary,
                    "value_now": val, "mean": mu, "sigma": sig, "z": z,
                    "disc_pct": disc,
                    "streak_days": streak(ser, mu, sig, watch),
                    "syaria": sflag,
                    **_ratios(frows), "flags": flags})
            ranked.sort(key=lambda r: r["z"])
            issues = [{"code": r["code"], "reason": r["reason"]} for r in
                      con.execute(
                          "SELECT code, reason FROM coverage_issues"
                          " ORDER BY code")]
            source, as_of = _scope_facts(con, [r["code"] for r in ranked])
            # peer snapshot stats: null for codes not in any peer_groups
            # (or with < 2 peers with data). Single loop covers both the
            # with_valuation and default return paths.
            for r in ranked:
                r["peer"] = peer.peer_stats_for(cfg, str(db_path), r["code"])

            if with_valuation:
                _decorate_with_valuation(ranked, con, cfg)
                return {"ok": True, "as_of": as_of, "source": source,
                        "window": window, "syaria": syaria,
                        "with_valuation": True,
                        "counts": {"ranked": len(ranked),
                                   "issues": len(issues)},
                        "rows": ranked, "issues": issues}
            return {"ok": True, "as_of": as_of, "source": source,
                    "window": window, "syaria": syaria,
                    "counts": {"ranked": len(ranked), "issues": len(issues)},
                    "rows": ranked, "issues": issues}
        finally:
            con.close()

    @app.get("/api/ticker/{code}")
    def ticker(code: str, window: str = "w5y"):
        _window_days(window)  # validation precedes any db access
        con = _open()
        try:
            known = con.execute(
                "SELECT EXISTS(SELECT 1 FROM stats WHERE code=:c)"
                " OR EXISTS(SELECT 1 FROM multiples WHERE code=:c)"
                " OR EXISTS(SELECT 1 FROM prices WHERE code=:c)"
                " OR EXISTS(SELECT 1 FROM fundamentals WHERE code=:c)",
                {"c": code}).fetchone()[0]
            if not known:
                return JSONResponse(status_code=404, content={
                    "ok": False, "error": "unknown ticker"})
            grp = group_of(cfg, code, con)
            gcfg = group_primary(cfg, grp)
            srow = con.execute(
                "SELECT mu, sigma, n_obs FROM stats WHERE code=? AND window=?",
                (code, window)).fetchone()
            stats_out = {
                "mu": srow["mu"] if srow else None,
                "sigma": srow["sigma"] if srow else None,
                "n_obs": srow["n_obs"] if srow else 0,
            }
            pcol = VAR_COLS.get(gcfg["primary"])
            series = [] if pcol is None else [
                {"date": r["date"], "v": r["v"],
                 "z": _z(r["v"], stats_out["mu"], stats_out["sigma"])}
                for r in con.execute(
                    f"SELECT date, {pcol} AS v FROM multiples WHERE code=?"
                    f" AND {pcol} IS NOT NULL ORDER BY date", (code,))]
            filings = [r[0] for r in con.execute(
                "SELECT DISTINCT period_end FROM fundamentals WHERE code=?"
                " AND period_end IS NOT NULL ORDER BY period_end", (code,))]
            source, as_of = _scope_facts(con, [code])
            return {"ok": True,
                    "meta": {"code": code, "sector_group": grp,
                             "primary_var": gcfg["primary"],
                             "secondary_var": gcfg["secondary"]},
                    "syaria": _syaria_flag(syaria_set, code),
                    "stats": stats_out, "filings": filings, "series": series,
                    "source": source, "as_of": as_of,
                    "peer": peer.peer_stats_for(cfg, str(db_path), code)}
        finally:
            con.close()

    @app.post("/api/refresh")
    def refresh_start():
        # started=False means a refresh is already in flight (still 200:
        # callers just poll /api/refresh/status either way)
        return {"ok": True, "started": refresher.start(),
                "state": refresher.snapshot()}

    @app.get("/api/refresh/status")
    def refresh_status():
        return {"ok": True, "state": refresher.snapshot()}

    @app.get("/api/valuation/{code}")
    def valuation_endpoint(code: str,
                          growth: str = "auto",
                          bond_yield: str = None):
        """Graham-classic intrinsic value + MOS% per ticker.

        See docs/specs/2026-08-26-mos-valuation-design.md for the
        full contract. Returns ``{ok: false, reason: ...}`` instead of
        4xx for valueability skips (negative EPS, USD, etc.) so the
        UI can render a "this ticker isn't valueable here" hint
        without a separate error-handling branch.
        """
        g_value, by_value, g_source = valuation.validate_overrides(
            growth, bond_yield)
        con = _open()
        try:
            filings = con.execute(
                "SELECT year, periode, period_end, currency, revenue,"
                " net_income, equity FROM fundamentals WHERE code=?"
                " ORDER BY period_end DESC", (code,)).fetchall()
            if not filings:
                return JSONResponse(status_code=404, content={
                    "ok": False, "error": "unknown ticker"})
            shares_row = con.execute(
                "SELECT listed_shares FROM shares_history"
                " WHERE code=? AND listed_shares>0"
                " ORDER BY date DESC LIMIT 1", (code,)).fetchone()
            shares = float(shares_row["listed_shares"]) if shares_row else 0
            latest_price_row = con.execute(
                "SELECT date, close FROM prices WHERE code=?"
                " ORDER BY date DESC LIMIT 1", (code,)).fetchone()
            as_of = (str(latest_price_row["date"])
                     if latest_price_row else
                     str(filings[0]["period_end"]))
            current_price = (float(latest_price_row["close"])
                             if latest_price_row else None)
        finally:
            con.close()

        # currency check -- non-IDR tickers need separate handling
        # (FX assumption, etc.) which is v0.5 work. For now: skip.
        currency = filings[0]["currency"] if filings else None
        if currency and currency != "IDR":
            return {"ok": False, "reason": "usd_unsupported",
                    "currency": currency, "code": code}

        eps = valuation.eps_ttm_from_filings(
            [dict(f) for f in filings], shares)
        if eps["eps_ttm"] is None:
            return {"ok": False, "reason": eps["reason"],
                    "filings_used": eps["filings_used"],
                    "code": code}

        if g_value is None:                     # auto
            growth_info = valuation.auto_growth(
                [dict(f) for f in filings])
            g_value = growth_info["growth"] if growth_info["growth"] is not None else 0.0
            g_source = growth_info["source"]
        else:
            growth_info = {"clamped_from": None}

        graham = valuation.compute_graham(
            eps_ttm=eps["eps_ttm"], growth=g_value,
            bond_yield=by_value)
        if graham["graham_value"] is None:
            return {"ok": False, "reason": graham["reason"],
                    "code": code}

        intrinsic = graham["graham_value"]
        if current_price:
            mos_pct = (intrinsic - current_price) / intrinsic * 100
        else:
            mos_pct = None

        return {
            "ok": True,
            "code": code,
            "as_of": as_of,
            "inputs": {
                "eps_ttm": eps["eps_ttm"],
                "eps_method": eps["method"],
                "filings_used": eps["filings_used"],
                "growth": g_value,
                "growth_source": g_source,
                "growth_clamped_from": growth_info.get("clamped_from"),
                "bond_yield": by_value,
                "bond_yield_source": "config_default" if bond_yield is None
                                       else "query",
            },
            "computation": {
                "graham_formula": graham["formula"],
                "graham_value": intrinsic,
                "intrinsic_value": intrinsic,
                "currency": "IDR",
            },
            "result": {
                "current_price": current_price,
                "current_price_date": as_of,
                "mos_pct": mos_pct,
                "mos_label": _mos_label(mos_pct),
            },
            "caveats": [
                "Graham formula assumes stable earnings; cyclical/"
                "distressed names are unreliable.",
                "MOS% > 30 is the Graham 'actionable' threshold; "
                "> 50 is high-conviction.",
            ],
        }

    @app.get("/api/meta")
    def meta():
        con = _open()
        try:
            stats_codes = {r["code"] for r in con.execute(
                "SELECT DISTINCT code FROM stats")}
            issue_codes = {r["code"] for r in con.execute(
                "SELECT code FROM coverage_issues")} & stats_codes
            row = con.execute(
                "SELECT value FROM meta WHERE key='last_compute'").fetchone()
            return {"ok": True,
                    "last_compute": row["value"] if row else None,
                    "universe_count": len(stats_codes),
                    "coverage": {"ok": len(stats_codes) - len(issue_codes),
                                 "issues": len(issue_codes)},
                    "syaria_codes": len(syaria_set),
                    "version": VERSION}
        finally:
            con.close()

    app.state.refresher = refresher

    # static ui mounted last so /api/* routes keep precedence
    static_dir = pathlib.Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True),
                  name="static")

    return app


app = create_app()
