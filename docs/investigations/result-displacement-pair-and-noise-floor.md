# Why bt 523085 is "low", and what that costs every earlier verdict

## The 10pp gap is one name

bt 873929 and bt 523085 are the **same instance, window, cash and granularity** (`v2-conv-ctl`,
2026-01-01..2026-03-01, 3600s, $6,000) with the same config. They returned **+16.41%** and
**+6.00%**.

The whole difference is `AGQ`:

| | bt 873929 | bt 523085 |
|---|---|---|
| log lines mentioning AGQ | **627** | **0** |
| AGQ P&L | **+$1,111.28 (113% of total)** | never held |
| return | +16.41% | +6.00% |

AGQ entered 873929 as a *discovered trend ETF*, keyed off stored Nexus trends
(`v2-conv-ctl|4f430a0ae8cdd108951ff2c3_price_pslv`). Six runs later that trend no longer fires, so
AGQ never enters the universe at all. Nothing was refused — it was never seen.

**bt 523085 is not a regression. bt 873929 was the lucky draw**, and its entire result was one
lottery ticket handed over by shared mutable Nexus state.

## What this costs

The objective warns that Nexus state is shared and mutable. Measured, that hazard is worth about
**10pp of return on identical configuration**. The working noise floor in this project has been
**4.94pp** — roughly half the real dispersion.

Everything judged against 4.94pp is therefore underpowered and must be treated as unproven:

* the conviction-reserve pair "rejection" (median -0.62pp over 4 windows) measured nothing;
* the W1 OOS "noise" call (+3.44pp) was correct by luck, not by power;
* the chained "+41.26% over 4 windows = +129%/yr" figure inherits AGQ and **is withdrawn** — it is
  not reproducible on the same configuration.

A paired design cannot fix this. Two arms drawing different lottery tickets differ by more than any
lever being tested. Either the state is frozen, or verdicts need many more repetitions than one run
per arm.

## The displacement pair itself

Preregistered in `prereg-displacement-pair.md` before either run. doc 194 vs doc 195, differing in
one operative key, both run to completion.

| endpoint | control 523085 | treatment 102463 | direction |
|---|---:|---:|---|
| return | +6.00% | **+11.12%** | +5.12pp |
| max drawdown | 7.8% | **4.6%** | better |
| trades | 32 | **16** | halved |
| core lane gross | 1.80x NAV | **0.96x NAV** | halved |
| round trips | 5 | 2 | fewer |

`DISPLACEMENT EXECUTE` fired 24 times, so the lever genuinely acted. `AMAT` — one of the eight
named winners — was bought and contributed +$272.37. Neither arm saw AGQ, so this pair is not
contaminated by that specific draw.

**Verdict: NOT a return win.** +5.12pp sits below the ~10pp same-config dispersion just measured,
so by the corrected standard it is noise, and the preregistered rule was written against a floor now
known to be too small.

What is *not* explained by noise is the turnover result. Trades halved, gross core-lane exposure
halved, drawdown fell by 3.2pp, and the mechanism is documented (24 executions funding buys from
existing holdings rather than opening fresh positions). Turnover is the objective's known leak
(~290%/mo live against ~50%/mo break-even), and this is the first change measured to move it in the
right direction while return did not fall.

That is a reason to test it properly, not a reason to enable it.

## Next

1. Repeat this pair 3-5 times per arm on the same window to estimate dispersion directly, or freeze
   Nexus state so a single pair means something. Until then no lever can be accepted or rejected on
   one pair.
2. Re-open the conviction reserve as **undecided**, not rejected.
3. Do not quote the +129%/yr chained figure again.

## Sizing is no longer the blocker; holding and selection are

Measured from the fill lines of both arms (`FILL BUY sym qty= price=`), cumulative buy notional per
name as a share of starting NAV:

| | control 523085 | treatment 102463 |
|---|---:|---:|
| names bought | 12 | 9 |
| per-name entry notional, median | **14.00%** | **14.00%** |
| per-name mean | 21.03% | 18.39% |
| SPY core, cumulative | 95.94% | **54.01%** |
| `TURNOVER BUDGET BINDING` lines | 611 | **314** |
| buys / sells | 16B / 16S | **11B / 5S** |

Two things follow.

**Blocker (2) reads differently now.** The objective records per-name satellite weight as mean 6.75%
/ median 4.73% of NAV. In both of these runs the median entry is **14.00%** — inside the 10-15% band
the objective asks for, and evidently a cap rather than an accident (the value is exactly 14.00 for
most names). Sizing is not what is holding the result back here.

Caveat: this is *cumulative* buy notional per name, so a name bought repeatedly sums above its true
position weight — that is why SPY shows 95.94%. For the satellites, most sit at a single 14.00%
entry, so the median is a fair read of entry size.

**So why did no name move the needle?** Both summaries say `NO single name moved the needle`, with
the top contributor at +4.9% (control) and +4.5% (treatment) of starting NAV. A 14% position
returning ~35% produces ~4.9%. The positions are large enough; the names either did not move far
enough or were not held through the move. That points at selection and holding, not sizing.

**The core is finally being spent as designed.** The objective says the SPY core is meant to be SOLD
to fund high-conviction buys. Control put 95.94% of NAV cumulatively through SPY; the treatment arm
halved that to 54.01% while buying `AMAT` at 14.0%, `URA` at 14.0%, `CCK` at 14.2%. The
buy/sell ratio also shifts from 16B/16S to 11B/5S — the treatment arm accumulates and holds rather
than churning, which is the behaviour the objective asks for.

None of this is a return claim. The +5.12pp return gap remains inside the ~10pp same-config
dispersion measured above.

## Capture: the names it does buy are now caught well; there are too few of them

Per bought name, from the fills and the run's own quote stream: the move available from entry price
to the subsequent peak, versus what was actually realised at exit (or mark at window end).

**Control 523085**

| name | entry | available | captured | share |
|---|---|---:|---:|---:|
| VICR | 2026-01-02 | +71.0% | +28.3% | 39.9% |
| SBLK | 2026-01-02 | +32.3% | +32.3% | **100.0%** |
| BALL | 2026-01-06 | +23.1% | +2.7% | 11.8% |
| SNDK | 2026-02-04 | +10.7% | +3.9% | 36.2% |

Median capture **38.1%**.

**Treatment 102463**

| name | entry | available | captured | share |
|---|---|---:|---:|---:|
| AMAT | 2026-01-08 | +39.8% | +32.4% | **81.2%** |
| TSLA | 2026-01-02 | -4.0% | -4.9% | n/a |

Three things fall out.

**`VICR` and `AMAT` are being bought.** The objective lists both among eight winners that "none were
bought". In these runs `VICR` is entered on the second trading day of the window and captures 39.9%
of a 71% move at 14% of NAV, and `AMAT` captures 81.2% of a 39.8% move at 14.0% of NAV. Whatever
was refusing them before is not refusing them here.

**`SNDK` still enters late.** Entry 2026-02-04 with only +10.7% left to peak, consistent with the
earlier finding that it signals on 01-19 at $413.60 and fills on 02-02 at $660.48 — 94.9% through
its move. Blocker (1), entry timing, is real and unfixed.

**The arithmetic explains the modest return.** The objective's target is four names at ~10% of NAV
each capturing half of a 60% move. Sizing is there (14%) and capture on the good entries is there
(40-100%), but only **two to four names** had a meaningful move available at all. The binding
constraint is breadth of conversion — the 103 -> 19 -> 9 -> 7 funnel — not size and not capture.

That is consistent with everything else measured today: the promotion path that would let a
no-news, no-graph-edge mover become a candidate never fires (2,922 skips, zero promotions).

## The funnel collapse reproduces across four independent runs

Same measurement, taken from each run's own quote stream and fills: names that moved >=30% from
their first quote to their subsequent peak, how many ever produced a `buy` action intent, and how
many were actually filled.

| run | window | moved >=30% | buy intent | bought |
|---|---|---:|---:|---:|
| bt 873929 | W0 ref | 103 | 19 (18%) | 7 (7%) |
| bt 553341 | W3 non-semi | 65 | 16 (25%) | 1 (2%) |
| bt 523085 | W0 control | 57 | 10 (18%) | 3 (5%) |
| bt 102463 | W0 treatment | 53 | 11 (21%) | 1 (2%) |

Across four runs spanning two windows, two instances and both arms of a paired test, **79-82% of
names that made a >=30% move never produce a buy intent at all**. The share that reaches a fill is
2-7%.

This is no longer a one-window observation. It is the stable shape of the system, and it is
upstream of every lever tested in this project: ordering, conviction reserve, displacement and
sizing all operate on the 10-19 names that do reach portfolio construction, never on the ~45 that
do not.

It also bounds what the objective's arithmetic can produce today. Four names at 14% of NAV each
capturing half of a 60% move requires four such names to be *bought*; these runs buy one to three,
because that is all that survives to the buy gate.

The mechanism that should promote a no-news, no-graph-edge mover into candidacy is the breakout
rescue, and it is measured dead: 2,922 evaluations, 100% exiting at `bars=0`, zero promotions,
covering six of the eight winners the objective names.

## Where the lost movers are lost: never scored, not refused

In bt 102463, 53 names moved >=30% and 42 never produced a buy intent. Splitting those 42 by
whether the name ever carried a score anywhere in the run (223 symbols did):

| fate | count | share |
|---|---:|---:|
| **never scored at all** | **36** | 86% |
| scored, but never produced a buy intent | 6 | 14% |

Among the never-scored are `AAOI` (+87.7%), `VIAV` (+68.9%) and `LASR` — three of the eight winners
the objective names — alongside `LITE` (+105.1%), `DTSS` (+95.7%), `ODYS` (+94.5%), `MRNA` (+81.8%)
and `UCTT` (+64.6%).

This is the sharpest statement of the problem available:

> For 86% of the large movers that fail to convert, portfolio construction never gets the chance to
> refuse them. They are never scored, so they are never candidates.

The objective's framing - "SNDK emitted 13 buy signals, all refused by portfolio construction" - is
accurate for `SNDK`, which is scored, proposed, and refused or filled late. It is **not** what
happens to the majority. Most large movers are lost one stage earlier, at scoring, exactly where
`_finalize_scores` leaves `fresh_score = 0` for a name with no LLM sentiment and no graph path, and
where the breakout rescue that should promote them is measured dead.

### Correction

A first pass at this reported "42 of 42 never scored" from a regex that took the first token of
lines like `Buy: GLD (Direct trend_momentum sentiment=+1 (raw=+1.000, 1 paths) | B), RTX (...)`,
which enumerate many symbols per line. That yielded 4 scored symbols in the whole run - implausible
on its face, which is what prompted the check. Parsing the enumerated form gives 223 scored symbols
and the 36/6 split above. The direction of the finding survives; the absolute claim did not.

## The complete chain, evidenced

Of the 36 large movers in bt 102463 that are never scored:

| | count |
|---|---:|
| **discovered by momentum** (`Discovered stock (momentum): SYM`) | **35 of 36** |
| blocked by the entry-extension gate | 4 |
| neither discovered nor blocked | 1 |

So the names are found, admitted to the universe, and have bars fetched — and then receive no score
at all. The extension gate explains only 4 of 36, and loosening it is a measured dead end (blocked
basket -7.95%), so it is not the lever here.

The full path, every step measured in this session:

1. **Discovery works.** 35 of 36 lost movers are discovered by momentum, e.g.
   `Discovered stock (momentum): AAOI (20d=+33.9%, 60d=+3.1%)`. The objective's claim that discovery
   already finds the winners is confirmed, not merely assumed.
2. **They enter the universe.** `Nexus discovered: expanding symbols with 120 new tickers: AAOI, ...`
   followed by `Backtest symbol expansion: fetching 1Hour history` and cached bar chunks.
3. **They are never scored.** `_finalize_scores` sets `fresh_score = 0` and only overwrites it if the
   symbol has LLM sentiment or a graph path. These names have neither.
4. **The rescue is dead.** `_compute_breakout_score_boost` exists to promote exactly this case on
   price action alone, and measures 2,922 evaluations, 100% exiting at `bars=0`, zero promotions,
   with 217 of the 396 skipped symbols demonstrably holding loaded bars.
5. **So they never reach portfolio construction**, which therefore never refuses them.

Everything built and tested in this project - buy ordering, conviction reserve, displacement,
sizing, trim-back - operates strictly downstream of step 5.

The single highest-value action remaining is the diagnostic already shipped and never run: set
`price_history_diagnostics_enabled=true` and read one line per tick,
`PH DIAG: map=N empty=N symbols_for_data=N data=N scored=N`. If `map << data`, the history map is
missing symbols that have bars; if `map ~= data` with a high empty count, the causal filter in
`get_price_history_up_to_current` is returning empty lists. Either answer identifies the fix for
step 4, which is the only step that can widen the funnel.

## The diagnostic ran, and it falsifies my entire line of investigation

bt 896168, `price_history_diagnostics_enabled=true`, 142 `PH DIAG` samples:

| quantity | result |
|---|---|
| `map == symbols_for_data` | **true on 142 of 142 lines** |
| `empty` (map entries with no bars) | **0 on every line** |
| `data - map` | constant **11** (mean 10.9, max 11) |
| map size | grows 1 -> 116 -> 135 -> **201** |
| `scored` (symbols passed to the scorer) | **0 on every line** |

Read against the discriminator written before the run:

* `map << data` would mean a membership gap. **It is not** - the gap is a constant 11 symbols, not
  the 217 that show `bars=0`.
* `map ~= data` with high `empty` would mean the causal filter in `get_price_history_up_to_current`
  returns empty lists. **It does not** - `empty` is 0 on every sample.

**The broker-side history map is healthy.** It mirrors `symbols_for_data` exactly, contains no empty
entries, and reaches 201 symbols. Every hypothesis advanced in this document about that map -
`breakout_min_history_bars`, widening to `data.keys()`, the discarding call sites, and the causal
filter - is now falsified. Four for four.

The `bars=0` skips are therefore **not** produced by the map measured here. `_compute_breakout_score_boost`
is called from `graph_nexus_analysis._finalize_scores`, which is a different call path from
`broker.run_run_once_strategies`; whatever history map that path receives is not this one.

### The unexpected result

`scored=0` on all 142 samples. The `symbols` argument passed to `run_run_once_strategies` is empty
on every tick of the run, while `price_history` carries up to 201 symbols. Whatever drives scoring,
it is not that argument. This was not the question the instrument was built to answer and it is the
most interesting thing it returned.

### What this costs and what it buys

Cost: the map investigation that occupied most of this session was aimed at a component that is
working. The measurements it produced (2,922 skips, 100% `bars=0`, 217 skipped symbols holding
bars, six of eight named winners in the skip set) all stand; the explanation attached to them was
wrong, and no fix was shipped on it - which is the one thing that went right.

Buys: the search is now correctly aimed. The next step is to instrument the *graph_nexus* side -
what `price_history` (if any) `_finalize_scores` passes into `_compute_breakout_score_boost` - and to
find out why the scorer is called with an empty symbol list.
