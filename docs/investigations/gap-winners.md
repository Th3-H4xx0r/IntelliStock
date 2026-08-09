# gap-winners — what the winners had at entry that the losers did not

Read-only forensics on the two CURRENT bull/chop runs. No code edited, no run started or
stopped, nothing pushed. Every number is read out of a run log; log lines are quoted
verbatim; `file:line` is the working tree.

Runs (per `docs/investigations/_RUNS2.md`), logs already on disk:
* **bt 915207** — 2026-01-01..03-01, `v2-let-run-core`, $6,000, 3600s, **+9.70% (+$581.83)**,
  17 fills. `backtests/915207.log` (41,184 lines).
* **bt 383778** — OOS 2026-03-30..04-27, same instance/cash/cadence, **+4.75% (+$284.96)**,
  30 fills. `backtests/383778.log` (19,643 lines).
* **bt 542754** — bear 2026-03-02..03-30, +11.94%, used only as the regime control.

Caveat stated up front: 915207 logs the extension gate as `recent runup +73.2%` while 383778
logs `range +47.2% ... [bars=153]`. The two runs therefore straddle a code change. Everything
below that spans both runs is a *mechanism* claim with independent evidence in each run, not a
point-estimate comparison.

---

## 0. THE ONE-LINE ANSWER

**Nothing the system can see separates the winners from the losers at entry.** The winners and
the biggest loser were bought on the same bar, in the same lane, at the same score, at the same
14.0% of NAV, at the same ~0% extension, and held the same ~40 sessions. The only thing the
winners had was **that they were bought at all, early, at conviction size** — and that is not a
selection property, it is a *funding* property: **the book funds names on days 1-8 and is then
frozen for the rest of the window by the satellite/core headroom cap.**

The single highest-value change is therefore not a new filter. It is **widening the conviction
overflow band (`core_min_pct` 0.30 → ~0.15 on the bull/chop profiles)** so that a raw≥1.50 name
that shows up on day 6+ can still be funded out of the index. Priced below at **+10.4pp on
915207 and +7.3pp on 383778**, and provably inert in the bear window.

---

## 1. THE ENTRY TAPE — every alpha entry in 915207, with every ex-ante feature

All 17 `[execution] FILL` lines, alpha only (SPY core excluded). `ext@entry` = fill price /
window-start price − 1. `tier path` is the `conviction_tier: sym=… path=…` string the run
itself emits.

| entry | sym | lane / `action_intent` | $ notional | % NAV | ext@entry | tier path (modal) | sessions held | P&L |
|---|---|---|---:|---:|---:|---|---:|---:|
| 01-02 | **XOM** | prop-expansion → `initial_buy` | $837 | 14.0% | **−0.07%** | `mcap_high` 482/482 | 40 | **+$225.25** |
| 01-02 | **NTR** | news trend → `initial_buy` | $840 | 14.0% | **+0.29%** | `mcap_high` 481/481 | 40 | **+$178.30** |
| 01-02 | **VOYA** | news trend → `initial_buy` | $840 | 14.0% | **+0.84%** | `default_low` 412/450 | 37 | **−$75.09** |
| 01-02 | TCMD | momentum watchlist → `momentum_watchlist_buy` | $815 | 13.6% | −2.76% | `default_low` 479/481 | 40 | +$32.55 |
| 01-02 | BA | propagation → `initial_buy` | $268 | 4.5% | +2.63% | `mcap_high` 489/489 | 40 | +$5.65 |
| 01-06 | **AAL** | propagation → `initial_buy` | $89 | 1.5% | +4.58% | `default_low` 169/174 | 15 | **−$10.92** |
| 01-08 | **AMAT** | prop-expansion → `initial_buy` | $652 | 10.7% | +9.53% | `mcap_high` 465/465 | 35 | **+$211.11** |
| 01-28 | NVDA | `backfill_queue_buy` | **$29** | 0.5% | +2.59% | `mcap_high` | 22 | −$2.09 |
| 01-28 | SNDK | `momentum_watchlist_buy` | **$29** | 0.5% | +115.1% | `mcap_high` | 22 | +$6.91 |
| 02-26 | RVLV | `backfill_queue_buy` | **$51** | 0.8% | −12.94% | `default_low` | 1 | −$2.11 |

`$4,341 of $4,450 of alpha notional (97.5%) was committed in the first five sessions.
Every entry after 01-08 was ≤ $51.`

The four day-1 orders are byte-identical in the broker's own words (915207 log lines
1794-1806):

```
[BROKER] NTR  @ 2026-01-01 00:00:00 ($61.73):  buy action_intent=initial_buy  (weighted scores from 1 strategies)
[BROKER] Buy gate inputs for NTR:  cash=$6000.00 reserved=$0.00 floor=$120.00 high_conv=True open_pos=0 cash_per_trade=$840.00 → PASS
[BROKER] VOYA @ 2026-01-01 00:00:00 ($74.515): buy action_intent=initial_buy  (weighted scores from 1 strategies)
[BROKER] Buy gate inputs for VOYA: cash=$6000.00 reserved=$0.00 floor=$120.00 high_conv=True open_pos=0 cash_per_trade=$840.00 → PASS
[BROKER] XOM  @ 2026-01-01 00:00:00 ($120.33): buy action_intent=initial_buy  (weighted scores from 1 strategies)
[BROKER] Buy gate inputs for XOM:  cash=$6000.00 reserved=$0.00 floor=$120.00 high_conv=True open_pos=0 cash_per_trade=$840.00 → PASS
```

`Weighted sum: 1.000, Total weight: 1.000, Normalized: 1.000` precedes each. Same score, same
size, same bar, same gate result. Outcomes: **+26.9%, −8.9%, +26.8%.**

---

## 2. THE SEPARATION TEST — every available ex-ante signal, scored

| candidate signal | winners (XOM/AMAT/NTR, AAOI) | losers (VOYA/AAL, AXTI/ACM/BIIB) | separates? |
|---|---|---|---|
| broker score (`Normalized:`) | 1.000 | 1.000 | **NO** |
| conviction raw (`raw=+1.800/1.700`) | 1.800 (AMAT), 1.800 (AAL is also 1.800) | 1.800 | **NO** |
| sized $ / % NAV | $837/$652/$840 = 14.0/10.7/14.0% | VOYA $840 = **14.0%** | **NO** |
| lane | prop_exp, trend, prop_exp | trend, propagation | **NO** |
| lane, OOS | AAOI = `momentum_watchlist_buy` | AXTI = `momentum_watchlist_buy` | **NO** |
| ext@entry | −0.07%, +9.53%, +0.29% | VOYA **+0.84%** (lowest of the four day-1 names bar TCMD) | **NO** |
| 20d/60d momentum at discovery | AMAT 20d +31.7% / 60d **+44.7%** | RVLV 60d **+44.7%**, AXTI 60d +74.1%, AAL 60d +34.9% | **NO** |
| days held | 35-40 sessions | VOYA **37 sessions** | **NO** |
| discovering trend | `saudi_oil_output_cut`, `us_iran_oil_supply_risk` | `analyst_pt_actions_mixed_feb25` (VOYA **and** RVLV, both losers) | suggestive, n=2, **untested OOS** |
| `conviction_tier` path | **`mcap_high` on 100% of bars** | **`default_low` on 92-97% of bars** | **YES on 915207 — and it INVERTS OOS** |

### 2.1 The one thing that looks like a signal, and why you must not use it

In 915207 the split is perfect:

```
conviction_tier: sym=XOM  tier=HIGH mcap=714475M raw_score=1.000 path=mcap_high   (482/482 bars)
conviction_tier: sym=AMAT tier=HIGH mcap=256431M raw_score=0.000 path=mcap_high   (465/465 bars)
conviction_tier: sym=NTR  tier=HIGH mcap=37025M  raw_score=1.000 path=mcap_high   (481/481 bars)
conviction_tier: sym=VOYA tier=LOW  mcap=6366M   raw_score=0.000 path=default_low (412/450 bars)
conviction_tier: sym=AAL  tier=LOW  mcap=6722M   raw_score=0.000 path=default_low (169/174 bars)
```

Persistent-`mcap_high` group (XOM, AMAT, NTR, BA, NVDA, SNDK): $2,655 deployed → **+$625.13**.
Modal-`default_low` group (TCMD, VOYA, AAL, RVLV): $1,795 deployed → **−$55.57**.

**It fails out of sample.** In 383778 the best single name is `default_low`:

```
conviction_tier: sym=AAOI tier=LOW mcap=6617M raw_score=0.000 path=default_low  (115/125 bars)
```
AAOI = **+$147.79 (+20.82%)**, the largest single-name gain in the run. The 383778
`mcap_high` cohort (MSFT, NVDA, AAPL, ABT, HOOD, LIN) made +$192.64; the `default_low`/`mcap_mid`
cohort (AAOI, AXTI, GSL, HLMN, ACM, BIIB, RIVN) made +$109.32 — the same order of magnitude,
with the single biggest winner on the "wrong" side.

**Stated plainly: market cap is a one-window artifact. Do not ship it.**

### 2.2 So what did the winners actually have?

They were **bought on the one bar the book had money.** `Buy budget:` from 915207:

```
2026-01-01  Buy budget: spendable=$3240 (cash=$6000, sells=$0, floor=$120, ramp=54%)
2026-01-06  Buy budget: spendable=$0    (cash=$38,   sells=$0, floor=$120, ramp=100%)
2026-01-09  Buy budget: spendable=$0    (cash=$35,   sells=$0, floor=$120, ramp=100%)
2026-01-12  Buy budget: spendable=$0    (cash=$35,   sells=$0, floor=$120, ramp=100%)
2026-01-14  Buy budget: spendable=$0    (cash=$35,   sells=$0, floor=$120, ramp=100%)
```

From 01-08 the book is frozen. The winners are the names that happened to be in the bar-1
discovery slate. VOYA was in the same slate and lost $75. **That is a coin flip, not an edge.**

---

## 3. WHAT DOES GENERALISE — the run makes money on names it converts EARLY, at SIZE, and
gives it back on the same names converted LATE

Both runs, same shape, using each run's own first-signal price vs its own fill price:

| run | name | first buy signal | price then | actual fill | price paid | delay | result |
|---|---|---|---:|---|---:|---:|---:|
| 915207 | **SNDK** | 01-06 mw rank **#1** (0.96), `new_buys=['SNDK']` | $328.19 | 01-28 | **$510.41** | 15 sessions | **+$6.91 on $29** (stock +166.10%) |
| 383778 | **AAOI** | 03-30 mw rank **#1** (1.001), `new_buys=['AAOI']` | $86.07 | 04-08 | **$120.18** | 7 sessions | +$147.79 on $710 (stock +48.17%) |
| 383778 | **LWLG** | 04-09 `momentum_watchlist_buy` | $8.25 | **never** | — | ∞ | **$0** (stock $6.74 → $14.78) |
| 383778 | AXTI | 04-16 mw rank #2 (1.653) | $68.68 | 04-24 | $76.67 | 6 sessions | **−$74.13** (at first-signal price it ends +3.0%) |

Delay does not distinguish winners from losers — it converts *both* into worse trades. AXTI is
the clean control: bought on its own signal it makes +$26; bought 6 sessions late it loses $74.

---

## 4. THE BINDING CONSTRAINT, NAMED AND PRICED

### 4.1 SNDK, 915207 — sized at conviction FIVE times and refused by the core floor

SNDK is momentum-watchlist rank #1 or #2 on **16 consecutive decision bars** (01-06 → 01-27).
The allocator sizes it correctly. The satellite/core cap kills it:

```
2026-01-09  [BROKER] SNDK @ 2026-01-09 15:00:00 ($363.01): buy action_intent=backfill_queue_buy
2026-01-09  [BROKER] SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($12 room); core would be squeezed below its floor
2026-01-09  [GNA]   V31.2 total-spend cap [CONCENTRATE]: funded 4 of 5 by conviction (GH@$866, SNDK@$866, UBER@$866, SLGN@$866) out of $3,895
2026-01-12  [BROKER] SNDK @ 2026-01-12 15:00:00 ($388.455): buy action_intent=backfill_queue_buy
2026-01-12  [BROKER] SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($21 room); core would be squeezed below its floor
2026-01-14  [BROKER] SNDK @ 2026-01-14 15:00:00 ($393.06): buy action_intent=backfill_queue_buy
2026-01-14  [BROKER] SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($-1 room); core would be squeezed below its floor
2026-01-19  [BROKER] SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($-28 room); core would be squeezed below its floor
2026-01-28  [BROKER] SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 — funding $29 of room out of the core (floor-bounded)
2026-01-28  [BROKER] SATELLITE CAP: SNDK trimmed $904 -> $29 to keep the core at target
2026-01-28  [BROKER] [execution] FILL BUY SNDK qty=0.05702688 price=510.406171
```

**$866 wanted at $363. $29 delivered at $510.** SNDK ran $237.33 → $631.54 (+166.10%) and
contributed **+$6.91** to a $581.83 run. Meanwhile the SPY core it was protecting made **+$8.96**.

### 4.2 LWLG, 383778 — the identical event, out of sample

```
2026-04-09  [BROKER] LWLG @ 2026-04-09 14:00:00 ($8.25):  buy action_intent=momentum_watchlist_buy
2026-04-09  [BROKER] SATELLITE CAP: LWLG skipped — satellite at its overflow ceiling ($19 room); core would be squeezed below its floor
2026-04-13  [BROKER] LWLG @ 2026-04-13 14:00:00 ($11.96): buy action_intent=momentum_watchlist_buy
2026-04-13  [BROKER] SATELLITE CAP: LWLG skipped — satellite at its overflow ceiling ($-4 room); core would be squeezed below its floor
2026-04-21  [BROKER] LWLG @ 2026-04-21 14:00:00 ($14.12): buy action_intent=momentum_watchlist_buy
2026-04-21  [BROKER] SATELLITE CAP: LWLG skipped — satellite at its overflow ceiling ($16 room); core would be squeezed below its floor
2026-04-23  [BROKER] SATELLITE OVERFLOW: LWLG raw=+1.981 >= 1.50 — funding $882 of room out of the core (floor-bounded)
2026-04-23  [BROKER] SKIP BUY LWLG — cash_to_use $45.45 < min $386 (allocated $882.44)
```

LWLG was mw top-3 from 04-13 and **never bought**. $6.74 → $14.78 (04-23) / $12.53 (04-24).

### 4.3 The cap in aggregate — this is not a handful of edge cases

`[core] funding request trimmed $X -> $Y — satellite headroom will refuse the remainder`:

| run | bars with a trim | intended funding | delivered | refused |
|---|---:|---:|---:|---:|
| 915207 | 16 | $37,728 | $4,067 | **$33,661** |
| 383778 | 16 | $50,523 | $8,695 | **$41,828** |

915207 from 01-08 onward: `$3,459→$12`, `$3,463→$21`, `$2,605→$19`, `$3,507→$0`, `$1,783→$0`,
`$891→$0`, `$3,554→$0`, `$3,549→$0`, `$2,712→$29`, `$917→$51`.
383778: `$2,486→$19`, `$2,526→$2`, `$2,533→$0`, `$3,486→$0`, `$3,485→$0`, `$3,686→$16`, `$3,526→$108`.

Plus `SATELLITE CAP … trimmed`: 14 events / **$6,196** cut in 915207, 21 events / **$7,717** in
383778.

### 4.4 The mechanism, in code

* `broker.py:14926-14936` — `_sat_room = _core_sleeve_satellite_headroom(..., conviction=_sat_is_conv)`;
  `if _sat_room <= _CORE_MIN_SATELLITE_TRIM_USD: … continue` ← the skip.
* `broker.py:3407-3409` — `share = satellite_max_share(cfg) if conviction else satellite_design_share(cfg)`;
  `return (share * nav) - satellite`.
* `core_sleeve.py:305-309` — `satellite_max_share` = `1.0 - core_min_pct - cash_reserve_floor_pct`,
  i.e. **0.68** on `core_min_pct=0.30, cash_reserve_floor_pct=0.02`.
* `core_sleeve.py:249-251` — `satellite_design_share` = `1.0 - core_target_pct - cash_reserve_floor_pct`,
  i.e. **0.58** on the run's `core_target_pct = 0.40` (`[core] bought $2400.00 SPY … 0.0% -> 40.0% of NAV`).

**The conviction overflow band is exactly `core_target_pct − core_min_pct` = 10.1pp of NAV.**
Confirmed arithmetically against the run: on 01-09 the two headroom readings printed side by
side are `design share ($-606 room)` and `overflow ceiling ($12 room)` — a $618 gap on a
$6,114 NAV = **10.1pp**. Once six names at ~14% each have taken the 58% design share, the whole
10pp band is gone and the #1 name in the market cannot get a dollar.

The bear window is the control: **bt 542754 has ZERO `SATELLITE CAP … skipped` lines and ZERO
`funding request trimmed` lines** — the core is off in bear by design, so this lever cannot
touch the validated SQQQ leg.

---

## 5. THE ONE CHANGE

> **Widen the conviction overflow band: `core_min_pct` 0.30 → 0.15, scoped to the bull/chop
> regime profiles. One key. Ships default-OFF elsewhere. Inert in bear.**

Band goes from 10.1pp to 25.1pp of NAV — **$618 → $1,530** on a $6,100 book — which is enough
to fund one $866 raw≥1.50 name per bar out of the index without selling any held name.

### Expected effect, priced at the run's own sizes and its own quoted prices

| run | name | fund at | shares | window-end mark | counterfactual P&L | actual | Δ | Δ on $6,000 |
|---|---|---|---:|---|---:|---:|---:|---:|
| 915207 | SNDK | 01-09 @ ~$365 (decision $363.01 + the run's own 0.54% slip), $866 | 2.373 | $631.54 | **+$632** | +$6.91 | **+$625** | **+10.4pp** |
| 383778 | LWLG | 04-09 @ ~$8.29, ~$850 | 102.5 | $12.53 (04-24) | **+$435** | $0 | **+$435** | **+7.3pp** |
| 383778 | AAOI | 04-03 @ ~$107.6, $820 (the size the same bar gave HLMN/LYV/NCNO/NTRS) | 7.62 | sold 04-23 @ $145.22 | +$287 | +$147.79 | +$139 | +2.3pp |
| 542754 | — | no satellite-cap or funding-trim events at all | | | **0** | | **0** | **0** |

Core drag from a ~$900 smaller SPY position: 915207 SPY +0.64% over the window → ≈ **−$6**;
383778 SPY +12.79% over ~18 held days → ≈ **−$100**. Both are already netted out of the
Δ estimates in the reply.

**915207 +9.70% → ≈ +20.0%. 383778 +4.75% → ≈ +11.5% (LWLG alone).** Both clear the +6%/mo target.

### Why this and not the alternatives

* **Not a better filter.** §2 shows no available signal separates winner from loser. Adding a
  filter cannot help; it can only cut the sample.
* **Not the extension gate.** It *is* the first refusal for SNDK (01-06/01-07) and AAOI
  (03-30/04-02), and the mw_buy-lane blocked basket is positive on both windows
  (915207 mean +28.2% / median +17.6%, n=10; 383778 mean +13.3%, n=4 — vs the
  `Entry extension gate:` quality-filter basket at +6.4% / +6.3%). **But** `OBJECTIVE.txt`
  lists "loosening the entry-extension gate" under DO NOT RETRY (blocked basket −7.95%), and
  `graph_nexus_analysis.py:5527-5531` documents why the conviction bypass was deliberately
  removed (CAR/TXG/AMPX). **Do not touch it as the first move.** It is the *second* refusal
  that matters anyway: SNDK and AAOI both cleared the gate later and were then killed by the
  headroom cap and by cash.
* **Not `max_positions`.** `max_positions gate armed: held=6, cap=8` on the 383778 bar where
  LWLG was refused — the cap was not binding.
* **Not exits.** Confirmed already in `exits-and-capture.md`; nothing in these two runs
  contradicts it.

### Risks and what this does NOT fix — state these to the operator

1. **Not a paired run.** These are counterfactuals reconstructed from each run's own decision
   tape. Validate with paired runs on 915207's and 383778's windows plus one non-semiconductor
   window, each arm with its own `history_scope_salt`, equally warm.
2. **The new room is spent in the priority sizing order, and the momentum watchlist has no
   seat at that table.** `Priority sizing order: watchlist=none | prop_exp=…` on **62 of 62**
   sizing bars across both runs. Cause: `watchlist_priority_slots` defaults to **0**
   (`graph_nexus_analysis.py:28584`) while `propagation_expansion_reserved_slots` defaults to
   **4** (`:28586`); and `is_watchlist_priority` is fed from the *sector* watchlist
   (`_active_watchlist_priority_tickers`, `:28100`), which logs
   `Watchlist candidate audit: active_sectors=none matched=none` on **64 of 64** bars in both
   runs — the set is always empty, so the momentum watchlist can never claim a front slot.
   On 383778 04-03 this is exactly what displaced AAOI: the four reserved prop_exp names
   (HLMN@$808, LYV@$808, NCNO@$808, NTRS@$808) took the budget, `Deferred unfunded buys
   demoted to hold: AAOI, BLBX, VLO` — and **three of those four never filled at all**;
   HLMN filled $82 and made +$7.07. SNDK is already inside 915207's funded top-4 on 01-09,
   so the core_min change reaches it; LWLG arrives on its own broker lane, so it reaches it
   too. But this is the obvious second lever and it is a *reader/writer* bug of the same
   family as `rank_band_momentum_exempt_min_score`.
3. **It does not fix the cash race.** `SKIP BUY LWLG — cash_to_use $45.45 < min $386
   (allocated $882.44)` on 04-23. Room without cash is still nothing. That is
   `_SYNTHESIS.md` root cause #1 and remains open.
4. **Turnover.** `TURNOVER BUDGET BINDING: 149%…235% of NAV` fires 258× in 915207 and 298× in
   383778 against a 50% budget. The conviction bypass (`raw>=1.50`) already lets the names in
   question through, but more headroom will raise gross turnover; watch it in the paired run.
5. **Regime scoping needs a code touch.** `core_target_pct` is resolved regime-aware
   (`core_sleeve.py:226-248`); `core_min_pct` is read flat (`core_sleeve.py:307`,
   `satellite_max_share(config, *, regime=None)` never uses `regime` for it). A bull/chop-only
   `core_min_pct` therefore needs the same regime resolution wired in, OR accept a global
   change — which 542754 shows is inert in bear (zero cap events) but is untested against a
   bear that actually opens with a warm core.

---

## 6. VERBATIM CITATION INDEX

| claim | source |
|---|---|
| identical day-1 treatment of XOM/NTR/VOYA | 915207 log 1794-1806 |
| VOYA/RVLV both from `analyst_pt_actions_mixed_feb25` | 915207 log 116, 103 |
| conviction tiers | 915207 `conviction_tier:` (482/481/465/450/174 lines); 383778 AAOI 125 lines |
| SNDK sized $866, skipped ×5 | 915207 log 7926-7927, 8785, 8879-8880, 10805-10806, 13624, 20311-20314 |
| SNDK mw rank #1 for 16 bars | 915207 `Momentum watchlist: … top3=[('SNDK', …)]` 5880 → 20150 |
| AAOI mw rank #1 blocked at $86.07 | 383778 log 370, 372, 1592 |
| AAOI deferred for four prop_exp names | 383778 log 5213, 5220, 5230, 5234 |
| LWLG skipped ×3, then cash-skipped | 383778 log 7850-7851, 9643-9644, 15191-15192, 17113-17117 |
| funding-request trims | 915207 / 383778 `[core] funding request trimmed` (16 each) |
| headroom formula | `broker.py:3407-3409`, `core_sleeve.py:249-251`, `:305-309` |
| satellite cap call site | `broker.py:14926-14968` |
| priority slots 0 vs 4 | `graph_nexus_analysis.py:28584-28586`, `:28100` |
| watchlist-priority set always empty | `Watchlist candidate audit: … matched=none` ×43 (915207), ×21 (383778) |
| core at 40% of NAV | 915207 `[core] bought $2400.00 SPY @ 681.82 (band_deploy: 0.0% -> 40.0% of NAV)` |
| bear control is clean | 542754: 0 `SATELLITE CAP … skipped`, 0 `funding request trimmed` |
