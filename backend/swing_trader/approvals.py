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

G8a ruling 2: the option positions are read the same way, through
account.option_book. AlpacaAdapter.list_option_positions never raises; after
an outage or while a contract's fields are unreadable it returns a partial
map, so an incomplete or stale map refuses the approval like an unreadable
order book.

Seams I-1: an approved swing entry spends from the lane's own budget
(account.swing_live_budget), so it can never spend the cash that secures the
wheel's short puts. An option book that cannot be read is
OptionBookUnreadable, and the approval goes back to pending.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from swing_trader import clock, wheel_rules
from swing_trader.account import latest_trade_price, option_book, swing_live_budget
from swing_trader.constants import POSITION_SIZE_PCT, PROFIT_TARGET, STOP_LOSS

DECISION_STATUS = {"approve": "approved", "approve_half": "approved_half",
                   "reject": "rejected"}
APPROVED_STATUSES = ("approved", "approved_half")


class SignalConflict(ValueError):
    """A decision on a signal that is not pending (already decided, submitted
    or failed). api/main.py:_run maps it to 400. A click that loses the
    compare-and-swap is the route's 409 (interactive_utils.SwingDecisionRaceError)."""


class BookUnreadable(ValueError):
    """fix F3: the broker's working-order book could not be read, or (G8a
    ruling 2) its option map is incomplete or stale, so a wheel approval
    cannot rule out a duplicate put. A-live's approval handler resets the
    signal to pending (G5 ruling), so the operator can approve again once
    the book is whole."""


class OptionBookUnreadable(BookUnreadable):
    """Seams I-1: the option book is unreadable, incomplete, stale or holds a
    short whose collateral is unknown, so a swing entry cannot tell how much
    cash secures the short puts. Transient, like BookUnreadable: the
    approval handler puts the signal back to pending ("option book
    unreadable — approve again")."""


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


def _working_orders(adapter, lane="wheel") -> list:
    """fix F3: the working-order book, or BookUnreadable. The strict reader
    when the adapter has one (AlpacaAdapter.list_open_orders_strict), else
    its list_open_orders; either way an error is a refusal, never []."""
    try:
        reader = getattr(adapter, "list_open_orders_strict", None) or adapter.list_open_orders
        orders = reader()
    except Exception as exc:
        raise BookUnreadable(
            f"the broker's working-order book is unreadable ({type(exc).__name__}: "
            f"{exc}); refusing the {lane} approval rather than reading it as empty") from exc
    if not isinstance(orders, (list, tuple)):
        raise BookUnreadable(f"the broker's working-order book is unreadable (got "
                             f"{type(orders).__name__}, not a list)")
    return list(orders)


def _option_positions(adapter) -> list:
    """G8a ruling 2: the option positions, or BookUnreadable when the map is
    unreadable, incomplete or stale (account.option_book). A partial map
    would hide an open put and let a duplicate through."""
    rows, not_ready = option_book(adapter)
    if rows is None or not_ready is not None:
        raise BookUnreadable(
            f"the broker's option positions are not a complete, current map ({not_ready}); "
            "refusing the wheel approval rather than reading a partial book")
    return rows


def _swing_budget(adapter) -> float:
    """Seams I-1: what an approved swing entry may spend -- the budget the
    lane's own entries use (account.swing_live_budget): buying power with no
    short put open, else the smaller of buying power and cash less the
    collateral of every open short put and working sell-to-open put. An
    unreadable working-order book is BookUnreadable; an option book that
    cannot be read as a complete, current map is OptionBookUnreadable."""
    if adapter is None:
        raise ValueError("a swing approval needs the broker adapter")
    book = _working_orders(adapter, lane="swing")
    budget, _collateral, reason = swing_live_budget(adapter, book)
    if budget is None:
        raise OptionBookUnreadable(
            f"the cash securing short puts cannot be read ({reason}); refusing the "
            "swing approval rather than spending it")
    # Final-round I-R1: a stock buy still working (a pre-market entry queued
    # for the open) has not left cash yet, so it comes off the budget too --
    # otherwise each approval re-spends the same cash, and the puts' collateral.
    return max(0.0, float(budget) - _working_stock_buys(adapter, book))


def _working_stock_buys(adapter, book) -> float:
    """The unfilled notional of every working stock BUY in `book`: its limit
    price when it has one, else the latest trade. A buy that cannot be
    priced is BookUnreadable (transient: the approval returns to pending)."""
    total = 0.0
    for order in book:
        if str(getattr(order, "side", "") or "").lower() != "buy":
            continue
        if str(getattr(order, "asset_class", None) or "us_equity").lower() != "us_equity":
            continue
        remaining = float(getattr(order, "qty", 0) or 0) - float(getattr(order, "filled_qty", 0) or 0)
        if remaining <= 0:
            continue
        price = getattr(order, "limit_price", None) or latest_trade_price(adapter, order.symbol)
        if not price or float(price) <= 0:
            raise BookUnreadable(
                f"the working buy for {order.symbol} cannot be priced; refusing the swing "
                "approval rather than spending cash that order already holds")
        total += remaining * float(price)
    return total


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

        # Seams I-1: never more than the lane's budget buys at the live price,
        # so the cash securing the wheel's short puts stays unspent.
        budget = _swing_budget(adapter)
        affordable = int(budget // live_price)
        if affordable < 1:
            raise ValueError(f"the swing budget ${budget:,.2f} buys no whole share of "
                             f"{signal.get('symbol')} at ${live_price:,.2f}")
        shares = min(shares, affordable)
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
        option_positions = _option_positions(adapter)  # G8a ruling 2: fail closed
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
