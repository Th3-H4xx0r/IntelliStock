"""Binance.US spot broker adapter (paper + live).

Implements the BrokerAdapter contract over BinanceUSClient. Two modes:

- **paper** (paper=True): NO real orders. Fills are simulated against the LIVE
  Binance.US price with the real 0.00%/0.02% maker/taker fee model, tracking
  cash/positions/trades locally exactly like PortfolioEmulator. Safe to run with
  read-only or no credentials (price is a public endpoint).
- **live** (paper=False): real money. MARKET orders via signed REST; positions
  and cash are seeded/reconciled from the account's balances. Every submit is
  recorded in the LiveOrderWAL (crash-safe intent) with a deterministic client
  order id before the HTTP call.

Emulator-compatible private attrs (_positions/_cash/_trades/_initial_value/
_last_prices) are populated so strategy pass-through works unchanged. Crypto is
24/7 so is_market_open is always True.

CAVEAT: live real-money mode is implemented but has NOT been exercised against a
funded Binance.US account here — validate in paper, then with a tiny live order,
before trusting it with size.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from broker_adapters.base import (
    BrokerAdapter, OrderRef, PositionDTO, CashDTO, AccountDTO, HealthStatus,
)
from broker_adapters.binanceus_client import (
    BinanceUSClient, BINANCE_US_FEES, to_binance, from_binance, BinanceUSError,
)

try:
    from broker_adapters._client_order_id import make_client_order_id
except Exception:  # pragma: no cover - keep import-safe if the helper moves
    def make_client_order_id(instance_id, symbol, bar_iso, side, retry_n):
        import uuid
        return f"bnus-{uuid.uuid4().hex[:20]}"

# Assets that count as spendable "cash" (USD-equivalents) on Binance.US.
_CASH_ASSETS = ("USD", "USDT", "USDC", "USD4", "BUSD")


def _now():
    return datetime.now(timezone.utc)


class BinanceUSAdapter(BrokerAdapter):
    def __init__(self, *, api_key: str = "", api_secret: str = "", paper: bool = True,
                 instance_id: str = "", wal: Any = None, initial_value: Optional[float] = None,
                 cid_prefix: Optional[str] = None, **_ignored):
        self.paper = bool(paper)
        self.instance_id = instance_id or ""
        self._wal = wal
        self._cid_prefix = cid_prefix or "bnus"
        self._client = BinanceUSClient(api_key, api_secret)
        self._taker = float(BINANCE_US_FEES["taker"])

        self._positions: dict[str, float] = {}
        self._trades: list[dict] = []
        self._last_prices: dict[str, float] = {}
        self._external_positions: dict[str, dict] = {}
        self._crypto_fees_paid = 0.0
        self._crypto_volume = 0.0
        self._portfolio_snapshots: list[dict] = []

        init = float(initial_value) if initial_value is not None else 100000.0
        if self.paper:
            self._cash = init
            self._initial_value = init
        else:
            try:
                self._sync_from_broker()
            except Exception:
                self._cash = 0.0
            self._initial_value = self.get_portfolio_value(self._last_prices) or init

    # ---- price / market data -------------------------------------------
    def _price(self, ticker: str) -> Optional[float]:
        try:
            p = self._client.price(ticker)
            if p and p > 0:
                self._last_prices[ticker] = p
            return p
        except Exception:
            return self._last_prices.get(ticker)

    def _sync_from_broker(self) -> None:
        """Live: seed cash + crypto positions from the account balances."""
        bals = self._client.balances()
        cash = 0.0
        pos: dict[str, float] = {}
        for asset, qty in bals.items():
            if asset in _CASH_ASSETS:
                cash += float(qty)
            elif float(qty) > 0:
                pos[from_binance(asset + "USD")] = float(qty)  # asset -> "ASSET/USD"
        self._cash = cash
        self._positions = pos

    # ---- fill accounting (shared) --------------------------------------
    def _record_trade(self, action: str, ticker: str, shares: float, price: float, total: float):
        self._trades.append({
            "timestamp": _now().isoformat(), "action": action, "ticker": ticker,
            "shares": shares, "price": price, "total": total, "cash_after": self._cash,
        })

    def _apply_buy(self, ticker: str, spend: float, price: float) -> bool:
        """Spend ``spend`` quote at ``price``; taker fee buys fewer coins."""
        if spend <= 0 or price <= 0 or spend > self._cash + 1e-9:
            return False
        self._cash -= spend
        filled = (spend / price) * (1.0 - self._taker)
        self._crypto_fees_paid += spend * self._taker
        self._crypto_volume += spend
        self._positions[ticker] = self._positions.get(ticker, 0.0) + filled
        self._record_trade("buy", ticker, filled, price, spend)
        return True

    def _apply_sell(self, ticker: str, shares: float, price: float) -> bool:
        held = self._positions.get(ticker, 0.0)
        shares = min(shares, held)
        if shares <= 0 or price <= 0:
            return False
        gross = shares * price
        fee = gross * self._taker
        net = gross - fee
        self._crypto_fees_paid += fee
        self._crypto_volume += gross
        self._cash += net
        rem = held - shares
        if rem <= 1e-12:
            self._positions.pop(ticker, None)
        else:
            self._positions[ticker] = rem
        self._record_trade("sell", ticker, shares, price, net)
        return True

    # ---- order submission ----------------------------------------------
    def submit_order(self, symbol, side, qty, notional, order_type, limit_price,
                     tif, extended_hours, client_order_id) -> OrderRef:
        side = side.lower()
        price = self._price(symbol) or (limit_price or 0.0)
        cid = client_order_id or make_client_order_id(self.instance_id, symbol, _now().isoformat(), side, 0)
        if self.paper:
            ok = (self._apply_buy(symbol, float(notional or (qty or 0) * price), price)
                  if side == "buy" else self._apply_sell(symbol, float(qty or 0), price))
            return OrderRef(broker_order_id=f"paper-{cid}", client_order_id=cid, symbol=symbol,
                            side=side, qty=float(qty or 0), status="filled" if ok else "rejected",
                            filled_qty=float(qty or 0) if ok else 0.0,
                            filled_avg_price=price if ok else None, submitted_at_utc=_now())
        # ---- live ----
        if self._wal is not None:
            try:
                self._wal.record_intent(client_order_id=cid, symbol=symbol, side=side,
                                        qty=qty, notional=notional, instance_id=self.instance_id)
            except Exception:
                pass
        kwargs = {"client_order_id": cid}
        if side == "buy" and notional:
            kwargs["quote_qty"] = round(float(notional), 2)
        else:
            kwargs["quantity"] = float(qty or 0)
        resp = self._client.new_order(symbol, "BUY" if side == "buy" else "SELL",
                                      order_type="MARKET", **kwargs)
        return self._apply_live_fill(symbol, side, cid, resp)

    def _apply_live_fill(self, symbol, side, cid, resp: dict) -> OrderRef:
        fills = resp.get("fills") or []
        filled_qty = float(resp.get("executedQty") or 0.0)
        quote_qty = float(resp.get("cummulativeQuoteQty") or 0.0)
        avg = (quote_qty / filled_qty) if filled_qty else None
        # keep local books roughly in sync (authoritative sync is refresh_positions)
        if filled_qty > 0 and avg:
            if side == "buy":
                self._cash -= quote_qty
                self._positions[symbol] = self._positions.get(symbol, 0.0) + filled_qty
                self._record_trade("buy", symbol, filled_qty, avg, quote_qty)
            else:
                self._cash += quote_qty
                rem = self._positions.get(symbol, 0.0) - filled_qty
                if rem <= 1e-12:
                    self._positions.pop(symbol, None)
                else:
                    self._positions[symbol] = rem
                self._record_trade("sell", symbol, filled_qty, avg, quote_qty)
            self._crypto_volume += quote_qty
            self._crypto_fees_paid += sum(float(f.get("commission") or 0.0) for f in fills)
        if self._wal is not None:
            try:
                self._wal.mark_submitted(client_order_id=cid, broker_order_id=str(resp.get("orderId")))
            except Exception:
                pass
        return OrderRef(broker_order_id=str(resp.get("orderId")), client_order_id=cid, symbol=symbol,
                        side=side, qty=filled_qty, status=str(resp.get("status") or "").lower() or "filled",
                        filled_qty=filled_qty, filled_avg_price=avg, submitted_at_utc=_now())

    def cancel_order(self, broker_order_id: str) -> bool:
        if self.paper:
            return True
        # Binance cancel needs the symbol; without an index we can't resolve it here.
        return False

    def get_order(self, broker_order_id: str) -> OrderRef:
        return OrderRef(broker_order_id=broker_order_id, client_order_id="", symbol="",
                        side="", qty=0.0, status="unknown")

    def get_order_by_client_id(self, client_order_id: str) -> Optional[OrderRef]:
        return None

    def list_open_orders(self, limit: int = 200) -> list[OrderRef]:
        if self.paper:
            return []
        try:
            out = []
            for o in self._client.open_orders():
                out.append(OrderRef(broker_order_id=str(o.get("orderId")),
                                    client_order_id=str(o.get("clientOrderId") or ""),
                                    symbol=from_binance(o.get("symbol")), side=str(o.get("side", "")).lower(),
                                    qty=float(o.get("origQty") or 0.0), status=str(o.get("status", "")).lower(),
                                    filled_qty=float(o.get("executedQty") or 0.0)))
            return out[:limit]
        except Exception:
            return []

    # ---- REST refresh ---------------------------------------------------
    def refresh_positions(self) -> list[PositionDTO]:
        if not self.paper:
            try:
                self._sync_from_broker()
            except Exception:
                pass
        out = []
        for sym, qty in self._positions.items():
            px = self._price(sym) or 0.0
            out.append(PositionDTO(symbol=sym, qty=qty, avg_entry_price=px, market_value=qty * px))
        return out

    def refresh_cash(self) -> CashDTO:
        if not self.paper:
            try:
                self._sync_from_broker()
            except Exception:
                pass
        return CashDTO(cash=self._cash, buying_power=self._cash, daytrading_buying_power=self._cash)

    def refresh_account(self) -> AccountDTO:
        eq = self.get_portfolio_value(self._last_prices)
        return AccountDTO(equity=eq, pattern_day_trader=False, daytrade_count=0,
                          account_blocked=False, trading_blocked=False,
                          buying_power=self._cash, last_equity=eq, cash=self._cash)

    def is_market_open(self, now_utc: datetime) -> bool:
        return True  # crypto trades 24/7/365

    def health_check(self) -> HealthStatus:
        errors = []
        auth_ok = True
        try:
            self._client.ping()
        except Exception as e:
            errors.append(f"ping: {e}")
        if not self.paper:
            try:
                self._client.account()
            except Exception as e:
                auth_ok = False
                errors.append(f"auth: {e}")
        return HealthStatus(auth_fresh=auth_ok, trade_updates_connected=False,
                            last_heartbeat_utc=_now(), errors=errors)

    # ---- PortfolioEmulator compatibility shims -------------------------
    def buy(self, ticker, shares, price, timestamp=None) -> bool:
        if self.paper:
            return self._apply_buy(ticker, float(shares) * float(price), float(price))
        ref = self.submit_order(ticker, "buy", None, float(shares) * float(price),
                                "market", None, "gtc", False, None)
        return ref.status in ("filled", "new", "partially_filled")

    def sell(self, ticker, shares, price, timestamp=None) -> bool:
        if self.paper:
            return self._apply_sell(ticker, float(shares), float(price))
        ref = self.submit_order(ticker, "sell", float(shares), None,
                                "market", None, "gtc", False, None)
        return ref.status in ("filled", "new", "partially_filled")

    def execute_signal(self, ticker, signal, price, timestamp=None,
                       cash_per_trade=1000.0, sell_fraction=1.0) -> bool:
        px = float(price) if price and price > 0 else (self._price(ticker) or 0.0)
        if px <= 0:
            return False
        if signal == 1:
            spend = min(float(cash_per_trade), self._cash)
            if spend <= 0:
                return False
            return self.buy(ticker, spend / px, px, timestamp)
        if signal == -1:
            held = self._positions.get(ticker, 0.0)
            if held <= 0:
                return False
            frac = max(0.0, min(1.0, float(sell_fraction)))
            shares = held if frac >= 1.0 else held * frac
            return shares > 0 and self.sell(ticker, shares, px, timestamp)
        return False

    def get_positions(self) -> dict[str, float]:
        return dict(self._positions)

    def get_positions_value(self, prices: dict[str, float]) -> float:
        v = 0.0
        for t, s in self._positions.items():
            p = (prices or {}).get(t) or self._last_prices.get(t)
            if p:
                v += s * float(p)
        return v

    def get_portfolio_value(self, prices: dict[str, float]) -> float:
        return self._cash + self.get_positions_value(prices)

    def get_trade_history(self) -> list[dict]:
        return list(self._trades)

    def get_portfolio_history(self) -> list[dict]:
        return list(self._portfolio_snapshots)

    def get_fee_summary(self) -> dict:
        return {"total_fees": round(self._crypto_fees_paid, 6),
                "total_volume": round(self._crypto_volume, 2), "taker_rate": self._taker}

    def get_cash(self) -> float:
        return self._cash

    def get_available_cash(self, reserved: float = 0.0) -> float:
        return max(0.0, self._cash - float(reserved))

    def get_initial_value(self) -> float:
        return self._initial_value

    def save_portfolio_snapshot(self, prices, timestamp=None) -> None:
        if prices:
            self._last_prices.update(prices)
        self._portfolio_snapshots.append({
            "timestamp": timestamp, "value": self.get_portfolio_value(prices),
            "cash": self._cash, "positions_snapshot": dict(self._positions),
        })

    def print_portfolio(self, prices, logger=None) -> None:
        msg = (f"[BinanceUS {'paper' if self.paper else 'LIVE'}] "
               f"value=${self.get_portfolio_value(prices):,.2f} cash=${self._cash:,.2f} "
               f"positions={self._positions}")
        (logger or print)(msg) if logger else print(msg)
