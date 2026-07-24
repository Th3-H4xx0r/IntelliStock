"""Recovery-capture regime timing (2026-07-24).

Two default-off levers let the auto-switch capture a V-shaped recovery faster
WITHOUT breaking the pure-bear result or re-arming bull v9 early:

1. `regime_recovery_ma_bars` (default 20 = byte-identical): the recovery-override's
   reclaim guard uses this MA window. Near a V-bottom the 20-day MA is still
   propped up by pre-crash highs and is never reclaimed, so the override was a
   no-op; a shorter MA (10) is reclaimed within a bar or two of a genuine turn.
2. `regime_recovery_fast_confirm_enabled` (default False): a chop raw PRODUCED by
   the recovery-override upgrades bear->chop immediately (skips the k-bar dwell),
   but ONLY bear->chop from a recovery bar; bull still needs the full k bars.

gna.py is not import-safe under pytest (module-load side effects), so we
AST-extract just the two pure functions with stubbed dependencies — same pattern
as test_regime_profile.py.
"""
import ast
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_WANTED = {"_detect_market_regime", "_apply_regime_hysteresis",
           "_next_recovery_flag", "_apply_recovery_cap"}
_src = open(os.path.join(_backend, "strategies", "graph_nexus_analysis.py")).read()
_tree = ast.parse(_src)

# Namespace with stubbed module-level deps the two functions reference.
_ns = {
    "_REGIME_RANK": {"crash": 0, "bear": 1, "chop": 2, "bull": 3},
    "_log": lambda *a, **k: None,
    # proxy closes are stored directly as a list under strategy_cache; the stub
    # just hands them back so we control the exact price series the detector sees.
    "_point_in_time_closes": lambda bars, date_str: bars if isinstance(bars, list) else [],
    "_daily_closes_from_intraday": lambda bars, date_str: [],
}
for _node in _tree.body:
    if isinstance(_node, ast.FunctionDef) and _node.name in _WANTED:
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "gna.py", "exec"), _ns)
detect = _ns["_detect_market_regime"]
hysteresis = _ns["_apply_regime_hysteresis"]
next_recovery_flag = _ns["_next_recovery_flag"]
apply_recovery_cap = _ns["_apply_recovery_cap"]

# A V-bottom: high plateau -> decline -> partial recovery. Numerically:
#   ret20 = -4.8% (< -3 -> bear branch eligible), ret5 = +3.48% (in [1.5,5) -> chop
#   not bull), off_low = 0.52 (>= 0.5), price > 10-day MA (93.07) but < 20-day MA
#   (96.23). So ma_bars=10 fires, ma_bars=20 (default) does not.
V_BOTTOM = [100] * 9 + [98, 96, 94, 92, 90, 91, 92, 93, 94, 94.5, 95, 95.2]
# A deepening bear (monotone decline): ret5 = -5.4% -> the thrust guard rejects it
# at ANY ma_bars. This is the dead-cat / pure-bear safety case.
DEEPENING = [100] * 9 + [99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88]

_RECOVERY_CFG = {
    "regime_bear_spy_drawdown_pct": 3,
    "regime_recovery_override_enabled": True,
    "regime_recovery_ret5_min_pct": 1.5,
    "regime_recovery_off_low_pct": 0.5,
    "regime_recovery_bull_ret5_pct": 5.0,
}


def _cache(closes):
    return {"_overlay_bars_raw": {"SPY": list(closes)}}


def _run_detect(closes, **cfg_over):
    cfg = {**_RECOVERY_CFG, **cfg_over}
    return detect(_cache(closes), cfg, "2026-04-02", data=None)


# ---------- Lever 1: parameterized reclaim MA ----------

def test_ma_bars_absent_is_byte_identical_no_fire():
    """Override ON but no regime_recovery_ma_bars key -> defaults to 20 -> the
    20-day MA is not reclaimed on the V -> stays bear (today's behavior)."""
    assert _run_detect(V_BOTTOM) == "bear"


def test_ma_bars_20_explicit_matches_default():
    assert _run_detect(V_BOTTOM, regime_recovery_ma_bars=20) == "bear"


def test_ma_bars_10_fires_chop_on_vbottom():
    """The fix: a 10-day reclaim window trips on the genuine turn -> chop."""
    assert _run_detect(V_BOTTOM, regime_recovery_ma_bars=10) == "chop"


def test_deepening_bear_never_fires_any_ma_bars():
    """Dead-cat / pure-bear safety: negative ret5 -> bear regardless of ma_bars."""
    assert _run_detect(DEEPENING, regime_recovery_ma_bars=10) == "bear"
    assert _run_detect(DEEPENING, regime_recovery_ma_bars=20) == "bear"


def test_override_disabled_is_bear_even_with_short_ma():
    """Master gate off -> the ma_bars lever is inert (default-safe)."""
    assert _run_detect(V_BOTTOM, regime_recovery_override_enabled=False,
                       regime_recovery_ma_bars=10) == "bear"


def test_bad_ma_bars_falls_back_to_safe_default_not_chop():
    """A malformed live-config value must fall back to the safe no-op default (20)
    -> stays bear, NOT crash-swallowed into 'chop' (which would disable bear
    protection on a deep-bear bar). Covers non-numeric, negative, 1, True."""
    for bad in ("abc", float("nan"), -3, 0, 1, True):
        assert _run_detect(V_BOTTOM, regime_recovery_ma_bars=bad) == "bear", bad
        # and on a genuine deep bear, a bad knob must never fabricate a recovery
        assert _run_detect(DEEPENING, regime_recovery_ma_bars=bad) == "bear", bad


def test_recovery_override_stamps_recover_diag():
    """The chop return must carry a 'recover' diag raw so the fast-confirm can
    distinguish it from an ordinary chop bar."""
    sc = _cache(V_BOTTOM)
    out = detect(sc, {**_RECOVERY_CFG, "regime_recovery_ma_bars": 10},
                 "2026-04-02", data=None)
    assert out == "chop"
    assert str(sc["_market_regime_diag"].get("raw") or "").startswith("recover")


# ---------- Lever 2: recovery fast-confirm ----------

def _seed(cur="bear"):
    return {"_regime_hyst": {"cur": cur, "pend": None, "n": 0},
            "_market_regime_diag": {"raw": "recover->chop"}}


def test_fast_confirm_off_still_needs_full_dwell():
    """Default (flag off): a recovery chop still pays the 3-bar upgrade dwell."""
    sc = _seed()
    cfg = {"regime_upgrade_confirm_bars": 3}
    assert hysteresis(sc, "chop", cfg) == "bear"   # bar 1
    assert hysteresis(sc, "chop", cfg) == "bear"   # bar 2
    assert hysteresis(sc, "chop", cfg) == "chop"   # bar 3 -> confirmed


def test_fast_confirm_on_upgrades_bear_to_chop_immediately():
    sc = _seed()
    cfg = {"regime_upgrade_confirm_bars": 3, "regime_recovery_fast_confirm_enabled": True}
    assert hysteresis(sc, "chop", cfg) == "chop"   # immediate, no dwell


def test_fast_confirm_does_not_fast_track_non_recovery_chop():
    """An ordinary chop bar (diag raw not 'recover') must NOT skip the dwell."""
    sc = {"_regime_hyst": {"cur": "bear", "pend": None, "n": 0},
          "_market_regime_diag": {"raw": "chop"}}
    cfg = {"regime_upgrade_confirm_bars": 3, "regime_recovery_fast_confirm_enabled": True}
    assert hysteresis(sc, "chop", cfg) == "bear"   # no fast-track


def test_fast_confirm_never_fast_tracks_bull():
    """A strong recovery->bull must still serve the full dwell (v9's aggressive
    extension gate is never re-armed early)."""
    sc = {"_regime_hyst": {"cur": "bear", "pend": None, "n": 0},
          "_market_regime_diag": {"raw": "recover->bull"}}
    cfg = {"regime_upgrade_confirm_bars": 3, "regime_recovery_fast_confirm_enabled": True}
    assert hysteresis(sc, "bull", cfg) == "bear"   # bar 1, no fast-track to bull


def test_fast_confirm_downgrade_still_immediate():
    """After fast-tracking to chop, a fresh-decline bear raw downgrades at once."""
    sc = _seed()
    cfg = {"regime_upgrade_confirm_bars": 3, "regime_recovery_fast_confirm_enabled": True}
    assert hysteresis(sc, "chop", cfg) == "chop"
    sc["_market_regime_diag"] = {"raw": "bear"}
    assert hysteresis(sc, "bear", cfg) == "bear"   # immediate downgrade


# ---------- v2: recovery-regime flag lifecycle ----------

def test_recovery_flag_set_on_recovery_produced_chop():
    assert next_recovery_flag(False, "chop", "recover->chop") is True


def test_recovery_flag_sticky_within_chop():
    # already in recovery, ordinary chop bar (raw no longer 'recover') -> stays True
    assert next_recovery_flag(True, "chop", "chop") is True


def test_recovery_flag_false_for_ordinary_chop():
    # entered chop via a non-recovery path and was never in recovery -> False
    assert next_recovery_flag(False, "chop", "chop") is False


def test_recovery_flag_cleared_when_leaving_chop():
    for conf in ("bear", "bull", "crash"):
        assert next_recovery_flag(True, conf, "recover->chop") is False, conf


# ---------- v2: recovery capacity ----------

def test_recovery_cap_raises_when_flag_and_key():
    assert apply_recovery_cap(8, True, {"max_positions_recovery": 14}) == 14


def test_recovery_cap_only_raises_never_lowers():
    assert apply_recovery_cap(20, True, {"max_positions_recovery": 14}) == 20


def test_recovery_cap_identity_without_flag():
    assert apply_recovery_cap(8, False, {"max_positions_recovery": 14}) == 8


def test_recovery_cap_identity_without_key():
    assert apply_recovery_cap(8, True, {}) == 8


def test_recovery_cap_bad_value_falls_back_to_base():
    for bad in ("abc", None, float("nan")):
        assert apply_recovery_cap(8, True, {"max_positions_recovery": bad}) == 8, bad
