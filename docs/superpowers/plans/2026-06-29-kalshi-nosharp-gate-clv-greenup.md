# Kalshi Soccer Bot — No-Sharp Gate · Bigger Trades · CLV Close · Green-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the paper "Soccer Live" bot (`032f0c62`) place meaningful ($3+) simulated trades, stop betting phantom model-only edges on games with no sharp line (while keeping that volume when the edge is genuinely large), grade its own CLV so we can tell if it's +EV, and bank in-play gains on small positions.

**Architecture:** Four narrow changes. **P1** adds a higher edge bar for markets with no sharp line (pure, in `strategy/candidates.py`, threaded from `orchestrator.plan_and_allocate`, configured via a new `no_sharp_edge_threshold` cap). **P2** is a prod DB config edit (bankroll/Kelly/per-bet-cap) — no code. **P3** stamps a rolling closing reference (`sharp_close_prob` + `pre_settle_mid_cents`) onto open placed decisions each tick so `reconcile.settle_and_learn` can grade true CLV (pure helper in `reconcile.py` + thin DB writer in `db.py` + one call site in `engine.run_instance`). **P4** lets the in-play take-profit fire on single-contract positions (pure, `live/live_decision.py`).

**Tech Stack:** Python 3.14, pytest, RethinkDB (prod at Tailscale `server7` 100.95.106.23:28015, DB `IntelliStock`). Trades are PAPER (`paper_mode=ON`) — nothing is submitted to the exchange. Honor CLAUDE.md: run `gitnexus_impact` before editing each symbol; `gitnexus_detect_changes` before committing.

**Context for the changes (verified against source):**
- Sizing is `kelly_fraction × edge/(1−price) × bankroll` (`risk.py`, `capital/planner.py`). At the live $54 bankroll trades are sub-$3; P2 fixes that.
- `fuse()` already caps model overconfidence ABOVE the sharp prob (`intelligence/fusion.py:cheap_side_cap`) — but only when a sharp line exists. No-sharp markets get `base = model` uncapped. P1 gates those by edge instead of hard-skipping (user's explicit steer: keep volume when edge is good).
- `reconcile.reconcile_position` grades CLV from `sharp_close_prob` (graded) or `pre_settle_mid_cents` (ungraded). `pre_settle_mid_cents` is NEVER written today and `sharp_close_prob` is set once at entry; P3 makes both roll forward to the close.
- In-play take-profit (`live_decision.decide`) requires `held >= 2`, so it can never bank a 1-contract position — the dominant size today. P4 fixes that.

---

### Task 1 (P1): No-sharp higher edge bar in `generate_candidates`

**Files:**
- Modify: `backend/kalshi/strategy/candidates.py` (`generate_candidates`)
- Modify: `backend/kalshi/orchestrator.py:52` (call site — thread `sharp_probs` + `no_sharp_edge_threshold`)
- Modify: `backend/kalshi/risk.py` (`RiskCaps` — add `no_sharp_edge_threshold: float = 0.0`)
- Modify: `backend/kalshi/instance_config.py` (`normalize_config` default 0.08; `risk_caps_from_config` read)
- Test: `backend/tests/test_kalshi_candidates.py`

- [ ] **Step 1: Write failing tests** in `test_kalshi_candidates.py`:

```python
def _winner_markets():
    return [
        {"market_ticker": "KX-HOME", "market_type": "winner", "side": "home", "yes_ask_cents": 40},
        {"market_ticker": "KX-AWAY", "market_type": "winner", "side": "away", "yes_ask_cents": 30},
    ]


def test_no_sharp_market_needs_higher_edge_bar():
    # home fair 0.50 @ 40c -> ~+6% net edge: clears the 3% base bar but NOT the 8% no-sharp bar
    probs = {"winner": {"home": 0.50, "away": 0.30}}
    cands = generate_candidates(
        "f1", "medium", probs, _winner_markets(),
        fee_rate=0.07, edge_threshold=0.03,
        sharp_probs={},                 # no sharp line for this fixture
        no_sharp_edge_threshold=0.08,
    )
    assert [c.market_ticker for c in cands] == []   # gated out: model-only, edge < 8%


def test_sharp_market_uses_base_bar():
    probs = {"winner": {"home": 0.50, "away": 0.30}}
    cands = generate_candidates(
        "f1", "medium", probs, _winner_markets(),
        fee_rate=0.07, edge_threshold=0.03,
        sharp_probs={"winner": {"home": 0.50}},   # sharp present for home
        no_sharp_edge_threshold=0.08,
    )
    assert "KX-HOME" in [c.market_ticker for c in cands]   # base bar applies, placed


def test_no_sharp_big_edge_still_traded():
    # home fair 0.80 @ 40c -> ~+38% edge: clears even the 8% no-sharp bar (keeps volume)
    probs = {"winner": {"home": 0.80, "away": 0.10}}
    cands = generate_candidates(
        "f1", "medium", probs, _winner_markets(),
        fee_rate=0.07, edge_threshold=0.03,
        sharp_probs={}, no_sharp_edge_threshold=0.08,
    )
    assert "KX-HOME" in [c.market_ticker for c in cands]


def test_no_sharp_bar_disabled_by_default():
    probs = {"winner": {"home": 0.50, "away": 0.30}}
    cands = generate_candidates(
        "f1", "medium", probs, _winner_markets(),
        fee_rate=0.07, edge_threshold=0.03, sharp_probs={},
    )  # no_sharp_edge_threshold defaults 0.0 -> unchanged legacy behavior
    assert "KX-HOME" in [c.market_ticker for c in cands]
```

- [ ] **Step 2: Run, verify fail** — `cd backend && python3 -m pytest tests/test_kalshi_candidates.py -q` → FAIL (unexpected kwargs / wrong gating).

- [ ] **Step 3: Implement.** In `candidates.py` add params and gating. New signature:

```python
def generate_candidates(
    fixture_id: str,
    tier: str,
    market_probs: dict,
    kalshi_markets: list[dict],
    *,
    fee_rate: float,
    edge_threshold: float,
    min_price_cents: int = 15,
    max_price_cents: int = 90,
    draw_min_edge: float = 0.10,
    sharp_probs: dict | None = None,
    no_sharp_edge_threshold: float = 0.0,
    collect_skips: bool = False,
):
```

Inside the per-market loop, after computing `gate` (the draw/base bar) and BEFORE the `if e <= gate` check, raise the bar for no-sharp markets:

```python
        gate = max(edge_threshold, draw_min_edge) if side == "draw" else edge_threshold
        has_sharp = ((sharp_probs or {}).get(mt) or {}).get(side) is not None
        if no_sharp_edge_threshold and not has_sharp:
            gate = max(gate, no_sharp_edge_threshold)
        if e <= gate:
            _skip(m, side, fair, price, e,
                  f"edge {e * 100:.1f}% <= bar {gate * 100:.1f}%"
                  + ("" if has_sharp else " (no sharp line)"))
            continue
```

In `orchestrator.py:52` pass the new args:

```python
        cands, skips = generate_candidates(
            fx["fixture_id"], tier, fused, fx.get("kalshi_markets", []),
            fee_rate=fee_rate, edge_threshold=edge_threshold,
            min_price_cents=getattr(caps, "min_price_cents", 15),
            max_price_cents=getattr(caps, "max_price_cents", 90),
            draw_min_edge=getattr(caps, "draw_min_edge", 0.10),
            sharp_probs=sharp,
            no_sharp_edge_threshold=getattr(caps, "no_sharp_edge_threshold", 0.0),
            collect_skips=True,
        )
```

In `risk.py` `RiskCaps`, add field after `draw_min_edge`:

```python
    no_sharp_edge_threshold: float = 0.0    # 0 = off; higher edge bar for markets with no sharp line
```

In `instance_config.py`: add to `normalize_config` return (near `draw_min_edge`):

```python
        "no_sharp_edge_threshold": float(raw.get("no_sharp_edge_threshold", 0.08)),
```

and to `risk_caps_from_config`:

```python
        no_sharp_edge_threshold=float(c.get("no_sharp_edge_threshold", 0.0)),
```

- [ ] **Step 4: Run** — `python3 -m pytest tests/test_kalshi_candidates.py tests/test_kalshi_orchestrator.py tests/test_kalshi_instance_config.py -q` → PASS. Fix `test_kalshi_instance_config.py` only if it asserts an exact key set (add the new key to expectations).

- [ ] **Step 5: Commit** — `git add -A && git commit` (message: "feat(kalshi): higher edge bar for no-sharp markets (keep volume, kill phantom favorite edges)").

---

### Task 2 (P3): Roll closing reference onto open placed decisions for CLV

**Files:**
- Modify: `backend/kalshi/reconcile.py` (add pure `close_ref_updates`)
- Modify: `backend/kalshi/db.py` (add `update_close_refs` thin writer)
- Modify: `backend/kalshi/engine.py` (call it once per tick, ~after `mark_paper_positions`)
- Test: `backend/tests/test_kalshi_reconcile.py`

- [ ] **Step 1: Write failing test** in `test_kalshi_reconcile.py`:

```python
from kalshi.reconcile import close_ref_updates


def test_close_ref_updates_open_placed_only():
    rows = [
        {"id": "i1|T1", "decision": "placed", "market_ticker": "T1", "outcome": None},
        {"id": "i1|T2", "decision": "placed", "market_ticker": "T2", "outcome": "win"},   # settled -> skip
        {"id": "i1|T3", "decision": "skipped", "market_ticker": "T3", "outcome": None},   # not placed -> skip
        {"id": "i1|T4", "decision": "placed", "market_ticker": "T4", "outcome": None},    # no data -> skip
    ]
    sharp_map = {"T1": 0.62}
    mid_map = {"T1": 58.0, "T2": 50.0}
    ups = close_ref_updates(rows, sharp_map, mid_map)
    assert ups == [{"id": "i1|T1", "sharp_close_prob": 0.62, "pre_settle_mid_cents": 58}]
```

- [ ] **Step 2: Run, verify fail** — `python3 -m pytest tests/test_kalshi_reconcile.py::test_close_ref_updates_open_placed_only -q` → FAIL (ImportError).

- [ ] **Step 3: Implement** in `reconcile.py`:

```python
def close_ref_updates(placed_rows, sharp_map, mid_map) -> list[dict]:
    """For each OPEN (outcome is None) placed decision, return the rolling closing
    reference to stamp: latest sharp prob (-> sharp_close_prob, the graded CLV
    reference) and latest Kalshi mid (-> pre_settle_mid_cents). At settlement the
    LAST value stamped is the close. Skips settled/non-placed rows and rows with no
    current data. Pure."""
    sharp_map = sharp_map or {}
    mid_map = mid_map or {}
    out = []
    for r in placed_rows or []:
        if r.get("decision") != "placed" or r.get("outcome") is not None:
            continue
        tk = r.get("market_ticker")
        upd = {}
        if tk in sharp_map and sharp_map[tk] is not None:
            upd["sharp_close_prob"] = float(sharp_map[tk])
        if tk in mid_map and mid_map[tk] is not None:
            upd["pre_settle_mid_cents"] = int(round(float(mid_map[tk])))
        if upd:
            out.append({"id": r.get("id"), **upd})
    return out
```

- [ ] **Step 4: Run** — `python3 -m pytest tests/test_kalshi_reconcile.py -q` → PASS.

- [ ] **Step 5: Add DB writer** in `db.py` (after `mark_paper_positions`):

```python
def update_close_refs(conn, instance_id, sharp_map, mid_map) -> int:
    """Stamp the rolling closing reference (sharp_close_prob + pre_settle_mid_cents)
    onto open placed decisions so reconcile can grade true CLV at settlement.
    Measurement only — never trades."""
    from kalshi.reconcile import close_ref_updates
    try:
        rows = list(_r.db(DB_NAME).table("kalshi_decisions")
                    .filter({"instance_id": str(instance_id), "decision": "placed"})
                    .pluck("id", "decision", "market_ticker", "outcome").run(conn))
    except Exception:
        return 0
    n = 0
    for u in close_ref_updates(rows, sharp_map, mid_map):
        try:
            _r.db(DB_NAME).table("kalshi_decisions").get(u["id"]).update(
                {k: v for k, v in u.items() if k != "id"}).run(conn)
            n += 1
        except Exception:
            pass
    return n
```

- [ ] **Step 6: Wire into `engine.run_instance`.** After the `mark_paper_positions` block (~engine.py:767), add:

```python
            # 7c-ii) Roll the closing reference onto open placed positions so CLV
            # grades against the LAST sharp prob + Kalshi mid before settlement.
            try:
                _sharp_map = {}
                for _mt in metas:
                    for _mk in _mt["mkts"]:
                        _sp = (_mt["sharp_probs"].get(_mk["market_type"], {}) or {}).get(_mk["side"])
                        if _sp is not None:
                            _sharp_map[_mk["market_ticker"]] = _sp
                kdb.update_close_refs(conn, config.instance_id, _sharp_map, _mark_map)
            except Exception as e:
                log(f"tick {tick}: update_close_refs failed: {type(e).__name__}: {e}", "yellow")
```

(`_mark_map` is the `{ticker: mid}` already built for `mark_paper_positions`.)

- [ ] **Step 7: Run + commit** — `python3 -m pytest tests/test_kalshi_reconcile.py tests/test_kalshi_db.py -q` → PASS. Commit: "feat(kalshi): roll sharp/mid closing reference onto open positions so CLV grades".

---

### Task 3 (P4): In-play take-profit fires on single-contract positions

**Files:**
- Modify: `backend/kalshi/live/live_decision.py` (`decide` take-profit block)
- Test: `backend/tests/test_kalshi_live_decision.py`

- [ ] **Step 1: Write failing test** in `test_kalshi_live_decision.py`:

```python
def test_take_profit_banks_single_contract():
    # held 1, entry 40c, live fair 0.50 (thesis intact: fair >= entry), mark 60c
    # overshoots fair by >= tp_overshoot (8c) -> should REDUCE (full exit of the 1).
    pos = Pos(contracts=1, avg_price_cents=40, current_price_cents=60)
    a = decide(position=pos, live_fair=0.50, yes_ask_cents=55, yes_bid_cents=60,
               caps=CAPS, phase=LIVE, elapsed_min=70, allow_open=True)
    assert a.kind == "reduce" and a.contracts == 1


def test_take_profit_half_when_multiple():
    pos = Pos(contracts=10, avg_price_cents=40, current_price_cents=60)
    a = decide(position=pos, live_fair=0.50, yes_ask_cents=55, yes_bid_cents=60,
               caps=CAPS, phase=LIVE, elapsed_min=70, allow_open=True)
    assert a.kind == "reduce" and a.contracts == 5   # unchanged: bank half
```

- [ ] **Step 2: Run, verify fail** — `python3 -m pytest tests/test_kalshi_live_decision.py::test_take_profit_banks_single_contract -q` → FAIL (returns hold).

- [ ] **Step 3: Implement.** In `live_decision.py` replace the take-profit guard (`if (held >= 2 and ...`):

```python
        # FAIR-GATED take-profit: thesis intact (fair >= entry) and the market OVERSHOT
        # fair by tp_overshoot_cents -> bank profit. Half when we hold >=2; full exit of
        # a single contract (can't sell half a contract, so don't let small wins ride).
        if (held >= 1 and entry > 0 and fair >= entry / 100.0
                and _implied(mark) >= fair + (caps.tp_overshoot_cents / 100.0)):
            sell = max(1, held // 2) if held >= 2 else 1
            return LiveAction("reduce", sell,
                              f"take-profit: mark {mark:.0f}c overshoots fair {fair:.2f} (thesis intact)")
```

- [ ] **Step 4: Run** — `python3 -m pytest tests/test_kalshi_live_decision.py tests/test_kalshi_monitor.py -q` → PASS.

- [ ] **Step 5: Commit** — "feat(kalshi): bank in-play take-profit on single-contract positions".

---

### Task 4: Full suite + change detection

- [ ] **Step 1:** `cd backend && python3 -m pytest tests/ -k kalshi -q` → all pass (baseline was 263).
- [ ] **Step 2:** `gitnexus_detect_changes()` — confirm only the expected symbols/flows changed; investigate anything unexpected.
- [ ] **Step 3:** Parallel adversarial bug sweep over the diff (correctness, regressions, off-by-one in gating/sizing, None-handling). Fix real findings.

---

### Task 5 (P2): Apply prod config so simulated trades are $3+ (ops, no code)

Live paper instance `032f0c62` ("Soccer Live"), RethinkDB `Instances.kalshi_config`.

- [ ] **Step 1:** Read current `kalshi_config`, change ONLY: `bankroll_cents` 5400→**50000** ($500), `kelly_fraction` 0.15→**0.25**, `per_bet_cap_frac` 0.07→**0.10**, add `no_sharp_edge_threshold`=**0.08**. Write back (merge, don't replace the blob).
- [ ] **Step 2:** Re-read and verify the four fields. Sizing math at $500: 5% edge @ 40c → ~$6 (≥$3 ✓); 3% edge → ~$3.7. Trades become $3–30, contract count ~10×.
- [ ] **Note:** takes effect on the engine's next tick / restart (operator-managed). All simulated — no exchange orders.

---

### Task 6: Push

- [ ] `git push -u origin feat/kalshi-nosharp-gate-clv-greenup` and open PR (or merge to main per standing autonomous-workflow preference).

## Self-review notes
- **Spec coverage:** P1=Task1, P2=Task5, P3=Task2, P4=Task3. ✓
- **No-default-break:** `no_sharp_edge_threshold` defaults 0.0 in `RiskCaps`/`risk_caps_from_config` → existing tests unaffected; only `normalize_config` writes 0.08 (new instances) and the P2 DB edit enables it live.
- **Type consistency:** `close_ref_updates(rows, sharp_map, mid_map) -> [{id, sharp_close_prob?, pre_settle_mid_cents?}]` used identically in `db.update_close_refs`. `decide` returns `LiveAction` unchanged.
- **Honesty:** P4 deliberately improves EXITS (banking gains), not opens — loosening in-play opens would be -EV churn per the research brief. "$3+ trades" come from P2 (bankroll), the real root cause.
