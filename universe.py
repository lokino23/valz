"""Universe seeder (Task 9).

Builds the ticker universe as the union of:
  1. codes from the idx-mcp tool `idx_fundamentals_screen`, and
  2. `*.md` file stems in the watchlist dir (files starting with `_` skipped).

DEVIATION from brief (fixture-forced, verified against the REAL idx-mcp
server, 2026-08-26): the brief's
`idx_fundamentals_screen(period="<current>", limit=500)` does NOT work —
the real tool has no `period` argument and returns HTTP-OK with **0 rows
silently** if you pass it. The real arguments are Indonesian:

    {"year": <int>, "periode": <"tw1"|"tw2"|"tw3"|"audit">, "limit": N}

Response dict carries {"count", "data_quality_note", "period", "rows",
"sort"}; codes are read from res["rows"][i]["code"]. Same class of
correction as Task 3 (real payload shape vs assumed shape).

PERIOD_LADDER uses int years because the real server expects an integer
`year`. The ladder descends until a rung returns >= MIN_CODES codes; that
rung's codes are used (first rung wins, no union across rungs). If no rung
reaches MIN_CODES, the largest result seen is returned so a thin market
still seeds something.
"""
import os

import yaml

TOOL = "idx_fundamentals_screen"
LIMIT = 500
MIN_CODES = 50
PERIOD_LADDER = [(2026, "tw2"), (2026, "tw1"), (2025, "audit")]


def _screen_codes(client):
    """Codes from idx_fundamentals_screen, descending PERIOD_LADDER."""
    best = []
    for year, periode in PERIOD_LADDER:
        res = client.call(TOOL, {"year": year, "periode": periode,
                                 "limit": LIMIT}) or {}
        codes = [str(r["code"]).upper() for r in res.get("rows", [])
                 if r.get("code")]
        if len(codes) >= MIN_CODES:
            return codes
        if len(codes) > len(best):
            best = codes
    return best


def _watchlist_codes(watchlist_dir):
    """Uppercased *.md stems; `_`-prefixed files never enter."""
    if not watchlist_dir or not os.path.isdir(watchlist_dir):
        return []
    return [fn[:-3].upper() for fn in os.listdir(watchlist_dir)
            if fn.endswith(".md") and not fn.startswith("_")]


def seed_universe(client, watchlist_dir=None):
    """Sorted union of screen codes + watchlist stems.

    watchlist_dir None or nonexistent -> screen only.
    """
    return sorted(set(_screen_codes(client))
                  | set(_watchlist_codes(watchlist_dir)))


def write_universe(cfg_path, codes):
    """Set cfg['universe'] = codes via safe_load/safe_dump round-trip,
    preserving all other content. Creates the file if missing."""
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        cfg = {}
    cfg["universe"] = list(codes)
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
