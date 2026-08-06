# Session state — 2026-08-06/07

Everything below is pushed to `main` and verified live via
`python3 scripts/check_deployed_code.py` (exits 0 when the deployed container
matches your working tree; 2 while it is rebuilding). The backend
**auto-deploys from main**, so a push restarts the container — never push while
a backtest is running.

## Shipped

| commit | what |
|---|---|
| `b3bebc6` | `/health` reports source hashes; `scripts/check_deployed_code.py` proves prod runs your commit |
| `069dbc2` | dead-key registry 1 → 38 entries + unsatisfiable-band check |
| `461fdc1` | `min_hold` clock stamps on submission; missing-market-cap dollar-volume escape; `fast_loser_cut_enabled` |
| `9347884` | core stays turnover-exempt through warm-up; executable-buy drop now logs |
| `e4fc91c` | `overlay_buy_block_enabled` — the LLM buy veto is switchable |
| `4a2aadf` | resolver WARNs when a `model_id` contradicts the model name beside it |
| `239a60c` | stalled structured call is re-issued on the same model, escalating deadline |
| latest | deploy check covers `llm_utils.py` + `model_resolver.py` |

## The finding that outranks the rest

**doc-193 cannot reach 2x, whatever the stock picking.** The core target is
**74.1%** of NAV (the run logs it; `core_target_pct: 0.60` only floors the bear
de-risk — `core_target_weight` is residual-driven, so as the satellite shrinks
the core grows and squeezes it further). That leaves 23.9% for stocks; at bull
`max_positions=14` each name is **1.71% of NAV**.

- A second SNDK (+1,847%, best of 536, 3.0% base rate) contributes **+31.5pp**.
- Doubling from one winner would need **+5,367%**.
- If **every** satellite name triples, the portfolio returns **+56%**.

The only lever is core weight, and it is a real trade-off — the core is what
makes bear regimes survivable and what cut turnover 66.5 → 16.4×/yr. Full
tables in `2026-08-06-why-a-winner-never-survives.md`.

## Config state

- **doc-193** (`v2-let-run-core`): all 13 LLM roles on `openrouter/nemotron-3-ultra-550b`
  except `company_article` + `lookback_company_article` on `bedrock/gpt-oss-120b`.
  `overlay_buy_block_enabled=false`, `downtrend_protection_sell_underperformers_pct=-25`,
  `fast_loser_cut_enabled=false`, winner-lock drawdown 30%, bull/recovery overlays
  no longer re-arm the exits the base config disables.
- **doc-179** (REAL MONEY, `alpaca-main`, stopped): only two defect repairs applied —
  `nexus_monitor_risk_exit_always_enabled=true` (risk exits were computed and
  **discarded** in bear/chop/crash) and the empty `5 > 3` winner-lock band. The
  let-winners-run reshaping was **not** applied; 179 is unvalidated for that shape.
- Backups for every change are in `scripts/doc*_backup_*.json`.

## In flight

**bt 249191** — doc-193, 2025-11-10..2026-02-24 @900s. Confirmed 178 nemotron /
14 gpt-oss / **0 nano**. Watch for: non-SPY names held past the old 5-14 day
exits, 0 `TURNOVER BUDGET BINDING`, 0 `ML overlay BUY_BLOCK`.

## Traps worth carrying forward

1. **`'llm_model_id'.endswith('_llm_model_id')` is False.** The unprefixed main
   role has no leading underscore. A role sweep that misses it will also verify
   as clean if the verifier derives roles the same way — which is how a nano
   call survived three "all consistent" checks. Enumerate by the id key; verify
   by *resolved model* against an allowlist.
2. **`LLMUsage` flushes on terminal status.** A running backtest shows 0 rows
   even while making calls. Read the log for models in flight.
3. **Predictions from config and code were wrong 3 times out of 4** this
   session — the quality filter was not the mass blocker, the turnover leak was
   not the bear transition, the trailing stop was already inert. Every one was
   corrected by a runtime log. Read a run before believing a mechanism.
4. **A stopped run still costs money.** Two runs were killed after diagnosis
   rather than left to finish on code known to be broken; that was the cheaper
   call both times.

## Open

- Core-weight decision (the 2x ceiling) — user's call, not a bug.
- doc-179 churn-heavy sleeve pairing (task #2) still untouched.
- EDGAR veto is wired and tested but never validated in a backtest.
- Budget: ~$34 of OpenRouter credit consumed against ~$33 provided, before this
  evening's runs. Nemotron-ultra is $0.60/$3.60 per M vs nano's $0.20/$1.25;
  `nvidia/nemotron-3-super-120b-a12b` is $0.085/$0.40 with 12B active params if
  speed or cost becomes the binding constraint.
