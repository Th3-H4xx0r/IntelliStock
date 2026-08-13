# Preregistration — conviction-reserve paired arms

Date: 2026-08-12 (written and committed BEFORE launch)
Hypothesis source: `docs/investigations/conversion-root-cause-bt915207.md`

## Question

Does enabling the already-built `satellite_conviction_reserve_pct` lever let a late-arriving
high-conviction name receive a materially larger share of NAV, instead of being trimmed to
sub-1% as `SNDK` was in bt 915207?

## Arms

| arm | doc | instance | `satellite_conviction_reserve_pct` | `history_scope_salt` |
|---|---:|---|---:|---|
| control | 194 | `v2-conv-ctl` | **0** | `conv-ctl-194` |
| treatment | 195 | `v2-conv-trt` | **0.15** | `conv-trt-195` |

Verified diff between the two documents is **exactly two keys**: the treatment variable and
the protocol-required per-arm salt. Every other one of the 580 config keys is byte-identical.

Both docs differ from doc 193 only by salt and `nexus_discovery_snapshot_enabled=False`
(identical in both arms, so neither can overwrite the shared base discovery snapshot and
rewrite the other arm's starting universe). doc 193 and doc 179 are untouched.

Both arms use fresh salts, so both start **equally cold** rather than one inheriting doc 193's
warm 6,957-row scope.

## Window

Reference window `2026-01-01..2026-03-01`, granularity 3600s, initial cash $6,000,
`pit_mode=research` (lookahead, therefore not promotion-eligible).

## Primary endpoint — mechanism, not P&L

The lever is accepted as *binding* only if the treatment log shows a changed signature:

1. at least one `SATELLITE OVERFLOW` event funding **> $300** at a bar later than bar 5
   (control delivered $12-$51 there);
2. at least one discovered name funded **>= 5% of NAV** that the control funded below 2%;
3. `[core] funding request trimmed` delivering **> 5%** of requested dollars after bar 5
   (control: 0.5%).

If none of these appear, the lever is **inert** and is reported as inert regardless of return.
This is the rule that five previously "working" levers failed.

## Secondary endpoints

* total return and SPY active return, treatment minus control;
* max drawdown magnitude;
* gross turnover — a rise here is disqualifying, turnover is the known leak
  (~290%/mo live versus ~50%/mo break-even);
* per-name funded NAV weight distribution (control mean 6.75% / median 4.73%).

## Decision rule, fixed in advance

* Return differences inside **+/-4.94pp** are the measured run-to-run noise floor and are
  **inconclusive**, not a win.
* A return improvement does not offset a drawdown worsening of 4.94pp or more.
* One window is never sufficient. Promotion requires at least three windows including one
  out-of-sample and one **not** led by semiconductors. Planned follow-ups, only if the
  mechanism binds here: `2026-03-30..2026-04-27` (OOS bull), `2026-06-01..2026-07-01`
  (non-semiconductor leadership), `2026-03-02..2026-03-30` (bear safety veto).
* n=5 round trips is not evidence.

## Known validity limits, stated before results

* Separate salts are the protocol's own instruction, but they are **not** true state
  isolation: Nexus state is shared and mutable. This pair is a directional screen, not the
  frozen causal test defined in `docs/investigations/frozen-paired-state-design.md`.
* `pit_mode=research` carries lookahead bias. No result here can promote anything to real
  money.
* Arms are run sequentially on shared infrastructure; arm-order effects cannot be fully
  excluded even with snapshot writes disabled.

## Prohibited during the pair

No push to `main` while either arm is running (a push auto-deploys and kills the run). No
config edit to doc 193, doc 194, doc 195, or doc 179 between the two arms.

## Operational finding — arms must run sequentially

First attempt launched both arms within seconds of each other:

* control `609441` (`v2-conv-ctl`) -> `stopped` at progress 0
* treatment `906181` (`v2-conv-trt`) -> `running`

The control log runs 320 lines, initialises cleanly (`RNG seed 0`, `PYTHONHASHSEED=0`,
history scope `v2-conv-ctl|4f430a0ae8cdd108951ff2c3`, clean start, Neo4j snapshot loaded)
and then ends mid bar-fetch at `06:57:06` with **no stop message, no traceback, and no
error** — the sole logged error is a benign sentiment-LLM fallback. That is an external
kill, not a strategy failure.

**Conclusion: this deployment runs one backtest at a time; a second launch preempts the
first.** Paired arms must be run strictly sequentially, and the arm-order caveat already
stated in the validity limits therefore applies to every pair.

Revised execution order: run treatment `906181` to completion, then relaunch the control on
the identical window/cash/granularity. Do not push to `main` while either arm is running.
