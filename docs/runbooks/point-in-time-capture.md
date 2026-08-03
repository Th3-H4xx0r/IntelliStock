# Runbook — point-in-time capture (making backtests describe live)

## Why this exists

Every equity backtest in this repo logs:

```
PIT RESEARCH MODE: no frozen snapshots for <ts>; running the legacy
current-state path. This result carries lookahead bias and is NOT
promotion-eligible.
```

That warning is accurate. The strategy reads the Neo4j graph **at today's
state** for a decision dated months ago, so it can see relationships that did
not exist at decision time. No amount of code review fixes it — it is a missing
**data** problem. `PointInTimeManifests` and `PointInTimeDatasetSnapshots` are
the fix, and as of 2026-08-03 both have **0 rows**.

## Why they are empty

Capture is gated by `_pit_capture_enabled` (`strategies/graph_nexus_analysis.py`):

```python
if context is None or not context.is_live:   return False   # LIVE ticks only
if str(mode or "FULL").strip().upper() != "FULL":  return False
raw = config.get("pit_capture_enabled") or os.environ["PIT_CAPTURE_ENABLED"]
```

**Capture only happens on live FULL ticks.** It can never be produced by a
backtest — deliberately, because a bundle captured during a backtest would
certify the lookahead-contaminated current-state graph *as* point-in-time,
which is worse than having no data at all: the contamination becomes invisible
to everything downstream.

So the registry stays empty until an equities instance runs live ticks.
`alpaca-main` is real money and stopped, `main` is retired, and the only
running instance is crypto. Hence: zero rows.

The gate's contract is pinned in `backend/tests/test_nexus_pit_capture.py`.

## The capture instance

| | |
|---|---|
| instance | `alpaca-paper-pit` |
| strategy doc | **182** (`Nexus Only — PAPER PIT capture`) |
| brokerage | `bf78ad0c-…` — Alpaca **paper**, account `PA3IBY5S84PG` |
| cadence | 900s, matching `alpaca-main` |

### Why a separate strategy doc, not a second instance on 179

Two instances sharing a strategy doc share **mutable Nexus state**. Pointing a
paper instance at doc-179 would pollute `alpaca-main`'s history — a real-money
instance the user may restart. doc-182 is a copy with its own
`history_scope_salt` (`paper-pit-2026-08-03`), so the two never interact.

Do not "simplify" this by moving the paper instance onto 179.

## Starting it

Setting `runCommand=True` alone was **not sufficient** on 2026-08-03. The
`Instances` changefeed in `server.py` is supposed to spawn the container, and it
did not (no `LiveBootAudit` row, no LLM calls, no `uptimeStart`) across two
attempts an hour apart with the API confirmed answering.

A redeploy does **not** fix it either — that was tried and it failed. The
crypto instance `test` has carried `uptimeStart=2026-07-27` across every deploy
since, which means **deploys do not restart instance containers**; the startup
scan did not launch `alpaca-paper-pit` when the backend redeployed on
2026-08-03.

So as of 2026-08-03 there is **no known DB-only way to launch a
freshly-created equity instance.** Two paths were tried and both failed:

| attempt | result |
|---|---|
| `runCommand=True` (changefeed) | no container, twice, API confirmed up |
| redeploy backend (startup scan) | no container; `test` uptimeStart unchanged |

Diagnosing further needs host-side visibility that is not reachable from the
DB: the backend container's logs around a start attempt, and whether
`DOCKER_INSTANCE_IMAGE` (default `intellistock-backend`) resolves via
`client.images.get(...)` on that host. `start_instance_container` returning
None is silent from every vantage point available here.

The instance row is left `runCommand=True` so it launches as soon as whatever
is wrong is fixed. It costs nothing while it is not running — measured LLM
spend while armed-but-unbooted was **$0.00**.

## Confirming it works

```
PointInTimeManifests    count > 0     # the thing that unblocks everything
PointInTimeDatasetSnapshots count > 0
```

Bundles accrue one per live FULL tick. Once a window is covered, a backtest over
that window resolves frozen snapshots instead of the legacy current-state path,
and the `NOT promotion-eligible` warning stops being correct.

Audit coverage with `backend/scripts/audit_point_in_time_coverage.py`.

## Cost

Roughly **$0.05–0.10/day** of LLM spend, extrapolated from backtests at ~$1.30
per 25 simulated days. It is not an open-ended burn, but it is not free either —
stop the instance when a window is covered.

## What this does NOT fix

Certifiable data makes backtests *honest*, not *profitable*. Measured
separately: gross edge ≈ zero, and ~290%/month turnover converts it to a loss.
See `project_turnover-is-the-leak`. PIT capture removes an excuse, not a
deficit.
