# INTELLISTOCK_SCHEMA: {"strategy": "strategy_x", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_x_enabled": false, "core_bull_symbol": "TQQQ", "core_chop_symbol": "SPY", "core_bear_symbol": "", "core_weight": 0.9, "core_band_pct": 0.05, "core_filter_symbol": "QQQ", "core_filter_ma_bars": 200, "core_vol_bars": 20, "core_vol_gate_mult": 2.25, "core_vol_median_bars": 252, "core_vol_median_min_samples": 60, "core_bear_weight": 0.35, "core_bear_short_ma_bars": 50, "core_bear_vol_expansion": 1.4, "core_bear_drawdown_pct": 0.15, "core_bear_lookback_bars": 252, "core_bear_min_confirm": 4, "core_bear_max_bars": 40, "core_bear_cooldown_bars": 20, "core_bear_exit_grace_bars": 2, "bear_system_mode": "off", "bear_cash_symbol": "BIL", "crisis_alpha_symbols": ["DBMF", "KMLM", "CTA"], "crisis_alpha_pct": 0.2, "crisis_alpha_min_history_bars": 60, "bear_kicker_symbol": "SQQQ", "bear_kicker_pct": 0.05, "bear_kicker_fast_ma_bars": 20, "bear_kicker_mid_ma_bars": 50, "bear_kicker_long_ma_bars": 200, "bear_kicker_max_bars": 5, "bear_kicker_cooldown_bars": 10, "satellite_pct": 0.0, "satellite_max_names": 6, "satellite_exit_rank": 12, "satellite_min_hold_bars": 21, "core_vol_target": 0.0, "core_vol_scale_min": 0.3, "core_vol_scale_max": 1.0, "core_leverage_factor": 3.0, "satellite_momentum_bars": 60, "satellite_min_price": 0.0, "commodity_pct": 0.0, "commodity_symbols": ["GLD", "SLV", "USO", "UNG", "GDX", "XLE", "DBA", "CPER"], "commodity_max_names": 2, "commodity_mom_bars": 60, "commodity_trend_bars": 100, "min_order_usd": 50.0, "cost_haircut_pct": 0.006, "broker_max_single_position_pct": 0.95, "core_once_per_session": true}}
# INTELLISTOCK_DESCRIPTION: Leveraged Nasdaq core (TQQQ) with a de-lever filter to SPY. Direction is NOT predicted — a trend + volatility filter decides only WHETHER to be levered. Replaying this module over 15.7y of real closes (next-bar fills, point-in-time): CAGR 33.97%, maxDD -48.5%, Sharpe 0.88, 99.6x vs SPY's 8.5x, 4 years above +100%. The inverse (SQQQ) leg DEFAULTS OFF (-4.2% CAGR). The stock satellite is worth turning ON: with `satellite_pct=0.2` + `commodity_pct=0.2` it measures +7,140% compounded over 81 rolling 2-month windows vs SPY's +473%, beating SPY in 68% of them. (The old "satellite costs -4.0pp" figure was measured while a band bug kept the sleeve from ever opening a position - it held nothing.) Needs QQQ+TQQQ+SPY in the instance universe and granularity 86400. DIFFICULTY: 2
# DIFFICULTY: 2
"""IntelliStock — Strategy X: leveraged core, filtered.

WHAT THIS IS
------------
One position at a time. When the Nasdaq is in an uptrend and volatility is
normal, hold TQQQ at `core_weight` of NAV. Otherwise hold SPY. That is the
entire strategy, and the simplicity is the finding, not a shortcut.

WHY IT IS NOT A "COUNCIL"
-------------------------
The original design scored five voters — price trend, realised vol, an LLM macro
read, LLM news breadth, and a Neo4j graph traversal — into a signed conviction
that chose between TQQQ and SQQQ. A pre-registered study
(`scripts/strategy_x_voter_study.py`) measured all of them against non-overlapping
5-day forward QQQ returns before any of it was written:

    fraction of 5d windows that are UP    0.6045   <- the bar to beat
    above MA200                           0.5827
    trend (repo's own regime rule)        0.5560
    news_breadth (n=84)                   0.6071   CI [0.500, 0.705]
    vol                                   0.4904
    macro_llm                             0.4762   <- worse than a coin flip

Nothing beat "always vote up". Directional timing was cut on that evidence.
`backend/strategy_x.py` carries the full measurement table; read it before
re-adding a voter.

WHAT THE GRAPH AND THE LLM STILL DO
-----------------------------------
Nothing, and this is now measured rather than assumed. The graph score has no
CROSS-SECTIONAL skill either: Spearman IC vs forward returns is negative in all
20 lag x horizon cells over 197,797 observations, never significant, and
orthogonal to momentum (rho -0.026). The "+0.17 IC" that used to justify it is a
publication-timestamp artifact — `date_key` is the UTC publication date and 52%
of ticker-tagged articles publish at or after the 20:00 UTC close; pre-close
articles give -0.015, post-close +0.109. See `scripts/graph_signal_ic_study.py`.

The score is also a constant in practice: across 506,498 stored trade contexts
it takes 3 distinct values, and `confidence` is bimodal with 77% of rows at
exactly 0.0 or 1.0. Ranking on it means ranking on a tie, broken by ticker
spelling.

THE STOCK SLEEVE (graph-ranked) — WIRED, BUT READ THIS
------------------------------------------------------
`satellite_pct > 0` funds a stock sleeve out of the core, ranked by Graph Nexus
conviction. The channel now EXISTS: graph_nexus_analysis publishes
`_nexus_conviction_scores` (its per-candidate `raw_net_score`), the broker pops
that and merges it into the shared `data["conviction_scores"]` map, and this
strategy reads it. Before 2026-08-23 that contract had two readers and zero
writers, so the sleeve was silently inert.

To use it, BOTH strategies go on the same document. Ordering is load-bearing:
graph_nexus_analysis ships `execution_position: 0` and this ships `10`, so Nexus
runs first and its scores are in `data` by the time this reads them. Reverse the
order and the sleeve sees an empty map and quietly holds nothing.

THE SATURATION IS HANDLED HERE; THE LIVE GAP IS NOT:
  * `raw_net_score` is saturated — 3 distinct values across 506,498 stored trade
    contexts — so ranking on it alone is a tie, and sorting ties by ticker made
    this sleeve buy the alphabet (it held AAL/IDAI/IPDN/PW: the first four
    candidates, two of them sub-$100M microcaps). Ties now break on trailing
    momentum over `satellite_momentum_bars`, which is worth +3,136pp compounded
    over 81 windows and is what turns CHOP positive (-1.76 -> +0.65). A real
    score difference still wins; momentum only orders what the graph cannot
    separate. Nexus also logs the distinct-value count per bar, so the
    saturation stays visible — the proper repair is still in the scorer.
  * `satellite_min_price` exists but DEFAULTS OFF: a $5 floor was measured to
    cost 3,205pp and to flip chop negative again. Excluding cheap names removes
    more return than it saves in spread.
  * The broker passes `data=None` in LIVE mode (`price_history if mode ==
    MODE_BACKTEST else None`), so this channel is backtest-only. A live sleeve
    needs a separate carrier.

THE BAND BUG, because it will look like a rounding detail and is not:
`core_band_pct` is a fraction of NAV, and a 20% sleeve over 4 names targets
exactly 0.05 of NAV each. `targets_to_orders` therefore skipped every satellite
OPEN as "inside the no-churn band" — on the first bar and every bar after — so
the sleeve never held anything while the log printed a full seven-name target
book. ANY sleeve whose per-name target is <= `core_band_pct` is silently dead.
Check `satellite_pct / satellite_max_names > core_band_pct`.

THE COMMODITY SLEEVE
--------------------
`commodity_pct > 0` holds the top-`commodity_max_names` commodity ETFs by 60d
momentum, among those above their own 100d MA, funded out of the core. It is the
one added sleeve that beat its control: against SPY dilution at the SAME weight
it delivers ~3pp better max drawdown at matched return, consistently at 10/15/20%.

It is OFF by default because it trades against the stated objective — at 15% it
costs 4.3pp of CAGR and halves the years above +100%. Turn it on to buy
drawdown, not return.

Today it is momentum-driven, not news-driven. That is deliberate: production
"commodity trends" in graph_nexus are already 20d/60d price momentum on
GLD/SLV/USO/UNG, and the news->commodity bridge does not exist (nothing reads
`affected_commodities`; only ~658 articles in three years name a tradable
commodity). `rank_commodities` is the seam a news signal would plug into.

EXECUTION NOTE — THE 15% CAP
---------------------------
`BROKER_MAX_SINGLE_POSITION_PCT` (env, default 0.15) clips every allocator-lane
buy to 15% of equity in BOTH backtest and live. A 90% core is unreachable
without raising it, which is why `broker_max_single_position_pct` is in this
strategy's config: `backend/engines/backtest_engine.py` reads it from the
instance's strategy document and injects it into that run's container only.
Instances without the key keep the 0.15 default — `alpaca-main` is untouched.

LIVE IS NOT ENABLED BY THIS. `live_risk_state.DEFAULT_MAX_LEVERAGED_FRACTION`
caps SQQQ/TQQQ/SPXU/UPRO/SOXL/SOXS at 10% of equity on the live order path, and
the gate BLOCKS rather than clips. Live deployment needs that addressed
separately and deliberately.

DEFAULT OFF. `strategy_x_enabled` is false, so attaching this changes nothing.
"""
from __future__ import annotations

import math
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from strategy_x import (  # noqa: E402
    DEFAULTS,
    BearSignal,
    bear_signal,
    core_signal,
    core_vol_scale,
    pit_daily_closes,
    plan_targets,
    rank_commodities,
    select_satellite,
    strategy_x_universe,
    targets_to_orders,
)
from strategy_x_bear import (  # noqa: E402
    BearSystemStateError,
    advance_kicker,
    bear_role_conflict,
    bear_system_mode,
    eligible_crisis_alpha,
    fast_crash_signal,
    plan_bear_overlay,
)

# Route through intellistock_logger, NOT print(). The backtest engine runs the
# broker with `detach=False, remove=True` and DISCARDS container stdout on
# success — it only persists the tail in its `except` branch. So a print() goes
# nowhere durable, and the one line that would expose an inert run ("closes=65")
# would be invisible. intellistock_logger fans out to the backtest log buffer
# which becomes BacktestResults.logs, the only log an operator actually reads.
try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategyX")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategyX] {msg}")


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _ny_session(current_time):
    """The NY calendar date of a decision, or "" if it cannot be determined.

    Sessions are the unit this strategy decides in, and NY is the exchange
    calendar — a UTC date would roll over mid-session in the evening.
    """
    from datetime import datetime as _dt, timezone as _tz
    from zoneinfo import ZoneInfo

    ts = current_time
    if not isinstance(ts, _dt):
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_tz.utc)
    try:
        return ts.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return ""


def _bars_for(data, symbol):
    """The engine hands bars as either {sym: {"bars": [...]}} or {sym: [...]}."""
    if not isinstance(data, dict):
        return []
    entry = data.get(symbol)
    if isinstance(entry, dict):
        return entry.get("bars") or []
    return entry or []


class StrategyX:
    """Run-once strategy. Returns `{symbol: 1|0|-1}` plus the
    `_nexus_position_sizes` channel the broker's run_once lane consumes."""

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_x_enabled", False)):
            return {}
        if portfolio_emulator is None:
            return {}

        prices = prices or {}
        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        bear_mode = bear_system_mode(cfg)
        raw_bear_mode = str(
            cfg.get("bear_system_mode", "off") or "off"
        ).strip().lower()
        invalid_bear_mode = raw_bear_mode not in {"off", "shadow", "active"}
        active_backtest = (bear_mode == "active"
                           and str(mode or "").strip().lower() == "backtest")
        incoming_symbols = {
            str(symbol).strip().upper() for symbol in (symbols or []) if symbol
        }
        prior_bear_owned = {
            str(symbol).strip().upper()
            for symbol in (cache.get("_sx_bear_owned") or []) if symbol
        }
        prior_kicker_targeted = bool(cache.get("_sx_bear_kicker_targeted", False))
        prior_satellite_ages = set(cache.get("_sx_sat_ages") or {})

        try:
            nav = float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
            positions = portfolio_emulator.get_positions() or {}
            cash = float(portfolio_emulator.get_cash() or 0.0)
        except Exception as exc:
            _log(f"StrategyX: portfolio read failed ({type(exc).__name__}) — "
                 "no decision this bar", "yellow")
            return {}
        if nav <= 0:
            return {}

        filt = str(cfg.get("core_filter_symbol", "QQQ") or "QQQ").strip().upper()
        bull = str(cfg.get("core_bull_symbol", "TQQQ") or "TQQQ").strip().upper()
        chop = str(cfg.get("core_chop_symbol", "SPY") or "SPY").strip().upper()
        bear = str(cfg.get("core_bear_symbol", "") or "").strip().upper()
        owned = {s for s in (bull, chop, bear) if s} | prior_bear_owned
        bear_cash = str(cfg.get("bear_cash_symbol", "BIL") or "").strip().upper()
        bear_managers = {
            str(symbol).strip().upper()
            for symbol in (cfg.get("crisis_alpha_symbols") or []) if symbol
        }
        kicker_symbol = str(
            cfg.get("bear_kicker_symbol", "SQQQ") or ""
        ).strip().upper()
        configured_bear_symbols = ({bear_cash, kicker_symbol} | bear_managers) - {""}
        held_symbols = {
            str(symbol).strip().upper()
            for symbol, quantity in positions.items()
            if float(quantity or 0.0) > 0
        }
        static_conflict = bear_role_conflict(cfg)
        if (active_backtest
                and (held_symbols & configured_bear_symbols) - prior_bear_owned):
            unknown = sorted((held_symbols & configured_bear_symbols)
                             - prior_bear_owned)
            raise BearSystemStateError(
                "unprovenanced active bear holding(s): " + ", ".join(unknown)
            )
        if (active_backtest
                and _truthy(cfg.get("_strategy_x_bear_residual_conflict", False))
                and kicker_symbol in prior_bear_owned):
            raise BearSystemStateError(
                f"{kicker_symbol} already has Strategy X provenance while the "
                "broker residual sleeve reports competing ownership"
            )

        # ── ONE DECISION PER SESSION ──────────────────────────────────────
        # This is a daily strategy: the filter reads daily closes and it was
        # measured at one decision a day. The engine calls run_once on EVERY
        # tick, which at 900s bars is ~60 evaluations per session, and
        # `price > ma` is a bare comparison with no hysteresis — so a tape
        # sitting on its MA would round-trip ~90% of NAV in a 3x ETF dozens of
        # times a day. Decide once per NY session and hold.
        day_key = _ny_session(current_time)
        if cfg.get("core_once_per_session", True) and day_key:
            if cache.get("_sx_last_decision_day") == day_key:
                return {}
        if invalid_bear_mode:
            _log(f"StrategyX: invalid bear_system_mode={raw_bear_mode!r}; "
                 "using off", "yellow")

        closes = pit_daily_closes(_bars_for(data, filt), current_time)

        # REFUSE rather than degrade when the filter cannot see. `core_signal`
        # fails closed to risk-off, which sounds safe and is not: risk-off is an
        # ACTIVE order for `core_weight` of NAV into the chop occupant, not a
        # flat. In live the broker passes `data=None`, so a blind filter would
        # buy 100% of the book into SPY and hold it forever. A strategy that
        # cannot evaluate its own signal must do NOTHING.
        need = int(cfg.get("core_filter_ma_bars", 200) or 200)
        if len(closes) < need:
            _log(f"StrategyX: REFUSING to trade — {len(closes)} daily closes "
                 f"for {filt}, need {need}. The filter cannot arm, and "
                 "'risk-off' would be a real buy, not a flat. "
                 + ("Live mode passes data=None: this strategy is "
                    "backtest-only until a live bar source exists."
                    if not _bars_for(data, filt) else
                    "Widen the window or use granularity=86400."), "red")
            cache["_strategy_x_last"] = {"risk_on": False, "reason": "refused: "
                                         f"{len(closes)} < {need} closes",
                                         "n_closes": len(closes),
                                         "targets": {},
                                         "bear_system_mode": bear_mode,
                                         "bear_overlay_reason":
                                             "insufficient filter history"}
            if not prior_bear_owned:
                return {}
            exit_prices = dict(prices)
            for symbol in prior_bear_owned:
                if float(exit_prices.get(symbol) or 0.0) > 0:
                    continue
                visible = pit_daily_closes(_bars_for(data, symbol), current_time)
                if visible and float(visible[-1]) > 0:
                    exit_prices[symbol] = float(visible[-1])
            decisions, sizes = targets_to_orders(
                {}, nav=nav, positions=positions, prices=exit_prices,
                cash=cash, config=cfg, owned=prior_bear_owned,
            )
            cache["_sx_bear_owned"] = sorted(
                symbol for symbol in prior_bear_owned
                if symbol in held_symbols or prior_kicker_targeted
            )
            cache["_sx_bear_kicker_targeted"] = False
            if not decisions:
                return {}
            sizes["_cash_reserve_floor_pct"] = 0.0
            sizes["_cash_reserve_floor_hard"] = False
            out = dict(decisions)
            out["_nexus_discovered"] = strategy_x_universe(cfg)
            out["_nexus_position_sizes"] = sizes
            out["_nexus_executable_buys"] = []
            out["_nexus_sell_enforcement"] = sorted(
                symbol for symbol, decision in decisions.items() if decision == -1
            )
            return out

        sig = core_signal(closes, cfg)
        # Continuous de-levering, computed from the same PIT closes the
        # trend filter uses. Returns 1.0 when `core_vol_target` is 0, so
        # the shipped path is byte-identical until an operator arms it.
        vol_scale = core_vol_scale(closes, cfg)

        held_core = ""
        for sym, qty in (positions or {}).items():
            if float(qty or 0) > 0 and str(sym).strip().upper() == bull:
                held_core = bull
                break

        # ── effective prices: quotes, plus a point-in-time last close ──
        # The broker's price map carries the instance watchlist; every
        # graph-discovered satellite candidate is absent from it, because
        # discoveries are collected AFTER run_once and only gate execution. That
        # left the sleeve unable to rank or size any of its own picks (bt 186584:
        # 3 symbols emitted a bar, 20% of NAV idle).
        #
        # Nexus already loads bars for those names and they arrive in the shared
        # `data` map, so the last VISIBLE close is available and is the same
        # number a quote would carry on this bar. `pit_daily_closes` applies the
        # same cutoff the core filter uses, so this adds no lookahead. Quotes
        # always win where they exist; this only fills gaps.
        broker_only_bear = (configured_bear_symbols - incoming_symbols
                            if bear_mode in {"shadow", "active"} else set())
        eff_prices = {
            str(symbol).strip().upper(): value
            for symbol, value in (prices or {}).items()
            if str(symbol).strip().upper() not in broker_only_bear
        }
        if float(cfg.get("satellite_pct", 0.0) or 0.0) > 0 and isinstance(data, dict):
            _conv = data.get("conviction_scores") or {}
            if isinstance(_conv, dict):
                for _sym in _conv:
                    _s = str(_sym).strip().upper()
                    if (not _s or _s in broker_only_bear
                            or float(eff_prices.get(_s) or 0.0) > 0):
                        continue
                    _c = pit_daily_closes(_bars_for(data, _s), current_time)
                    if _c and _c[-1] > 0:
                        eff_prices[_s] = float(_c[-1])

        ranked = []
        if float(cfg.get("satellite_pct", 0.0) or 0.0) > 0:
            # Buy/hold spread applied HERE, where holdings are known: a held
            # name survives while inside satellite_exit_rank, a new one must be
            # inside satellite_max_names. Without it the sleeve re-draws its
            # entire book every bar, because the conviction score is saturated
            # and "top N" is a tie broken by ticker spelling.
            held_syms = {str(s).strip().upper()
                         for s, q in (positions or {}).items()
                         if float(q or 0) > 0}
            # Names this sleeve has bought, with how many bars they have been
            # held. Tracked in the cache because the ranking cannot be used as
            # a memory — Nexus rotates its candidate set daily, so a held name
            # is usually absent from it entirely.
            ages = dict(cache.get("_sx_sat_ages") or {})
            selection_ages = {
                symbol: age for symbol, age in ages.items()
                if symbol not in prior_bear_owned
            }
            # The sleeve's book is what it TARGETED, not what the emulator
            # currently reports. Fills land on the NEXT bar, so a name chosen
            # last bar is not in `positions` yet — gating the minimum hold on
            # observed holdings meant every new pick was unprotected for exactly
            # the bar it most needed protecting, and the book churned anyway.
            # Measured: with the holdings gate, only 1 of 4 names survived a bar.
            mine = set(ages) | {s for s in ages if s in held_syms}
            ranked_candidates = [
                symbol for symbol in self._ranked(
                    cfg, data, prices=eff_prices, as_of=current_time,
                )
                if symbol not in prior_bear_owned
            ]
            ranked = select_satellite(
                ranked_candidates, set(selection_ages), cfg,
                ages=selection_ages)
            # EVERY name this sleeve has ever bought and still holds must stay
            # in `owned`, or `targets_to_orders` cannot sell it: a position that
            # drops out of the ranking would fall outside the sell scope and
            # accumulate forever. Observed live as targets churning while
            # orders=1 — the buys landed and the exits never fired.
            owned |= set(ranked) | mine | held_syms & set(ages)
            # Age only the names still selected. A name that drops out leaves
            # `ages`, which is what makes it exitable — but it stays in `owned`
            # via `mine` for this bar so the SELL can actually be emitted.
            ages = {s: selection_ages.get(s, 0) + 1 for s in ranked}
            cache["_sx_sat_ages"] = ages

        com_ranked = []
        com_syms = [str(s).strip().upper()
                    for s in (cfg.get("commodity_symbols") or []) if s]
        if float(cfg.get("commodity_pct", 0.0) or 0.0) > 0 and com_syms:
            com_closes = {s: pit_daily_closes(_bars_for(data, s), current_time)
                          for s in com_syms}
            com_ranked = rank_commodities(com_closes, cfg)
            # Own the whole candidate set, not just today's picks — otherwise a
            # name that drops out of the ranking is outside `owned` and can
            # never be sold.
            owned |= set(com_syms)
            missing = [s for s in com_syms if not com_closes.get(s)]
            if missing:
                _log(f"StrategyX: commodity sleeve has NO bars for "
                     f"{', '.join(missing)} — those cannot be ranked or held. "
                     "Add them to the instance's stock list.", "red")

        # ── bear gate: SQQQ only on an auto-detected bad regime ──
        # Three pieces of cache state, all owned here because bear_signal is
        # pure: `_sx_bear_bars` counts bars the leg is actually HELD (not bars
        # the gate is open), `_sx_bear_grace` rides out a one-bar confirm
        # flicker so the leg is not liquidated and rebought on consecutive days,
        # and `_sx_bear_cooldown` keeps it down after the time limit instead of
        # letting it re-engage immediately.
        bsig = None
        bear_on = False
        if bear:
            engaged_bars = int(cache.get("_sx_bear_bars", 0) or 0)
            cooldown = int(cache.get("_sx_bear_cooldown", 0) or 0)
            grace = int(cache.get("_sx_bear_grace", 0) or 0)
            max_bars = int(cfg.get("core_bear_max_bars", 0) or 0)

            # The time limit is enforced HERE, not inside bear_signal, because
            # the caller owns the counter. Detecting it in the pure function and
            # reacting here meant the limit bar looked like an ordinary
            # non-confirming bar, which reset the counter to 0 and let the leg
            # re-engage immediately — a 40-on / 1-off cycle forever.
            if cooldown <= 0 and max_bars > 0 and engaged_bars >= max_bars:
                cache["_sx_bear_cooldown"] = int(
                    cfg.get("core_bear_cooldown_bars", 20) or 0)
                cache["_sx_bear_bars"] = 0
                cache["_sx_bear_grace"] = 0
                bsig = BearSignal(False, f"time limit {engaged_bars} >= "
                                         f"{max_bars} bars — cooling down for "
                                         f"{cache['_sx_bear_cooldown']}")
                bear_on = False
                cooldown = -1          # skip the branches below this bar

            if cooldown < 0:
                pass                   # handled above
            elif cooldown > 0:
                # A time-limited leg must STAY down. Resetting the counter on
                # the stand-down bar made the limit meaningless: it cycled
                # 40-on / 1-off / 40-on forever, and each cycle is a full
                # round trip of the leg at the widest spreads of the episode.
                cache["_sx_bear_cooldown"] = cooldown - 1
                cache["_sx_bear_bars"] = 0
                bsig = BearSignal(False, f"cooldown {cooldown} bars remaining")
                bear_on = False
            elif (bsig := bear_signal(closes, cfg,
                                      bars_engaged=engaged_bars)).engaged:
                # Count bars the leg is actually HELD, not bars the gate is
                # open: the gate can be open while the trend filter is still
                # risk-on, in which case no SQQQ exists and the -6*sigma^2
                # clock should not be running.
                bear_on = not sig.risk_on
                cache["_sx_bear_bars"] = engaged_bars + (1 if bear_on else 0)
                cache["_sx_bear_grace"] = int(cfg.get("core_bear_exit_grace_bars", 2) or 0)
                if (int(cfg.get("core_bear_max_bars", 0) or 0) > 0
                        and cache["_sx_bear_bars"] >= int(cfg["core_bear_max_bars"])):
                    cache["_sx_bear_cooldown"] = int(
                        cfg.get("core_bear_cooldown_bars", 20) or 0)
            elif grace > 0 and engaged_bars > 0:
                # EXIT HYSTERESIS. One non-confirming bar used to liquidate the
                # whole leg and rebuy it the next day — measured as two full
                # 35%-of-NAV round trips in a -3x fund on consecutive days in
                # March 2020. Hold through a brief flicker; a genuine turn
                # spends the grace bars and then stands down.
                cache["_sx_bear_grace"] = grace - 1
                bear_on = not sig.risk_on
                cache["_sx_bear_bars"] = engaged_bars + (1 if bear_on else 0)
            else:
                cache["_sx_bear_bars"] = 0
                bear_on = False

        targets, notes = plan_targets(risk_on=sig.risk_on, config=cfg,
                                      satellite_ranked=ranked,
                                      held_core=held_core,
                                      commodity_ranked=com_ranked,
                                      bear_engaged=bear_on,
                                      vol_scale=vol_scale)
        selected_targets = dict(targets)
        overlay_reason = "bear system is off"
        bear_prices = dict(eff_prices)
        if bear_mode in {"shadow", "active"}:
            state_keys = (
                "_sx_bear_system_state",
                "_sx_bear_kicker_bars",
                "_sx_bear_kicker_cooldown",
                "_sx_bear_state_version",
                "_sx_bear_kicker_entry_day",
            )
            state_before = {
                key: (key in cache, cache.get(key)) for key in state_keys
            }
            manager_symbols = [
                str(symbol).strip().upper()
                for symbol in (cfg.get("crisis_alpha_symbols") or []) if symbol
            ]
            bear_prices.update({
                str(symbol).strip().upper(): value
                for symbol, value in prices.items()
            })
            for symbol in [
                str(cfg.get("bear_cash_symbol", "BIL") or "").strip().upper(),
                *manager_symbols,
                str(cfg.get("bear_kicker_symbol", "SQQQ") or "").strip().upper(),
            ]:
                if not symbol or float(bear_prices.get(symbol) or 0.0) > 0:
                    continue
                visible = pit_daily_closes(_bars_for(data, symbol), current_time)
                if visible and float(visible[-1]) > 0:
                    bear_prices[symbol] = float(visible[-1])
            manager_closes = {
                symbol: pit_daily_closes(_bars_for(data, symbol), current_time)
                for symbol in manager_symbols
            }
            eligible = eligible_crisis_alpha(manager_closes, bear_prices, cfg)
            unavailable = tuple(symbol for symbol in manager_symbols
                                if symbol not in eligible)
            crash = fast_crash_signal(closes, cfg)
            kicker = kicker_symbol
            valid_states = {"idle", "armed", "holding", "cooldown"}

            def _safe_persisted_count(value):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return float("nan")
                number = float(value)
                if (not math.isfinite(number) or not number.is_integer()
                        or number < 0 or number > 100_000):
                    return float("nan")
                return int(number)

            raw_state = cache.get("_sx_bear_system_state", "idle")
            state_name = str(raw_state or "").strip().lower()
            raw_bars = cache.get("_sx_bear_kicker_bars", 0)
            raw_cooldown = cache.get("_sx_bear_kicker_cooldown", 0)
            persisted_state_present = any(
                key in cache for key in (
                    "_sx_bear_system_state",
                    "_sx_bear_kicker_bars",
                    "_sx_bear_kicker_cooldown",
                    "_sx_bear_state_version",
                    "_sx_bear_kicker_entry_day",
                    "_sx_bear_kicker_targeted",
                )
            )
            prior_shadow = cache.get("_sx_bear_shadow")
            if not isinstance(prior_shadow, dict):
                prior_shadow = {}
            prior_kicker = prior_shadow.get("kicker")
            if not isinstance(prior_kicker, dict):
                prior_kicker = {}
            recorded_kicker = str(
                prior_kicker.get("symbol") or ""
            ).strip().upper()
            if recorded_kicker:
                state_bound_to_kicker = recorded_kicker == kicker
            elif prior_kicker_targeted and kicker in prior_bear_owned:
                state_bound_to_kicker = True
            else:
                state_bound_to_kicker = not persisted_state_present
            current_prior_targeted = (
                prior_kicker_targeted and state_bound_to_kicker
            )

            safe_bars = _safe_persisted_count(raw_bars)
            safe_cooldown = _safe_persisted_count(raw_cooldown)
            max_bars_limit = _safe_persisted_count(
                cfg.get("bear_kicker_max_bars", 5)
            )
            cooldown_limit = _safe_persisted_count(
                cfg.get("bear_kicker_cooldown_bars", 10)
            )
            limits_valid = (
                isinstance(max_bars_limit, int) and max_bars_limit >= 1
                and isinstance(cooldown_limit, int)
            )
            counters_consistent = False
            if (state_name in valid_states and isinstance(safe_bars, int)
                    and isinstance(safe_cooldown, int) and limits_valid):
                if state_name == "idle":
                    counters_consistent = (
                        safe_bars == 0 and safe_cooldown == 0
                        and not current_prior_targeted
                    )
                elif state_name == "armed":
                    counters_consistent = (
                        safe_bars == 0 and safe_cooldown == 0
                        and not current_prior_targeted
                        and kicker not in held_symbols
                    )
                elif state_name == "holding":
                    counters_consistent = (
                        1 <= safe_bars <= max_bars_limit
                        and safe_cooldown == 0
                        and (bear_mode == "shadow" or kicker in held_symbols
                             or current_prior_targeted)
                    )
                else:
                    counters_consistent = (
                        safe_bars == 0
                        and safe_cooldown <= cooldown_limit
                        and not current_prior_targeted
                    )
            cache_valid = (
                cache.get("_sx_bear_state_version") == 1
                and state_bound_to_kicker and counters_consistent
            )
            if cache_valid:
                cached_state = state_name
                cached_bars = safe_bars
                cached_cooldown = safe_cooldown
            elif persisted_state_present:
                cached_state = state_name if state_name in valid_states else "idle"
                cached_bars = float("nan")
                cached_cooldown = safe_cooldown
            else:
                cached_state, cached_bars, cached_cooldown = "idle", 0, 0
                current_prior_targeted = False
            if (not cache_valid and kicker in prior_bear_owned
                    and kicker in held_symbols):
                # Provenance proves the holding is ours, but invalid/missing
                # state cannot prove its age. Force cooldown and exit.
                cached_bars = float("nan")
            if cache_valid and state_name == "holding":
                try:
                    from datetime import date as _date

                    entry_date = _date.fromisoformat(str(
                        cache.get("_sx_bear_kicker_entry_day") or ""
                    ))
                    decision_date = _date.fromisoformat(day_key)
                    if entry_date > decision_date:
                        raise ValueError("future kicker entry day")
                except (TypeError, ValueError):
                    cached_bars = float("nan")

            kicker_decision = advance_kicker(
                crash,
                state=cached_state,
                bars=_safe_persisted_count(cached_bars),
                cooldown=_safe_persisted_count(cached_cooldown),
                risk_on=bool(sig.risk_on),
                bull_held=bull in held_symbols,
                kicker_held=kicker in held_symbols,
                kicker_priceable=float(bear_prices.get(kicker) or 0.0) > 0,
                shadow=bear_mode == "shadow",
                prior_targeted=current_prior_targeted,
                config=cfg,
            )
            overlay_cfg = dict(cfg, bear_system_mode="active")
            dynamic_overlap = sorted(
                configured_bear_symbols
                & (set(ranked) | prior_satellite_ages
                   | set(cache.get("_sx_sat_ages") or {}))
            )
            residual_conflict = _truthy(
                cfg.get("_strategy_x_bear_residual_conflict", False)
            )
            if dynamic_overlap:
                overlay_cfg["bear_system_mode"] = "off"
            allocation = plan_bear_overlay(
                targets, risk_on=bool(sig.risk_on), config=overlay_cfg,
                eligible_symbols=eligible,
                kicker_engaged=(kicker_decision.engaged
                                and not residual_conflict),
                prices=bear_prices,
            )
            proposed_targets = dict(allocation.targets)
            reason = allocation.reason
            refusal_reason = ""
            if dynamic_overlap:
                reason = "role conflict: satellite selection/age contains " + ", ".join(
                    dynamic_overlap
                )
                refusal_reason = reason
                proposed_targets = dict(targets)
            elif static_conflict:
                refusal_reason = f"role conflict: {static_conflict}"
            elif bear:
                refusal_reason = "legacy bear configuration is enabled"
            if residual_conflict:
                refusal_reason = "broker residual-sleeve kicker conflict"
                if allocation.applied:
                    reason = "bear overlay applied; kicker suppressed by residual sleeve"
            if bear_mode == "active" and not active_backtest:
                refusal_reason = "research-only runtime"
            delta = {
                symbol: round(proposed_targets.get(symbol, 0.0)
                              - targets.get(symbol, 0.0), 6)
                for symbol in sorted(set(targets) | set(proposed_targets))
                if round(proposed_targets.get(symbol, 0.0)
                         - targets.get(symbol, 0.0), 6) != 0
            }
            overlay_reason = (
                refusal_reason
                if refusal_reason == "research-only runtime"
                else reason
            )
            if active_backtest and allocation.applied and not dynamic_overlap:
                selected_targets = dict(proposed_targets)
            cache["_sx_bear_system_state"] = kicker_decision.state
            cache["_sx_bear_kicker_bars"] = kicker_decision.bars
            cache["_sx_bear_kicker_cooldown"] = kicker_decision.cooldown
            cache["_sx_bear_state_version"] = 1
            prior_entry = cache.get("_sx_bear_kicker_entry_day", "")
            if kicker_decision.state == "holding":
                cache["_sx_bear_kicker_entry_day"] = (
                    str(prior_entry) if prior_entry else day_key
                )
            else:
                cache["_sx_bear_kicker_entry_day"] = ""
            kicker_targeted = (active_backtest and kicker in selected_targets)
            cache["_sx_bear_kicker_targeted"] = bool(kicker_targeted)
            newly_owned = (
                {
                    symbol for symbol in configured_bear_symbols
                    if float(selected_targets.get(symbol, 0.0) or 0.0) > 0
                }
                if active_backtest and allocation.applied and not dynamic_overlap
                else set()
            )
            retained_owned = {
                symbol for symbol in prior_bear_owned
                if (symbol in held_symbols
                    or float(selected_targets.get(symbol, 0.0) or 0.0) > 0
                    or prior_kicker_targeted)
            }
            cache["_sx_bear_owned"] = sorted(retained_owned | newly_owned)
            owned |= retained_owned | newly_owned
            cache["_sx_bear_shadow"] = {
                "mode": bear_mode,
                "core_state": "risk_on" if sig.risk_on else "risk_off",
                "core_reason": sig.reason,
                "state": kicker_decision.state,
                "reason": reason,
                "refusal_reason": refusal_reason,
                "eligible_managers": list(eligible),
                "unavailable_managers": list(unavailable),
                "signal": {
                    "stacked": crash.stacked,
                    "fresh": crash.fresh,
                    "below_fast": crash.below_fast,
                    "reason": crash.reason,
                },
                "kicker": {
                    "symbol": kicker,
                    "state": kicker_decision.state,
                    "engaged": kicker_decision.engaged,
                    "bars": kicker_decision.bars,
                    "cooldown": kicker_decision.cooldown,
                    "reason": kicker_decision.reason,
                },
                "baseline_targets": dict(targets),
                "proposed_targets": proposed_targets,
                "target_delta": delta,
            }
            _log(
                f"StrategyX bear mode={bear_mode} | {reason}"
                f"{f' | refused={refusal_reason}' if refusal_reason else ''}"
                f" | eligible={list(eligible)} | kicker={kicker_decision.state}"
                f" | provenance={sorted(cache['_sx_bear_owned'])}"
                f" | delta={delta}",
                "cyan" if allocation.applied else "yellow",
            )
            if bear or static_conflict or dynamic_overlap:
                # A hard ownership/configuration conflict is observational:
                # it may publish refusal telemetry and unwind old provenance,
                # but it cannot move the new kicker state machine forward.
                for key, (was_present, value) in state_before.items():
                    if was_present:
                        cache[key] = value
                    else:
                        cache.pop(key, None)
        if bsig is not None and not sig.risk_on:
            _log(f"  bear gate: {bsig.reason}"
                 + (f" | held {cache.get('_sx_bear_bars', 0)} bars"
                    if bear_on else "")
                 + (f" | COOLDOWN {cache.get('_sx_bear_cooldown', 0)}"
                    if cache.get("_sx_bear_cooldown") else ""),
                 "red" if bear_on else "white")
        decisions, sizes = targets_to_orders(
            selected_targets, nav=nav, positions=positions,
            prices={**eff_prices, **bear_prices},
            cash=cash, config=cfg, owned=owned)
        if bear_mode == "off" and prior_bear_owned:
            cache["_sx_bear_owned"] = sorted(
                symbol for symbol in prior_bear_owned
                if symbol in held_symbols or prior_kicker_targeted
            )
            cache["_sx_bear_kicker_targeted"] = False

        # A traded leg with no quote is not a no-op — it is the whole strategy
        # silently not running, and the self-censor in targets_to_orders (skip
        # on px <= 0) makes it look like a bar with nothing to do.
        missing = [s for s in (bull, chop) if float(prices.get(s) or 0.0) <= 0]
        if missing:
            _log(f"StrategyX: NO PRICE for {', '.join(missing)} — this bar can "
                 f"emit no order. Are {', '.join(sorted(owned))} in the "
                 "instance's stock list?", "red")

        # The sleeve's recurring failure in this repo has been SILENCE — an
        # inert lever logs the same as a working one. State the decision every
        # bar, whether or not it produced an order.
        _log(f"StrategyX {'RISK-ON' if sig.risk_on else 'RISK-OFF'} | "
             f"{sig.reason} | targets="
             f"{ {k: round(v, 4) for k, v in selected_targets.items()} } | "
             f"orders={len(decisions)} | nav=${nav:,.0f} | closes={len(closes)}",
             "cyan" if sig.risk_on else "yellow")
        for note in notes:
            _log(f"  {note}", "white")
        if not closes:
            _log(f"StrategyX: NO visible {filt} bars at {current_time} — the "
                 "filter is blind and the core is held flat. Is the filter "
                 "symbol in the instance's stock list?", "red")
        elif len(closes) < int(cfg.get("core_filter_ma_bars", 200) or 200):
            # THE false-negative trap. The broker's warmup is clamped to 90
            # CALENDAR days (~65 sessions) at any sub-daily granularity, so a
            # 200-day MA never forms and the strategy sits 100% in the chop
            # occupant for the whole run — reading as "Strategy X has no edge"
            # when it simply never ran. Say so loudly.
            _log(f"StrategyX: only {len(closes)} daily closes, need "
                 f"{cfg.get('core_filter_ma_bars')} — the filter CANNOT arm and "
                 "this run is 100% "
                 f"{cfg.get('core_chop_symbol')}. Use granularity=86400 (700-bar "
                 "warmup) or start the window >= 10 months after data begins. "
                 "This is NOT a result.", "red")

        cache["_strategy_x_last"] = {
            "risk_on": bool(sig.risk_on),
            "reason": sig.reason,
            "price": sig.price,
            "ma": sig.ma,
            "rvol": sig.rvol,
            "rvol_median": sig.rvol_median,
            "targets": dict(selected_targets),
            "n_closes": len(closes),
            "bear_system_mode": bear_mode,
            "bear_overlay_reason": overlay_reason,
        }

        if day_key:
            cache["_sx_last_decision_day"] = day_key
        if not decisions:
            return {}
        out = dict(decisions)
        # Declare the universe this strategy owns so the broker admits its
        # symbols even when the instance watchlist does not list them. Nexus
        # uses the same channel: `allowed_syms = set(symbols) |
        # set(nexus_discovered) | ...`, and discovery also triggers the price
        # backfill. Bars for the FIXED legs come from the broker-side fetch list
        # (`_strategy_x_universe_symbols` in broker.py).
        #
        # The satellite names MUST be included. They are auto-discovered by
        # Nexus, so they are not in the instance watchlist and not in this
        # strategy's own fixed universe — and `allowed_syms` is computed
        # PER-SPEC, so a satellite buy for a name absent from THIS strategy's
        # discovered list is dropped by the broker before it ever reaches
        # execution. That drop is silent: the order simply never appears.
        out["_nexus_discovered"] = strategy_x_universe(cfg) + [
            s for s in ranked if s not in strategy_x_universe(cfg)]
        # Release the broker's cash-reserve floor for this strategy. The floor
        # defaults to 10% of the INITIAL account value and can only be bypassed
        # by a high-conviction Nexus buy with >= 5 open positions — neither of
        # which this strategy ever produces. Targets that sum to 1.0 would then
        # leave the second leg permanently unfundable: it stays ~9.5% below
        # target, outside the band, and is re-issued every single bar for the
        # rest of the run. This strategy holds its own reserve via `core_weight`.
        sizes["_cash_reserve_floor_pct"] = 0.0
        sizes["_cash_reserve_floor_hard"] = False
        out["_nexus_position_sizes"] = sizes
        out["_nexus_executable_buys"] = [s for s, d in decisions.items() if d == 1]
        out["_nexus_sell_enforcement"] = [s for s, d in decisions.items() if d == -1]
        return out

    @staticmethod
    def _ranked(cfg, data, prices=None, as_of=None):
        """Conviction-ordered satellite candidates, best first.

        Reads `data["conviction_scores"]` — the same channel `index_core_tilt`
        uses, which is how a graph/LLM ranking reaches this strategy. Falls back
        to an EMPTY list, never to an arbitrary order: a fake ranking makes a
        dead signal look alive, which is exactly how 677/677 `raw_score=0.000`
        stayed invisible for so long.

        TWO GUARDS, both added after watching this sleeve trade for real:

        1. TIEBREAK. `raw_net_score` is saturated — 3 distinct values across
           506,498 trade contexts — so ordering by `(-score, ticker)` decays
           into ALPHABETICAL order. Production bt 331865 bought AAL, IDAI,
           IPDN, PW: literally the first four candidates in the alphabet. Ties
           now break on trailing momentum over `satellite_momentum_bars`,
           computed from the same point-in-time closes the core filter uses, so
           it is deterministic and carries no lookahead. A real score
           difference still wins — momentum only orders what the graph cannot
           separate.

        2. PRICE FLOOR. Two of those four picks were sub-$100M microcaps. At a
           45.6bps modelled spread a $1.40 stock cannot pay for its own round
           trip. `satellite_min_price` drops them. A name with NO price is kept
           — absent data is not evidence of a penny stock, and dropping it
           silently is how sleeves go quietly empty.
        """
        scores = {}
        if isinstance(data, dict):
            raw = data.get("conviction_scores") or {}
            if isinstance(raw, dict):
                skip = {
                    str(cfg.get("core_bull_symbol", "") or "").strip().upper(),
                    str(cfg.get("core_chop_symbol", "") or "").strip().upper(),
                    str(cfg.get("core_bear_symbol", "") or "").strip().upper(),
                }
                for sym, val in raw.items():
                    try:
                        score = float(val)
                    except (TypeError, ValueError):
                        continue
                    s = str(sym).strip().upper()
                    if s and s not in skip and score > 0:
                        scores[s] = score

        # Fall back to DEFAULTS, NOT to a hardcoded 0.0. A live document was
        # written before these keys existed, so `cfg` does not carry them; a
        # literal default here means the floor is 0 in production and the guard
        # ships inert while every test still passes.
        floor = float((cfg or {}).get(
            "satellite_min_price", DEFAULTS["satellite_min_price"]) or 0.0)
        px = prices or {}
        # INVESTABILITY — the production blocker, not a nicety. Nexus discovers
        # a name and scores it in the SAME bar, but the broker builds the price
        # map BEFORE run_once and never expands it with those discoveries
        # (`nexus_discovered_syms` is gathered afterwards and only gates
        # execution). So the top-ranked candidates routinely have no price,
        # `targets_to_orders` drops them at `px <= 0`, and the sleeve burns every
        # `satellite_max_names` slot on names it cannot buy — measured in
        # bt 186584 as 3 emitted symbols a bar (core + 2 commodity) with the
        # whole 20% sitting in cash, on four names that were re-picked bar after
        # bar because the minimum hold kept them.
        #
        # A candidate with no price is not investable THIS bar. Drop it so the
        # slot goes to one that is; it returns as soon as a price exists.
        kept = {}
        for s, score in scores.items():
            try:
                p = float(px.get(s) or 0.0)
            except (TypeError, ValueError):
                p = 0.0
            if p > 0.0 and (floor <= 0.0 or p >= floor):
                kept[s] = score
        scores = kept

        mom_bars = max(2, int((cfg or {}).get(
            "satellite_momentum_bars",
            DEFAULTS["satellite_momentum_bars"]) or 60))
        mom = {}
        for s in scores:
            closes = pit_daily_closes(_bars_for(data, s), as_of)
            if len(closes) > mom_bars and closes[-1 - mom_bars] > 0:
                mom[s] = closes[-1] / closes[-1 - mom_bars] - 1.0

        # Sort key: score first, then momentum, then ticker as the final
        # deterministic fallback. `s in mom` ranks ahead of `s not in mom` so a
        # name with no history sorts last rather than at momentum 0.0, which
        # would place it above every genuine decliner.
        return [s for s, _ in sorted(
            scores.items(),
            key=lambda kv: (-kv[1], 0 if kv[0] in mom else 1,
                            -mom.get(kv[0], 0.0), kv[0]))]
