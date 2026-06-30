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


def close_ref_updates(placed_rows, sharp_map, mid_map) -> list[dict]:
    """For each OPEN (outcome is None) placed decision, return the rolling closing
    reference to stamp: latest sharp prob (-> sharp_close_prob, the GRADED CLV
    reference) and latest Kalshi mid (-> pre_settle_mid_cents). At settlement the
    LAST value stamped is the close, so reconcile grades true CLV instead of using a
    stale entry-time reference. Skips settled/non-placed rows and rows with no current
    data. Pure (no DB)."""
    sharp_map = sharp_map or {}
    mid_map = mid_map or {}
    out = []
    for r in placed_rows or []:
        if r.get("decision") != "placed" or r.get("outcome") is not None:
            continue
        tk = r.get("market_ticker")
        upd = {}
        if sharp_map.get(tk) is not None:
            upd["sharp_close_prob"] = float(sharp_map[tk])
        if mid_map.get(tk) is not None:
            upd["pre_settle_mid_cents"] = int(round(float(mid_map[tk])))
        if upd:
            out.append({"id": r.get("id"), **upd})
    return out


def _hours_between(ts_iso, now_iso) -> float | None:
    """Hours from ts_iso to now_iso (ISO-8601, 'Z' or offset). None if unparseable."""
    import datetime
    def _p(s):
        if not s:
            return None
        try:
            d = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        return d.replace(tzinfo=datetime.timezone.utc) if d.tzinfo is None else d
    a, b = _p(ts_iso), _p(now_iso)
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600.0


def _last_seen_age_hours(row, now_iso) -> float | None:
    """Hours since a decision row was last seen OPEN. Skipped rows refresh their `ts`
    every tick (stable id, re-inserted), so `ts` tracks last-seen-open for them. PLACED
    paper rows have a FROZEN `ts` (written once, then deduped), so their last-seen-open
    is `mark_ts` (mark_paper_positions refreshes it every tick the market still has a
    live price). Use the MOST RECENT of the two (smallest age). None if neither parses."""
    ages = [h for h in (_hours_between(row.get("ts"), now_iso),
                        _hours_between(row.get("mark_ts"), now_iso)) if h is not None]
    return min(ages) if ages else None


def prune_finished_decisions(rows, open_tickers, now_iso, *,
                             delete_after_hours: float = 3.0,
                             expire_after_hours: float = 12.0) -> dict:
    """Decisions whose market is no longer in Kalshi's OPEN set and that haven't been
    seen open for a while are from FINISHED games. Returns {"delete":[ids],"expire":[ids]}:
      - skipped/blocked/queued, not seen open for >= delete_after_hours -> DELETE (clears
        the pregame board)
      - still-open PLACED paper trade, not seen open for >= expire_after_hours -> EXPIRE
        (stop showing as open; longer window so settle_and_learn can grade it first)
      - already-settled placed rows -> kept (P&L history)
      - recently-seen rows -> kept (guards a transient/partial discovery gap)
    NEVER acts when open_tickers is empty (a discovery outage must not mass-delete the
    log). Age is measured from the most recent of ts/mark_ts (see _last_seen_age_hours).
    Pure (no DB)."""
    open_set = set(open_tickers or [])
    out = {"delete": [], "expire": []}
    if not open_set:
        return out
    for r in rows or []:
        if r.get("market_ticker") in open_set:
            continue
        age = _last_seen_age_hours(r, now_iso)
        if age is None:
            continue
        if r.get("decision") == "placed":
            if r.get("outcome") is None and age >= expire_after_hours:
                # Realize at the LAST mark: the unrealized P&L the engine stamped each
                # tick becomes the realized P&L (best estimate when Kalshi never settled
                # it), so the dashboard's realized total + filled-orders history keep it.
                out["expire"].append({"id": r.get("id"),
                                      "realized_pnl_cents": int(r.get("unrealized_pnl_cents") or 0)})
        elif age >= delete_after_hours:
            out["delete"].append(r.get("id"))
    return out


def paper_cash_state(rows, bankroll_cents) -> dict:
    """Paper portfolio cash from kalshi_decisions rows. Open paper positions tie up
    cost (size*entry); settled/expired ones credit realized P&L. available = bankroll
    + realized - open_cost (clamped >=0). Lets the engine size paper buys against the
    cash actually left, so total deployed never exceeds the (paper) account. Pure."""
    open_cost = realized = open_count = 0
    for r in rows or []:
        if not (r.get("paper") and r.get("decision") == "placed"):
            continue
        if r.get("outcome") is None:
            size = int(r.get("size") or 0)
            entry = r.get("entry_avg_cents")
            if size > 0 and entry is not None:
                open_cost += size * int(entry)
                open_count += 1
        else:
            realized += int(r.get("realized_pnl_cents") or 0)
    available = int(bankroll_cents or 0) + realized - open_cost
    return {"open_cost_cents": open_cost, "realized_cents": realized,
            "available_cents": max(0, available), "open_count": open_count}


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
