# valz — Valuation Z-Score Screener

Ranks LQ45+IDX80 stocks by how far their sector-appropriate valuation
multiple sits below its own history, in standard deviations — then lets you
drill into any ticker to see the full multiple history against its own
μ/σ bands.

## Why

Cheap-vs-its-own-history is a mean-reversion signal that per-stock context
makes honest: a PER of 9 means nothing until you know the same stock spent
five years between 14 and 22. valz fits winsorized rolling stats (3y/5y
windows) per code on the sector's primary variable (banks → PBV, commodity
→ EV/EBITDA, everything else → PER), and ranks the discount.

## Runbook

```bash
# 1) clone + config
git clone ssh://git@<homeserver>:2222/gitadmin/valz.git ~/valz/src
cd ~/valz/src && cp config.example.yaml config.yaml

# 2) seed universe (idx-mcp must be reachable; localhost:8001 is REFUSED
#    on this host — always export the LAN/Tailscale address)
export IDX_MCP_URL=http://<homeserver-lan-ip>:8001/mcp
python3 backfill.py --seed            # writes universe into config.yaml

# 3) backfill history (first run ~30-60 min for 113 codes)
python3 backfill.py                   # add --dry-run to keep db untouched

# 4) compute multiples + z-stats
python3 compute.py

# 5) serve
echo 'IDX_MCP_URL=http://<homeserver-lan-ip>:8001/mcp' > .env
docker compose up -d --build          # UI at http://<host>:8096

# 6) nightly refresh (weekdays 19:05)
crontab -e
#   5 19 * * 1-5 cd ~/valz/src && ./refresh.sh >> data/refresh.log 2>&1
```

## Data provenance

- **Prices**: Yahoo Finance `.JK` primary (~6 years daily); Arjum fallback.
  Every price row keeps its source; the API surfaces it (`source` field,
  `mixed` when rows disagree on origin).
- **Fundamentals**: idx-mcp `idx_fundamentals` XBRL-audited filings,
  cached permanently in sqlite (`raw_json` kept verbatim). A filing is
  considered available 90 days after its period end.
- **Shares**: implied from filings (`equity ÷ BVPS`) as a continuous
  series, anchored by `idx_shares` listed-share counts where fetched;
  corporate actions corrected via `ca_overrides` in config.

## Methodology in one breath

TTM multiples built daily from trailing four-quarter filings (EBITDA
all-or-nothing); z = (value_now − μ)/σ over the window with 1%/99%
winsorization; non-positive denominators excluded; streak counts days
below the watch threshold; ROE/rev-growth/DER are display-only context
(never ranked).

## ca_overrides

Rights issues / stock splits make implied shares jump. Add an entry:

```yaml
ca_overrides:
  - {code: BBRI, date: "2024-06-10", mult: 0.83}
```

`mult` scales shares *before* that date (dilution factor). Verify by
checking the PBV series around the ex-date for a sawtooth — if you see
one, the override is missing or wrong.

## API

- `GET /api/screen?window=w5y&sector=&max_z=-1.0`
- `GET /api/ticker/BBCA?window=w5y`
- `GET /api/meta`

Read-only; validation errors return 422 before touching the database;
unknown tickers return 404.
