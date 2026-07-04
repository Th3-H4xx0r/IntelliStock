"""Run-185254 leak #2: LLM sell raws of -0.55..-1.15 on META/IBM were blocked
by min-hold+grace from day 1; the only exit left was the -10% fast-loser cut,
which fired at the June-10 bottom. This lever lets a strong LLM sell exit a
loser in the -3%..-10% dead zone."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.strategies.graph_nexus_analysis import _llm_sell_conviction_bypass

CFG = {
    "llm_sell_conviction_bypass_enabled": True,
    "llm_sell_conviction_min_raw": -0.5,
    "llm_sell_conviction_min_loss_pct": 3.0,
}


def test_strong_sell_on_loser_bypasses():
    assert _llm_sell_conviction_bypass(CFG, raw_score=-0.55, pnl_pct=-4.2) is True


def test_weak_signal_does_not_bypass():
    assert _llm_sell_conviction_bypass(CFG, raw_score=-0.3, pnl_pct=-9.0) is False


def test_winner_never_bypasses():
    assert _llm_sell_conviction_bypass(CFG, raw_score=-2.0, pnl_pct=+1.0) is False


def test_shallow_loss_does_not_bypass():
    assert _llm_sell_conviction_bypass(CFG, raw_score=-2.0, pnl_pct=-1.0) is False


def test_disabled_by_default():
    assert _llm_sell_conviction_bypass({}, raw_score=-2.0, pnl_pct=-9.0) is False


def test_none_pnl_never_bypasses():
    # Unknown P&L must fail closed (no bypass) rather than crash.
    assert _llm_sell_conviction_bypass(CFG, raw_score=-2.0, pnl_pct=None) is False
