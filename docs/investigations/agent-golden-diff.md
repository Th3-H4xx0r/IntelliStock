# Golden diff: bt 873929 (+16.41%) vs bt 523085 (+6.00%), same config

**Scope.** Log-and-source only. No backtest was run, nothing was pushed. Sources:
`/tmp/bt873929.log` (run **A**, id 873929), `/tmp/bt523085.log` (run **B**, id 523085),
and `backend/` at the current working tree. Log citations are `A:<line>` / `B:<line>`
(1-based line numbers in the raw log files). Source citations are `file:line`.

---

## 0. Bottom line

**The premise "same config, different result" is true; the premise "same experiment" is false.**
The two runs did not evaluate the same universe, did not run the same LLM stack, and did
not start from the same persisted state. They share only 2 of 29 traded names.

The answer to *"does anything other than AGQ differ?"* is emphatically **yes, almost
everything differs.** AGQ is a *symptom* of the divergence, not the cause of it.

And the headline framing is backwards:

| | run A (873929) | run B (523085) |
|---|---|---|
| Stored P&L | **+$984.79 (+16.41%)** `A:39745` | **+$360.22 (+6.00%)** `B:40342` |
| Printed P&L | +$984.79 (+16.41%) `A:39686` | **+$366.10 (+6.10%)** `B:40297` |
| Distinct names traded | 19 | 12 |
| Total trades / buys / sells | 47 / 26 / 21 `A:39700` | 32 / 16 / 16 `B:40311` |
| Winners / losers (per-stock) | **7 / 12** | **8 / 4** |
| Sum of per-stock P&L | +$982.78 | +$358.49 |
| **Sum of per-stock P&L excluding AGQ** | **−$128.50** | **+$358.49** |

Run A's entire alpha is one leveraged silver ETF. **Strip AGQ and run A is the losing
run** (−$128.50 across 18 names, 6 winners / 12 losers), while run B is up $358.49 across
12 names at a 2:1 win rate. Any conclusion of the form "873929's process is better" is
not supported by these logs.

---

## 1. Validity checks performed first (the two traps)

Both traps the task flagged were checked and **neither applies** to the comparisons below:

* **Partial-window comparison — ruled out.** Both runs cover the identical calendar span
  and the identical bar count: 51 distinct dated bars (2026-01-01 → 2026-02-28, identical
  sets, symmetric difference empty), 677 `[Pending] 2026-…` broker ticks in each, and
  43 `Final scoring + ML overlay: start` scoring days on the *same 43 dates*. Every
  per-day comparison in this document is day-aligned on those 43 dates.
* **Empty/absent log lines mean "evaluated, no match" — respected.** Two examples that
  matter here. (a) `Discovery cooldown: …` is emitted only under
  `if _sold_cooldown:` (`backend/strategies/graph_nexus_analysis.py:27022`), so its
  absence on run A's 2026-01-01 means *the cooldown set was computed and was empty*, not
  that the cooldown was skipped. (b) All 24 `reason=` occurrences in A and 16 in B were
  inspected; **zero** have an empty reason string, so no "evaluated, no match" line was
  mistaken for a rejection anywhere in this analysis.

---

## 2. What is genuinely identical (the explicit "nothing differs" list)

These were checked and are **not** differentiators. Stating them so the divergence is not
over-attributed:

* **Config banner** — byte-identical `Effective config | ts=12.0/12.0/10.0 | pools=10/4 …`
  line in both (`A:18`, `B:18`), same strategy version string
  `V32-PHASE3-PATH12-BLACKLIST-HCFLOOR-CASHGATEDIAG-MCAPPREFILTER200M` (`A:16`, `B:16`).
* **Seeding** — both `RNG seed: 0 (BACKTEST_SEED env ('0'))` and
  `PYTHONHASHSEED=0 (confirmed)` (`A:1-2`, `B:1-2`).
* **Instance / scope** — same `scoped_instance_id=v2-conv-ctl|4f430a0ae8cdd108951ff2c3`
  (`A:19`, `B:19`) and same sentiment cache scope id `b2c22eda1ad3d975a420d880` (`A:14`, `B:14`).
* **Regime classification — bit-for-bit identical.** 43 `V31 market regime:` calls each,
  aggregating to **26 chop / 17 bull in both**, with **zero day on which the two runs
  disagree** on the regime label. The regime engine is exonerated.
* **Position-cap behaviour** — 634 `max_positions gate armed` lines in each, cap
  distribution A `{6: 592, 8: 40, 14: 2}` vs B `{6: 591, 8: 41, 14: 2}`. The only
  difference is the *ordering* of the first bar's cap publication on 2026-01-01
  (A's first armed line is `cap=6`, B's is `cap=8`). One-line ordering jitter, not a
  systematic difference.
* **Article intake** — both hit `Only 0 Alpaca articles (min 20)` on the first bar and
  both run the Google macro pipeline.

---

## 3. What actually differs — the causal chain, in order

### 3.1 Neither run ran a lookback. All day-one memory is cross-run residue.

Both logs say `Historic lookback prep: no eligible strategies — skipping.` (`A:7`, `B:7`)
and `… | lookback=NO | …` (`A:16`, `B:16`). Therefore **100% of the trend memory present
at 2026-01-01 was left in the shared RethinkDB by earlier runs**, in both cases.

The two runs did not inherit the same residue:

| | run A | run B |
|---|---|---|
| `Backtest restart: cleared N post-lookback rows` | **30** `A:21` | **382** `B:21` |
| `Market trends: N active trends loaded` (first bar) | **6** `A:32` | **99** `B:32` |
| Distinct trend ids referenced on 2026-01-01 | **4** | **60** |
| Trend ids on 2026-01-01 | `price_pslv`, `price_slv`, `price_cper`, `chip_price_surge` | `ai_investment_surge`, `roku_fox_acquisition`, `shipping_fleet_growth`, `defense_nato_expansion`, `helium_supply_crisis`, … (60) |

Run A woke up holding *silver and copper price-momentum trends*. Run B woke up holding
*sixty LLM-derived news trends from some other window*. Same instance, same date, same
config, same seed — different memory.

### 3.2 That residue is the entire reason AGQ exists in A and not in B.

The chain is fully traceable in one log page:

```
A:55  Discovered trend ETF: PSLV (trends: …_price_pslv, …_price_slv)
A:56  Discovered trend ETF: SLV  (trends: …_price_pslv, …_price_slv)
A:57  Discovered trend ETF: AGMI (trends: …_price_pslv, …_price_slv)
A:58  Discovered trend ETF: AGQ  (trends: …_price_pslv, …_price_slv)
A:64  Symbols expanded: 0 → 6 (includes 6 discovered)
```

AGQ enters run A's universe on bar 1, sourced *by name* from the two leaked silver price
trends. Run B has **zero occurrences of the string `AGQ` anywhere in 40,345 lines**, and
zero occurrences of `price_pslv`, `price_slv`, `price_cper`, `PSLV`, or the word
"silver". AGQ was **never evaluated and rejected in B — it never entered the universe**,
because the trend that would have introduced it was not in B's leaked state.

Run B's bar 1 instead reads:

```
B:193 Symbols expanded: 0 → 156 (includes 156 discovered)
      Discovered stock: AAPL (from trends: …_ai_investment_surge)
      Discovered stock: AMZN (from trends: …_ai_investment_surge, …_ai_power_infrastructure, …)
      Discovered stock: … 126 names
```

Every one of run B's eventual holdings traces to a leaked trend: `roku_fox_acquisition`
→ ROKU (−15.52%), `shipping_fleet_growth` → SBLK (+34.89%), `ai_power_infrastructure`
→ AMZN/ETN/DTE/RTX.

### 3.3 Universe size diverges on bar 1 by 6.3× and never reconverges.

`Final scoring + ML overlay: start | symbols=N`, day-aligned across the 43 shared
scoring days:

| date | A | B |
|---|---|---|
| 2026-01-01 | **30** | **190** |
| 2026-01-02 | 132 | 197 |
| 2026-01-15 | 217 | 241 |
| 2026-02-02 | 212 | 255 |
| 2026-02-27 | 218 | 242 |

B's scored universe is larger than A's on **43 of 43** scoring days, by +160 on day 1 and
by a persistent +20 to +30 thereafter. Two different search spaces, therefore two
different books. Traded-name overlap is `{SNDK, SPY}` — 2 names out of 29.

### 3.4 Run B never called the sentiment/trend LLM at all.

This is the largest single behavioural difference in the pair and is independent of the
DB residue.

| event | A | B |
|---|---|---|
| `Sentiment cache scope MISS` | **43** | **1** |
| `Sentiment: using cache for … — LLM skipped` | **0** | **42** `B:2017` |
| `…/enhanced_sentiment:` provider call | **42** | **0** |
| `…/macro_batch:` provider call | **42** | **0** |
| `Macro classification: N signals from N headlines` | **42** | **0** |
| `Google macro: cache hit` | **0** | **42** |
| `LLM trends: N new, N confirmed, N weakening, N ended` | **42** `A:707` | **0** |
| `Future trade review: …` | **22** `A:750` | **0** |
| `LLM cancelled: …` | **10** `A:5091` | **0** |
| `New trend: …` created during the run | **95** | **4** (all `price momentum`) |

Run A paid for the LLM calls and populated the cache; run B, launched 13 hours later
(`A:1` 08:29:25 → `B:1` 21:36:25), read that cache and skipped the LLM entirely. This is
not a tie-break — it disabled three whole subsystems in B (see Defect 1).

### 3.5 Discovery provenance telemetry is unusable in B.

`Discovery source usage` census on 2026-01-01:

```
A:132  Discovery source usage: momentum=4, sector_fill=20, trend_etf=6
B:316  Discovery source usage: momentum=6, trend_etf=6, unknown=109
```

By 2026-02-27, A reports `sector_peer=3, competitor=5, momentum=112, trend_etf=6` and B
reports `momentum=120, trend_etf=6`. Across the whole run B logs **716 `unknown`-sourced
discoveries vs A's 27**. See Defect 3 — this is a code bug, not a data difference, and it
is why the operator cannot attribute B's book at all.

---

## 4. Defects

### Defect 1 — the sentiment cache is not a complete snapshot of the LLM call, so a warm-cache re-run silently disables trend updates, scheduled trades, and cancellations

**Severity: critical. This makes every paired re-run non-comparable to its predecessor.**

`_enhanced_sentiment_from_llm` returns **four** products
(`backend/strategies/graph_nexus_analysis.py:26926`):

```python
sentiment_data, llm_future_trades, llm_cancellations, llm_trend_updates = _enhanced_sentiment_from_llm(...)
```

The cache stores and restores only the first. On the cache-hit branch
(`graph_nexus_analysis.py:26867-26870`) the other three keep the empty values assigned at
`:26858-26860`:

```python
llm_future_trades: list[dict] = []      # :26858
llm_cancellations: list[dict] = []      # :26859
llm_trend_updates: dict = {}            # :26860
...
if cached_sentiment and isinstance(cached_sentiment, dict):     # :26867
    sentiment_data = dict(cached_sentiment)
    _log(f"Sentiment: using cache … — LLM skipped, price-based trends will still run")  # :26870
elif use_llm_now and llm_key:                                   # :26889
    ...
```

Downstream, `_apply_trend_updates` is guarded by the now-permanently-empty dict
(`:27220`): `if use_llm_now and llm_key and llm_trend_updates:` — so on a cache hit the
LLM trend engine **never runs**. Same for cancellations (`:26938`) and scheduled future
trades (`:26944`).

**Evidence.** B emits `Sentiment: using cache …` 42×, `LLM trends:` 0×, `Future trade
review:` 0×, `LLM cancelled:` 0×. A emits the mirror image: 0 / 42 / 22 / 10. The log
line itself half-admits the hole — *"price-based trends will still run"* — and indeed B's
only 4 new trends are `price momentum` ones.

**Consequence for this pair.** Run B's trend memory is frozen at whatever it inherited;
it cannot be rebuilt from the window's own news. This is what converts a small day-one
state difference into a permanent, whole-run divergence.

**Proposed fix.**
1. Make the cache a *complete* record of the call. In the write path, persist
   `future_trades`, `cancellations`, and `trend_updates` alongside `sentiment` under the
   same scope key; in the read path at `:26867`, restore all four:
   ```python
   if cached_sentiment and isinstance(cached_sentiment, dict):
       sentiment_data     = dict(cached_sentiment.get("sentiment", cached_sentiment))
       llm_future_trades  = list(cached_sentiment.get("future_trades") or [])
       llm_cancellations  = list(cached_sentiment.get("cancellations") or [])
       llm_trend_updates  = dict(cached_sentiment.get("trend_updates") or {})
   ```
   Treat a legacy sentiment-only cache entry as a **miss**, not a hit, so old rows cannot
   silently reintroduce the bug.
2. Until (1) ships, make the asymmetry loud and fatal for paired runs: when
   `nexus_backtest_deterministic` is set and a *partial* cache entry is hit, log at
   `red` and set a `cache_completeness=partial` flag on the `BacktestResults` row so
   `bt X vs bt Y` comparisons can refuse to compare a cold-cache run against a
   warm-cache run.

---

### Defect 2 — the backtest restart cleanup cannot remove pre-window rows, so trend memory ratchets across runs on a shared `instance_id`

**Severity: critical. It is the proximate cause of `6 trends` vs `99 trends`.**

The cleanup at `graph_nexus_analysis.py:26064-26081` deletes only rows dated **at or
after** the backtest start:

```python
.filter(lambda doc, _df=_date_field:
        (doc["instance_id"] == instance_id)
        & (doc.has_fields(_df))
        & (doc[_df] >= date_key))          # :26071-:26075
.delete()
```

with `TRENDS_TABLE: "start_date"` (`:26055`). Three structural holes follow:

1. **`start_date < date_key` rows are immortal.** A trend created by a run whose window
   started earlier can never be deleted by a 2026-01-01 run's cleanup, yet
   `_load_active_trends` will happily serve it (`:12424-12426` filters only on
   `instance_id` and `status != "ended"`), and `_load_active_trends` keeps it alive as
   long as `last_confirmed_date >= date_key - trend_max_age_days` (`:12435`, `:12465`).
   Every prior run that *confirmed* such a trend pushed its `last_confirmed_date`
   forward (`:15232`), so the row is refreshed by the very runs that should be blind to it.
2. **`has_fields(_df)` excludes rows missing the date field** from deletion entirely — a
   row with no `start_date` survives every cleanup forever.
3. **The state ratchets in both directions and never resets.** `status: "ended"` writes
   (`:12469`, `:15203`) do not touch `start_date`, so an auto-ended pre-window trend is
   invisible to `_load_active_trends` *and* immune to deletion — permanently dead. That
   is consistent with run B having no `price_pslv`/`price_slv` at all while run A did.

**Evidence.** Both runs report `lookback=NO` (`A:16`, `B:16`) and
`Historic lookback prep: no eligible strategies — skipping.` (`A:7`, `B:7`), yet the very
next stage reports `Market trends: 6 active trends loaded` (`A:32`) vs
`Market trends: 99 active trends loaded` (`B:32`), and the cleanup itself reports clearing
`30` vs `382` rows (`A:21`, `B:21`). A second, independent witness: run B logs
`Discovery cooldown: 11 recently-sold ticker(s) excluded from re-discovery` at `B:2020`
on **2026-01-01** — and there are **zero sell events anywhere in B lines 1-2019**. Eleven
tickers were suppressed from discovery on day one by sells that happened in a *different
run*. The corresponding reset at `:26102-26112` only revives rows with
`sold_date >= date_key`, so rows sold in the 7 days *before* the window stay suppressed.

**Proposed fix.**
1. Scope the fixture, not the date. Give every backtest run a `run_scope` /
   `fixture_id` component in the persisted `instance_id` (or add a mandatory
   `run_id` field to `TRENDS_TABLE`, `DISCOVERED_TABLE`, `OUTCOMES_TABLE`,
   `NEXUS_*`), and make `_load_active_trends` / `_get_recently_sold_discovered_tickers`
   filter on it. A backtest then cannot see another backtest's rows *at all*, which
   removes the need for the date-based cleanup to be correct.
2. If (1) is too invasive short-term, invert the cleanup for non-lookback restarts: when
   `historical_lookback_mode` is false **and** the run has no lookback
   (`lookback=NO`), delete **every** `instance_id`-scoped row in
   `_RESTART_SCOPED_TABLES` and `DISCOVERED_TABLE` regardless of date, and drop the
   `has_fields(_df)` predicate. There is no lookback data to preserve in that case —
   preserving it is what created the 6-vs-99 split.
3. Add a hard start-of-run assertion and log line:
   `Prior-state audit: trends=N discovered=M cooldown=K carried into a lookback=NO run`
   and abort (or mark the row `promotion_ineligible`) when any of them is non-zero. Both
   of these runs would have aborted, which is the correct outcome.

---

### Defect 3 — trend-sourced discoveries are written with no `source` field, so the largest discovery channel reports as `unknown`

**Severity: high (observability). It is why run B's book is unattributable.**

The trend-driven discovery writer builds its document without a `"source"` key
(`graph_nexus_analysis.py:13509-13521`):

```python
doc = {
    "id": doc_id, "ticker": ticker, "instance_id": instance_id,
    "discovered_date": date_key, "source_trend_ids": source_trends,
    "status": "active", "last_signal_date": date_key, "sell_reason": None,
}                                                       # :13509-:13518  — no "source"
_r.db(DB_NAME).table(DISCOVERED_TABLE).insert(doc, conflict="replace").run(conn)
```

Every sibling path *does* set it — `"source": "propagation"` (`:13622`),
`"sector_peer"` (`:13693`), `"etf_co_holding"` (`:13752`), `"sector_watchlist"`
(`:13900`), `"sector_fill"` (`:13937`), plus competitor and momentum. The census function
then buckets the missing field into `unknown`
(`:13278`): `source = str(doc.get("source") or "unknown").strip().lower() or "unknown"`.

**Evidence.** `B:316` — `Discovery source usage: momentum=6, trend_etf=6, unknown=109`,
logged on the same bar that emitted 120 `Discovered stock: X (from trends: …)` lines
(B lines 195-315). The 109 `unknown` rows *are* the trend discoveries. Aggregated over
the run, B reports 716 `unknown` census-slots vs A's 27 — precisely because B's leaked
trend memory made the trend channel dominant. The operator's discovery-attribution
telemetry therefore describes 0% of B's primary channel.

**Proposed fix.** One line at `:13509`:

```python
doc = {
    "id": doc_id, "ticker": ticker, "instance_id": instance_id,
    "source": "trend",                      # <-- add; matches sibling writers
    "discovered_date": date_key, "source_trend_ids": source_trends,
    ...
}
```

and add `"trend"` to `_discovery_source_priority` so it orders sanely in the census.
Optionally back-fill: in `_format_discovery_source_usage`, infer
`source = "trend"` when the field is absent but `source_trend_ids` is non-empty, so
historical rows stop reporting as `unknown`.

---

### Defect 4 (bonus, quantified exactly) — the printed headline P&L and the stored P&L use two different price bases

**Severity: high for exactly this kind of investigation, because the number under
comparison is ambiguous.**

Run B publishes two different results for itself:

```
B:40297  Profit & Loss:     +$366.10 (+6.10%)
B:40342  Updated backtest results in database (id=523085, status=finished, P&L=360.2209302016072)
```

Run A publishes one (`A:39686` +$984.79 and `A:39745` `P&L=984.791204195717`). The task
brief quotes B as "+6.00%", i.e. the **DB** number (360.22 / 6000 = 6.004%), while the log
headline says 6.10%.

**Cause.** `broker.py:12388` prints via
`portfolio_emulator.print_portfolio(final_prices, …)` using the *resolver-fetched*
end-date bar, while `broker.py:12523-12536` derives the stored `pnl` from
`compute_backtest_summary(snapshots, …)`, i.e. the **last equity snapshot's own marks** —
the basis `resolve_end_prices` deliberately prefers (`backend/backtest_summary.py:546-570`,
whose docstring cites incident 586767). The DB side was fixed; the printed side was not.

**Proof to the cent.** B's SNDK is marked `@ $635.94` in the printed positions block
(`B:40308`, 1.3364 shares) but `$631.54` in the snapshot-based movement block
(`B:40338`). `1.3364 × ($635.94 − $631.54) = $5.8802`, and
`$366.10 − $360.22 = $5.88`. The entire discrepancy is one symbol priced two ways.
A shows `$635.94` in both blocks, which is why A's two numbers agree.

**Proposed fix.** Feed `print_portfolio` the same dict the summary uses. In `broker.py`,
move the `resolve_end_prices(final_prices, snapshots)` call (currently `:12410`) above
`:12388` and pass its result to `print_portfolio`, so the printed headline, the per-stock
table, the movement table, and the stored `pnl` all sit on the snapshot basis. Then add a
cheap invariant right before the DB write:
`assert abs(final_pnl - (printed_final_value - initial_cash)) < 0.01`, logging `red` and
setting `benchmark_incomplete_reason="pnl_basis_mismatch"` rather than silently shipping
two numbers.

---

## 5. Two further code-level observations (lower confidence, stated as such)

* **`_load_active_trends` is called with the default `max_age_days=90` from inside
  `_apply_trend_updates`** — at `graph_nexus_analysis.py:12519` and `:12699` the call omits
  `max_age_days`, so it falls back to the signature default `90` (`:12411`) instead of the
  configured `trend_max_age_days=21` used at the two other call sites (`:26412`, `:27213`).
  `backend/tests/test_nexus_fixes.py:1547-1549` asserts only the *two* compliant sites, so
  the test passes while these two leak. **I could not confirm any log line in this pair
  that isolates the effect**, since B never reaches `_apply_trend_updates` at all
  (Defect 1) and the reload is not logged. Flagging as a code-reading finding only.
* **`SLOT MIN-NOTIONAL: skip queue buy X $100 < $100 floor`** appears 260× in A and 0× in B
  (e.g. `A:` 2026-01-12 block). The `$100 < $100` reads as a bug but is consistent with
  `%.0f` rounding of e.g. $99.6 against a $100.02 floor (`:32259-32260`,
  `:32004-32005`). **I cannot distinguish a real epsilon bug from a display artefact from
  the log alone**; the fix either way is to print `:.2f` in both messages. The A-vs-B
  count difference here is downstream of the universe divergence, not an independent defect.

---

## 6. Answer to the question as posed

> *Determine whether anything OTHER than AGQ differs systematically. Be explicit if the
> answer is 'nothing else differs'.*

**Not "nothing else". Almost everything else.** Systematically, and day-aligned across all
43 shared scoring days:

1. **Persisted trend memory at t=0**: 6 trends vs 99 trends, with 0 overlap in kind
   (price-momentum vs LLM-news). Runs on `lookback=NO`, so this is 100% cross-run residue.
2. **Universe size**: B larger on 43/43 days; 30 vs 190 on day 1.
3. **Discovery volume and channel mix**: 6 vs 156 symbols expanded on bar 1; B's census
   collapses to `momentum` + `unknown`, A's retains `sector_fill`, `sector_peer`,
   `competitor`, `etf_co_holding`.
4. **LLM execution**: A made 42 `enhanced_sentiment` + 42 `macro_batch` calls; B made
   **zero of each**. A applied 42 days of LLM trend updates, 22 future-trade reviews and
   10 cancellations; B applied none.
5. **Trade counts**: 47 vs 32; winner add-ons 1 vs 3.
6. **Traded names**: 19 vs 12, overlap `{SNDK, SPY}` only.

And what does **not** differ, checked explicitly: the config banner, the seed, the
instance/scope ids, the calendar and tick count, and **the regime engine — 26 chop /
17 bull in both, zero disagreeing days.**

AGQ is worth 113% of A's P&L, which is true and also the point: **run A has no
demonstrated edge outside a single position it acquired from contaminated memory.** The
correct reading of this pair is not "873929 beat 523085" but "the harness cannot yet
produce two comparable runs of the same config," for the reasons in Defects 1 and 2.

## 7. Claims I could NOT support

* I cannot say **where** run B's 99 trends came from (which prior run, which window). The
  logs record only that they were present at load time; the originating run is not
  identified in either file.
* I cannot say whether the two runs' underlying **price data** was identical. The prices
  CSV footers differ (`A:39747` 5,135 bars vs `B:40344` 3,359 bars) but that is fully
  explained by the different traded universes, so it is not evidence of a data defect.
* I cannot quantify the `max_age_days=90` leak's effect on this pair (see §5).
* I cannot rule out additional differences inside the LLM overlay scoring, because B never
  invoked it in a comparable way (Defect 1 masks any further comparison).
