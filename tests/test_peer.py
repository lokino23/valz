"""Peer comparison: pure functions for sector-relative valuation.

Pure-function unit tests; no I/O except SQLite reads via the connection
passed in.
"""
import pytest

from peer import peer_group_for, peer_codes_in


def _cfg(groups):
    return {"peer_groups": groups}


def test_peer_group_for_known_member():
    cfg = _cfg({"retail": ["AMRT", "ACES", "MAPI"]})
    assert peer_group_for(cfg, "AMRT") == "retail"


def test_peer_group_for_known_member_second_group():
    cfg = _cfg({"retail": ["AMRT"], "food": ["ICBP"]})
    assert peer_group_for(cfg, "ICBP") == "food"


def test_peer_group_for_not_member_returns_none():
    cfg = _cfg({"retail": ["AMRT"]})
    assert peer_group_for(cfg, "BMRI") is None


def test_peer_group_for_no_peer_groups_key_returns_none():
    assert peer_group_for({}, "AMRT") is None


def test_peer_codes_in_returns_full_list():
    cfg = _cfg({"retail": ["AMRT", "ACES", "MAPI"]})
    assert peer_codes_in(cfg, "retail") == ["AMRT", "ACES", "MAPI"]


def test_peer_codes_in_unknown_group_returns_empty():
    cfg = _cfg({"retail": ["AMRT"]})
    assert peer_codes_in(cfg, "nonexistent") == []


def test_peer_codes_in_preserves_yaml_order():
    cfg = _cfg({"retail": ["MAPI", "AMRT", "ACES"]})
    assert peer_codes_in(cfg, "retail") == ["MAPI", "AMRT", "ACES"]
