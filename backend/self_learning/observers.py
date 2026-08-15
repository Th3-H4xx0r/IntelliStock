"""Normalize existing decision documents into `Observation`s.

The sources are written by `broker.py` in the COMMON execution path, not by any
strategy, so this module is strategy-agnostic by construction: RSI and
graph_nexus_analysis produce the same record shape.

The refusal set is the point. `backtest_decisions` records what was DECIDED and
`backtest_trades` records what EXECUTED; a BUY present in the first and absent
from the second is a refusal, and refusals are where this project's findings
have always lived (0 of 134 grants cleared the min-position floor; 0% of the 52
names that moved 30%+ were bought). Nothing else records them.
"""
from __future__ import annotations

from self_learning.types import Observation

_BUY_ACTIONS = frozenset({"buy", "initial_buy"})


def _norm_symbol(value) -> str:
    return str(value or "").strip().upper()


def _trade_keys(doc: dict) -> set:
    """(symbol, timestamp) for every executed trade. Trades call it `ticker`;
    decisions call it `symbol` — the same thing under two names."""
    keys = set()
    for trade in (doc.get("backtest_trades") or []):
        if not isinstance(trade, dict):
            continue
        keys.add((_norm_symbol(trade.get("ticker") or trade.get("symbol")),
                  str(trade.get("timestamp") or "")))
    return keys


def _votes(entry: dict) -> tuple:
    out = []
    for sub in (entry.get("strategies") or []):
        if not isinstance(sub, dict):
            continue
        try:
            weight = float(sub.get("weight") or 0.0)
        except (TypeError, ValueError):
            weight = 0.0
        try:
            decision = int(sub.get("decision") or 0)
        except (TypeError, ValueError):
            decision = 0
        out.append((str(sub.get("strategy") or ""), decision, weight))
    return tuple(out)


def _score(entry: dict):
    raw = entry.get("normalized_score")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def observations_from_backtest(doc: dict, *, venue: str = "equity") -> list:
    """One `Observation` per decision record in a `BacktestResults` document."""
    decisions = (doc or {}).get("backtest_decisions") or []
    executed_keys = _trade_keys(doc or {})
    run_id = str((doc or {}).get("id") or "")
    out = []
    for entry in decisions:
        if not isinstance(entry, dict):
            continue
        symbol = _norm_symbol(entry.get("symbol"))
        as_of = str(entry.get("timestamp") or "")
        try:
            decision = int(entry.get("decision") or 0)
        except (TypeError, ValueError):
            decision = 0
        executed = (symbol, as_of) in executed_keys
        action = str(entry.get("action") or "").strip().lower()
        # A hold that did not trade is not a refusal — it is the system doing
        # exactly what it decided. Only an unexecuted BUY/SELL is a refusal.
        refused = (not executed) and (decision != 0)
        out.append(Observation(
            run_id=run_id, origin="backtest", venue=venue,
            strategy_id=str(entry.get("primary_strategy") or ""),
            as_of=as_of, symbol=symbol, action=action, decision=decision,
            normalized_score=_score(entry), executed=executed,
            refusal_reason="unknown" if refused else None,
            votes=_votes(entry), config_hash=(doc or {}).get("config_hash"),
        ))
    return out


def observations_from_live(rows: list, *, instance_id: str,
                           venue: str = "equity") -> list:
    """One `Observation` per `BotTradeDecisions` row.

    `origin` participates in the content id, so a live instance and a backtest
    that happen to share an id can never collide.
    """
    out = []
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        try:
            decision = int(row.get("decision") or 0)
        except (TypeError, ValueError):
            decision = 0
        executed = bool(row.get("filled"))
        refused = (not executed) and (decision != 0)
        out.append(Observation(
            run_id=str(instance_id), origin="live", venue=venue,
            strategy_id=str(row.get("primary_strategy") or ""),
            as_of=str(row.get("ts") or row.get("created_at") or ""),
            symbol=_norm_symbol(row.get("symbol")),
            action=str(row.get("action") or "").strip().lower(),
            decision=decision, normalized_score=_score(row), executed=executed,
            refusal_reason="unknown" if refused else None,
            votes=_votes({"strategies": row.get("strategy_summary") or []}),
            config_hash=row.get("config_hash"),
        ))
    return out


def funnel_summary(doc: dict) -> dict:
    """Aggregate counts for one run. Written as an aggregate, never as rows."""
    obs = observations_from_backtest(doc)
    buys = [o for o in obs if o.action in _BUY_ACTIONS or o.decision == 1]
    return {
        "decided": len(obs),
        "executed": sum(1 for o in obs if o.executed),
        "refused": sum(1 for o in obs if o.refusal_reason is not None),
        "buy_decided": len(buys),
        "buy_executed": sum(1 for o in buys if o.executed),
    }
