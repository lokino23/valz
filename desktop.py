"""valz desktop launcher: single-file Windows entry point.

Behaviour:
- On first run, copy the bundled ``valz.db`` + ``config.yaml`` snapshot into
  ``%LOCALAPPDATA%\\valz\\data\\`` so user data survives reinstalls.
- On every run, if the last compute is older than the most recent trading
  day's close (today, or Friday if today is Sat/Sun), auto-trigger an
  asynchronous background refresh before the user opens the page. The HTTP
  server still starts immediately -- the user can browse stale data while
  the refresh runs.
- Pick a free port starting at 8103 (homeserver uses 8102, so we skip it).
  Scan up to 50 ports, then give up.
- Open the default browser pointed at the chosen URL once the server is
  ready. The browser launch is best-effort and never blocks the server.

This script is what PyInstaller bundles. Importing it does nothing on its
own -- ``if __name__ == "__main__": main()`` is the single entry point.
"""
import datetime as _dt
import os
import shutil
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

# When PyInstaller freezes the app, the bundled snapshot lives next to the
# exe (sys._MEIPASS); in dev, it lives next to this file. Resolve the
# payload once, then never assume cwd.
if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(sys._MEIPASS)
else:
    BUNDLE_DIR = Path(__file__).resolve().parent

APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "valz"
DATA_DIR = APP_DIR / "data"
ENV_FILE = APP_DIR / ".env"           # optional, holds ARJUM_API_KEY
SEED_DB = BUNDLE_DIR / "payload" / "valz.db"
SEED_CFG = BUNDLE_DIR / "payload" / "config.yaml"

PORT_BASE = 8103
PORT_SCAN = 50
HOST = "127.0.0.1"


def user_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def seed_first_run():
    """Copy the bundled snapshot once; preserve user DB if present.

    A user with a real ``valz.db`` is mid-flight; we never overwrite that.
    The snapshot is a convenience for the very first launch only. We
    create the target dir if missing so a first-run on a fresh machine
    with no %LOCALAPPDATA\\valz yet still works.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    target_db = DATA_DIR / "valz.db"
    target_cfg = DATA_DIR / "config.yaml"
    if not target_db.exists() and SEED_DB.exists():
        shutil.copy2(SEED_DB, target_db)
    if not target_cfg.exists() and SEED_CFG.exists():
        shutil.copy2(SEED_CFG, target_cfg)


def load_env_file():
    """Source the optional ``.env`` (KEY=VALUE) into os.environ."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip()
        if not key:                       # skip "=value" or " =value" lines
            continue
        os.environ.setdefault(key, v.strip().strip('"').strip("'"))


def is_port_free(host, port):
    # No SO_REUSEADDR: on Windows, it lets the same process rebind a port
    # it already owns, so is_port_free would falsely report 8103 as free
    # when this process is the one holding it.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port():
    for p in range(PORT_BASE, PORT_BASE + PORT_SCAN):
        if is_port_free(HOST, p):
            return p
    raise RuntimeError(
        f"No free port in [{PORT_BASE}..{PORT_BASE + PORT_SCAN})")


def is_data_stale(db_path, today=None):
    """True if the last_compute timestamp is older than the last trading day.

    Weekend rule: Saturday/Sunday map to the previous Friday so we don't
    kick off a refresh on Sunday that will find no new data anyway. The
    intent is "any day the market actually closed since our last compute".
    ``today`` is injectable for tests; defaults to ``_dt.date.today()``.
    """
    from db import connect                         # local import keeps top slim
    if not db_path.exists():
        return True
    try:
        con = connect(str(db_path), readonly=True)
        try:
            row = con.execute(
                "SELECT value FROM meta WHERE key='last_compute'").fetchone()
        finally:
            con.close()
    except Exception:
        return True
    if not row or not row["value"]:
        return True
    try:
        last = _dt.datetime.fromisoformat(row["value"])
    except ValueError:
        return True
    if today is None:
        today = _dt.date.today()
    # weekday(): Mon=0..Sun=6 -- Saturday(5) and Sunday(6) roll back to Fri
    days_back = 0 if today.weekday() < 5 else (today.weekday() - 4)
    last_trading = today - _dt.timedelta(days=days_back)
    return last.date() < last_trading


def open_browser_when_ready(url, app_state, stop):
    """Poll the HTTP server until it answers, then open the default browser.

    Best-effort: any failure (e.g. headless install) is silently ignored so
    the launcher never wedges on a missing browser.
    """
    import urllib.request
    deadline = time.time() + 8
    while not stop.is_set() and time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/meta", timeout=0.5) as r:
                if r.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.15)
    # fall through on timeout: server is still starting; don't block it


def make_app():
    """ASGI factory invoked by uvicorn with ``factory=True``.

    Built every worker startup so the app sees the just-seeded db_path +
    cfg_path resolved against ``%LOCALAPPDATA%\\valz\\data`` rather than
    the repo-relative defaults that ``app.create_app`` ships with.
    """
    from config import load_config
    from app import create_app
    db_path = DATA_DIR / "valz.db"
    cfg_path = DATA_DIR / "config.yaml"
    cfg = load_config(str(cfg_path) if cfg_path.exists() else None)
    return create_app(db_path=str(db_path), cfg=cfg)


def _install_log_redirection():
    """Windowed PyInstaller exes lose their console; reroute to a log file
    so launcher crashes don't disappear silently into a black box.

    Skipped when stdout already has a real terminal (dev mode).
    """
    if sys.stdout is not None and getattr(sys.stdout, "isatty", lambda: False)():
        return
    log_path = APP_DIR / "valz.log"
    APP_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(log_path, "a", encoding="utf-8")
    sys.stdout = fh
    sys.stderr = fh
    print(f"--- valz desktop launcher started {_dt.datetime.now().isoformat()} ---")


def main():
    _install_log_redirection()
    user_data_dir()
    seed_first_run()
    load_env_file()

    db_path = DATA_DIR / "valz.db"
    cfg_path = DATA_DIR / "config.yaml"
    if not db_path.exists():
        # seed is missing entirely -- create an empty DB so the API can
        # still serve its 404/422 contract instead of crashing
        from db import init_db
        init_db(str(db_path))

    port = pick_port()
    url = f"http://{HOST}:{port}"

    # stale-on-boot auto-refresh: kick off in background, never block the
    # server. If the user is offline the first screen load still works
    # against the seeded snapshot.
    if is_data_stale(db_path):
        try:
            from refresher import Refresher
            from config import load_config
            cfg = load_config(str(cfg_path) if cfg_path.exists() else None)
            Refresher(str(db_path), cfg).start()
        except Exception:
            pass    # best-effort; manual Refresh button is the fallback

    # Use a factory so the running ASGI app sees the seeded db_path, not
    # the relative "data/valz.db" default that app.create_app() assumes.
    cfg_obj = uvicorn.Config(
        "desktop:make_app", host=HOST, port=port,
        log_level="info", access_log=False,
        # disable websockets entirely: the valz API is HTTP-only and
        # pulling in wsproto/websockets inflates the bundle and forces an
        # extra pip dep that we don't otherwise need.
        ws="none",
        lifespan="on",
        factory=True,
    )
    server = uvicorn.Server(cfg_obj)

    stop = threading.Event()
    threading.Thread(target=open_browser_when_ready,
                     args=(url, None, stop), daemon=True).start()
    try:
        server.run()
    except KeyboardInterrupt:
        stop.set()
        server.should_exit = True


if __name__ == "__main__":
    main()
