"""Pure Kalshi orderbook parser + maker-placement helpers (no network — unit-tested).

Kalshi returns BIDS ONLY: `orderbook_fp.{yes_dollars, no_dollars}` (legacy
`orderbook.{yes, no}`), each level a `[price, count]` pair. A YES bid at X is the
same as a NO ask at (1-X), so:
  • best YES bid (the price we can SELL at)  = highest yes-side bid
  • best YES ask (the price we must BUY at)  = 100 - highest no-side bid
This unblocks maker-first execution (rest inside the spread, post_only) and the
orderbook guard for the in-play sleeve.
Source: https://docs.kalshi.com/getting_started/orderbook_responses
"""
from __future__ import annotations

from dataclasses import dataclass


def _to_cents(price, dollars: bool) -> int:
    """Convert by KNOWN unit (not by guessing): '0.4200' dollars -> 42c; 42 cents -> 42c.
    Guessing per-value mis-reads a legacy 1-cent level as $1.00."""
    try:
        v = float(price)
    except (TypeError, ValueError):
        return 0
    return int(round(v * 100)) if dollars else int(round(v))


def _to_count(c) -> int:
    try:
        return int(round(float(c)))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class Book:
    top_bid: int      # best YES bid (our SELL price), cents; 0 if empty
    top_ask: int      # best YES ask (our BUY price), cents; 0 if empty
    bid_depth: int    # contracts resting at the top YES bid
    ask_depth: int    # contracts resting at the top YES ask (= top NO bid)
    spread: int       # top_ask - top_bid (cents); 0 if one side empty

    @property
    def imbalance(self) -> float:
        """(+1 bid-heavy, -1 ask-heavy). 0 when no depth."""
        d = self.bid_depth + self.ask_depth
        return 0.0 if d == 0 else (self.bid_depth - self.ask_depth) / d


def _levels(arr, dollars):
    out = []
    for lvl in (arr or []):
        try:
            p, c = lvl[0], lvl[1]
        except (TypeError, IndexError, KeyError):
            continue
        out.append((_to_cents(p, dollars), _to_count(c)))
    return out


def parse(raw: dict) -> Book:
    ob = (raw or {}).get("orderbook_fp") or (raw or {}).get("orderbook") or {}
    if ob.get("yes_dollars") is not None or ob.get("no_dollars") is not None:
        yes, no = _levels(ob.get("yes_dollars"), True), _levels(ob.get("no_dollars"), True)
    else:   # legacy integer-cents shape
        yes, no = _levels(ob.get("yes"), False), _levels(ob.get("no"), False)
    top_bid = max((p for p, _ in yes), default=0)
    bid_depth = sum(c for p, c in yes if p == top_bid) if top_bid else 0
    top_no = max((p for p, _ in no), default=0)
    top_ask = (100 - top_no) if top_no else 0
    ask_depth = sum(c for p, c in no if p == top_no) if top_no else 0
    spread = (top_ask - top_bid) if (top_bid and top_ask) else 0
    return Book(top_bid=top_bid, top_ask=top_ask, bid_depth=bid_depth,
                ask_depth=ask_depth, spread=spread)


def maker_buy_price(book: Book, *, fallback_ask: int, min_spread: int = 3) -> int | None:
    """Resting BUY (YES bid) price: improve the best bid by 1c but stay strictly
    BELOW the ask so post_only rests instead of crossing. None when the spread is too
    thin to make (take instead) or there's no book to anchor to."""
    if not book.top_ask and not book.top_bid:
        return None
    # Never rest ABOVE the price the planner sized the bet at — the market may have
    # moved up between the planner snapshot and this orderbook fetch.
    cap = int(fallback_ask)
    if book.top_bid and book.top_ask:
        if book.spread < min_spread:
            return None
        if book.top_ask > cap:
            return None                          # market moved away — take at our limit
        price = min(book.top_bid + 1, book.top_ask - 1, cap)
        return price if price >= 1 else None
    ref = book.top_ask or cap
    return min(max(1, int(ref) - 1), cap)


def maker_sell_price(book: Book, *, fallback_bid: int) -> int | None:
    """Resting SELL (YES ask) price: undercut the best ask by 1c, stay above the bid."""
    if not book.top_ask and not book.top_bid:
        return None
    if book.top_bid and book.top_ask:
        return min(99, max(book.top_ask - 1, book.top_bid + 1))
    ref = book.top_bid or fallback_bid
    return min(99, int(ref) + 1)


def allows_maker_buy(book: Book, *, min_book_depth: int = 5,
                     max_adverse_imbalance: float = -0.5) -> bool:
    """Guard against adverse selection: don't rest a buy into a book stacked with
    asks, or one with no real depth to sit behind."""
    if book.ask_depth and book.imbalance <= max_adverse_imbalance:
        return False
    if (book.bid_depth + book.ask_depth) < min_book_depth:
        return False
    return True
