"""Feedback loop (PURE — no DB/network, unit-tested with plain dicts).

This is the loop the bot never had. `kalshi_decisions.realized_pnl_cents/outcome/clv`
were ALWAYS None, so the strategy couldn't see or learn from its own results. These
helpers let it MEASURE realized P&L and closing-line value (CLV) per settled market
and decide — statistically, NO-GO by default — whether it has proven edge on paper
before any real money is risked.

Key correctness property (the adversarial review's B2): reconcile at the POSITION
level. Multiple `placed` rows for one market (re-entries/adds) collapse into ONE
cost-weighted net position so a single settlement isn't double-counted across rows.
"""
from __future__ import annotations

import math
from collections import defaultdict


def validate_order(contracts, limit_cents, *, max_per_market: int = 100000) -> tuple[bool, str]:
    """Pre-trade param check. Reject before hitting the API (avoids the observed
    400 invalid_parameters failures)."""
    try:
        c = int(contracts)
        p = int(limit_cents)
    except (TypeError, ValueError):
        return False, "non-integer order params"
    if c < 1:
        return False, f"contracts < 1 ({c})"
    if c > max_per_market:
        return False, f"contracts > max_per_market ({c} > {max_per_market})"
    if p < 1 or p > 99:
        return False, f"limit {p}c out of [1,99]"
    return True, ""


def _entry_cents(row, fee_rate: float) -> float | None:
    """Best available entry price for a placed row: a recorded fill avg if present,
    else reconstructed from fused_fair - edge (the ask the bot bought at)."""
    ev = row.get("entry_avg_cents")
    if ev is not None:
        try:
            return float(ev)
        except (TypeError, ValueError):
            pass
    ff, e = row.get("fused_fair"), row.get("edge")
    if ff is not None and e is not None:
        # edge = fair - (ask/100 + fee) -> ask = (fair - edge - fee)*100. Subtract the
        # fee the edge already netted, else the reconstructed entry is overstated ~1-2c.
        from kalshi.fees import fee_as_prob
        ask0 = (float(ff) - float(e)) * 100.0
        return round(ask0 - fee_as_prob(max(1.0, ask0), fee_rate) * 100.0)
    return None


def aggregate_positions(decision_rows, *, fee_rate: float = 0.07) -> list[dict]:
    """Collapse all PLACED rows per (instance_id, market_ticker) into ONE
    cost-weighted net position. Prevents the double-count where one settlement
    fans out across 6 placed rows for the same market."""
    groups: dict = defaultdict(lambda: {"contracts": 0, "cost_cents": 0.0, "ids": []})
    for r in decision_rows:
        if r.get("decision") != "placed":
            continue
        # live exit/reduce rows are SELLS written as decision='placed' — never count
        # them as buys (that would inflate the net position's contracts and cost).
        if r.get("live_action") in ("exit", "reduce"):
            continue
        size = int(r.get("size") or 0)
        entry = _entry_cents(r, fee_rate)
        if size <= 0 or entry is None:
            continue
        key = (r.get("instance_id"), r.get("market_ticker"))
        g = groups[key]
        g["contracts"] += size
        g["cost_cents"] += size * float(entry)
        g["ids"].append(r.get("id"))
    out = []
    for (inst, tk), g in groups.items():
        if g["contracts"] <= 0:
            continue
        out.append({
            "instance_id": inst,
            "market_ticker": tk,
            "contracts": g["contracts"],
            "avg_entry_cents": g["cost_cents"] / g["contracts"],
            "cost_cents": g["cost_cents"],
            "decision_ids": g["ids"],
        })
    return out


def _fee_cents(contracts: int, price_cents: float, fee_rate: float = 0.07) -> int:
    p = max(0.01, min(0.99, price_cents / 100.0))
    return math.ceil(fee_rate * contracts * p * (1.0 - p))


def reconcile_position(position, *, result: str, close_cents=None,
                       sharp_close_prob=None, fee_rate: float = 0.07) -> dict:
    """One settled position -> {outcome, realized_pnl_cents, clv, clv_graded}.
    `result`: 'yes' if the YES side won. CLV uses the SHARP book close when given
    (the only real CLV grade — Kalshi is not sharp), else the last Kalshi mid, else
    the settlement (100/0)."""
    won = (result == "yes")
    C = int(position["contracts"])
    avg = float(position["avg_entry_cents"])
    settle = C * 100 if won else 0
    fee = _fee_cents(C, avg, fee_rate)
    realized = settle - round(C * avg) - fee
    if sharp_close_prob is not None:
        close = float(sharp_close_prob) * 100.0
        graded = True
    elif close_cents is not None:
        close = float(close_cents)
        graded = False   # Kalshi mid is not a sharp reference -> not a real CLV grade
    else:
        close = 100.0 if won else 0.0
        graded = False
    clv = (close - avg) / 100.0   # positive => we entered cheaper than the close
    return {
        "instance_id": position.get("instance_id"),
        "market_ticker": position["market_ticker"],
        "contracts": C,
        "avg_entry_cents": avg,
        "outcome": "win" if won else "loss",
        "realized_pnl_cents": int(realized),
        "clv": round(clv, 4),
        "clv_graded": bool(graded),
        "decision_ids": position.get("decision_ids", []),
    }


def calibration_summary(reconciled) -> dict:
    """Roll up reconciled positions: realized P&L, EV/$ deployed, and avg CLV over
    the GRADED (vs-sharp) subset."""
    n = len(reconciled)
    graded = [r for r in reconciled if r.get("clv_graded")]
    avg_clv = (sum(r["clv"] for r in graded) / len(graded)) if graded else None
    cost = sum(round(r["contracts"] * r["avg_entry_cents"]) for r in reconciled)
    pnl = sum(r["realized_pnl_cents"] for r in reconciled)
    wins = sum(1 for r in reconciled if r["outcome"] == "win")
    return {
        "n": n,
        "graded_n": len(graded),
        "wins": wins,
        "avg_clv": (round(avg_clv, 4) if avg_clv is not None else None),
        "realized_pnl_cents": pnl,
        "ev_per_dollar": (pnl / cost) if cost else None,
    }


def go_live_ready(reconciled, *, min_graded: int = 100,
                  clv_threshold: float = 0.0, z: float = 1.645) -> dict:
    """The statistical GO-LIVE gate. NO-GO by default. Requires the 95% one-sided
    lower-confidence-bound of mean CLV (vs the SHARP book) to clear a POSITIVE
    threshold over >= min_graded graded bets. A bare point estimate (avg CLV > 0)
    green-lights a losing strategy ~40% of the time, so we use the LCB instead.
    Returns a dict; flipping live_enabled stays a MANUAL operator action."""
    graded = [r["clv"] for r in reconciled if r.get("clv_graded")]
    ng = len(graded)
    if ng < min_graded:
        return {"ready": False, "graded_n": ng,
                "reason": f"insufficient graded bets ({ng}/{min_graded})"}
    mean = sum(graded) / ng
    var = sum((x - mean) ** 2 for x in graded) / (ng - 1) if ng > 1 else 0.0
    se = math.sqrt(var / ng) if ng > 0 else float("inf")
    lcb = mean - z * se
    ready = lcb > clv_threshold
    return {
        "ready": bool(ready),
        "graded_n": ng,
        "mean_clv": round(mean, 4),
        "lcb_clv": round(lcb, 4),
        "threshold": clv_threshold,
        "reason": "" if ready else f"CLV lower-bound {lcb:.4f} <= threshold {clv_threshold}",
    }
