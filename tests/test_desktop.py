"""Desktop launcher behaviour: seed-copy, port pick, stale check, env load.

These tests run the launcher helpers without touching the real
``%LOCALAPPDATA%`` -- a tmp_path is injected as both the data dir and the
source of the bundled snapshot. The stale check uses an injectable ``today``
so the weekend-rollback rule is asserted without monkeypatching stdlib.
"""
import datetime as dt
import os
import socket
import sqlite3
from pathlib import Path

import pytest

import desktop

# ----------------------------- monkey-patched locations

@pytest.fixture()
def fake_paths(tmp_path, monkeypatch):
    """Redirect desktop's module-level APP_DIR / DATA_DIR to tmp_path."""
    fake_app = tmp_path / "app"
    fake_app.mkdir()
    monkeypatch.setattr(desktop, "APP_DIR", fake_app)
    monkeypatch.setattr(desktop, "DATA_DIR", fake_app / "data")
    monkeypatch.setattr(desktop, "ENV_FILE", fake_app / ".env")
    return fake_app


# ----------------------------------------------------- seed_first_run

def test_seed_first_run_copies_when_target_missing(fake_paths, tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "valz.db").write_bytes(b"DB")
    (bundle / "config.yaml").write_text("universe: []", encoding="utf-8")
    desktop.SEED_DB = bundle / "valz.db"
    desktop.SEED_CFG = bundle / "config.yaml"

    desktop.seed_first_run()

    assert (fake_paths / "data" / "valz.db").read_bytes() == b"DB"
    assert (fake_paths / "data" / "config.yaml").read_text(encoding="utf-8") == \
        "universe: []"


def test_seed_first_run_creates_target_dir_if_missing(fake_paths, tmp_path):
    """A brand-new %LOCALAPPDATA\\valz must not block the first run."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "valz.db").write_bytes(b"DB")
    desktop.SEED_DB = bundle / "valz.db"
    desktop.SEED_CFG = bundle / "no-config"        # missing -> no copy
    assert not (fake_paths / "data").exists()        # pre-condition

    desktop.seed_first_run()

    assert (fake_paths / "data" / "valz.db").exists()
    assert not (fake_paths / "data" / "config.yaml").exists()


def test_seed_first_run_preserves_existing_user_data(fake_paths, tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "valz.db").write_bytes(b"FRESH_SEED")
    desktop.SEED_DB = bundle / "valz.db"

    (fake_paths / "data").mkdir(parents=True)
    (fake_paths / "data" / "valz.db").write_bytes(b"USER_DB")
    desktop.seed_first_run()
    assert (fake_paths / "data" / "valz.db").read_bytes() == b"USER_DB"


# ----------------------------------------------------------- load_env_file

def test_load_env_file_sets_only_valid_keys(fake_paths, monkeypatch):
    (fake_paths / ".env").write_text(
        "ARJUM_API_KEY=sk_test_xyz\n"
        "# comment line\n"
        "EMPTY_LINE=\n"
        "=no_key_here\n"
        'QUOTED="sk_q"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("ARJUM_API_KEY", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)
    desktop.load_env_file()
    assert os.environ["ARJUM_API_KEY"] == "sk_test_xyz"
    assert os.environ["QUOTED"] == "sk_q"
    # setdefault semantics: pre-set values are preserved
    monkeypatch.setenv("ARJUM_API_KEY", "already_set")
    desktop.load_env_file()
    assert os.environ["ARJUM_API_KEY"] == "already_set"


def test_load_env_file_no_file_is_silent(fake_paths):
    desktop.load_env_file()    # no .env -> no exception, no envs added


# ----------------------------------------------------------- pick_port

def test_pick_port_skips_already_bound(monkeypatch):
    """Reserve PORT_BASE, then pick_port must return a higher free port."""
    monkeypatch.setattr(desktop, "PORT_SCAN", 10)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((desktop.HOST, desktop.PORT_BASE))
    s.listen(1)
    try:
        chosen = desktop.pick_port()
        assert chosen > desktop.PORT_BASE
    finally:
        s.close()
    # after release, PORT_BASE is preferred again
    assert desktop.pick_port() == desktop.PORT_BASE


def test_pick_port_raises_when_all_taken(monkeypatch):
    """Exhaust the scan window with a held port at the top + bottom."""
    monkeypatch.setattr(desktop, "PORT_SCAN", 2)
    s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s1.bind((desktop.HOST, desktop.PORT_BASE))
    s1.listen(1)
    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2.bind((desktop.HOST, desktop.PORT_BASE + 1))
    s2.listen(1)
    try:
        with pytest.raises(RuntimeError, match="No free port"):
            desktop.pick_port()
    finally:
        s2.close()
        s1.close()


# ---------------------------------------------------------- is_data_stale

def _write_db(tmp_path, *, last_compute=None, name="valz.db"):
    """Build a minimal valid sqlite with the meta table the launcher reads."""
    p = tmp_path / name
    if p.exists():
        p.unlink()
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    if last_compute is not None:
        con.execute("INSERT INTO meta VALUES('last_compute',?)", (last_compute,))
    con.commit()
    con.close()
    return p


def test_is_data_stale_true_when_db_missing(tmp_path):
    assert desktop.is_data_stale(tmp_path / "nope.db") is True


def test_is_data_stale_false_when_last_compute_is_today(tmp_path):
    p = _write_db(tmp_path, last_compute="2026-08-24T10:00:00")
    assert desktop.is_data_stale(p, today=dt.date(2026, 8, 24)) is False


def test_is_data_stale_true_when_last_compute_older_than_today(tmp_path):
    p = _write_db(tmp_path, last_compute="2026-08-20T18:05:00")
    assert desktop.is_data_stale(p, today=dt.date(2026, 8, 24)) is True


def test_is_data_stale_weekend_rolls_back_to_friday(tmp_path):
    p = _write_db(tmp_path, last_compute="2026-08-21T18:05:00")   # Friday
    # Saturday: last_trading is still Friday -> not stale
    assert desktop.is_data_stale(p, today=dt.date(2026, 8, 22)) is False
    # Sunday: same -> still not stale
    assert desktop.is_data_stale(p, today=dt.date(2026, 8, 23)) is False
    # Friday before that: stale
    p2 = _write_db(tmp_path, last_compute="2026-08-14T18:05:00", name="v2.db")
    assert desktop.is_data_stale(p2, today=dt.date(2026, 8, 22)) is True


def test_is_data_stale_handles_missing_or_corrupt_meta(tmp_path):
    assert desktop.is_data_stale(_write_db(tmp_path, name="empty.db"),
                                 today=dt.date(2026, 8, 24)) is True
    assert desktop.is_data_stale(_write_db(tmp_path, last_compute="not-a-date",
                                           name="bad.db"),
                                 today=dt.date(2026, 8, 24)) is True


# --------------------------------------------------- import smoke

def test_module_imports_without_spinning_up_server():
    """Importing desktop must not start a server or hold a listening socket."""
    import threading
    assert "desktop" in __import__("sys").modules
    assert not any(t.name == "valz-server" for t in threading.enumerate())
