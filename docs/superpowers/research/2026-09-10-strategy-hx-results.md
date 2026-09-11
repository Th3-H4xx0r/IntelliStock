# Strategy HX — engine results (2026-09-10)

Spec: `docs/superpowers/specs/2026-09-10-strategy-hx-design.md`. Engine runs via
`scripts/hx_run_native_api.py` on instance `strategy-hx-lab` (doc 202), daily
stepping, `etf-liquid` cost preset, starting cash 6000, code at 88a9e12.
Archives: `output/research/hx-2026-09-10/<variant>-<window>-<HHMMSS>/`.

## Verdict against the frozen gate: FAIL

HX passes the bear gate on two of three windows and beats SPY on all three
bears, then loses to SPY in every chop window and over the cycle. The fast
trigger that makes it positive in bears fires on healthy pullbacks in bull
and chop, and the inverse leg bleeds: PSQ lost $1,354 over the cycle.

## Bear windows (gate 1: positive P&L; gate 2: beats SPY)

| window | dates | SPY | V1 PSQ 60/BIL 40 | beats SPY | positive | max DD |
|---|---|---|---|---|---|---|
| rb1 | 2022-01-01→06-30 | −19.40% | **+0.18%** | +19.6 pts | yes | 11.8% |
| rb2 | 2026-02-01→04-01 | −5.84% | **+2.79%** | +8.6 pts | yes | 2.1% |
| rb3 | 2025-02-15→04-15 | −11.40% | −2.67% | +8.7 pts | **no** | 8.4% |

rb3, all five pre-registered bear books (the window opens on the Feb 19 2025
peak; the core lost ~$336 in the seven sessions before the trigger fired):

| variant | bear book | rb3 | max DD |
|---|---|---|---|
| V1 | PSQ 0.60 / BIL 0.40 | −2.67% | 8.4% |
| V2 | SQQQ 0.25 / BIL 0.75 | −3.05% | 8.4% |
| V3 | SH 0.60 / BIL 0.40 | −2.45% | 6.7% |
| V4 | GLD 0.30 / BIL 0.70 | −3.97% | 6.2% |
| V5 | PSQ 0.40 / GLD 0.20 / BIL 0.40 | −2.21% | 6.5% |

No bear book turns rb3 positive. The loss is the levered core before the
flip, not the hedge.

## Chop, bull, cycle (V1)

| window | regime | SPY | HX | delta | max DD | attribution |
|---|---|---|---|---|---|---|
| rc1 | chop 2025-11-10→2026-02-24 | +2.08% | −11.98% | −14.1 | 13.8% | PSQ −158, core −551 |
| rc2 | chop 2022-07-01→12-31 | +2.24% | −6.14% | −8.4 | 13.9% | PSQ +188, core −542 |
| rc3 | chop 2024-07-01→10-31 | +7.03% | −7.69% | −14.7 | 17.1% | PSQ −165, core −289 |
| ru1 | bull 2023-01-01→07-31 | n/a | +40.22% | | 8.5% | core +2,466 |
| ru3 | bull 2024-01-01→06-30 | n/a | +8.12% | | 11.7% | PSQ −272 |
| cyc | 2021-11-01→2026-08-27 | +77.11% | +19.14% | −58.0 | 21.2% | PSQ −1,354, core +2,572 |

## What the numbers say

- The hedge works: in the three bears PSQ earned $181, $209 and $518.
- The trigger is the problem outside bears. Every chop window flips into the
  bear book on a pullback, then exits after the slow confirm, paying both the
  inverse decay and the re-entry. rc1 flipped repeatedly (44 trades in three
  months) and never held a gain.
- A two-month bear that opens at the peak (rb3) cannot be positive for any
  long-core strategy that reacts to price; the first week is lost before any
  signal exists. That is the same "needs foresight" conclusion recorded on
  2026-09-03.

## Fallback

The spec's fallback is a three-state classifier on slow moving averages. It
would whipsaw less in chop but, by construction, be later into every bear
and therefore lose rb2 and rb3 outright. It does not close the gap the gate
requires; it moves the failure from chop to bear. Not started.

## Runs

703933 V1 rb3 · 100682 V2 rb3 · 971021 V3 rb3 · 400863 V4 rb3 · 945820 V5 rb3 ·
676519 V1 rb2 · 659874 V1 rb1 (a duplicate rb1 run produced the identical
result) · rc1/rc2/rc3/ru1/ru3/cyc V1 ids in the archive `request.json` files.
