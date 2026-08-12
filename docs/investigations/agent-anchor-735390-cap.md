# Independent cap audit — backtest 735390 / commit `960a469`

Date: 2026-08-10

## Bottom line

The UUUU anchor order and fill did **not** breach 20%. The broker admitted the order from a
17.647662% position, sized it against $155.429489 of decision-time headroom, and the next-event fill
left UUUU at **19.483069%** of post-fill NAV. UUUU first crossed 20% one snapshot later with no
quantity change: its mark rose from $21.29 to $23.21 while other holdings declined. The terminal
21.304205% weight is therefore mark-to-market drift, not anchor overfill.

Commit `960a469` implements a **buy-admission/current-mark cap**, not continuous portfolio
rebalancing. It checks planner and broker headroom before submission. It neither rechecks/reduces the
next-event fill against then-current NAV nor trims later appreciation. Some comments call this a
"final-position cap," but the executable code and tests do not promise continuous <=20% weight.

This does not retroactively turn the registered run into a pass. The preregistration deliberately made
"final recipient weight stays at or below 20%" a stronger terminal criterion, so bt 735390 still fails
that criterion, and its independent turnover >80% invalidator remains. The precise cap diagnosis is:
**admission/fill cap passed; preregistered continuous/terminal cap failed.**

## Evidence audited

* Full stopped log: `/tmp/bt735390-stopped.log`, 15,224 lines, SHA-256
  `aa58322f239ed36c4fee2ce7fe2e6c728d6a27518c7380a17db855eb333dcc11`.
* Stopped status plus full `/summary`: `/tmp/bt735390-stopped.json`, SHA-256
  `a1d49035c5c6705a87acda4b2c49f3f60d7636025c49b33dbeccbdc6fc17c69a`.
* Summary: stopped at 33.33%, curve through `2026-01-21T00:00:00`, 211 observations.
* Source and tests at HEAD `960a469fee5776544df1a3bfeb7b84fb3c8eeacf`; the three audited code/test
  files have no diff from that commit. Focused result: `PYTHONPATH=. pytest -q
  backend/tests/test_anchor_execution_contract.py` -> 20 passed.

For every row below I independently checked `NAV = cash + sum(quantity * snapshot mark)`; differences
were at most floating-point noise (`2e-12`).

## Plan and order reconstruction

The wall-clock plan/order lines map to simulated `2026-01-19 15:00:00`:

```text
ANCHOR PLAN ... UUUU:s2:p7 target=$1321 current=$1166 need=$155 planned=$155
UUUU @ 2026-01-19 15:00:00 ($21.97): buy ...
Broker single-position cap ... existing=$1166.06, cap=20%=$1321.49
ANCHOR ORDER ... requested=$155.43 cash_to_use=$155.43 fundable=$155.43
```

| state | NAV | mark | held/projected qty | position value | weight |
|---|---:|---:|---:|---:|---:|
| planner current | $6,607.445823 | $21.970000 | 53.075087665984 | $1,166.059676 | 17.647662% |
| accepted order, holdings still unchanged | $6,607.445823 | $21.970000 | 53.075087665984 | $1,166.059676 | 17.647662% |
| projected after the order at the decision mark | $6,607.445823 | $21.970000 | 60.133326059035 | $1,321.129174 | 19.994552% |

The exact 20% cap was `$6,607.4458229253705 * 0.20 = $1,321.4891645850741`.
Current marked value was `$1,166.0596760216663`, leaving `$155.4294885634079` headroom. The log
rounds all three to cents/whole dollars. Because execution is next-event, order acceptance did not
change the held 53.075087665984 shares. The cost-aware order quantity was 7.058238393051 shares;
at the decision mid that quantity represented $155.069497, so the projected position was already
about $0.36 below the cap rather than above it.

The intervening equity observations through `2026-01-20T13:00:00` retain the same 53.075087665984
shares and $21.97 mark. NAV only creeps from $6,607.445823 to $6,607.469544 with cash interest; no
UUUU exposure is added until the next quote.

## Fill and every later snapshot

The correlated next-event execution was:

```text
ANCHOR FILL ... fill=$150.61 ... quantity=60.13332606 mark=$21.290000 position_value=$1280.24
ANCHOR STAGE PARTIAL ... remaining=$41.25
[execution] FILL BUY UUUU qty=7.05823839 ... price=21.338755 fees=0.004518
             quote=2026-01-20 14:00:00+00:00
```

| snapshot (simulated UTC) | NAV | UUUU mark | UUUU quantity | UUUU value | weight |
|---|---:|---:|---:|---:|---:|
| 2026-01-20 14:00 (fill) | $6,571.030923 | $21.290000 | 60.133326059035 | $1,280.238512 | **19.483069%** |
| 2026-01-20 15:00 | $6,635.343808 | $23.210000 | 60.133326059035 | $1,395.694498 | **21.034245%** |
| 2026-01-20 16:00 | $6,675.896941 | $23.390000 | 60.133326059035 | $1,406.518497 | 21.068607% |
| 2026-01-20 17:00 | $6,720.223877 | $23.750000 | 60.133326059035 | $1,428.166494 | 21.251770% |
| 2026-01-20 18:00 | $6,686.024286 | $23.710000 | 60.133326059035 | $1,425.761161 | 21.324499% |
| 2026-01-20 19:00 | $6,667.285082 | $23.940000 | 60.133326059035 | $1,439.591826 | 21.591874% |
| 2026-01-20 20:00 | $6,658.419583 | $23.930000 | 60.133326059035 | $1,438.990493 | **21.611592% max** |
| 2026-01-20 21:00 | $6,632.310972 | $23.500000 | 60.133326059035 | $1,413.133162 | 21.306799% |
| 2026-01-20 22:00 | $6,633.116398 | $23.500000 | 60.133326059035 | $1,413.133162 | 21.304212% |
| 2026-01-20 23:00 | $6,633.117455 | $23.500000 | 60.133326059035 | $1,413.133162 | 21.304208% |
| 2026-01-21 00:00 (terminal) | $6,633.118511 | $23.500000 | 60.133326059035 | $1,413.133162 | **21.304205%** |

There are exactly two UUUU execution fills in the whole log: the original 53.07508767-share entry
and this 7.05823839-share anchor add. There is no later UUUU order/fill, and no fill of any symbol
after this anchor fill. Quantity is byte-for-byte constant in every later snapshot. From 14:00 to
15:00 UUUU appreciated 9.018% (`$21.29 -> $23.21`), adding $115.455986 to its marked value, while
other positions lost a net ~$51.14. That combination, not a quantity increase, caused the first
20% crossing.

Even the nonstandard conservative check of valuing the entire post-fill quantity at the $21.338755
execution price gives $1,283.170312 / $6,571.030923 = 19.527686%, still below 20%.

## Marks, quantities, and the apparent dollar mismatch

There is no quantity mismatch:

`53.075087665984 pre + 7.058238393051 fill = 60.133326059035 post` exactly.

There is also no value-accounting mismatch. Three different prices/states are being shown:

1. The planner and broker use the decision mid `$21.97`, so planner `current=$1166` is
   `53.075087665984 * $21.97 = $1,166.059676`.
2. The execution line uses the next quote's modeled buy price `$21.338755`; fill notional is
   `7.058238393051 * $21.338755 = $150.614020`, plus `$0.004518` fees.
3. The fill/state line correctly marks all held shares at the next quote's mid `$21.29`, so
   `60.133326059035 * $21.29 = $1,280.238512`.

Thus `remaining=$41.25` is not supposed to equal `$155.43 requested - $150.61 fill`. It is the
stored dollar target minus the newly marked entire position:
`$1,321.49 - $1,280.238512 = $41.251488`. The difference decomposes into the old lot's $36.091060
mark decline, $4.815469 of requested notional left unused because quantity was fixed from the higher
decision price, $0.344124 between fill price and mid-mark value, plus sub-cent target rounding.

## What commit `960a469` actually contracts

The implementation checks concentration only while planning/admitting a buy:

* `graph_nexus_analysis.py:10888-10902` reads current marked `position_value`, caps the target at
  `portfolio_total * execution_cap_pct`, and plans only the difference.
* `broker.py:3542-3555` defines headroom from the **current** NAV, held shares, and supplied price.
* `broker.py:15913-15940` recalculates that headroom at the broker buy choke point and trims
  `cash_to_use` before order submission.
* `broker.py:11189-11234` reconciles a confirmed fill and recomputes stage completion at the current
  mark, but it does not enforce the cap, trim the fill, or schedule a later rebalance.
* `PortfolioEmulator.execute_signal` fixes quantity from the decision price and executes it on a later
  quote. The notional limit bounds cash spent; it is not a fresh weight cap.

The focused tests pin planning/headroom and next-event identity/stage accounting. They contain no
continuous-weight, later-appreciation, or fill-gap cap test. Accordingly, "final-position" in two
broker comments/docstrings can only safely mean the position projected at buy admission; it is not an
implemented promise that every later snapshot remains below the percentage. It is not even a strict
fill-time guarantee if the next event gaps or the rest of NAV moves, although this run's actual fill
was safely below the limit.

## Minimal justified fix

No trading-behavior fix is justified by this fill: it did what the admission cap currently does.
The minimal correction is contract/documentation clarity:

1. Describe `anchor_reinforce_execution_max_position_pct` as a **decision-time buy-admission/current-
   mark limit**, explicitly not a continuous rebalance limit and not a strict next-event fill limit.
2. Report future verification separately as `order admission`, `post-fill weight`, and
   `terminal/continuous drift`; preserve bt 735390's preregistered fail rather than rewriting it.
3. Rename/clarify the two "final-position" comments/docstrings and add a focused price-gap test that
   pins whichever fill-time semantic is chosen. A small logging-only improvement would put NAV,
   resulting weight, and cap on `ANCHOR ORDER`/`ANCHOR FILL`, with an `ANCHOR CAP DRIFT` diagnostic
   if a fill arrives above the admission cap.

If <=20% at every snapshot is truly required, that is a separate continuous concentration/rebalance
policy with trim thresholds, transaction-cost and turnover accounting. It should not be smuggled in
as a reinterpretation of this order-sizing flag; it is larger than the minimal fix supported by bt
735390.
