"""Capture a trimmed Yahoo chart fixture for tests/fixtures/yahoo_bbca.json.

Fetches BBCA.JK daily 6y from Yahoo, keeps ONLY:
meta.symbol + last-50 timestamp/close/adjclose triples (null closes dropped
so parse_yahoo yields exactly 50 rows).
"""
import datetime as dt, json, os, sys, time
import requests

URL = "https://query1.finance.yahoo.com/v8/finance/chart/BBCA.JK?range=6y&interval=1d"
HEADERS = {"User-Agent": "Mozilla/5.0 (valz/1.0)"}
N = 50


def fetch():
    last = None
    for attempt in range(5):
        try:
            r = requests.get(URL, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            print(f"attempt {attempt + 1} failed: {e}; retrying in 5s", file=sys.stderr)
            time.sleep(5)
    raise SystemExit(f"yahoo capture failed after retries: {last}")


def main():
    doc = fetch()
    res = doc["chart"]["result"][0]
    meta = {"symbol": res["meta"]["symbol"]}
    ts = res["timestamp"]
    q = (res.get("indicators") or {}).get("quote") or [{}]
    close = (q[0].get("close") or [])
    adj_list = ((res.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or close

    # last N indices with a usable value, keeping ts/close/adjclose aligned
    idx = [i for i in range(min(len(ts), len(adj_list))) if i < len(close)
           and adj_list[i] is not None and close[i] is not None][-N:]
    trimmed = {
        "chart": {
            "result": [
                {
                    "meta": meta,
                    "timestamp": [ts[i] for i in idx],
                    "indicators": {
                        "quote": [{"close": [close[i] for i in idx]}],
                        "adjclose": [{"adjclose": [adj_list[i] for i in idx]}],
                    },
                }
            ]
        }
    }
    os.makedirs("tests/fixtures", exist_ok=True)
    with open("tests/fixtures/yahoo_bbca.json", "w", encoding="utf-8") as f:
        json.dump(trimmed, f)

    last_ts = dt.datetime.fromtimestamp(ts[idx[-1]], dt.timezone.utc)
    total = min(len(ts), len(close))
    print(f"symbol={meta['symbol']} kept={len(idx)} "
          f"(nulls_in_source={sum(1 for i in range(total) if close[i] is None or adj_list[i] is None)})")
    print(f"last date={last_ts.date().isoformat()} "
          f"close={close[idx[-1]]} adjclose={adj_list[idx[-1]]}")


if __name__ == "__main__":
    main()
