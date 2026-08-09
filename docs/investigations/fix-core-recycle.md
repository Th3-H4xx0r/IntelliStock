# fix-core-recycle — a core funding release is a reserved credit, not spendable cash

Scope: `backend/core_sleeve.py` + `backend/tests/` ONLY. No backtest started or stopped, nothing
pushed, `docs/OBJECTIVE.txt` untouched, `graph_nexus_analysis.py` / `broker.py` not edited.
Builds on `sweep2.md` §2c/§2d, `gap-capital.md` §5, `_SYNTHESIS.md` #1. Not redone here.

Logs read (read-only, on disk): `backtests/427197_sweep2.log` (32,105 lines),
`backtests/915207.log` (41,184), `backtests/383778.log` (19,643), `backtests/542754_sweep.log`
(18,265).

---

## 0. WHAT SHIPPED

| | |
|---|---|
| config key | **`core_funding_release_reserve_decisions`** (int, **0 = OFF = absent = today**) |
| file | `backend/core_sleeve.py` — one new branch in `core_rebalance_order`'s deploy leg, one arm on the `funding` leg, one module-level ledger |
| new no-op reason | `funding_release_reserved` |
| log signature to grep next run | `[core] hold (deploy) — funding_release_reserved: core X% vs target Y% of NAV` |
| tests | `backend/tests/test_core_funding_release_reserve.py` (17 tests, 16 of them fail or cannot import without the fix) |
| recommended value | **4** (see §4 — the ladder is measured, not guessed) |

**The rule.** A `funding` release is capital the allocator has already claimed for a named
satellite buy. While that claim is outstanding, `band_deploy` may not count those dollars as
spendable. The claim is capped by the cash that actually exists (so it evaporates for free the
moment the satellite spends it) and expires after N core deploy decisions it actually refuses
(so cash the satellite declines still comes home to the index).

**Default-off, proved rather than asserted.** 200,000 randomised `core_rebalance_order` states
(NAV 0/100/6k/6.1k/250k x 4 target x 4 floor x 5 regimes x 4 dwell x 5 cadence x circuit x
turnover) were run against `git show HEAD:backend/core_sleeve.py` and against the patched module
with the key absent: **0 divergences in notional, reason, target_weight or current_weight, and
the ledger stayed `{"usd": 0.0, "decisions": 0}` — no state is even written.** A 4,000-state
version of that sweep is pinned in the test file
(`test_key_absent_is_byte_identical_across_a_randomised_state_sweep`).

---

## 1. THE DEFECT, RE-DERIVED FROM THE LOGS — and it is WORSE than sweep2 says

`sweep2.md` reports the release->rebuy as a cash-race. It is that, but the logs show something
sharper: **on the re-buy bar the starved conviction names had already PASSED the buy gate at
full size.** This is not "the gate could not see the cash". The gate saw it, approved it, and
the core's SPY order took it on the same tick.

### 427197, bar 2026-01-06 (release decided 01-05, filled 01-05 16:00)

    L4102  [core] released 2.4401 SPY @ 683.33 (core rebalance: funding (39.3% -> 37.3% of NAV))
    L4359  [execution] FILL SELL SPY qty=2.44012361 price=686.743763 quote=2026-01-05 16:00  = $1,675.74
    L4876  Buy budget: spendable=$1557 (cash=$1677, sells=$0, floor=$120, ramp=100%)
    L5044  Buy gate inputs for BALL: cash=$1677.40 ... cash_per_trade=$866.19 available=$1677.40
           cash_to_use=$866.19 -> PASS
    L5054  Buy gate inputs for CCK:  cash=$1677.40 ... cash_to_use=$866.19 -> PASS
    L4582  [core] bought $1546.03 SPY @ 687.73 (band_deploy: 12.1% -> 37.6% of NAV)
    L5293  [execution] FILL BUY SPY  qty=2.23835604 price=690.678055 quote=2026-01-06 16:00 = $1,545.98
    L5294  [execution] FILL BUY BALL qty=0.85841423 price=55.436661  quote=2026-01-06 16:00 = $47.59

**Two conviction names PASSED at $866.19 each. BALL filled $47.59 (5.5%). CCK filled $0. SPY
filled $1,545.98.**

### 427197, bar 2026-02-05 (release decided 02-04)

    L25358 Buy gate inputs for AMZN: cash=$133.10 ... cash_to_use=$133.10 -> PASS
    L25359 SKIP BUY AMZN — cash_to_use $133.10 < min $393 (allocated $586.87)   [same for C, MRK, WDC]
    L25605 [execution] FILL SELL SPY = $452.18   quote=2026-02-04 16:00
    L25864 [core] bought $453.88 SPY @ 686.10 (band_deploy: 12.1% -> 19.2% of NAV)
    L26284 Buy gate inputs for LLY: cash=$585.29 ... cash_per_trade=$610.43 cash_to_use=$585.29 -> PASS
    L26515 [execution] FILL BUY SPY = $448.02    quote=2026-02-05 16:00
    L26517 [execution] FILL BUY LLY qty=0.12715701 @ $1,024.98 = $130.33

### 915207, bar 2026-01-06 (release decided 01-05) — different commit, different universe

    L4416  [execution] FILL SELL SPY = $761.52   quote=2026-01-05 16:00
    L4639  [core] bought $636.59 SPY @ 687.73 (band_deploy: 26.9% -> 37.3% of NAV)
    L5103  Buy gate inputs for AAL:  cash=$763.24 ... cash_to_use=$761.47 -> PASS
    L5109  Buy gate inputs for AMCR: cash=$763.24 ... cash_to_use=$761.47 -> PASS
    L5344  [execution] FILL BUY SPY qty=0.92165644 = $636.57  quote=2026-01-06 16:00
    L5345  [execution] FILL BUY AAL qty=5.52844505 = $88.57   quote=2026-01-06 16:00

### The three bars, totalled

| run | bar | core `band_deploy` | conviction names that PASSED the gate | approved $ | filled $ |
|---|---|---:|---|---:|---:|
| 427197 | 01-06 | **$1,546.03** | BALL, CCK | $1,732.38 | $47.59 |
| 427197 | 02-05 | **$453.88** | LLY | $610.43 | $130.33 |
| 915207 | 01-06 | **$636.59** | AAL, AMCR | $1,522.94 | $88.57 |
| | | **$2,636.50** | **5 names** | **$3,865.75** | **$266.49 (6.9%)** |

`$2,636.50` of index re-buy against `$3,865.75` of gate-APPROVED conviction notional that
converted at 6.9%. That is the number this key exists to move.

### The forced arithmetic
`core_sleeve.py` deploy leg, unchanged behaviour when the key is absent:

    _spendable = max(0.0, cash - cfg.cash_floor_pct * nav) / (1.0 + CORE_DEPLOY_COST_HAIRCUT)
    buy = min(drift_usd, _spendable)

On 427197 01-06: `drift_usd = (0.376 - 0.121) * 6,103 = $1,556`, `_spendable = $1,537.71` — which
IS the $1,675.74 the core released one bar earlier, minus the 2% floor and the cost haircut.
`buy = $1,537.71`. Reconstructed to the dollar in
`test_key_absent_reproduces_the_427197_rebuy_exactly` and, end to end through broker.py's own
`_residual_sleeve_release`/`_residual_sleeve_deploy`, in `test_wiring_without_the_key_...`:

    released $1,667.31  re-bought $1,537.71  recycled 92.2%     <- HEAD core_sleeve.py, key SET
    released $1,667.31  re-bought $0.00      recycled  0.0%     <- patched, key SET

`band_deploy` has no knowledge that the gap it is closing was opened on purpose to fund a buy
that is still queued. The log line one screen above it literally predicts the outcome
(`releasing core for it would only be bought back`) and releases anyway.

---

## 2. WHY IT LIVES IN `core_sleeve.py` AND WHAT THAT COST

I own `core_sleeve.py` and not `broker.py`, so the credit cannot be threaded through
`_core_sleeve_decide` as a parameter. It is a module-level ledger instead. That is defensible on
its own terms — one core sleeve exists per process (`_core_sleeve_cfg` returns the first enabled
spec), the rule and its state live in the same file, and it is deliberately NOT persisted so a
restart fails toward today's behaviour rather than toward a core that can never re-deploy — but
it is the reason for the awkward unit in §4. **If another agent owns `broker.py`, passing a bar
key into `core_rebalance_order` would let the unit be "bars" instead of "decisions"; I did not
take that edit.**

---

## 3. WHAT THE FIX DOES *NOT* TOUCH (safety, checked against the logs)

* **The opening core build.** `[core] bought $2,400.00 SPY (band_deploy: 0.0% -> 40.0%)` on bar 1
  of every run has no prior release, so no credit is armed and it is untouched.
* **The bear book.** `542754_sweep.log` has **zero** `[core] released ... funding` lines in
  18,265 lines (doc-193 has no bear core profile). No credit can ever be armed there, so the
  key is *provably* inert in the bear window and cannot touch the SQQQ leg.
* **Sells.** The credit is consulted only inside `if drift_usd > 0.0`. `bear_derisk`,
  `band_release` and the `funding` release itself are all reached before it and are untouched —
  pinned by `test_the_credit_never_blocks_a_sell_or_the_bear_derisk`. (The 2026-08-03 sweep's
  MED-HIGH finding was exactly a buy-side condition silently cancelling risk reduction.)
* **Quiet bars.** A bar the core was not going to deploy on (`deploy_below_min`) returns before
  the credit is consulted and burns nothing — otherwise a few quiet bars would silently disarm
  the fix, which is how a lever ships inert.

---

## 4. THE UNIT IS A "DECISION", AND THE LADDER IS MEASURED, NOT GUESSED

broker.py evaluates the core **twice per bar**: `_residual_sleeve_release` at cycle start (which
DISCARDS a positive notional, broker.py:4481) and `_residual_sleeve_deploy` at cycle end. Both
reach the deploy branch. This module is handed no clock that can tell them apart.

That the release path really does return a discarded `band_deploy` is not a theory — it prints:

    [core] hold (release) — band_deploy : 3 lines in 427197, 2 in 915207, 2 in 383778

So protecting the satellite for ONE bar costs **three** refused decisions, because the funding
sell fills before the release bar's own cycle-end deploy:

    bar N   release -> `funding`, arms the credit            (no decision)
    bar N   deploy  -> refused #1
    bar N+1 release -> refused #2   (positive notional, discarded by broker anyway)
    bar N+1 deploy  -> refused #3   <- the re-buy this document is about
    bar N+2 release -> refused #4, or expiry

`test_wiring_the_decision_ladder_is_three_per_bar` pins that ladder against broker.py's own
AST-extracted functions, **and asserts that a key set to 2 lets the re-buy through** — i.e. the
test fails if anyone tunes the default down into the inert range. Ship **4**: it covers the bar
with one decision of margin. Bias UP, not down. A credit that expires early is a sixth inert
lever; a credit that expires late leaves the index one bar of cash behind, which on these runs
is worth about $0.60 per $1,000 per bar.

Decisions are consumed ONLY when the reserve changes the outcome, so on bars where the core is
`within_band` or `cadence_hold` the credit does not age at all — 4 decisions is therefore a
*floor* of ~1.3 bars in wall-clock terms, often more.

---

## 5. GENERALIZATION — STATED PLAINLY, INCLUDING WHERE IT FAILS

**Mechanism: generalizes. Dollars: ONE WINDOW. I do not meet the >=2-window bar for dollars.**

The mechanism is arithmetic (`buy = min(drift_usd, _spendable)` with no provenance on
`_spendable`) and is present on every bar of every run. The *reachable* dollars are not:

| run | window / regime | `funding` releases | release -> `band_deploy` gap | caught at 4 decisions |
|---|---|---:|---|---:|
| **427197** | 01-01..03-01 bull, core_min 0.10 | 5 | **1 bar** (01-05->01-06), **1 bar** (02-04->02-05) | **$1,999.91** |
| **915207** | 01-01..03-01 bull, core_min 0.25 | 2 | **1 bar** (01-05->01-06) | **$636.59** |
| **383778** | 03-30..04-27 OOS bull | 6 | 6 bars (04-07->04-16), 2-3 bars (04-17/20->04-22) | **$0 — INERT** |
| **542754** | 03-02..03-30 bear | **0** | n/a — the 3 `band_deploy`s are `0.0% -> 40.0%` rebuilds | **$0 — inert by design** |

* 427197 and 915207 are the **same calendar window**. They are two independent *configurations*
  (pre/post commit 89e71f3, `core_min_pct` 0.10 vs 0.25, opening-book overlap **0/4**), which is
  worth something under `_SYNTHESIS.md`'s >=4.94pp noise floor — but it is **not two windows**,
  and I will not claim it is.
* **383778 is where it fails.** The same starvation happens there —
  `Buy gate inputs for AAPL/GOOGL/LWLG/VOD: cash_to_use=$899.61 -> PASS` on 04-22, against
  `[core] bought $824.72 SPY (band_deploy: 26.9% -> 39.9%)`, filling AAPL $88.06 — but the
  nearest funding release is 2-3 bars earlier, so a 4-decision credit has expired. A longer
  credit would reach it; I have not measured the intervening consumption and will not claim it.
  **Honest reading: on the OOS window this key is currently a no-op.**
* In the bear it is structurally unreachable and therefore safe.

### Dollars, back-of-envelope, flagged as unvalidated
Last-vs-first `cp=$` over each log's span: BALL $54.30 -> $66.97 (**+23.3%**),
LLY $1,020.88 -> $1,050.16 (+2.9%), AAL $15.67 -> $15.06 (**-3.9%**), SPY $683.33 -> $682.51
(-0.1%) in 427197 and $683.33 -> $686.16 (+0.4%) in 915207. CCK and AMCR were never bought and
have no marks. Assuming the withheld cash converts on the next bar at the size the gate already
approved: **427197 ~ +$205 (+3.4pp), 915207 ~ -$26 (-0.4pp).** That is three priced names, two
unpriced, no compounding, no exit modelling, and it assumes a conversion this module does not
control. **It is not a forecast.** Per `OBJECTIVE.txt:88-96` this needs a paired A/B on >=3
windows with its own `history_scope_salt` before anyone believes a number.

The direction the fix is certain about: **$2,636.50 stops being index and becomes available to
the lane the design intends.** Whether that lane picks winners is a different investigation
(`discovery-and-ranking.md`), and 915207's AAL says it does not always.

---

## 6. VERIFICATION STATUS — READ THIS BEFORE BELIEVING THE LEVER

**The log signature has NOT been observed in a real run. I was not permitted to start one.**
Five levers shipped inert this session; here is exactly how to falsify this one in one grep.

    grep -n "funding_release_reserved" backtests/<id>.log

Expected, from `_core_sleeve_log_hold` (broker.py:3814):

    [core] hold (deploy) — funding_release_reserved: core 12.1% vs target 37.6% of NAV

Note `_core_sleeve_log_hold` prints **once per CHANGE of reason**, so the line count is a
presence signature, not a counter of refusals.

Second, independent, and stronger falsifier — the reason string on the release path must FLIP:

    today:            [core] hold (release) — band_deploy      (3 / 2 / 2 lines in 427197/915207/383778)
    with the key on:  [core] hold (release) — funding_release_reserved

Third: `[core] bought $X SPY (band_deploy: ...)` must NOT appear on the bar immediately after
`[core] released ... (core rebalance: funding ...)`. In 427197 that pairing occurs twice
(01-05->01-06, 02-04->02-05) and in 915207 once (01-05->01-06). **Zero is the pass condition.**

What IS verified, without a run:
* the leak reproduces to the dollar through broker.py's own extracted `_residual_sleeve_release`
  / `_residual_sleeve_deploy` (92.2% recycled at HEAD, 0.0% patched, same book, same config);
* the key's absence is byte-identical over 200,000 randomised states with an empty ledger;
* the 3-decisions-per-bar ladder matches the `[core] hold (release) — band_deploy` counts that
  actually print in all three bull logs.

---

## 7. SUITE

    PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider backend/tests \
      --ignore=backend/tests/test_core_sleeve_adversarial.py \
      --ignore=backend/tests/test_adv_exit_discipline_findings.py \
      --ignore=backend/tests/test_zz_adversarial_sweep.py
    => 4755 passed, 13 skipped

`test_core_sleeve_adversarial.py` is 7 failed / 4 passed both before and after this change
(identical, verified by swapping `git show HEAD:backend/core_sleeve.py` in and out) — it is one
of the three findings files the brief excludes.

---

## 8. WHAT THIS DOES NOT FIX (so nobody re-tries it here)

1. **The same-tick cash race** (`_SYNTHESIS.md` #1). The gate on the RELEASE bar still reads
   pre-sell cash (`SKIP BUY ARWR — cash_to_use $1.69`). This key does not credit the sale on the
   same tick; it makes the money survive to the next one. That half lives in `broker.py` /
   `graph_nexus_analysis.py` and belongs to whoever owns them.
2. **The satellite design-share binder** (`gap-capital.md`). On 02-02 the refusals are
   `SATELLITE CAP: AMZN skipped — satellite at its design share ($-977 room)`, not cash. Holding
   the cash back does nothing for a headroom refusal beyond delaying the SPY buy by a bar.
3. **Index re-buys funded by SATELLITE sells.** 427197's 02-02 `$510.62` (APP circuit-breaker
   proceeds) and 915207's 02-26 `$685.19` (RVLV proceeds) are the same competition with a
   different source of cash, and this key deliberately does not arm on them — `sweep2.md`'s
   `$2,505` for 427197 includes that `$510.62`, so the honest reachable figure there is
   **$1,999.91, not $2,505**.
4. **`watchlist_priority_slots`.** Still provably inert (`matched=none` on 121/121 audit bars).
   Unrelated, unchanged, do not ship it.
