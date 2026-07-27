"""Ensemble blend + margin-guarded champion selection."""
import sys, types
sys.modules.setdefault("socketio", types.ModuleType("socketio"))

from kalshi import model_ensemble as me


def test_ensemble_blends_and_renormalizes():
    phys = {"home": 0.7, "draw": 0.2, "away": 0.1}
    learned = {"home": 0.3, "draw": 0.3, "away": 0.4}
    b = me.ensemble(phys, learned, w=0.5)
    assert abs(sum(b.values()) - 1.0) < 1e-9
    assert 0.3 < b["home"] < 0.7            # between the two
    # w=0 -> pure physical; w=1 -> pure learned
    assert abs(me.ensemble(phys, learned, w=0.0)["home"] - 0.7) < 1e-9
    assert abs(me.ensemble(phys, learned, w=1.0)["away"] - 0.4) < 1e-9


def _synthetic_fixtures(n=100):
    # Outcomes matching the physical probs below (50h/25d/25a) so physical is already
    # well-calibrated -> a base-rate learned model can't beat it by a margin.
    fx = []
    for i in range(n):
        m = i % 4
        res = "home" if m in (0, 1) else ("draw" if m == 2 else "away")
        fx.append({"fixture_id": f"f{i:03d}", "kickoff_ts": i,
                   "home": "TeamA", "away": "TeamC", "result": res})
    return fx


def _feat_fn(fx):
    # identical features every game -> learned can only recover the base rate, which
    # equals the (well-calibrated) physical probs -> no margin win.
    return [0.5, 0.25, 0.25, 0.0, 1.0], {"home": 0.5, "draw": 0.25, "away": 0.25}


def test_evaluate_models_shape_and_margin_default_physical():
    out = me.evaluate_models(_synthetic_fixtures(), _feat_fn, min_train=20, margin=0.02)
    assert set(out["results"]) == {"physical", "learned", "ensemble"}
    assert out["champion"] in ("physical", "learned", "ensemble")
    # with identical physical features every game, learned can't beat physical by a
    # margin -> stays physical (the safe default)
    assert out["champion"] == "physical"
    assert out["learned_weights"] is None   # not stored when champion is physical


def test_too_little_data_stays_physical():
    out = me.evaluate_models(_synthetic_fixtures(10), _feat_fn, min_train=30)
    assert out["champion"] == "physical" and out["learned_weights"] is None
