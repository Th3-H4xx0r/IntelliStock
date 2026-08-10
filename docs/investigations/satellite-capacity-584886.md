# satellite-capacity-584886 — the refusals in the non-semi window are CAPACITY, not error

**Written while bt 584886 was still in flight**, from its own log plus the cached bars.
Zero extra runs. Window `2026-06-01..2026-07-01`, v2-let-run-core, $6,000, 3600s.

---

## 0. WHAT IT LOOKS LIKE

Ten conviction names — every one of them in the top band, `raw >= 1.50` — were sized correctly at
~$840 (14% of NAV) and then cut to $82–$105. Three more were refused outright.

```
SATELLITE OVERFLOW: MRVL raw=+1.888 >= 1.50 — funding $105 of room out of the core (floor-bounded)
SATELLITE CAP:      MRVL trimmed $838 -> $105 to keep the core at target
SATELLITE CAP:      PANW skipped — satellite at its design share ($-1,427 room)
```

| | |
|---|---|
| conviction exposure requested | **$8,358** |
| funded | **$960 (11.5%)** |
| names trimmed to a runt | 10 (ALAB, ARCB, DELL, LSTR, MRVL×2, NVTS×2, VPG, WOLF×2) |
| names skipped outright | PANW ×2, WOLF |
| of those, ever filled | **none** |

`MRVL raw=+1.888` — **the highest conviction score in the entire run** — got $105, i.e. 12.5% of
its intended size, and then never filled at all, because $105 is below the `min_position_nav_pct`
0.06 floor ($360). AAPL at `raw=+1.800` got **$2,771** — twenty-six times more — for no reason
except that it arrived two days earlier.

The allocation is **temporal, not meritocratic**. That is objective blocker #3, quoted almost
word for word: *"a great name is refused while a mediocre one sits on the budget"*.

---

## 1. AND YET — THE OBVIOUS FIX WOULD NOT HAVE MADE MONEY

The tempting next move is displacement: rank the book, sell the weakest holding, fund MRVL. Before
building it, measure whether the refused names were actually better. Window returns, from
`scripts/benchmark_window.py` (cached bars, no run):

| refused | ret | | bought | ret |
|---|---|---|---|---|
| ALAB | **+33.90%** | | CRDO | **+26.61%** |
| MRVL | +24.34% | | NTLA | +25.66% |
| PANW | +17.15% | | IONS | +5.14% |
| VPG | +14.09% | | UBER | -1.63% |
| ARCB | -0.61% | | AAPL | -3.97% |
| CRWD | -1.12% | | NYAX | -6.41% |
| LSTR | -2.92% | | AVGO | **-21.18%** |
| DELL | -10.88% | | | |
| WOLF | -16.35% | | | |
| NVTS | **-33.47%** | | | |
| **mean** | **+2.41%** | | **mean** | **+3.46%** |
| **median** | -0.86% | | **median** | -1.63% |

**The refused basket did not beat the bought basket.** It was very slightly worse on the mean and
slightly better on the median — i.e. indistinguishable at n=10 vs n=7, against a noise floor the
repo already measures at >=4.94pp.

Worse for the displacement idea: within the conviction band, the score does not rank.

```
conviction raw score vs window return:  r = -0.235  (n = 15)
```

`NVTS raw=1.829 -> -33.47%` and `WOLF raw=1.836 -> -16.35%` scored HIGHER than
`ALAB raw=1.700 -> +33.90%`. This is the third independent confirmation of the objective's own
warning: **nothing separates winners from losers at entry. Do not build the filter.**

---

## 2. SO WHAT IS ACTUALLY HAPPENING

Reconstructed from the fills, the core is not being starved — it is being **spent, correctly**:

```
06-01  park $4,980 idle -> SPY (fills 3.45 sh = $2,613, cash-race clipped)
       BUY IONS $855, NTLA $826, UBER $837                    satellite ~$2,518
06-02  SELL SPY 2.51 sh ($1,901) -> BUY CRDO $748, AAPL $748  satellite ~$4,014
06-04  SELL SPY 0.42 sh ($318)   -> BUY NYAX $753, AVGO $407  satellite ~$5,174
       core now floor-bounded; every later conviction name gets $82-$105
```

Seven names at ~13-14% of NAV each, ~87% of the book in the satellite against a 63% design share,
funded by selling the core down to `core_min_pct = 0.10`. **That is exactly what the objective
asks for** — "four names at ~10% of NAV each", "size so one winner matters" — and it is what last
session's `core_min_pct 0.25 -> 0.10` fix was built to allow.

The refusals from 06-04 onward are therefore **the book being full**, not the book being stupid.
There is no capital left because the capital is already deployed at the intended size.

---

## 3. WHAT THIS CHANGES

1. **Do not build conviction-ranked displacement.** §1 is the evidence: the names it would promote
   are not better, and the score does not rank inside the band. Rotation was already measured at
   -3.04% for the same reason.
2. **The runt trim is still a defect worth removing, on cost grounds alone.** Ten orders were
   emitted at $82-$105 that could never fill (the 6% floor is $360). They cost log noise and gate
   work; more importantly the `SATELLITE OVERFLOW ... floor-bounded` line reads as if it funded
   something. If the fundable amount is below the min-position floor, the honest behaviour is to
   refuse with that reason, not to "trim" to an unfillable size.
3. **The interesting question is no longer WHICH names, it is HOW MANY.** The book committed 100%
   of its risk budget in 4 sessions, to the first 7 names that cleared the band, and then had no
   response to anything that happened in the remaining 18. Whether staging that deployment helps
   is a real, unanswered question — and unlike displacement it does not require ranking names
   against each other.

---

## 4. LIMITS

* One window, one run, n=17 names. Below the 4.94pp noise floor for any P&L claim.
* Window returns are measured 06-01 to 07-01, not from each name's refusal bar, so they overstate
  what was actually available to a buyer on 06-04+. They are an upper bound on the missed move.
* bt 584886 had not finished when this was written; §2's reconstruction is from its partial log.
