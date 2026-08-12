# Anchor broker safety bug sweep

Read-only sweep of the current execution-aware anchor diff in:

- `backend/broker.py`
- `backend/strategies/graph_nexus_analysis.py`
- `backend/tests/test_anchor_execution_contract.py`

No production code, configuration, broker state, or backtest state was changed.

## Verdict

**Not ready to enable.** The default-OFF/live block, lane-local cap, satellite-floor admission, source-tagged next-event order, and fill callback are present. Four concrete outcome/pre-funding gaps remain.

## Must fix

### 1. Core pre-funding turnover check is stale before the core sell it causes

The pre-pass computes `used + planned_anchor` at `backend/broker.py:14835-14850`. If that passes, `_residual_sleeve_release` runs at `:14928-14937`; an accepted core sell is itself recorded in the same rolling ledger at `:4453-4458`. The execution check then rereads that larger ledger at `:15547-15561` and can block the anchor at `:15563-15578`.

Minimal reproduction with the real extracted helper, NAV `$6,000`, 75% already used, and a `$240` anchor/core release:

```text
pre_funding (True, 0.79)
execution_after_core_sell_booked (False, 0.8300000000000001)
```

Thus the pre-pass can sell core for an order the later lane ceiling refuses—the exact release/redeploy churn it claims to prevent. It must project the core-release notional as well as the anchor buy, using the same ledger semantics as the final gate.

### 2. Core pre-funding still does not replay position headroom or actual same-tick fundability

`_anchor_reinforcement_position_headroom` is defined at `backend/broker.py:3530-3544` but has **no production caller** (only the new test calls it). The funding pre-pass adds the full `_fr_cash` at `:14863-14871`; the actual 15%-25% lane cap/headroom can reject much later at `:15756-15812`. A held name with less than the policy minimum of cap headroom can therefore trigger a core sell and then be blocked.

The cash path has the same mismatch. After a next-event core sell is submitted, the buy ceiling still starts from raw `get_cash()` at `:15694` and clamps `cash_to_use` at `:15742-15743`. `_exec_fundable_amount` can only reduce that already-clipped request (`:3645-3650`); even optional pending-sell credit in `get_buying_power` cannot size it back up. The new anchor minimum check at `:15965-15978` prevents a runt, but does so **after** core release, converting the defect into a block/churn rather than making pre-funding work. This needs the requested real cash/reservation/T+1/core-sell contract test.

Also, exclusions in the new pre-pass only replace `_core_funding_request` inside `if _fr_room is not None` (`:14876-14895`). If satellite-headroom measurement fails open and returns `None`, the original request summed at `:14680-14683` survives, including research-only/invalid anchors that the local buckets excluded.

### 3. Accepted no-fill/cancel and at least one broker block leave pending state stuck

Safe planning stores `_anchor_reinforce_pending` at `backend/strategies/graph_nexus_analysis.py:10912-10919`, and that state suppresses every later plan at `:10834-10837`. Broker fill handling clears it only on a final fill at `backend/broker.py:11134-11158`; the main callback is invoked only for emitted fills at `:12518-12522`.

There is no anchor cancellation/expiry/end-of-run/no-next-event reconciliation and no `ANCHOR NO FILL` state. This is reachable when passive execution expires an accepted order or when no later price event exists. The order disappears from simulator pending orders without a fill callback, so the ticker remains planner-pending forever (or is unreconciled at run end).

A broker block is also missed: split-guard BUY suppression goes directly to `continue` at `backend/broker.py:16145-16154` without `_anchor_reinforcement_block`. All terminal block/cancel/expire/no-fill outcomes must clear pending without committing the stage.

### 4. A blocked/unfilled anchor still changes the unrelated new-entry lane

The planner deducts every planned anchor immediately from the stock slate at `backend/strategies/graph_nexus_analysis.py:30310-30313`. Broker BLOCK, rejected submission, or accepted-but-unfilled execution happens later and cannot refund that same-bar budget. Therefore a treatment with zero anchor exposure can still change unrelated new-entry selection. The new test does not assert unrelated-lane invariance.

This is especially material while findings 1-3 create valid plans that later block. Either pre-admission must match the broker closely enough to make the reservation authoritative, or the experiment must explicitly account for this non-anchor treatment effect.

## Nonblocking / correctly wired

- **Default OFF and live fail-closed:** strategy safe mode is gated at `graph_nexus_analysis.py:10797`; broker additionally requires the source marker and marks non-backtest modes research-only at `broker.py:3491-3496`. Invalid/research-only anchors are blocked before normal execution.
- **Lane-local 15%-25% cap:** the policy is capped by the explicit document cap, strategy cap, and 25% at `broker.py:3497-3503`; only anchor orders override the normal broker fraction at `:15756-15760`. Other BUY sources retain the existing cap.
- **Satellite floor admission:** `allow_core_floor` changes only the anchor's satellite admission at `broker.py:15361-15368`; it does not forge the raw score for unrelated lanes.
- **Independent final turnover gate:** every anchor reaches the explicit ceiling even when the general budget is not binding (`broker.py:15540-15578`), and `_tb_bypass` is reset per symbol at `:15516`.
- **Reservations/T+1 minimum at final gate:** `_exec_min_position_gate` produces the emulator-fundable amount and the anchor-specific check at `:15965-15978` removes the held-add exemption below the anchor minimum. The missing piece is agreement with pre-funding, not the final clamp.
- **Order provenance/callback:** the submitted source is `anchor_reinforcement:stage=...` at `broker.py:16360-16374`; actual main-loop fills call `_apply_backtest_confirmed_fill_state` at `:12518-12522`.

## Test result and coverage gap

```text
PYTHONPATH=. pytest -q backend/tests/test_anchor_execution_contract.py
14 passed, 6 warnings
```

The suite passes, but the broker-main assertions at `backend/tests/test_anchor_execution_contract.py:208-238` are source-string checks. The emulator tests submit directly and manually invoke the fill callback at `:287-295` and `:310-315`. They do not drive core release, raw cash vs buying power, in-flight reservations, T+1 withholding, passive expiry/cancel/no-fill, pre-funding position headroom, or unrelated-lane invariance; consequently the failures above are not exercised.
