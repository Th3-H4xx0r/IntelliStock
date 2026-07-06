"""Pure paper-vs-real mode discriminator + decision-row scoping (kalshi.mode).

is_real_mode MUST mirror engine.should_execute (the real-order gate) — the contract
test enforces they never diverge.
"""
from kalshi.mode import is_real_mode, scope_decisions
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
