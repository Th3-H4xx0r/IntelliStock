# iter8 — momentum-rotated book on the engine-confirmed champion

## Pre-registered bar (stated before results)

roll-12m ≥ **95%** AND all three bears ≥ 0 AND every margin ≥ **1.5pp beyond
replay noise** (rolling ±7pp; bears +3…8pp optimistic).

## Pass count: **0 of 72.**

Grid: menu{GLD/GDX/XLE, +UNG, +UNG+SMH, +BIL} × M{42,63,126} × k{1,2} ×
min-hold{0,4 weeks} × weighting{equal, champion-base renormalised, k=2 only}.
Core and damp frozen at the engine champion (tv .20, 10/40, wmax .65, QQQ,
N25, 1/2%, damp 0). 73 runs in 1.6 s.

**Simulator.** `core8.py` = iter6/core6 with UNG+BIL columns added and a
per-step book vector; a book change is a trade trigger exactly as a trend flip
is. On the identical static config it reproduces core6 to **0.0000pp** across
all 19 windows. Replay-vs-engine on the champion: cycle +224.0 (engine +233.8),
roll-12m 95.9% (engine 92.6%), maxDD −27.6% (engine −27.5%), bears
+0.70/+3.88/−0.28 (engine +0.55/+0.35/+6.18). **The replay is +3.3pp optimistic
on rolling and mis-signs rb3** — so every level below is scored as a paired
delta against this same-simulator baseline, never as an absolute.

## The two frontier points

| | STATIC (baseline) | **T8a** M126/k2/h4/base | **T8b** M42/k2/h0/base |
|---|---|---|---|
| roll 3m / 6m / 12m | 66.9 / 79.0 / **95.9%** | 68.0 / 81.3 / **98.9%** | 67.2 / 79.4 / 93.7% |
| bears rb1/rb2/rb3 | +0.70/+3.88/−0.28 (2/3) | +6.37/**−4.47**/+6.52 (2/3) | +6.75/+3.28/+6.52 (**3/3**) |
| 19-window wins | 15/19 | 15/19 | 15/19 |
| **cluster A** (end 2023-02..06) | 75/104 | **98/104** | 82/104 |
| **cluster B** (end 2024-09..2025-03) | 134/144 | **141/144** | 134/144 |
| cycle margin | +145.3pp | +177.0pp | +165.3pp |
| CAGR / maxDD | 27.63% / −27.6% | 30.13% / −22.6% | 29.23% / −23.7% |
| turnover | 437%/yr (36%/mo) | **734%/yr (61%/mo)** | **1212%/yr (101%/mo)** |
| cost drag | 0.40pp/yr | 0.79pp/yr | 1.55pp/yr |
| book changes / 4.8yr | 0 | 16 | 44 |
| **gold+energy-neutralised margin** | **+10.5pp** | **+11.8pp** | **+11.8pp** |
| 2010-06→2021-12 CAGR | +9.44%/yr | **+8.75%/yr** | +10.65%/yr |

Both loss clusters improve, as predicted, and T8a fixes cluster A almost
completely (72% → 94% of windows). Four results kill it anyway.

## Why it fails, four ways

**1. The gain is a gold bet, not a timing edge.** Neutralising GLD/GDX/XLE/UNG/
SMH → SPY collapses the rotation's advantage from **+31.7pp to +1.3pp**. Only
**4% of the apparent gain survives**; neutralised bears are identical to the
baseline's to 0.03pp (−21.33 vs −21.36). Concentrating into the momentum leader
during a historic gold bull (GDX +142.7%, GLD +61.5% in cal-2025 alone) is what
the ranker actually does. There is no rotation alpha underneath.

**2. M=126 is a spike, not a plateau.** Swept one step at a time
(21…252), roll-12m is 69.8 / 87.8 / 89.6 / 92.2 / 89.8 / **98.9** / 83.6 / 76.0
/ 88.6 / 82.9. M=126 sits **6.6pp above its best neighbour** and 10.6pp above
the median; the sd across M is **8.3pp** against a gain over baseline of
**+3.0pp** — 0.36 sd. Cluster A is worse: 98/104 at M=126, **13/104 at M=147**.

The decisive control: **the mechanism is stable while the score is not.** XLE's
share of selected legs through the Mar–Oct 2022 gold crash is exactly 50% for
every M from 63 to 252 — the rotation reliably does the thing it was designed
to do — yet roll-12m over those same M values ranges 76.0–98.9%. M=126 is a
scoring accident. (min-hold, by contrast, *is* a plateau: h∈{2,4,6,8} → 97.4 /
98.9 / 97.5 / 95.5%.)

**3. Concentration deletes the ballast that carries the next bear.** rb2 flips
+3.88 → **−4.47**. Forensics: Feb–Apr 2026 GLD +2.50%, GDX +1.93%, **XLE
+17.82%**, SPY −5.78%. T8a was 454 days into a GDX+GLD spell and held XLE for
only 12% of legs; the baseline's permanent 25% XLE is precisely what makes rb2
positive. Momentum concentrates into what just worked and drops the diversifier
that pays next. Across M, XLE's rb2 share falls monotonically 46% → 0% as the
lookback lengthens — the trade-off is structural, not tunable away.

**4. Turnover.** T8a runs **61%/mo**, T8b **101%/mo**, against the baseline's
36%/mo and this program's own Novy-Marx threshold of <50%/mo. Cost drag doubles
(0.40 → 0.79pp/yr) or quadruples (1.55pp/yr). T8b buys 3/3 bears with 2.8× the
baseline's turnover *and* a rolling-12m that is **below** baseline (93.7 < 95.9).

## The three named skeptical checks

- **Does it pick XLE through Mar–Oct 2022?** **Yes, robustly.** XLE = 50% of
  legs for every M ≥ 63, when XLE returned +26.2% against GLD −16.4% and GDX
  −32.6%. This half of the thesis is confirmed.
- **Does 63d lag the 2025 gold rally?** **No.** M=63 is 90% gold-complex through
  cal-2025 and M=126 is **100%** — already inside a spell running 2024-12-19 →
  2026-03-18. Lookback lag is not the binding constraint.
- **Is UNG a landmine?** **Yes, unambiguously.** Across all 36 UNG-bearing
  configs, UNG's compounded return *over exactly the sessions it was selected*
  is negative in **36 of 36**, ranging **−25.7% to −77.8%** (median ≈ −59%),
  while held 16–36% of the time. Momentum selection does not rescue a
  contango-decayed asset; it buys the bounce and holds the decay. Every
  UNG menu is worse than the M3 menu on every axis. SMH (M5US) is worse still —
  bears down to −34.9%, maxDD −67.2%. **BIL adds nothing** (best M4B roll-12m
  81.5%). The winning menu is the champion's own three names.

## Best (roll, bears) points, reported regardless of the bar

- max roll-12m, and max cluster wins: **T8a**, 98.9%, but 2/3 bears.
- max roll-12m subject to 3/3 bears: **T8b**, 93.7% — *below* the baseline.
- max min-bear subject to roll ≥ 95%: T8a again (−4.47%); nothing clears both.
- max cycle margin: M42/k2/h0/eq, +204.4pp, 3/3 bears, but roll 92.4% and
  **1170%/yr turnover**.

Equal-weight beats base-renorm on cycle margin everywhere and loses on rolling;
neither is a mechanism, both are the same gold bet at different concentrations.

## Verdict

**Do not build this.** The rotation improves both pre-registered loss clusters
and the headline rolling number, but 96% of the gain is gold concentration, the
single best cell is a 6.6pp spike between two much worse neighbours, it breaks
a bear the champion already wins, it doubles turnover past the program's own
churn threshold, and it is **worse out of design sample** (+8.75%/yr vs the
static book's +9.44%/yr, both far under SPY's +15.94%). It is also **not
expressible in config** — `trend_on_book`/`trend_off_book` are static dicts, so
unlike every prior iteration this needs new engine code to test at all. Spending
that build on a 0.36-sd result is not warranted. **The engine-confirmed static
champion stands.**

*Scripts: `iter8/{core8,validate8,sweep8,diag8,extra8,probe8}.py`; logs
`iter8/{validate,sweep,diag,extra,probe8}_log.txt`; grid `rows8.pkl`;
`detail8.json`, `best8.json`.*
