# Kalshi Paper vs Real-Money Separation — Design

**Date:** 2026-07-06
**Status:** Approved (design), pre-implementation
**Scope:** A Kalshi trading instance must keep **paper** and **real-money** trading in
two logically separate spaces. Each mode's engine, API, and UI see only that mode's
data; the other mode's data is hidden but fully preserved. Toggling the paper setting
restarts the engine so the change takes effect.

---

## 1. Problem

When a live-brokerage Kalshi instance has its **paper-mode** toggle turned OFF (→ real
money), the instance still surfaces all its paper ("MOCK") data:

- The detail page shows the **PAPER P&L · PROGRESS (MOCK)** card, the **MOCK · N trades**
  summary, mock positions/history, and per-row MOCK badges in Pregame Analysis.
- The running engine still **marks paper positions every tick** (`marked 4 paper
  position(s)` appears in a `live` engine's logs).

Requirements (from the user):

1. Paper and real are **two separate spaces** — positions, decisions, P&L, pregame.
2. The engine sees **only the active mode's data** (the live engine must not read/mark/
   mutate paper positions or decisions; the paper engine must not touch real ones).
3. Flipping the paper toggle **restarts the engine** (both directions).
4. The **UI shows only the active mode**, inactive-mode data hidden but preserved, so
   switching back to paper restores everything exactly as it was.

## 2. Root cause

Paper and real are **not** separate tables. Everything lives in one set of `kalshi_*`
tables. A paper trade is a `kalshi_decisions` row with `paper: true`; a real trade is the
same row without it. That flag is stamped correctly **at write time**
(`engine.py:888` pregame fill, `engine.py:989` in-play), so existing paper data is already
tagged and safe — nothing needs migrating or deleting.

The bug is entirely on the **read side**: nothing filters by the instance's *current* mode.

- **Engine:** `mark_paper_positions()` (`db.py:244`, called `engine.py:915`) and the
  paper-expire path of `prune_finished()` (`db.py:331`) run **regardless of mode** — the
  only paper operations not already gated on `dry`. (Paper dedup `engine.py:759`, paper
  cash `engine.py:797`, paper-fill tagging `engine.py:888`, and the paper-P&L snapshot
  `engine.py:1054` are all already `if dry:`.)
- **API:** three endpoints return paper data whenever it exists, with no mode check —
  decisions/paper-block (`main.py:4537`), orders/mock (`main.py:4782`), portfolio
  paper series (`api_payloads.py:30` via `telemetry.paper_pnl_series`).
- **UI:** `KalshiInstanceDetailView.vue` computes `liveReal` but only uses it for the
  header badge (`:319`); every MOCK section renders on data-presence alone.

## 3. Design principle

**One discriminator, applied on every read.** An instance is in **REAL mode** iff
`should_execute(environment, live_enabled, paper_mode)` is true (`engine.py:38`); otherwise
it is in **PAPER/dry mode**. Note **demo** instances are also `dry` and legitimately show
paper data — so the discriminator is `should_execute` / the engine's `dry`, **not** raw
`paper_mode`. The frontend's existing `liveReal` computes the same predicate. We unify all
three layers (engine, API, UI) on this single notion of "is this instance placing real
orders right now."

- Paper data is **never moved or deleted** — it is filtered out of the active view and the
  engine's working set, and reappears intact when the mode flips back.
- Approach **A (mode-scoped reads)** chosen over **B (physical table split)**: rows are
  already correctly tagged, so scoping is a filter — no schema migration or data move on a
  live real-money dataset.

### Decision-row mode tagging (the one write-path change)

The `paper` flag is currently stamped **only on placed fills**, so skipped/blocked rows
(the Pregame board and Decision-Summary counts) carry no mode tag and can't be scoped.
Fix: stamp `paper = bool(dry)` on **every** decision row at write time (pregame
`engine.py:894`, in-play `engine.py:991`).

- Backward-compatible: existing paper-P&L / marking filters require
  `decision == "placed" AND paper == True`, so adding `paper == False` to skipped/real
  rows changes nothing there.
- The decision `id` is `f"{instance_id}|{market_ticker}|{ts}"` (`decisions.py:36`) — the
  timestamp is in the id, so each tick writes a *new* row and going live can **never**
  overwrite a paper position row.
- **No backfill.** Pre-deploy skipped rows lack the flag; they are visually identical to
  live skipped rows (skipped rows carry no MOCK marker), get superseded by fresh
  same-market rows each tick, and are pruned when their match finishes — self-healing
  within hours. We deliberately do not mutate historical rows on a real-money instance.
  (If exact historical counts are wanted immediately, a bounded non-placed-only backfill
  is a possible fast-follow — out of scope here.)

## 4. Changes by layer

### 4.1 Engine (`backend/kalshi/engine.py`, `backend/kalshi/db.py`)

- **Stamp mode on every decision row:** set `d["paper"] = bool(dry)` before the pregame
  write (`engine.py:894`) and `r["paper"] = bool(live_dry)` before the in-play write
  (`engine.py:991`). (Supersedes the placed-only `paper=True` stamps, which stay
  harmlessly consistent.)
- **Gate `mark_paper_positions()` on `dry`** (`engine.py:911-920`): the live engine does
  not mark paper positions (fixes the `marked N paper position(s)` bleed). Frozen paper
  positions keep their last mark until the instance is paper again.
- **Gate the paper-expire path of `prune_finished()` on `dry`:** add an `expire_paper:
  bool = True` param to `db.prune_finished` (default preserves backtest/paper behavior)
  and pass `expire_paper=dry` from `engine.py:944`, so the live engine never expires
  (mutates) a frozen paper position. Stale-skipped-row *deletion* stays mode-agnostic
  cleanup (it never touches placed/position rows).

### 4.2 Restart on toggle (`backend/server.py`, `backend/api/main.py`)

Because `EngineConfig` is frozen at boot and the config PATCH (`main.py:4328`) only writes
the DB row, a `paper_mode` change requires a full process restart to take effect.

- **Restart is driven by `server.py`'s existing instance changefeed** (`run_instance_change`,
  `server.py:1216`), which already owns container lifecycle + the `running_threads_objs`
  registry — so it can stop→(container gone)→start race-free (unlike a naive
  API-side `runCommand` flip, which the changefeed can collapse). When a **running**
  kalshi instance's config changes such that `old.paper_mode != new.paper_mode` (or
  derived `live_enabled` differs), the changefeed cycles the container
  (`stop_instance_container` → `start_instance_container`).
- **Safety — cancel resting orders on a live→paper switch.** Plain stop does not cancel
  resting orders (only the kill switch does), so a resting real order could fill *after*
  the user turned real money OFF. On a real→paper transition of a running instance, the
  PATCH handler cancels the instance's resting orders (reusing the scoped cancel primitive
  in `live_kill_switch.py`) before writing the config. Open real *positions* are left on
  the broker (frozen, unmanaged) — we never auto-sell; documented behavior.
- Scope the restart trigger to a **mode (paper/live) change only** — other config edits
  keep today's behavior (apply on next manual restart). This matches the user's ask and
  minimizes blast radius on a real-money engine.

### 4.3 API read scoping (`backend/api/main.py`, `backend/kalshi/*`)

Compute `show_paper = not should_execute(env, live_enabled, paper_mode)` per instance.

- **Decisions** (`main.py:4503`): filter rows to the active mode
  (`bool(r.get("paper")) == show_paper`) before building `decisions`, `summary`
  (`summarize_decisions`), and `count`. Return the `paper` block only when `show_paper`
  (else `null`). Fixes the mixed PLACED/SKIPPED counts and hides paper positions from the
  board in real mode.
- **Orders** (`main.py:4705`): build `mock` / `mock_history` only when `show_paper`
  (else empty). The handler already reads the instance row.
- **Portfolio chart series:** `portfolio_payload` keeps returning both `series` (real
  value) and `paper_series`; the *chart* selects which to show by mode (see 4.4). Where an
  endpoint is per-instance (`/instances/{id}/kalshi/equity`), it may additionally drop
  `paper_series` in real mode for defense-in-depth.

These are server-side, so the **mobile** client inherits the decisions/orders gating for
free.

### 4.4 Web UI (`frontend/src/**`)

- `KalshiInstanceDetailView.vue`: gate every paper section on `!liveReal` and real
  sections on `liveReal` — MOCK progress card (`:345`), MOCK trades summary (`:355`),
  Pregame per-row MOCK badge/paper-P&L (`:486`,`:502`), Mock positions / Mock filled
  (`:541-590`). `liveReal` (`:98`) already exists; extend its use beyond the header badge.
- `KalshiPortfolioChart.vue`: replace data-presence `isPaper` (`:55`) with a `paperMode`/
  `isReal` prop passed down from the detail page; show the paper P&L curve in paper mode,
  the real portfolio-value curve in real mode.
- Real-mode empty states: until real trades exist, real sections show a clean "no real
  trades yet" rather than paper data.
- **Pregame Analysis in real mode shows the *live* engine's decisions** (the engine
  analyzes every match each tick regardless of mode), with no MOCK/paper markers — it is
  not blanked out. The paper pregame board is preserved and restored on switch-back. This
  reads the user's "the pregame data should disappear (and be saved)" as *replace the
  paper board with the live board, preserving the paper one* — the more useful behavior
  for a running real instance. (Hiding the section entirely in real mode is a one-line
  conditional if preferred.)

### 4.5 Mobile (`mobile/lib/features/kalshi/**`)

Inherits the server-side decisions/orders gating automatically. Only the portfolio-chart
series selection is client-side — noted as a fast-follow, not in this change's core scope.

## 5. Safety & non-goals

- **No data is deleted or moved.** Paper positions/decisions/P&L persist and reappear
  intact when paper mode is re-enabled.
- **No backfill / schema migration** on the live dataset.
- Restart is scoped to a mode change on a running instance; resting real orders are
  canceled on live→paper; open real positions are left untouched (never auto-sold).
- Per repo rules: run `gitnexus_impact` before editing each symbol and
  `gitnexus_detect_changes` before committing.
- **Non-goals:** physical table separation; a paper-history "archive" browser in real
  mode; restarting on non-mode config edits; the mobile chart series selection.

### Residual risks (reviewed & accepted)

- **live→paper cancel is best-effort + the restart is async.** The API cancels resting
  real orders, then the config write triggers the `server.py` restart. Between the cancel
  and the container teardown the old (still real) engine could, in principle, place one
  more order (poll interval 30s ≫ ~5s teardown, so effectively never). This strictly
  improves on the pre-change behavior (no cancel, no restart at all). Fully closing it
  would require canceling inside the server-side restart after the old container stops —
  deferred as not worth the cross-process complexity for this window.
- **Hiding real rows in paper mode reduces observability of a *missed* restart.** If the
  engine ever failed to restart after a flip (requires a changefeed `old_val` of `None`,
  which does not occur for `.update()`s), a still-real engine's rows would be filtered
  out of the paper view. The restart guard is correct for all real update events; noted
  for completeness.

## 6. Testing strategy

- **Pure/unit (no DB):** `should_execute`-driven `show_paper`; `summarize_decisions` over a
  mode-filtered row set; `paper_pnl_from_rows` unaffected by `paper=False` skipped rows;
  decision-row stamping `paper == bool(dry)` for placed/skipped in both modes;
  `prune_finished` with `expire_paper=False` does not expire paper positions but still
  deletes stale skipped rows.
- **API:** decisions endpoint returns only active-mode rows + null paper block in real
  mode; orders endpoint returns empty mock in real mode; both return paper data in
  paper/demo mode.
- **Restart:** changefeed cycles the container on a paper_mode change of a running
  instance and not on unrelated updates; live→paper cancels resting orders first.
- **End-to-end verification:** drive the real detail page in real mode (paper sections
  gone) and paper mode (restored); confirm a `live` engine log no longer prints
  `marked N paper position(s)`.

## 7. Rollout

1. Land engine + API + UI + server.py restart behind the existing flags (no new config).
2. Deploy backend + frontend.
3. Verify on the live `Soccer Live` instance: real mode hides MOCK data and stops marking
   paper positions; toggling to paper restarts the engine and restores the paper view;
   toggling back restarts into real.
