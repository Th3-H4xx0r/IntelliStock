# NEXT-RUN PLAN — exact sequence, in order, with the log signature to grep

Written 2026-08-10 while bt 584886 (non-semi window) was in flight. Nothing here may be
executed until that run reports a terminal status: **every push auto-deploys and kills the
run.**

## STEP 0 — wait, then push the accumulated commits

```bash
python3 scripts/pull_backtest_logs.py 584886 --summary      # must NOT say 'running'
python3 scripts/summarize_backtest.py 584886                # the verdict, vs SPY -1.71%
git push                                                    # deploys; safe only now
python3 scripts/check_deployed_code.py                      # must say all 6 files match
```

Commits waiting: the fresh-low stamp + gate, its offline verification, and three tools
(`set_doc_config.py`, `summarize_backtest.py`, `benchmark_window.py`).

## STEP 1 — arm the fresh-low rule, verified by read-back

```bash
python3 scripts/set_doc_config.py 193 \
    --set residual_sleeve_bear_block_at_fresh_low_bars=2 \
    --set regime_rally_onset_enabled=true --apply
```

N=2, not 1 — see `fresh-low-verification.md` §2. The script reads the document back and
prints `ok`/`FAIL` per key; a config that was not read back is a config we do not know.

## STEP 2 — the run that decides it: OOS bull, where the misfire happened

```bash
python3 scripts/reset_backtest_event_state.py --instance v2-let-run-core --apply
python3 scripts/run_validation_backtest.py 2026-03-30 2026-04-27 \
        --cash 6000 --granularity 3600 --instance v2-let-run-core
```

**Declared log signature — grep it before claiming anything works:**

```
[sleeve] bear leg SKIPPED — proxy at a fresh 20d low (since_20d_low=0 < 2, off_low=+0.0%)
```

and `V31 market regime: ... since_20d_low=0 off_20d_low=0.0` on 03-30/03-31.

**Pass/fail, declared in advance:**

| outcome | reading |
|---|---|
| `SQQQ` absent from `pnl_per_stock`, return ~+21% | the fix works; that window goes from a dead heat with SPY (+13.35% vs +13.10%) to ~+8pp of real alpha |
| SKIP line present but return still ~+13% | the freed $2,100 went somewhere equally bad — a capital-allocation problem, not a sleeve problem |
| SKIP line ABSENT | the gate did not reach the decision. Check the diag is stamped (`since_20d_low=` on the regime line) before touching the rule |
| return drops | the hedge was carrying more than the $514 loss suggests; revert |

## STEP 3 — the run that protects the downside

```bash
python3 scripts/reset_backtest_event_state.py --instance v2-let-run-core --apply
python3 scripts/run_validation_backtest.py 2026-03-02 2026-03-30 \
        --cash 6000 --granularity 3600 --instance v2-let-run-core
```

The bear window's SQQQ leg was **+$965 = 124% of that window's profit**, and it opened at
`since_20d_low = 18`. The gate must not touch it. **Fail = any `bear leg SKIPPED — proxy at a
fresh 20d low` line in this log, or SQQQ P&L materially below +$900.**

Do not skip this. `fresh-low-verification.md` predicts the gate is inert here; a prediction
that is not checked against the run is exactly how five inert levers shipped last session.

## STEP 4 — only then, the scorecard

| window | SPY | strategy | alpha | status |
|---|---|---|---|---|
| ref bull/chop 01-01..03-01 | +0.24% | +17.36% / +14.65% | +17.1 / +14.4pp | pass |
| bear 03-02..03-30 | -7.86% | +10.44% | +18.3pp | pass, re-verify at STEP 3 |
| OOS bull 03-30..04-27 | +13.10% | +13.35% | **+0.25pp** | **dead heat — STEP 2 decides it** |
| non-semi 06-01..07-01 | -1.71% | bt 584886 | ? | the overfit test |

The objective needs >=3 windows including 1 OOS and 1 not semi-led. Until STEP 2 lands, the
OOS slot is a tie with the benchmark at 24x the drawdown, which is not a pass.
