# Preregistration: does displacement help, hurt, or do nothing?

Written **before** the runs. Prompted by the operator's question after seeing bt 278531 report
+2.23% — a run that was `[stopped]` at ~70% progress, with three flags on and a warm salt, and
therefore not comparable to any completed run.

## Arms

| | control | treatment |
|---|---|---|
| doc | 194 | 195 |
| instance | `v2-conv-ctl` | `v2-conv-trt` |
| salt | `conv-ctl-194` | `conv-trt-195` |
| `satellite_displacement_enabled` | absent (off) | **True** |

Everything else identical, verified by config read: `satellite_conviction_reserve_pct = 0` in both,
`nexus_discovery_snapshot_enabled = False` in both, diagnostics off in both. Window
2026-01-01..2026-03-01, 3600s, $6,000. Both instances have five prior runs, so they are comparably
warm — the condition the objective requires ("equally warm or equally cold"). Runs are sequential;
a second concurrent launch silently preempts the first.

**Both arms must run to completion.** Three runs were killed early today for cost, and a stopped
run's return is meaningless — that is what produced the question this preregistration answers.

## Endpoints, fixed now

1. **Return** vs control, with +/-4.94pp as the noise floor.
2. **Max drawdown** — a >=4.94pp worsening is not offset by a return gain.
3. **Gross turnover** — a rise is disqualifying. Turnover is the known leak (~290%/mo live vs ~50%
   break-even). `CCK` was trimmed nine separate times in bt 278531; repeated shaving of one holding
   is the specific churn risk.
4. **Per-name funded NAV weight** — the objective wants fewer, larger positions.
5. **Mechanism** — `DISPLACEMENT EXECUTE` must appear, and the funded names must include at least
   one large mover. Absent that, any P&L difference is not attributable to this lever.

## Decision rule

Accept only if return is better by more than 4.94pp, drawdown does not worsen materially, and
turnover does not rise. Reject on any turnover increase regardless of return. If |return delta| is
under 4.94pp, the verdict is **noise**, not a win.

One window is not a result. If this pair passes, the same pair repeats on the bear window
(2026-03-02..03-30, safety veto) and the non-semiconductor window (2026-06-01..07-01, where the
strategy currently loses to SPY by 10.14pp) before anything is claimed.

## Prior expectation, recorded so it cannot be revised afterwards

Weakly positive on mechanism, unknown on P&L. In bt 278531 displacement funded `SNDK` (+174.7%) and
`WDC` (+71.8%), the window's two biggest movers, with a mean of +21.4% across 12 measurable funded
names. Only one trimmed name was measurable (`META`, +9.7%), so the cost side is unknown. A single
run with n=12 is not evidence, and displacement cannot reach the 84 of 103 movers that never
receive a buy intent at all.

## Not in scope

doc 193 is untouched and has none of these keys. doc 179 / `alpaca-main` is real money and is not
involved. All runs are `pit_mode=research` (lookahead) and not promotion-eligible.
