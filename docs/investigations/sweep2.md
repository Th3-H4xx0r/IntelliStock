# sweep2 — bug sweep of the two config changes in bt 427197

Read-only. Log: `backtests/427197_sweep2.log` (32,105 lines, status=`running`, 33 bars),
compared against `backtests/915207.log` (41,184 lines, 43 bars), `backtests/542754_sweep.log`,
`backtests/383778_sweep.log`. Config confirmed off the run's own sidecar
`backtests/427197_sweep2.json`: `core_min_pct=0.1`, `watchlist_priority_slots=2`,
`sector_watchlist={}`, `core_max_pct=0.4`, `regime_profiles.*.core_target_pct=0.35`,
`total_spend_cap_target_weight_pct=0.14`.

Builds on `_SYNTHESIS.md`, `gap-capital.md`, `gap-target.md`, `gap-bugsweep.md`. Not redone here.

---

## VERDICT

| lever | fires? | signature | does what it claims? |
|---|---|---|---|
| `core_min_pct` 0.25 -> 0.10 | **YES** | `SATELLITE OVERFLOW ... funding $1,922 of room` (was $1,050) | **YES mechanically — and 68% of the money it frees is bought straight back as SPY** |
| `watchlist_priority_slots` 0 -> 2 | **NO — 100% INERT** | `Priority sizing order: watchlist=none` 30/30 bars | **NO. Zero allocations changed.** |

---

## 1. `watchlist_priority_slots` 0 -> 2 — INERT. Structurally cannot fire.

The value reaches the strategy — `Effective config | ... prio=watch:2@0.15` (427197 L18),
vs `prio=watch:0@0.15` in 915207 L18. It then changes nothing:

    427197 L420 : Priority sizing order: watchlist=none | prop_exp=SBLK, TDY, VEON, XOM
    427197 L2195: Priority sizing order: watchlist=none | prop_exp=V, PONY, VEON
    ... watchlist=none on **30 of 30** `Priority sizing order` lines
    427197 L310 : Watchlist candidate audit: active_sectors=none matched=none inserted=none blocked=none
    ... `matched=none` on **33 of 33** audit bars (915207 43/43, 542754 24/24, 383778 21/21)

Root cause, `backend/strategies/graph_nexus_analysis.py:13153-13165`:

    def _get_active_watchlist_state(active_trends, config):
        watchlist_map = _get_sector_watchlist_map(config)
        if not watchlist_map:
            return {"active_sectors": [], "matched_keys": [], "priority_tickers": set(), ...}

`sector_watchlist` is `{}` in the run config, so `priority_tickers` is **always the empty set**,
so `is_watchlist_priority` is always False at `:28595`, so the slot loop at `:28598`
(`if _is_watchlist_priority and ... and len(_watchlist_priority) < _watchlist_priority_slots`)
never appends. The slot count is multiplied by a set that is unconditionally empty.
Corroborated by `discover=watch:0/0` (`sector_watchlist_reserved_slots=0`,
`sector_watchlist_max_per_sector=0`) on the same config line.

**This is the fourth inert lever.** It cannot be fixed by raising the slot count. It needs a
non-empty `sector_watchlist` map, or the priority path must be re-pointed at a source that
actually populates (e.g. momentum watchlist tickers). Until then, ship it back to 0 — it costs
nothing but it also proves nothing.

---

## 2. `core_min_pct` 0.25 -> 0.10 — the satellite DID get bigger. Then the core bought it back.

### 2a. It is not inert. Same calendar bars, `[core] ... core X% vs target Y% of NAV`:

| bar | 427197 core% | 915207 core% |
|---|---|---|
| 01-02 | 40.1 | 39.9 |
| 01-05 | **12.1** | **27.0** |
| 01-06 | 36.8 | 37.4 |
| 01-07 | 37.2 -> **23.0** | 37.6 -> **27.1** |
| 01-08 | **12.0** | 26.9 |

Satellite / SPY / cash as % of NAV, reconstructed per bar from `Buy budget: ... (cash=$X`,
`Buy budget floor: ... (floor=10% of $NAV)` and cumulative SPY fills:

| | 427197 | 915207 |
|---|---|---|
| satellite % of NAV, mean bars 4+ | **85.5%** (n=23) | **72.9%** (n=36) |
| SPY % of NAV, mean bars 4+ | **13.3%** | **26.0%** |
| cash % of NAV, mean bars 4+ | 1.12% | 1.09% |

**+12.6pp of NAV moved from core to satellite. It did NOT sit as cash** (1.12% vs 1.09%).
The `core_min` arithmetic mismatch `gap-capital.md` convicted is genuinely fixed: the conviction
band went `0.35-0.25 = 0.10*NAV = $620` (< one $840 clip) to `0.35-0.10 = 0.25*NAV = $1,922`
(> one clip). All 17 `SATELLITE CAP ... overflow ceiling` skips are gone.

### 2b. But the binder only moved, it did not go away — and the parent brief is wrong on this.
"all 42 SATELLITE CAP skips (now 0)" is **not what the log says**. 427197 still has 26
`SATELLITE CAP` events: **12 skips** and **14 trims**.

    427197 counts: skip_overflow=0  skip_design=12  trimmed=14   (26 over 33 bars = 0.79/bar)
    915207 counts: skip_overflow=17 skip_design=11  trimmed=14   (42 over 43 bars = 0.98/bar)

The skip reason flipped from `overflow ceiling` to `design share`, and the negative room got
an order of magnitude WORSE, because the satellite is now at 85.5% against a ~60-63% design share:

    915207 L…: SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($12 room)
    427197 L13736: SATELLITE CAP: GH   skipped — satellite at its design share ($-1,578 room)
    427197 L14759: SATELLITE CAP: BTC  skipped — satellite at its design share ($-1,582 room)
    427197 L23508: SATELLITE CAP: AMZN skipped — satellite at its design share ($-977 room)
    427197 L24440: SATELLITE CAP: FBP  skipped — satellite at its design share ($-1,037 room)

`gap-target.md` §3 (score-ranked satellite trim-back) is now the *only* remaining release valve.

### 2c. THE BUG — the core sells to fund a buy that is refused, then buys itself back.

The full loop, 2026-01-05 -> 2026-01-06, quoted verbatim:

    L4101  [core] funding request trimmed $1,709 -> $1,669 — satellite headroom will refuse the
           remainder; releasing core for it would only be bought back
    L4112  SATELLITE OVERFLOW: ARWR raw=+1.750 >= 1.50 — funding $1,669 of room out of the core
    L4119  SATELLITE OVERFLOW: USPH raw=+1.800 >= 1.50 — funding $1,669 of room out of the core
    L4359  [execution] FILL SELL SPY qty=2.44 price=686.74  quote=2026-01-05 16:00  = $1,675.74
    L4115  SKIP BUY ARWR — cash_to_use $1.69 < min $366 (allocated $854.39)
    L4122  SKIP BUY USPH — cash_to_use $1.69 < min $366 (allocated $854.39)
    L4582  [core] bought $1546.03 SPY @ 687.73 (band_deploy: 12.1% -> 37.6% of NAV)
    L5293  [execution] FILL BUY SPY qty=2.238 price=690.68 quote=2026-01-06 16:00 = $1,545.98
    L5294  [execution] FILL BUY BALL ... = $47.59

$1,675.74 of core sold to fund two conviction names. The buy gate saw **$1.69**.
$1,545.98 (92.3%) went straight back into SPY one bar later. **$47.59 (2.8%) reached alpha.**

The rebuy is arithmetically forced at `backend/core_sleeve.py:517-542`:

    if drift_usd > 0.0:
        _spendable = max(0.0, float(cash or 0.0) - cfg.cash_floor_pct * nav)
        _spendable /= (1.0 + CORE_DEPLOY_COST_HAIRCUT)
        buy = min(drift_usd, _spendable)
        ...
        return RebalanceOrder(notional=buy, reason="band_deploy", **base)

`drift_usd` = (37.6% - 12.1%) * $6,103 = $1,556. `_spendable` = the $1,676 the core itself just
released. `buy = min(1556, 1676) = $1,546`. Exact match to the fill. `band_deploy` has **no
knowledge that the gap it is closing was opened on purpose, one bar ago, to fund a named
conviction buy that is still queued.** The log line at L4101 literally predicts this
("releasing core for it would only be bought back") and releases anyway.

Same-bar version, 2026-02-02 — the core buys SPY on the exact tick four conviction names are refused:

    L23499 [core] funding request trimmed $3,567 -> $0 — satellite headroom will refuse the remainder
    L23508 SATELLITE CAP: AMZN skipped — satellite at its design share ($-977 room)
    L23511 SATELLITE CAP: META skipped — ...   L23514 TXN   L23517 V
    L23750 [execution] FILL BUY SPY qty=0.733 price=696.31 quote=2026-02-02 16:00 = $510.60

### 2d. GENERALIZABLE — the release->rebuy loop is in all four runs, two regimes, three windows.

SPY fills, deploy excluded:

| run | window | released | re-bought | **recycled** | gross ex-deploy |
|---|---|---|---|---|---|
| **427197** core_min 0.10 | 01-01..03-01 bull | $3,703 | $2,505 | **68%** | $6,208 |
| **915207** core_min 0.25 | 01-01..03-01 bull | $1,410 | $1,317 | **93%** | $2,727 |
| **542754** bear | 03-04..03-19 | $3,924 | $1,567 | **40%** | $5,491 |
| **383778** OOS bull | 04-06..04-27 | $2,437 | $1,715 | **70%** | $4,152 |
| | | **$11,474** | **$7,104** | **62%** | |

**$7,104 of core capital released for conviction and returned to SPY across 4 runs.**
It is a mechanism, not a window artefact.

### 2e. What `core_min_pct=0.10` actually bought, in dollars.

    non-SPY BUY notional: 427197 $5,332 (9 fills)  vs  915207 $4,450 (10 fills)   = +$882
    SPY gross ex-deploy:  427197 $6,208 (8 fills)  vs  915207 $2,727 (4 fills)    = +$3,481
    rolling 21-session turnover, `TURNOVER BUDGET BINDING: N% of NAV`:
        427197  min 50  median **104**  max 111   (268 lines)
        915207  min 56  median  **88**  max  92   (258 lines)

**$3.95 of extra SPY churn per $1 of extra satellite exposure**, and +16pp on median rolling
turnover against a documented ~50%/mo break-even (OBJECTIVE.txt). The lever is not backwards,
but it is paying 4:1 for what it delivers, and it delivers into names the run picked anyway
(the extra capital bought AMAT $853.50 +21.9% and APP $700.34 -> sold $563.80, **-19.5%**).

### 2f. Caveat the parent must hear: 427197 vs 915207 is NOT a clean A/B.
915207 is PRE-commit 89e71f3 (`SKIP BUY CFG — cash_to_use $1.69 < min $50`); 427197 is POST
(`SKIP BUY ARWR — ... < min $366`), and prints the new extension form
(`V32 mw_buy extension-block: SNDK range +73.2% > 25% [bars=97]`) that 915207 never prints.
**Opening-book overlap is 0/4**: 427197 = SLV/CPER/SBLK/TDY, 915207 = NTR/TCMD/VOYA/XOM.
Per `_SYNTHESIS.md` the noise floor is >= 4.94pp. The +12.6pp satellite share IS attributable to
`core_min_pct` (the core% prints on matched calendar bars). **The P&L delta is not.**

SNDK still not bought, still for the reason `gap-bugsweep.md` §2 convicted, unrelated to either lever:
`V32 mw_buy extension-block: SNDK range +73.2% > 25% — no conviction bypass [bars=97]` (L5827, L6765).

---

## 3. THE SINGLE HIGHEST-VALUE FIX

**A core funding release must be a named, reserved credit — spendable by the buy gate on the
same tick, and untouchable by `band_deploy` until it expires.**

Two lines, one mechanism:
1. `backend/core_sleeve.py:517` — before `if drift_usd > 0.0`, return
   `RebalanceOrder(reason="conviction_release_outstanding")` while any release issued in the last
   N bars is unspent. Today `band_deploy` re-buys the released cash the very next bar.
2. The `[core] funding request trimmed ... -> $X` release must credit the buy gate's
   `cash_to_use` on the SAME tick (the `gap-capital.md` #1 cash race). Today the gate reads
   `$1.69` against $1,676 in flight. `backtest_credit_pending_sell_proceeds` emits **0 log lines
   in 427197** (grep `credit_pending|pending_sell|reservation|in.flight` = 0) — it is still
   unverifiable and evidently not doing this.

**Expected effect.** Recovers the $2,505 (41.8% of NAV) that 427197 released and re-bought as
SPY, and $1,317 / $1,567 / $1,715 in the other three runs. At the run's own $840 clip that is
~3 additional full-size conviction slots in 427197 alone. At `gap-target.md`'s measured mean
forward return for refused names with 60d >= +50% (**+23.52%**), $2,505 deployed instead of
parked in SPY (+0.6% over the window) is **~+$575 = +9.6% on a $6,000 book**. It also removes
the 4 extra SPY round trips that pushed median rolling turnover 88% -> 104%.

**Generalizable: YES.** Same mechanism, 4 runs, 3 windows, 2 regimes (bull 68%/93%, bear 40%,
OOS bull 70%). It is not a property of `core_min_pct` — `core_min_pct=0.10` only made the
leak 2.6x bigger by releasing 2.6x more money into a gate that cannot see it.

**Do NOT ship `watchlist_priority_slots` again at any value** until `sector_watchlist` is
non-empty. It is provably a no-op (`matched=none` on 121/121 audit bars across 4 runs).
