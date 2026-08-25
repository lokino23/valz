"""valz read-only API: /api/screen, /api/ticker/{code}, /api/meta (Task 10).

Design notes:
- ``create_app(db_path=None, cfg=None)`` factory; module-level ``app`` is the
  uvicorn entry. Construction never opens sqlite, so request-time validation
  is observable against a nonexistent db file (422-before-db contract).
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

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from compute import VAR_COLS, group_of, group_primary
from config import load_config
from db import connect
from zstats import streak

VERSION = "0.1.0"
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


def create_app(db_path=None, cfg=None):
    if cfg is None:
        # mirror the CLI: honor a repo-local/deployed config.yaml when present
        cfg = load_config("config.yaml" if os.path.exists("config.yaml")
                          else None)
    if db_path is None:
        db_path = "data/valz.db"

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
    def screen(window: str = "w5y", sector: str = "", max_z: str = "-1.0"):
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
                ranked.append({
                    "code": s["code"], "sector_group": grp,
                    "primary_var": primary,
                    "value_now": val, "mean": mu, "sigma": sig, "z": z,
                    "disc_pct": disc,
                    "streak_days": streak(ser, mu, sig, watch),
                    **_ratios(frows), "flags": flags})
            ranked.sort(key=lambda r: r["z"])
            issues = [{"code": r["code"], "reason": r["reason"]} for r in
                      con.execute(
                          "SELECT code, reason FROM coverage_issues"
                          " ORDER BY code")]
            source, as_of = _scope_facts(con, [r["code"] for r in ranked])
            return {"ok": True, "as_of": as_of, "source": source,
                    "window": window,
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
                    "stats": stats_out, "filings": filings, "series": series,
                    "source": source, "as_of": as_of}
        finally:
            con.close()

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
                    "version": VERSION}
        finally:
            con.close()

    # static ui mounted last so /api/* routes keep precedence
    static_dir = pathlib.Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True),
                  name="static")

    return app


app = create_app()
