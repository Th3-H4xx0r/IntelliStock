# Winner/anchor reinforcement: independent code and budget audit

**Audit snapshot:** 2026-08-10, commit `2cd998c`.  **Repository changes:** this report only; no
code/config/state changes, commits, pushes, or backtest control actions. API credentials came from
`.env`; fetched logs were kept outside the repository/in memory. Backtest 615886 was still running
when sampled and is not treated as a result.

## Executive finding

`ANCHOR ADD:` is a **planner-allocation line, not an executed-add line**. In bt 633644 the planner
produced five such lines ($1,153 total), but the broker rejected every one immediately at the
standing satellite cap. UUUU, NVO, and SNDK had no add fills and no quantity increases. The P&L in
the existing `docs/investigations/anchor-target.md` result section belongs to their original lots;
it is not P&L on $1,153 of reinforcement capital.

The hard-coded 40% budget was locally binding on four of the five planner allocations, but it was
**not causally binding on a single executed fill**:

* every plan died first at `SATELLITE CAP`;
* all five positions were already above the broker's default 15%-of-NAV single-position cap, so
  their broker headroom was $0 even if the satellite gate had allowed them;
* the first four plans occurred while the turnover budget was binding at 74-81% of NAV, and their
  raw scores (1.20-1.25) were below the 1.50 turnover/satellite conviction threshold;
* the planner marks the stage fired before any broker gate or fill, so each failed plan is never
  retried during the run;
* the failed plan is nevertheless subtracted from the same-bar stock budget, so it can crowd out a
  new entry without spending cash on the anchor.

Therefore changing `0.40` alone is not a functional fix. It increases planned/crowd-out dollars but
cannot make the observed bt 633644 attempts execute.

## GitNexus result and blast-radius warning

I followed the GitNexus workflow before source inspection:

1. `npx gitnexus status` reported a stale index; `npx gitnexus analyze .` refreshed it to commit
   `2cd998c` (44,917 nodes, 83,594 edges, 144 flows).
2. Concept queries for anchor/winner budgets and broker buying power found the relevant allocator,
   core/turnover, pending-sell, and execution tests.
3. `context`/upstream+downstream `impact` for `_plan_anchor_reinforcement` and
   `_winner_add_budget_cap` returned **not found / UNKNOWN**. This is an index limitation, not proof
   of no blast radius: `backend/strategies/graph_nexus_analysis.py` is one of the files skipped as
   >512 KiB, and `_winner_add_budget_cap` is a local in `GraphNexusAnalysis.run_once` rather than a
   symbol. `backend/broker.py` is likewise too large for useful symbol coverage.
4. Manual symbol search gives the direct radius: `_plan_anchor_reinforcement` has one production
   caller (`GraphNexusAnalysis.run_once`, line 30139) plus
   `backend/tests/test_anchor_reinforce_target.py`; its output becomes broker execution metadata.
   The 40% local controls anchor planning, the remaining new-stock slate, core funding request, and
   broker order sizing in that one run-once flow.
5. The indexed downstream surfaces are high-risk: changing
   `PortfolioEmulator.get_buying_power` is **HIGH** impact (108 impacted, 10 direct), while
   `PortfolioEmulator.execute_signal` is **MEDIUM** (18 impacted, 11 direct). No change to either is
   needed for the recommended first fix. Any later edit there requires explicit HIGH-risk warning
   and the full settlement/execution suite.

Because the two requested strategy/broker files are excluded from graph indexing, GitNexus cannot
certify a safe blast radius for a code edit here. Treat the proposed strategy/broker change as at
least **MEDIUM, manually assessed**, and do not claim graph-proven safety.

## Actual dataflow

### 1. Candidate and target planning

`GraphNexusAnalysis.run_once` builds held candidates from held stock buy signals plus any held name
whose current raw score is at least `winner_add_min_raw_score` (default/config 0.25), then attaches
position health (`graph_nexus_analysis.py:30066-30118`). The candidate list is broader than
stage-qualified winners.

With `anchor_reinforce_enabled` true, `_plan_anchor_reinforcement` (`:10748-10870`) sorts by raw
score/P&L and checks:

* positive available budget and enabled flag;
* equity, valid ticker, positive entry notional;
* held days and unrealized-P&L stage thresholds;
* generic peak-drawdown limit (5%, or 10% for raw >= 1.5);
* stage not already recorded in `_anchor_reinforce_stage`;
* target gap and allocation each at least `min_position_size` (bt 633644: $100).

For target-percent mode:

```text
stage target = portfolio NAV * target_pct/100 * stage_mult/stage1_mult
current_value = first_entry_notional * (1 + first_entry_return)
need          = max(0, stage target - current_value)
allocation    = min(remaining anchor budget, need)
```

It permits a partial stage whenever the partial is at least $100, and then immediately records the
whole stage as fired. The stage-specific config keys `anchor_reinforce_stage{1,2,3}_max_dd` present
in bt 633644's frozen schema are not read; only the generic and high-conviction drawdown keys are.

### 2. Upstream budget construction

The exact budget chain before the hard-coded 40% is:

| Order | Constraint | Code / behavior |
|---|---|---|
| 1 | Cash plus planned sells | `_compute_available_buy_budget` (`:11348-11418`) starts from `get_cash()` and adds estimated gross same-bar sell proceeds. It does **not** start from `get_buying_power()`. |
| 2 | Cash-reserve floor | Subtracts scaled floor from initial NAV (`cash_reserve_floor_pct`; 2% in bt 633644). |
| 3 | Deployment ramp | Caps deployment room by bar/regime; bt 633644 ramp logs include 54%, 57%, 60%, then 100%. |
| 4 | Conditional reserve release | `_compute_releasable_cash_reserve` can add the floor back only after the minimum alpha-position count and a qualifying `stock_buys` score. An anchor-only candidate does not independently trigger this input. |
| 5 | Macro scale | Bearish macro can multiply the budget down (0.9 minimum in bt 633644); positive SPY-20d can suppress it. |
| 6 | Buy-budget floor | `buy_budget_floor_pct` can raise the planner budget to a NAV share (10% in bt 633644), even above genuinely spendable cash. Broker/emulator clamps remain authoritative. |
| 7 | Backfill reserve | `_compute_backfill_budget_partition` holds 10%, or 20% with a queued high-conviction item in bt 633644, leaving the primary budget. |
| 8 | Stock/ETF split | Stock receives `primary * .95/(.95+.15)` when both lanes exist, all primary when only stocks exist, and **zero when `stock_buys` is empty**. Held anchor documents can still exist through the raw-score append while this stock budget is zero. |
| 9 | Rotation proceeds | After rotation pairing, `_estimate_rotation_sell_proceeds` adds newly planned rotation proceeds to both available and stock budgets (`:30051-30064`). This is gross estimated notional and precedes actual next-event settlement. No rotation funding line appeared in bt 633644. |
| 10 | Anchor share | `_winner_add_budget_cap = _stock_budget_available * 0.40`; planner receives `min(stock, cap)`, i.e. exactly 40% (`:30120-30146`). Unused planned anchor budget is returned; planned spend is subtracted from the new-entry slate (`:30216-30220`). |

Important zero-budget defect/interaction: bt 633644 printed 43 anchor-budget cycles. Forty-one had
candidate documents; **13 of those 41 had `stock_budget=$0` and therefore anchor budget $0** even
though the primary budget on those lines was about $509-$585. The stock split is keyed on fresh
`stock_buys`, not on stage candidates.

### 3. Strategy-side constraints after planning

* Sell-signal, sell-enforcement, and forced-exit filters can delete planned adds (`:30161-30192`).
* `new_entry_reserved_budget_pct` only reserves money from the legacy momentum-amplifier path;
  amplifier is skipped while anchor reinforcement is enabled. It does not further cap anchor plans.
* `single_position_max_pct` is applied to the **legacy** `_plan_winner_adds` (`:10929-10941`) but is
  missing from `_plan_anchor_reinforcement`.
* The later per-entry and total-spend caps deliberately exclude held symbols
  (`:32291-32308`, `:32417-32423`), so they do not restrain anchor adds.
* The final NAV min-position guard also deliberately excludes held symbols (`:32588-32617`). Thus
  the anchor's only strategy floor is $100, not bt 633644's 6%-of-NAV new-entry floor (~$360-$414).
* Failed broker attempts are not refunded into the already-built same-bar new-entry slate.

### 4. Broker/core/turnover/cash constraints

The broker processes sells first, then buys by intent; `winner_add_buy` priority is ahead of ordinary
new entries (`backend/broker.py:13948-13986`). Each anchor intent then faces, in order:

1. Nexus execution/price guard.
2. **Standing satellite/core cap** (`:15065-15169`), explicitly applicable to held adds. A raw score
   below `satellite_conviction_overflow_min_raw_score` uses only the design-share room. Raw >= the
   threshold may use floor-bounded overflow room. The core funding pre-pass sums the planned anchor
   dollars but caps its release against the same plain/conviction room (`:14519-14703`), so release
   and buy agree. Bt 633644 used threshold 1.5; all five anchor plans had raw 1.20-1.25 and the
   satellite was already $1,364-$1,574 beyond design room.
3. **Turnover budget** (`:15193-15290`). Every discretionary buy, including an add, is blocked once
   rolling one-way notional reaches 50% of NAV. Sells remain allowed. Bt 633644 enabled a conviction
   bypass at raw >= 1.5 with no bypass ceiling; the five anchors were below it. Four anchor ticks
   were already at 74-81%; the fifth occurred later after old ledger rows rolled off but still died
   at the satellite cap.
4. Regime/max-position gates. Held-name adds are exempt, so these are not the bt 633644 blocker.
5. **Broker cash gate** (`:15325-15413`). It computes
   `min(planned, get_cash - strategy_reserved_capital - effective_cash_floor)`. Here
   `reserved_total` means capital reserved by other configured strategies, not next-event order
   reservations. The 2% cash floor can be waived for a `high_conviction` hint after five alpha
   positions; anchor metadata calls raw >= 0.50 high-conviction, a different threshold from the
   1.50 satellite/turnover threshold.
6. **Broker single-position cap** (`:15414-15463`). This is
   `BROKER_MAX_SINGLE_POSITION_PCT`, a fraction read from the process environment, default 0.15,
   and it applies in backtest and live. It is distinct from strategy
   `single_position_max_pct=25` (percent points). The frozen result does not record the env value.
   Bt 633644 also carried `max_single_position_pct=0.15`, but broker comments/tests identify that
   config key as dead; it is not the cap reader.
7. **Execution min floor** (`:15572-15640`). New names face the configured NAV floor and a
   `fundable` check. Held adds are explicitly exempt, so an anchor can pass even if final fundable
   dollars are below $100 or zero.
8. **PortfolioEmulator final clamp** (`portfolio_emulator.py:1457-1494`). It funds
   `min(cash_to_use, get_buying_power(sum(in-flight BUY reservations)))`. Buying power subtracts
   unsettled proceeds and in-flight reservations. With
   `backtest_credit_pending_sell_proceeds=true` (bt 633644), it can add 95% of pending same-tick
   sell notional. With `backtest_credit_sell_proceeds_enabled=false` (also bt 633644), however, the
   earlier broker cash ceiling itself is not lifted by submitted sells, so raw cash may already
   have truncated `cash_to_use`. A held add's floor exemption means this truncation can silently
   turn a planned stage into a tiny fill.
9. Live-only stock-order gate, price sanity, kill switch, and order idempotency still apply in live.

Sell proceeds are therefore counted three different ways: 100% estimates in strategy budget, 95%
optional same-cycle broker credit, and 95% optional pending-sell credit in emulator buying power;
then 5% of completed equity-sale proceeds is withheld to T+1. A planner cap increase does not
remove any of these later ceilings.

## Bt 633644: plan versus execution

Frozen relevant settings included target 20%, entry target 14%, strategy single-position cap 25%,
broker default cap 15%, anchor floor $100, satellite/turnover threshold 1.5, turnover budget 50%,
backfill reserve 10/20%, core armed by regime, and same-cycle broker sell credit off.

| Planned line | Target need from logged NAV/entry/P&L | 40% cap / allocation | Existing weight before add | Actual broker outcome |
|---|---:|---:|---:|---|
| UUUU stage 1, +$241 | ~$240 | $245 / $241 | 16.26% | `SATELLITE CAP ... skipped ($-1,400 room)` |
| NVO stage 1, +$175 | ~$301 | $175 / $175 | 15.25% | `SATELLITE CAP ... skipped ($-1,364 room)` |
| UUUU stage 2, +$211 | ~$457 | $211 / $211 | 17.68% | `SATELLITE CAP ... skipped ($-1,457 room)` |
| UUUU stage 3, +$319 | ~$848 | $319 / $319 | 18.47% | `SATELLITE CAP ... skipped ($-1,574 room)` |
| SNDK stage 2, +$207 | ~$373 | $207 / $207 | 18.86% | `SATELLITE CAP ... skipped ($-1,574 room)` |

The four equality rows prove that 40% clipped the **plan**. They do not prove it blocked an
execution. The pre-add weights also prove the next hard broker cap had $0 headroom in all five
cases. Stage targets themselves are 20.0%, 24.6%, and 30.8% of NAV, fundamentally incompatible
with a 15% broker cap unless the general cap is explicitly and safely changed for this lane.

Execution logs contain only the original recipient buys:

```text
FILL BUY UUUU ... 53.07508767 shares ... 2026-01-02
FILL BUY NVO  ... 16.13507190 shares ... 2026-01-02
FILL BUY SNDK ...  1.78663492 shares ... 2026-01-29
```

There are no later recipient buy fills. The immediate sequence is instead:

```text
ANCHOR ADD: UUUU stage=1 +$241 ...          # planner
UUUU ... buy action_intent=winner_add_buy
SATELLITE CAP: UUUU skipped ...             # broker
```

The same sequence occurs for all five. Existing documentation's “mechanically alive” and recipient
add-P&L claims must be corrected before using bt 633644 as evidence.

## Additional correctness defects

1. **Stage is committed at plan time.** `_anchor_reinforce_stage[ticker] = current_stage` occurs at
   `:10868-10869`, before sell filtering, broker gates, order acceptance, or fill. All five skipped
   bt 633644 attempts permanently consumed their stage.
2. **Planner spend is charged despite rejection.** The five false allocations reduced the same-bar
   stock slate by $1,153 total even though cash and recipient shares did not change. A target arm can
   alter unrelated name selection without any anchor exposure.
3. **Later stages ignore filled add capital.** Snapshot construction provides actual
   `position_value` (quantity x price), but the anchor planner uses only original entry notional x
   original-entry return. If an add ever fills, later-stage `need` overstates the gap by ignoring
   prior added shares. Legacy winner-add planning already prefers actual `position_value`.
4. **Partial funding consumes a whole stage.** A $100 partial of a $500 need is considered the stage
   completed. Existing `test_the_budget_still_binds` explicitly pins this behavior.
5. **Current logging conflates three states:** candidate document, planner allocation, and executed
   fill. `none funded` also gives no reject reason. A one-day-old holding can print “none funded on a
   $1,497 budget”; that does not mean budget caused the rejection.
6. **Stage state is in-memory only.** A process restart can forget a genuinely completed stage and
   plan it again; conversely a broker-skipped plan remains suppressed until restart.

## Is the 40% cap “the next constraint”?

No, not as currently phrased.

* In bt 633644 it clipped four plans and reserved 60% for entries as designed.
* It did **not** explain 36 `none funded` lines. Thirteen had zero stock budget; the other 23 had
  $170-$600, while candidates could still fail age/P&L/drawdown/stage/target-gap tests. The planner
  intentionally funds a partial as low as $100, so `$178 < nominal $234` is not itself a no-fund
  explanation.
* It did not prevent a fill; every planned add failed stricter later gates.
* Raising it would increase false “spent” dollars and new-entry crowd-out while stages still die at
  the satellite/single-position/turnover/cash chain.

A cap-only key such as `anchor_reinforce_budget_cap_pct` (absent/0 -> legacy 0.40) is the smallest
possible code lever, but it is **not a sufficient fix and should not be run as a P&L treatment**
until execution acknowledgement and final-cap compatibility exist.

## Smallest safe default-OFF fix

The smallest safe **behavioral** fix is an execution-acknowledged stage mode, not a risk-gate
bypass:

```text
anchor_reinforce_commit_on_fill_enabled = false   # default OFF
```

When enabled:

1. Planner returns `anchor_stage`, `target_total`, `current_position_value`, `additional_needed`,
   and planned dollars, but does not advance the completed-stage map.
2. It uses actual `position_value` for current value (fallback to entry-derived value only if actual
   value is unavailable), so prior fills count toward later targets.
3. Broker carries a distinct anchor order source/stage through next-event execution. A pending-stage
   record prevents duplicate attempts while an order is live.
4. Only a confirmed anchor fill advances the completed stage, with filled notional recorded. A
   satellite/turnover/single-cap/cash/order rejection leaves the stage incomplete. A partial fill
   records dollars remaining rather than declaring the whole target complete.
5. All existing satellite, turnover, cash, settlement, and single-position gates remain in force.
   This is why the mode is safe; it fixes false completion/provenance without silently increasing
   concentration or turnover.

This mode will correctly reveal that bt 633644 still has zero executed adds. It is the prerequisite
to any policy experiment. A retry-only patch without pending/fill accounting is **not** safe: it
would repeatedly crowd out the new-entry slate on every broker-blocked bar.

There is no small safe patch that also makes the bt 633644 plans fill. Doing so requires an explicit,
separate default-OFF anchor execution policy across at least three risk gates:

* a lane-specific final position cap above 15% but no higher than the strategy cap;
* permission to consume floor-bounded core overflow even when raw is below 1.5;
* a bounded turnover exception (bt 633644's current zero ceiling means unbounded bypass).

Those are concentration/core/turnover policy changes, not a budget bug fix. Raising the global
`BROKER_MAX_SINGLE_POSITION_PCT`, treating every profitable anchor as raw-1.5 conviction, or
bypassing the satellite cap would affect other lanes or defeat explicit risk design and is not a
safe “smallest fix.”

If only one code patch is allowed now, implement the default-OFF fill acknowledgement/provenance
mode and leave 40% unchanged.

## Exact tests required before enabling anything

### Default-off and planner accounting

1. **Byte-identical OFF:** absent key and explicit false reproduce current allocations, stage-map
   mutation, and 40% behavior exactly.
2. **No commit on plan (ON):** a $150 plan against a $234 need creates a pending partial, not a
   completed stage.
3. **Actual current value:** after a real stage-1 fill, stage-2 need equals
   `target2 - actual shares*price`, not `target2 - first_entry_notional*(1+pnl)`.
4. **Zero-stock-budget case:** primary budget >0, no fresh `stock_buys`, held eligible anchor;
   explicitly assert current stock/anchor budget is zero and log the reason. Any proposed change to
   this behavior needs its own flag.
5. **Cap accounting:** legacy 40%, any optional override, invalid values, `<= stock_budget`, unused
   return, and allocation below/full need all pinned separately.
6. **Single-position strategy parity:** anchor planner clips by actual held value when the new safe
   mode is on; a clipped result below its floor does not become a completed stage.

### Real broker/emulator path

7. **Bt-633644 UUUU reconstruction:** planner proposes ~$241, satellite room is negative, broker
   emits no order, quantity is unchanged, stage remains incomplete in safe mode.
8. **15% cap reconstruction:** remove/disable only the satellite and turnover blockers; an existing
   16.26%-of-NAV UUUU position produces $0 broker headroom, no accepted order, no stage commit.
9. **Turnover:** raw 1.20 anchor is blocked at 80%; raw >=1.5 follows the existing bypass ceiling;
   neither rejected case commits.
10. **Cash/reservations:** real `PortfolioEmulator`, pending core sell, T+1 withheld tranche, and an
    in-flight BUY reservation. Assert requested, broker `cash_to_use`, `get_buying_power`, accepted
    notional, and eventual fill separately.
11. **Held min-floor:** held add remains slot-floor-exempt, but a $0/unaccepted or sub-policy-min
    result cannot complete a stage in safe mode.
12. **Partial fill / reject / cancel:** no duplicate while pending; cumulative fill accounting is
    exactly once; rejection/cancel clears pending without completing; full/defined partial policy
    commits the correct dollars.
13. **Core funding agreement:** planned anchor dollars do not cause a core release if the matching
    anchor order will be satellite-blocked; no same-bar core sell/buy churn.
14. **Live/backtest parity:** same anchor intent, final position cap, turnover decision, and stage
    acknowledgement in both modes. The frozen result must record the effective broker cap/env.

Existing focused tests passed read-only:

```text
python3 -m pytest \
  backend/tests/test_anchor_reinforce_target.py \
  tests/test_winner_room_and_gate.py \
  backend/tests/test_exec_runt_leak_fundable.py -q
# 30 passed
```

They test planner arithmetic and fundable cash, but none proves an anchor fill or the planner-to-
broker contract.

## Required log signatures

Do not use `ANCHOR ADD:` as success. Make states unambiguous and greppable:

```text
ANCHOR PLAN: UUUU stage=1 target=$1285 current=$1045 need=$240 \
  budget=$245 planned=$240 partial=no raw=1.200

ANCHOR BLOCK: UUUU stage=1 gate=satellite_cap planned=$240 \
  design_room=$-1400 raw=1.200 threshold=1.500
# or gate=turnover / single_position_cap / cash / buying_power / order_gate

ANCHOR ORDER: UUUU stage=1 accepted order_id=... requested=$240 \
  cash_to_use=$... fundable=$... source=anchor_reinforcement

ANCHOR FILL: UUUU stage=1 order_id=... fill=$... cumulative_stage_fill=$... \
  position_value=$... nav_pct=... model=...

ANCHOR STAGE COMMIT: UUUU stage=1 filled=$... target=$... remaining=$0
```

For no-plan bars, log a structured reason summary rather than the current guess:

```text
ANCHOR PLAN NONE: docs=5 stage_eligible=0 budget=$1497 \
  rejects={age:5,pnl:0,drawdown:0,already_fired:0,target_gap:0,budget:0}
```

The validation grep must require `ANCHOR FILL` (or source-tagged `FILL BUY` plus a confirmed
recipient quantity increase), then reconcile plan -> block/order -> fill/commit counts and dollars.
Also retain and correlate the existing signatures:

```text
V31 anchor reinforcement budget
SATELLITE CAP / SATELLITE OVERFLOW
TURNOVER BUDGET BLOCK / BYPASS / BYPASS CEILING
Broker single-position cap
Buy gate inputs
SKIP BUY ... fundable
MAX_POSITIONS_GATE
ORDER GATE BLOCKED
[execution] FILL BUY
```

Promotion criterion: `sum(ANCHOR FILL notional)` must equal the increase in recipient buy notional
and stage-fill ledger (within execution-cost tolerance). A planner line, symbol-level P&L, or an
accepted-but-unfilled next-event order is insufficient.

## Bt 615886 bounded snapshot (not a verdict)

At the final read it was still `running`, 32.76% complete. It had six candidate-bearing anchor
cycles and six `none funded` lines, including budgets around $1,497, $1,418, $660, and $216; it had
no `winner_add_buy` intent and no anchor fill. This is useful only to show that a large 40% budget
and a candidate document do not imply stage eligibility. I did not stop, reset, alter, or wait on
the run.

## Conclusion

The observed execution lane is still unproven/inert. Bt 633644 demonstrates planner activation and
four instances of 40% planner clipping, but **zero executed reinforcement**. The next safe action is
execution-aware provenance and fill-time stage accounting behind a default-OFF flag. Do not raise
40%, widen the global 15% cap, or interpret any multi-window return until a source-tagged anchor
fill and recipient quantity increase are present in the log.
