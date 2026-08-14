# Defect register — parallel log audit, 2026-08-14

Twelve independent agents, different logs, no shared context. Convergence across agents is the
evidence standard here: where several found the same thing from different angles, it is real.

## D1 — THE CONVERSION BUG. Satellite cap trims below the floor that then refuses. 6 agents.

`_CORE_MIN_SATELLITE_TRIM_USD = 25.0` (broker.py:3255) permits the cap to trim `cash_per_trade` to
any room above $25. 560 lines later `_exec_min_position_floor` (broker.py:3732) refuses anything
under `max($50, NAV*0.06)` ~ **$370**. The two numbers are **15x apart** and neither knows the other.

| finding | source |
|---|---|
| 47 trims, clipped to $154-$260 (median 3.07% NAV) vs floors $361-$383 | floor-consistency |
| **44 of 44 that reached the floor were below it and refused on the NEXT line** | floor-consistency |
| **zero trims ever filled** | floor-consistency |
| 32 of 68 SKIP BUYs were trimmed below the floor ($28,272 request -> $4,555) | nav-scope |
| 44/47 trims followed immediately by SKIP BUY; $67,416 blocked (72%) | entry-refusals |
| trim band $30-$170 never overlaps floor band $361-$382 | winners-missed |
| 5 of SNDK's 9 refusals | sndk-forensic |

`L7120 COP $869 -> $208` then `L7123 "< min $373"`. The core is SOLD to raise cash for these
($5,022 of SPY in one window) and the buy dies anyway.

**This is the answer to the objective's opening question.** A #1-ranked name is not refused by
judgement; it is sized into a band that cannot fill.

**Fix, and a warning.** `sndk-forensic` verified a one-liner by driving the real
`_exec_min_position_skips` on all nine logged SNDK tuples: lowering the floor when it exceeds
`cash_per_trade` flips 01-12/13/14/15/29 to BUY. **Do not ship that.** It admits $167 positions =
2.8% of NAV, and the objective is explicit that a +100% name at 2% of NAV is noise. The correct
repair is the opposite direction: trim to `max(floor, room)` when headroom allows, otherwise decline
the buy AND cancel the core release that was raising cash for it (nav-scope E3, floor-consistency 1).

## D2 — Core in-flight reservation invisible to the buy gate. 3 agents.

The gate logs/sizes on `get_cash()` (broker.py:16461); `execute_signal` clamps to
`get_buying_power(reserved)` (portfolio_emulator.py:1489-92). Reservations never expire
(portfolio_emulator.py:1566, `expire_after_quotes=0`).

Exact arithmetic, found independently twice: 01-19 `$1,299.21 - in-flight SPY $1,165.72 = $133.49`,
the precise "fundable" figure on which AMZN, SKYT and SNDK were all starved. 14 refusals; core took
88-91% of tick cash on those ticks.

This is already recorded in the project's own global memory as a known hazard and is still live.

## D3 — The core deploys only on ticks alpha cannot trade, then can't give the cash back. 2 agents.

All five `[core] bought` lines are 01:00-03:00 UTC ticks with `symbols=0`, "strategy idle", and the
turnover brake blocking every alpha buy — which the core is exempt from (broker.py:4184-86). Then
`broker.py:4148` passes `get_cash()` so `core_sleeve.py:587 need = req - cash` = 0 on every starved
tick, and the core cannot release. `core-first` warns the obvious fix is **unsafe alone**: gating the
deploy would have deployed the core zero times in this window.

## D4 — Rotation sells execute; the paired buy is refused. 2 agents.

01-16 "Momentum rotation: sell VICR -> buy SNDK $1081". VICR sold $1,077.85 (L12962). Both buys
refused (L12717, L12724). Zero buys that tick. VICR then ran +33.3%; SNDK was bought 19 days later
at +52%. Cost $541.12 versus intent, $374 versus doing nothing at all.
Also: `graph_nexus_analysis.py:31129` writes the momentum-watchlist score (1.113) into
`raw_net_score`, and `broker.py:15892-97` tests `>= 1.50`, so the rotation lane fails its own
conviction test and gets the design share instead of the overflow ceiling.

## D5 — Skipped buys are invisible downstream. 1 agent, high confidence.

Sibling refusal sites append to `_broker_skipped_buys` (15810/16135/16177/16617); the satellite cap
does not. That list feeds the backfill queue and next-bar scoring. Reason histogram in the logs is
only `insufficient_cash 68 / turnover_budget 8 / buy_price_floor 1` — **`satellite_cap` never
appears**, though 40 such skips occurred. 28 of 38 refused names were reported nowhere; 35 were
never bought.

## D6 — BEAR LEG: the hedge's first park kills the alpha book. 1 agent, exact ledger.

bt 624674 L3245 parks $4,182.96 of SQQQ = **70% of NAV**, with zero prior submissions. Eighteen
lines later the run's FIRST `TURNOVER BUDGET BINDING: 103% of NAV` fires. The ledger reconciles to
the cent: `XLE $899.34 + USO $894.44 + SQQQ $4,182.96 + $163.76 = $6,140.50 = 102.8%`. Without the
hedge it reads 30%. Result: 279 binding lines over 19 of 21 bars; the book ends at 2 of 6 slots.
The core is exempt from this ledger; the hedge is not (broker.py:3172).

**Confirmed correct in the same audit:** the core is OFF on all seven `[core]` bars, and the -10%
stop was armed and correctly never fired. doc-193's deliberate omission of a bear profile works as
designed.

## D7 — Buy-gate preview lies. 3 agents.

`broker.py:16456` hardcodes `_exec_min_pos_preview = 50.0` for the "-> PASS" log while the binding
floor is ~$375. **67 of 78 "PASS" lines are followed immediately by SKIP BUY (86%).** Diagnostic
only, but it is why this defect survived so long: the log said the gate passed.

## D8 — Cross-run state contamination. 1 agent.

Restart cleanup deletes only rows with `start_date >= date_key`
(graph_nexus_analysis.py:26071-75), so pre-window rows are immortal. Two runs of the same config
loaded 6 vs 99 trends; one logged "cooldown: 11 recently-sold" on day 1 with zero prior sells. This
is the mechanism behind the ~10pp same-config dispersion measured earlier, and behind AGQ appearing
in one run and not its twin.

Also: the sentiment cache persists 1 of 4 LLM outputs, so `_apply_trend_updates` is permanently
skipped on a cache hit (42 cache hits, 0 "LLM trends:" lines).

## Ruled out, with evidence

* **Turnover brake is not the constraint.** Binds on 96.4% of ticks but blocked only 7 orders ever,
  all in week one, while admitting 71 conviction bypasses. Two agents said explicitly: do not loosen
  it. Consistent with the objective's DO-NOT-RETRY list.
* **Winners are not sold early.** Median hold 53 of 57 days; the only two alpha exits were losers;
  the trailing stop fired 0 times and was suppressed 56 times.
* **Not max_positions** (held 6, cap 8), **not the alphabetical tie-break**, **not exits**.
