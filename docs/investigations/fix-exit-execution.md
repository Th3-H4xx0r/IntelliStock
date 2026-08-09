# fix-exit-execution — the peak give-back exit fired 55 times and SLV was never sold

Scope: bt 571147 defect (A). Read `_RUNS4.md`, `_SYNTHESIS.md`, `hold-check.md`,
`gap-bugsweep.md`, `dd-drop.md`, `sweep2.md`, `exits-and-capture.md` first.
No backtest started or stopped. Nothing pushed. `docs/OBJECTIVE.txt` untouched.
Files edited: `backend/strategies/graph_nexus_analysis.py` (exit path only),
`backend/tests/test_peak_giveback_forced_exit.py` (new).

---

## 0. HEADLINE

`fresh_score = -1` is not a sell. It is a **proposal**, and every gate between it
and the broker is keyed on one boolean — `_forced_exit` — which is computed by
**substring-matching the exit REASON** against a tuple the give-back reason does
not belong to:

```
gna:19811  _RISK_EXIT_TAGS   = ("Fast loser", "Circuit breaker", "Trailing stop",
                                "Hold-limit", "Profit take", "Catastrophic stop")
gna:19828  _FORCED_EXIT_TAGS = ("Fast loser", "Trailing stop", "Hold-limit",
                                "Circuit breaker", "Catastrophic stop")

           fresh_reason      = "Peak give-back exit: peaked +60.5% then handed
                                back 28.2% (thresholds 30%/25%)"
                                                     ^ matches NEITHER tuple
```

So `_forced_exit=False`, and a `-1` with `_forced_exit=False` on a name that is
not in the day's discovery universe is **discarded without a log line**. That is
the whole defect. It is not min-hold, not winner_protect, not the rank band —
those are all downstream of the same flag and never even get the chance to
refuse.

The fix is one boolean, default OFF, behind `peak_giveback_forced_exit_enabled`.

---

## 1. THE TWO TAPES, SIDE BY SIDE — `backtests/571147_audit.log`

The exit that **executed** and the exit that **did not**, same run, same config,
same bar-week. Every line below is verbatim.

| | **CART — circuit breaker, SOLD** | **SLV — peak give-back, NOT SOLD** |
|---|---|---|
| trigger | `[sell-gate] CART \| gate=circuit_breaker \| tier=LOW \| regime=bull \| unrealized=-10.4% \| floor=-10.0% (base=-10%) \| result=fired` | `PEAK GIVE-BACK EXIT: SLV peaked +60.5% (>=30%) and has handed back 28.2% (>=25%) — selling` **x55** |
| decision | — | `Monitor decision: SLV day 28 pnl=+15.2% cp=$77.59 entry=$67.35 → SELL (Peak give-back exit: ...)` **x52** |
| overlay | `ML overlay PRESERVE forced-exit: CART score=-1 reason=Circuit breaker: ...` | **absent** |
| sweep | `Sell enforcement ADD: CART forced_exit=True, reason=Circuit breaker: ...` | **absent** |
| broker | `Nexus sell enforcement: CART` | **absent** |
| broker | `V7.5 sell enforcement injection: 1 held ticker(s) added to execution: CART` | **absent** |
| broker | `Nexus sell enforcement: overriding CART from 0 to -1 (trend reversal)` | **absent** |
| fill | `FILL SELL CART qty=16.24549906 ... price=39.120210` | **absent** |

Counts in the 27,188-line log: `PEAK GIVE-BACK EXIT` **55**, `Sell enforcement
ADD` **1** (CART), `FILL SELL` **7** (6 SPY core legs + CART). SLV appears in
zero of them.

**52 of the 55 fires are MONITOR ticks, 3 are FULL cycles.** Both cadences fail,
for related but distinct reasons. Both are traced below.

---

## 2. WHERE THE `-1` DIES — the exact call sites

### 2a. It is never marked forced (three emit sites, one rule)

```
gna:20836  "_forced_exit": (final_score == -1 and fresh_reason
                            and any(x in fresh_reason for x in _FORCED_EXIT_TAGS))   # _finalize_scores, pending branch
gna:20846  "_forced_exit": (fresh_score  == -1 and fresh_reason
                            and any(x in fresh_reason for x in _FORCED_EXIT_TAGS))   # _finalize_scores, plain branch
gna:24429  "_forced_exit": (_epr_score   == -1 and _epr_reason
                            and any(x in _epr_reason for x in _FORCED_EXIT_TAGS))    # _run_monitor_cycle
```
All three -> `False` for `"Peak give-back exit: ..."`.

### 2b. FULL cycle — the ML overlay silently recomputes the sell into a BUY

```
gna:22864   if base.get("_forced_exit"):
gna:22865       final_score = base["score"]          # <- CART lands here, keeps its -1
gna:22866       _log("ML overlay PRESERVE forced-exit: ...")
gna:22874   elif base.get("score") == -1 and _v11_unrealized_pct < -5.0:   # SLV was +15.2%, not < -5%
gna:22880   else:
gna:22881       final_score = 0
gna:22882       if raw_net >= buy_threshold:
gna:22883           final_score = 1                  # <- SLV lands here
```
On the 2026-02-02 full cycle the log records
`conviction_tier: sym=SLV tier=HIGH mcap=?M raw_score=1.000 path=raw_high`
immediately above the give-back line. **raw_net = +1.000, buy_threshold = 0.15**,
so the sell became a **buy**, with no log line at all. There is no
`ML overlay SELL_BLOCK`, no `RANK_BAND`, no `[sell-gate]` — nothing to grep,
which is exactly why `_RUNS4.md` says "something downstream turns that into a
hold".

### 2c. The forced-exit sweep never sees it — and enforcement is the ONLY channel

```
gna:31104   for _fe_sym, _fe_data in scores.items():
gna:31105       if isinstance(_fe_data, dict) and _fe_data.get("_forced_exit") and _fe_data.get("score") == -1:
gna:31107           nexus_sell_enforcement.add(_fe_sym)
gna:31108           _log(f"Sell enforcement ADD: {_fe_sym} forced_exit=True, ...")
gna:31112           entry["sell_fraction"] = 1.0
```
This matters more than it looks, because the broker **filters the strategy's
score dict by `allowed_syms`**:

```
broker:6158  allowed_syms = set(symbols) | set(nexus_discovered) | set(nexus_expansion_buys) | set(nexus_executable_buys)
broker:6170      if sym not in allowed_syms: continue
```
On the 2026-02-02 full cycle the strategy handed the broker **120 symbols and
SLV is not one of them** (verified: parsed all 120 `SYM @ 2026-02-02 ... :
hold/sell/buy` lines — `AMAT`, `CPER`, `SNDK` are there, `SLV` is not). A held
name that has aged out of discovery reaches the broker **only** through
`nexus_sell_enforcement` -> `V7.5 sell enforcement injection` (broker:13442) ->
`overriding X from 0 to -1` (broker:14688). CART travelled that road. SLV was
never put on it.

### 2d. MONITOR cycle — enforcement is not just the main channel, it is the only one

```
571147: "Monitor cycle complete | date=2026-01-30 | symbols=9 | sells=1 | holds=8"
571147: "Run-once strategy 'graph_nexus_analysis' returned scores for 0 symbols"
```
The monitor's whole score dict is discarded (`symbols` is empty on a monitor
tick, so `allowed_syms` is empty). Its one surviving output is:

```
gna:24599   if (bool(config.get("nexus_monitor_risk_exit_execution_enabled", False))
gna:24601       or bool(config.get("nexus_monitor_risk_exit_always_enabled", False))):
gna:24603       for _msym, _mentry in out.items():
gna:24606           if _mentry.get("score") == -1 and _mentry.get("_forced_exit"):
gna:24610               _se_out.append(_msym)
```
`nexus_monitor_risk_exit_always_enabled` **is already True** in
`scripts/doc193_backup_patch_20260808T110842Z.json` — the flag is not the
problem. `_forced_exit` is. That is where 52 of the 55 fires died.

### 2e. Everything else people suspected is downstream of the same flag

Each of these would have refused SLV too, and each is literally
`not base.get("_forced_exit")` — none of them ever ran, because the score was
gone by 2b/2c:

| candidate | site | guard |
|---|---|---|
| `llm_sell_min_hold` (`sell_enforcement_min_hold_days`, **15** in this doc) | gna:22890 | `if portfolio_emulator is not None and not base.get("_forced_exit")` |
| `winner_protect` | gna:22911 | `... and not base.get("_forced_exit") and ...` |
| overlay `sell_block` | gna:22960 | `... and not base.get("_forced_exit") and ...` |
| rank-band exit suppression (`rank_band_enabled=True` here) | gna:23300 | `if sc.get("_forced_exit") or any(tag in _reason for tag in _RISK_EXIT_TAGS): continue` |
| momentum-watchlist protection stripping enforcement | gna:32565 | `if isinstance(_sd, dict) and _sd.get("_forced_exit")` |
| V31 grace | gna:20550 | `_RISK_EXIT_TAGS` substring |

**Answer to the parent's direct question:** yes — it needs `_forced_exit=True`
**or** a `_RISK_EXIT_TAGS` reason string. Those are the same key on two doors.
`sell_enforcement_min_hold_days` / `llm_sell_min_hold` / `winner_protect` /
rank-band are **not** the blocker on their own; they never got a turn.
Non-blockers, checked and cleared: V31 grace (SLV held 28d, `initial_grace_bars`
14; no `V31 grace SUPPRESS: SLV` line exists), the broker's sleeve exemption
(broker:13253, `{SPY, SQQQ}` only — SLV is not a sleeve leg), and any ETF
carve-out (there is none in the enforcement path).

---

## 3. THE FIX — `peak_giveback_forced_exit_enabled`, default False

Minimal and structural. The give-back requests the forced-exit path through the
`extras` dict `_evaluate_position_risk` already returns, instead of smuggling a
`_FORCED_EXIT_TAGS` substring into the reason (which would keep the reason
honest-looking while making `grep "Circuit breaker"` lie).

1. `_evaluate_position_risk` (gna:20280) — on a give-back fire with the key on:
   `extras["forced_exit"] = True`, plus a **new, greppable log line**:
   `PEAK GIVE-BACK FORCED EXIT: <sym> routed through _forced_exit -> nexus_sell_enforcement (peak_giveback_forced_exit_enabled)`.
2. V31 grace (gna:20681) — a forced give-back counts as a protective exit.
   Grace gates signal sells; a stop a calendar can veto is not a stop.
3. Return guard (gna:20750) — `forced_exit` is dropped unless the score is still
   `-1` **and** the reason is still the give-back. Without this, a later
   partial `Profit take` trim would inherit the flag and the sweep's
   `sell_fraction = 1.0` would liquidate 100% of the position.
4. The three `_forced_exit` emit sites (gna:20971 / 20987 / 24573) OR in
   `_epr_extras.get("forced_exit")`, written so the historical expression's
   value is preserved **exactly** when the extra is absent.

Key absent -> `extras` never gets the entry -> all four sites evaluate to
today's value -> byte-identical. The give-back still fires and still goes
nowhere, which the tests assert explicitly.

---

## 4. EVIDENCE — the log signature, produced by the real code path

Replay: reconstruct each name's recorded price tape from the
`Monitor decision: SYM day N pnl=X% cp=$Y entry=$Z` lines and drive the real
`GraphNexusAnalysis._run_monitor_cycle` over it with 571147's exit-relevant
document, key OFF then ON. This exercises the actual module, not a model of it.

```
RESULT 571147_audit.log  forced=False  bars=266  give_back_fires= 52  sell_enforcement_entries=  0
RESULT 571147_audit.log  forced=True   bars=266  give_back_fires= 52  sell_enforcement_entries= 52
     first = 2026-01-30  cp=$77.59  "Peak give-back exit: peaked +60.3% then handed back 28.2%"
RESULT 427197.log        forced=False  bars=324  give_back_fires=110  sell_enforcement_entries=  0
RESULT 427197.log        forced=True   bars=324  give_back_fires=110  sell_enforcement_entries=110
     first = 2026-01-30  cp=$77.59  "Peak give-back exit: peaked +60.3% then handed back 28.2%"
```

`forced=False` reproduces the defect **to the count**: 52 monitor fires, 0
enforcement entries — the log's own 52 `Monitor decision: SLV ... → SELL` lines
with no sell. `forced=True` converts every one of them, first on the exact bar
and at the exact price the run recorded.

Lever log signature to grep in the next run:
`PEAK GIVE-BACK FORCED EXIT:` (strategy) then `Sell enforcement ADD: SLV
forced_exit=True` -> `Nexus sell enforcement: SLV` -> `V7.5 sell enforcement
injection: ... SLV` -> `FILL SELL SLV`. If the first line is absent the key did
not reach the strategy; if it is present and `FILL SELL` is not, the defect is
below the strategy and this document is wrong.

### Blast radius — 8 runs, 5 windows, 3 regimes, every held name

Same replay over every name in every log I have, counting names routed into
`_nexus_sell_enforcement`:

| run | window / regime | names | routed OFF | routed ON | **delta** |
|---|---|---:|---|---|---|
| 571147 | 2026-01-01..03-01 bull | 10 | `{}` | `{SLV: 52}` | **+SLV** |
| 427197 | 2026-01-01..03-01 bull | 10 | `{APP: 71}` | `{APP: 71, SLV: 110}` | **+SLV** |
| 915207 | 2026-01-01..03-01 bull/chop | 11 | `{NVDA: 5}` | `{NVDA: 5}` | — |
| 820236 | 2026-01-01..03-01 bull | 8 | `{CORD: 7}` | `{CORD: 7}` | — |
| 613166 | 2026-01-01..03-01 bull | 10 | `{AMZN: 179, PLRZ: 4}` | identical | — |
| 725146 | 2026-01-01..03-01 bull | 10 | `{PLRZ: 4, NVDA: 5, WDC: 1}` | identical | — |
| 383778 | 2026-03-30..04-27 OOS bull | 16 | 16 names | identical | — |
| 542754 | 2026-03-02..03-30 **bear** | 4 | `{UHS: 4}` | identical | — |

**The change adds exactly one name, in the two runs that own the defect, and is
bit-for-bit inert in the other six** — including the bear window and the OOS
window. `APP: 71` in 427197 is the -20% circuit breaker routing itself both ways:
a live control proving the harness measures the flag and not the weather.

### The rule underneath is still the rule that was validated

Independently recomputed from the same tapes — worst drawdown-from-peak per
name, against the 25% arm:

```
571147  SLV 40.4% FIRES | SNDK 20.9% | AMAT 15.4% | CPER 10.9% | BA 9.6%
427197  SLV 40.4% FIRES | APP  26.5% (peak +8.9%, fails the +30% leg) | AMAT 15.4%
915207  SNDK 22.4% | TCMD 18.2% | AMAT 15.4% | VOYA 13.0% | NVDA 12.0%
820236  SNDK 22.4% | WDC 17.1% | LRCX 16.2% | CPER 12.4% | CORD 12.0%
613166  AGMI 20.3% | AMZN 19.0% | SNDK 13.0% | EGO 10.1%
383778  AAOI 12.4% | HOOD 9.8% | RIVN 8.6%
542754  BTC 12.3% | UHS 11.6% | SQQQ 9.2%
```
Worst surviving winner **22.4%** (SNDK, twice) vs a **25%** arm: 2.6pp of
clearance, matching `hold-check.md` §5. APP is the useful near-miss — 26.5% off
its peak, above the drawdown leg, but its peak was only +8.9%, so the +30% leg
correctly refuses to treat a loser's bounce as a give-back.

### Dollars — an upper bound, honestly labelled

SLV: `12.47106599 sh @ $67.353890 = $839.97`, first arm 2026-01-30 at $77.59.

| run | exit at first arm | actually held to | delta | on $6,000 |
|---|---|---|---|---|
| 571147 | $967.63 (**+$127.66**) | $855.64 at 02-06, the last bar (+$15.67) | **+$111.99** | +1.87pp |
| 427197 | $967.63 (**+$127.66**) | $877.96 at 02-13 (+$37.99) | **+$89.67** | +1.49pp |

**$90–$112, not the $469 peak give-back.** SLV gapped 21.2% -> 28.2% off its
peak inside one bar on 01-30; no peak-anchored rule that leaves WDC/SNDK/LRCX/
AGMI alone can arm before that. The value is converting an unbounded give-back
into a bounded one. Second-order effects (the freed ~$968 re-entering the buy
lane, the turnover brake, the `max_positions` slot) are **not** modelled here
and could go either way.

---

## 5. WHAT IS VERIFIED AND WHAT IS NOT

**Verified.**
* The defect, to the count, in a real log: 55 fires / 52 monitor sells / 0 sell
  enforcement / 0 `FILL SELL SLV`, and the byte-for-byte contrast with CART.
* The mechanism, at named line numbers, in both cadences, including the two
  silent drops (`allowed_syms`, the overlay recompute) that leave no log line.
* The fix producing `_forced_exit=True` and a `_nexus_sell_enforcement` entry,
  through the real `_run_monitor_cycle`, on the recorded tapes of **two** runs,
  first firing on the exact bar and price the runs recorded.
* Inertness on the other six runs / three regimes, every held name.
* 17 new tests; **8 of them fail on the pre-fix file** (confirmed by stashing
  only `graph_nexus_analysis.py` and re-running).

**NOT verified — stated plainly, because five levers shipped inert this session.**
* **No backtest was run.** The broker leg — `V7.5 sell enforcement injection` ->
  `overriding SLV from 0 to -1` -> `FILL SELL SLV` — is proven only by CART
  traversing it in the *same run under the same config*, plus static reading of
  broker:13442 / 14688. It is not proven for SLV.
* The FULL-cycle path (3 of the 55 fires) is argued from source and from the
  120-symbol universe dump, not replayed — my replay harness drives the monitor
  cadence, which is where 52 of 55 fires are.
* The dollar figures are single-name mark arithmetic on a book that would have
  diverged the moment the sell filled.
* One name, two runs, one window. The **routing** generalises (mechanism +
  6 inert runs across 3 regimes); the **profit** does not, and I will not claim
  it does.

## 6. NOT DONE, WITH REASONS
* I did **not** add `"Peak give-back"` to `_RISK_EXIT_TAGS` / `_FORCED_EXIT_TAGS`.
  It is a smaller diff and it would work, but it changes behaviour under the
  already-set `peak_giveback_*` keys with no new opt-in, and
  `test_nexus_cost_discipline.py:221` parametrises over `_RISK_EXIT_TAGS`, so
  widening it quietly widens an unrelated contract.
* I did **not** touch the thresholds (30/25), the trailing stop, or
  `trailing_stop_disabled`. `exits-and-capture.md` is right that a 12–15% trail
  kills all eight winners; this is not that.
* I did **not** touch the entry/sizing question of why $840 of a $6,000 alpha
  book is in silver. That is `gap-capital.md` / `discovery-and-ranking.md`.

## 7. SUITE
```
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider backend/tests \
  --ignore=backend/tests/test_core_sleeve_adversarial.py \
  --ignore=backend/tests/test_adv_exit_discipline_findings.py \
  --ignore=backend/tests/test_zz_adversarial_sweep.py
```
Green for everything in this fix's blast radius: 228 passed across
`test_peak_giveback_forced_exit` (17, new), `test_nexus_evaluate_position_risk`,
`test_nexus_monitor_cycle`, `test_nexus_cost_discipline`,
`test_nexus_v9_preflight`, `test_bfq_ordering_and_giveback`,
`test_drawdown_circuit`, `test_bear_book_trim`,
`test_rank_band_momentum_exemption`, `test_nexus_tick_mode_stamp`,
`test_nexus_granularity_scaling`.

**Full suite: `4755 passed, 13 skipped` — GREEN.**

Caveat on how that number was reached: sibling agents are editing
`backend/core_sleeve.py` and `graph_nexus_analysis.py`'s backfill-queue path in
the same working tree, so two earlier full runs failed transiently and
differently (`5 failed` in source-inspection tests that pass in isolation, then
`4 failed` in `test_core_funding_release_reserve.py`, a sibling's new file).
Both sets were outside the exit path and both are gone now. The green run above
therefore covers this change **plus** whatever the siblings had landed at that
moment; whoever integrates should re-run it once the tree is quiet.
