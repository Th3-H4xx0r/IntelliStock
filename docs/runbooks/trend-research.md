# TR (deep-research trend picker): server runbook

Backtest only. Nothing here touches docs 200/201, `alpaca-main` or any live
account. Spec: `docs/superpowers/specs/2026-11-05-deep-research-trend-picker-design.md`.

All API calls go through `scripts/_api.py` (auth from the primary `.env`).
Run each step to completion before the next. Engine backtests run **one at a
time**.

## 0. Deploy check

```bash
python3 scripts/check_deployed_code.py      # exit 0 = prod runs this tree
```

Exit 1: redeploy first. A job or backtest on stale code proves nothing.

## 1. Ingest jobs

One job per kind may be active at a time (a second gets HTTP 409). Dates are
`YYYY-MM-DD`; cover the ledger window plus its lead-in.

```bash
python3 scripts/_api.py POST /trend-research/jobs \
  '{"kind": "ingest_news", "params": {"start": "2021-01-01", "end": "2026-10-30"}}'
python3 scripts/_api.py POST /trend-research/jobs \
  '{"kind": "ingest_themes", "params": {"start": "2021-01-01", "end": "2026-10-30", "articles_start": "2024-01-01"}}'
python3 scripts/_api.py POST /trend-research/jobs \
  '{"kind": "ingest_macro", "params": {"start": "2021-01-01", "end": "2026-10-30"}}'
```

`ingest_news` uses the paper instance `alpaca-paper-fwd`'s data credentials
(live instances are refused). `ingest_themes` stores theme volume/tone
for the whole range and article titles only from `articles_start` (default:
`start`). Each POST returns
`{"id": "tr-job|<utc iso>|<kind>"}`.

## 2. Build the ledger

```bash
python3 scripts/_api.py POST /trend-research/jobs \
  '{"kind": "build_ledger", "params": {"model_id": "<Models row id>", "chain_id": "honest-1", "start": "2025-03-03", "end": "2026-10-26", "max_calls": 2000}}'
```

- `start`/`end`: the honest window (weeks on or after the model's
  `research_model_cutoff`, spec C11). Weeks are built in order; a chain
  starts cold.
- `max_calls` caps LLM calls for this job. A rerun with the same params
  resumes: `ok` weeks are skipped, failed weeks retried.
- The job's `progress.ledger.model` is the model string the rows carry; it is
  the `--model` for step 4.

Then the filings for the names the ledger picked:

```bash
python3 scripts/_api.py POST /trend-research/jobs \
  '{"kind": "ingest_filings", "params": {"start": "2021-01-01", "end": "2026-10-30", "symbols": "ledger", "chain_id": "honest-1"}}'
```

## 3. Job status and stop

URL-encode the id (`|` -> `%7C`, `:` -> `%3A`):

```bash
ID='tr-job%7C2026-11-05T12%3A00%3A00Z%7Cbuild_ledger'
python3 scripts/_api.py GET  "/trend-research/jobs/$ID"
python3 scripts/_api.py POST "/trend-research/jobs/$ID/stop"
```

`state` is `queued | running | done | failed | stopped`; `progress` holds the
chunk counters (`phase: quota_wait` with `quota_wait_until` while a
subscription quota is slept out). A job whose container vanished reads
`failed` with an error starting `lost`; resubmit the same kind and params to
resume.

## 4. Lab doc and instance

```bash
python3 scripts/tr_lab_setup.py --chain honest-1 --model <ledger model string> \
  --cutoff 2025-03-01 [--prompt-version tr-v1]
```

Creates or updates doc "TR lab" and instance `strategy-tr-lab` (granularity
900, brokerage cloned from `strategy-eb`). It refuses docs 200/201 and any
brokerage that is not a paper Alpaca account other than `alpaca-main`'s,
before writing anything. It never starts the instance.

## 5. Backtest (one at a time)

```bash
python3 scripts/tr_backtest.py --start 2021-07-05 --end 2026-10-30
python3 scripts/tr_backtest.py --scorecard <backtest id>      # reprint
```

Posts granularity "900" and $5,500, refuses while any backtest is in flight,
polls, then prints `tr_scorecard` (honest / optimistic / overall). Cost
flags (`--cost-tiers etf-liquid`, `--cost-bps 25|50`) are sent only when
given. On `TIMEOUT` the run is left alone: a status can be stale, so check
its logs (`GET /backtests/<id>/logs`) and stop it with
`POST /backtests/<id>/stop`; a stuck container only dies when its
`BacktestInstances` row has `run=false`.

## 6. Journal

```bash
python3 scripts/tr_journal.py --chain honest-1 --out /tmp/tr-honest-1.md
```

Per week: regime and reasons, trends, picks with thesis and breaks-if, names
that left the book, dropped candidates with reasons, LLM calls used. Reads
Postgres read-only (`default_transaction_read_only=on`).

Every report states the known limits (spec section 9).
