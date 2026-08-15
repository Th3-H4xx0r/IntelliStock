"""Normalize existing decision documents into `Observation`s.

The sources are written by `broker.py` in the COMMON execution path, not by any
strategy, so this module is strategy-agnostic by construction: RSI and
graph_nexus_analysis produce the same record shape.

────────────────────────────────────────────────────────────────────────────
TWO KINDS OF REFUSAL, BOTH NOW OBSERVED
────────────────────────────────────────────────────────────────────────────
* `refusal_reason="unfilled"` — the strategy decided BUY or SELL, the order
  reached the execution path, and no matching fill followed. Source:
  `backtest_decisions` minus `backtest_trades`.
* `refusal_reason=<gate code>` — a gate refused the name before it ever reached
  the execution path: `min_position_floor`, `max_positions`, `satellite_cap`,
  `fundamental_veto`, `anchor_buying_power`, `nexus_buy_guard`, `min_hold`,
  `split_guard`, `no_price`. Source: `backtest_refusals`.

The second kind is the one that matters and it used to be invisible.
`broker.py` records a decision only when `not _trade_skipped_no_price`, and the
comment at the min-position floor says the quiet part out loud —
``_trade_skipped_no_price = True  # reuse flag to prevent recording``. So
`backtest_decisions` was the COMPLEMENT of the refusal set: the names refused at
a gate were precisely the ones missing. That is why "0 of 134 grants cleared the
min-position floor" took a human reading logs to find.

`backtest_refusals` is emitted by an optimistic-then-cleared register in the
order loop, so it covers every refusing path without instrumenting each one.
Runs recorded BEFORE that shipped have no `backtest_refusals` field; for those
the gate refusals remain unobservable and `funnel_summary` reports
`gate_refusals_available=False` so a consumer cannot mistake absence for zero.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from self_learning.timeline import to_naive_utc
from self_learning.types import Observation

# Rows the emulator appends to the SAME trade list that are not executions of a
# strategy decision — `portfolio_emulator.py:976` appends `action="dividend"`.
# Counting one as a fill would mark an unrelated decision executed.
_EXECUTION_SIDES = frozenset({"buy", "sell"})

# A fill lands one execution-delay after its decision, and the delay is the
# backtest increment — 15 minutes to a day. A week is generous enough for daily
# bars across a weekend and tight enough that a decision cannot claim a fill
# from the far side of the run.
DEFAULT_MAX_LAG_SECONDS = 7 * 24 * 3600


def _norm_symbol(value) -> str:
    return str(value or "").strip().upper()


def _side(action: str, decision: int):
    """The side a decision or trade represents, or None when it is neither."""
    lowered = str(action or "").strip().lower()
    if lowered in _EXECUTION_SIDES:
        return lowered
    if decision == 1:
        return "buy"
    if decision == -1:
        return "sell"
    return None


def _votes(entry: dict, key: str = "strategies") -> tuple:
    out = []
    for sub in (entry.get(key) or []):
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


def _score(entry: dict, key: str = "normalized_score"):
    raw = entry.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _fill_index(doc: dict):
    """(symbol, side) -> ascending fill times, plus the executable-trade count.

    The count includes trades whose timestamp could not be parsed, because join
    health is measured against how many fills EXIST, not how many were legible.
    """
    buckets = defaultdict(list)
    executable = 0
    for trade in (doc.get("backtest_trades") or []):
        if not isinstance(trade, dict):
            continue
        side = str(trade.get("action") or "").strip().lower()
        if side not in _EXECUTION_SIDES:
            continue                      # dividend / fee / adjustment rows
        executable += 1
        stamp = to_naive_utc(trade.get("timestamp"))
        if stamp is None:
            continue
        symbol = _norm_symbol(trade.get("ticker") or trade.get("symbol"))
        try:
            notional = abs(float(trade.get("total") or 0.0))
        except (TypeError, ValueError):
            notional = 0.0
        buckets[(symbol, side)].append((stamp, notional))
    for key in buckets:
        buckets[key].sort(key=lambda pair: pair[0])
    return buckets, executable


def _match_fills(entries, buckets, *, max_lag_seconds: float) -> set:
    """Indices of decisions that have a matching fill.

    An interval join, not an equality join: the decision stamp is naive and the
    fill stamp is aware-UTC one execution-delay later, so they are never equal.
    Each fill is consumed at most once, so two buys of one name cannot both
    claim a single fill.

    Returns {decision index: filled notional}. A bare boolean cannot tell a
    $5,000 request that filled for $50 from one that filled in full — and for a
    subsystem whose thesis is "the position floor refused everything", the size
    is part of the measurement.
    """
    used = {key: [False] * len(times) for key, times in buckets.items()}
    matched = {}
    ordered = sorted(
        (m for m in entries if m[3] is not None and m[2] is not None),
        key=lambda m: m[3],
    )
    for index, symbol, side, stamp in ordered:
        times = buckets.get((symbol, side))
        if not times:
            continue
        flags = used[(symbol, side)]
        for position, (fill_time, notional) in enumerate(times):
            if fill_time < stamp:
                continue              # execution never precedes its decision
            if (fill_time - stamp).total_seconds() > max_lag_seconds:
                break                 # sorted, so nothing later can match
            if flags[position]:
                continue
            flags[position] = True
            matched[index] = notional
            break
    return matched


def observations_from_backtest(doc: dict, *, venue: str = "equity",
                               max_lag_seconds: float = DEFAULT_MAX_LAG_SECONDS
                               ) -> list:
    """One `Observation` per decision record in a `BacktestResults` document."""
    decisions = (doc or {}).get("backtest_decisions") or []
    buckets, _executable = _fill_index(doc or {})
    run_id = str((doc or {}).get("id") or "")

    entries = []
    for index, entry in enumerate(decisions):
        if not isinstance(entry, dict):
            entries.append(None)
            continue
        try:
            decision = int(entry.get("decision") or 0)
        except (TypeError, ValueError):
            decision = 0
        symbol = _norm_symbol(entry.get("symbol"))
        side = _side(entry.get("action"), decision)
        entries.append((index, symbol, side,
                        to_naive_utc(entry.get("timestamp"))))

    matched = _match_fills([e for e in entries if e], buckets,
                           max_lag_seconds=max_lag_seconds)

    out = []
    for index, entry in enumerate(decisions):
        meta = entries[index]
        if meta is None:
            continue
        _, symbol, side, _stamp = meta
        try:
            decision = int(entry.get("decision") or 0)
        except (TypeError, ValueError):
            decision = 0
        executed = index in matched
        # A hold that did not trade is not a refusal — it is the system doing
        # exactly what it decided. Only an unexecuted BUY/SELL is one.
        refused = (not executed) and side is not None
        out.append(Observation(
            run_id=run_id, origin="backtest", venue=venue,
            strategy_id=str(entry.get("primary_strategy") or ""),
            as_of=str(entry.get("timestamp") or ""), symbol=symbol,
            action=str(entry.get("action") or "").strip().lower(),
            decision=decision, normalized_score=_score(entry),
            executed=executed,
            filled_notional=matched.get(index),
            refusal_reason="unfilled" if refused else None,
            votes=_votes(entry),
            # No provenance today: `broker.py` writes no top-level config_hash
            # onto BacktestResults. Read anyway so it lights up for free if a
            # later phase starts stamping one.
            config_hash=(doc or {}).get("config_hash"),
        ))
    return out


def observations_from_gate_refusals(doc: dict, *, venue: str = "equity") -> list:
    """One `Observation` per name a gate refused before execution.

    These carry `executed=False` and the gate's own reason code, so the learner
    can ask "what did the min-position floor cost me?" — which is the question
    the whole subsystem exists to answer.
    """
    run_id = str((doc or {}).get("id") or "")
    out = []
    for entry in ((doc or {}).get("backtest_refusals") or []):
        if not isinstance(entry, dict):
            continue
        try:
            decision = int(entry.get("decision") or 0)
        except (TypeError, ValueError):
            decision = 0
        if decision == 0:
            continue
        out.append(Observation(
            run_id=run_id, origin="backtest", venue=venue,
            strategy_id=str(entry.get("primary_strategy") or ""),
            as_of=str(entry.get("timestamp") or ""),
            symbol=_norm_symbol(entry.get("symbol")),
            action=str(entry.get("action") or "").strip().lower(),
            decision=decision, normalized_score=_score(entry),
            executed=False,
            refusal_reason=str(entry.get("reason") or "gate"),
            votes=(), config_hash=(doc or {}).get("config_hash"),
        ))
    return out


def observations_from_live(rows: list, *, instance_id: str,
                           venue: str = "equity") -> list:
    """One `Observation` per `BotTradeDecisions` row.

    The real record shape is `bot_decision_log.build_decision_doc:90-105`:
    `side`, `ts`, `strategy`, `score`, `action_intent`, `contributors`. An
    earlier version of this function read `decision`/`filled`/`primary_strategy`
    /`normalized_score`/`strategy_summary`, none of which exist — so every live
    observation came out an empty HOLD.

    Every row here is an EXECUTION, never a refusal: `broker.py:17172` writes
    this table only under `if _es_placed and decision in (1, -1)`. A live
    refusal produces no row at all, so inferring one here would be a
    fabrication.
    """
    out = []
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        side = str(row.get("side") or "").strip().lower()
        decision = 1 if side == "buy" else (-1 if side == "sell" else 0)
        out.append(Observation(
            run_id=str(instance_id), origin="live", venue=venue,
            strategy_id=str(row.get("strategy") or ""),
            as_of=str(row.get("ts") or row.get("created_at") or ""),
            symbol=_norm_symbol(row.get("symbol")), action=side,
            decision=decision, normalized_score=_score(row, "score"),
            executed=True, refusal_reason=None,
            votes=_votes(row, "contributors"),
            config_hash=row.get("config_hash"),
        ))
    return out


def all_observations(doc: dict, *, venue: str = "equity") -> list:
    """Everything one run decided: what reached execution, and what a gate
    refused before it could. The two sources are disjoint by construction —
    a name that reached the record site had its refusal register cleared."""
    return (observations_from_backtest(doc or {}, venue=venue)
            + observations_from_gate_refusals(doc or {}, venue=venue))


def funnel_summary(doc: dict, observations=None, *, venue: str = "equity") -> dict:
    """Aggregate counts for one run, written as an aggregate and never as rows.

    `trades_available` / `trades_matched` are the JOIN HEALTH. They exist
    because the first version of this join silently matched nothing on the
    equity path, which turned a 95%-conversion run into a confident "0% of buys
    executed" finding. A consumer that cannot tell those apart will publish the
    broken one.
    """
    if observations is None:
        observations = all_observations(doc or {}, venue=venue)
    _buckets, executable = _fill_index(doc or {})
    buys = [o for o in observations if o.decision == 1]
    gate_refused = [o for o in observations
                    if o.refusal_reason not in (None, "unfilled")]
    reasons = {}
    for obs in gate_refused:
        reasons[obs.refusal_reason] = reasons.get(obs.refusal_reason, 0) + 1
    return {
        "decided": len(observations),
        "executed": sum(1 for o in observations if o.executed),
        "refused": sum(1 for o in observations if o.refusal_reason is not None),
        "buy_decided": len(buys),
        "buy_executed": sum(1 for o in buys if o.executed),
        "trades_available": executable,
        "trades_matched": sum(1 for o in observations if o.executed),
        "gate_refused": len(gate_refused),
        "gate_reasons": reasons,
        # False for runs recorded before gate capture shipped. A consumer must
        # not read a missing field as "no gate refused anything".
        "gate_refusals_available": "backtest_refusals" in (doc or {}),
    }
