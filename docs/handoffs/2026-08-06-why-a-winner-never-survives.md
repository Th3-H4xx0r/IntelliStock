# Why a 10-bagger never survives this config — and why the SNDK result was never real

Measured 2026-08-06 from bars and rows already in the database, plus a
five-agent sweep of the buy path, sell path, config registry and discovery
pipeline. **No backtest spend.**

Two separate findings. The first is fixable and worth a lot. The second
retires a claim this project has been leaning on for months.

---

## Part 1 — the exits are calibrated backwards

### The paths

Nine of the 2025-26 semiconductor moonshots, entered 2025-10-07:

| symbol | entry | final | total | worst drawdown from a running peak |
|---|---:|---:|---:|---:|
| SNDK | 122.29 | 2272.05 | **+1757.9%** | **−31.2%** |
| MU | 194.12 | 1159.77 | +497.4% | −27.9% |
| MXL | 16.70 | 128.08 | +666.9% | −27.7% |
| ICHR | 20.18 | 112.28 | +456.4% | −38.9% |
| AAOI | 33.00 | 150.11 | +354.9% | −46.7% |
| VICR | 50.37 | 381.76 | +657.9% | −29.4% |
| SIMO | 98.25 | 337.96 | +244.0% | −24.2% |
| INTC | 37.70 | 141.49 | +275.2% | −22.2% |
| MRVL | 89.38 | 297.59 | +232.9% | −27.2% |

Not one drew down less than 22% on the way up.

### What doc-193 actually has armed

The profit-side exits are genuinely off, and this was verified against the code,
not just the config:

| mechanism | key | why it cannot fire |
|---|---|---|
| trailing stop | `trailing_stop_disabled=true` | `graph_nexus_analysis.py:20120` — kill switch ANDed in; logs `Trailing stop SUPPRESSED`. The bull profile's `trailing_stop_pct=15` is **inert**. |
| profit take | `profit_take_disabled=true` | `:20155` — outranks both `profit_take_enabled` and `profit_take_tiers`. |
| portfolio DD circuit, `−7%` floor, profit ratchet | `portfolio_drawdown_halt_enabled=false` | `:23254` early-returns before all three. `drawdown_circuit_enabled=true` and `portfolio_profit_ratchet_enabled=true` are **dead keys** — they live inside the function that never runs. |
| `max_hold_days` | 3650 | unreachable |
| trend-reversal / stale-ETF enforcement | `sell_enforcement_enabled=false` | both branches gated on it |

`peak_protection` and `rank_band` are **shields, not sellers** — they suppress
other exits. Neither can sell a position.

**The loss-side exits were left fully armed**, and they are what actually sells
a volatile winner:

| rank | mechanism | doc-193 | reader |
|---|---|---|---|
| 1 | `fast_loser_cut` | **−10%** | `graph_nexus_analysis.py:19857` — **there is no `fast_loser_cut_enabled` key**; it cannot be turned off from config |
| 2 | `downtrend_protection_sell_underperformers_pct` | **−3%** | `:27952` — after 3 macro-bearish bars, force-sells every position ≤ −3% |
| 3 | intraday monitor risk exits | **`true` in bull/recovery** (base `false`) | `:24104` — the overlay switches ON an exit path the base config disables |
| 4 | `bear_book_trim` to `max_positions_bear=2` | enabled | `:27464` |
| 5 | `catastrophic_stop_pct` | −40% | `:19971` |
| 6 | `consecutive_sell_days_to_prune` | 20 bars | `:27697` — not gated by `sell_enforcement_enabled` |
| 7 | `v32_convert_min_loss_pct` | −4 base, **−1 in the bull profile** | `:9207` |

Against the measured paths: **−3% reaches 6 of 9 names, −10% reaches 5 of 9,
−40% reaches 0 of 9.**

The bull/recovery overlays are the trap. They *enable* exit machinery the base
config deliberately disables (`nexus_monitor_risk_exit_execution_enabled`,
`momentum_partial_trim_execution_enabled`), so reading the base config gives the
opposite impression of what runs in a bull market.

### `min_hold_days = 120` protects nothing

Two independent reasons, both confirmed:

**Structural.** `_POSITION_ENTRY_TS` is stamped only by
`_min_hold_note_position` (`broker.py:3378`), which reads the **post-fill**
position. Execution is next-event: `execute_signal` records the order and
returns a `SimulationSubmission`; `_positions` is not touched until
`_apply_confirmed_fill` on a later bar. So on a new-name buy the quantity is
still 0, the `else` branch runs, and the symbol is `pop`ped instead of stamped.
`min_hold.py` then hits its `no_entry_timestamp` branch, which **fails open**.

**Empirical.** bt 216767 ran doc-192 (identical to doc-193 except the veto keys
and the salt) over 2025-11-10..2026-02-24 — **106 days, with a 120-day floor.**
No satellite sell should have been possible at all. Observed: BOIL 5 days,
OLMA 7, CAE 9, NVO 14. Zero `MIN_HOLD_GATE` lines.

**Second-order:** on a *sell* the position is still present (fill deferred), so
`setdefault` stamps the symbol at the moment of its first sale — and
`setdefault` then blocks a real re-entry from ever correcting it.

Separately, `broker.py:14844` exempts anything in `nexus_sell_enforcement` from
min_hold — and every forced exit in the strategy funnels through that set. So
even a working clock would not stop mechanisms 1-7.

### What this costs, measured on the real discovery dates

Not a hypothetical entry — the dates the pipeline actually flagged these names:

| symbol | discovery | entry | if simply HELD to 06-30 | worst vs entry | −3% rule | −10% rule |
|---|---|---:|---:|---:|:--:|:--:|
| SNDK | 2026-01-06 | 349.59 | **+541.0%** | −4.3% | **CUT** | held |
| SNDK | 2026-02-06 | 599.10 | +274.1% | −12.0% | **CUT** | **CUT** |
| SNDK | 2026-04-06 | 724.12 | +209.5% | −1.9% | held | held |
| MU | 2026-01-29 | 435.93 | +163.2% | −26.4% | **CUT** | **CUT** |
| MU | 2026-02-17 | 396.62 | +189.3% | −19.1% | **CUT** | **CUT** |
| MU | 2026-06-18 | 1139.94 | +0.7% | −6.8% | **CUT** | held |

**Even discovering SNDK 200% late still left +541% on the table.** The −3% rule
cuts 5 of these 6 entries. That is the defect, and it is worth more than any
signal change on the table.

---

## Part 2 — the SNDK evidence was never evidence

The 196-run AI-farm result that started this thread does not survive inspection.

**The universe was hand-typed, not discovered.** All 101 `AIBacktestingResults`
rows on that window use a byte-identical 7-name list: `AAPL, AMZN, GLDM, META,
MSFT, SNDK, TSM`. SNDK is 1 of 7 by count and supplies ~51% of net P&L. That is
a concentrated bet, not a discovery.

**It was chosen after the outcome was known.** Those runs were created
2026-03-06 to 2026-03-10. On 2026-03-06 SNDK traded at **$526.96**, already
+336% off $120.87. The window tested, 2025-11-10..2026-02-24, is one over which
SNDK had already gone +138%.

**Discovery's own record contradicts the claim.** `GraphNexusDiscoveredStocks`
has 6,296 events across 1,890 distinct tickers — verified directly:

- **SNDK: 4 events (0.064%)** — *below* the median ticker's 3.3
- **MU: 12 events (0.191%)**
- The earliest hit for **both** came from `sector_watchlist`, an operator-typed
  config dict, on the same day (2025-10-14)
- Every *autonomous* SNDK hit was late-stage `propagation`: +200%, +413%, +521%
  into the move. **Zero momentum discoveries of SNDK, ever.**
- The pipeline's most-discovered names are SMH, SOXX, XLE, OIH, VDE, XBI —
  sector ETFs, which is what `_build_momentum_scan_universe` is built to surface

**The code path leaked.** Every equities backtest overrides `pit_mode` to
`"research"` at the shared creation choke point (`interactive_utils.py:5579`),
which `backtest_evidence_options.py:60` defines as *"explicitly opt out and run
the legacy current-state path."* Strict mode is not usable — `PointInTimeManifests`
has 1 row. In research mode the buy gate reads **today's** market cap. The
codebase says what that does (`:5384`): *"the bias is not symmetric: it
systematically lets the eventual winners through the floor and keeps the
eventual losers out."* And 574 of the 576 runs on that window predate the
2026-08-02 fix for reading the same session's close before deciding.

**The strategy is fitted to these tickers by name.** ~30 load-bearing sites in
`graph_nexus_analysis.py` cite SNDK or MU in the justification for a default —
`:8040` moved the circuit-breaker floor off −15% because *"SNDK −18.3% → +487%
missed"*; `:15049` added an entire macro-override keyword mechanism because
*"Backtest 901920 cancelled SNDK's 2025-11-28 scheduled buy."* Those are
defaults chosen with the answer in hand.

---

## Consequences

**Do not run the 1-year backtest on doc-193 as it stands.** It will cut its
winners on −3% wobbles, and the measurement above already predicts that for
free.

**The "buy the big names" thesis needs restating.** Discovery *does* surface
these names — late, at +200% to +521% — and even that late entry leaves
+163% to +541% if held. The reachable prize is real but it is "capture part of
a late leg", not "buy SNDK at $116". Nothing here supports 2-3x as a repeatable
target; the earlier evidence against that (2.1% precision on moonshot
selection, sector momentum losing to SPY in 4 of 4 sub-periods over 20 years)
is untouched by any of this.

## The fix list, in value order

1. **Stamp the entry clock on submission, not on fill** — `min_hold` is inert in
   backtest *and* live until this is fixed. Everything else is downstream.
2. **Add a `fast_loser_cut_enabled` key** and default it off for held winners;
   today the −10% cut cannot be disabled from config at all.
3. **`downtrend_protection_sell_underperformers_pct` −3 → −25** in bull/recovery.
4. **Revert `nexus_monitor_risk_exit_execution_enabled` and
   `momentum_partial_trim_execution_enabled` to `false`** in both overlays.
5. **`v32_convert_min_loss_pct`** −1 → −15 in the bull profile.
6. Delete the dead keys so the config stops lying: `regime_bear_max_positions`,
   `regime_chop_max_positions` (live readers are `max_positions_bear`=2 and
   `max_positions_chop`), `drawdown_circuit_enabled`,
   `portfolio_profit_ratchet_*`, `portfolio_dd_hard_cut_floor_pct`.
7. **`quality_filter_missing_metadata_policy`** — code default is `"block"` while
   the file's own schema advertises `"warn"`, and **0 of 7,052 `Stocks` rows have
   `market_cap > 0`**. Under `"block"`, any name whose market cap fails to
   resolve is rejected silently, with ETFs and held names exempt — which
   reproduces "it only buys SPY" exactly. This is the fundamental-veto bug in a
   second module.

Scripts: `$CLAUDE_JOB_DIR/tmp/winner_survival.py`,
`momentum_entry_survival.py`.
