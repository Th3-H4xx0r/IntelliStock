"""Approval state machine and order rebuild, ported from ST app.py.

    app.py:982-1073   api_approve: the stored prices are stale at approval, so
                      the bracket legs and the share count are recomputed at
                      the live price ("reduce" became approve_half)
    app.py:888-940    api_approve_wheel: place_put_order on the stored
                      candidate, whose expiry is now recomputed (fix 2)
    app.py:1076-1108  api_reject
A decision is final: pending -> approved | approved_half | rejected. The
compare-and-swap that makes it final against a concurrent click is
signals_store.cas_signal; the order goes out through the broker's
submit_order LiveCommand (plan A-live's _execute_swing_approval in broker.py).

fix F3 (pre-flight ruling): the wheel approval's duplicate check reads the
working-order book through the adapter's STRICT reader and refuses when the
book cannot be read. AlpacaAdapter.list_open_orders answers an outage with
[], which would read as "no working put" and let a duplicate through.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from swing_trader import clock, wheel_rules
from swing_trader.constants import POSITION_SIZE_PCT, PROFIT_TARGET, STOP_LOSS

DECISION_STATUS = {"approve": "approved", "approve_half": "approved_half",
                   "reject": "rejected"}
APPROVED_STATUSES = ("approved", "approved_half")


class SignalConflict(ValueError):
    """A decision on a signal that is not pending (already decided, submitted
    or failed). api/main.py:_run maps it to 400. A click that loses the
    compare-and-swap is the route's 409 (interactive_utils.SwingDecisionRaceError)."""


class BookUnreadable(ValueError):
    """fix F3: the broker's working-order book could not be read, so a wheel
    approval cannot rule out a duplicate put. A-live's approval handler turns
    it, like every refusal here, into status "failed"."""


def decide(signal: dict, decision: str, user, reason, now_iso: str) -> dict:
    status = DECISION_STATUS.get(str(decision or "").strip().lower())
    if status is None:
        raise ValueError(f"decision must be one of {sorted(DECISION_STATUS)}, "
                         f"got {decision!r}")
    current = str((signal or {}).get("status") or "")
    if current != "pending":
        raise SignalConflict(f"signal {(signal or {}).get('id')} is "
                             f"{current or 'missing'}, not pending — decisions are final")
    out = dict(signal)
    out.update({"status": status, "decided_by": (str(user) if user else None),
                "decided_at": now_iso,
                "decision_reason": (str(reason).strip()[:500] or None) if reason else None})
    return out


def _working_orders(adapter) -> list:
    """fix F3: the working-order book, or BookUnreadable. The strict reader
    when the adapter has one (AlpacaAdapter.list_open_orders_strict), else
    its list_open_orders; either way an error is a refusal, never []."""
    try:
        reader = getattr(adapter, "list_open_orders_strict", None) or adapter.list_open_orders
        orders = reader()
    except Exception as exc:
        raise BookUnreadable(
            f"the broker's working-order book is unreadable ({type(exc).__name__}: "
            f"{exc}); refusing the wheel approval rather than reading it as empty") from exc
    if not isinstance(orders, (list, tuple)):
        raise BookUnreadable(f"the broker's working-order book is unreadable (got "
                             f"{type(orders).__name__}, not a list)")
    return list(orders)


def build_approved_order(signal: dict, *, live_price, equity, cfg, adapter=None,
                         today=None) -> dict:
    lane = (signal or {}).get("lane")
    half = signal.get("status") == "approved_half"
    cfg = cfg or {}

    if lane == "swing":
        live_price = float(live_price or 0.0)
        if not live_price > 0:
            raise ValueError(f"no live price for {signal.get('symbol')}")
        stop_loss = float(cfg.get("stop_loss", STOP_LOSS))
        profit_target = float(cfg.get("profit_target", PROFIT_TARGET))
        size_pct = float(cfg.get("position_size_pct", POSITION_SIZE_PCT))

        # Recalculate bracket legs from live price using strategy percentages
        stop_price   = round(live_price * (1 - stop_loss),    2)
        target_price = round(live_price * (1 + profit_target), 2)

        stored_adj = float(signal.get("size_adjustment") or 1.0)
        base_shares = max(1, int((float(equity) * size_pct) / live_price))
        # Apply AI position_size_adjustment, then halve again if the operator
        # chose approve_half (ST: the REDUCE button)
        adj_shares  = max(1, int(base_shares * stored_adj))
        shares      = max(1, adj_shares // 2) if half else adj_shares

        # Sanity-check recalculated prices
        if stop_price >= live_price - 0.01:
            raise ValueError(f"stop_price ${stop_price:.4f} >= live_price "
                             f"${live_price:.4f} − 0.01 after recalc")
        if target_price <= live_price + 0.01:
            raise ValueError(f"target_price ${target_price:.4f} <= live_price "
                             f"${live_price:.4f} + 0.01 after recalc")
        return {"kind": "equity_bracket", "symbol": signal["symbol"], "qty": shares,
                "take_profit_price": target_price, "stop_loss_price": stop_price}

    if lane == "wheel":
        if adapter is None:
            raise ValueError("a wheel approval needs the broker adapter")
        today = today or date.fromisoformat(clock.ny_date(datetime.now(timezone.utc)))
        # fix 2: ST placed the order on the stored candidate's expiry, which is
        # in the past once a review waits into the next week.
        expiry = wheel_rules.next_friday(today, clock.trading_days)
        proposal = dict(signal.get("proposal") or {})
        qty = int(proposal.get("qty") or 1)
        if half:
            qty = max(1, qty // 2)
        candidate = {"symbol": signal["symbol"], "stock_price": float(live_price or 0.0),
                     "strike_price": float(proposal["strike"]), "expiry": expiry,
                     "est_premium": float(proposal.get("premium_est") or 0.0),
                     "position_size_contracts": qty}
        option_positions = list(adapter.list_option_positions() or [])
        open_orders = _working_orders(adapter)  # fix F3: fail closed
        duplicate = wheel_rules.duplicate_put_reason(signal["symbol"], option_positions,
                                                     open_orders)
        if duplicate:
            raise ValueError(duplicate)
        account = adapter.get_account_options() or {}
        order, error, _meta = wheel_rules.build_put_order_live(
            candidate, adapter=adapter, cfg=cfg, equity=float(equity),
            cash=float(account.get("cash") or 0.0), option_positions=option_positions,
            open_orders=open_orders, signal_id=signal.get("id"),
            session=today.isoformat())
        if order is None:
            raise ValueError(error or "no order could be built")
        return {"kind": "option", **order}

    raise ValueError(f"unknown lane {lane!r}")
