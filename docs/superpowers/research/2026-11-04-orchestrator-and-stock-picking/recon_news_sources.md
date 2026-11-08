# Recon: point-in-time news and filings sources for a weekly "global trends" LLM picker

Date: 2026-11-05. Read-only recon; every number below comes from a live query unless marked
*(docs/third-party)*. Scratch scripts and raw outputs:
`/private/tmp/claude-501/-Users-pranavkrishna-PranavFiles-coding-projects-IntelliStock/94c70bd0-1591-488c-8e5e-87f09677edf3/scratchpad/recon_news/`.
Alpaca was queried only with the **alpaca-paper-fwd** data credentials, which the code asserted were `paper=True`.
The credentials stayed in memory. Postgres was read-only: `default_transaction_read_only=on` was confirmed
by `SHOW`.

## TL;DR

- **Alpaca News (Benzinga) is the backbone for both the backtest and live use.** It has full-day history back to
  **2015-01-01** and returns 620–1,230 articles per weekday, about 3,700 per trading week. Each article
  carries an exact `created_at`. The history is stable: re-querying days captured live in 2025–26 found 0 changed
  headlines and at most 1 of 200–340 rows missing. Backfilling 2021-01 → 2026-11 takes about 36k calls, or about 3 h at
  200 req/min.
- **The repo's current news capture is not a usable PIT corpus.** The all-symbol fetch takes one page of
  `limit=50` with the API's default **descending** sort, so it keeps only the 50 newest articles before
  `end`. That is roughly the last 1–2 hours of a day, which leaves the stored days with only **21–42 %** of that day's articles.
  83 % of Google rows are stamped exactly 07:00Z or 08:00Z, so they are date-only (midnight Pacific).
- **GDELT** fills the global/geopolitical gap. The DOC 2.0 API now serves **artlist and timelines back
  to 2021+** (verified 2021-01 timelines and 2022-06 and 2025-04 artlists). It is throttled hard: a 1-per-5 s rule,
  and in practice 15 s spacing. The raw Events files (15-min, `DATEADDED`) are PIT and small. Raw GKG is about 480 MB/day
  zipped, which is too heavy to mirror.
- **SEC EDGAR** has exact `acceptanceDateTime` per filing and `filed` per XBRL fact, which makes it PIT-safe if you
  filter on those fields. **ALFRED** gives keyless vintage macro CSVs; revisions are real (PAYEMS Jan-2022 was 149,744
  at the 2022-06-15 vintage and is 150,006 today).
- **The biggest PIT risk is not the news.** The LLM itself has read 2021–2026. See §7.

---

## 1. Alpaca News API (Benzinga) — `https://data.alpaca.markets/v1beta1/news`

### Interface (measured)
| Property | Finding |
|---|---|
| Earliest article | `2015-01-01T13:21:10Z` (a query from 2014-06 returns this as the first row) |
| Params | `start`, `end` (RFC3339), `symbols` (comma list; optional), `limit` (**max 50**; 200 → HTTP 400 "larger than the allowed maximum of 50"), `sort` (`asc`/`desc`, **default desc**), `page_token`, `include_content` |
| No-symbol "general" news | Works. Omitting `symbols` returns all Benzinga items, and 2–7 % of them have an empty `symbols` list (macro/market wraps) |
| Rate limit | Header `X-RateLimit-Limit: 200` (per minute). No 429 in 194 calls plus a 3-day re-pull |
| Fields | `id, author, created_at, updated_at, headline, summary, content, images, source, symbols, url` |
| `source` | `""` in 2015/2021, `"benzinga"` from 2022. `author` is mostly "Benzinga Newsdesk" / "Benzinga Insights" |

### Sample days (full pagination, `sort=asc`)
| Day | Articles | Pages | No-symbol | With summary | Macro-keyword headlines | Top symbols |
|---|---|---|---|---|---|---|
| 2015-01-06 | 621 | 13 | 52 | 0 | 22 | VZ, AOL, MU |
| 2021-03-16 | 722 | 15 | 14 | 187 | 19 | SPY, AAPL, TSLA |
| 2022-06-15 (FOMC +75 bp) | 771 | 16 | 40 | 349 | 70 | SPY, ETHUSD, TSLA |
| 2023-05-25 (day after NVDA print) | 1,087 | 22 | 43 | 150 | 44 | SPY, **NVDA 65** |
| 2024-08-05 (yen-carry crash) | 1,233 | 25 | 29 | 185 | 34 | SPY, BTCUSD, NVDA |
| 2026-10-14 | 936 | 19 | 25 | 300 | 67 | SPY, BAC, NVDA |
| **Week 2022-06-13..17** | **3,701** | 75 | 126 | 1,756 | 185 | SPY, ETHUSD, TSLA |
| NVDA only, 2023-05-24..26 | 115 | 3 | — | 47 | — | the NVDA earnings 8-K/PR, guidance, and reaction pieces all present |

"Macro-keyword" counts headlines that match Fed/FOMC/CPI/China/tariff/oil/Ukraine/Iran/yen/yield/war and similar terms.
Macro coverage exists, but it is US-market-centric: "Bitcoin… ahead of Fed", "Yen carry-trade unwinding…", "IEA keeps
2022 oil demand…". It is not a world-news wire.

### Point-in-time quality
- `created_at` is second-resolution UTC. `updated_at − created_at` has a median of 0–1 s and p90 ≤ 155 s. The maximum observed
  was 22.5 h, with 0 over 1 day across 2,745 articles. Edits are near-instant, so there is no material revision lag.
- **Stability test:** I re-pulled three days that the live bot had captured into `GraphNexusNewsRaw` (2025-12-16,
  2026-03-19, 2026-04-24) and compared them with the API today. Of 187/200/342 stored articles, 1/0/1 are gone and 0
  headlines changed. Benzinga history served today is a faithful replay of what was visible then, at headline
  level.
- `content` (`include_content=true`) is present for **28–40 %** of articles: 201/722, 431/1,087, 300/936. Where present, the
  median is 2.5–4.6 k chars. With content a day is about 1.1–3.3 MB of JSON.
- Licensing *(docs)*: the data is Benzinga's, redistributed under the Alpaca Market Data agreement. It is fine for
  internal use and model input; do not republish it. It is free on the paper/Basic data plan.

### Backfill cost (2021-01-01 → 2026-11-05)
About 2,135 days × ~850/day ≈ **1.8 M articles**, ≈ **36k calls** at 50 per page, ≈ **3 h** at 200/min (run one worker). Storage is
≈ 4–6 GB with content, or ≈ 1 GB for headline + summary + symbols.

## 2. GDELT

| Product | Coverage / PIT | Limits | Use |
|---|---|---|---|
| **DOC 2.0 API** `api.gdeltproject.org/api/v2/doc/doc` | **Verified historical:** `timelinevolraw` 2021-01-01..03-01 (daily: 1,325 / 3,349 … ECON_INFLATION articles); `artlist` 2022-06-15 and 2025-04-02..09; `timelinetone` 2025-03..04 for ECON_TRADE_DISPUTE (46 daily points, −2.08 → −1.42). Each article has `seendate` (crawl time, 15-min) — PIT-usable. | Server text: "limit requests to one every 5 seconds". In practice I got 429s at 6 s spacing and successes at 15 s, sometimes on the second attempt. `artlist` max **250 records**. | Weekly theme volume/tone plus the top 250 articles per theme query. Example: "(tariff OR tariffs)" for 2025-04-02..09 returned 250 English articles from US 101, India 76, China 14, and other countries. |
| **Events 2.0 raw** `data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.export.CSV.zip` | Every 15 min since 2015-02. Verified files for 2021-01, 2022-06, 2024-08, and 2026-06. One file: 1,058 rows × 61 cols, with `DATEADDED` = PIT publish slot. About 1 % of rows reference older event dates, so filter on `DATEADDED`, not `SQLDATE`. | About 75 KB/15 min → about 7 MB/day → **about 15 GB** for 2021–26, with no rate limit beyond politeness. | Geopolitical conflict/cooperation intensity (CAMEO root codes, Goldstein, AvgTone) by country per week. |
| **GKG 2.0 raw** `…gkg.csv.zip` | Same cadence. One file had 1,379 docs × 27 cols, with themes, tone, and locations; 411 docs carry ECON_* themes. | **About 5 MB zipped per 15 min → about 480 MB/day → about 1 TB** for 2021–26. Too heavy to mirror. BigQuery `gdelt-bq.gdeltv2.gkg_partitioned` costs per TB scanned. | Skip; the DOC timelines are the aggregated form of GKG. |
| Live feed | `lastupdate.txt` returned the 2026-11-05 14:30 slot when polled at about 14:35 UTC. | — | Live weekly pull works the same way. |

Reliability: GDELT's machine-translated, crawl-based coverage drifts over time as sources come and go, so use
`timelinevolraw` with its `norm` field or `timelinevol` (normalized %) instead of raw counts. Tone is lexicon-based and noisy.
The licence is open with attribution requested. GDELT itself points high-volume users to its ngrams dataset.

**Long-span timelines.** A 1-year `timelinevolraw` call (2023-01-01..2024-01-01, ECON_INFLATION) returned
**daily resolution, 365 points, 0 zeros**. It succeeded on the 5th attempt at 20 s spacing. The full 2021-01..2026-11
span got 429 on all 6 attempts, so whether it works or what resolution it returns is unknown. Plan on 1-year chunks:
25 themes × 2 modes × 6 years ≈ **300 calls ≈ 2–3 h** with backoff. Throttling is IP-level and bursty, so the job
must resume from the last successful call.

## 3. SEC EDGAR (User-Agent `IntelliStock research krishnatechpranav@gmail.com`)

| Endpoint | Finding |
|---|---|
| `data.sec.gov/submissions/CIK##########.json` | NVDA: 1,000 "recent" filings (2020-09-17 →) plus one overflow file (1998–2020). Each filing has `filingDate`, **`acceptanceDateTime`** (ms UTC), `reportDate`, `form`, `items`, `primaryDocument`. Example: NVDA Q1-FY24 earnings **8-K items 2.02/9.01 accepted 2023-05-24T20:22:18Z** (4:22 pm ET, after the close); 10-Q accepted 2023-05-26T20:17:32Z. |
| `data.sec.gov/api/xbrl/companyfacts/CIK….json` | 627 us-gaap concepts for NVDA. **Every value carries `filed` + `form`**, and the same fact recurs in later filings. Revenue for the quarter ending 2023-04-30 appears twice: 10-Q filed 2023-05-26 and again in the 10-Q filed 2024-05-29, as the prior-year comparative. PIT rule: for each (concept, period), keep the latest row with `filed ≤ as_of`. Restatements are then handled correctly. |
| `efts.sec.gov/LATEST/search-index?q=…&forms=8-K&dateRange=custom&startdt&enddt` | Works. "tariff" in 8-Ks from 2025-04-01..10 returned 41 hits, with `file_date`, `form`, `items`, `ciks`, `sics`, `adsh`. It has **date-only** granularity, so join on `adsh` to `submissions` for the acceptance time. *(docs)*: 100 hits per page, about 10k-hit cap, history from 2001. |
| `www.sec.gov/Archives/edgar/daily-index/YYYY/QTRn/master.YYYYMMDD.idx` | 200. 2023-05-24 is 377 KB. The cheap way to enumerate every filing each day. |
| `api/xbrl/frames/…` | Works, but it returns the latest value per frame (not PIT). Do not use it for backtests. |

PIT caveat: an 8-K accepted after 17:30 ET gets the next business day as its `filingDate`. Always key on
`acceptanceDateTime`. Fair access is 10 req/s; the data is public domain. Useful 8-K items for a trends picker are 1.01 (material
agreement), 2.02 (results), 5.02 (executive changes), 7.01/8.01 (Reg FD/other).

## 4. Other sources

| Source | Verdict | Evidence |
|---|---|---|
| **ALFRED vintages** `alfred.stlouisfed.org/graph/alfredgraph.csv?id=X&vintage_date=D` | **Use.** Keyless CSV, true vintage values. | PAYEMS at the 2022-06-15 vintage was Jan 149,744 / May 151,682; today it is 150,006 / 151,924. The FRED JSON API needs a (free) key: the keyless call returned HTTP 400. |
| Fed FOMC calendar / BLS release schedule | **Use as a scheduled-event table.** Dates are known in advance, so they are PIT by construction. | federalreserve.gov `fomccalendars.htm`, bls.gov `schedule/news_release`. Scrape once and store. |
| Wikipedia `Portal:Current_events/YYYY_Month_D` | **Usable only by revision.** | 2022-06-15 had 40 revisions: 19 on the day, 8 on 06-16, 7 on 06-17, and 2 in **2024-11**. 2026-10-14 had 49 revisions through 10-15. Fetch the revision as of decision time (`rvstart`), never the current page. It is a compact human digest of world events (3.7–7.7 KB/day), licensed CC BY-SA. |
| Google News RSS (repo) | **Avoid for backtests.** | `after:/before:` date filters work, but rows are date-only and pulled after the fact. Results are ranked by today's relevance, which is survivorship bias. |
| yfinance news | No history (latest ~10 items only). Live only. | — |
| NewsAPI.org free | No. 1-month history, 100 req/day, dev-only licence *(third-party)*. | — |
| Finnhub free | Live only. Company news goes back about 1 year, 60 req/min, non-commercial *(third-party)*. | — |
| Polygon/Massive free | Not worth it. 5 req/min, about 2 years of history; news-on-free is unconfirmed *(third-party)*. Its news is also largely Benzinga, so it duplicates Alpaca. | — |
| Benzinga direct (repo already has `benzinga_*` fetchers: ratings, insider, gov trades, M&A, earnings calendar) | Optional enrichment. The registry already enforces `available_at ≤ as_of` for these (`point_in_time_registry.py`, `_benzinga_available_at`). | Needs the paid Benzinga key. |

## 5. What the repo ingests today, and its PIT problems

- `GraphNexusNewsRaw` (Postgres@server7): **51,527 rows**. 34,575 are Alpaca (2021-11 → 2026-11) and 16,952 are google_news
  (2010 → 2026). 23 distinct `instance_id`s. Before 2025 it is sparse: Alpaca had 100 rows in 2021, 187 in 2023, and 539 in 2024, which are backtest
  spot-pulls. Alpaca's median is 69 rows per day it has; Google's median is 33.
- **Alpaca truncation:** `_fetch_alpaca_news_all` (`backend/strategies/graph_nexus_analysis.py:16703`) sends a
  single request with `limit≤50` and no `page_token`. With Alpaca's default `sort=desc`, that keeps the **50 newest
  before `end`**, so a 16:00 ET decision sees only about the last hour of news. The legacy `ml_news.py:302`
  fetcher does the same: `min(limit*len(batch), 50)` per 20-symbol batch. Measured against today's API, the stored days
  hold **21.2 %, 21.6 %, and 42.4 %** of the articles in their own time window. 1,236 Alpaca rows have
  `published_at` older than their `date_key`, which is carry-over from multi-day windows.
- **Google News:** `published_at` is exactly 07:00:00Z or 08:00:00Z on 14,096 of 16,952 rows (83 %), so it is date-only. All
  16,952 rows have no symbols and no excerpt. Backtest rows are fetched today with `before:` and ranked by Google's current relevance. Both
  are look-ahead and survivorship risks that no stored-row filter can fix.
- `content_excerpt` on Alpaca rows (28,791 of 34,575) is scraped from the article URL at fetch time. For a past date,
  the scrape returns today's page.

## 6. Recommended stack

### (a) Backtest 2021-01 → 2026-11
1. **Alpaca/Benzinga full mirror.** Page every day with `sort=asc` and `limit=50`, with no symbol filter. Keep `content` but cap it
   (for example 4 kB). About 1.8 M rows, about 3 h.
2. **SEC EDGAR.** Daily `master.idx` → `submissions` for the universe. Store 8-K/10-Q/10-K rows keyed on
   `acceptanceDateTime`, plus `items`. Pull companyfacts per CIK, keep it filed-dated, and select facts as-of.
3. **GDELT DOC timelines + Events.** About 25 fixed macro/geo themes (ECON_INFLATION, ECON_TRADE_DISPUTE,
   ECON_CENTRALBANK, ENV_OIL, ARMEDCONFLICT, SANCTIONS, TAX_FNCACT_… plus country keywords). Pull `timelinevolraw` and
   `timelinetone` in 1-year chunks, which is about 300 calls. Pull **weekly `artlist` top 250** per theme, which is about 25 × 305 weeks ≈ 7.6k
   calls ≈ 32 h at 15 s spacing; run it as a resumable background job. Optionally mirror Events raw (about 15 GB) for country
   conflict indices.
4. **ALFRED vintages** for about 20 macro series (CPI, core PCE, payrolls, unemployment, GDP, ISM, claims, 10y/2y, DXY, oil). Fetch
   one CSV per series with all vintages (`alfredgraph.csv` with `vintage_date` list, or the FRED API `realtime_start/end` with a free key).
5. **Scheduled-event calendar** (FOMC, CPI, NFP, GDP, OPEC). Static table.
6. Optional: Wikipedia Current Events at revision-as-of-Friday-close, as a 1-page "world digest" (about 305 calls).

### (b) Live
Use the same sources and tables with a 15-min incremental poller. Alpaca: `start = max(created_at seen)`, paginate `asc`.
GDELT: `lastupdate.txt`. EDGAR: today's `master.idx` / RSS. ALFRED/FRED: daily. Live writes must use the **same
normalizer and table**, so a weekly cycle reads identically whether the date is simulated or real. Adding Finnhub/yfinance only adds
noise; skip it.

### Expected volume per weekly cycle
| Layer | Raw per week | After pre-processing → prompt |
|---|---|---|
| Alpaca/Benzinga | ≈ 3,700 weekday + ≈ 400 weekend ≈ **4–6k articles** | Dedupe, drop "Insights"/price-action boilerplate and crypto, cluster by symbol/theme → **≈ 150–300 headlines + 1-line summaries** (~15–25k tokens) |
| GDELT | 25 theme × (7 daily vol + tone) + 25 × 250 articles | Week-over-week z-score table of the 25 themes (~1k tokens) plus the top 5 titles per rising theme (~3k tokens) |
| EDGAR | ≈ 1.5–2k 8-Ks/week market-wide; ≈ 50–150 for a 500-name universe | Counts by item, plus headlines for 1.01/2.02/5.02 in the universe (~3k tokens) |
| ALFRED + calendar | ≈ 5–10 new prints | Surprise table (as-first-released vs prior) (~1k tokens) |

### Storage plan: one PIT table
```
NewsPIT (partitioned by month on published_at; DDL in backend/db/schema.py, not at a call site)
  id            text  -- "{source}:{native_id}"  (benzinga id / adsh / gdelt url-hash / fred series@vintage)
  source        text  -- benzinga | sec_edgar | gdelt_doc | gdelt_event | alfred | calendar | wiki_ce
  published_at  timestamptz  -- created_at / acceptanceDateTime / seendate / DATEADDED / vintage realtime_start
  available_at  timestamptz  -- = published_at + source latency guard (e.g. +60 s Benzinga, +0 EDGAR, +15 min GDELT, vintage day close)
  fetched_at    timestamptz  -- when we pulled it (audit: backfill vs live)
  symbols       jsonb, themes jsonb, headline text, summary text, body text (capped), url text, raw jsonb
```
- The backtest reads **only** `available_at ≤ decision_ts AND available_at ≥ decision_ts − 7d`, ordered `COLLATE "C"`. It
  never touches the network. Gate this with the existing `point_in_time_registry` manifest/hash so a run proves which
  snapshot it saw.
- Through `db.store` only: `store.between('NewsPIT', lo, hi)` is `[lo, hi)`. Use `store.iter()` for the weekly window,
  which can exceed 100k rows in backfill. Insert with `conflict="update"` on `id`.
- Keep `fetched_at` so live-captured rows can be audited against backfilled ones. The test in §1 found 0 headline drift.
- Do not reuse `GraphNexusNewsRaw`. It is instance-scoped, truncated, and mixes in date-only Google rows.

## 7. Caveats the picker must design around
1. **LLM memory look-ahead dominates.** Any model trained after 2021 knows the NVDA 2023 boom, the 2022 bear, and the 2024 yen
   crash. PIT news stops the *data* from leaking; it does not stop the *model* from recognizing "late May 2023, NVDA
   guidance". Mitigations, none complete: strip dates and absolute price levels from the prompt; anonymize tickers in the
   reasoning step and de-anonymize afterwards; prefer a model whose training cutoff precedes the test window for any segment
   you want to call out-of-sample; treat only the post-cutoff segment (for this model, after about mid-2026) as clean
   evidence.
2. Benzinga is US-market-centric. "Global trends" needs GDELT/Wikipedia for non-US politics and conflict.
3. GDELT source mix drifts. Use normalized and z-scored measures, never raw counts across years.
4. Alpaca `content` is missing for about 60–70 % of articles. Do not let URL-scraped bodies into a backtest, because they are scraped today.
5. EDGAR full-text search is date-only. Always resolve to `acceptanceDateTime` via `adsh`.
