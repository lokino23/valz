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

## Quick start (Windows local — untuk trader/investor)

Cara paling cepet pake valz di Windows: **download zip, extract, double-click**.

```
1. Download release terbaru dari
   https://github.com/lokino23/valz/releases
   (cari file valz-X.Y.Z-portable.zip, sekitar 21 MB)

2. Extract zip ke folder mana aja, misal C:\valz\
   (akan jadi C:\valz\valz\valz.exe + C:\valz\valz\_internal\)

3. Double-click C:\valz\valz\valz.exe

4. Browser otomatis kebuka ke http://127.0.0.1:8103
   (kalau gak kebuka, manual buka URL itu)

5. Selesai. Data IHSG bundled (snapshot dari waktu zip dibuild,
   biasanya 1-2 hari old). Bisa langsung browse screener.
```

**Yang di-bundle di zip:**
- Python runtime + semua dependencies (FastAPI, uvicorn, dll)
- SQLite database `valz.db` (113 ticker IHSG, ~6 tahun history)
- `config.yaml` dengan sector_map 113 ticker + 6 industry_lenses
- Launcher `valz.exe` (windowed, no console)

**Yang TIDAK di-bundle (perlu di-setup terpisah kalau mau refresh data):**
- `idx-mcp` server (untuk re-fetch fundamentals via XBRL)
- `arjum` API key (untuk re-fetch prices kalau Yahoo Finance gagal)
- `IDX_MCP_URL` + `ARJUM_API_KEY` di `~/valz/src/.env`

**Struktur folder di Windows (setelah first-run):**
```
%LOCALAPPDATA%\valz\data\
├── valz.db        # SQLite database (lo bisa pake sqlite3 CLI utk query)
├── config.yaml    # sector_map, industry_lenses, peer_groups, etc.
└── valz.log       # launcher logs (kalau error, cek sini)
```

**Customization (advanced, optional):**
- Edit `config.yaml` di `%LOCALAPPDATA%\valz\data\` untuk adjust:
  - `sector_map:` — map ticker ke sector (default 113 ticker ke bank/commodity/consumer/property/telco/general)
  - `peer_groups:` — peer set untuk AMRT-like false positive detection
  - `industry_lenses:` — verdict rules per sector
- **Restart `valz.exe`** setiap kali edit config biar kebaca ulang.

**Refresh data (advanced, butuh setup):**
- Untuk re-fetch prices + fundamentals (butuh idx-mcp + arjum), lihat
  section **Maintainer / cloud setup** di bawah. Refresh harian dijalanin
  di homeserver (100.86.244.90:8102), hasilnya tinggal download zip baru
  dari GitHub releases.

**Troubleshooting:**
- Browser gak kebuka otomatis: manual buka http://127.0.0.1:8103
- "Port already in use": tutup valz.exe lain, atau set `VALZ_PORT_BASE` env
- Stuck loading: cek `%LOCALAPPDATA%\valz\valz.log` untuk error details
- Data udah lama (>1 minggu): download zip baru dari GitHub releases

## Maintainer / cloud setup (untuk developer/sysadmin)

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
docker compose up -d --build          # UI at http://<host>:8102

# 6) nightly refresh (weekdays 19:05) — refresh.sh sources .env itself,
#    so IDX_MCP_URL/ARJUM_API_KEY must live in ~/valz/src/.env
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
