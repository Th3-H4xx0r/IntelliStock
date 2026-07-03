"""SP2 model ensemble: features, blend, and the champion-selection harness that
compares the physical / learned / ensemble models on held-out settled games.

The learned model stacks on the physical model — its features ARE the physical
model's probs plus a little context (Elo gap, neutral-site) — so it can only learn
to *correct* the physical view, and the ensemble hedges the two. Selection is
held-out log-loss (same discipline as SP1: never trust in-sample fit), so the
worker only promotes a model that actually beats the others on unseen games.
"""
from __future__ import annotations

import math

from kalshi import learned_model, training
from kalshi.quant.elo import elo_to_expected_goals, HOME_FIELD_ADVANTAGE, NEUTRAL_HFA
from kalshi.quant.dixon_coles import scoreline_matrix
from kalshi.quant.derive_markets import one_x_two
from kalshi.quant.national_elo import national_elo_from, is_national_team
from kalshi.data.sources.clubelo import elo_for

CLASSES = ("home", "draw", "away")
FEATURE_NAMES = ("phys_home", "phys_draw", "phys_away", "elo_diff_tanh", "neutral")


def build_feature_fn(nat_elo, elo):
    """Return `feat(fx) -> (features:list, phys_probs:dict)`. The physical winner
    probs are computed with the SAME context-aware HFA the live model uses, so the
    learned model trains on the same signal it will see live."""
    nat_elo = nat_elo or {}
    elo = elo or {}

    def _elo(name):
        return national_elo_from(nat_elo, name) if is_national_team(name) else elo_for(elo, name)

    def feat(fx):
        home, away = (fx or {}).get("home", ""), (fx or {}).get("away", "")
        he, ae = _elo(home), _elo(away)
        neutral = 1.0 if (is_national_team(home) and is_national_team(away)) else 0.0
        hfa = NEUTRAL_HFA if neutral else HOME_FIELD_ADVANTAGE
        hxg, axg = elo_to_expected_goals(he, ae, hfa=hfa)
        phys = one_x_two(scoreline_matrix(hxg, axg))
        features = [phys.get("home", 1 / 3), phys.get("draw", 1 / 3), phys.get("away", 1 / 3),
                    math.tanh((he - ae) / 400.0), neutral]
        return features, phys

    return feat


def ensemble(phys: dict, learned: dict, w: float = 0.5) -> dict:
    """Weighted blend of two {home,draw,away} dicts, renormalized. `w` = weight on
    the LEARNED model (0 = pure physical, 1 = pure learned)."""
    out = {s: (1.0 - w) * float(phys.get(s, 0.0)) + w * float(learned.get(s, 0.0)) for s in CLASSES}
    tot = sum(out.values()) or 1.0
    return {s: out[s] / tot for s in CLASSES}


def _per_side_samples(fixtures, probs_fn):
    s = []
    for f in fixtures:
        probs = probs_fn(f)
        res = f.get("result")
        for side in CLASSES:
            s.append((probs.get(side, 1 / 3), 1.0 if side == res else 0.0))
    return s


def evaluate_models(fixtures, feat_fn, *, l2: float = 2.0, ensemble_w: float = 0.5,
                    min_train: int = 30, margin: float = 0.02) -> dict:
    """Split settled fixtures train/test (by kickoff), fit the learned model on the
    train half, then score physical / learned / ensemble on the HELD-OUT half.
    Returns a ranked report + the champion + the fitted learned weights.

    Champion selection is MARGIN-GUARDED: physical is the safe default, and we only
    switch to learned/ensemble when it beats physical's held-out log-loss by at least
    `margin` (relative). On thin/noisy data (where the raw winner flips with the
    regularization strength) this keeps us on physical rather than chasing a split-
    specific fluke. Degrades to physical when there isn't enough data to fit."""
    fixtures = [f for f in (fixtures or []) if (f or {}).get("result") in CLASSES]
    fixtures.sort(key=lambda f: (str(f.get("kickoff_ts") or ""), str(f.get("fixture_id") or "")))
    train, test = fixtures[::2], fixtures[1::2]

    Xtr, ytr = [], []
    for f in train:
        feats, _ = feat_fn(f)
        Xtr.append(feats)
        ytr.append(CLASSES.index(f["result"]))
    weights = learned_model.fit(Xtr, ytr, l2=l2) if len(Xtr) >= min_train else None

    def phys_fn(f):
        return feat_fn(f)[1]

    def learned_fn(f):
        if not weights:
            return feat_fn(f)[1]
        return learned_model.predict(weights, feat_fn(f)[0])

    def ens_fn(f):
        if not weights:
            return feat_fn(f)[1]
        feats, phys = feat_fn(f)
        return ensemble(phys, learned_model.predict(weights, feats), ensemble_w)

    results = {}
    for name, fn in (("physical", phys_fn), ("learned", learned_fn), ("ensemble", ens_fn)):
        m = training.evaluate(_per_side_samples(test, fn), None)   # raw (uncalibrated) held-out
        results[name] = {"logloss": round(m["raw_logloss"], 4),
                         "brier": round(m["raw_brier"], 4), "n": m["n_eval"]}
    return _select(results, weights, ensemble_w, len(Xtr), len(test), l2, margin)


def _select(results, weights, ensemble_w, n_train, n_test, l2, margin):
    ranked = [k for k, _ in sorted(results.items(), key=lambda kv: kv[1]["logloss"])]
    # Margin guard: physical is the default; switch only on a real held-out win.
    champion = "physical"
    if weights and results["physical"]["n"] > 0:
        phys_ll = results["physical"]["logloss"]
        best = ranked[0]
        if best != "physical" and results[best]["logloss"] <= phys_ll * (1.0 - margin):
            champion = best
    return {"results": results, "ranked": ranked, "champion": champion,
            "learned_weights": weights if champion != "physical" else None,
            "ensemble_w": ensemble_w, "feature_names": list(FEATURE_NAMES),
            "l2": l2, "margin": margin, "n_train": n_train, "n_test": n_test}


def refit_model_once(conn, instance_id, fixtures, feat_fn, *, new_id, now_iso,
                     l2: float = 2.0, ensemble_w: float = 0.5, min_train: int = 30,
                     margin: float = 0.02, promote: bool = True) -> dict:
    """Run the physical/learned/ensemble comparison, persist a `kind='model'`
    registry version, and promote it (the margin guard inside `evaluate_models`
    already keeps the champion = physical unless a real held-out win, so promoting
    the latest assessment is safe)."""
    from kalshi import db as _db
    rep = evaluate_models(fixtures, feat_fn, l2=l2, ensemble_w=ensemble_w,
                          min_train=min_train, margin=margin)
    version = {
        "id": new_id, "instance_id": instance_id, "kind": "model",
        "created_at": now_iso, "is_champion": False,
        "champion": rep["champion"], "learned_weights": rep["learned_weights"],
        "ensemble_w": rep["ensemble_w"], "feature_names": rep["feature_names"],
        "l2": rep.get("l2"), "metrics": rep["results"], "ranked": rep["ranked"],
        "n_train": rep["n_train"], "n_test": rep["n_test"],
    }
    _db.save_model_version(conn, version)
    if promote:
        _db.set_champion(conn, new_id, instance_id, "model")
    version["promoted"] = bool(promote)
    return version


def champion_predict_fn(champ_doc, nat_elo, elo):
    """Return `fn(fx) -> winner_probs` for the current model champion. Defaults to
    the physical model (safe) for a missing/physical champion; uses the learned or
    ensemble prediction only when the champion is that and weights are present."""
    feat_fn = build_feature_fn(nat_elo, elo)
    champ = (champ_doc or {}).get("champion", "physical")
    weights = (champ_doc or {}).get("learned_weights")
    ew = float((champ_doc or {}).get("ensemble_w", 0.5))

    def fn(fx):
        feats, phys = feat_fn(fx)
        if champ == "physical" or not weights:
            return phys
        learned = learned_model.predict(weights, feats)
        return learned if champ == "learned" else ensemble(phys, learned, ew)

    return fn
