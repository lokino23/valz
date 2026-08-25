"""valz backfill CLI: seed universe, fetch fundamentals/prices/shares.

Usage:
  python backfill.py --seed
  python backfill.py --tickers BBCA,BBRI,ANTM [--dry-run]
  python backfill.py                # full configured universe

IDX_MCP_URL must point at the LAN/Tailscale address of idx-mcp — the
localhost default is known-REFUSED on the homeserver host.
"""
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
import prices
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
    n_px = prices.merge_prices(con, cfg, codes)   # module attr: monkeypatchable
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
