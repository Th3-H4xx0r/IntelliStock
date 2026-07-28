# Confirmed-bull momentum breakout sizing

**Date:** 2026-07-28
**Scope:** Strategy 179 equities / Alpaca only
**Safety boundary:** configuration-only, inactive instance, no change to bear/chop/recovery sizing

## Finding

The latest bull validation (`bt#670700`, 2026-03-30 through 2026-04-27)
returned **+8.26%** versus **SPY +13.16%**. Its confirmed-bull
`momentum_breakout_add` entries were capped at 6% of NAV, even though that lane's
normal target is 12%. The global 6% cap was introduced for a valid reason: before
the lane honored regime and entry gates, 12%-NAV bear entries generated a
documented all-loser cohort. Removing the cap globally is therefore rejected.

The historical +28.60% bull reference (`bt#353454`) allocated roughly 12–13% of
NAV to its strongest momentum replacement. The proposed compromise is a hard
**10% cap only after the regime is confirmed bull**:

- base bear/chop cap: 6% (unchanged)
- recovery cap: 6% (unchanged)
- confirmed-bull cap: 10%

## Available replay evidence

Strict PIT intentionally rejects new historical Graph Nexus runs without an
immutable snapshot for every decision timestamp. No current-state data was
backdated to bypass that rule. The estimate below is a lot-level replay of the
four already-recorded qualifying entries, not promotion-grade fresh evidence.

| Window | Lot | Entry → exit path | Incremental effect at 10% cap |
|---|---|---:|---:|
| Bull | CAR | staged exits, about +140% | about +$329 |
| Bull | AAOI | $111.02 → $143.76 | about +$68 |
| Flip | ISSC | $20.425 → $20.00 | about −$5 |
| Flip | SNDK re-entry | $680.945 → $590.66 | about −$35 |

Before secondary cash-allocation effects, this implies:

- bull: about **+14.9%**, versus SPY **+13.16%**
- flip-flop: about **+5.7%**, versus SPY **−0.20%**
- bear and rally: no qualifying lane fires; the candidate is inert

The sample is too small to claim a durable edge. The hard cap, confirmed-bull
scope, and inactive rollout bound the risk while forward PIT evidence accrues.

## Rollout

`scripts/doc179_bull_momentum_sizing_v1.json` preserves the full current
`regime_profiles` value and adds only
`bull.momentum_breakout_max_nav_pct = 0.10`. Apply it through the existing
API merge-patcher only after a drift-check and after the code containing the
credential-log fix is deployed. Do not start the Alpaca instance.
