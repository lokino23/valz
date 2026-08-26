"""Desktop refresh machinery: Refresher single-flight + /api/refresh contract.

Endpoint tests use an injected fake job (no network, no real pipeline);
``job_default`` is covered by monkeypatching ``prices.merge_prices`` /
``compute.compute_all`` so the canonical Yahoo->compute wiring is asserted
without touching the internet.
"""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import create_app
from db import init_db
from refresher import STATE_KEYS, Refresher, job_default

CFG = {
    "universe": ["AAA"],
    "sector_map": {},
    "groups": {"general": {"primary": "per", "secondary": "pbv"}},
    "windows_days": {"w3y": 300, "w5y": 600},
    "min_coverage": 0.8,
    "filing_lag_days": 90,
    "thresholds": {"watch": -1.0, "deep": -2.0},
}


def _wait_state(client, pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get("/api/refresh/status").json()["state"]
        if pred(st):
            return st
        time.sleep(0.02)
    pytest.fail("timeout waiting for refresh state")


@pytest.fixture()
def client(tmp_path):
    p = str(tmp_path / "r.db")
    init_db(p)
    return TestClient(create_app(db_path=p, cfg=CFG))


# ------------------------------------------------------- /api/refresh/status

def test_status_shape_before_any_refresh(client):
    b = client.get("/api/refresh/status").json()
    assert set(b) == {"ok", "state"}
    assert b["ok"] is True
    assert set(b["state"]) == STATE_KEYS
    assert b["state"] == {"running": False, "started_at": None,
                          "finished_at": None, "error": None, "result": None}


# ---------------------------------------------------------- POST /api/refresh

def test_refresh_start_runs_job_and_reports_result(client):
    seen = {}

    def fake_job(db_path, cfg):
        seen["args"] = (db_path, cfg)
        return {"prices": {"AAA": [2, "yahoo"]}, "compute": {"ok": 1}}

    # replace the job on the app-owned refresher before first POST
    ref = client.app.state.refresher
    ref.job = fake_job

    r = client.post("/api/refresh")
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] is True and b["started"] is True

    st = _wait_state(client, lambda s: not s["running"])
    assert st["error"] is None
    assert st["result"] == {"prices": {"AAA": [2, "yahoo"]}, "compute": {"ok": 1}}
    assert st["started_at"] and st["finished_at"]
    # the app-owned refresher was wired with the factory's db_path + cfg
    assert seen["args"][1] == CFG


def test_refresh_second_post_while_running_is_single_flight(tmp_path):
    p = str(tmp_path / "s.db")
    init_db(p)
    gate = threading.Event()
    calls = []

    def blocking_job(db_path, cfg):
        calls.append(db_path)
        gate.wait(timeout=5)

    c = TestClient(create_app(db_path=p, cfg=CFG,
                              refresher=Refresher(p, CFG, job=blocking_job)))
    first = c.post("/api/refresh").json()
    assert first["started"] is True

    second = c.post("/api/refresh")
    assert second.status_code == 200
    assert second.json()["started"] is False      # refused, still running
    assert len(calls) == 1                        # exactly one job spawned

    gate.set()
    _wait_state(c, lambda s: not s["running"])
    assert len(calls) == 1


def test_refresh_error_is_captured_in_state(client):
    def boom(db_path, cfg):
        raise RuntimeError("boom")

    client.app.state.refresher.job = boom
    client.post("/api/refresh")
    st = _wait_state(client, lambda s: not s["running"])
    assert st["running"] is False
    assert st["result"] is None
    assert "RuntimeError" in st["error"] and "boom" in st["error"]
    assert st["finished_at"]


# ------------------------------------------------------------------ job_default

def test_job_default_merges_prices_then_computes_offline(tmp_path, monkeypatch):
    p = str(tmp_path / "j.db")
    init_db(p)
    log = []

    def fake_merge(con, cfg, codes):
        log.append(("merge", codes, hasattr(con, "execute")))
        return {"AAA": (3, "yahoo")}

    def fake_compute(db_path, cfg):
        log.append(("compute", db_path))
        return {"ok": 1, "issues": 0}

    monkeypatch.setattr("refresher.prices.merge_prices", fake_merge)
    monkeypatch.setattr("refresher.compute_all", fake_compute)
    out = job_default(p, CFG)
    assert out == {"prices": {"AAA": [3, "yahoo"]},
                   "compute": {"ok": 1, "issues": 0}}
    # merge gets a live writable connection over the full universe, then
    # compute runs against the same db file
    assert log[0] == ("merge", ["AAA"], True)
    assert log[1] == ("compute", p)
