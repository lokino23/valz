import datetime as dt, json

# Field-mapping priority lists, CORRECTED against the real idx-mcp payload
# (tests/fixtures/bbca_2021_audit.json):
# - numbers live in top-level "raw" dict (NOT "summary", which is a verdict
#   string like "PASS: 0 fail, 0 warn dari 1 cek"); lookup order below:
#   raw -> payload top-level -> recomputed (ratios only, last resort).
# - equity prefers equity_parent: the server's own bvps/pbv use parent equity
#   (bvps*shares == equity_parent exactly for BBCA FY2021).
# - total_debt/ebitda/da have no honest source key in this payload shape
#   ("liabilities"/"operating_profit" are semantically wrong) -> stay None.
_PRI = {
    "revenue": ["revenue"],
    "net_income": ["net_income", "laba_rugi", "ni_ttm", "net_profit"],
    "equity": ["equity_parent", "equity", "equity_total", "total_equity", "ekuitas"],
    "total_debt": ["total_debt", "debt", "utang"],
    "cash": ["cash", "kas"],
    "ebitda": ["ebitda", "ebitda_ttm"],
    "da": ["da", "depreciation", "depresiasi"],
}

def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

def _pick(d, keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = _num(d.get(k))
        if v is not None:
            return v
    return None

def parse_fundamentals(p):
    raw = p.get("raw")
    rec = p.get("recomputed")
    cur = str(p.get("currency") or "")
    pe = p.get("period_end") or p.get("periode_end")
    yr = p.get("year")
    if yr is None and isinstance(pe, str) and len(pe) >= 4 and pe[:4].isdigit():
        yr = int(pe[:4])                      # real payloads carry no "year" key
    row = {"code": str(p.get("code", "")).upper(),
           "year": int(yr or 0), "periode": p.get("periode", ""),
           "period_end": pe,
           "currency": "USD" if "USD" in cur.upper() else ("IDR" if cur else None),
           "sector": p.get("sector")}
    for field, keys in _PRI.items():
        row[field] = _pick(raw, keys) or _pick(p, keys) or _pick(rec, keys)
    return row

PERIODES = ("tw1", "tw2", "tw3", "audit")

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
                  (row["code"], row["year"], row["periode"], row["period_end"],
                   row["currency"], row["sector"], row["revenue"], row["net_income"],
                   row["equity"], row["total_debt"], row["cash"], row["ebitda"],
                   row["da"], json.dumps(p, ensure_ascii=False),
                   dt.datetime.now().isoformat(timespec="seconds")))
                con.commit(); got += 1
    return {"fetched": got, "cached": cached, "missing": missing}
