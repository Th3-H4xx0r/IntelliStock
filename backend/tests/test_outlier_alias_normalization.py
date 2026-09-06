"""Dated identity normalization must retain the underlying issuer's history."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from normalize_outlier_archive import duplicate_prefix_cutoffs


def bar(day, close=10, volume=1000):
    return {"t": day, "c": close, "v": volume}


def test_old_ticker_prefix_removed_only_when_successor_preserves_all_observations():
    event = {"old_symbol": "OLD", "new_symbol": "NEW", "process_date": "2022-06-09"}
    raw = {"OLD": [bar("2022-06-07"), bar("2022-06-08"), bar("2025-01-01", 50)],
           "NEW": [bar("2022-06-07"), bar("2022-06-08"), bar("2022-06-09", 11)]}
    cutoffs, unresolved = duplicate_prefix_cutoffs(raw, [event])
    assert cutoffs == {"OLD": "2022-06-09"}
    assert unresolved == []
    assert [b["t"] for b in raw["OLD"] if b["t"] >= cutoffs["OLD"]] == ["2025-01-01"]


def test_missing_successor_does_not_remove_a_delisted_issuer():
    event = {"old_symbol": "DEAD", "new_symbol": "DEADQ", "process_date": "2023-03-10"}
    cutoffs, _ = duplicate_prefix_cutoffs({"DEAD": [bar("2023-03-09", .2)]}, [event])
    assert cutoffs == {}


def test_partial_or_different_successor_history_cannot_remove_old_history():
    event = {"old_symbol": "OLD", "new_symbol": "NEW", "process_date": "2022-06-09"}
    raw = {"OLD": [bar("2022-06-07"), bar("2022-06-08")], "NEW": [bar("2022-06-07", 20)]}
    cutoffs, unresolved = duplicate_prefix_cutoffs(raw, [event])
    assert cutoffs == {}
    assert unresolved[0]["old"] == "OLD"


def test_identity_chain_preserves_one_complete_successor():
    raw = {"A": [bar("2021-01-01")],
           "B": [bar("2021-01-01"), bar("2022-01-01")],
           "C": [bar("2021-01-01"), bar("2022-01-01"), bar("2023-01-01")]}
    events = [{"old_symbol": "A", "new_symbol": "B", "process_date": "2022-01-01"},
              {"old_symbol": "B", "new_symbol": "C", "process_date": "2023-01-01"}]
    assert duplicate_prefix_cutoffs(raw, events)[0] == {"A": "2022-01-01", "B": "2023-01-01"}


def test_ticker_changes_back_without_removing_both_copies():
    raw = {"A": [bar("2021-01-01"), bar("2022-01-01"), bar("2023-01-01")],
           "B": [bar("2021-01-01"), bar("2022-01-01")]}
    events = [{"old_symbol": "A", "new_symbol": "B", "process_date": "2022-01-01"},
              {"old_symbol": "B", "new_symbol": "A", "process_date": "2023-01-01"}]
    assert duplicate_prefix_cutoffs(raw, events)[0] == {"B": "2023-01-01"}


def test_same_day_rename_cycle_fails_closed_if_all_copies_would_be_deleted():
    import pytest
    raw = {"A": [bar("2021-01-01")], "B": [bar("2021-01-01")]}
    events = [{"old_symbol": "A", "new_symbol": "B", "process_date": "2022-01-01"},
              {"old_symbol": "B", "new_symbol": "A", "process_date": "2022-01-01"}]
    with pytest.raises(ValueError, match="no retained successor"):
        duplicate_prefix_cutoffs(raw, events)
