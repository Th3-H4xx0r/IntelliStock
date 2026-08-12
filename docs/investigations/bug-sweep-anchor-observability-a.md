# Bug sweep A — anchor observability diff after bt735390

**Date:** 2026-08-10
**Base:** `960a469fee5776544df1a3bfeb7b84fb3c8eeacf`
**Scope:** only the current uncommitted changes in `backend/broker.py` and `backend/tests/test_anchor_execution_contract.py`. No code, configuration, or git operation was performed; the only written artifact is this requested report.

## Verdict

**REQUEST CHANGES.** I found one **MEDIUM** telemetry-correctness defect and two **LOW** failure/provenance hazards. I found no normal-path change to order execution, fills, quantities, cash, stage completion, scheduled-position ownership, or residual-sleeve state. The terminology-only turnover edits are accurate and behavior-inert.

The two independent reports were read in full:

* `agent-anchor-735390-cap.md`: the real UUUU fill was 19.483069% at the immediate emitted snapshot; its later >20% state was appreciation/relative-NAV drift, not overfill.
* `agent-anchor-735390-turnover.md`: the generic turnover ledger is accepted-request notional, the p7 order was admitted at 79.8855%, and the old generic fill line lacked source and appeared after source-specific reconciliation.

The pending diff correctly addresses those two wording/order gaps in principle, but its new NAV diagnostic is not generally a current fill-snapshot valuation.

## Findings

### A-1 — **MEDIUM** — `ANCHOR FILL nav/weight` can value every non-fill holding at a stale prior-snapshot mark, causing false or missed `ANCHOR CAP DRIFT`

**Code:** `backend/broker.py:11222-11227`, reached from the pending-fill loop at `12653-12675`.

The new code builds `fill_marks` from `portfolio_emulator._last_prices` and overwrites only the anchor symbol. That is not necessarily the current tick's portfolio mark map:

1. The loop has already computed the current `prices` map at `broker.py:12629` and adds current pending events at `12649-12652`.
2. It does not pass that map to `_apply_backtest_confirmed_fill_state`.
3. `PortfolioEmulator.apply_fill` updates `_last_prices` only for the filled symbol (`portfolio_emulator.py:12198-12205`).
4. All current tick marks are not copied into `_last_prices` until the later snapshot call (`portfolio_emulator.py:903-933`, especially `925-926`).

Therefore the claimed `fill-snapshot` NAV combines the anchor's new mid with prior-snapshot marks for all other holdings. This can change both the logged weight and whether the warning fires. It does not change the portfolio or stage state, but it makes the new verification signal untrustworthy near the cap.

**Minimal real-emulator reproduction:** seed WIN at $1,000, OTHER at $4,000, and $1,000 cash; save a $100 prior snapshot; fill the accepted $200 WIN add at $100 on the next event while the current tick has OTHER at $50. The helper logs the stale $100 OTHER mark:

```text
ANCHOR FILL: WIN ... position_value=$1200.00 nav=$6000.00 weight=20.0000% admission_cap=20.00%
ANCHOR STAGE COMMIT: WIN ...
actual current tick NAV: 4000.0 weight: 30.0
```

No `ANCHOR CAP DRIFT` is emitted even though the actual current tick weight is 30%. Reversing the other holding's move can analogously create a false positive. The added test cannot catch this because `_configured_emulator()` holds only WIN plus cash.

The bt735390 reconstruction is not overturned: its independently reconstructed emitted snapshot remains 19.483069%. The defect is in the general telemetry implementation, not evidence of a hidden UUUU overfill.

### A-2 — **LOW** — the logged “decision-time admission cap” is recomputed at fill from mutable module global configuration

**Code:** `backend/broker.py:11228-11234`.

The fill does not carry the cap used at admission, and the pending record does not store it. Instead the helper re-reads `_cached_strategies` at fill time. The current backtest boot path freezes this list, so bt735390 is unaffected. Nevertheless, the diagnostic is not intrinsically the *decision-time* value and becomes wrong if in-memory config is mutated/reloaded or this helper is reused in a resumable/reloadable path.

**Reproduction:** admit the order under 20%, change only `NS['_cached_strategies']` to 25% before the next-event fill, and fill at $120:

```text
ANCHOR FILL: WIN ... nav=$6200.00 weight=22.5806% admission_cap=25.00%
ANCHOR STAGE COMMIT: WIN ...
```

No drift line appears, although 22.5806% exceeded the actual 20% admission cap. The robust provenance is the cap recorded with the accepted plan/order, not a later global lookup.

### A-3 — **LOW** — moving generic logging before reconciliation changes failure-path state ordering

**Code:** `backend/broker.py:12656-12675`.

On the normal path this is behavior-inert and gives the intended grep order:

```text
[execution] FILL ... source=...
ANCHOR FILL ...
ANCHOR STAGE ...
```

However, it is not strictly “observability only” under a log failure. `process_price_events` has already applied cash and position mutations before returning fills. If the now-first `_log` raises, `_apply_backtest_confirmed_fill_state` is never called. Depending on source, the following already-existing metadata stays stale:

| source/fill class | reconciliation skipped after the portfolio fill |
|---|---|
| `anchor_reinforcement:*` | seen/fill ledger, pending removal, stage partial/commit |
| any final sell with anchor execution enabled | full-exit episode reset |
| `scheduled_start:*`, `scheduled_same_bar:*` | `_earnings_positions` ownership |
| `residual_bear*` | confirmed quantity, entry/peak/exit metadata |
| `residual_bull*` buy | `last_park_ts` |
| `main_signal` / unknown buy | no source-specific metadata |

`_log` normally catches the primary logger exception, so this requires both the primary logger and fallback stdout to fail. It is still reachable: AST-extracting the real `_log`, making the primary sink raise `OSError`, and making `print` raise `BrokenPipeError` yields:

```text
('BrokenPipeError', 'stdout closed')
```

Before this diff, the same generic-log failure occurred only after reconciliation. The outer broker loop then terminates the backtest either way, so severity is LOW rather than MEDIUM, but the state-order change is real and is not covered by the new source-order text assertion.

## Focused audit results with no additional finding

### Generic source coverage, compatibility, and secrets

The production next-event sources reaching this loop are:

* `main_signal`;
* `anchor_reinforcement:stage=<integer>:plan=<ticker>:s<stage>:p<sequence>`;
* `scheduled_start:<strategy-name>` and `scheduled_same_bar:<strategy-name>`;
* `residual_bull_deploy`, `residual_bull_refill`, `residual_bull_protective_exit`;
* `residual_bear_deploy`, `residual_bear_refill`, `residual_bear_full_exit`, `residual_bear_stop_exit`, and `residual_bear_protective_exit`.

All normal `SimulationFill` objects already normalize `source` to a string, so `%s` formatting and colons are safe. The source strings contain no broker key, secret, model credential, account identifier, or API header. Scheduled sources add only the strategy name, which is already printed in existing pending-trade logs. The normal `IntelliStockLogger.log` path redacts known secrets before console, file, and buffer sinks. I found no credential leak on production source paths.

Appending `source=...` preserves the complete old prefix and key order, so grep/substr and key/value parsers remain compatible. There is no in-repo parser of the human `[execution] FILL` line. An external strict regex anchored at `model=...$` will break because `model` is no longer the last field; this is an expected format extension but should be treated as a log-schema/version compatibility note. Arbitrary `SimulationOrder.source` strings are not length/control-character sanitized, but broker-owned producers above do not supply such values.

### NAV arithmetic, divide by zero, and side effects

* `fill_weight = current_value / fill_nav if fill_nav > 0 else 0` prevents division by zero.
* A zero/negative/non-numeric NAV is represented as `nav=0, weight=0` after the caught exception/guard and suppresses drift. That is diagnostically lossy, but ordinary long-only `PortfolioEmulator` execution keeps finite nonnegative cash/prices, so I did not raise a separate reachable defect.
* The anchor mark itself is correct: `apply_fill` stores the quote mid in `_last_prices[fill.symbol]`, and the helper uses that mid rather than execution price.
* `get_portfolio_value` is read-only and receives a copied dict. The new block does not trim, submit, mutate positions/cash, or alter stage completion. `remaining` and stage commit still use the same anchor mid/current quantity as before.
* `ANCHOR CAP DRIFT` is explicitly only a fill-time diagnostic; it does not and should not claim to detect later appreciation such as UUUU's Jan 20 15:00 crossing.

### AST extraction/import safety and tests

`broker.py` remains intentionally non-import-safe. The test uses `ast.parse`, compiles only named top-level functions, and includes the new dependency `_core_sleeve_cfg_raw` in `WANTED`; no broker main loop/import side effect is triggered. The new test mutates only its real emulator and test namespace. Its main coverage gap is that the one-position book makes `_last_prices` equivalent to a full price map, and the generic fill-loop test is static source slicing rather than runtime logging/reconciliation.

## Validation run

```text
PYTHONPATH=. pytest -q backend/tests/test_anchor_execution_contract.py
22 passed

PYTHONPATH=. pytest -q \
  backend/tests/test_anchor_execution_contract.py \
  backend/tests/test_portfolio_emulator_fills.py \
  backend/tests/test_backtest_broker_fill_wiring.py \
  backend/tests/test_backtest_execution_costs.py
51 passed

git diff --check -- backend/broker.py backend/tests/test_anchor_execution_contract.py
clean
```

GitNexus reported its index current at `960a469`; `detect-changes --scope unstaged` reported LOW aggregate risk, although the graph did not index this very large module's top-level helper/loop well enough to provide a useful caller result. Direct source tracing establishes the single pending-fill caller described above.
