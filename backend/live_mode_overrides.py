"""Live-mode runtime configuration overrides.

HARD RULE: This module MUST NOT import rethinkdb, MUST NOT perform any
RethinkDB write, and MUST NOT mutate its input dict. Its sole purpose is to
return a NEW dict where live-mode safety defaults are merged ON TOP of the
user's tuned Strategies-table config.

The Strategies table rows remain untouched. Backtest mode is unaffected
because backtest code paths never call apply_live_overrides().

The bug-checking agents verify (a) no `rethinkdb` import and (b) no
`.update(`/`.replace(`/`.insert(`/`.delete(` substrings anywhere in this file.
"""

from __future__ import annotations


# Baseline live-mode safety overrides. Values here represent "safe day-1"
# not "optimal P&L". They prioritise determinism, bounded cost, and fail-closed
# behavior over squeezing every basis point out of the strategy.
LIVE_OVERRIDES: dict[str, object] = {
    # LLM / analyst panel
    "analyst_panel_enabled": False,
    "nexus_live_max_llm_calls_per_cycle": 4,
    "nexus_live_llm_timeout_sec": 30.0,

    # Data freshness / fail-closed
    "quality_filter_missing_metadata_policy": "block",
    "nexus_live_fail_closed_on_missing_mcap": True,
    "nexus_live_graph_engine_ttl_sec": 900,

    # TTL semantics (wall-clock, not bar-index)
    "v32_convert_cooldown_window_utc": True,

    # Portfolio protection
    "break_glass_fresh_shield_enabled": True,
    "max_positions_breach_auto_rotate": False,
    "portfolio_drawdown_halt_enabled": True,
    # 2026-04-22 unit fix: downstream comparison at graph_nexus_analysis.py:15325
    # treats this value as percent-points (CLI default is 15.0 meaning 15%).
    # Previously shipped as 0.10 — which the formatter printed as "halt=0.1%/2d"
    # and any bar-to-bar noise tripped halt on every cycle. 10.0 = 10% drawdown
    # threshold, matching the CLI/tests convention.
    "portfolio_drawdown_halt_pct": 10.0,

    # PE-bridge escape valve: strategy reads `private_entity_bridge_enabled`
    # with default True (preserving eb20269 backtest behavior — key absent →
    # True → original code runs). LIVE flips it False so the live loop
    # doesn't spawn hours of serial GPT-5-mini calls per cycle.
    "private_entity_bridge_enabled": False,
}


def apply_live_overrides(config: dict | None) -> dict:
    """Return a NEW dict with LIVE_OVERRIDES merged on top of config.

    Never mutates input. Never writes to DB.
    """
    merged: dict = dict(config or {})
    merged.update(LIVE_OVERRIDES)
    return merged
