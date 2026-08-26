"""Task 11 static smoke: index.html carries the required UI contract hooks.

Honest static check only -- the file must contain the exact element ids and
fetch paths the behaviour contract depends on. Visual QA stays manual.
"""
import pathlib

HTML = (pathlib.Path(__file__).parent.parent / "static" / "index.html"
        ).read_text(encoding="utf-8")


def _has(needle):
    assert needle in HTML, f"missing required marker: {needle}"


def test_window_filter_select_with_both_windows():
    _has('id="filter-window"')
    # both window options must be offered on the select
    select = HTML.split('id="filter-window"', 1)[1].split("</select>", 1)[0]
    assert "w3y" in select and "w5y" in select


def test_syaria_filter_select_with_three_modes():
    _has('id="filter-syaria"')
    select = HTML.split('id="filter-syaria"', 1)[1].split("</select>", 1)[0]
    for opt in ('value="all"', 'value="only"', 'value="exclude"'):
        assert opt in select, f"syaria select missing {opt}"


def test_syaria_column_header_in_table():
    """Syaria column must be in the header for the per-row badge to render."""
    # between <thead> and </thead>
    head = HTML.split('<thead>', 1)[1].split('</thead>', 1)[0]
    assert '<th>Sy</th>' in head


def test_threshold_slider_present():
    _has('id="threshold"')


def test_table_drawer_and_chart_containers():
    _has('id="tbl"')
    _has('id="drawer"')
    _has('id="chart"')


def test_fetch_paths_verbatim():
    _has("/api/screen")
    _has("/api/ticker/")


def test_vendored_echarts_referenced():
    _has("vendor/echarts.min.js")


def test_dark_palette_and_placeholder_literal():
    _has("#0f172a")                      # bg token from the palette contract
    _has("Tanggal data tidak tersedia")  # null as_of literal


def test_mounted_static_served_by_app(tmp_path):
    """app.py mounts static/ at / so the ui is reachable without a proxy."""
    from fastapi.testclient import TestClient

    from app import create_app

    c = TestClient(create_app(db_path=str(tmp_path / "x.db"), cfg={
        "universe": [], "sector_map": {}, "groups": {"general": {
            "primary": "per", "secondary": "pbv"}},
        "windows_days": {"w3y": 300, "w5y": 600},
        "min_coverage": 0.8, "filing_lag_days": 90,
        "thresholds": {"watch": -1.0, "deep": -2.0}}))
    r = c.get("/")
    assert r.status_code == 200
    assert b'id="filter-window"' in r.content
    assert c.get("/vendor/echarts.min.js").status_code == 200


def test_chart_renders_peer_median_when_present():
    """The chart drawer's renderChart function must reference the
    peer-median series when the data has a peer field. The reference
    'peer_median' is the series name and the label string key."""
    assert "peer_median" in HTML
