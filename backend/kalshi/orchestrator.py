"""Pipeline orchestration (pure). Given per-fixture inputs (expected goals, sharp
probs, analyst output, available Kalshi markets, liquidity, time-to-kickoff),
produce the capital allocation across the whole forward book PLUS a decision-log
row for every candidate (placed or skipped). This is the decision core the
run_instance loop wraps with I/O — fully unit-testable with no network."""
from __future__ import annotations

from kalshi.intelligence.pricing import build_market_probs
from kalshi.strategy.candidates import generate_candidates
from kalshi.capital.opportunity import score as opportunity_score
from kalshi.capital.planner import allocate
from kalshi.decisions import decision_doc
from kalshi.devig import market_probs_from_asks
from kalshi.intelligence.fusion import renormalize_group
from kalshi import training


def plan_and_allocate(
    fixtures: list[dict],
    *,
    instance_id: str,
    brokerage_id: str,
    ts: str,
    tier: str,
    caps,
    fee_rate: float,
    edge_threshold: float,
    reserve_frac: float,
    expected_better_soon: bool,
    w_sharp: float = 0.7,
    llm_cap: float = 0.05,
    market_shrink: float = 0.0,
    one_bet_per_fixture: bool = True,
    devig_method: str = "power",
    calibrator: dict | None = None,
) -> dict:
    """fixtures: each {fixture_id, expected_goals, sharp_probs, analyst:{adjustments,
    rationales}, kalshi_markets, liquidity, hours_to_kickoff, model_confidence}.
    Returns {allocations, decisions, candidates}."""
    scored: list[dict] = []
    meta: dict = {}  # cid -> (candidate, fixture, rationale, model_probs, opp_score)
    skip_decisions: list = []  # considered-but-rejected markets (stable id -> latest eval)

    for fx in fixtures:
        eg = fx.get("expected_goals")
        # Pregame context carried onto every decision row for the UI match view.
        _hx = eg[0] if (eg and len(eg) > 0) else None
        _ax = eg[1] if (eg and len(eg) > 1) else None
        _ctx = dict(home_elo=fx.get("home_elo"), away_elo=fx.get("away_elo"),
                    home_xg=_hx, away_xg=_ax)
        sharp = fx.get("sharp_probs") or {}
        analyst = fx.get("analyst") or {}
        adjustments = analyst.get("adjustments") or {}
        rationales = analyst.get("rationales") or {}

        player_probs = fx.get("player_probs")
        fused = build_market_probs(eg, sharp, adjustments, player_probs=player_probs, w_sharp=w_sharp, llm_cap=llm_cap)
        model_only = build_market_probs(eg, {}, {}, player_probs=player_probs, w_sharp=w_sharp, llm_cap=llm_cap)
        # MARKET ANCHOR: with no sharp line, pull the (overconfident) model toward the
        # de-vigged Kalshi market so edge = a SHRUNK model-vs-market disagreement, not
        # the model's blind view. Only fires when there is no sharp anchor already.
        if not sharp and market_shrink > 0.0:
            win_asks = {
                m["side"]: m["yes_ask_cents"]
                for m in fx.get("kalshi_markets", [])
                if m.get("market_type") == "winner" and (m.get("yes_ask_cents") or 0) > 0
            }
            market = market_probs_from_asks(win_asks, devig_method)
            if market and "winner" in fused:
                for side in list(fused["winner"]):
                    if side in market:
                        fused["winner"][side] = (
                            (1.0 - market_shrink) * fused["winner"][side]
                            + market_shrink * market[side]
                        )
        # CALIBRATION: remap the winner probs through the champion isotonic calibrator
        # (fit on settled outcomes by the training worker) so a stated 60% actually
        # wins ~60%. Monotone + renormalized to sum to 1. No champion -> no-op.
        if calibrator and fused.get("winner"):
            fused["winner"] = renormalize_group(
                {s: training.apply(calibrator, p) for s, p in fused["winner"].items()})
        cands, skips = generate_candidates(
            fx["fixture_id"], tier, fused, fx.get("kalshi_markets", []),
            fee_rate=fee_rate, edge_threshold=edge_threshold,
            min_price_cents=getattr(caps, "min_price_cents", 15),
            max_price_cents=getattr(caps, "max_price_cents", 90),
            draw_min_edge=getattr(caps, "draw_min_edge", 0.10),
            sharp_probs=sharp,
            no_sharp_edge_threshold=getattr(caps, "no_sharp_edge_threshold", 0.0),
            collect_skips=True,
        )
        # PER-FIXTURE LOCK: never hold more than one side of the same match — keep
        # only the single highest-edge candidate (no correlated both-sides exposure).
        if one_bet_per_fixture and len(cands) > 1:
            cands = sorted(cands, key=lambda c: c.edge, reverse=True)[:1]
        for s in skips:
            # Log what the bot evaluated and why it passed. Stable id (no ts) upserts
            # to ONE row per market = its latest evaluation -> the decision log shows
            # every match watched + its edge, without unbounded per-tick growth.
            srow = decision_doc(
                instance_id=instance_id, brokerage_id=brokerage_id, ts=ts,
                fixture_id=fx["fixture_id"], market_ticker=s["market_ticker"], side=s["side"],
                model_prob=(model_only.get(s["market_type"], {}) or {}).get(s["side"]),
                sharp_prob=(sharp.get(s["market_type"], {}) or {}).get(s["side"]),
                fused_fair=s["fair"], edge=s["edge"], decision="skipped",
                block_reason=s["reason"], league=fx.get("league", ""),
                # carry the analyst's per-match reasoning so the decision log shows the
                # LLM work it did, not just the numeric skip.
                llm_adjustment=adjustments.get(s["market_type"]),
                llm_rationale=rationales.get(s["market_type"], ""),
                **_ctx,
            )
            srow["id"] = f"{instance_id}|{s['market_ticker']}"
            skip_decisions.append(srow)
        for c in cands:
            cid = f"{fx['fixture_id']}|{c.market_ticker}"
            opp = opportunity_score(
                edge=c.edge, model_confidence=fx.get("model_confidence", 0.6),
                liquidity=fx.get("liquidity", 0.0), hours_to_kickoff=fx.get("hours_to_kickoff", 24.0),
            )
            scored.append({"id": cid, "score": opp, "edge": c.edge, "price_cents": c.price_cents,
                           "has_sharp": c.has_sharp})
            meta[cid] = (
                c, fx, rationales.get(c.market_type, ""),
                (model_only.get(c.market_type, {})).get(c.side),
                (sharp.get(c.market_type, {})).get(c.side),
                adjustments.get(c.market_type), opp,
            )

    allocations = allocate(
        scored, bankroll_cents=getattr(caps, "bankroll_cents", 0), caps=caps,
        reserve_frac=reserve_frac, expected_better_soon=expected_better_soon,
    )
    placed = {a["id"]: a for a in allocations}

    decisions = []
    for cid, (c, fx, rationale, model_p, sharp_p, adj, opp) in meta.items():
        a = placed.get(cid)
        _eg = fx.get("expected_goals")
        decisions.append(decision_doc(
            instance_id=instance_id, brokerage_id=brokerage_id, ts=ts,
            fixture_id=fx["fixture_id"], market_ticker=c.market_ticker, side=c.side,
            model_prob=model_p, sharp_prob=sharp_p, llm_adjustment=adj, llm_rationale=rationale,
            fused_fair=c.fair, edge=c.edge, size=(a["contracts"] if a else 0),
            opportunity_score=opp, decision=("placed" if a else "skipped"),
            league=fx.get("league", ""),
            # CLV reference: the sharp book's prob at entry — reconcile grades whether
            # we bought below sharp fair (the only real, sharp-anchored CLV grade).
            sharp_close_prob=sharp_p,
            entry_avg_cents=(a["price_cents"] if a else None),
            home_elo=fx.get("home_elo"), away_elo=fx.get("away_elo"),
            home_xg=(_eg[0] if (_eg and len(_eg) > 0) else None),
            away_xg=(_eg[1] if (_eg and len(_eg) > 1) else None),
        ))
    return {"allocations": allocations, "decisions": decisions + skip_decisions, "candidates": scored}
