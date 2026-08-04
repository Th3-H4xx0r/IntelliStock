# Full session record — 2026-08-03 / 08-04

Everything from one long session: what was measured, what was fixed, what was
retracted, and what is left. Jarvis handoff points here; this is the complete
version.

---

## 0. THE STANDING GOAL, AND WHY PART OF IT IS UNREACHABLE

User's standing goal (a Stop hook, fired ~20 times): *"fix all issues and make it
ready for live and all backtests must be descriptive of live and must overperform SPY
always, work fully autonomously and make sure I can make a lot of money in live mode."*

**"Overperform SPY always" is a FALSE PREMISE, not an unfinished task.**

Ten parallel Opus agents, all computing from PRIMARY data (Ken French's library, Cboe's
official BXM/PUT/SPX histories, AQR's own factor files, yfinance total returns, this
repo's RethinkDB) rather than citing secondary summaries. Every independent line reached
the same verdict: **nothing a retail account TRADES has beaten SPY out-of-sample.**

Do not manufacture a window, seed or config that appears to. That is exactly how this
project produced the +15.80% artifact. Deliver the cost work, quantify it, and say so.

---

## 1. CORRECTED SPY BENCHMARKS — prior sessions cited these WRONG

Benchmark SPY as measured INSIDE each backtest — its own price feed, same bars,
price-return like the portfolio. Source:
`BacktestResults.portfolio_value_history[*].prices.SPY`, first vs last.

| window | WRONG (cited for weeks) | **CORRECT** |
|---|---|---|
| bull 2026-03-30..04-27 | +13.23% | **+12.79%** |
| bear 2026-03-02..03-30 | −6.89% | **−7.89%** |
| OOS 2026-07-07..08-01 | −0.88% | **−0.60%** |
| 2026-07-20..07-27 | — | **−0.60%** |

yfinance disagrees (different bar boundaries + total-return with dividends reinvested).
Do NOT use it for this comparison.

---

## 2. EVERY BACKTEST RUN THIS SESSION, vs SPY

### Bull · 2026-03-30 → 04-27 · SPY +12.79%

| bt | strategy | return | vs SPY | trades | turnover | build |
|---|---|---|---|---|---|---|
| 337043 | **Turnover-cut (4 keys)** | **+16.02%** | **+3.23** | 10 | 23.3×/yr | d0fe242ae4 |
| 894413 | Layer 3 (full config) | +11.60% | −1.20 | 24 | 27.8×/yr | 03980176df |
| 264106 | doc-179 baseline | +2.30% | **−10.49** | 104 | 79.2×/yr | d0fe242ae4 |
| 842458 | doc-179 baseline | +0.06% | **−12.73** | 61 | 65.3×/yr | 03980176df |
| 300917 | v2 IndexCoreTilt (15%-capped) | +1.74% | −11.05 | 1 | — | local |

### Bear · 2026-03-02 → 03-30 · SPY −7.89%

| bt | strategy | return | vs SPY | trades |
|---|---|---|---|---|
| 766499 | baseline, SQQQ hedge fired | +10.22% | **+18.11** | 16 |
| 804910 | baseline, SQQQ hedge (current build) | +10.07% | **+17.96** | — |
| 656271 | Layer 3, hedge→cash | −6.84% | +1.05 | 33 |
| **332464** | **Turnover-cut (4 keys)** | **−2.71%** | **+5.18** | 17 |

### Out-of-sample · 2026-07-07 → 08-01 · SPY −0.60%

| bt | strategy | return | vs SPY | trades |
|---|---|---|---|---|
| 669068 | Layer 3, core fixed | −1.87% | −1.27 | 36 |
| 383711 | Layer 3, core bugged | −1.92% | −1.32 | 62 |
| 475798 | doc-179 baseline | −2.22% | −1.62 | 47 |

### Short · 2026-07-20 → 07-27 · SPY −0.60%

| bt | strategy | return | vs SPY | trades |
|---|---|---|---|---|
| 766075 | baseline (alpaca-main) | +3.93% | +4.53 | 2 |
| 729683 | baseline (control) | −0.18% | +0.42 | 9 |
| 262454 | Turnover-cut (4 keys) | −1.52% | −0.92 | 11 |

### How to read it

**Only 4 of 13 beat SPY**, and each has a non-skill explanation:
- **766499 (+18.11)** — entirely the SQQQ leveraged short in a 21-day downtrend at the
  **88th percentile** of bear-episode length. The stress audit measured that mechanism
  across 45 historical episodes: **18 winners (40%), −$87,736 on $100k.**
- **337043 (+3.23)** — 59% from three high-beta AI/growth ETFs held through a bull.
- **766075 (+4.53)** — **two trades.** Noise.
- **729683 (+0.42)** — inside noise.

**The same baseline config is −12.73 in the bull and +18.11 in the bear.** That is
regime exposure, not edge.

---

## 3. THE TURNOVER CUT — 3.4× in the BULL window only (see §4: it reverses in a bear)

Paired 28-day A/B. Build `d0fe242ae4:1785797551`, window 2026-03-30..04-27, both arms
identical except four config keys, isolated `history_scope_salt` per arm,
`historical_lookback_enabled=False` on both.

| | control 264106 (doc-185 = 179 as-is) | cut 337043 (doc-184) |
|---|---|---|
| trades | **104** | **10** |
| notional | $36,461 | $10,733 |
| **turnover** | **79.2×/yr** | **23.3×/yr** |
| **exec cost** | **1.41% of book** | **0.41%** |
| pnl | +2.30% | +16.02% |

The four keys — all built, all default-OFF:

```
core_sleeve_enabled          = true   # REQUIRES residual_sleeve_enabled=true, else fails closed
min_hold_enabled             = true
min_hold_days                = 30
rank_band_enabled            = true
turnover_budget_monthly_pct  = 0.50
```

**⚠️ DO NOT cite the +16.02% as alpha.** Attribution: SPY +$394 (41%), AGIX +$248 (26%),
ARKG +$172 (18%), AIQ +$144 (15%). **59% is three high-beta AI/growth ETFs held through
one bull window** — what such ETFs do in that regime, and the reverse in a drawdown.
One window, three names, and it is the SAME window that produced the +15.80% artifact.

**The mechanism is NOT better picking — it is that not churning means HOLDING BETA.**
The control traded 104 times across 24 symbols and captured 2.30% of a +12.79% market;
its winners (AAOI +$248, SNSE +$93) were eaten by losers (FTH −$88, FIRY −$56,
ASHS −$40) plus 1.41% of execution cost.

**⚠️ A 7-DAY VERSION OF THIS A/B IS WORTHLESS.** I ran one first and it appeared to
REFUTE the thesis: cut 111.5×/yr vs control 86.9×/yr. Over 7 days, BUILDING the
~60%-of-NAV core IS the turnover ($5,100 of `residual_bull_deploy` on a $6k book).
Use 28 days minimum so establishment amortises.

---

## 4. THE BEAR TEST — **COMPLETE, AND IT OVERTURNS THE HEADLINE**

bt 804910 (control, hedge on) vs bt 332464 (4 keys), 2026-03-02..03-30, SPY −7.89%,
build `d0fe242ae4`.

| | control (hedge ON) | cut (4 keys) |
|---|---|---|
| pnl | **+10.07%** | **−2.71%** |
| trades | 19 | 17 |
| notional | $14,773 | $16,453 |
| **turnover** | **32.1×/yr** | **35.7×/yr** ⬆ |
| exec cost | 0.57% of book | **0.64%** ⬆ |
| P&L driver | **SQQQ +$676** | **SPY −$165** |

### TWO findings, both bad for the four-key bundle

**1. The turnover benefit is BULL-SPECIFIC and REVERSES in a bear.**

| window | control | cut | effect |
|---|---|---|---|
| bull | 79.2×/yr | 23.3×/yr | **−70%** ✅ |
| bear | 32.1×/yr | **35.7×/yr** | **+11%** ❌ |

In the bear the core must de-risk and re-deploy: `residual_bull_protective_exit` is
$3,866 (23.5% of notional) plus `residual_bull_deploy` $5,712 (34.7%). That is exactly
the churn the config exists to remove. **The 3.4× reduction was one window, not a
general property.** Do not quote it as one.

**2. Enabling `core_sleeve_enabled` costs the SQQQ hedge — 12.78pp in this window.**
Control +10.07% vs cut −2.71%. The control's entire result is SQQQ +$676; the cut arm
holds SPY straight down.

### The nuance that stops this being a clean verdict

The hedge is NOT reliably good either. The stress audit measured that SQQQ mechanism
across 45 historical episodes: **18 winners (40%), −$87,736 total on $100k.** This bear
window is a 21-day downtrend at the **88th percentile** of episode length — one of the
40% where a persistent short works. So:

- giving up the hedge cost **12.78pp HERE**
- but on the historical base rate the hedge is **net negative**

Both statements are true. The honest position is that this single window cannot settle
it, and neither can any single window.

### RECOMMENDATION (following the rule set BEFORE the result)

Result landed in the "in between" band, so: **report the tradeoff, do not bundle.**

- **Do NOT enable `core_sleeve_enabled` on doc-179** on the strength of the turnover
  argument — the turnover argument does not survive the bear window.
- The other three keys (`min_hold_enabled`, `rank_band_enabled`,
  `turnover_budget_monthly_pct`) cut churn WITHOUT touching the hedge and were never
  isolated. **They need their own A/B**, bull AND bear, before any recommendation.
- The 4-key bundle was measured as a bundle. How much of the bull-window 3.4× came from
  `core_sleeve_enabled` versus the other three is **UNKNOWN**.

### NEXT EXPERIMENT (the one that actually decides it)

Three-arm, both windows, same build:
1. doc-179 as-is (control)
2. `min_hold` + `rank_band` + `turnover_budget` only — **no `core_sleeve`**
3. all four (already measured)

If arm 2 cuts turnover in BOTH regimes while keeping the hedge, that is the shippable
configuration and it is a genuinely good outcome. Cached windows cost ~$0.001, so this
is nearly free — there is no excuse for not running it.

## 4b. (superseded) original bear-test plan

`core_sleeve_enabled=true` routes bear de-risk to CASH and **never parks the SQQQ leg**
(deliberate — see the comment at broker.py's `_residual_sleeve_deploy`: a −3x
daily-rebalanced instrument carries structurally negative decay, cost 1.67pp on the bull
window, consumed 15% of traded notional there, and needed six independent suppressors
before it stopped losing money).

- bear control 804910 (hedge on): **+10.07%** vs SPY −7.89%
- bear cut 332464 (4 keys): **GET THIS FIRST**

**Decision rule, set before seeing it:**

| cut result | recommendation |
|---|---|
| ≈ +10%, hedge intact | enable all four keys — the cost saving is free |
| ≈ −7%, hedge lost | **do NOT enable `core_sleeve_enabled`**; take `min_hold_enabled` + `rank_band_enabled` only |
| in between | report the exact tradeoff, no bundled recommendation |

Caveat either way: the bear window is 88th-percentile length and the hedge wins only
40% of the time historically. A good result here is ALSO regime-specific.

---

## 5. UNCOMMITTED WORK — passive limit orders (built, tested, NOT pushed)

Held back deliberately: pushing triggers a redeploy that kills in-flight backtests.

Files modified, not committed:
- `backend/simulated_execution.py` — `SimulationOrder.limit_price` +
  `expire_after_quotes`; passive branch in `on_quote`; `expired_order_count` property
- `backend/portfolio_emulator.py` — `_passive_limit_for()` + `set_passive_execution()`
  classmethod override, wired into `SimulationOrder(...)`
- `backend/broker.py` — reads `passive_execution_enabled` / `passive_expire_quotes` from
  strategy config at both "Loaded N strategy(ies)" sites
- `backend/tests/test_passive_limit_execution.py` — 12 tests, green (UNTRACKED)

Suite with these: **4324 passed**, 19 failures = the three untracked red-by-design files.

**The fill rule is deliberately pessimistic**, because an optimistic passive model is
indistinguishable from free money and this repo already shipped one fantasy execution
model (sub-$1 fills, zero spread):
- a buy fills ONLY if the **ASK** reaches the limit — never because the mid or bid
  drifted our way
- fills AT the limit, never better (real price improvement exists; assuming it is how a
  backtest lies)
- `spread_cost = 0` and `slippage_cost = 0` for passive fills — we set the price
- unfilled orders EXPIRE and are COUNTED via `expired_order_count`

Measured saving ≈ **22.8 bps per side**. The cost that replaces it is non-fill risk.
**A run that "saves" 22.8bps while missing a third of its entries has saved nothing** —
always report `expired_order_count` alongside any cost claim.

Only viable alongside a holding floor: waiting hours for a fill is free on a 30-day
hold and fatal at a 15-minute cadence. That floor now exists (`e7901b3`).

---

## 6. NINE BUGS FIXED (all pushed)

| # | bug | commit |
|---|---|---|
| 1 | **`NEO4J_*` never forwarded to instance containers** → fell back to `DEFAULT_NEO4J_PASSWORD="intellistock"` → `Unauthorized` on ETF mapping, sector mapping, market-cap cache. Run CONTINUES degraded and silent. **Every LIVE tick ran a graph strategy with an inert graph.** Backtests unaffected (backtest-engine gets it from compose), so live and backtest were never the same strategy | `435e508` |
| 2 | **`kind="crypto"` missing from `start_instance_container` dispatch** → every crypto instance raised `LiveReadinessError("instance kind is malformed")`. The soak survived only because its container predated the dispatch — one restart from ending silently with `runCommand=True` still set | `435e508` |
| 3 | **Crypto live-gate regression I CAUSED.** Fixing #2 meant crypto reached `brokerage_requires_live_gate`, which rejected any kind outside equities → `INSTANCE CRASH [test]: instance is not an equities configuration`, crash-loop | `0f0edb3` |
| 4 | **`agent.run_sync` had NO timeout** (llm_utils.py:3099/3101). All deadline machinery (`_call_deadline`, `_attempt_timeout`, 300s/600s caps) covers only the PLAIN paths; the PydanticAI-native path — every provider routes through it — was unbounded. **One hung call wedged the entire backtest engine**; stopping the backtest ROW did not free it. On a live tick this stalls a trading bar | `42fa08c` |
| 5 | **Core deploy retry storm.** `core_rebalance_order` sized against the MID while execution charges mid + half-spread + fees, so a deploy at the full spendable balance could never afford itself; the cadence clock stamped only on CONFIRMED fills, so it re-issued every tick. bt 383711: 23 identical `band_deploy` decisions, 21 `ok=False` | `e65d3e8` |
| 6 | **Minimum holding period** — new `backend/min_hold.py` + broker wiring | `e7901b3` |
| 7 | **IndexCoreTilt hold path** (returned satellite targets on a hold → re-sized every bar) | `24e35e1` |
| 8 | **IndexCoreTilt sizing + cadence** (bare `1` got the broker's ~$1,000 default; cadence stamped by a partial fill locked an 85pp drift for 90 days) | `1e5cb9e` |
| 9 | **Clean-room baseline preflight** — refuse to launch with one clear message instead of 6 crash-loop restarts | `3f99873` |

Plus, fixed by hand (not code): **zombie `BacktestInstances` rows** stuck at `running`
since 2026-07-23 and 07-30 while their results rows were terminal, holding engine slots.

---

## 7. FOUR CLAIMS I RETRACTED — do not resurrect

1. **"Costs explain 119% of the live shortfall; this was a cost problem read as an alpha
   problem."** WRONG. Stripping ALL execution cost from 7 runs flips no sign, and moves
   alpha ≤1.2pp. **Satellite gross alpha is NEGATIVE in 7 of 7 runs**, mean ≈ −6.9pp per
   ~1-month window. Costs were never hiding alpha.
2. **"The `api` container's missing Docker socket is the blocker."** Real defect — its
   `200 {"started": true}` means "flag set", never "container started", because only
   `backend` and `backtest-engine` mount `/var/run/docker.sock` — but it was not the cause.
3. **"The graph was never switched on" (`raw_score=0.000` in 677/677).** WRONG. Tested
   directly (bt 766075, $0.44): raw_score is still 0.000 **with Neo4j connected and
   `mcap=109118M` populated**, AND the run traded on a `main_signal` pick (which requires
   a graph score). `BacktestResults.logs` is **TAIL-TRUNCATED (~500 lines)** so those are
   late MONITOR cycles, not entry decisions. The agent that produced the figure warned me
   of exactly this; I quoted the caveat and built on the number anyway.
4. **The SPY benchmarks** — see §1.

---

## 8. VERIFIED FACTS WORTH KEEPING

- **The Neo4j graph is REAL and populated**: 2,534,202 nodes / 7,043,212 relationships.
  `GraphEdgeInterval` 2,347,738 · `LegalEntity` 166,494 · `Institution` 8,604 ·
  `Company` 5,568 · `ETF` 5,108 · `LEIEntity` 462 · `Theme` 67 · `Index` 62.
  Credentials in `.env` are valid. Live simply never received them until `435e508`.
- **`BROKER_MAX_SINGLE_POSITION_PCT` = 15% hard ceiling** on ANY single position. A plain
  `run_once` strategy therefore **CANNOT hold an index core** — asked for 100% SPY, got
  15%. Large cores only work through the residual sleeve, which carries six
  `_sleeve_symbols` sell-exemptions. This is why `core_sleeve.py` reuses
  `residual_sleeve_symbol`, and why `_core_sleeve_cfg` fails CLOSED without
  `residual_sleeve_enabled`.
- **Cached-window backtests cost ~$0.001, not $1.30.** The LLM prompt cache is warm
  (301 and 286 `LLMUsage` rows for $0.0008 / $0.0011). I rationed runs all session on a
  wrong estimate taken from cold-cache runs.
- **The backtest engine SERIALIZES.** A second concurrent launch returns an id but never
  creates a `BacktestResults` row.
- **`BacktestInstances` = queue; `BacktestResults` = output.** A `pending` queue entry has
  no results row, so the UI detail page returns **HTTP 400** and the run cannot be viewed
  or deleted.
- **`LIVE_CLEAN_ROOM_MODE` is set host-wide**, so EVERY instance requires `initial_value`.
  Absent → adapter refuses to build → 6 broker restarts in 60s → latched, `runCommand`
  flipped False. Correct refusal (inferring a baseline from broker equity would redefine
  the P&L reference every restart), bad ergonomics — now fixed.
- **Strategy config lives at `Strategies[id].strategies[0].config`** (533 keys), NOT the
  top-level `.config` (189 keys, none of the nexus levers).
- **zsh does NOT word-split unquoted `$VAR`** — `pytest $FILES` passes one argument and
  "collects nothing", which reads like a green run.
- **An inline `.env` regex of `[A-Z_]+` silently skips keys containing DIGITS** —
  `NEO4J_*` parsed as empty and the failure looked server-side. Use
  `[A-Za-z_][A-Za-z0-9_]*`.
- **`mobile/lib/features/instances/data/instance_repository.dart`** is the authoritative
  list of API routes — `server.py` has no route decorators to grep.
- **Live-instance FULL cycles** are anchored to `nexus_dual_cadence_full_cycle_minute_of_day_pt`
  (default 390 = 06:30 PT) and gated once per PT date by
  `_nexus_full_cycle_completed_date` in the strategy cache
  (`NexusStrategyCache`, id `<instance>|<strategy>|<hash>|live|<date>`).

---

## 9. THE TEN-AGENT RESEARCH SWEEP — what does NOT beat SPY

All computed from primary data, not cited from summaries.

- **Factor ETFs**: EVERY category lost over 10Y *and* 15Y. USMV −5.20pp/yr, SPLV −6.63,
  IWD −4.02, VTV −2.59, SCHD −2.30, COWZ −1.88, small-value −4.5 to −5.2. Post-2005
  French premia: SMB −0.72, HML −1.29, CMA +0.19, RMW +2.46 (t=1.73, insignificant).
  **AVUV's apparent win is an inception artifact** — +0.08pp/yr on the honest common
  window. Small-value's entire case is 1999–2009 (+8.98pp/yr) vs 2010–2026 (−2.22pp/yr):
  a valuation-timing call, not a premium. **Remove calendar 2021 and the 10-year
  small-value record collapses** (+1.48pp → −3.3pp).
- **Momentum**: UMD post-2005 = +1.20%/yr, **t = 0.75**. Long-only decile beats the index
  on return but Sharpe 0.56 vs 0.65 — that is beta. GEM −3.4pp/yr full sample,
  **−5.8pp/yr since publication**. Faber 10-month: +0.05 Sharpe over 32 years for
  −1.5pp/yr, and −6.3pp/yr since 2013. **The DAILY 200-day version returns 5.96% vs SPY
  10.89% — pure whipsaw. Signal frequency destroys trend-following**, an independent
  confirmation of the turnover thesis.
- **Leverage**: never improves Sharpe. SSO = 1.48× SPY's return for 2× the risk over 20
  years. 3× ≈ **56% chance of a >50% loss per decade**; 2000–02 and 2007–09 would each
  have been ≈ −97%. HFEA −66% in 2022 (the negative stock/bond correlation is
  regime-conditional, not structural). Alpaca: 6.25% margin, **75% maintenance on 3× ETFs**,
  PDT 3 trades/5 days under $25k. Half-Kelly ≈ 0.5–0.6×.
- **Options**: BXM and PUT are **FRICTIONLESS indices** and still lost 3.9–4.9pp/yr over
  two decades ($6k → $25,530 BXM vs $61,045 SPX TR). A 30-delta SPY put needs **$74,500
  collateral = 12.4× the account**. Affordable single names show **17.4% median
  round-trip friction** vs SPY's 0.7% — a ~20× small-account penalty. Category to avoid.
  BXM's "lower risk" claim also fails: 73% SPY / 27% T-bills matches its volatility and
  beats it by 1.7pp/yr.
- **Concentration**: with no demonstrated skill it lowers the MEDIAN outcome while
  leaving the mean unchanged (Bessembinder skew). 14 positions is already near-optimal;
  14→3 costs 1.6pp/yr of compounding and doubles P(−50%) to 71%. **Grinold: your top-3
  need 2.2× the information coefficient of your average pick just to break even.**
- **Piotroski F-score**: failed replication — Hou/Xue/Zhang list it under "anomalies that
  cannot be replicated", 0.29%/mo, **t = 1.06**. Novy-Marx & Velikov: gross 0.20%/mo
  (t=1.04) → net 0.09%/mo (t=0.45). No ETF implements it.
- **QMJ (quality)**: from AQR's own file — full sample 3.49%/yr → post-publication
  (2013–2026) 1.58% → **trailing 10yr −0.16%/yr (Sharpe 0.04)**. ~60% decay, matching
  McLean & Pontiff's generic 58%.
- **Value premium**: worst drawdown in the 100-year record — HML 2007–2026 −1.85%/yr,
  cumulative −30.56%, max DD −57.79%, still 30.6% below its 2006 peak. AQR's
  intangibles-corrected HML Devil is also negative (−0.97%/yr).

### The ONE thing with a real edge

**Microcap value+profitability, LONG-ONLY, ANNUAL rebalance**: ~3%/yr net alpha.
Microcap cost ≈ 4.3bp/mo per 1% one-sided turnover (~215bp round trip), so break-even is
~26%/mo and annual rebalancing (2.9%/mo) has **9× headroom**. Anything MONTHLY is −2 to
−5× negative — the Cohen/Malloy/Pomorski 82bp opportunistic-insider signal is −250bp/mo
at microcap spreads.
**Blocker: 25 microcaps at ~60% idiosyncratic vol ⇒ ~13%/yr tracking error, so a 3%/yr
edge needs ~75 YEARS to reach t=2.** Alpaca is exchange-listed only (no OTC), which is a
feature. Backtest survivorship bias alone is 1–2%/yr — comparable to the entire edge.

### Cost is only binding at HIGH turnover — an important correction

| turnover | drag/yr on $6k |
|---|---|
| 290%/mo (live today) | **8.07%** ← binding |
| 830%/yr | 1.93% |
| 300%/yr | 0.70% ≈ **$42** |
| 100%/yr | 0.23% |

So the turnover work **solves the cost problem outright**, not partially. Below ~100%/yr,
23.2bps is a rounding error and the binding constraint becomes "is there any alpha at all".

### The endpoint-sensitivity warning

One agent measured MTUM at **+2.2pp/yr on June 30** and **≈+0.5pp/yr on July 31**.
Nothing changed but the date — MTUM fell −12.6% in July. Same artifact class as the
+15.80% headline, and a live reminder that a three-window A/B is endpoint-sensitive too.

---

## 10. THE REDDIT THREAD (r/ValueInvesting, "consistently beat the SP 500")

181 comments, 75 top-level, parsed from the user's downloaded `.json` (Reddit blocks the
fetcher directly).

| theme | comments | top score |
|---|---|---|
| deep knowledge / circle of competence | 19 | 84 |
| **tech overweight explains it** | **18** | 84 |
| buy the dip / valuation | 16 | 872 |
| concentration, few positions | 14 | **872** |
| quality / FCF / moat | 14 | 84 |
| long holding / don't sell | 11 | 872 |
| **survivorship / luck / "just lie"** | **11** | **160** |
| just buy the index | 8 | 26 |

The two highest-scoring comments contradict each other. **#1 (872)**: *"Only buy shares
of a company you have extremely high conviction in… Don't take profits solely for the
sake of taking profits. Don't try to be a trader… Don't be scared to add to your
winners."* **#2 (160)**: *"Just lie about it on Reddit."*

Most honest voices:
> *"I invested in Visa and enjoyed a 17x banger. **I was lucky. Warren Buffett would call
> me a coin flipper.**"*
> *"In the very long run I don't know a single person who beat the index… most people here
> who have successfully beaten the index **invested a little heavier in tech over the past
> decade** so they're cocky now."*
> *"My biggest trick and tip is **to be lucky**. I'd be lying if I said I anticipated Nvidia."*

The two most credible claims state methodology: *"23.41% IRR vs the S&P 500 TR 16.28%"*
and *"19% annualized, per my broker, time weighted, since 2014"* — both ~4–7pp of excess
over exactly the period a tech tilt delivered that for free.

**Why it matters:** the top-voted advice maps one-to-one onto levers already built here —
`profit_take_disabled`, `trailing_stop_disabled`/`min_hold_days`, `winner_add_max_count`,
`rank_band_exit_pct`, the turnover cut, and the 15% position cap ("my limit is 15–20%").
That is independent corroboration of the turnover finding from a completely different
direction.

**What does NOT transfer:** the single most common theme — "know the company inside out"
(19 comments) — is exactly what a news/graph signal cannot do, and concentration is
actively dangerous with nexus's negative measured selection alpha.

---

## 11. LIVE STATE AT HANDOFF

| instance | runCommand | initial_value | note |
|---|---|---|---|
| `alpaca-main` | **False** | **None** ⚠️ | REAL MONEY. Refuses to launch until `initial_value` is set |
| `main` | False | 6434.48 | retired Robinhood, do not start |
| `test` | True | 10000 | crypto soak, Alpaca paper `PA3TRLBV6ZNZ`, running clean |
| `alpaca-paper-pit` | True | 6000 | PIT capture, doc-182, Alpaca paper `PA3IBY5S84PG` |

- **doc-179 (real money) UNTOUCHED** — no `core_*`, no `min_hold_*`, no `rank_band_*`.
  ⚠️ It DOES have `residual_sleeve_enabled=True` with `core_sleeve_enabled` **absent** —
  the churn-heavy pairing. Change before restarting `alpaca-main`.
- Test docs created: **182** (PIT capture), **183** (v2 core-only), **184**
  (turnover-cut), **185** (control). Instances: `v2-backtest`, `v2-turnover-cut`,
  `v2-turnover-ctl` — all stopped, used only as backtest config holders.
- **PIT manifests: 0.** One was written and I DELETED it — all four dataset payloads were
  empty (`graph` 0 bytes) because the Neo4j fix had not deployed. A bundle certifying an
  empty graph as point-in-time is worse than no bundle: a future backtest would resolve
  it and run on nothing while carrying a "verified PIT" label.
- Suite: **4324 passed**, 19 failures — all in the three UNTRACKED red-by-design files
  (`test_adv_exit_discipline_findings.py` 11, `test_core_sleeve_adversarial.py` 7,
  `test_zz_adversarial_sweep.py` 1). They document open findings and are red on purpose.

---

## 12. PROCESS LESSONS PAID FOR

1. **Everything I got right came from RUNNING the system. Everything I got wrong came
   from reasoning about it and stopping there.** A 501-session price simulation caught a
   v2 bug that 19 green unit tests missed; the real engine then caught two more the
   simulation missed.
2. **State the pass condition BEFORE seeing the number**, and name what the measurement
   structurally cannot show. Treat a metric that confirms a hoped-for story as suspect.
3. **Never push while a backtest is in flight** — the redeploy kills it. Done twice more
   this session.
4. **Never judge a partial run.** **Never compare across builds** — `code_version` exists
   for exactly this.
5. **Fixing a dormant bug can break a working system.** The crypto soak had run since
   2026-07-27 on a container that predated the launch dispatch; repairing the launch path
   meant a restart, and the restart walked into guards first boot never touched. Two of
   the nine fixes were regressions I introduced this way.
6. **Agent output is not evidence until checked.** One agent reported `max_positions=50`
   ("cut to 15 — the largest non-sleeve reduction") when the live value is **14**, plus
   four other wrong config values. Its headline recommendation was void; its trade-data
   attribution was sound.
