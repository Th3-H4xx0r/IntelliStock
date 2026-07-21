# Local Backtest Harness — Design (2026-07-21)

**Goal:** Run the *real* IntelliStock backtest engine locally with the strategy's LLM routed to a free/cheap local Claude, so backtests stop burning the (nemotron/OpenRouter) LLM credits that dominate per-run cost. Reusable across arbitrary date windows. Then use it to explore a new bull-alpha direction (bull P&L up, bear held constant), validated on a multi-window suite so it doesn't overfit.

**Non-goal:** Reimplementing the engine. The prior `bt_lib` fast harness over-stated because it reimplemented fills; we run the real code instead. Not offline — max fidelity requires the real Neo4j graph + RethinkDB (behind Tailscale).

## 1. Findings that shape the design (from investigation)

- **Cost = the LLM.** ~900–1000 structured calls per 20-day run, ~75% from the per-candidate "overlay" fan-out (uncached, temperature > 0). Routing these to `claude-cli` (the local `claude` binary) makes them **$0 metered**.
- **One routing seam.** Every LLM role resolves through `_resolve_role_llm_config` (graph_nexus_analysis.py:1129), falling back to `config["llm_provider"]`/`["llm_model"]`, overridable by env `GRAPH_NEXUS_LLM_PROVIDER` / `GRAPH_NEXUS_LLM_MODEL`. One env pair redirects all roles. `claude-cli` is fully wired in both dispatchers, needs no API key, supports structured output via `--json-schema` + `--model` + `--effort`.
- **`broker.py` is a script, not a function** — its backtest loop and the ~2,400-line fill/sizing/cap/sleeve logic run at module scope. The only faithful way to run it is as a **subprocess**: `python broker.py <instance> backtest <start> <end> <granularity> <key> <secret> [symbols…] --initial-cash <c> --backtest-id <id>` (exactly how `engines/backtest_engine.py:617` launches it, minus Docker).
- **Determinism** is `PYTHONHASHSEED=0` + `BACKTEST_SEED` (env) + the model-keyed sentiment cache (forced on in backtest). Note: the official engine itself is only ~95% reproducible (uncached overlay LLM at temp > 0), so the fidelity target is "within the engine's own noise band", not byte-identity.
- **Do NOT change doc-179's model.** The sentiment/history caches are model-keyed; changing the SaaS model invalidates them and forces a costly nemotron historic-lookback rebuild. The local harness uses `claude-cli`, where any rebuild is free — so it never touches the SaaS config.
- **Infra state:** RethinkDB (`100.95.106.23:28015`) currently times out (Tailscale down). The harness preflights reachability and runs when infra is up.

## 2. Architecture

A thin, reusable runner that shells out to the real engine with three injections (cheap LLM, determinism, window) and captures the result. No engine code is modified.

```
scripts/local_backtest.py  ── builds env + CLI ──▶ subprocess: python broker.py … backtest …
        │                                                    │ (real loop, real strategy, real
        │                                                    │  PortfolioEmulator, real Neo4j/RethinkDB)
        │  ◀── streams stdout to console + <window>.log ─────┘
        └── parses final portfolio summary ──▶ <window>.result.json
```

### Components

1. **`scripts/local_backtest.py`** — the runner.
   - Args: `instance start end [--granularity 3600] [--cash 6000] [--provider claude-cli] [--model haiku] [--effort medium] [--symbols …] [--backtest-id N] [--out DIR]`.
   - Loads `.env` (RethinkDB/Neo4j hosts, Alpaca data keys, `INTELLISTOCK_CRED_KEY`), then sets: `GRAPH_NEXUS_LLM_PROVIDER`, `GRAPH_NEXUS_LLM_MODEL`, `GRAPH_NEXUS_LLM_EFFORT` (if the resolver reads it; else per-role), `PYTHONHASHSEED=0`, `BACKTEST_SEED` (default 0).
   - **Preflight** `_infra_reachable()` — socket-probe RethinkDB host:port (and Neo4j if configured), 3s timeout. If unreachable: print a clear "infra down (Tailscale?), harness ready — retry when up" and exit code 3 (distinct from a run failure).
   - Resolves Alpaca key/secret: prefer the instance's creds path used by the engine; fall back to `.env` `ALPACA_KEY`/`ALPACA_SECRET` (these fetch bars).
   - Runs the subprocess with `cwd=backend/`, merged env, line-streamed stdout tee'd to `<out>/<instance>_<start>_<end>.log`.
   - On exit, calls the parser and writes `<...>.result.json`.
2. **`local_backtest_result.py` (parser)** — parse the broker's final portfolio summary from the log (the `PORTFOLIO SUMMARY` / `Total P&L` / `pnl_percent` / per-stock block, same fields `pull_backtest_logs`/`/summary` expose). Returns `{pnl, pnl_percent, per_stock, buys, sells, final_cash, positions_value}`. Robust to missing fields (returns partial + a `parsed_ok` flag).
3. **`local_backtest_windows.json`** — the multi-window suite: named windows tagged `regime: bull|bear`, seeded with the two known windows (bull 2026-03-30→04-27, bear 2026-03-02→03-30) plus explicitly-labeled out-of-sample slots to fill with additional historical bull/bear months. Editable; the mechanism (multi-window run) is the deliverable, not the specific dates.
4. **`--suite` mode** — iterate the suite (sequential, since the engine is one-run-per-instance), tabulate `window | regime | pnl% | duration`, write a combined `suite_<ts>.json`. Prints a bull-mean / bear-mean summary.
5. **`--assert-bear-unchanged` mode** — run the bear windows, diff each `pnl%` against a saved `bear_baseline.json` (tolerance for the ~5% engine noise band), exit non-zero if any bear window moved beyond tolerance. This is the guardrail for "bull first, bear constant".

### LLM provider choice
Default `claude-cli` + Haiku + medium effort ($0 metered, slower: spawn-per-call, ~5–30s each, 10 concurrent). Fallback `--provider openrouter --model anthropic/claude-haiku` (metered ~$1–3/run, faster, no CLI spawns) for when iteration speed matters. Both selectable per run.

## 3. Fidelity & validation

- **Offline-validatable now (no infra):**
  1. `claude-cli` structured smoke test — call the provider with a trivial Pydantic schema, assert a valid parsed object comes back from the local binary. Gates the whole LLM approach.
  2. Env-routing test — set `GRAPH_NEXUS_LLM_PROVIDER` and assert `_resolve_role_llm_config` returns `claude-cli` for every role.
  3. Runner unit tests — CLI-arg construction, preflight detects unreachable infra (exit 3), result parser on a captured official log.
- **Infra-dependent (when Tailscale up):**
  4. Calibration run — one harness run (Claude LLM) vs the official nemotron result on the bear reference window; report the P&L delta so we *measure* the LLM-swap's fidelity cost rather than assume it.
  5. Establish `bear_baseline.json` from a harness bear-suite run; thereafter `--assert-bear-unchanged` guards it.

## 4. Reusability & anti-overfit

- Any window via args; the suite for breadth. Bull-alpha changes are accepted only if they improve the **bull-window mean across the suite** while every **bear window stays within tolerance** — never judged on a single window.
- Instance-parameterized (works for any instance/doc, not just alpaca-main).
- Model/effort parameterized so cost/speed/fidelity is a per-run dial.

## 5. Risks

- **Infra gating:** can't produce real numbers until Tailscale is up. Mitigation: build + offline-validate now; the runner preflights and waits.
- **claude-cli speed:** ~900 cold calls × spawn overhead. Mitigation: Haiku/medium default; warm sentiment cache after first run; openrouter+haiku fallback for speed.
- **claude-cli reliability/rate-limits** for ~900 structured calls on a subscription. Mitigation: the smoke test + a small-window first run surface this early; fallback provider.
- **Cache pollution:** claude-keyed cache rows written to prod RethinkDB (additive, don't touch nemotron rows) — acceptable; optionally target a dedicated backtest instance later.

## 6. Out of scope (separate, follows the harness)
The actual new bull-alpha strategy direction. The harness is the enabler; strategy exploration is a distinct spec once the harness can run.
