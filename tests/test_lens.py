"""Industry lens: pure functions for sector-aware valuation.

Pure-function unit tests; no I/O. The I/O function (lens_metrics_for) is
tested via the API integration tests in tests/test_api.py with the
client_with_lenses fixture.
"""
import pytest

from lens import (
    lens_for,
    lens_cfg_for,
    list_lens_labels,
    list_supported_sectors,
    evaluate_verdict,
)


SECTOR_MAP = {"BBCA": "bank", "ICBP": "consumer", "ADRO": "commodity"}


def _cfg(sectors=None, sector_map=None):
    """Build a minimal cfg dict for testing. By default uses bank/consumer/commodity."""
    if sectors is None:
        sectors = {
            "bank": {
                "label": "bank_value",
                "primary": "pbv",
                "supporting": {"roe_min": 0.15, "der_max": 5.0},
                "verdict_rules": {
                    "undervalued_quality": [
                        {"primary_z": "<= -1.0"}, {"roe": ">= 0.15"}, {"der": "<= 5.0"}
                    ],
                    "cheap_but_deteriorating": [
                        {"primary_z": "<= -1.0"}, {"roe": "< 0.15"}
                    ],
                    "expensive": [{"primary_z": ">= 1.5"}],
                    "fair": "default",
                },
            },
        }
    return {
        "sector_map": sector_map or SECTOR_MAP,
        "industry_lenses": sectors,
    }


def test_lens_for_known_code_returns_sector():
    cfg = _cfg()
    assert lens_for(cfg, "BBCA") == "bank"


def test_lens_for_unknown_code_returns_none():
    cfg = _cfg()
    assert lens_for(cfg, "ZZZZ") is None


def test_lens_for_empty_sector_map_returns_none():
    # Pass a sector_map without BBCA to honor the test name's intent
    # (BBCA not in the lookup table => lens_for returns None).
    cfg = _cfg(sector_map={"ICBP": "consumer", "ADRO": "commodity"})
    assert lens_for(cfg, "BBCA") is None


def test_lens_cfg_for_known_sector():
    cfg = _cfg()
    block = lens_cfg_for(cfg, "bank")
    assert block is not None
    assert block["label"] == "bank_value"
    assert block["primary"] == "pbv"


def test_lens_cfg_for_unknown_sector_returns_none():
    cfg = _cfg()
    assert lens_cfg_for(cfg, "nonexistent") is None


def test_lens_cfg_for_no_lenses_block_returns_none():
    cfg = {"sector_map": SECTOR_MAP}  # no industry_lenses key
    assert lens_cfg_for(cfg, "bank") is None


def test_list_lens_labels_returns_all_labels():
    cfg = _cfg(sectors={
        "bank": {"label": "bank_value", "primary": "pbv", "supporting": {},
                  "verdict_rules": {"fair": "default"}},
        "consumer": {"label": "consumer_value", "primary": "per", "supporting": {},
                      "verdict_rules": {"fair": "default"}},
    })
    assert set(list_lens_labels(cfg)) == {"bank_value", "consumer_value"}


def test_list_supported_sectors_returns_sectors_with_lens():
    cfg = _cfg(sectors={
        "bank": {"label": "bank_value", "primary": "pbv", "supporting": {},
                  "verdict_rules": {"fair": "default"}},
    })
    assert list_supported_sectors(cfg) == ["bank"]


def test_evaluate_verdict_matches_first_rule_in_priority():
    """Priority: undervalued_quality > cheap_but_deteriorating > expensive > fair."""
    cfg = _cfg()
    lens_cfg = lens_cfg_for(cfg, "bank")
    # primary_z=-1.5 satisfies both undervalued_quality (z <= -1.0) AND
    # cheap_but_deteriorating (z <= -1.0). With roe=0.20, the first rule
    # (undervalued_quality) should win.
    result = evaluate_verdict(lens_cfg, primary_z=-1.5, supporting_values={"roe": 0.20, "der": 4.0})
    assert result == "undervalued_quality"


def test_evaluate_verdict_cheap_but_deteriorating_when_roe_low():
    cfg = _cfg()
    lens_cfg = lens_cfg_for(cfg, "bank")
    # primary_z=-1.5, roe=0.10 (below 0.15 threshold) -> cheap_but_deteriorating
    result = evaluate_verdict(lens_cfg, primary_z=-1.5, supporting_values={"roe": 0.10, "der": 4.0})
    assert result == "cheap_but_deteriorating"


def test_evaluate_verdict_expensive_when_primary_z_high():
    cfg = _cfg()
    lens_cfg = lens_cfg_for(cfg, "bank")
    result = evaluate_verdict(lens_cfg, primary_z=2.0, supporting_values={"roe": 0.20, "der": 4.0})
    assert result == "expensive"


def test_evaluate_verdict_fair_when_no_rule_matches():
    cfg = _cfg()
    lens_cfg = lens_cfg_for(cfg, "bank")
    # primary_z=0.0 doesn't match any rule
    result = evaluate_verdict(lens_cfg, primary_z=0.0, supporting_values={"roe": 0.20, "der": 4.0})
    assert result == "fair"


def test_evaluate_verdict_skips_rule_when_metric_missing():
    """If a rule requires roe but roe is None, skip that rule (not crash)."""
    cfg = _cfg()
    lens_cfg = lens_cfg_for(cfg, "bank")
    # roe=None should skip the undervalued_quality rule (needs roe),
    # but cheap_but_deteriorating also needs roe. Both skipped.
    # expensive needs primary_z only; primary_z=2.0 matches. Result: expensive.
    result = evaluate_verdict(lens_cfg, primary_z=2.0, supporting_values={"roe": None, "der": 4.0})
    assert result == "expensive"


def test_evaluate_verdict_handles_none_primary_z():
    """If primary_z is None, no z-based rule fires; default to fair."""
    cfg = _cfg()
    lens_cfg = lens_cfg_for(cfg, "bank")
    result = evaluate_verdict(lens_cfg, primary_z=None, supporting_values={"roe": 0.20, "der": 4.0})
    assert result == "fair"
