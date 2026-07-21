# Regime Recovery-Override — Design (2026-07-21)

**Problem.** The graph_nexus regime detector (`_detect_market_regime`) classifies from a 20-day proxy return. At the start of a bull cycle that follows a bear month, that 20-day window is still dominated by the prior drawdown, so the detector reads **bear** on day 1 of a rally. Concrete failure: the bull window (SPY +13.6%) opened `ret20=-8.18%` → classified bear → `max_positions 14→2` → defensive trades (buy oil/BITO, dump RIVN) → **−4.63%** while the market ripped. The detector cannot tell "recovering off the lows / bull turn" from "deepening bear."

**Goal.** Recognize a bull turn early and participate, without getting faked by bear-market bounces (bear windows must stay safe). Validate on both the bull (03-30→04-27) and bear (03-02→03-30) windows.

## Mechanism

Before the ret20 branch returns **bear**, check a recovery-override that fires only when **three signals agree** — a mix of acceleration (catches the turn early) and structure (filters dead-cat bounces):

1. **Acceleration:** `ret5 ≥ regime_recovery_ret5_min_pct` (default +2%) — a short-term thrust up while the 20-day is still negative.
2. **Trend reclaim:** current price `> 20-day MA` — the bounce has reclaimed the short-term trend, not just ticked up under resistance.
3. **Recovery depth:** price `≥ regime_recovery_off_low_pct` (default 0.5) of the way from the 20-day low to the 20-day high — a real recovery, not a one-bar blip.

When all three hold → reclassify **bear → chop** (participate: the defensive cap lifts to the chop cap, the SQQQ hedge is skipped, the bear RS entry-gate is bypassed). If the thrust is very strong (`ret5 ≥ regime_recovery_bull_ret5_pct`, default +5%) → promote straight to **bull**. The structural (`< 200-day MA`) bear is never overridden.

**Why bear stays safe.** In a deepening bear the market makes new lows: `ret5 < 0` and price is below the 20-day MA, so gates 1 and 2 fail and the override can't fire. The three-gate AND is exactly the "recovering vs still-falling" discriminator. A strong bear *bounce* is the residual risk (all three could briefly align) — measured empirically on the bear window.

## Config (all default OFF/neutral — no behavior change until enabled)

| key | default | meaning |
|---|---|---|
| `regime_recovery_override_enabled` | `false` | master switch |
| `regime_recovery_ret5_min_pct` | `2.0` | acceleration gate (%) |
| `regime_recovery_off_low_pct` | `0.5` | recovery-depth gate (fraction) |
| `regime_recovery_bull_ret5_pct` | `5.0` | thrust to promote chop→bull |

Diagnostics: on trigger, `strategy_cache["_market_regime_diag"]["recovery"] = {ret5, off_low}` and `raw = "recover->chop"|"recover->bull"` — so the bull log can confirm it fired and the bear log can confirm it stayed silent.

## Deploy & validation

- The mechanism is one `_detect_market_regime` change, config-gated default-off → deploy the branch to the engine **once** (safe), then all tuning is config-only via normal backtests (no redeploy per iteration).
- **Bull:** enable + tune; the override should fire in the opening week, lift the cap, and turn −4.6% into clear participation (target positive, ideally toward SPY).
- **Bear:** the +2.29% must hold within tolerance; grep the bear log to confirm `recover->` never fires (or fires harmlessly).
- Supersedes the earlier inert `regime_bear_stale_recovery_pct` stale-guard (removed) — recovery-depth is now one of three gates, not the sole one.

## Unit tests (`test_regime_recovery_override.py`)
Default-off no-op; recovering→chop; strong thrust→bull; deepening bear stays bear; dead-cat bounce (below MA) stays bear; thresholds configurable. 6 tests, all green.
