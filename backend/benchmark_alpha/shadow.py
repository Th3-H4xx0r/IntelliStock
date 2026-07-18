"""Quote-driven virtual shadow portfolio (Task 9A).

Maintains virtual cash, positions, fills, rejections, and equity against
recorded quotes and the registered cost model. Buys pay the ask, sells hit
the bid, every fill carries modeled costs. There is deliberately NO broker
submission surface of any kind — SHADOW_PORTFOLIO evidence must be
impossible to contaminate with real orders (adversarial finding B08).
"""
from dataclasses import dataclass

MIN_TRADE_NOTIONAL = 1.0


@dataclass(frozen=True)
class ShadowCycle:
    fills: tuple
    rejected: tuple
    equity: float
    cash: float
    positions: dict


class ShadowPortfolio:
    def __init__(self, cash):
        self.cash = float(cash)
        self.positions = {}
        self.history = []

    def equity(self, quotes):
        total = self.cash
        for sym, qty in self.positions.items():
            quote = (quotes or {}).get(sym) or {}
            bid, ask = quote.get("bid"), quote.get("ask")
            if bid and ask:
                total += qty * (float(bid) + float(ask)) / 2.0
        return total

    def apply(self, targets, quotes, cost_model, now):
        """Advance one cycle toward ``targets`` (symbol -> weight of equity).

        Symbols without a two-sided quote are REJECTED, never filled at an
        invented price. Sells execute before buys so funding is real."""
        quotes = quotes or {}
        equity = self.equity(quotes)
        fills = []
        rejected = []

        deltas = {}
        symbols = set(targets) | set(self.positions)
        for sym in sorted(symbols):
            target_weight = float((targets or {}).get(sym, 0.0) or 0.0)
            quote = quotes.get(sym) or {}
            bid, ask = quote.get("bid"), quote.get("ask")
            if not bid or not ask:
                if target_weight > 0 or sym in self.positions:
                    rejected.append({"symbol": sym, "reason": "no_quote"})
                continue
            mid = (float(bid) + float(ask)) / 2.0
            current_notional = self.positions.get(sym, 0.0) * mid
            deltas[sym] = (target_weight * equity - current_notional, quote)

        # Reductions first.
        for sym, (delta, quote) in sorted(deltas.items()):
            if delta >= -MIN_TRADE_NOTIONAL:
                continue
            bid = float(quote["bid"])
            sell_qty = min(self.positions.get(sym, 0.0), -delta / bid)
            if sell_qty <= 0:
                continue
            notional = sell_qty * bid
            cost = cost_model.estimate(
                side="sell", notional=notional, decision_quote=quote,
                submit_quote=quote, adv_notional=None).total_cost
            self.positions[sym] = self.positions.get(sym, 0.0) - sell_qty
            if self.positions[sym] <= 1e-12:
                self.positions.pop(sym, None)
            self.cash += notional - cost
            fills.append({"symbol": sym, "side": "sell", "qty": sell_qty,
                          "price": bid, "cost": cost})

        # Then affordable increases.
        for sym, (delta, quote) in sorted(deltas.items()):
            if delta <= MIN_TRADE_NOTIONAL:
                continue
            ask = float(quote["ask"])
            notional = min(delta, max(0.0, self.cash))
            if notional <= MIN_TRADE_NOTIONAL:
                rejected.append({"symbol": sym, "reason": "insufficient_cash"})
                continue
            cost = cost_model.estimate(
                side="buy", notional=notional, decision_quote=quote,
                submit_quote=quote, adv_notional=None).total_cost
            qty = notional / ask
            self.positions[sym] = self.positions.get(sym, 0.0) + qty
            self.cash -= notional + cost
            fills.append({"symbol": sym, "side": "buy", "qty": qty,
                          "price": ask, "cost": cost})

        cycle = ShadowCycle(
            fills=tuple(fills), rejected=tuple(rejected),
            equity=self.equity(quotes), cash=self.cash,
            positions=dict(self.positions))
        self.history.append(cycle)
        return cycle
