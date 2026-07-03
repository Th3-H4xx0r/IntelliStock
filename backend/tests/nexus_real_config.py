"""Real-config test fixture for graph_nexus_analysis strategy tests.

Round-1's regression tests passed while live risk-exits never fired because
their hand-built config fixtures omitted the V31 grace keys — the exact
config-vs-default divergence that blinded us (a 14-day grace window vetoed
fast-loser / circuit-breaker cuts, so positions rode their whole life
ungated). This helper makes strategy tests run against the REAL doc-179 config
so that divergence can never recur silently again.

``real_config(**overrides)`` loads the committed pre-tune snapshot of the
live Strategies doc-179 config, overlays the 2026-07 live tune deltas, strips
every secret-like key (defense in depth — the snapshot carries real API keys),
then applies caller overrides (which always win).

Grace keys AS FOUND in the live snapshot (the V31-grace blind spot — these are
the keys Round-1 fixtures lacked; documented here so their live values are
visible at the point of use):

    initial_grace_enabled                 = True    # grace ACTIVE live
    initial_grace_bars                    = 14      # 14-day grace window
    initial_grace_catastrophic_loss_pct   = -15     # escape_A (LOW tier)
    initial_grace_cumulative_loss_pct     = -10     # escape_B threshold
    initial_grace_cumulative_min_days     = 5       # escape_B min days held
    initial_grace_regime_escape_enabled   = True    # escape_C (bear regime)

Related risk-exit knobs also present live:

    fast_loser_cut_pct                    = -10
    fast_loser_cut_pct_high_vol           = -18
    fast_loser_cut_recent_runup_block_pct = 40
    fast_loser_cut_recent_runup_lookback_bars = 20
    (no explicit max_open_loss_pct → circuit breaker uses the tier-resolved
     conviction-aware floor; LOW tier defaults to -15%)

The tune overlay applied on top of the snapshot (2026-07 live tune):
    portfolio_drawdown_halt_pct                       = 8
    profitable_min_hold_conviction_override_enabled   = False
    new_entry_reserved_budget_pct                     = 0.1
    cash_reserve_floor_pct                            = 0.02
    allocation_max_new_stock_buys                     = 10
    max_propagated_scoring_slots                      = 40
    max_positions                                     = 10
    rotation_break_glass_delta                        = 2.5
    rotation_break_glass_raw_score                    = 3.5
    rotation_profitable_min_incoming_raw_score        = 2.0
"""

import json
import os

# Path to the committed live-config snapshot (repo-root relative).
_SNAPSHOT_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "docs",
        "superpowers",
        "specs",
        "2026-07-02-strategy-179-pre-tune-snapshot.json",
    )
)

# 2026-07 live tune deltas overlaid on top of the pre-tune snapshot.
_TUNE_OVERLAY = {
    "portfolio_drawdown_halt_pct": 8,
    "profitable_min_hold_conviction_override_enabled": False,
    "new_entry_reserved_budget_pct": 0.1,
    "cash_reserve_floor_pct": 0.02,
    "allocation_max_new_stock_buys": 10,
    "max_propagated_scoring_slots": 40,
    "max_positions": 10,
    "rotation_break_glass_delta": 2.5,
    "rotation_break_glass_raw_score": 3.5,
    "rotation_profitable_min_incoming_raw_score": 2.0,
}

# Any config key whose name contains one of these substrings is scrubbed before
# the config reaches a test — the snapshot carries real live secrets.
_SECRET_SUBSTRINGS = ("key", "secret", "password", "token")


def _strip_secret_like(cfg: dict) -> dict:
    return {
        k: v
        for k, v in cfg.items()
        if not any(s in str(k).lower() for s in _SECRET_SUBSTRINGS)
    }


def real_config(**overrides) -> dict:
    """Return the REAL doc-179 config (snapshot + 2026-07 tune), secrets
    stripped, with caller ``overrides`` applied last (overrides always win)."""
    with open(_SNAPSHOT_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    cfg = dict(data["strategies"][0]["config"])
    cfg.update(_TUNE_OVERLAY)
    cfg = _strip_secret_like(cfg)
    cfg.update(overrides)
    return cfg
