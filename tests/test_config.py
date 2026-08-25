from config import load_config

def test_defaults_when_no_file():
    cfg = load_config(None)
    assert cfg["windows_days"]["w5y"] == 1260
    assert cfg["groups"]["bank"]["primary"] == "pbv"
    assert cfg["filing_lag_days"] == 90

def test_user_overrides_win(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("filing_lag_days: 60\n", encoding="utf-8")
    assert load_config(str(p))["filing_lag_days"] == 60
