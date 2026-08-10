#!/usr/bin/env python3
"""Re-run the ALLOCATION + GATE chain of a FINISHED backtest under a new config.

WHY THIS EXISTS
---------------
Backtest credits are the scarce resource. ``scripts/replay_gate_decisions.py``
answers "how many refusals would lever X have changed?" for two hard-coded
levers. This is the general form: it reconstructs per-bar state out of a
recorded log (book, cash, NAV, prices, satellite share, the ordered candidate
list with its sizes and -- where the log printed them -- its scores) and then
walks the SAME broker-side gate chain the run walked, under whatever config you
hand it, reporting which buys are now admitted, at what size, and what the book
would look like.

IT IMPORTS PRODUCTION CODE, IT DOES NOT REIMPLEMENT IT
------------------------------------------------------
CALLED DIRECTLY by the replay:

  backend/broker.py               _core_sleeve_satellite_headroom  (the satellite
                                    clamp -- SATELLITE CAP / SATELLITE OVERFLOW)
                                  _core_turnover_state             (turnover budget)
                                  _satellite_conviction_min_raw
                                  _turnover_cfg_conviction_bypass
                                  _turnover_cfg_bypass_ceiling
                                  _turnover_is_governed
                                  _turnover_ledger_record / _turnover_ledger_rolling
                                  _regime_position_cap_hard
                                  _max_positions_excludes_sleeve
                                  _residual_sleeve_config
                                  _residual_sleeve_universe_symbols
                                  _core_sleeve_cfg / _core_sleeve_cfg_raw
  backend/nexus_broker_utils.py   max_positions_gate               (the hard cap)
                                  max_positions_projected_count
                                  resolve_max_positions_cap
                                  buy_ceiling                      (sell-proceeds credit)
                                  max_positions_admissible_buys    (core funding pre-pass
                                  planned_full_exit_symbols         diagnostic)
  backend/portfolio_emulator.py   PortfolioEmulator -- NAV, cash and the real
                                  get_buying_power() clamp that re-bounds an
                                  order the broker already approved

REACHED TRANSITIVELY (so they still cannot drift):

  backend/core_sleeve.py          satellite_design_share / satellite_max_share via
                                    _core_sleeve_satellite_headroom
                                  core_sleeve_config via _core_sleeve_cfg
                                  turnover_budget_state via _core_turnover_state

NOT MODELLED AT ALL (named so the omission is visible):

  core_rebalance_order / core_target_weight / core_sleeve_armed_for_bar -- the
  core's OWN buy and release decisions are replayed from the log verbatim, not
  re-decided. A config that changes the core's cadence, band or bear scaling
  will therefore show NO effect here even though it would change a real run.
  What the harness does report is the funding pre-pass: how much core release
  the recorded bar asked for, and how much of it was for buys max_positions was
  about to refuse.

``broker.py`` is NOT import-safe (argparse runs at module scope and SystemExits
under any other entrypoint), so its helpers are AST-extracted into a stub
namespace -- the pattern established by ``backend/tests/test_residual_sleeve.py``
and for the same reason. Extraction is prefix-matched and then ASSERTED: a
helper that goes missing raises instead of silently turning a gate into a
fail-open no-op that still prints a plausible report.

The only arithmetic written here is the glue the broker does inline inside its
``_exec_order`` loop -- the cash floor, the ``available`` subtraction, the 15%
single-position cap and the $50 execution minimum. Each carries the broker.py
line it mirrors, and ``backend/tests/test_simulate_allocation.py`` pins them.

============================================================================
WHAT THIS CANNOT MODEL.  READ THIS BEFORE QUOTING ANY NUMBER IT PRINTS.
============================================================================

1. P&L.  It reports ADMISSIONS and SIZES, never dollars earned. A name the log
   never bought has no price series in the log, so there is nothing to mark it
   against. A freed slot is not a fill and a fill is not a profit. bt 718249 is
   the standing warning: relaxing the position cap admitted five extra names
   (EFX -16.2%, AMZN -13.8%, MSFT -7.3%, ETH -5.0%, C -7.3%) and the run came
   in at +4.23% against +12.33% for the tighter arm.

2. PRICE IMPACT, FILL PRICES, PARTIAL FILLS, FILL TIMING.  Execution is
   next-event (decide on bar N, fill at bar N+1's quote) and every fill crosses
   a modelled 22.8 bps half-spread (simulated_execution.py:117-121 (LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL)). This
   harness assumes an admitted buy fills at the price the log recorded for that
   bar. It does not run NextEventExecutionSimulator.

3. DOWNSTREAM DIVERGENCE -- the big one.  The moment one admission differs, the
   real run diverges: different cash next bar, different held set, different
   monitor sells, different rotation pairs, different backfill queue, different
   regime-cap occupancy, different turnover ledger. The recorded candidate list
   is what the RECORDED book produced. Two propagation modes, both explicit:

     --book frozen     (default) every tick is scored against the state the RUN
                       actually had. Pure arithmetic, no compounding fiction,
                       reproducible from the log. Answers "which gate refused
                       what, and would config B have refused it too".
     --book projected  admitted buys are applied to a shadow book and carried
                       forward. Answers "roughly what would the book look like".
                       It is WRONG after the first divergence, and the header
                       says so. An upper bound, never a result.

4. THE STRATEGY SIDE.  Candidate discovery, ranking, the LLM overlay, the V31.2
   total-spend cap, the backfill queue, rotations and the per-name sizing that
   produces ``cash_per_trade`` all live in
   ``backend/strategies/graph_nexus_analysis.py`` and are REPLAYED AS RECORDED.
   Changing a key only the STRATEGY reads (allocation_profile,
   total_spend_cap_target_weight_pct, momentum_position_size_floor_pct,
   nexus_portfolio_pct, ...) changes nothing here even though it would change a
   real run. Only the BROKER-side gates listed above are re-evaluated. The
   report prints every config key it actually consumed, so an unread key is
   visible rather than silently assumed effective.

5. WHAT THE LOG DID NOT PRINT.  Chiefly ``raw_net_score``: the log emits it only
   on ``SATELLITE OVERFLOW`` / ``TURNOVER BUDGET BYPASS`` lines, i.e. only for
   names already over the conviction threshold. Everything else falls back to
   the backfill-queue ``signal_score`` (a DIFFERENT number on a similar scale)
   or to None. Every score carries its provenance and the summary counts the
   unscored, so a score-threshold lever's answer over those names is a LOWER
   BOUND, not an answer.

6. LIVE-ONLY BEHAVIOUR.  Price-sanity reject, the Alpaca order gate,
   ``ordered_today``, the per-tick kill switch, the 15s/120s watchdogs and the
   three flags ``live_mode_overrides.LIVE_OVERRIDES`` flips
   (portfolio_drawdown_halt_enabled, private_entity_bridge_enabled,
   max_positions_breach_auto_rotate) are live-only -- see
   docs/handoffs/2026-08-08-production-readiness-research.md section 4. This
   harness replays the BACKTEST path.

7. SETTLEMENT.  The real ``PortfolioEmulator.get_buying_power`` is used, but its
   ``_unsettled_tranches`` are not replayed (the log never prints settlement
   state), so the T+1 5% withhold reads as zero. Where a bar's cash is mostly
   fresh sale proceeds this harness is OPTIMISTIC by that amount.

8. SELLS.  Only the BUY side of the chain is re-evaluated. Sells, the monitor,
   trailing stops and the min-hold clock are replayed as recorded.

USAGE
-----
    # re-score the recorded run under the doc it ran, and validate the
    # reconstruction against the log's own counters
    python3 scripts/simulate_allocation.py backtests/820236_*.log \
        --config scripts/doc193_backup_patch_20260808T110842Z.json --validate

    # A/B a lever without spending a credit
    python3 scripts/simulate_allocation.py backtests/820236_*.log \
        --config scripts/doc193_backup_patch_20260808T110842Z.json \
        --set max_positions=8 --diff

    # every candidate, every stage, machine readable
    python3 scripts/simulate_allocation.py backtests/820236_*.log \
        --config <doc.json> --verbose --json out.json
"""
from __future__ import annotations

import argparse
import ast
import collections
import copy
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


# ---------------------------------------------------------------------------
# PRODUCTION IMPORTS
# ---------------------------------------------------------------------------

#: Prefix-matched rather than name-listed, for the reason
#: backend/tests/test_residual_sleeve.py gives: the broker helpers call each
#: other, their bodies are wrapped in bare ``except Exception``, and one missing
#: name turns a gate into a silent fail-open that still prints a plausible
#: report. Prefixes mean adding a helper cannot reopen that hole.
_BROKER_PREFIXES = (
    "_core_sleeve", "_core_turnover", "_core_funding",
    "_turnover_ledger", "_turnover_is_governed", "_turnover_cfg",
    "_satellite_conviction", "_max_positions_excludes_sleeve",
    "_residual_sleeve_universe_symbols", "_residual_sleeve_config",
    "_regime_position_cap_hard", "_regime_recovery_hard_cap",
    "_CORE_", "_RESIDUAL_SLEEVE_",
    # _residual_sleeve_config calls this one, and the sleeve bodies swallow
    # NameError inside a bare except -- a missing callee is how a gate goes
    # silently inert.
    "_chop_ret20_cfg",
)

#: Every broker helper this harness dispatches to. Absence is fatal.
_BROKER_REQUIRED = (
    "_core_sleeve_satellite_headroom", "_core_sleeve_cfg", "_core_sleeve_cfg_raw",
    "_core_sleeve_decide", "_satellite_conviction_min_raw",
    "_turnover_cfg_conviction_bypass", "_turnover_cfg_bypass_ceiling",
    "_turnover_is_governed", "_turnover_ledger_record", "_turnover_ledger_rolling",
    "_core_turnover_state", "_regime_position_cap_hard",
    "_max_positions_excludes_sleeve", "_residual_sleeve_config",
    "_residual_sleeve_universe_symbols", "_core_funding_mpg_aware",
)


class Production:
    """Handle on the real allocation/gate implementations.

    ``regime`` is a REPLAY input (the log stamps it once per strategy cycle),
    so the broker's module-level ``_sleeve_market_regime`` accessor is rebound
    per tick rather than stubbed to a constant.
    """

    def __init__(self):
        import core_sleeve
        import nexus_broker_utils
        from portfolio_emulator import PortfolioEmulator

        self.core_sleeve = core_sleeve
        self.nbu = nexus_broker_utils
        self.PortfolioEmulator = PortfolioEmulator

        self.logs: list = []
        self._regime = ""
        self._circuit = ""
        self.sleeve_state: dict = {}

        ns = {
            "_log": lambda msg, *a, **k: self.logs.append(str(msg)),
            "math": __import__("math"),
            "os": os,
            "datetime": __import__("datetime"),
            "_sleeve_market_regime": lambda: self._regime,
            "_sleeve_circuit_tier": lambda: self._circuit,
            "_sleeve_rally_onset": lambda: False,
            "_RESIDUAL_SLEEVE_STATE": self.sleeve_state,
        }
        src = (BACKEND / "broker.py").read_text()
        #: broker.py is edited constantly, so pin what this replay was built
        #: against. Extraction is by NAME/PREFIX, never by line number, which
        #: is why a broker edit does not silently break the chain -- but a
        #: report still has to say which revision produced it.
        self.broker_fingerprint = (
            __import__("hashlib").sha256(src.encode()).hexdigest()[:12],
            len(src.splitlines()),
        )
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and str(t.id).startswith(_BROKER_PREFIXES)
                    for t in node.targets):
                exec(compile(ast.Module(body=[node], type_ignores=[]),
                             "broker.py", "exec"), ns)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and str(node.name).startswith(_BROKER_PREFIXES):
                exec(compile(ast.Module(body=[node], type_ignores=[]),
                             "broker.py", "exec"), ns)
        missing = [n for n in _BROKER_REQUIRED if n not in ns]
        if missing:
            raise RuntimeError(
                "AST extraction from broker.py failed for: " + ", ".join(missing)
                + " -- refusing to run with a gate silently absent")
        self.ns = ns
        for name in _BROKER_REQUIRED:
            setattr(self, name.lstrip("_"), ns[name])
        # The extracted ledger helpers close over the ORIGINAL dict object, so
        # keep the same identity rather than reassigning it later.
        self.sleeve_state = ns["_RESIDUAL_SLEEVE_STATE"]

    def set_regime(self, regime: str) -> None:
        self._regime = str(regime or "").strip().lower()

    def reset_ledger(self) -> None:
        self.sleeve_state.clear()


# ---------------------------------------------------------------------------
# LOG PARSING
# ---------------------------------------------------------------------------
#
# Every regex below is anchored on a line broker.py or graph_nexus_analysis.py
# actually emits; the broker.py line reference is in the comment. If a log has
# none of a given line the corresponding stage is simply never observed, and
# --validate reports that rather than pretending the stage did not exist.

RX_TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2} [\d:]+)\]\s*\[([^\]]+)\]\s*(.*)$")
# broker.py:14249 -- emitted once per execution cycle, BEFORE any buy is
# considered, carrying the two numbers the gate itself will use. Using the
# gate's own line as the tick delimiter means the harness cannot disagree with
# the run about where a tick starts.
RX_TICK = re.compile(r"max_positions gate armed: held=(\d+), cap=(\d+)")
RX_RUNONCE = re.compile(r"Run once \|.*?\bdate=(\d{4}-\d{2}-\d{2})")
RX_REGIME = re.compile(r"V31 market regime: (\w+)")
RX_FILL = re.compile(
    r"FILL (BUY|SELL) (\S+) qty=([\d.]+) cumulative=\S+ price=([\d.]+) "
    r"fees=([\d.]+) quote=(\d{4}-\d{2}-\d{2})")
RX_MONITOR = re.compile(
    r"Monitor decision: (\S+) day (\d+) pnl=([-+\d.]+)% cp=\$([\d.]+) entry=\$([\d.]+)")
# broker.py:15347
RX_BUYGATE = re.compile(
    r"Buy gate inputs for (\S+): cash=\$([\d.]+)(?: bp=\$[\d.]+)? "
    r"reserved=\$([\d.]+) floor=\$([\d.]+) effective_floor=\$([\d.]+) "
    r"high_conv=(\w+) open_pos=(\d+) cash_per_trade=\$([\d.]+) "
    r"available=\$([\d.]+) cash_to_use=\$([\d.]+) . (\w+)")
# broker.py:14956 / 14863 / 14870
RX_SATTRIM = re.compile(
    r"SATELLITE CAP: (\S+) trimmed \$([\d,.]+) -> \$(-?[\d,.]+) to keep the core")
RX_SATSKIP = re.compile(r"SATELLITE CAP: (\S+) skipped . satellite at its ")
RX_SATOVF = re.compile(
    r"SATELLITE OVERFLOW: (\S+) raw=([-+][\d.]+) >= ([\d.]+) . funding \$([\d,.]+)")
# broker.py:15061 / 14501 / 14993 / 14984
RX_TBBLOCK = re.compile(r"TURNOVER BUDGET BLOCK: (\S+) skipped . (\d+)% of NAV")
RX_TBBIND = re.compile(r"TURNOVER BUDGET BINDING: (\d+)% of NAV")
RX_TBBYPASS = re.compile(r"TURNOVER BUDGET BYPASS: (\S+) raw=([-+][\d.]+)")
RX_TBCEIL = re.compile(r"TURNOVER BYPASS CEILING: (\S+) refused despite raw=([-+][\d.]+)")
# broker.py:15550 / 15036 / 15186 / 15306
RX_MPG = re.compile(r"MAX_POSITIONS_GATE: blocked (\S+) \(held=(\d+), cap=(\d+)\)")
RX_RCAP = re.compile(r"REGIME CAP HARD BLOCK: (\S+) skipped . held=(\d+) >= cap=(\d+)")
RX_SINGLE = re.compile(
    r"Broker single-position cap: (\S+) cash_to_use \$([\d.]+) trimmed to \$([\d.]+)")
RX_SKIPMIN = re.compile(r"SKIP BUY (\S+) . cash_to_use \$([\d.]+) < min \$(\d+)")
# graph_nexus_analysis.py -- the only per-symbol scores the log prints for
# names that never reached the conviction path.
RX_BFQ_SCORE = re.compile(r"Backfill queue ADD: (\S+) \(score=([\d.]+)")
RX_BFQ_SIG = re.compile(
    r"BFQ ALLOC=0: (\S+) \(queued \d+d, score=[\d.]+, signal_score=([\d.]+)\)")
RX_CORE_PCT = re.compile(r"\[core\] .*?core ([\d.]+)% vs target ([\d.]+)% of NAV")
RX_SCP_CEIL = re.compile(
    r"Sell-proceeds credit: sizing ceiling \$([\d.]+) . \$([\d.]+)")

#: broker.py:15363 -- the execution-time minimum below which a buy is dropped.
EXEC_MIN_POSITION_USD = 50.0
#: broker.py:15207 -- BROKER_MAX_SINGLE_POSITION_PCT default, both modes since
#: 2026-08-02.
DEFAULT_MAX_SINGLE_POSITION_PCT = 0.15


@dataclass
class Candidate:
    """One BUY the broker's ``_exec_order`` loop considered on this tick."""
    symbol: str
    #: ``cash_per_trade`` as the ALLOCATOR handed it to the broker, i.e. BEFORE
    #: the satellite clamp trimmed it (broker.py:14955-14900). Recovered from
    #: the ``SATELLITE CAP: X trimmed $A -> $B`` line where present, else from
    #: ``Buy gate inputs ... cash_per_trade=$A``.
    alloc_cash: float = 0.0
    price: float = 0.0
    raw_score: float | None = None
    score_provenance: str = "unknown"
    high_conviction: bool = False
    recorded_stage: str = "unobserved"
    recorded_size: float = 0.0
    recorded_filled: float = 0.0
    logged_cash: float | None = None
    logged_reserved: float | None = None
    logged_floor: float | None = None
    logged_effective_floor: float | None = None
    logged_open_pos: int | None = None
    logged_available: float | None = None
    logged_cash_to_use: float | None = None


@dataclass
class Tick:
    index: int
    wall_ts: str
    bar_date: str
    regime: str
    logged_held: int
    logged_cap: int
    #: Book at the START of this tick, walked from the FILL lines and validated
    #: against ``logged_held``. A mismatch is counted and reported, never
    #: silently absorbed.
    positions: dict = field(default_factory=dict)
    prices: dict = field(default_factory=dict)
    cash: float = 0.0
    cash_source: str = "carried"
    logged_turnover_pct: int | None = None
    candidates: list = field(default_factory=list)
    core_pct: float | None = None
    core_target_pct: float | None = None
    held_reconstructed: int = 0
    #: Symbols the run SOLD out of entirely on this tick, from the fills booked
    #: inside the tick. Feeds nexus_broker_utils.max_positions_gate's
    #: ``planned_sells_full_exit`` argument.
    full_exits: set = field(default_factory=set)


@dataclass
class Replay:
    ticks: list
    initial_cash: float
    fills: list
    held_mismatches: int = 0
    scp_events: int = 0
    notes: list = field(default_factory=list)


def _money(text) -> float:
    return float(str(text).replace(",", ""))


def parse_log(lines, initial_cash: float | None = None) -> Replay:
    """Reconstruct per-tick state from a finished backtest log."""
    positions: dict = {}
    prices: dict = {}
    fills: list = []
    scores: dict = {}
    bar_date = ""
    regime = ""
    cash = float(initial_cash or 0.0)
    turnover_pct: int | None = None
    core_pct = core_tgt = None
    scp_events = 0

    ticks: list = []
    cur: Tick | None = None
    by_symbol: dict = {}
    mismatches = 0

    def cand(sym: str) -> Candidate | None:
        if cur is None:
            return None
        key = str(sym).strip().upper()
        c = by_symbol.get(key)
        if c is None:
            known = scores.get(key)
            c = Candidate(symbol=key, price=prices.get(key, 0.0),
                          raw_score=known[0] if known else None,
                          score_provenance=known[1] if known else "unknown")
            by_symbol[key] = c
            cur.candidates.append(c)
        return c

    for raw in lines:
        m = RX_TS.match(raw)
        wall, body = (m.group(1), m.group(3)) if m else ("", raw)

        mm = RX_RUNONCE.search(body)
        if mm:
            bar_date = mm.group(1)
        mm = RX_REGIME.search(body)
        if mm:
            regime = mm.group(1).strip().lower()
        mm = RX_MONITOR.search(body)
        if mm:
            prices[mm.group(1).strip().upper()] = float(mm.group(4))
        mm = RX_BFQ_SCORE.search(body)
        if mm:
            scores[mm.group(1).strip().upper()] = (float(mm.group(2)), "bfq_queue_score")
        mm = RX_BFQ_SIG.search(body)
        if mm:
            scores[mm.group(1).strip().upper()] = (float(mm.group(2)), "bfq_signal_score")
        mm = RX_TBBIND.search(body)
        if mm:
            # PER TICK, never carried. broker.py:14560 emits this line on every
            # tick where the budget binds, so its ABSENCE inside a tick is the
            # run telling us the budget did not bind there.
            turnover_pct = int(mm.group(1))
            if cur is not None:
                cur.logged_turnover_pct = turnover_pct
        mm = RX_CORE_PCT.search(body)
        if mm:
            core_pct, core_tgt = float(mm.group(1)), float(mm.group(2))
            if cur is not None:
                cur.core_pct, cur.core_target_pct = core_pct, core_tgt
        if RX_SCP_CEIL.search(body):
            scp_events += 1

        mm = RX_FILL.search(body)
        if mm:
            side, sym = mm.group(1), mm.group(2).strip().upper()
            qty, px, fee = float(mm.group(3)), float(mm.group(4)), float(mm.group(5))
            prices[sym] = px
            before = positions.get(sym, 0.0)
            positions[sym] = before + (qty if side == "BUY" else -qty)
            if abs(positions[sym]) < 1e-9:
                positions.pop(sym, None)
                if side == "SELL" and before > 0 and cur is not None:
                    cur.full_exits.add(sym)
            cash += (-(qty * px + fee)) if side == "BUY" else (qty * px - fee)
            fills.append(dict(wall=wall, bar=mm.group(6), side=side, symbol=sym,
                              qty=qty, price=px, fee=fee, notional=qty * px,
                              tick=(cur.index if cur is not None else -1)))
            if cur is not None and side == "BUY":
                c = by_symbol.get(sym)
                if c is not None:
                    c.recorded_filled = qty * px
                    c.recorded_stage = "filled"
            continue

        mm = RX_TICK.search(body)
        if mm:
            held_n, cap = int(mm.group(1)), int(mm.group(2))
            recon = {s for s, q in positions.items() if q > 0}
            cur = Tick(index=len(ticks), wall_ts=wall, bar_date=bar_date,
                       regime=regime, logged_held=held_n, logged_cap=cap,
                       positions=dict(positions), prices=dict(prices),
                       cash=cash, logged_turnover_pct=None,
                       core_pct=core_pct, core_target_pct=core_tgt,
                       held_reconstructed=len(recon))
            ticks.append(cur)
            by_symbol = {}
            if len(recon) != held_n:
                mismatches += 1
            continue

        if cur is None:
            continue

        mm = RX_SATTRIM.search(body)
        if mm:
            c = cand(mm.group(1))
            c.alloc_cash = max(c.alloc_cash, _money(mm.group(2)))
            c.recorded_stage = "satellite_trim"
            c.recorded_size = _money(mm.group(3))
            continue
        mm = RX_SATSKIP.search(body)
        if mm:
            cand(mm.group(1)).recorded_stage = "satellite_skip"
            continue
        mm = RX_SATOVF.search(body)
        if mm:
            c = cand(mm.group(1))
            c.raw_score = float(mm.group(2))
            c.score_provenance = "satellite_overflow"
            c.high_conviction = True
            continue
        mm = RX_TBBYPASS.search(body)
        if mm:
            c = cand(mm.group(1))
            c.raw_score = float(mm.group(2))
            c.score_provenance = "turnover_bypass"
            continue
        mm = RX_TBCEIL.search(body)
        if mm:
            c = cand(mm.group(1))
            c.raw_score = float(mm.group(2))
            c.score_provenance = "turnover_bypass_ceiling"
            c.recorded_stage = "turnover_bypass_ceiling"
            continue
        mm = RX_TBBLOCK.search(body)
        if mm:
            cand(mm.group(1)).recorded_stage = "turnover_budget"
            continue
        mm = RX_RCAP.search(body)
        if mm:
            cand(mm.group(1)).recorded_stage = "regime_cap"
            continue
        mm = RX_BUYGATE.search(body)
        if mm:
            c = cand(mm.group(1))
            c.logged_cash = float(mm.group(2))
            c.logged_reserved = float(mm.group(3))
            c.logged_floor = float(mm.group(4))
            c.logged_effective_floor = float(mm.group(5))
            c.high_conviction = c.high_conviction or (mm.group(6) == "True")
            c.logged_open_pos = int(mm.group(7))
            c.alloc_cash = max(c.alloc_cash, float(mm.group(8)))
            c.logged_available = float(mm.group(9))
            c.logged_cash_to_use = float(mm.group(10))
            c.recorded_stage = "buy_gate_" + mm.group(11).lower()
            c.recorded_size = float(mm.group(10))
            # The buy gate prints the emulator's own cash read, which is the
            # authoritative value for this tick (broker.py:15154).
            cur.cash = float(mm.group(2))
            cur.cash_source = "buy_gate"
            cash = cur.cash
            continue
        mm = RX_SINGLE.search(body)
        if mm:
            c = cand(mm.group(1))
            c.recorded_stage = "single_position_cap"
            c.recorded_size = float(mm.group(3))
            continue
        mm = RX_SKIPMIN.search(body)
        if mm:
            c = cand(mm.group(1))
            c.recorded_stage = "min_position"
            c.recorded_size = float(mm.group(2))
            continue
        mm = RX_MPG.search(body)
        if mm:
            cand(mm.group(1)).recorded_stage = "max_positions"
            continue

    return Replay(ticks=ticks, initial_cash=float(initial_cash or 0.0),
                  fills=fills, held_mismatches=mismatches, scp_events=scp_events)


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

#: (key, expected value, regex proving it, human note).
#:
#: Strategy-doc backups on disk are PRE-patch snapshots -- ``doc193_backup_*``
#: is written by ``apply_doc193_*.py`` BEFORE it mutates the doc -- so the
#: config file that looks like a run's config usually lacks the very lever that
#: made the run interesting. bt 820236 is the case in point: every on-disk
#: backup is missing ``turnover_budget_conviction_bypass_enabled`` and the log
#: contains 19 ``TURNOVER BUDGET BYPASS`` lines. Reading the levers OFF THE RUN
#: is the whole point of this harness.
_LOG_CONFIG_FACTS = (
    ("turnover_budget_conviction_bypass_enabled", True,
     re.compile(r"TURNOVER BUDGET BYPASS:"),
     "the run admitted conviction buys through a pinned budget"),
    ("turnover_budget_conviction_bypass_max_pct", ">0",
     re.compile(r"TURNOVER BYPASS CEILING:"),
     "the run refused a conviction buy at the bypass ceiling"),
    ("satellite_conviction_overflow_min_raw_score", ">0",
     re.compile(r"SATELLITE OVERFLOW: \S+ raw=[-+][\d.]+ >= ([\d.]+)"),
     "the run funded a satellite overflow out of the core"),
    ("max_positions_exclude_sleeve_legs", True,
     re.compile(r"max_positions: index-core leg\(s\)"),
     "the run dropped the sleeve legs from the position count"),
    ("core_funding_max_positions_aware", True,
     re.compile(r"\[core\] funding pre-pass: max_positions will refuse"),
     "the core's funding release ran the max_positions pre-pass"),
    ("backtest_credit_sell_proceeds_enabled", True,
     re.compile(r"Sell-proceeds credit: booked"),
     "the run credited same-cycle sell proceeds into the buy ceiling"),
    ("turnover_budget_monthly_pct", ">0",
     re.compile(r"TURNOVER BUDGET BINDING:"),
     "the run's turnover budget bound at least once"),
    ("total_spend_cap_concentrate", True,
     re.compile(r"total-spend cap \[CONCENTRATE\]"),
     "the allocator ran in CONCENTRATE mode"),
    ("core_sleeve_enabled", True,
     re.compile(r"SATELLITE CAP: \S+ (?:trimmed|skipped)"),
     "the index core's satellite clamp was live"),
)


#: A lever's ABSENCE from a log only means something when the run had a chance
#: to exercise it. These say what a chance looks like, so "the log is silent"
#: can be turned into "the flag was not live" rather than left ambiguous.
_LOG_FACT_OPPORTUNITY = {
    "backtest_credit_sell_proceeds_enabled": (
        re.compile(r"FILL SELL "), "SELL fill"),
    "turnover_budget_conviction_bypass_enabled": (
        re.compile(r"TURNOVER BUDGET BINDING:"), "binding tick"),
}


def config_facts_from_log(lines) -> list:
    """Levers the log PROVES were on, and levers it proves were NOT.

    Returns ``[(key, expected, count, note, sample_line, opportunities)]``.
    ``count == 0`` with ``opportunities > 0`` is the interesting case: the run
    had every chance to exercise the lever and never did.
    """
    hits = []
    for key, expected, rx, note in _LOG_CONFIG_FACTS:
        sample, n = None, 0
        for raw in lines:
            if rx.search(raw):
                n += 1
                if sample is None:
                    sample = raw.strip()
        opp = 0
        orx = _LOG_FACT_OPPORTUNITY.get(key)
        if orx is not None:
            opp = sum(1 for raw in lines if orx[0].search(raw))
        if n or opp:
            hits.append((key, expected, n, note, sample, opp))
    # max_positions is printed outright.
    caps = {int(m.group(2)) for m in (RX_TICK.search(l) for l in lines) if m}
    if caps:
        hits.append(("max_positions",
                     sorted(caps)[0] if len(caps) == 1 else sorted(caps),
                     len(caps), "printed by 'max_positions gate armed'", "", 0))
    return hits


def config_from_log_overrides(facts, cfg: dict) -> dict:
    """Config edits that make the baseline match what the LOG proves.

    Only touches the booleans and the cap, i.e. facts the log states outright.
    A ``>0`` threshold is left alone: the log proves it was positive, not what
    it was, and inventing a number is exactly the class of guess this harness
    exists to remove.
    """
    out = {}
    for key, expected, n, _note, _sample, opp in facts:
        if expected is True:
            out[key] = bool(n > 0) if (n > 0 or opp > 0) else cfg.get(key)
            if out[key] is None:
                out.pop(key)
        elif key == "max_positions" and isinstance(expected, int):
            out[key] = expected
    return {k: v for k, v in out.items() if v is not None}


def print_config_facts(facts, cfg: dict):
    print("-- CONFIG FACTS THE LOG PROVES " + "-" * 47)
    if not facts:
        print("  (none of the tracked levers left a trace in this log)")
        print("")
        return
    _MISSING = object()

    def _effective(key):
        """A key may live in the base config OR in a regime_profiles overlay --
        doc-193 puts core_sleeve_enabled / core_target_pct only in the
        overlays, and _apply_regime_profile merges them before any gate reads
        them (core_sleeve.py:157-210)."""
        if key in (cfg or {}):
            return cfg[key], "base"
        for name, over in ((cfg or {}).get("regime_profiles") or {}).items():
            if isinstance(over, dict) and key in over:
                return over[key], f"profile:{name}"
        return _MISSING, "absent"

    disagree = []
    contradicted = []
    for key, expected, n, note, sample, opp in facts:
        have, where = _effective(key)
        if n == 0:
            # The log had chances and never exercised it: the flag was NOT live
            # in this run, whatever the config file says.
            if have is not _MISSING and bool(have):
                contradicted.append((key, opp, _LOG_FACT_OPPORTUNITY[key][1]))
            continue
        if have is _MISSING:
            ok = False
            shown = "<absent>"
        else:
            shown = repr(have)
            if expected is True:
                ok = bool(have) is True
            elif expected == ">0":
                try:
                    ok = float(have) > 0
                except (TypeError, ValueError):
                    ok = False
            else:
                ok = (have == expected)
        mark = "ok " if ok else "!! "
        print(f"  {mark}{key:<44} log={expected!s:<8} config={shown:<10}"
              f" [{where}]  (x{n})")
        if not ok:
            disagree.append((key, expected, have, note, sample))
    for key, opp, what in contradicted:
        print(f"  !! {key:<44} log=NEVER FIRED config=True"
              f"          ({opp} x {what} in the log)")
    if contradicted:
        print("")
        print("     A lever the config claims but the run never exercised, with")
        print("     opportunities on the board, was NOT live in that run. Turn it")
        print("     OFF in the baseline or the A/B below is against a fiction.")
    if disagree:
        print("")
        print("  !! THE SUPPLIED CONFIG DISAGREES WITH THE RUN. Every number below")
        print("     is then an A/B against a baseline that never happened. Fix it:")
        for key, expected, have, note, sample in disagree:
            print(f"       --set {key}={'true' if expected is True else expected}"
                  f"    # {note}")
            if sample:
                print(f"         evidence: {sample[:150]}")
    print("")

def load_config(path: str | None) -> dict:
    """Read a graph_nexus_analysis config out of a strategy-doc JSON.

    Accepts three shapes seen on disk:
      * a full Strategies doc      -> ``{"strategies": [{"config": {...}}]}``
      * a bare config              -> ``{"max_positions": 6, ...}``
      * a list of specs            -> ``[{"strategy": ..., "config": {...}}]``
    """
    if not path:
        return {}
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict) and isinstance(data.get("strategies"), list) and data["strategies"]:
        return dict(data["strategies"][0].get("config") or {})
    if isinstance(data, list) and data and isinstance(data[0], dict):
        for spec in data:
            if "graph_nexus" in str(spec.get("strategy") or "").lower():
                return dict(spec.get("config") or {})
        return dict(data[0].get("config") or data[0])
    if isinstance(data, dict):
        return dict(data)
    raise ValueError(f"unrecognised config shape in {path}")


def _coerce(text: str):
    low = str(text).strip()
    if low.lower() in ("true", "false"):
        return low.lower() == "true"
    if low.lower() in ("none", "null"):
        return None
    try:
        return int(low)
    except ValueError:
        pass
    try:
        return float(low)
    except ValueError:
        pass
    if low.startswith(("{", "[")):
        return json.loads(low)
    return low


def apply_overrides(cfg: dict, pairs) -> dict:
    out = copy.deepcopy(cfg)
    for pair in (pairs or []):
        if "=" not in pair:
            raise ValueError(f"--set expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        out[key.strip()] = _coerce(value)
    return out


def merge_regime_profile(cfg: dict, regime: str) -> dict:
    """Mirror broker.py's ``_apply_regime_profile``: the matching overlay is
    merged into the spec BEFORE any of these gates read it, and the config is
    returned UNCHANGED when no overlay matches.

    This is not cosmetic. doc-193 defines ``core_sleeve_enabled`` and
    ``core_target_pct`` ONLY inside ``regime_profiles.{bull,chop,recovery}``,
    so a harness that read the base config would report the core off on every
    bar and every satellite number would be wrong (core_sleeve.py:157-210
    documents the same trap on the production side).
    """
    over = ((cfg or {}).get("regime_profiles") or {}).get(str(regime or "").strip().lower())
    if not isinstance(over, dict):
        return dict(cfg or {})
    merged = dict(cfg or {})
    merged.update(over)
    return merged


# ---------------------------------------------------------------------------
# GATE CHAIN
# ---------------------------------------------------------------------------

#: Stages in the order broker.py's `_exec_order` loop evaluates them for a BUY.
STAGES = (
    "unsized",                  # the log never printed an allocator size
    "satellite_skip",           # broker.py:14862  room <= _CORE_MIN_SATELLITE_TRIM_USD
    "turnover_budget",          # broker.py:15000
    "turnover_bypass_ceiling",  # broker.py:15044
    "turnover_unknown",         # undecidable: see TurnoverState
    "regime_cap",               # broker.py:15094
    "min_position",             # broker.py:15364  cash_to_use < $50
    "max_positions",            # broker.py:15547
    "emulator_buying_power",    # portfolio_emulator.py:1414-1423 (re-clamp)
    "admitted",
)


@dataclass
class TurnoverState:
    """This tick's turnover-budget verdict, with an explicit unknown.

    The log prints ``TURNOVER BUDGET BINDING: N% of NAV`` only on ticks where
    the budget BOUND (broker.py:14560-14503). On every other tick the run tells
    us only that ``used < budget_of_the_run``. That is enough to answer some
    config questions exactly and not others, and conflating the two is how a
    replay produces a confident wrong number:

      * candidate budget >= the run's budget  -> a non-binding tick stays
        non-binding. EXACT.
      * candidate budget <  the run's budget  -> undecidable on a non-binding
        tick. Reported as ``turnover_unknown`` and counted, never guessed.

    ``source='reconstructed'`` replaces all of this with a ledger rebuilt
    through the real broker helpers from the log's fills; it is what
    ``--turnover reconstructed`` and ``--book projected`` use, and the
    validation block prints how far it lands from the log's own readings.
    """
    used: float = 0.0
    known: bool = True
    budget: float = 0.0
    run_budget: float = 0.0
    source: str = "reconstructed"

    def verdict(self):
        if self.budget <= 0.0:
            return "open", "budget OFF"
        if self.known:
            txt = f"{self.used*100:.0f}%"
            return ("blocked" if self.used >= self.budget else "open"), txt
        # Not printed by the log: used < run_budget is all we know.
        if self.budget >= self.run_budget > 0.0:
            return "open", f"<{self.run_budget*100:.0f}% (log silent)"
        return "unknown", f"<{self.run_budget*100:.0f}% (log silent)"


@dataclass
class Decision:
    symbol: str
    admitted: bool
    stage: str
    size: float
    alloc_cash: float
    note: str = ""
    raw_score: float | None = None
    score_provenance: str = "unknown"
    tick: int = -1
    bar_date: str = ""
    recorded_stage: str = ""
    recorded_size: float = 0.0


class GateChain:
    """Replay the broker's BUY gate chain for one tick under one config.

    Order and semantics mirror broker.py's ``_exec_order`` loop. Where the
    broker calls a helper, so does this; where the broker does arithmetic
    inline, the line reference is on the statement.
    """

    def __init__(self, prod: Production, cfg: dict, *,
                 max_single_position_pct: float = DEFAULT_MAX_SINGLE_POSITION_PCT,
                 turnover_source: str = "from-log",
                 run_budget_pct: float = 0.0):
        self.p = prod
        self.base_cfg = dict(cfg or {})
        self.max_single_position_pct = float(max_single_position_pct)
        self.turnover_source = turnover_source
        #: `turnover_budget_monthly_pct` as the RUN was configured. Needed to
        #: interpret the log's silence on non-binding ticks.
        self.run_budget_pct = float(run_budget_pct or 0.0)
        self.keys_read: set = set()
        #: Core funding pre-pass totals (diagnostic only -- see run_tick).
        self.funding_requested = 0.0
        self.funding_admissible = 0.0

    def turnover_state(self, specs, tick: Tick, nav: float, ledger_date) -> TurnoverState:
        """This tick's budget state under the CANDIDATE config.

        `from-log` takes `used` straight off the run's own
        ``TURNOVER BUDGET BINDING`` line, which is ground truth for the ticks
        that printed one; `reconstructed` runs the real ledger helpers.
        """
        cfg = merge_regime_profile(self.base_cfg, tick.regime)
        budget = float(cfg.get("turnover_budget_monthly_pct", 0.0) or 0.0)
        core_on = self.p.core_sleeve_cfg(specs) is not None
        if not core_on:
            # core_sleeve.turnover_budget_state returns (False, 0.0) when the
            # core is off, so the budget cannot bind either.
            budget = 0.0
        if self.turnover_source == "from-log":
            if tick.logged_turnover_pct is not None:
                return TurnoverState(used=tick.logged_turnover_pct / 100.0,
                                     known=True, budget=budget,
                                     run_budget=self.run_budget_pct,
                                     source="from-log")
            return TurnoverState(used=0.0, known=False, budget=budget,
                                 run_budget=self.run_budget_pct, source="from-log")
        _blocked, used = self.p.core_turnover_state(specs, nav, ledger_date)
        return TurnoverState(used=used, known=True, budget=budget,
                             run_budget=self.run_budget_pct, source="reconstructed")

    # -- helpers ---------------------------------------------------------
    def specs(self, regime: str) -> list:
        cfg = merge_regime_profile(self.base_cfg, regime)
        return [{"strategy": "graph_nexus_analysis", "config": cfg}], cfg

    def book(self, positions: dict, cash: float, initial_cash: float):
        """A real PortfolioEmulator seeded with the replayed book.

        Used rather than a shim so NAV, ``get_cash`` and above all
        ``get_buying_power`` are the production implementations -- the last of
        those is the clamp that made the sell-proceeds credit inert in bt
        498816 (ceiling lifted $700.74 -> $1,397.39, fill still $700.65).
        """
        pe = self.p.PortfolioEmulator(initial_cash=max(1.0, float(initial_cash or 1.0)))
        pe._cash = float(cash or 0.0)
        pe._positions = {s: q for s, q in (positions or {}).items() if q > 0}
        return pe

    # -- the chain -------------------------------------------------------
    def run_tick(self, tick: Tick, *, cash: float, positions: dict,
                 initial_cash: float, ledger_date: str,
                 sell_proceeds: list | None = None) -> list:
        p = self.p
        p.set_regime(tick.regime)
        specs, cfg = self.specs(tick.regime)
        pe = self.book(positions, cash, initial_cash)
        prices = tick.prices
        nav = float(pe.get_portfolio_value(prices) or 0.0)

        cap, cap_reason = p.nbu.resolve_max_positions_cap(specs)
        self.keys_read.add("max_positions")

        held = {str(s).strip().upper() for s, q in (positions or {}).items() if q > 0}
        self.keys_read.add("max_positions_exclude_sleeve_legs")
        if p.max_positions_excludes_sleeve(specs):
            legs = {str(s).strip().upper()
                    for s in p.residual_sleeve_universe_symbols(specs)}
            held = held - legs

        # broker.py:14550-14569 -- the turnover budget is read ONCE per tick.
        # `turnover` is a TurnoverState: `used` may be EXACT (the log printed
        # it) or UNKNOWN (the log only prints a reading on ticks where the
        # budget bound). See TurnoverState for how an unknown is handled
        # instead of guessed.
        turnover = self.turnover_state(specs, tick, nav, ledger_date)
        self.keys_read.update({"turnover_budget_monthly_pct", "core_sleeve_enabled"})
        conv_min = p.satellite_conviction_min_raw(specs)
        bypass_on = p.turnover_cfg_conviction_bypass(specs)
        bypass_ceiling = p.turnover_cfg_bypass_ceiling(specs)
        self.keys_read.update({
            "satellite_conviction_overflow_min_raw_score",
            "turnover_budget_conviction_bypass_enabled",
            "turnover_budget_conviction_bypass_max_pct",
        })
        rc = p.regime_position_cap_hard(specs)

        # Same-cycle sell-proceeds credit -- broker.py:14227-14245 / 15133-15142.
        # In BACKTEST the credit only arms when
        # `backtest_credit_sell_proceeds_enabled` is set; live it is always on.
        # The proceeds list is APPROXIMATED by the SELL fills the log printed
        # inside this tick (broker.py books qty x frac x price at submit, and
        # under next-event execution the log prints the resulting fill in the
        # same tick block -- verified on bt 498816's 2026-01-16 CPER sell).
        scp_on = bool(cfg.get("backtest_credit_sell_proceeds_enabled", False))
        self.keys_read.add("backtest_credit_sell_proceeds_enabled")
        scp_proceeds = list(sell_proceeds or []) if scp_on else []

        emitted: set = set()
        full_exits = set(tick.full_exits)
        out: list = []
        sim_cash = float(cash or 0.0)
        sim_positions = dict(positions or {})

        # -- CORE FUNDING PRE-PASS (diagnostic, does not move the book) -------
        # broker.py's `_core_funding_mpg_aware` block sizes the core's release
        # off the buys the allocator approved, then trims it to the buys the
        # position cap will actually admit. Replaying it with the real
        # `max_positions_admissible_buys` says how much SPY the bar was about to
        # sell for orders that were never going to emit -- the churn loop bt
        # 455506 measured at $9,081 of gross for -1.37 shares of net.
        self.keys_read.add("core_funding_max_positions_aware")
        ordered = [c.symbol for c in tick.candidates if float(c.alloc_cash or 0) > 0]
        admissible = p.nbu.max_positions_admissible_buys(
            held, cap, full_exits, ordered)
        self.funding_requested += sum(float(c.alloc_cash or 0.0)
                                      for c in tick.candidates)
        self.funding_admissible += sum(
            float(c.alloc_cash or 0.0) for c in tick.candidates
            if c.symbol in admissible)

        for c in tick.candidates:
            sym = c.symbol
            price = float(c.price or prices.get(sym, 0.0) or 0.0)
            alloc = float(c.alloc_cash or 0.0)
            d = Decision(symbol=sym, admitted=False, stage="", size=0.0,
                         alloc_cash=alloc, raw_score=c.raw_score,
                         score_provenance=c.score_provenance, tick=tick.index,
                         bar_date=tick.bar_date, recorded_stage=c.recorded_stage,
                         recorded_size=c.recorded_size)
            if price <= 0.0:
                # No price in the log for a name that was never held. Only the
                # single-position cap's existing-value term and the projected
                # book need it, and both are zero for a new name.
                d.note = "no price in log (new name)"

            pe_now = self.book(sim_positions, sim_cash, initial_cash)
            nav_now = float(pe_now.get_portfolio_value(prices) or 0.0)

            # 1) SATELLITE CAP / OVERFLOW -- broker.py:14909-14959.
            #    Evaluated BEFORE the size check because the SKIP branch does
            #    not depend on the size: the log's `SATELLITE CAP: X skipped`
            #    line never prints one, and refusing to score those would drop
            #    the run's second-largest refusal class out of the report.
            raw = c.raw_score
            is_conv = bool(conv_min > 0 and raw is not None and raw >= conv_min)
            room = p.core_sleeve_satellite_headroom(
                pe_now, prices, specs, conviction=is_conv)
            self.keys_read.update({"core_target_pct", "core_min_pct",
                                   "cash_reserve_floor_pct",
                                   "satellite_conviction_reserve_pct",
                                   "residual_sleeve_symbol",
                                   "residual_sleeve_bear_symbol"})
            if room is not None and room <= p.ns["_CORE_MIN_SATELLITE_TRIM_USD"]:
                d.stage = "satellite_skip"
                d.note = f"headroom ${room:,.0f} <= ${p.ns['_CORE_MIN_SATELLITE_TRIM_USD']:.0f}"
                out.append(d)
                continue

            if alloc <= 0.0:
                # Past the satellite gate but the log never printed the size
                # the allocator had given it. Counted separately so it cannot
                # be mistaken for a gate verdict.
                d.stage = "unsized"
                d.note = "allocator size never printed for this candidate"
                out.append(d)
                continue

            cash_per_trade = alloc
            if room is not None and cash_per_trade > room:
                cash_per_trade = room
                d.note = f"satellite trim ${alloc:,.0f}->${room:,.0f}"

            # 2) TURNOVER BUDGET -- broker.py:15034-15081.
            verdict, used_txt = turnover.verdict()
            if verdict == "unknown":
                d.stage = "turnover_unknown"
                d.note = (f"budget lowered to {turnover.budget*100:.0f}% but the "
                          f"log never printed this tick's usage ({used_txt}) -- "
                          f"undecidable from this log")
                out.append(d)
                continue
            if verdict == "blocked":
                allow = False
                if conv_min > 0 and bypass_on and raw is not None and raw >= conv_min:
                    if bypass_ceiling > 0 and turnover.used >= bypass_ceiling:
                        d.stage = "turnover_bypass_ceiling"
                        d.note = (f"{used_txt} >= ceiling "
                                  f"{bypass_ceiling*100:.0f}%")
                        out.append(d)
                        continue
                    allow = True
                if not allow:
                    d.stage = "turnover_budget"
                    d.note = (f"{used_txt} of NAV in 21 sessions"
                              + ("" if raw is not None else "; raw_net_score UNKNOWN in log"))
                    out.append(d)
                    continue

            # 3) REGIME CAP HARD -- broker.py:15082-15115 (excludes sleeve legs).
            if rc is not None:
                sleeve = p.residual_sleeve_config(specs)
                excl = {sleeve.get("symbol") or "", sleeve.get("bear_symbol") or ""}
                open_n = sum(1 for s, q in sim_positions.items()
                             if float(q or 0.0) > 0.0 and s not in excl)
                self.keys_read.update({"max_positions_bull", "max_positions_chop",
                                       "max_positions_bear", "max_positions_crash",
                                       "regime_position_cap_hard_enforce"})
                if float(sim_positions.get(sym, 0.0) or 0.0) <= 0.0 and open_n >= rc[1]:
                    d.stage = "regime_cap"
                    d.note = f"held={open_n} >= cap={rc[1]} (regime={rc[0]})"
                    out.append(d)
                    continue

            # 4) CASH FLOOR + available -- broker.py:15116-15203.
            floor_pct = float(cfg.get("cash_reserve_floor_pct", 0.10) or 0.0)
            floor_hard = bool(cfg.get("cash_reserve_floor_hard", True))
            floor_min_pos = int(cfg.get("cash_reserve_hard_min_positions", 5) or 5)
            floor_release = bool(cfg.get("cash_reserve_release_after_min_positions", True))
            self.keys_read.update({"cash_reserve_floor_hard",
                                   "cash_reserve_hard_min_positions",
                                   "cash_reserve_release_after_min_positions"})
            cf_exclude: set = set()
            if p.core_sleeve_cfg(specs) is not None:
                sleeve = p.residual_sleeve_config(specs)
                cf_exclude = {sleeve.get("symbol") or "",
                              sleeve.get("bear_symbol") or ""} - {""}
            open_positions = sum(1 for s, q in sim_positions.items()
                                 if float(q or 0.0) > 0.0 and s not in cf_exclude)
            high_conv = bool(c.high_conviction) or is_conv
            if floor_hard:
                can_bypass = floor_release and high_conv and open_positions >= floor_min_pos
            else:
                can_bypass = high_conv
            cash_floor = float(initial_cash) * floor_pct
            effective_floor = 0.0 if can_bypass else cash_floor
            reserved = float(c.logged_reserved or 0.0)
            sizing_ceiling = float(pe_now.get_cash() or 0.0)
            if scp_proceeds:
                lifted = p.nbu.buy_ceiling(sizing_ceiling, scp_proceeds, enabled=True)
                if lifted > sizing_ceiling:
                    d.note = (d.note + "; " if d.note else "") +                         f"sell-proceeds ceiling ${sizing_ceiling:,.2f}->${lifted:,.2f}"
                sizing_ceiling = lifted
            available = max(0.0, sizing_ceiling - reserved - effective_floor)
            cash_to_use = min(cash_per_trade, available)

            # 5) BROKER SINGLE-POSITION CAP -- broker.py:15221-15252.
            if self.max_single_position_pct > 0:
                equity = nav_now if nav_now > 0 else float(initial_cash)
                existing = float(sim_positions.get(sym, 0.0) or 0.0) * price
                headroom = max(0.0, equity * self.max_single_position_pct - existing)
                if cash_to_use > headroom:
                    cash_to_use = headroom
                    d.note = (d.note + "; " if d.note else "") + \
                        f"single-position cap -> ${headroom:,.0f}"

            # 6) EXECUTION MINIMUM -- broker.py:15364.
            if cash_to_use < EXEC_MIN_POSITION_USD and cash_to_use < cash_per_trade:
                d.stage = "min_position"
                d.size = cash_to_use
                d.note = (d.note + "; " if d.note else "") + \
                    f"cash_to_use ${cash_to_use:,.2f} < ${EXEC_MIN_POSITION_USD:.0f}"
                out.append(d)
                continue

            # 7) MAX_POSITIONS -- broker.py:15547, the real gate function.
            if cap is not None:
                if not p.nbu.max_positions_gate(held, cap, full_exits, emitted, sym):
                    proj = p.nbu.max_positions_projected_count(held, full_exits, emitted)
                    d.stage = "max_positions"
                    d.note = f"held={proj}, cap={cap}"
                    out.append(d)
                    continue

            # 8) EMULATOR RE-CLAMP -- portfolio_emulator.py:1414-1423. The
            #    broker approved `cash_to_use`; the emulator will only spend
            #    what get_buying_power() allows. This is the step that made the
            #    sell-proceeds credit inert in bt 498816.
            bp = float(pe_now.get_buying_power(reserved) or 0.0)
            filled = min(cash_to_use, bp)
            if filled + 1e-9 < cash_to_use:
                d.note = (d.note + "; " if d.note else "") + \
                    f"emulator clamp ${cash_to_use:,.2f}->${filled:,.2f}"
            if filled < EXEC_MIN_POSITION_USD:
                d.stage = "emulator_buying_power"
                d.size = filled
                out.append(d)
                continue

            d.admitted = True
            d.stage = "admitted"
            d.size = filled
            out.append(d)
            if cap is not None and sym not in held:
                emitted.add(sym)
            sim_cash = max(0.0, sim_cash - filled)
            if price > 0:
                sim_positions[sym] = sim_positions.get(sym, 0.0) + (filled / price)
            else:
                # No price in the log for this name. It still occupies a slot
                # and still consumes cash; it just cannot be marked. Recorded
                # as a sentinel qty so the position count is right and any
                # later valuation of it is visibly zero rather than invented.
                sim_positions[sym] = sim_positions.get(sym, 0.0) + 1e-6

        return out


# ---------------------------------------------------------------------------
# SIMULATION DRIVER
# ---------------------------------------------------------------------------

@dataclass
class Result:
    label: str
    mode: str
    decisions: list = field(default_factory=list)
    by_stage: collections.Counter = field(default_factory=collections.Counter)
    admitted_notional: float = 0.0
    admitted_names: collections.Counter = field(default_factory=collections.Counter)
    end_book: dict = field(default_factory=dict)
    end_cash: float = 0.0
    end_nav: float = 0.0
    keys_read: set = field(default_factory=set)
    turnover_checks: list = field(default_factory=list)
    unscored: int = 0
    turnover_source: str = "from-log"
    funding_requested: float = 0.0
    funding_admissible: float = 0.0


def _fills_by_tick(replay: Replay) -> dict:
    out = collections.defaultdict(list)
    for f in replay.fills:
        out[f.get("tick", -1)].append(f)
    return out


def _sleeve_symbols_of(prod: Production, specs) -> set:
    try:
        return {str(s).strip().upper()
                for s in prod.residual_sleeve_universe_symbols(specs)}
    except Exception:
        return set()


def simulate(prod: Production, replay: Replay, cfg: dict, *,
             mode: str = "frozen", label: str = "cfg",
             max_single_position_pct: float = DEFAULT_MAX_SINGLE_POSITION_PCT,
             turnover_source: str = "from-log",
             run_budget_pct: float = 0.0) -> Result:
    """Walk every tick and score every candidate.

    ``mode='frozen'``    -- book/cash/prices per tick are exactly the RUN's.
                            Pure arithmetic, reproducible from the log.
    ``mode='projected'`` -- admitted buys are carried into a shadow book; the
                            run's SELL fills are applied proportionally and its
                            broker-owned core-leg fills verbatim. Diverges from
                            the run by construction; see the module docstring.

    The turnover ledger is maintained through the REAL broker helpers
    (``_turnover_ledger_record`` / ``_turnover_ledger_rolling``) under the real
    exemption rule (``_turnover_is_governed``), so the core's exemption and the
    21-session window come from production, not from a copy here.
    """
    chain = GateChain(prod, cfg, max_single_position_pct=max_single_position_pct,
                      turnover_source=turnover_source, run_budget_pct=run_budget_pct)
    res = Result(label=label, mode=mode)
    res.turnover_source = turnover_source
    prod.reset_ledger()
    fills_by_tick = _fills_by_tick(replay)

    shadow_positions: dict = {}
    shadow_cash = float(replay.initial_cash or 0.0)

    for tick in replay.ticks:
        positions = shadow_positions if mode == "projected" else tick.positions
        cash = shadow_cash if mode == "projected" else tick.cash

        specs, _merged = chain.specs(tick.regime)
        sleeve_syms = _sleeve_symbols_of(prod, specs)
        ledger_date = tick.bar_date or tick.wall_ts[:10]

        prod.set_regime(tick.regime)
        pe = chain.book(positions, cash, replay.initial_cash)
        nav = float(pe.get_portfolio_value(tick.prices) or 0.0)
        # Always compute the reconstructed ledger reading so --validate can
        # report how far it lands from the log's own, even when the gates are
        # being driven from the log.
        _blocked, used = prod.core_turnover_state(specs, nav, ledger_date)
        if tick.logged_turnover_pct is not None:
            res.turnover_checks.append((tick.index, tick.logged_turnover_pct,
                                        int(round(used * 100.0))))

        tick_sells = [f["notional"] for f in fills_by_tick.get(tick.index, [])
                      if f["side"] == "SELL"]
        decisions = chain.run_tick(tick, cash=cash, positions=positions,
                                   initial_cash=replay.initial_cash,
                                   ledger_date=ledger_date,
                                   sell_proceeds=tick_sells)
        prices_here = tick.prices
        for d in decisions:
            res.decisions.append(d)
            res.by_stage[d.stage] += 1
            if d.raw_score is None:
                res.unscored += 1
            if not d.admitted:
                continue
            res.admitted_notional += d.size
            res.admitted_names[d.symbol] += 1
            # broker.py:15756 -- book one-way notional, governed symbols only.
            if prod.turnover_is_governed(d.symbol, specs):
                prod.turnover_ledger_record(ledger_date, d.size, "sim buy")
            if mode == "projected":
                price = next((c.price for c in tick.candidates
                              if c.symbol == d.symbol and c.price > 0),
                             float(prices_here.get(d.symbol, 0.0) or 0.0))
                if price > 0:
                    shadow_positions[d.symbol] = (
                        shadow_positions.get(d.symbol, 0.0) + d.size / price)
                    shadow_cash = max(0.0, shadow_cash - d.size)

        for f in fills_by_tick.get(tick.index, []):
            if f["side"] == "SELL":
                if prod.turnover_is_governed(f["symbol"], specs):
                    prod.turnover_ledger_record(ledger_date, f["notional"], "replay sell")
                if mode == "projected":
                    have = shadow_positions.get(f["symbol"], 0.0)
                    sold = min(have, f["qty"])
                    if sold > 0:
                        shadow_positions[f["symbol"]] = have - sold
                        shadow_cash += sold * f["price"]
                    if shadow_positions.get(f["symbol"], 0.0) <= 1e-9:
                        shadow_positions.pop(f["symbol"], None)
            elif mode == "projected" and f["symbol"] in sleeve_syms:
                # The core leg is broker-owned, not a strategy candidate, so it
                # is replayed verbatim rather than re-decided.
                shadow_positions[f["symbol"]] = (
                    shadow_positions.get(f["symbol"], 0.0) + f["qty"])
                shadow_cash = max(0.0, shadow_cash - f["notional"])

    last = replay.ticks[-1] if replay.ticks else None
    if mode == "projected":
        res.end_book, res.end_cash = dict(shadow_positions), shadow_cash
    elif last:
        res.end_book, res.end_cash = dict(last.positions), last.cash
    if last:
        res.end_nav = res.end_cash + sum(
            q * float(last.prices.get(s, 0.0) or 0.0)
            for s, q in res.end_book.items())
    res.keys_read = set(chain.keys_read)
    res.funding_requested = chain.funding_requested
    res.funding_admissible = chain.funding_admissible
    return res


# ---------------------------------------------------------------------------
# VALIDATION -- does the reconstruction agree with the log?
# ---------------------------------------------------------------------------

#: Which simulated stage a recorded stage should map to when the harness is
#: replaying the run's OWN config. `satellite_trim` / `single_position_cap` are
#: trims, not verdicts -- the run went on to some later stage the log then
#: overwrote -- so they are excluded from the agreement rate rather than
#: scored against a guess.
_RECORDED_TO_SIM = {
    "filled": "admitted",
    "buy_gate_pass": "admitted",
    "max_positions": "max_positions",
    "turnover_budget": "turnover_budget",
    "turnover_bypass_ceiling": "turnover_bypass_ceiling",
    "satellite_skip": "satellite_skip",
    "regime_cap": "regime_cap",
    "min_position": "min_position",
    "buy_gate_skip": "min_position",
}
_FIDELITY_EXCLUDED = {"satellite_trim", "single_position_cap", "unobserved"}


def fidelity(res: Result):
    """How often does the replay reproduce the run's OWN verdict?

    This is the harness's credibility number, and it only means anything when
    the ``--config`` handed in is the config the run actually used. A low
    agreement rate almost always means the config is wrong (a strategy doc on
    disk is a PRE-patch backup, so the key that made the run interesting is
    often missing from it) -- fix that before reading any A/B below it.
    """
    agree = disagree = skipped = 0
    confusion = collections.Counter()
    for d in res.decisions:
        expected = _RECORDED_TO_SIM.get(d.recorded_stage)
        if d.recorded_stage in _FIDELITY_EXCLUDED or expected is None:
            skipped += 1
            continue
        if expected == d.stage:
            agree += 1
        else:
            disagree += 1
            confusion[(d.recorded_stage, d.stage)] += 1
    total = agree + disagree
    return dict(agree=agree, disagree=disagree, skipped=skipped,
                rate=(agree / total * 100.0) if total else 0.0,
                confusion=confusion)


def print_fidelity(res: Result):
    f = fidelity(res)
    print("-- BASELINE FIDELITY (does the replay reproduce the run?) " + "-" * 19)
    print(f"  scored candidates          {f['agree'] + f['disagree']}"
          f"   (excluded {f['skipped']}: trims and unobserved)")
    print(f"  agreement with the log     {f['rate']:.1f}%"
          f"  ({f['agree']} agree, {f['disagree']} disagree)")
    if f["confusion"]:
        print("  disagreements (log stage -> replay stage):")
        for (rec, sim), n in f["confusion"].most_common(12):
            print(f"    {rec:<24} -> {sim:<24} x{n}")
    if f["rate"] < 85.0:
        print("")
        print("  !! BELOW 85%. The --config handed in is probably NOT the config")
        print("     this run used -- see CONFIG FACTS above. Strategy-doc backups")
        print("     on disk are PRE-patch snapshots, so the lever that made the")
        print("     run interesting is often missing. Fix that before reading any")
        print("     A/B underneath it.")
    elif f["disagree"]:
        print("")
        print("  Residual disagreement is dominated by candidates whose")
        print("  raw_net_score the log never printed (see the validation block):")
        print("  the replay falls back to the backfill-queue score, which is a")
        print("  different number on a similar scale.")
    print("")


def validate(replay: Replay, res: Result) -> list:
    """Cross-checks the reconstruction against numbers the log printed itself.

    A harness whose reconstruction disagrees with the run is worse than no
    harness, so these print by default rather than hiding behind a flag.
    """
    rows = []
    ticks = replay.ticks
    rows.append(("ticks parsed", str(len(ticks)), ""))
    rows.append(("held-count mismatches vs 'max_positions gate armed: held='",
                 str(replay.held_mismatches), "0 is the only acceptable value"))
    recorded = collections.Counter(c.recorded_stage
                                   for t in ticks for c in t.candidates)
    rows.append(("candidates observed", str(sum(recorded.values())),
                 ", ".join(f"{k}={v}" for k, v in recorded.most_common())))
    rows.append(("BUY fills in log",
                 str(sum(1 for f in replay.fills if f["side"] == "BUY")), ""))
    rows.append(("sell-proceeds-credit events in log", str(replay.scp_events),
                 "0 means backtest_credit_sell_proceeds_enabled never fired"))
    if res.turnover_checks:
        diffs = sorted(abs(a - b) for _i, a, b in res.turnover_checks)
        rows.append(("turnover % of NAV, log vs reconstructed",
                     f"n={len(diffs)} median_abs_diff={diffs[len(diffs)//2]}pp",
                     "a large gap means the ledger reconstruction is "
                     "incomplete and the budget verdicts are indicative only"))
    rows.append(("candidates with NO raw_net_score in the log", str(res.unscored),
                 "a score-threshold lever's answer over these is a LOWER BOUND"))
    return rows


# ---------------------------------------------------------------------------
# REPORTING
# ---------------------------------------------------------------------------

BANNER = "=" * 78


def _fmt_money(v):
    return f"${v:,.0f}"


def print_header(log_path: Path, replay: Replay, mode: str, label: str,
                 turnover_source: str, broker_fp):
    print(BANNER)
    print(f"ALLOCATION + GATE SIMULATION -- {log_path.name}")
    print(BANNER)
    print(f"config           : {label}")
    print(f"book propagation : {mode}")
    print(f"turnover source  : {turnover_source}")
    print(f"broker.py         : sha256[:12]={broker_fp[0]} ({broker_fp[1]} lines)")
    print(f"                    line references in this file are indicative; the")
    print(f"                    replay binds to broker.py by NAME, not by line.")
    if mode == "projected":
        print("  !! PROJECTED MODE: admitted buys are carried forward into a shadow")
        print("     book. This DIVERGES from the run at the first differing")
        print("     admission and is an upper bound, not a result.")
    print(f"ticks            : {len(replay.ticks)}"
          f"   bars {replay.ticks[0].bar_date if replay.ticks else '?'}"
          f" .. {replay.ticks[-1].bar_date if replay.ticks else '?'}")
    print(f"initial cash     : {_fmt_money(replay.initial_cash)}")
    print("")
    print("MODELS: broker-side satellite clamp, turnover budget, regime cap,")
    print("        cash floor, 15% single-position cap, $50 execution minimum,")
    print("        max_positions, emulator buying-power re-clamp.")
    print("DOES NOT MODEL: P&L, fill prices, price impact, downstream")
    print("        divergence, the strategy-side allocator, or any live-only")
    print("        gate. See the module docstring before quoting a number.")
    print("")


def print_validation(rows):
    print("-- RECONSTRUCTION VALIDATION " + "-" * 49)
    for name, value, note in rows:
        print(f"  {name:<58} {value}")
        if note:
            print(f"      note: {note}")
    print("")


def print_result(res: Result, replay: Replay):
    print(f"-- OUTCOME [{res.label}] " + "-" * max(0, 56 - len(res.label)))
    total = sum(res.by_stage.values())
    order = [s for s in STAGES if s in res.by_stage] + \
            [s for s in res.by_stage if s not in STAGES]
    for stage in order:
        n = res.by_stage[stage]
        share = (n / total * 100.0) if total else 0.0
        print(f"  {stage:<26} {n:>5}   {share:5.1f}%")
    print(f"  {'TOTAL candidates':<26} {total:>5}")
    print("")
    print(f"  admitted notional          {_fmt_money(res.admitted_notional)}"
          f"   ({res.admitted_notional / max(1.0, replay.initial_cash) * 100:.0f}%"
          f" of initial cash, summed over the window)")
    print("      A FLOW, not a book. Every tick is sized against the cash the RUN")
    print("      had, so admissions on later ticks do not know about earlier ones")
    print("      in frozen mode. Read it as gate throughput, never as exposure.")
    print(f"  distinct names admitted    {len(res.admitted_names)}")
    top = ", ".join(f"{s}x{n}" for s, n in res.admitted_names.most_common(12))
    if top:
        print(f"  most-admitted names        {top}")
    print("")
    book = {s: q for s, q in res.end_book.items() if q > 1e-9}
    print(f"  end book ({len(book)} names)  "
          + ", ".join(f"{s}={q:.4f}" for s, q in sorted(book.items())))
    wasted = res.funding_requested - res.funding_admissible
    print(f"  core funding pre-pass      requested {_fmt_money(res.funding_requested)}"
          f", max_positions-admissible {_fmt_money(res.funding_admissible)}")
    print(f"                             {_fmt_money(wasted)} of the release was for"
          f" buys the cap would refuse")
    print("")
    print(f"  end cash                   {_fmt_money(res.end_cash)}")
    print(f"  end NAV (marked at the log's last price)  {_fmt_money(res.end_nav)}")
    print("      NOT a P&L number: the marks come from the RECORDED run, and in")
    print("      projected mode the book that produced them never existed.")
    print("")


def print_verbose(res: Result, limit=None):
    print("-- EVERY CANDIDATE " + "-" * 59)
    print(f"  {'bar':<11}{'sym':<7}{'alloc$':>9}{'size$':>9}  {'sim stage':<24}"
          f"{'recorded stage':<22}{'raw':>7}  note")
    rows = res.decisions if limit is None else res.decisions[:limit]
    for d in rows:
        raw = f"{d.raw_score:+.3f}" if d.raw_score is not None else "  ?  "
        print(f"  {d.bar_date:<11}{d.symbol:<7}{d.alloc_cash:>9,.0f}{d.size:>9,.0f}  "
              f"{d.stage:<24}{d.recorded_stage:<22}{raw:>7}  {d.note}")
    if limit is not None and len(res.decisions) > limit:
        print(f"  ... {len(res.decisions) - limit} more (use --verbose-limit 0 for all)")
    print("")


def print_diff(a: Result, b: Result):
    print(BANNER)
    print(f"DIFF  {a.label}  ->  {b.label}")
    print(BANNER)
    stages = sorted(set(a.by_stage) | set(b.by_stage),
                    key=lambda s: STAGES.index(s) if s in STAGES else 99)
    print(f"  {'stage':<26}{'A':>7}{'B':>7}{'delta':>8}")
    for s in stages:
        na, nb = a.by_stage.get(s, 0), b.by_stage.get(s, 0)
        print(f"  {s:<26}{na:>7}{nb:>7}{nb - na:>+8}")
    print("")
    delta_usd = b.admitted_notional - a.admitted_notional
    print(f"  admitted notional     {_fmt_money(a.admitted_notional):>12}"
          f"{_fmt_money(b.admitted_notional):>14}"
          f"{('+' if delta_usd >= 0 else '-') + _fmt_money(abs(delta_usd)):>14}")
    dn = len(b.admitted_names) - len(a.admitted_names)
    print(f"  distinct names        {len(a.admitted_names):>12}"
          f"{len(b.admitted_names):>14}{dn:>+14}")
    print("")
    ka = {(d.tick, d.symbol) for d in a.decisions if d.admitted}
    kb = {(d.tick, d.symbol) for d in b.decisions if d.admitted}
    gained = collections.Counter(s for _t, s in (kb - ka))
    lost = collections.Counter(s for _t, s in (ka - kb))
    bysym_b = {(d.tick, d.symbol): d for d in b.decisions}
    if gained:
        print("  NEWLY ADMITTED under B (tick-level):")
        for s, n in gained.most_common(25):
            usd = sum(bysym_b[(t, sym)].size for (t, sym) in (kb - ka) if sym == s)
            print(f"    {s:<7} x{n:<4} {_fmt_money(usd)}")
    if lost:
        print("  NO LONGER ADMITTED under B (tick-level):")
        for s, n in lost.most_common(25):
            print(f"    {s:<7} x{n}")
    if not gained and not lost:
        print("  no admission changed. The lever is INERT on this log, or the key")
        print("  it changes is read by the STRATEGY, which this harness replays.")
    print("")
    print("  A changed admission is NOT a changed P&L. bt 718249 relaxed the")
    print("  position cap, admitted five more names, and returned +4.23% against")
    print("  +12.33% for the tighter arm.")
    print("")


def print_override_shadowing(cfg: dict, overrides, regimes):
    """Warn when a --set key is SHADOWED by a regime_profiles overlay.

    `_apply_regime_profile` merges the matching overlay ON TOP of the base
    config before any gate reads it, so `--set core_target_pct=0.20` on a doc
    that defines `core_target_pct` inside `regime_profiles.chop` changes
    nothing. doc-193 does exactly this for `core_sleeve_enabled`,
    `core_target_pct`, `core_rebalance_band_pct` and `core_rebalance_min_days`
    (core_sleeve.py:157-210 documents the same trap on the production side), so
    this is the single easiest way to run a lever test that silently tested
    nothing.
    """
    profiles = (cfg or {}).get("regime_profiles") or {}
    if not isinstance(profiles, dict) or not overrides:
        return
    hit = []
    for pair in overrides:
        key = pair.split("=", 1)[0].strip()
        where = [name for name, over in profiles.items()
                 if isinstance(over, dict) and key in over
                 and str(name).strip().lower() in regimes]
        if where:
            hit.append((key, sorted(where)))
    if not hit:
        return
    print("   !! --set keys SHADOWED by a regime_profiles overlay (the overlay")
    print("      is merged on top before any gate reads the config, so these")
    print("      --set values had NO effect):")
    for key, where in hit:
        print(f"      {key}  overridden by regime_profiles.{'/'.join(where)}")
        example = json.dumps({where[0]: {key: "<value>"}})
        print("        set it inside the profile instead, e.g.")
        print(f"        --set 'regime_profiles={example}'")
    print("")


def print_keys(res: Result, cfg: dict, overrides):
    read = res.keys_read
    print("-- CONFIG KEYS THIS HARNESS ACTUALLY CONSUMED " + "-" * 32)
    print("   " + ", ".join(sorted(read)))
    touched = {p.split("=", 1)[0].strip() for p in (overrides or [])}
    ignored = sorted(touched - read)
    if ignored:
        print("")
        print("   !! --set keys NOT read by any gate replayed here:")
        for k in ignored:
            print(f"      {k}   (strategy-side or unmodelled -- this run cannot")
            print(f"           tell you anything about it)")
    print("")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", help="a finished backtest log (backtests/<id>_*.log)")
    ap.add_argument("--config", help="strategy doc / config JSON for the BASELINE arm")
    ap.add_argument("--fix-config-from-log", action="store_true",
                    help="start the BASELINE from the levers the log PROVES "
                         "(CONFIG FACTS), then apply --base-set on top. Use this "
                         "when the on-disk doc backup is a pre-patch snapshot.")
    ap.add_argument("--base-set", action="append", default=[], metavar="KEY=VALUE",
                    help="fix the BASELINE config, e.g. to add a lever the on-disk "
                         "doc backup is missing (CONFIG FACTS tells you which)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a config key for the CANDIDATE arm (repeatable)")
    ap.add_argument("--candidate-config",
                    help="a second config JSON instead of --set")
    ap.add_argument("--diff", action="store_true",
                    help="(the diff always prints when a candidate arm exists; "
                         "kept for explicitness in scripts)")
    ap.add_argument("--book", choices=("frozen", "projected"), default="frozen",
                    help="book propagation mode (default frozen; read the docstring)")
    ap.add_argument("--turnover", choices=("from-log", "reconstructed"),
                    default="from-log",
                    help="'from-log' (default) takes this tick's turnover usage "
                         "straight off the run's own TURNOVER BUDGET BINDING line "
                         "and reports 'turnover_unknown' where the log is silent; "
                         "'reconstructed' rebuilds the ledger through the real "
                         "broker helpers (see --validate for how far off it lands)")
    ap.add_argument("--initial-cash", type=float, default=None,
                    help="starting cash; default is read from the log's first "
                         "'Buy gate inputs' line")
    ap.add_argument("--max-single-position-pct", type=float,
                    default=DEFAULT_MAX_SINGLE_POSITION_PCT,
                    help="BROKER_MAX_SINGLE_POSITION_PCT (default 0.15)")
    ap.add_argument("--verbose", action="store_true", help="print every candidate")
    ap.add_argument("--verbose-limit", type=int, default=60,
                    help="rows for --verbose (0 = all)")
    ap.add_argument("--json", help="write the full result to this path")
    ap.add_argument("--no-validate", action="store_true")
    return ap


def infer_initial_cash(lines) -> float:
    """First ``Buy gate inputs`` cash reading -- the emulator's own number
    before anything was spent. Falls back to 0.0, which makes NAV-relative
    numbers obviously wrong rather than quietly wrong."""
    for raw in lines:
        m = RX_BUYGATE.search(raw)
        if m:
            return float(m.group(2))
    return 0.0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.log)
    if not path.is_file():
        print(f"no such log: {path}", file=sys.stderr)
        return 2
    lines = path.read_text(errors="replace").splitlines()

    initial_cash = args.initial_cash
    if initial_cash is None:
        initial_cash = infer_initial_cash(lines)
    replay = parse_log(lines, initial_cash=initial_cash)
    if not replay.ticks:
        print("no 'max_positions gate armed' lines in this log -- the gate never "
              "armed, so there is no tick boundary to replay against.",
              file=sys.stderr)
        return 3

    prod = Production()
    base_cfg = load_config(args.config)
    base_label = Path(args.config).name if args.config else "empty-config"
    facts = config_facts_from_log(lines)
    fixes = {}
    if args.fix_config_from_log:
        fixes = config_from_log_overrides(facts, base_cfg)
        base_cfg = dict(base_cfg, **fixes)
        base_label += f" +from-log({len(fixes)} levers)"
    base_cfg = apply_overrides(base_cfg, args.base_set)
    if args.base_set:
        base_label += " +" + "+".join(args.base_set)

    run_budget = float(base_cfg.get("turnover_budget_monthly_pct", 0.0) or 0.0)
    turnover_source = "reconstructed" if args.book == "projected" else args.turnover
    print_header(path, replay, args.book, base_label, turnover_source,
                 prod.broker_fingerprint)
    if fixes:
        print("-- BASELINE PATCHED FROM THE LOG " + "-" * 45)
        for k, v in sorted(fixes.items()):
            print(f"   {k} = {v}")
        print("")

    base = simulate(prod, replay, base_cfg, mode=args.book, label=base_label,
                    max_single_position_pct=args.max_single_position_pct,
                    turnover_source=turnover_source, run_budget_pct=run_budget)
    if not args.no_validate:
        print_config_facts(facts, base_cfg)
        print_validation(validate(replay, base))
        print_fidelity(base)
    print_result(base, replay)
    print_keys(base, base_cfg, args.set)
    if args.verbose:
        print_verbose(base, None if args.verbose_limit == 0 else args.verbose_limit)

    cand = None
    if args.set or args.candidate_config:
        cand_cfg = (load_config(args.candidate_config) if args.candidate_config
                    else apply_overrides(base_cfg, args.set))
        cand_label = (Path(args.candidate_config).name if args.candidate_config
                      else "+".join(args.set))
        print_override_shadowing(cand_cfg, args.set,
                                 {t.regime for t in replay.ticks if t.regime})
        cand = simulate(prod, replay, cand_cfg, mode=args.book, label=cand_label,
                        max_single_position_pct=args.max_single_position_pct,
                        turnover_source=turnover_source, run_budget_pct=run_budget)
        print_result(cand, replay)
        if args.verbose:
            print_verbose(cand, None if args.verbose_limit == 0 else args.verbose_limit)
        print_diff(base, cand)

    if args.json:
        payload = {
            "log": str(path),
            "mode": args.book,
            "initial_cash": replay.initial_cash,
            "validation": [list(r) for r in validate(replay, base)],
            "arms": [],
        }
        for r in [x for x in (base, cand) if x is not None]:
            payload["arms"].append({
                "label": r.label,
                "by_stage": dict(r.by_stage),
                "admitted_notional": r.admitted_notional,
                "admitted_names": dict(r.admitted_names),
                "end_book": r.end_book,
                "end_cash": r.end_cash,
                "end_nav": r.end_nav,
                "keys_read": sorted(r.keys_read),
                "decisions": [asdict(d) for d in r.decisions],
            })
        Path(args.json).write_text(json.dumps(payload, indent=2, default=str))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
