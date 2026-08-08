# Discovery & ranking — is the ranking any good, and does the book buy what it likes?

Read-only investigation. No code changed, no backtest started, nothing pushed.
Runs read: **bt 820236** (+12.33%) and **bt 613166** (+9.17%), both
`v2-let-run-core`, 2026-01-01→2026-03-01, 3600s, $6,000, `pit_mode=research`.
Logs pulled with `python3 scripts/pull_backtest_logs.py <id> --stdout`
(38,155 and 38,588 lines).

---

## TL;DR

1. **The 60-day momentum component is a real signal.** Pooled over both runs,
   n=362 discovered names, Spearman IC(60d, forward return) = **+0.201, p=0.00012**.
2. **The 20-day component is an anti-signal.** Pooled IC = **−0.127, p=0.016**,
   and negative in **8 of 8** slices measured.
3. **The ranking the discovery lane actually sorts on is `max(20d, 60d)`
   (`graph_nexus_analysis.py:13920`). Its pooled IC is −0.003, p=0.96 — zero.**
   The good signal and the bad signal are combined with `max()`, and the `max()`
   destroys the edge. IC(60d) > IC(max-key) at **8 of 8** run×horizon
   comparisons (p = 1/256 under a coin flip).
4. **The book does not buy what the ranking likes.** In bt 820236, 59 names
   reached the final `Executable buys:` slate with $72,780 of conviction sizing;
   **7 ever filled**. 52 names / **$63,357 of sizing produced no trade**.
5. **The single most-liked name got 2.5% of the traded notional.** SNDK was
   momentum-watchlist rank-1 on 20 of 43 bars and top-3 on 36 of 43. It received
   **$490.83** of buy fills. SPY — the index core, which carries no ranking
   signal — received **$13,585, 68% of all traded notional**.
6. **The "VICR refused / WDC+LRCX bought" story is cherry-picked.** In the same
   run the names bought were *significantly stronger* on 60d than the names
   refused (+63.6% vs +22.2%, permutation p=0.018). VICR is a genuine miss
   (#4 of 184 by 60d) but the run also bought SNDK (#6) and CORD (#7).
7. **The `+0.17 IC @1d (t=3.1)` claim cannot be reproduced from this tree.**
   There is no script, notebook, dataset or artifact anywhere in the repo that
   computes it (details in §1).

---

## 1. The `+0.17 @1d (t=3.1)` claim — how it was measured

### Provenance

Three files state it, all authored in the same session:

- `docs/OBJECTIVE.txt:80-82` — *"'The ranking score is noise': false. Measured IC
  is +0.17 at 1d, t=3.1. The earlier zero came from a range-restricted sample."*
- `docs/handoffs/2026-08-07-satellite-clamp-and-conviction-overflow.md:148-152` —
  *"Measured on the full scored set with symbol-clustered bootstrap CIs the IC is
  positive: +0.170 @1d (t=3.06)."*
- `docs/superpowers/specs/2026-08-07-bt804832-three-layer-remediation-design.md:170` —
  *"+0.170 @1d (t=3.06), +0.226 @10d (t=3.18). The ≈0 figure came from joining
  scores to `backtest_prices.json`, which holds only the 11 traded names."*

`git log --all -S "0.170"` returns exactly two commits, both docs-only
(`4424d34`, `4ccadab`). Both attribute the number to **bt 804832**, not to
820236 or 613166.

### The measurement itself is not in the repository

- `grep -rn "spearmanr\|pearsonr\|kendalltau\|np.corrcoef\|\.corr("` across every
  `*.py` in the tree returns **zero hits**.
- `scipy` is not in any `requirements*.txt`.
- No file named `backtest_prices.json` exists anywhere in the tree (the referenced
  input). `backend/backtest_prices.csv` exists but is 182 rows of SPY hourly closes
  from a *different* window (2026-03-30…), last written by commit `d5dedfd`.
- `git log --all --diff-filter=AD` finds no deleted analysis script.
- "symbol-clustered bootstrap" appears only in the two prose documents.

**Verdict.** The claim is a prose assertion with no reproducible artifact. It may
well be correct, but nothing in this repository lets anyone re-run it, and it was
measured on a *different run* (804832) than the two under review here.

### The range-restriction complaint is factually right about the data

The result row's `stock_price_change` really does hold only traded names
(`backend/backtest_summary.py:604-625`, `compute_stock_price_change(all_traded, …)`).
For these runs that is **8 names** (bt 820236) and **10 names** (bt 613166) —
against 184 and 182 momentum-discovered names. Correlating a score against that
set is range-restricted on both variables, exactly as the spec says.

### The literal test the task asked for (and why it cannot answer anything)

`stock_price_change` on the result row, joined to the run's own momentum ranks:

**bt 820236** — 8 traded names, only 4 carry a momentum rank:

| sym | 20d | 60d | change_percent | pnl_per_stock |
|---|---|---|---|---|
| SNDK | +15.6 | +95.9 | **+166.10%** | $100.95 |
| WDC | +7.7 | +37.5 | +61.91% | $450.49 |
| LRCX | +15.8 | +31.9 | +36.71% | $238.22 |
| CPER | – | – | +5.58% | $55.69 |
| BA | – | – | +4.80% | $2.26 |
| SPY | – | – | +0.64% | $8.76 |
| OMER | – | – | −29.83% | −$60.99 |
| CORD | −14.9 | +89.2 | −55.70% | −$59.43 |

`spearman(60d, change_percent) = +0.400, p=0.60, n=4`.

**bt 613166** — 10 traded names, **1** carries a momentum rank (AGMI). n=1.

**Neither supports any conclusion.** This is the range restriction, reproduced.
Everything below therefore uses the full discovered set with prices recovered
from the run's own broker decision lines.

### Method used instead

Every log line of the form
`[BROKER] <SYM> @ <date> <time> ($<px>): <action> action_intent=…`
is a price the simulator itself marked that symbol at on that bar. Harvesting
them gives a daily price series for **308 symbols** (bt 820236) and **272**
(bt 613166) — 121/122 of them covering the whole window. That is the run's own
price data, not an external source.

**Caveats, stated plainly:**
- Coverage is not uniform. A name drops out of the decision loop when discovery
  prunes it, so exit dates range 2026-01-20…2026-02-27 (median 24 days held in
  view). Every headline below is therefore repeated on (a) the full set,
  (b) only names priced through to 2026-02-27, and (c) fixed 10d/20d horizons.
- 5 of 19 (bt 820236) and **14 of 27 (bt 613166)** momentum-watchlist top-3 names
  have **no price line at all** — see §3. They are absent from every return
  statistic, and their absence is itself a finding, not a nuisance.
- The per-bar IC t-stats in §2.3 are inflated: the ranking vector is static per
  name, so bar-to-bar ICs are strongly autocorrelated. Read the **signs and the
  ordering**, not the t-values.

---

## 2. Is the ranking any good?

### 2.1 The sort key is the problem, not the signal

`backend/strategies/graph_nexus_analysis.py:13920`:

```python
candidates.sort(key=lambda x: (-max(x[1], x[2]), -x[1], -x[2], x[0]))
#                                    ^ r20   ^ r60
```

Pooled across both runs (n=362 distinct discovered names, forward return from
first discovery to last in-run price):

| ranking variable | Spearman IC | p |
|---|---|---|
| `60d` momentum | **+0.201** | **0.000115** |
| `20d` momentum | **−0.127** | 0.0156 |
| `max(20d, 60d)` — **the key actually used** | **−0.003** | 0.956 |

Per run and per subset:

| run | subset | n | IC(60d) | IC(20d) | IC(max-key) |
|---|---|---|---|---|---|
| 820236 | all | 183 | +0.224 (p=.002) | −0.128 | +0.061 (p=.41) |
| 820236 | priced through 02-27 | 108 | +0.238 (p=.013) | −0.064 | +0.246 |
| 613166 | all | 179 | +0.152 (p=.042) | −0.136 | **−0.154 (p=.040)** |
| 613166 | priced through 02-27 | 115 | −0.028 (p=.77) | −0.099 | −0.176 |
| 820236 | fixed 10d | 172 | +0.143 | −0.004 | +0.058 |
| 820236 | fixed 20d | 146 | +0.116 | −0.079 | −0.056 |
| 613166 | fixed 10d | 170 | +0.080 | −0.050 | −0.150 |
| 613166 | fixed 20d | 152 | +0.214 (p=.008) | −0.122 | −0.087 |

**IC(20d) is negative in 8/8 slices. IC(60d) is positive in 7/8. IC(max-key) is
never significantly positive and is significantly *negative* in one.**

Mechanically: the key takes 20d whenever 20d > 60d, which is **39%** of names in
bt 820236 and **55%** in bt 613166. Those names returned:

| run | key came from 20d | key came from 60d | Mann-Whitney p |
|---|---|---|---|
| 820236 | +5.03% (n=72) | +11.07% (n=111) | 0.0038 |
| 613166 | −2.28% (n=99) | +2.12% (n=80) | 0.139 |

### 2.2 What a 60d-only key would have picked instead

Same discovered universe, same forward returns, equal weight:

| run | top-K | current key `max(20,60)` | `60d` only | overlap |
|---|---|---|---|---|
| 820236 | 12 (the daily cap, `mom:12`) | **+19.02%** | **+24.19%** | 10/12 |
| 820236 | 20 | +18.31% | +22.20% | 16/20 |
| 820236 | 30 | +19.15% | +19.26% | 23/30 |
| 613166 | 12 | **−0.16%** | **+6.44%** | 10/12 |
| 613166 | 20 | +2.36% | +4.23% | 16/20 |
| 613166 | 30 | −1.82% | +0.51% | 24/30 |

**60d-only wins 6/6.** Only 2 of the 12 daily names change; the gain comes from
which 2.

### 2.3 Horizon structure — the edge is not at 1 day

Per-bar cross-sectional Spearman IC, averaged over bars (t-stats inflated, see
caveats):

| horizon | IC(60d) 820236 | IC(60d) 613166 | IC(max-key) 820236 | IC(max-key) 613166 |
|---|---|---|---|---|
| 1 bar | +0.030 (t=1.16) | +0.043 (t=1.86) | −0.013 | −0.003 |
| 5 bars | +0.053 (t=2.05) | +0.133 (t=5.50) | −0.023 | +0.029 |
| 10 bars | +0.103 (t=3.43) | +0.192 (t=8.37) | +0.011 | +0.019 |
| 20 bars | +0.289 (t=7.77) | +0.145 (t=3.01) | +0.181 | +0.015 |

IC(60d) exceeds IC(max-key) at **8 of 8** run×horizon cells.

There is **no measurable 1-day edge in the momentum ranking** in either run
(+0.030 / +0.043, t=1.16 / 1.86). That does not refute the OBJECTIVE's +0.17@1d
— that claim is about the news/graph score on a different run — but the log emits
no per-bar cross-sectional score vector for the news/graph blend, so **that claim
cannot be re-measured from bt 820236 or bt 613166 at all.**

### 2.4 Quintile monotonicity — good in one run, not the other

Forward return by 60d quintile (5 = strongest):

| quintile | 820236 mean | 613166 mean |
|---|---|---|
| 1 (weakest 60d) | +3.82% | **−10.35%** |
| 2 | +3.81% | +1.58% |
| 3 | +8.55% | +3.14% |
| 4 | +8.49% | +2.71% |
| 5 (strongest 60d) | **+18.64%** | +1.43% |

bt 820236 is cleanly monotone. **bt 613166 is not** — there the signal is
"avoid the bottom quintile", not "buy the top". The two runs discovered very
different universes despite identical window/config; bt 613166 was run after
clearing `GraphNexusActiveEvents` (`_RUNS.md`), and it shows: **39 of 182 (22%)**
of its discovered names carry a **60d of exactly `+0.0%`** versus 6 of 184 (3%)
in bt 820236.

That `0.0` is fabricated, not missing —
`graph_nexus_analysis.py:13898-13899`:

```python
r20 = ((latest - closes[-21]) / closes[-21] * 100.0) if len(closes) > 21 and closes[-21] > 0 else 0.0
r60 = ((latest - closes[-61]) / closes[-61] * 100.0) if len(closes) > 61 and closes[-61] > 0 else 0.0
```

A name with fewer than 62 closes gets `r60 = 0.0` and is then ranked against
names with a real 60-day return. Excluding those 39 names, bt 613166's IC(60d)
recovers to +0.198 (t=2.37, n=140).

### 2.5 The very top of the momentum ranking is junk

Top-3 by 60d at first discovery, and what they did:

- bt 820236: MSTZ (−11.5%), DZZ (−7.8%), TE (−5.0%). **Top-3 basket = −8.09%.**
  Top-8 = +31.80%.
- bt 613166: TE (−27.6%), SKYT (−4.9%), TYGO (+14.1%). **Top-3 = −6.14%.**

MSTZ is a −2× MSTR ETF, DZZ a double-short gold ETN. The
`Momentum ETF exclusion` filter fired on all 43 bars
(`dropped 5 leveraged/inverse/commodity ETF candidate(s)`) and the
`Momentum ceiling block` fired 9 times (`MSTZ 20d=+33.7% 60d=+312.1%
(caps 20d=80%, 60d=200%)`) — **and MSTZ still entered the discovered set at
60d=+199.0% once its 60d decayed under the 200% cap.**

### 2.6 The momentum-watchlist rank *does* order correctly

`Momentum watchlist: … top3=[…]` (43 lines per run). Forward return by rank
position (10-day horizon, price-covered observations only):

| rank | 820236 mean | 613166 mean |
|---|---|---|
| 1 | **+6.40%** (n=31) | **+8.56%** (n=8) |
| 2 | −3.64% (n=26) | +2.44% (n=14) |
| 3 | −1.20% (n=27) | −4.39% (n=7) |

`spearman(rank, fwd10) = −0.207 (p=0.059)` and `−0.351 (p=0.062)`. Negative rho
is the correct sign. Marginal individually; consistent across both runs.

**So the ranking is not noise. The 60d axis works, the watchlist top-1 works, and
the sort key that decides who enters the funnel throws the edge away.**

---

## 3. Does the book buy what the ranking likes?

### 3.1 The funnel, counted

| stage | bt 820236 | bt 613166 |
|---|---|---|
| momentum-discovered names | 184 | 182 |
| distinct names ever in the watchlist **top-3** | 19 | 27 |
| … of those, **never priced** in the broker decision loop | 5 (26%) | **14 (52%)** |
| … of those, ever **bought** | **2** | **4** |
| distinct names reaching `Executable buys:` (final slate) | **59** | **59** |
| … ever **filled** | **7** | **9** |
| conviction sizing issued by `V31.2 total-spend cap` | **$72,780** | **$72,692** |
| … sizing on names **never bought** | **$63,357 (87%)** | **$53,606 (74%)** |
| `FILL BUY` lines total / non-SPY | 19 / **10** | 21 / **12** |

A $6,000 account issued $72,780 of conviction allocations and executed 10 non-SPY
buys.

### 3.2 Where the money actually went

`[execution] FILL …`, bt 820236 — 38 fills, $19,946 gross = **332% of initial NAV**:

| sym | buys | sells | gross notional |
|---|---|---|---|
| **SPY** | 9 | 17 | **$13,585 (68%)** |
| CPER | 2 | – | $1,080 |
| WDC | 1 | – | $840 |
| LRCX | 1 | – | $840 |
| OMER | 1 | 1 | $1,598 |
| CORD | 1 | 1 | $1,407 |
| **SNDK** | 3 | – | **$491** |
| BA | 1 | – | $107 |

bt 613166 is the same shape: SPY 22 fills, $13,802 = 66% of gross.

**SNDK — rank-1 on 20 of 43 bars, top-3 on 36 of 43, `raw=+1.700`, the name the
whole objective is about — received 2.5% of the traded notional. The index core,
which is not ranked at all, received 68%.**

### 3.3 The exact refusal chain for SNDK (bt 820236)

```
13:02:12  Entry extension gate: SNDK recent runup +28.5% > 25% — buy blocked        [sim 2026-01-01, $237.33]
13:02:51  Rank band (entry<=#14, exit>#69 of 137): blocked 54 buy(s) [RGEN, SNDK, …]
13:03:47  Entry extension gate: SNDK recent runup +28.5% > 25% — buy blocked
13:11:21  V31.2 total-spend cap [CONCENTRATE]: funded 3 of 4 by conviction (SNDK@$873, VICR@$873, TER@$873)
13:11:21  Executable buys: SNDK, TER, VICR
13:11:21  SATELLITE CAP: SNDK trimmed $873 -> $591 to keep the core at target
13:11:21  TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 — admitting a conviction buy through a 105% budget
13:11:21  Buy gate inputs for SNDK: … open_pos=5 … cash_to_use=$591.39 → PASS
13:11:21  MAX_POSITIONS_GATE: blocked SNDK (held=6, cap=6)                          [sim 2026-01-12, $388.46]
13:12:36  … same, trimmed $881 -> $579, PASS, MAX_POSITIONS_GATE blocked            [01-13, $390.49]
13:17:47  … same, trimmed $883 -> $564, PASS, MAX_POSITIONS_GATE blocked            [01-16, $405.47]
13:19:02  … trimmed $884 -> $562, PASS → FILL BUY SNDK qty=0.2878 price=$443.83     [01-20]
13:34:53  FILL BUY SNDK qty=0.4827 price=$517.69                                    [01-29]
14:06:45  SATELLITE CAP: SNDK trimmed $228 -> $114 → FILL BUY qty=0.1665 @$679.70   [02-23]
```

Note `open_pos=5` and `held=6, cap=6` on the **same line pair**. Across the run
`max_positions gate armed: held=6, cap=6` fires **599 of 634 times** — the gate
is saturated essentially always, and one of the 6 slots is SPY.

**The dollars.** Actual SNDK: qty 0.9371, cost $490.83, VWAP **$523.80**, marked
$631.54 → **+$100.95** (matches `pnl_per_stock.SNDK = 100.9476`).

| counterfactual | qty | value @ $631.54 | gain |
|---|---|---|---|
| the $591.39 the buy gate PASSED on 01-12 @ $388.46 | 1.5224 | $961 | **+$370** |
| the $578.64 on 01-13 @ $390.49 | 1.4818 | $936 | +$357 |
| the full $873 conviction size on 01-12 | 2.2474 | $1,419 | **+$546** |

**The run's entire P&L was $739.61.** Executing on 01-12 the size the broker had
already approved is worth **+$269** (+4.5pp of NAV); the untrimmed conviction size
is worth **+$445** (+7.4pp).

Entry timing, measured: window start $237.33, end mark $631.54. First fill
$443.83 = **52% of the way through the move**; VWAP $523.80 = **73% of the way
through**. That is the OBJECTIVE's blocker #1 (`OBJECTIVE.txt:56-58`) reproduced
with numbers from this run.

### 3.4 Names the ranking put at #1 that the plumbing could not price

`V31.2 total-spend cap … skipped as not executable: X(price unresolved)` fires on
**23 of 43 bars** in bt 820236 (33 skips) and **21 of 43** in bt 613166 (49 skips).

| name | top-3 appearances | rank-1 appearances | best watchlist score | priced? | bought? |
|---|---|---|---|---|---|
| **AXTI** (613166) | 17 | 4 | **1.930** — highest score in the run | **no** | no |
| **IBRX** (both) | 7 / 7 | 7 / 7 | 1.734 | **no** | no |
| **GLUE** (both) | 2 / 7 | 1 / 2 | 1.036 | **no** | no |
| VTVT (613166) | 4 | 3 | 0.690 | no | no |
| SOC, HL, KTOS, ONDS, USAR, ASTS, VAL, IMMX, CNC, ZETA | 1–4 each | – | – | no | no |

AXTI alone accounts for 5 of bt 613166's 49 `price unresolved` skips; GLUE 3;
RVMD 2. **Half of bt 613166's top-3 names never reached the decision loop at
all.** They are not "refused" — they were never buyable.

The current working tree already removes this skip
(`git diff backend/strategies/graph_nexus_analysis.py`, the
`_conc_any_priced` deletion, annotated *"SNDK was the MOST-deferred name in
725146, skipped 4 times as 'price unresolved'"*). **That change is uncommitted and
was not in either run under review.**

### 3.5 The rank band, and its alphabetical tiebreak

`Rank band (entry<=#N, exit>#M of K): blocked B buy(s)` fires on all 43 bars:

- bt 820236: **2,833** blocked buy signals (this exactly reproduces the figure in
  the code comment at `graph_nexus_analysis.py:22986`), mean 65.9/bar, entry band
  mean #18.9 of 184 = top 10.3%.
- bt 613166: **2,522** blocked, mean 58.7/bar, entry band #17.3 of 168.6.

The ranking is `_rotation_effective_score`, sorted
`ranked.sort(key=lambda pair: (-pair[0], pair[1]))` — **ticker string is the
tiebreak** (`graph_nexus_analysis.py:22956`). Because `raw` saturates at ±1 and
the log shows large blocks of identical scores, the cutoff repeatedly lands
*inside* a tie, so the alphabet decides who enters.

Measurable signature: the log prints the 8 highest-ranked blocked names in rank
order. If ties were rare, the head of that list would be alphabetically random
(expected leading increasing run = e−1 = 1.72).

| leading strictly-alphabetical run at the head of the blocked list | bt 820236 | bt 613166 | expected if random |
|---|---|---|---|
| ≥ 4 names | **21 / 43 bars** | **23 / 43** | 1.8 bars |
| ≥ 6 names | 12 / 43 | 11 / 43 | 0.06 bars |
| ≥ 8 names (all shown) | 4 / 43 | 5 / 43 | 0.001 bars |
| mean run length | **3.86** | **3.98** | 1.72 |

Example, bt 820236 13:08:07: `blocked 69 buy(s) [TXN, VOYA, XRN, XRP, A, AAOI,
AAPG, AARD...]` — two alphabetical blocks back to back.

**On roughly half the bars the entry cutoff is decided by ticker spelling, not by
conviction.** Late-alphabet tickers are structurally disadvantaged: VICR, VOYA,
XRN, TXN, UCTT, WDAY, SYNA, TRDA appear in the blocked head over and over.

### 3.6 The staged `rank_band_momentum_exempt` lever fired zero times

`_RUNS.md` credits bt 613166 with `rank_band_momentum_exempt`.
`scripts/run_validation_suite.py:62` sets `rank_band_momentum_exempt_min_score: 0.8`.
The lever's log line is
`Rank band: N momentum name(s) exempt from the entry band` (`:23044-23051`).

**It appears 0 times in bt 613166, while the band blocked 2,522 buys.**
(bt 613166 *did* get the sibling levers — `TURNOVER BYPASS CEILING` fires 44 times
there and 0 in bt 820236 — so the doc was patched.)

Mechanism, from the code (a prediction, flagged as such): the exemption keys on
`sc["momentum_watchlist_score"]` (`:23016`), which is only ever written for
tickers in `_momentum_picks` (`:21520`, `:28622`) — and the same picks get
`raw_net_score = max(score, 1.50) + diff` (`:21505-21513`). A raw of ≥1.50 puts
them near the top of a band whose cut is #19 of 184, so `rank > entry_cut` is
essentially never true for a name that carries the field. **The run measurement
(0 exemptions / 2,522 blocks) is the fact; the mechanism is a code reading and
should be confirmed by instrumenting, not believed.**

### 3.7 Full refusal census

| gate | bt 820236 | bt 613166 |
|---|---|---|
| Rank band blocked buy signals | 2,833 | 2,522 |
| Backfill queue BLOCKED | 327 | 325 |
| TURNOVER BUDGET BINDING lines | 263 | 416 |
| V28 BFQ ALLOC=0 | 193 | 214 |
| Entry extension gate blocks | 95 | 80 |
| SATELLITE CAP events | 65 | 42 |
| MAX_POSITIONS_GATE blocks | 45 | 12 |
| `Promoted buys demoted to queue-only hold` | 43 | 35 |
| `Deferred unfunded buys demoted to hold` | 34 | 31 |
| `(price unresolved)` conviction-slot skips | 33 | 49 |
| price-floor blocked buys | 9 | 23 |
| sector-concentration demotions | 8 | 14 |
| **`FILL BUY` non-SPY** | **10** | **12** |

---

## 4. The VICR / WDC / LRCX claim — representative or cherry-picked?

The claim appears verbatim as a code comment,
`backend/strategies/graph_nexus_analysis.py:22989-22997`:

```
#   VICR  discovered 20d=+20.4%  60d=+119.6%  -> rank-band blocked x6, never bought
#   …
#   WDC   discovered 20d= +7.7%  60d= +37.5%  -> +$450.49
#   LRCX  discovered 20d=+15.8%  60d= +31.9%  -> +$238.22
```

**Every individual fact in it checks out against bt 820236.** VICR was rank-band
blocked on 6 bars (13:04:35, 13:05:41, 13:06:48, 13:09:29, 13:12:34, 13:16:38),
plus entry-extension blocked (`+48.8% > 25%`), plus `SATELLITE CAP: VICR skipped
— satellite at its design share ($-32 room)`, plus
`[core] funding pre-pass: max_positions will refuse 1 of 3 sized buy(s) (VICR)`,
plus `Sector concentration detail: technology: kept SNDK, MU, NXPI, ON, SYNA,
TXN, STX, AMAT | demoted VICR, TNC`, plus 6× `BFQ ALLOC=0`. It reached
`Executable buys: SNDK, TER, VICR` at $873 on 2026-01-12 and never filled.
VICR's forward return: **+81.4%** from 01-01, **+47.6%** from the bar it was
funded on.

**But the comparison it draws is cherry-picked.** Ranked by 60d across all 184
momentum-discovered names in bt 820236:

| rank by 60d | sym | 20d | 60d | forward return | bought? |
|---|---|---|---|---|---|
| 1 | MSTZ | +19.1 | +199.0 | −11.5% | no |
| 2 | DZZ | +4.8 | +141.6 | −7.8% | no |
| 3 | TE | +40.7 | +138.8 | −5.0% | no |
| **4** | **VICR** | +20.4 | +119.6 | **+81.4%** | **no** |
| 5 | CRCD | −20.6 | +108.6 | +33.1% | no |
| **6** | **SNDK** | +15.6 | +95.9 | **+170.2%** | **yes** |
| **7** | **CORD** | −14.9 | +89.2 | **−49.1%** | **yes** |
| … | | | | | |
| **23** | **WDC** | +7.7 | +37.5 | +61.5% | **yes** |
| **39** | **LRCX** | +15.8 | +31.9 | +21.9% | **yes** |

The comment names the run's #23 and #39 buys and omits its #6 and #7. Aggregated:

| bt 820236, momentum-discovered universe | 60d at discovery | 20d at discovery |
|---|---|---|
| **bought** (n=4: SNDK, CORD, LRCX, WDC) | **+63.6% mean** / +63.4% median | +6.0% mean |
| **refused** (n=180) | **+22.2% mean** / +20.0% median | +16.1% mean |
| 20,000-draw permutation p | **p(bought > refused) = 0.018** | p(bought < refused) = 0.054 |

**The names bought were significantly *stronger* on 60d than the names refused,
and *weaker* on 20d — which, given IC(20d) = −0.127, is the right direction on
both axes.** bt 613166 has n=1 bought momentum name (AGMI) and can support
nothing (p=0.33 / 0.09).

The same holds one stage further down the funnel, on the conviction-funded slate
(forward return from each name's first funded bar to its last in-run price):

| | bt 820236 | bt 613166 |
|---|---|---|
| **FILLED** basket, equal weight | **+19.28%** (n=6) | +3.59% (n=8) |
| **REFUSED** basket, equal weight | **+4.64%** (n=24) | +2.20% (n=20) |
| best refused | RIG +55.1%, **VICR +47.6%**, TER +39.1% | SBLK +29.7%, AMD +21.6% |
| worst refused | SHLS −34.8%, CRCD −30.2%, AMD −22.8% | ETH −35.3%, RVLV −19.6% |
| forgone P&L if every refused name filled at its own sized alloc | +$936 | +$365 |

And on the watchlist top-3 basket:

| | bt 820236 | bt 613166 |
|---|---|---|
| top-3 names **bought** | +31.67% (n=2) | +6.64% (n=4) |
| top-3 names **never bought** | +2.50% (n=11) | +3.96% (n=6) |

**Verdict.** VICR is a real, expensive, individually-provable miss. It is **not**
representative: at every level of aggregation I can measure, in both runs, the
book bought *better* than it refused. The failure is not "the gates select the
wrong names" — it is **volume**: 59 executable names → 7 fills, $72,780 of
conviction sizing → $19,946 of gross of which 68% is SPY, and the one name the
ranking loved most got $491.

---

## 5. Things I could not prove

- **I cannot reproduce or refute `+0.17 IC @1d, t=3.1`.** No measurement code, no
  input data, no artifact; and neither log emits the per-bar cross-sectional
  news/graph score vector that would be needed.
- **I cannot prove `rank_band_momentum_exempt_min_score` was set to 0.8 in
  bt 613166.** The doc-193 on-disk backups are pre-patch snapshots and none of
  them contain the key. What I can prove is that the lever's log line never
  appeared while 2,522 buys were band-blocked, and that the sibling levers
  (`TURNOVER BYPASS CEILING`) did fire in that run.
- **I cannot cleanly separate the two runs' differences.** bt 613166 ran on cold
  `GraphNexusActiveEvents` *and* with three new levers *and* on a later build.
  They are not a paired A/B and I have treated them as two independent
  observations, not an experiment.
- **Forward returns are truncated for names that leave the decision loop.** I have
  repeated every headline on full-coverage and fixed-horizon subsets; results are
  reported above including the one that flips (bt 613166 IC(60d) on the
  full-coverage subset: −0.028).
- **The `Entry extension gate` measurement is inconclusive and I am NOT proposing
  to touch it.** Its blocked basket in bt 820236 is +16.79% (n=18, +7.76%
  ex-SNDK); in bt 613166 it is −11.49% (n=7). `OBJECTIVE.txt:74` records −7.95%
  and lists loosening it as DO-NOT-RETRY. My coverage is 18 of 65 blocked names —
  too thin to overturn a prior measurement.

---

## 6. Ranked list — what to change

### 1. Sort momentum discovery on `60d`, not `max(20d, 60d)` — `graph_nexus_analysis.py:13920`

- **Expected effect:** top-12 daily basket +19.02% → +24.19% (bt 820236) and
  −0.16% → +6.44% (bt 613166). Only 2 of 12 names per day change.
- **Evidence:** pooled IC(60d) = +0.201 (p=0.00012) vs IC(max-key) = −0.003
  (p=0.96), n=362. IC(20d) = −0.127, negative in 8/8 slices. IC(60d) > IC(max-key)
  at 8/8 run×horizon cells. 60d-only beats the current key at 6/6 (run × K∈{12,20,30}).
- **Cost if wrong:** one config-scale change to a sort key; no gate behaviour moves.

### 2. Make the missing 60-day return *missing*, not `0.0` — `graph_nexus_analysis.py:13899`

- **Expected effect:** removes 39 of 182 (22%) fabricated ranks from bt 613166's
  universe. Doing so lifts that run's IC(60d) from +0.152 to +0.198 (t=2.37).
- **Evidence:** `r60 = … else 0.0` on a `len(closes) > 61` test; 39 names in
  bt 613166 and 6 in bt 820236 carry `60d=+0.0%` exactly, and they mean
  "<62 closes", not "flat". This is a precondition for #1 — sorting on 60d while
  short-history names are pinned at 0.0 mostly just demotes them, which is
  accidental rather than intended.

### 3. Break the alphabetical tiebreak in the rank band — `graph_nexus_analysis.py:22956`

- **Expected effect:** stops ticker spelling deciding the entry cut on ~half the
  bars. Does not change how many names are admitted, only which.
- **Evidence:** leading strictly-alphabetical run at the head of the blocked list
  is ≥4 on 21/43 bars (bt 820236) and 23/43 (bt 613166) against a random
  expectation of 1.8; ≥8 on 4 and 5 bars against ~0.001. Mean run 3.86 / 3.98 vs
  1.72. VICR/VOYA/XRN/TXN/UCTT/WDAY recur in the blocked head.
- **Caveat:** this reallocates refusals, it does not reduce them. Expect it to move
  *which* names are missed, not the fill count. Pair with #4.

### 4. Free the index-core slot in `MAX_POSITIONS_GATE`, and stop the satellite cap trimming a conviction buy the gate then refuses

- **Expected effect, measured on bt 820236's own numbers:** executing the $591.39
  the buy gate already PASSED for SNDK on 2026-01-12 is **+$269** vs the actual
  SNDK result; the untrimmed $873 conviction size is **+$445**. Run total P&L was
  $739.61, so +4.5pp to +7.4pp of NAV from one name on one bar.
- **Evidence:** `Buy gate inputs for SNDK: … open_pos=5 … cash_to_use=$591.39 →
  PASS` immediately followed by `MAX_POSITIONS_GATE: blocked SNDK (held=6, cap=6)`
  on 4 consecutive bars (13:11:21, 13:12:36, 13:17:47, and again at 13:19:02
  before the first partial fill). `max_positions gate armed: held=6, cap=6` on
  599 of 634 armings. SNDK VWAP $523.80 = 73% of the way through a
  $237.33→$631.54 move.
- **Note:** `max_positions_exclude_sleeve_legs` was staged for bt 613166 and
  MAX_POSITIONS_GATE blocks did fall 45 → 12, but the gate still blocked SNDK and
  WDC there, and SNDK's notional fell to $126.93. Partially effective at best.

### 5. Ship the uncommitted `price unresolved` fix, and add a pre-allocation price resolve for watchlist top-N

- **Expected effect:** restores buyability to the names the ranking scores
  highest. AXTI (score 1.930, the highest in bt 613166, rank-1 on 4 bars) and
  IBRX (1.734, rank-1 on 7 bars in *both* runs) were never priced and therefore
  never buyable.
- **Evidence:** 33 and 49 `(price unresolved)` conviction-slot skips, on 23/43 and
  21/43 bars. **14 of bt 613166's 27 top-3 names (52%) have no price line
  anywhere in the log.** The tree already contains the un-committed narrowing
  (`git diff graph_nexus_analysis.py`), annotated with bt 725146's SNDK case — but
  bt 725146 went NEGATIVE, so **this must be validated, not assumed.**

### 6. Fix or delete `rank_band_momentum_exempt` — it is currently inert

- **Expected effect:** unknown; it produced literally nothing.
- **Evidence:** 0 `Rank band: N momentum name(s) exempt` lines in bt 613166
  against 2,522 band-blocked buys, in a run whose sibling levers demonstrably
  fired (44 × `TURNOVER BYPASS CEILING`). Do not count this lever as part of
  bt 613166's +9.17% and do not re-stage it as-is.

### Not recommended, on this evidence

- **Loosening the entry-extension gate.** My bt 820236 measurement (+16.79%
  blocked basket) points the other way from `OBJECTIVE.txt:74` (−7.95%), but with
  price coverage on only 18 of 65 blocked names and the opposite sign in
  bt 613166 (−11.49%, n=7), this is not evidence.
- **Any conclusion from the top-3-by-60d.** Those are −8.09% and −6.14%
  (MSTZ a −2× ETF, DZZ a double-short gold ETN). Sorting on 60d (#1) must keep
  the existing `_filter_momentum_etf_candidates` and ceiling caps, and both
  already leak: MSTZ entered at 60d=+199.0% after being ceiling-blocked at +312.1%.

### Validation this owes before anything ships

Per `OBJECTIVE.txt:87-99`: 3+ windows, ≥1 OOS, ≥1 non-semi-led, distinct
`history_scope_salt` per arm, equal warmth, same build. bt 820236 and bt 613166
are **not** a paired A/B — they differ in event-state warmth, three levers and the
build. Changes #1 and #2 are the cheapest to test because they alter only which
12 names/day enter the funnel and touch no gate.
