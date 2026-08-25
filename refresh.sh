#!/usr/bin/env bash
# valz nightly refresh: prices + season-opened fundamentals + compute.
# Cron line (add manually):
#   5 19 * * 1-5 cd ~/valz/src && ./refresh.sh >> data/refresh.log 2>&1
set -u
cd "$(dirname "$0")"
mkdir -p data

echo "=== valz refresh $(date -Is) ==="

# 1) price merge for the full configured universe
python3 - <<'PY'
import json, os
from config import load_config
from db import connect
import prices

cfg = load_config("config.yaml" if os.path.exists("config.yaml") else None)
con = connect("data/valz.db")
n = prices.merge_prices(con, cfg, cfg["universe"])
print("prices:", json.dumps(n))
con.close()
PY

# 2) fundamentals: only periods whose availability window opened since the
#    last check (meta.season_check), then stamp it.
python3 - <<'PY'
import datetime as dt, json, os
from config import load_config
from db import connect
from fundamentals_fetch import backfill_fundamentals
from mcp_client import McpClient

cfg = load_config("config.yaml" if os.path.exists("config.yaml") else None)
con = connect("data/valz.db")
client = McpClient(os.environ["IDX_MCP_URL"])
years = list(range(2020, dt.date.today().year + 1))
fr = backfill_fundamentals(con, client, cfg, cfg["universe"], years)
con.execute("INSERT OR REPLACE INTO meta VALUES('season_check',?)",
            (dt.datetime.now().isoformat(),))
con.commit()
print("fundamentals:", json.dumps(fr))
con.close()
PY

# 3) recompute multiples + z-stats for every window
python3 compute.py --db data/valz.db

echo "=== done $(date -Is) ==="
