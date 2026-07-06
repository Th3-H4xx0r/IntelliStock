"""Pure paper-vs-real mode discriminator + decision-row scoping (kalshi.mode).

is_real_mode MUST mirror engine.should_execute (the real-order gate) — the contract
test enforces they never diverge.
"""
from kalshi.mode import is_real_mode, scope_decisions, kalshi_mode_changed
from kalshi.engine import should_execute


def test_is_real_mode_truth_table():
    assert is_real_mode("demo", False, False) is True        # demo executes freely
    assert is_real_mode("live", True, False) is True          # live + gate on
    assert is_real_mode("live", False, False) is False        # live, gate off
    assert is_real_mode("live", True, True) is False          # paper_mode hard override
    assert is_real_mode("", True, False) is False             # unknown env


def test_is_real_mode_matches_should_execute_contract():
    # mode.is_real_mode MUST never diverge from the real-order gate.
    for env in ("demo", "live", "prod", "", "weird"):
        for le in (True, False):
            for pm in (True, False):
                assert is_real_mode(env, le, pm) == should_execute(env, le, pm)


def test_scope_decisions_paper_vs_real():
    rows = [
        {"id": "a", "paper": True, "decision": "placed"},
        {"id": "b", "decision": "skipped"},              # no flag -> real side
        {"id": "c", "paper": False, "decision": "placed"},
        {"id": "d", "paper": True, "decision": "skipped"},
    ]
    paper = {r["id"] for r in scope_decisions(rows, show_paper=True)}
    real = {r["id"] for r in scope_decisions(rows, show_paper=False)}
    assert paper == {"a", "d"}
    assert real == {"b", "c"}


def _inst(kind="kalshi", paper=False, live_enabled=True, name="x"):
    return {"id": "i1", "kind": kind, "name": name,
            "kalshi_config": {"paper_mode": paper, "live_enabled": live_enabled}}


def test_mode_changed_on_paper_flip():
    assert kalshi_mode_changed(_inst(paper=False, live_enabled=True),
                               _inst(paper=True, live_enabled=False)) is True


def test_mode_unchanged_on_name_only_edit():
    assert kalshi_mode_changed(_inst(paper=True, live_enabled=False, name="a"),
                               _inst(paper=True, live_enabled=False, name="b")) is False


def test_mode_changed_ignores_non_kalshi():
    old = {"id": "i", "kind": "stock", "kalshi_config": {"paper_mode": False}}
    new = {"id": "i", "kind": "stock", "kalshi_config": {"paper_mode": True}}
    assert kalshi_mode_changed(old, new) is False


def test_mode_changed_handles_missing_snapshots():
    assert kalshi_mode_changed(None, _inst()) is False
    assert kalshi_mode_changed(_inst(), None) is False
