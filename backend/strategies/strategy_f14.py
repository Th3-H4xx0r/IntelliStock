# INTELLISTOCK_SCHEMA: {"strategy": "strategy_f14", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_f14_enabled": false, "brokerage_id": "b0d7dc67-f12b-4959-83db-0d324c6bc517", "account_number": "PA3TRLBV6ZNZ", "instance_id": "f14-paper", "dry_run": false, "act_minutes": 25}}
# INTELLISTOCK_DESCRIPTION: F14_F paper mirror — holds the frozen forward-test book (SPY / MTUM / IWD / DBMF, IEF when the rule says so, 1.4-2.0x gross on margin) on the TMasters Swing paper account. The rule itself runs in the f14-mirror service, which publishes one signal a day; this lane rebalances to it 25 to 3 minutes before the close. PAPER ONLY; it has no backtest.
"""Strategy F14 lane: the F14_F paper mirror as an IntelliStock strategy.

This lane does not hand decisions to the broker. Its book is 1.4-2.0x gross and
the engine's order gates refuse every opening order on negative cash, so the
lane ALWAYS returns ``{}`` and trades through ``forward_mirror.Mirror`` in its
``executor`` role: the same order code, safety checks and ledger the
``f14-mirror`` service has used since 2026-11-10.

The frozen rule is not in this image (``research/`` is outside the backend build
context) and is not re-implemented here. The ``f14-mirror`` service, running in
its ``signal`` role, verifies the frozen dir, runs the rule and publishes
``f14-paper|<date>|signal``; this lane reads that row.

It acts only on a live scheduler tick (``mode`` FULL / MONITOR / IDLE). A
backtest passes no mode and gets ``{}``: there is nothing to backtest here, the
scored forward test is ``research/forward-test-2026-11/score_forward.py``.

Runbook: docs/runbooks/f14-paper-mirror.md
"""
import datetime as dt
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import forward_mirror as fm  # noqa: E402

try:
    from utils import log_message as _log
except Exception:  # pragma: no cover - broker-only import
    def _log(msg, color="white"):
        print(msg)

DEFAULTS = {
    "strategy_f14_enabled": False,
    "brokerage_id": fm.DEFAULT_BROKERAGE_ID,
    "account_number": fm.DEFAULT_ACCOUNT_NUMBER,
    "instance_id": fm.DEFAULT_INSTANCE_ID,
    "dry_run": False,
    # The scheduler ticks at :00/:20/:40, so one tick lands exactly 20 minutes
    # before the close. 25 leaves it inside the window whichever side of the
    # minute it fires on; the 3-minute deadline is the mirror's own.
    "act_minutes": 25,
}
LIVE_MODES = frozenset({"FULL", "MONITOR", "IDLE"})

_mirrors = {}           # config key -> booted executor Mirror, one per process
_last_refusal = [None]  # (date, message) of the last refusal written to the ledger


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _mirror_for(cfg):
    key = (str(cfg["brokerage_id"]), str(cfg["account_number"]), str(cfg["instance_id"]),
           _truthy(cfg["dry_run"]), float(cfg["act_minutes"]))
    m = _mirrors.get(key)
    if m is None:
        m = fm.build(brokerage_id=key[0], account_number=key[1], role="executor",
                     own_instance_id=key[2], dry_run=key[3], act_minutes=key[4])
        m.boot()        # account checks + a boot row; a refusal here is retried next tick
        _mirrors.clear()
        _mirrors[key] = m
    return m


def _note_refusal(m, message: str) -> None:
    """One ``refused`` ledger row per distinct refusal per day, not one per tick."""
    stamp = (dt.datetime.now(dt.timezone.utc).date(), message)
    if m is None or _last_refusal[0] == stamp:
        return
    _last_refusal[0] = stamp
    try:
        m.record(f"{fm.MIRROR}|refused|{fm._utc_iso()}", "refused", error=message[:1000], role="executor")
    except Exception as e:
        _log(f"StrategyF14: could not record the refusal: {e}", "red")


class StrategyF14:
    """The class name is NOT free: broker.py resolves a run-once strategy by
    CamelCasing its id (`strategy_f14` -> `StrategyF14`)."""

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_f14_enabled", False)):
            return {}
        if str(mode or "").upper() not in LIVE_MODES:
            return {}                   # a backtest: this lane never trades from one
        fm.set_log_sink(lambda msg: _log(f"[f14] {msg}", "cyan"))
        m = None
        try:
            m = _mirror_for(cfg)
            status = m.step()
            st = m.state()
            _log(f"StrategyF14 {status} | mode={mode} | account={cfg['account_number']} | "
                 f"last_act={st.get('last_act_session')} | synced={bool(st.get('synced'))}"
                 + (" | DRY RUN" if m.dry_run else ""), "cyan")
        except fm.MirrorRefused as e:
            _log(f"StrategyF14 REFUSED: {e}", "red")
            _note_refusal(m, str(e))
        except Exception as e:          # never take the instance's tick down
            _log(f"StrategyF14 step failed: {type(e).__name__}: {str(e)[:300]}", "red")
        return {}
