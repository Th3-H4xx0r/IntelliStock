"""Live monitor step (integration glue). Thin: it wires the pure live modules
(event_detect, live_fair, live_decision) to the Kalshi client + decision log. All
math lives in the tested helpers; this only polls prices, executes gated orders,
and builds decision rows. `dry_run`, `client`, and `llm` are injected so it's
testable with fakes (no network)."""
from __future__ import annotations

from kalshi.decisions import decision_doc
from kalshi.live.event_detect import detect_move
from kalshi.live.live_decision import decide
from kalshi.live.live_fair import live_fair
from kalshi.live.match_clock import LIVE


def _implied(cents) -> float:
    return max(0.0, min(1.0, (float(cents) if cents is not None else 0.0) / 100.0))


def _noop(*_a, **_k):
    pass


def run_live_step(*, client, live_matches, price_history, adds_by_match,
                  positions_by_ticker, caps, dry_run, instance_id, brokerage_id, ts,
                  llm=None, log=_noop, move_threshold_cents: float = 8.0,
                  max_history: int = 10) -> list[dict]:
    """For each live match's markets: update the rolling mid history, detect a
    material move, (on a move) get a bounded LLM tilt, compute hybrid live fair,
    decide open/add/reduce/exit/hold, execute (unless dry_run), and emit a
    LIVE-tagged decision row. Returns the rows; the caller persists them.

    live_matches: list of {fixture_id, home, away, phase, elapsed_min,
      model_probs:{market_ticker:prob}, markets:[{market_ticker, side, market_type,
      yes_ask_cents, yes_bid_cents, mid_cents}]}.
    price_history / adds_by_match: mutable dicts persisted across ticks by the engine.
    positions_by_ticker: {market_ticker: KalshiContractPosition}.
    llm(match, market, move) -> tilt float in [-cap,cap] (optional)."""
    rows: list[dict] = []
    for match in live_matches or []:
        fixture_id = match.get("fixture_id", "")
        phase = match.get("phase", LIVE)
        elapsed = match.get("elapsed_min")
        model_probs = match.get("model_probs") or {}
        for mk in match.get("markets", []):
            ticker = mk.get("market_ticker")
            if not ticker:
                continue
            mid = mk.get("mid_cents")
            if mid is None:
                a, b = mk.get("yes_ask_cents"), mk.get("yes_bid_cents")
                mid = (float(a) + float(b)) / 2.0 if (a and b) else (a or b)
            hist = price_history.setdefault(ticker, [])
            if mid is not None:
                hist.append(float(mid))
                if len(hist) > max_history:
                    del hist[:-max_history]
            move = detect_move(hist, threshold_cents=move_threshold_cents)

            tilt = 0.0
            if move.moved and llm is not None:
                try:
                    tilt = float(llm(match, mk, move) or 0.0)
                except Exception as e:
                    log(f"live LLM tilt failed for {ticker}: {type(e).__name__}: {e}", "yellow")

            mkt_prob = _implied(mid if mid is not None else mk.get("yes_ask_cents"))
            prematch = model_probs.get(ticker)
            fair = live_fair(mkt_prob, prematch, elapsed, tilt)

            pos = positions_by_ticker.get(ticker)
            # Open/add freely only when the clock CONFIRMS live (precise kickoff).
            # When the clock is unknown (same-day, no kickoff time), a material
            # price move is the only Kalshi-price-only evidence the match is
            # actually in play — so opens require a move. Exits are always allowed.
            allow_open = (phase == LIVE and elapsed is not None) or move.moved
            action = decide(
                position=pos, live_fair=fair,
                yes_ask_cents=mk.get("yes_ask_cents") or 0,
                yes_bid_cents=mk.get("yes_bid_cents") or 0,
                caps=caps, phase=phase, elapsed_min=elapsed,
                adds_so_far=int(adds_by_match.get(fixture_id, 0)),
                allow_open=allow_open,
            )

            executed = "skipped"
            if action.contracts > 0 and action.kind in ("open", "add", "exit", "reduce"):
                if dry_run:
                    executed = "skipped"
                    log(f"live DRY-RUN {action.kind} {action.contracts}x {ticker} @ "
                        f"fair {fair:.2f} ({action.reason}).", "cyan")
                else:
                    is_buy = action.kind in ("open", "add")
                    # Guard BOTH sides: never buy with no ask / never sell into no bid
                    # (a 0c limit sell would dump the whole position for nothing).
                    price = int(mk.get("yes_ask_cents") or 0) if is_buy else int(mk.get("yes_bid_cents") or 0)
                    if price <= 0:
                        executed = "blocked"
                        log(f"live {action.kind} {ticker}: no {'ask' if is_buy else 'bid'} to "
                            f"{'buy' if is_buy else 'sell'} into; skipping.", "yellow")
                    else:
                        try:
                            client.submit_order(
                                market_ticker=ticker, side="yes",
                                action="buy" if is_buy else "sell",
                                contracts=action.contracts, limit_cents=price,
                                client_order_id=f"{instance_id}-live{action.kind}-{ticker}-{ts}",
                            )
                            executed = "placed"
                            if action.kind == "add":
                                adds_by_match[fixture_id] = int(adds_by_match.get(fixture_id, 0)) + 1
                            log(f"live {action.kind.upper()} {action.contracts}x {ticker} @ {price}c "
                                f"({action.reason}).", "green")
                        except Exception as e:
                            executed = "blocked"
                            log(f"live {action.kind} {ticker} failed: {type(e).__name__}: {e}", "red")

            row = decision_doc(
                instance_id=instance_id, brokerage_id=brokerage_id, ts=ts,
                fixture_id=fixture_id, market_ticker=ticker, side=mk.get("side", "yes"),
                model_prob=prematch, fused_fair=fair, edge=(fair - mkt_prob),
                llm_adjustment=(tilt or None),
                llm_rationale=action.reason,
                size=action.contracts,
                decision=("placed" if executed == "placed"
                          else ("blocked" if executed == "blocked" else "skipped")),
                block_reason=(action.reason if action.kind == "hold" else ""),
            )
            row["in_play"] = True
            row["live_action"] = action.kind
            row["elapsed_min"] = elapsed
            row["event"] = move.direction if move.moved else ""
            rows.append(row)
    return rows
