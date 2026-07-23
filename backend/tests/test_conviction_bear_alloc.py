"""2026-07-23 conviction-scaled SQQQ hedge: _conviction_bear_alloc scales the
bear-leg NAV cap UP with downtrend conviction (ret20 depth + confirmed-bear
persistence + ret5 momentum floor), ratcheted, default-off. broker.py runs
argparse at import, so we ast-extract the pure function.
"""
import ast
import pathlib

_BROKER = pathlib.Path(__file__).resolve().parents[1] / "broker.py"


def _load(name):
    src = _BROKER.read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            ns = {}
            exec(ast.get_source_segment(src, node), ns)
            return ns[name]
    raise AssertionError(f"{name} not found")


alloc = _load("_conviction_bear_alloc")

CFG = {
    "bear_alloc_pct": 0.35, "bear_alloc_scale_enabled": True,
    "bear_alloc_max_pct": 0.70, "bear_scale_start_pct": 4.0,
    "bear_scale_slope": 0.10, "bear_scale_min_days": 3,
    "bear_scale_ret5_floor_pct": 0.5,
}


def test_disabled_returns_static_base():
    cfg = dict(CFG, bear_alloc_scale_enabled=False)
    assert alloc(cfg, "bear", -6, -2, 5, 0.0) == (0.35, 0.0)  # ratchet untouched too


def test_crash_is_max():
    assert alloc(CFG, "crash", 0, 0, 0, 0.0) == (0.70, 0.70)


def test_confirmed_deep_falling_scales():
    # depth = -(-6) - 4 = 2 -> 0.35 + 0.10*2 = 0.55
    a, r = alloc(CFG, "bear", -6, -2, 5, 0.0)
    assert round(a, 4) == 0.55 and round(r, 4) == 0.55


def test_dwell_below_min_stays_base():
    assert alloc(CFG, "bear", -6, -2, 2, 0.0)[0] == 0.35  # only 2 confirmed days


def test_ret5_not_falling_stays_base():
    assert alloc(CFG, "bear", -6, -0.2, 5, 0.0)[0] == 0.35  # ret5 -0.2 > -0.5 floor


def test_shallow_depth_stays_base():
    assert alloc(CFG, "bear", -3, -2, 5, 0.0)[0] == 0.35  # depth = max(0, 3-4) = 0


def test_ratchet_never_shrinks():
    # computed would be base 0.35, but a prior 0.60 ratchet holds it
    a, r = alloc(CFG, "bear", -3, -2, 5, 0.60)
    assert round(a, 4) == 0.60 and round(r, 4) == 0.60


def test_clamped_at_max():
    # depth = 10-4 = 6 -> 0.35 + 0.6 = 0.95 -> clamp 0.70
    assert alloc(CFG, "bear", -10, -2, 5, 0.0)[0] == 0.70


def test_bad_inputs_safe():
    a, r = alloc(CFG, "bear", None, "x", None, None)
    assert a == 0.35  # non-numeric ret/dwell -> no scale, base
