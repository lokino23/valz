"""Background price-refresh + recompute machinery (desktop edition).

The desktop build ships a static fundamentals snapshot (no idx-mcp), so the
only refreshable inputs are prices (public Yahoo) and the derived multiples /
z-stats, recomputed fully offline by ``compute.compute_all`` from local
SQLite. The server exposes POST /api/refresh + GET /api/refresh/status on top
of :class:`Refresher`; ``desktop.py`` reuses the same class for its
stale-on-boot auto refresh.

Contract:
- one refresh at a time per Refresher instance; a second ``start()`` while
  running returns False and spawns nothing;
- job runs in a daemon thread; results/errors land in ``state`` which is only
  ever mutated by that thread plus the short critical section under the lock;
- ``snapshot()`` returns a shallow copy safe to serialize as JSON.
"""
import datetime as dt
import threading

import prices
from compute import compute_all
from db import connect

STATE_KEYS = {"running", "started_at", "finished_at", "error", "result"}


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def make_state():
    return {"running": False, "started_at": None, "finished_at": None,
            "error": None, "result": None}


def job_default(db_path, cfg):
    """Canonical desktop refresh: merge Yahoo prices, then offline compute.

    Module-level so tests can monkeypatch ``prices.merge_prices`` /
    ``compute.compute_all`` and stay network-free.
    """
    con = connect(db_path)
    try:
        merged = prices.merge_prices(con, cfg, cfg["universe"])
    finally:
        con.close()
    return {"prices": {k: list(v) for k, v in merged.items()},
            "compute": compute_all(db_path, cfg)}


class Refresher:
    """Owns the single-flight refresh state for one app/db pair."""

    def __init__(self, db_path, cfg, job=None):
        self.db_path = db_path
        self.cfg = cfg
        self.job = job or job_default
        self._lock = threading.Lock()
        self.state = make_state()

    def snapshot(self):
        with self._lock:
            return dict(self.state)

    def start(self):
        """Spawn the refresh thread; False when one is already running."""
        with self._lock:
            if self.state["running"]:
                return False
            self.state.update(running=True, started_at=_now(),
                              error=None, result=None)
        threading.Thread(target=self._run, daemon=True,
                         name="valz-refresh").start()
        return True

    def _run(self):
        try:
            result = self.job(self.db_path, self.cfg)
            with self._lock:
                self.state["result"] = result
                self.state["finished_at"] = _now()
                self.state["running"] = False
        except Exception as e:  # surface, never crash the thread silently
            with self._lock:
                self.state["error"] = f"{type(e).__name__}: {e}"
                self.state["finished_at"] = _now()
                self.state["running"] = False
