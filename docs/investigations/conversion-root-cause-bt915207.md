# Why the #1 name did not get bought — bt 915207

Date: 2026-08-12
Run: bt **915207**, `finished`, 2026-01-01..2026-03-01, $6,000, 3600s (reference window)
Log: 41,184 lines pulled with `scripts/pull_backtest_logs.py`
Status: diagnosis from a real run. No config was changed and no run was launched.

## Finding

The satellite budget is allocated **chronologically, not by conviction**. Names that appear at
early bars take ~12.7% of NAV each. By bar 6 the satellite is at/over its design share, the
conviction-overflow path collapses to ~zero, and every later name — including the winners — is
trimmed to sub-1% of NAV.

This is the conversion gap. It is not discovery, not ranking, and not the entry gates.

## The evidence table

Per-name funded weight versus what the name actually did in the window:

| symbol | move in window | NAV% funded | outcome |
|---|---:|---:|---|
| AAL | **-16.1%** | **12.68%** | full size in a loser |
| AMCR | 0.0% | 12.68% | full size, flat |
| CFG | 0.0% | 12.65% | full size, flat |
| USPH | 0.0% | 12.65% | full size, flat |
| LLY | +3.6% | 11.45% | full size, flat |
| GBDC | 0.0% | 11.45% | full size, flat |
| AMAT | +32.3% | 11.30% | full size, worked |
| NVDA | +2.9% | 0.48% | starved |
| **SNDK** | **+170.2%** | **0.48%** | **starved** |
| AAOI | +104.1% | entry-limited | starved |

`SNDK` at 12.68% instead of 0.48% is worth roughly **+21.6% of NAV** from that one name.
As funded it contributed about **+0.8%**.

## The refusal chain, verbatim

```
[core] funding request trimmed $2,712 -> $29 — satellite headroom will refuse the remainder;
       releasing core for it would only be bought back
SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 — funding $29 of room out of the core (floor-bounded)
SATELLITE CAP: SNDK trimmed $904 -> $29 to keep the core at target
Buy gate inputs for SNDK: cash=$113.14 ... high_conv=True open_pos=6 cash_per_trade=$29.11
```

SNDK was discovered, ranked, marked high-conviction, cleared the `raw >= 1.50` overflow
threshold, and was allocated **$904**. It received **$29** — 0.48% of a $6,000 account.

In the same bar, `AAOI` (+104%) is `action_intent=hold` and `AAL` keeps its 12.68%.

## Conviction overflow dies after bar 5

Core funding requested versus delivered, per event:

| bar | requested | delivered | delivered % |
|---:|---:|---:|---:|
| 1 | $2,517 | $1,050 | 41.7% |
| 2 | $1,703 | $759 | 44.6% |
| 3 | $2,573 | $761 | 29.6% |
| 4 | $1,712 | $687 | 40.1% |
| 5 | $2,573 | $678 | 26.4% |
| 6 | $3,459 | $12 | 0.3% |
| 7 | $3,463 | $21 | 0.6% |
| 8 | $2,605 | $19 | 0.7% |
| 9-13 | $13,284 | $0 | 0.0% |
| 14 | $2,712 | $29 | 1.1% | <- SNDK, NVDA
| 15 | $210 | $0 | 0.0% |
| 16 | $917 | $51 | 5.6% | <- RVLV

* first 5 bars: **$3,935 of $11,078 delivered (36%)**
* bars 6+: **$132 of $26,650 delivered (0.5%)**

Across all 14 `SATELLITE CAP ... trimmed` events the book asked for **$12,126** and received
**$5,930 (48.9%)**, and the shortfall is concentrated entirely on the late-arriving winners.

## Root cause

1. Satellite headroom is measured against a **design share**, and it is consumed
   first-come-first-served across bars.
2. Once headroom is negative (`$-617 room` observed), the only remaining funding path is
   conviction overflow out of the core.
3. That path is **floor-bounded** and is refused with
   `releasing core for it would only be bought back` — so it delivers ~0.5% of requests after
   bar 5.
4. There is **no trim-back**: nothing reclaims AAL's 12.68% when SNDK appears with raw +1.700.

Point 4 is objective blocker (3) and is the failing-on-purpose `test_A11`
(`test_A11_an_appreciation_overrun_never_recovers`, "core target pinned at 30% with no
mechanism to reduce the satellite").

## What this rules out

* Not a discovery failure — SNDK, AAOI, AMAT were all found and ranked.
* Not the ranking score — SNDK carried `raw=+1.700`, above the 1.50 overflow threshold.
* Not the entry-extension gate, turnover exemptions, or `max_positions` — all in the
  measured do-not-retry list, and none of them is the binder in these lines.
* Not cash starvation per se — `cash=$113.14` was available and `cash_per_trade` was
  computed at **$29.11**. The allocator, not the wallet, refused.

## Implied next lever (design only, not implemented)

Conviction-ranked displacement inside the satellite: when a candidate clears the overflow
threshold and headroom is negative, free room by trimming the **weakest-conviction existing
satellite position** rather than refusing the candidate or raiding the core.

Constraints that must hold:
* default-OFF, enabled per document;
* displacement is a satellite-internal swap, so it must not raise gross turnover materially
  (turnover is the known leak at ~290%/mo versus ~50%/mo break-even);
* it must not become per-bar churn — require a conviction gap and a minimum hold;
* it must not touch the bear path or add a bear regime profile to doc 193.

This lever is **not** in the measured do-not-retry list. It is the mechanism objective
blocker (3) names as missing.

## Caveats

* This run is `pit_mode=research` (lookahead). It diagnoses **mechanism**, not edge, and is
  not promotion-eligible.
* Window moves above are first-seen to last-seen quote prices inside the run, not realized P&L.
* No causal claim is made here: this is a single-arm mechanical reconciliation, and a funded
  versus starved comparison still requires the frozen paired-arm protocol.

## Addendum — the already-built lever that is switched off

`satellite_conviction_reserve_pct` was built for this exact failure (after bt 613166, where
SNDK got $168) and is covered by `backend/tests/test_satellite_conviction_reserve.py`.

**In doc 193 it is set to `0`.** It is inert.

A second lever is inert in the same way: `turnover_budget_conviction_bypass_enabled = True`
but `turnover_budget_conviction_bypass_max_pct = 0` — enabled with a zero budget.

### The arithmetic matches the log exactly

Live doc-193 shape is `core_target_pct 0.35` (bull/chop/recovery profiles), `core_min_pct 0.10`,
`cash_reserve_floor_pct 0.02`:

| quantity | value |
|---|---:|
| satellite design share | 0.630 = **$3,780** |
| satellite max share | 0.880 = $5,280 |
| conviction band | $1,500 |
| observed plain entry size | 12.68% NAV = **$761** |
| plain names needed to exhaust the design share | **$3,780 / $761 = 5.0** |

The log shows conviction overflow delivering real money for **exactly the first 5 bars**
(36% of requests) and then collapsing to **0.5% of requests from bar 6 onward**. Five plain
buys consume the design share; everything after that — SNDK, NVDA, RVLV — gets scraps.

That is a numeric match, not an analogy.

### What a reserve would change

| reserve | design share | conviction band | plain names before full |
|---:|---:|---:|---:|
| 0.00 (current) | $3,780 | $1,500 | **5.0** |
| 0.10 | $3,180 | $2,100 | 4.2 |
| **0.15** | **$2,880** | **$2,400** | **3.8** |
| 0.20 | $2,580 | $2,700 | 3.4 |
| 0.25 | $2,280 | $3,000 | 3.0 |

`0.15` leaves room for roughly three full-size conviction entries while still letting plain
buys build a base — closest to the objective's "four names at ~10% of NAV each".

The core target (0.35) and floor (0.10) are untouched by the reserve, so this is not a de-risk.

### Note on a stale test assumption

`test_satellite_conviction_reserve.py` calls `core_target_pct 0.35 / core_min_pct 0.25`
"the live doc-193 shape". The live floor is **0.10**, not 0.25, so the band figures in that
test ($600 band) do not describe the running config ($1,500 band). The lever's behaviour is
still correct; the documented example is out of date.

### Required validation before believing any of this

Per the hard-won rule, a unit test on a pure function is not proof a lever binds in a run.
Five levers have previously shipped "working" while the log showed nothing changed.

Paired arms, control `reserve=0` versus treatment `reserve=0.15`, same window/instance/
granularity/cash, at least three windows including one out-of-sample and one not led by
semiconductors. Accept only if the log shows a changed signature:

* `SATELLITE OVERFLOW` funding materially above $29-$51 at late bars;
* at least one discovered winner funded above 5% of NAV;
* `[core] funding request trimmed` delivering more than 0.5% after bar 5.

Treat any return difference below the 4.94pp noise floor as inconclusive, and check gross
turnover did not rise — turnover is the known leak.
