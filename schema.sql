CREATE TABLE IF NOT EXISTS prices(
  code TEXT NOT NULL, date TEXT NOT NULL, close REAL,
  adj_close REAL, source TEXT, PRIMARY KEY(code,date));
CREATE TABLE IF NOT EXISTS fundamentals(
  code TEXT NOT NULL, year INTEGER NOT NULL, periode TEXT NOT NULL,
  period_end TEXT, currency TEXT, sector TEXT,
  revenue REAL, net_income REAL, equity REAL, total_debt REAL, cash REAL,
  ebitda REAL, da REAL, raw_json TEXT, fetched_at TEXT,
  PRIMARY KEY(code,year,periode));
CREATE TABLE IF NOT EXISTS shares_history(
  code TEXT NOT NULL, date TEXT NOT NULL, listed_shares REAL, source TEXT,
  PRIMARY KEY(code,date));
CREATE TABLE IF NOT EXISTS multiples(
  code TEXT NOT NULL, date TEXT NOT NULL,
  per_ttm REAL, pbv REAL, ev_ebitda REAL, ps_ttm REAL, PRIMARY KEY(code,date));
CREATE TABLE IF NOT EXISTS stats(
  code TEXT NOT NULL, window TEXT NOT NULL,
  mu REAL, sigma REAL, n_obs INTEGER, PRIMARY KEY(code,window));
CREATE TABLE IF NOT EXISTS coverage_issues(
  code TEXT PRIMARY KEY, reason TEXT, detail TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
