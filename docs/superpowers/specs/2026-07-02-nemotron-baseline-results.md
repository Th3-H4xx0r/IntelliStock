# Nemotron baseline backtests — Track C results (2026-07-02)

Track C validation for the `graph_nexus` strategy (Strategies doc **179**, live
instance `alpaca-main`). Runs in parallel with the gated live deploy; **does not
gate it**. This document records the launch mechanism, the pilot outcome, the
runs in flight, and how to finish the battery. It is intentionally honest about
what is and is not a valid baseline.

> Status at write time: **pilot phase only.** One healthy June-replay run is in
> flight; the expensive tuned battery was deliberately **not** launched (see
> "Decision" and "NOT done"). Numbers below are poll instructions, not final
> results — no run completed within the session.

---

## 1. Launch mechanism (Step 1)

Three candidate entry points were evaluated:

| Candidate | Verdict |
|---|---|
| `backend/engines/ai_backtest_engine.py` | **No.** This is the AI *strategy-generation* agent (LLM invents strategies, `FIXED_START_DATE`/`FIXED_END_DATE` at `:45`, 2h timeout). Not a config-driven replay of doc-179. |
| Run `broker.py backtest …` locally | **Impossible from this machine.** The broker needs Neo4j (graph_nexus), Alpaca market-data auth, an OpenRouter key, and the full backend deps. Local probe: **no Docker, Neo4j port 7687 closed, no `OPENROUTER_API_KEY`**. Only `.env` secret present is `INTELLISTOCK_CRED_KEY`. |
| **Queue a `BacktestInstances` row → prod backtest-engine service** | **Chosen.** This is how the existing 1058 `BacktestResults` were made. |

**How it actually runs.** `backend/engines/backtest_engine.py` is a standalone
service that watches the `BacktestInstances` table via changefeed and, per
pending row, spawns a **Docker container** running
`broker.py <instance> backtest <start> <end> <gran> <key> <secret> [symbols]
--backtest-id <id>` (`backend/engines/backtest_engine.py:582`). The broker reads
the **strategy config from the referenced instance's `strategy_id` → Strategies
doc** — config/model-roles/cadence are **not** passed on the CLI; they come from
the linked doc. Market-data creds come from the instance's
`alpaca_data_brokerage_id`.

**Consequence for config selection:**
- CURRENT doc-179 config → reference instance **`alpaca-main`** (`strategy_id=179`,
  `alpaca_data_brokerage_id` set, key/secret present). ✅ available.
- TUNED config → **has no instance in the DB.** It exists only as the transform
  in `backend/scripts/apply_tune_2026_07.py::build_tuned_strategies`. Running it
  requires standing up a *new* Strategies doc + a *new* backtest-only instance
  carrying the tuned config **without editing doc-179** (forbidden). See "NOT
  done".

**Launch method used:** `interactive_utils.action_create_backtest(conn,
"alpaca-main", [], start, end, granularity_sec=86400)` against **prod
RethinkDB** (`get_conn()`, repo-root `.env` `RETHINKDB_HOST`). This is the exact
code path behind `POST /backtests`; it inserts a queue row and is **not** a
live-trading action. The prod API (`https://intellistock-api.pkrishna.dev`) is
up but auth-gated (401); the direct DB insert avoids needing an API token and
does not restart/reconfigure any server. Empty `stocks` = pure-discovery mode
(Nexus discovers its own tickers, matching live). `granularity_sec=86400` (daily
bars) matches all 452 historical doc-179 runs; the dual-cadence intraday
simulation is driven by the config flag, not granularity.

**Prod engine liveness (important):** first read of state looked *dead* —
newest `BacktestResults` was 2026-05-29 (>1 month stale), `EngineControl.
backtest_engine.running=False`, and 111 orphaned `BacktestInstances` rows stuck
at `status=running, run=False`. But the pilot insert was **claimed in <20 s**
(status → running), so the consumer **is alive**; the engine does not gate on
`EngineControl` and the orphan rows are stale residue. Nobody had simply queued
a real backtest in a month.

---

## 2. Pilot verdict (Step 2)

**VIABLE.** The never-before-backtested OpenRouter Nemotron config runs.

Two June-2026 replays (`2026-06-01 → 2026-07-01`, current config, `alpaca-main`)
were queued. Observations:

- **`992271`** — claimed in <20 s, ran the Nexus graph-build/macro phase
  (discovered 106 symbols; prod Neo4j reachable at `bolt://100.95.106.23:7687`
  via Tailscale), then **stopped at ~255 s, progress 0**, no error line, queue
  `run` flipped False. Cause: almost certainly **concurrency starvation** — it
  was killed mid-graph-build once the second high-difficulty run started. The
  engine's `MAX_CONCURRENT_HIGH_DIFFICULTY=1` cap should have serialized them but
  both got claimed. **Not a valid result.** Lesson: **run these sequentially.**
- **`217590`** — healthy. Progressed past graph-build into the simulation phase
  (fetching historical bars, e.g. `ITUB: 155 bars`). This is the viable pilot.
  Left running.

**Runtime signal.** Historical doc-179 daily-granularity runs over a ~6.5-month
window took **~6 hours each** (`22,190 s / 23,494 s / 21,967 s / 25,120 s`; one
outlier at `60,061 s` ≈ 16.7 h). That is **~0.9 h per simulated month**. So:
- June replay (1 month) → projected **~1 h** (under the 2 h gate; will not finish
  in-session).
- Each tuned 6-month window → projected **~5–6 h** (each **exceeds** the 2 h
  pilot-stop gate).

**Cost signal (real OpenRouter tokens).** Nemotron-550b is called many times per
bar per discovered ticker (~100+ tickers). Every observed call logged
`ok=True` but **`raw_json_fallback=True`** — the 550b model is **not** returning
schema-clean structured JSON; the pipeline salvages it via raw-JSON parsing.
Calls succeed but this is a fragility/quality signal for a "baseline," and a
~6 h × 550b run is genuinely expensive.

**Config honesty finding (matters).** doc-179 CURRENT is **not** yet the tuned
config, and is **not** fully Nemotron:
- Default role: `openrouter / nvidia/nemotron-3-ultra-550b-a55b`. ✅
- `macro_article_*` and `lookback_macro_article_*` roles: still
  **`codex-cli / gpt-5.4-mini`** (the tune's B2 migration off codex-cli has
  **not** been applied to the DB — despite the "apply 2026-07 live tune" commit,
  all 25 tuned keys still differ from current: drawdown 12, max_positions 8,
  etc.). So the *fully-Nemotron* config is actually the **TUNED** one. The
  current-config pilot's macro-role calls hit codex-cli, which may not
  authenticate inside the backtest container.

**Decision:** per the HARD-RULE pilot gate ("if a single run would exceed ~2 h …
STOP after the pilot attempt and report"), the three 6-month tuned runs are **not
launched** — each exceeds the gate, each needs a tuned instance that does not
exist, each costs real Nemotron tokens, and concurrency proved unstable. One
healthy June-replay run (`217590`) is left in flight. The June *repeat* for
variance was not re-queued concurrently (would starve again); launch it
sequentially after `217590` finishes.

---

## 3. Runs & how to poll (Step 3/4)

| Purpose | Window | Config | Instance | Run id | Status @ write |
|---|---|---|---|---|---|
| Pilot — June replay | 2026-06-01→2026-07-01 | CURRENT (Nemotron default + **codex-cli macro**) | alpaca-main | **217590** | **running** (sim phase) |
| June replay (dupe, concurrency-starved) | 2026-06-01→2026-07-01 | CURRENT | alpaca-main | 992271 | **stopped @255 s — invalid** |
| June replay repeat ×1 | 2026-06-01→2026-07-01 | CURRENT | alpaca-main | — | **NOT launched** (do sequentially) |
| Tuned Jan–Jun ×2 | 2026-01-02→2026-06-30 | TUNED (fully Nemotron) | *new instance needed* | — | **NOT launched** |
| Tuned non-bull ×1 | 2025-07-01→2025-12-31 | TUNED | *new instance needed* | — | **NOT launched** |

**Poll (prod RethinkDB, table `BacktestResults`, primary key = run id):**

```python
# repo root; python3; .env RETHINKDB_HOST
import sys, types; sys.modules.setdefault("socketio", types.ModuleType("socketio"))
sys.path.insert(0, "backend"); import os; os.chdir("backend")
from dotenv import load_dotenv; load_dotenv("../.env"); load_dotenv(".env")
from rethinkdb import RethinkDB; import interactive_utils as IU
r = RethinkDB(); conn = IU.get_conn()
row = r.db("IntelliStock").table("BacktestResults").get(217590).pluck(
    "status","progress","pnl_percent","pnl","tickers",
    "time_elapsed_seconds","start_date","end_date").run(conn)
print(row)
```

Terminal `status`: `finished` (done), `stopped`/`failed` (aborted). Result
fields: `pnl_percent`, `pnl` (dollars), `pnl_percent_per_stock`, `tickers`,
`time_elapsed_seconds`, `initial_cash` (100000), `strategy_schema` (config
snapshot), `backtest_trades`, `portfolio_value_history`. When `217590` reaches
`finished`, fill the results table below (pnl%, trade count from
`backtest_trades`, win rate = winning round-trips / total).

**Analyses to complete once runs finish (methods, so the controller can finish):**
- **vs SPY same window:** compare run `pnl_percent` to SPY buy-and-hold over the
  identical dates. June 2026 SPY return must be pulled from `PriceHistory`/Alpaca
  for `2026-06-01→2026-07-01`; a run only "beats the market" if it clears SPY.
- **Variance across repeats:** needs ≥2 valid runs of the same window (currently
  only 1 valid June run — launch the repeat sequentially).
- **Gross-vs-net sensitivity:** backtest fills are **frictionless** (no
  slippage/commission). Do **not** apply a fake precision haircut; note that a
  ~100-ticker, high-churn daily strategy would lose an estimated few tens of bps
  to spread+slippage per round-trip live, so net < gross — treat gross pnl% as an
  optimistic upper bound, not a live estimate.
- **June replay vs actual live June (+0.5%):** live `alpaca-main` returned
  **+0.5%** in June 2026. Compare `217590`'s June pnl%. A large positive gap =
  the backtest is over-optimistic (frictionless fills, no live halts/blocks,
  cleaner data) and should be discounted accordingly — this is the core
  live-vs-backtest divergence question this branch investigates.

**Results (fill on completion):**

| Run id | Window | pnl% (gross) | Trades | Win rate | SPY same-window | vs live/SPY |
|---|---|---|---|---|---|---|
| 217590 | Jun-2026 | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |

---

## 4. Historical baselines are INVALID (context)

The pre-existing 90–266% doc-179 backtests are **not** honest baselines:

- Single **bull window** (`2025-11-10 → ~2026-05` in every finished run).
- In-sample tuned; `full_every_tick` cadence (not the live dual-cadence sim).
- Old models (`gpt-5.4-mini` / kimi), **never Nemotron**.
- Run against retired instance `main` (Robinhood), not `alpaca-main`.

Sample finished runs (all bull-window, `full_every_tick`, non-Nemotron):
`pnl% = 101.2 / 180.4 / 192.2 / 112.9 / 130.8 / 152.2 / 83.9` over ~6.5 months.
Use these only to demonstrate why fresh, out-of-sample, dual-cadence, Nemotron
runs are needed — not as a performance claim.

---

## 5. NOT done (explicit hand-off)

1. **Tuned-config battery (Jan–Jun ×2, non-bull Jul–Dec ×1)** — blocked on two
   things, both requiring authorization given real-money token cost:
   - Each run ~5–6 h (> 2 h pilot gate).
   - **No tuned instance exists.** To run without editing doc-179: create a new
     Strategies doc from `build_tuned_strategies(doc179)[0]` (import from
     `backend/scripts/apply_tune_2026_07.py`) under a fresh id, create a
     backtest-only Instances row pointing at it (copy `alpaca-main`'s
     `alpaca_data_brokerage_id`/key/secret), then `action_create_backtest` against
     that instance. Note the tuned strategies embed real API keys — handle like
     doc-179 (never print values).
2. **June replay repeat** — launch **sequentially** after `217590` finishes
   (concurrent high-difficulty runs starve each other — proven by `992271`).
3. **All result analysis** (pnl%, trades, win rate, SPY, variance, net
   sensitivity, June-vs-live +0.5%) — no run completed in-session.
4. **Orphan cleanup** — `992271`'s queue row is left as `status=running,
   run=False` residue (prod state not touched); the engine's reconcile loop
   handles these.
