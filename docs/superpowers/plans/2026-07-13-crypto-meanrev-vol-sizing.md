# Crypto MeanRev Vol-Scaled Sizing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Size each MeanRev dip-buy by the coin's volatility (ATR%) instead of a flat `pv/top_k`, deploying more capital into higher-bounce coins — validated to win/tie equal-weight in every market regime.

**Architecture:** Extend the existing per-symbol indicator loop in `Meanrev.run_once` to compute ATR%, and generalize the shared `_apply_equal_weight_sizing` helper to a bounded volatility-weighted allocation gated behind a new `sizing` config (default `"vol"`), with equal-weight as the backward-compatible default path used by Connors.

**Tech Stack:** Python, numpy, talib, existing `strategies.crypto.core` helpers, `PortfolioEmulator`.

## Global Constraints

- `run_once` MUST return `{sym: 1|0|-1}` (never float sizes) plus `_nexus_position_sizes`; sizes are dicts with `buy_cash`/`sell_fraction` + `"asset_class":"crypto"`, never bare floats. (verbatim from project gotchas)
- `_apply_equal_weight_sizing` is imported by `connors.py` (4 positional args) — its default behavior MUST remain exact equal-weight.
- meanrev class is `Meanrev`; crypto strategy id is lower-case everywhere.
- Vol weight is bounded by `clamp(atr_pct/ref, 0.6, 1.6)`; missing/NaN ATR falls back to `pv/top_k`.
- Binance.US taker fee 0.0002 for backtest verification.

---

### Task 1: Volatility-aware sizing helper (backward-compatible)

**Files:**
- Modify: `backend/strategies/crypto/meanrev.py` (`_apply_equal_weight_sizing`, ~lines 176-202)
- Test: `backend/tests/test_crypto_meanrev_vol_sizing.py` (create)

**Interfaces:**
- Consumes: `result: dict` (sym→1|0|-1), `prices: dict`, `portfolio_emulator` (has `get_portfolio_value(prices)`), `top_k: int`.
- Produces: `_apply_equal_weight_sizing(result, prices, portfolio_emulator, top_k, atr_pct_by=None, sizing="equal")` — mutates `result["_nexus_position_sizes"]`. When `sizing="equal"` or `atr_pct_by is None`, emits `buy_cash = round(pv/top_k, 2)` per buy (current behavior). When `sizing="vol"`, emits `buy_cash = round(pv/top_k * clamp(atr_pct_by[s]/ref, 0.6, 1.6), 2)` where `ref = median` of valid `atr_pct_by` values over the buy set; missing-ATR buys use `pv/top_k`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_crypto_meanrev_vol_sizing.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from strategies.crypto.meanrev import _apply_equal_weight_sizing


class _PE:
    def __init__(self, pv): self._pv = pv
    def get_portfolio_value(self, prices): return self._pv


def _sizes(result):
    return result.get("_nexus_position_sizes", {})


def test_equal_default_matches_pv_over_topk():
    # No atr_pct_by / sizing='equal' -> each buy gets pv/top_k (Connors path).
    result = {"BTC/USD": 1, "ETH/USD": 1}
    _apply_equal_weight_sizing(result, {"BTC/USD": 100, "ETH/USD": 50}, _PE(10000.0), 2)
    s = _sizes(result)
    assert s["BTC/USD"]["buy_cash"] == 5000.0
    assert s["ETH/USD"]["buy_cash"] == 5000.0
    assert s["BTC/USD"]["asset_class"] == "crypto"
    assert s["_cash_reserve_floor_pct"] == 0.0 and s["_buy_price_floor"] == 0.0


def test_vol_overweights_higher_atr_coin():
    # ETH has 2x BTC's ATR% -> ETH gets more buy_cash than BTC.
    result = {"BTC/USD": 1, "ETH/USD": 1}
    atr = {"BTC/USD": 0.01, "ETH/USD": 0.02}
    _apply_equal_weight_sizing(result, {"BTC/USD": 100, "ETH/USD": 50}, _PE(10000.0), 2,
                               atr_pct_by=atr, sizing="vol")
    s = _sizes(result)
    assert s["ETH/USD"]["buy_cash"] > s["BTC/USD"]["buy_cash"]


def test_vol_weight_is_clamped():
    # Extreme ATR ratios are bounded to [0.6, 1.6] * pv/top_k.
    result = {"BTC/USD": 1, "ETH/USD": 1}
    atr = {"BTC/USD": 0.001, "ETH/USD": 0.5}  # 500x ratio
    _apply_equal_weight_sizing(result, {"BTC/USD": 100, "ETH/USD": 50}, _PE(10000.0), 2,
                               atr_pct_by=atr, sizing="vol")
    s = _sizes(result)
    per = 10000.0 / 2
    assert 0.6 * per - 0.01 <= s["BTC/USD"]["buy_cash"] <= 1.6 * per + 0.01
    assert 0.6 * per - 0.01 <= s["ETH/USD"]["buy_cash"] <= 1.6 * per + 0.01


def test_vol_missing_atr_falls_back_to_equal():
    result = {"BTC/USD": 1, "ETH/USD": 1}
    atr = {"BTC/USD": 0.01}  # ETH missing
    _apply_equal_weight_sizing(result, {"BTC/USD": 100, "ETH/USD": 50}, _PE(10000.0), 2,
                               atr_pct_by=atr, sizing="vol")
    s = _sizes(result)
    assert s["ETH/USD"]["buy_cash"] == 5000.0  # fallback pv/top_k


def test_sells_still_emit_full_fraction():
    result = {"BTC/USD": -1, "ETH/USD": 1}
    atr = {"BTC/USD": 0.01, "ETH/USD": 0.02}
    _apply_equal_weight_sizing(result, {"BTC/USD": 100, "ETH/USD": 50}, _PE(10000.0), 2,
                               atr_pct_by=atr, sizing="vol")
    s = _sizes(result)
    assert s["BTC/USD"]["sell_fraction"] == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python3 -m pytest tests/test_crypto_meanrev_vol_sizing.py -v`
Expected: FAIL — `test_vol_overweights_higher_atr_coin` etc. fail (helper ignores new args / TypeError on unexpected kwarg).

- [ ] **Step 3: Implement the vol-aware helper**

Replace `_apply_equal_weight_sizing` in `backend/strategies/crypto/meanrev.py` with:

```python
def _apply_equal_weight_sizing(result, prices, portfolio_emulator, top_k,
                               atr_pct_by=None, sizing="equal"):
    """Emit per-buy ``buy_cash`` and full ``sell_fraction`` sizes into
    ``result["_nexus_position_sizes"]``.

    Default (``sizing="equal"`` or no ``atr_pct_by``): each buy targets an equal
    ``pv/top_k`` slot (unchanged; this is the path connors.py uses).
    ``sizing="vol"`` with ``atr_pct_by``: each slot is scaled by the coin's
    volatility — ``buy_cash = pv/top_k * clamp(atr_pct/ref, 0.6, 1.6)`` where
    ``ref`` is the median ATR% over the coins being bought — so more capital flows
    to higher-bounce names. Buys with missing/invalid ATR fall back to ``pv/top_k``.
    Never emits bare floats. No-op if there are no buys."""
    buys = [s for s, v in result.items()
            if isinstance(s, str) and not s.startswith("_") and v == 1]
    if not buys:
        return
    pv = 0.0
    if portfolio_emulator is not None:
        try:
            pv = float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
        except Exception:
            pv = 0.0
    sizes = result.get("_nexus_position_sizes")
    if not isinstance(sizes, dict):
        sizes = {}
    per = round(pv / top_k, 2) if pv > 0 else 0.0

    # volatility reference = median ATR% over the buys that have a valid value
    ref = None
    if sizing == "vol" and isinstance(atr_pct_by, dict):
        vals = sorted(v for s in buys
                      for v in [atr_pct_by.get(s)]
                      if isinstance(v, (int, float)) and v is not None and v > 0)
        if vals:
            n = len(vals)
            ref = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0

    for s in buys:
        if per <= 0:
            continue
        cash = per
        if ref and ref > 0:
            av = atr_pct_by.get(s) if isinstance(atr_pct_by, dict) else None
            if isinstance(av, (int, float)) and av is not None and av > 0:
                w = av / ref
                w = 0.6 if w < 0.6 else (1.6 if w > 1.6 else w)
                cash = round(per * w, 2)
        sizes[s] = {"buy_cash": cash, "asset_class": "crypto"}

    for s, v in result.items():
        if isinstance(s, str) and not s.startswith("_") and v == -1:
            sizes[s] = {"sell_fraction": 1.0, "asset_class": "crypto"}
    sizes["_cash_reserve_floor_pct"] = 0.0
    sizes["_buy_price_floor"] = 0.0
    result["_nexus_position_sizes"] = sizes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python3 -m pytest tests/test_crypto_meanrev_vol_sizing.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/strategies/crypto/meanrev.py backend/tests/test_crypto_meanrev_vol_sizing.py
git commit -m "feat(crypto): vol-aware MeanRev sizing helper (equal-weight default preserved)"
```

---

### Task 2: Compute ATR% in run_once + wire sizing config

**Files:**
- Modify: `backend/strategies/crypto/meanrev.py` (`Meanrev.__init__`, `run_once` indicator loop ~124-140, sizing call ~172, SCHEMA/DESCRIPTION comment ~line 1-2)
- Test: `backend/tests/test_crypto_meanrev_vol_sizing.py` (extend — run_once level)

**Interfaces:**
- Consumes: helper from Task 1 (`_apply_equal_weight_sizing(..., atr_pct_by=, sizing=)`), `core.series(bars, "h"|"l"|"c")`, `talib.ATR`.
- Produces: `run_once` reads `sizing` (default `"vol"`) and `atr_period` (default 14) from merged config; builds `atr_pct_by: dict[sym→float|None]`; passes both to the sizing helper. Return contract unchanged.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_crypto_meanrev_vol_sizing.py
import numpy as np
from strategies.crypto.meanrev import Meanrev


class _PE2:
    def __init__(self, cash=10000.0):
        self._cash = cash; self.positions = {}
    def get_portfolio_value(self, prices): return self._cash
    def get_positions(self): return {}


def _synth_bars(n, base, vol, dip_last=True):
    # deterministic OHLC series above SMA200, RSI oversold at the last bar.
    bars = []
    px = base
    for i in range(n):
        px = px * (1.0 + 0.0008)  # gentle uptrend => close > SMA200 (regime ok)
        o = px
        c = px
        if dip_last and i >= n - 6:
            c = px * (1 - 0.04)   # last few bars drop => RSI < 35
        hi = max(o, c) * (1 + vol)
        lo = min(o, c) * (1 - vol)
        bars.append({"t": f"2026-01-01T{i:02d}:00:00Z", "o": o, "h": hi, "l": lo, "c": c, "v": 1.0})
    return bars


def test_run_once_vol_sizing_prefers_higher_vol_coin():
    # Two coins both oversold-in-uptrend; HIGHVOL has wider ATR -> bigger buy_cash.
    n = 260
    data = {"BTC/USD": _synth_bars(n, 100.0, 0.005),   # low vol
            "ETH/USD": _synth_bars(n, 100.0, 0.03)}    # high vol
    prices = {s: data[s][-1]["c"] for s in data}
    strat = Meanrev()
    res = strat.run_once(symbols=["BTC/USD", "ETH/USD"], prices=prices,
                         current_time=None,
                         config={"band": "low", "top_k": 2, "regime_ma": 200,
                                 "rsi_buy": 35, "rsi_exit": 55, "sizing": "vol"},
                         conditions={}, data=data, portfolio_emulator=_PE2(), mode="LIVE")
    sizes = res.get("_nexus_position_sizes", {})
    buys = {s: v for s, v in res.items() if isinstance(s, str) and not s.startswith("_") and v == 1}
    assert set(buys) == {"BTC/USD", "ETH/USD"}, res
    assert sizes["ETH/USD"]["buy_cash"] > sizes["BTC/USD"]["buy_cash"]


def test_run_once_equal_sizing_is_flat():
    n = 260
    data = {"BTC/USD": _synth_bars(n, 100.0, 0.005),
            "ETH/USD": _synth_bars(n, 100.0, 0.03)}
    prices = {s: data[s][-1]["c"] for s in data}
    strat = Meanrev()
    res = strat.run_once(symbols=["BTC/USD", "ETH/USD"], prices=prices, current_time=None,
                         config={"band": "low", "top_k": 2, "regime_ma": 200,
                                 "rsi_buy": 35, "rsi_exit": 55, "sizing": "equal"},
                         conditions={}, data=data, portfolio_emulator=_PE2(), mode="LIVE")
    sizes = res.get("_nexus_position_sizes", {})
    assert sizes["BTC/USD"]["buy_cash"] == sizes["ETH/USD"]["buy_cash"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_crypto_meanrev_vol_sizing.py::test_run_once_vol_sizing_prefers_higher_vol_coin -v`
Expected: FAIL — buy_cash equal (run_once still equal-weight; ignores `sizing`).

- [ ] **Step 3: Implement ATR% computation + config wiring**

In `Meanrev.__init__`, add defaults:
```python
        self.atr_period = 14
        self.sizing = "vol"
```

In `run_once`, after reading the other params (after `top_k = ...`), add:
```python
        atr_p = max(2, int(settings.get("atr_period", self.atr_period)))
        sizing = str(settings.get("sizing", self.sizing)).lower()
```

In the per-symbol indicator loop, alongside `closes`, compute ATR% (reuse the same
capped slice). Initialize `atr_pct_by = {}` before the loop, and inside — after the
`closes = _series(...)` and the `if len(closes) > _cap: closes = closes[-_cap:]` slice —
add:
```python
            highs = _series(data.get(sym) or [], "h")
            lows = _series(data.get(sym) or [], "l")
            if len(highs) > _cap:
                highs = highs[-_cap:]
            if len(lows) > _cap:
                lows = lows[-_cap:]
            atr_pct_by[sym] = None
            try:
                if len(highs) == len(closes) and len(lows) == len(closes):
                    _atr = talib.ATR(highs, lows, closes, timeperiod=atr_p)
                    if not np.isnan(_atr[-1]) and closes[-1] > 0:
                        atr_pct_by[sym] = float(_atr[-1]) / float(closes[-1])
            except Exception:
                atr_pct_by[sym] = None
```
(Ensure `atr_pct_by = {}` is initialized next to `rsi_by = {}` / `regime_ok = {}`.)

Change the sizing call (was `_apply_equal_weight_sizing(result, prices, portfolio_emulator, top_k)`):
```python
        _apply_equal_weight_sizing(result, prices, portfolio_emulator, top_k,
                                   atr_pct_by=atr_pct_by, sizing=sizing)
```

Update the top-of-file comment config + description:
```python
# INTELLISTOCK_SCHEMA: {"strategy": "MeanRev", "weight": 1.0, "execution_position": 0, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"band": "medium", "rsi_period": 14, "rsi_buy": 35, "rsi_exit": 55, "regime_ma": 200, "top_k": 2, "sizing": "vol", "atr_period": 14}}
```
Append to the DESCRIPTION: `Sizes each dip by volatility (ATR%) so higher-bounce coins get more capital ("sizing":"vol", bounded); "equal" restores flat 1/top_k slots.`

- [ ] **Step 4: Run to verify pass (both run_once tests + full file)**

Run: `cd backend && python3 -m pytest tests/test_crypto_meanrev_vol_sizing.py -v`
Expected: PASS (all tests, equal + vol).

- [ ] **Step 5: Commit**

```bash
git add backend/strategies/crypto/meanrev.py backend/tests/test_crypto_meanrev_vol_sizing.py
git commit -m "feat(crypto): MeanRev computes ATR% and vol-scales dip-buys (config sizing=vol default)"
```

---

### Task 3: Faithful backtest verification (vol >= equal)

**Files:**
- Create: `scripts/verify_meanrev_vol_sizing.py` (faithful `PortfolioEmulator` run over cached bars, equal vs vol on target + OOS windows)

**Interfaces:**
- Consumes: `PortfolioEmulator`, `strategies.crypto.meanrev.Meanrev`, a cached bars JSON (path via `--cache`, default the repo's crypto bars fixture or the tmp cache).
- Produces: prints `equal` vs `vol` P&L% per window; exits non-zero if `vol < equal - 0.5` on any window (regression guard).

- [ ] **Step 1: Write the verification script**

```python
#!/usr/bin/env python3
"""Faithful vol-vs-equal check for MeanRev through the real PortfolioEmulator
(Binance.US 0.02% fee). Asserts vol >= equal - tolerance on each window."""
import argparse, json, sys, os, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from portfolio_emulator import PortfolioEmulator
from strategies.crypto.meanrev import Meanrev

FEE = 0.0002; CASH = 10_000.0; WARMUP = 220; WINDOW = 260


def run(bars, times, uni, sizing, lo, hi):
    pe = PortfolioEmulator(initial_cash=CASH, taker_fee=FEE)
    strat = Meanrev()
    cfg = {"band": "low", "top_k": 2, "regime_ma": 200, "rsi_buy": 35,
           "rsi_exit": 55, "sizing": sizing, "allocations": []}
    for i in range(lo, hi):
        w0 = max(0, i - WINDOW + 1)
        data = {s: bars[s][w0:i + 1] for s in uni}
        prices = {s: bars[s][i]["c"] for s in uni}
        ct = dt.datetime.strptime(times[i][:19], "%Y-%m-%dT%H:%M:%S")
        res = strat.run_once(symbols=list(uni), prices=prices, current_time=ct,
                             config=cfg, conditions={}, data=data,
                             portfolio_emulator=pe, mode="LIVE")
        if not isinstance(res, dict):
            continue
        sizes = res.get("_nexus_position_sizes") if isinstance(res.get("_nexus_position_sizes"), dict) else {}
        sig = {k: v for k, v in res.items() if isinstance(k, str) and not k.startswith("_")}
        for s, v in sig.items():
            if v == -1:
                pe.execute_signal(s, -1, prices[s], timestamp=ct, sell_fraction=1.0)
        for s, v in sig.items():
            if v == 1:
                sz = sizes.get(s)
                cash = float(sz["buy_cash"]) if isinstance(sz, dict) and "buy_cash" in sz else pe.get_portfolio_value(prices) / 2.0
                pe.execute_signal(s, 1, prices[s], timestamp=ct, cash_per_trade=cash)
        pe.save_portfolio_snapshot(prices, timestamp=ct)
    final = pe.get_portfolio_value({s: bars[s][hi - 1]["c"] for s in uni})
    return (final / CASH - 1) * 100


def idx(times, iso):
    for i, t in enumerate(times):
        if t >= iso:
            return i
    return len(times)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    args = ap.parse_args()
    d = json.load(open(args.cache))
    uni, times = d["universe"], d["times"]
    bars = {s: [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                for r in d["bars"][s]] for s in uni}
    windows = {"tgt 26-04..07": ("2026-04-13", "2026-07-13"),
               "oos 25-06..26-04": ("2025-06-08", "2026-04-13")}
    ok = True
    print(f"{'window':20s} {'equal':>8s} {'vol':>8s} {'delta':>7s}")
    for name, (s0, e0) in windows.items():
        lo, hi = max(idx(times, s0 + 'T00:00:00Z'), WARMUP), idx(times, e0 + 'T00:00:00Z')
        eq = run(bars, times, uni, "equal", lo, hi)
        vo = run(bars, times, uni, "vol", lo, hi)
        print(f"{name:20s} {eq:+8.2f} {vo:+8.2f} {vo-eq:+7.2f}")
        if vo < eq - 0.5:
            ok = False
    print("PASS" if ok else "FAIL: vol regressed below equal")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the verification against the cached bars**

Run: `python3 scripts/verify_meanrev_vol_sizing.py --cache "$CLAUDE_JOB_DIR/tmp/bars_cache.json"`
Expected: prints equal vs vol per window; `vol >= equal` on both; final line `PASS`, exit 0.

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_meanrev_vol_sizing.py
git commit -m "test(crypto): faithful PortfolioEmulator check — MeanRev vol >= equal"
```

---

### Task 4: Full crypto suite regression + bug sweep

**Files:** none (validation only)

- [ ] **Step 1: Run the full crypto test suite**

Run: `cd backend && python3 -m pytest tests/ -k crypto -q`
Expected: all pass (including Connors + discovery-cache tests — Connors sizing unchanged).

- [ ] **Step 2: Sanity-check Connors still equal-weights**

Run: `cd backend && python3 -c "from strategies.crypto.connors import Connors; print('import ok')"`
Expected: `import ok` (no signature break on the shared helper).

- [ ] **Step 3: (handled by workflow) requesting-code-review + gitnexus detect_changes before final push.**

---

## Self-Review

**Spec coverage:**
- ATR% in indicator loop → Task 2 ✓
- vol-aware helper w/ backward-compat + clamp + fallback → Task 1 ✓
- `sizing`/`atr_period` config + SCHEMA update → Task 2 ✓
- Connors preserved → Task 1 default path + Task 4 Step 2 ✓
- Unit tests (overweight, equal-regression, clamp, fallback, sells) → Task 1/2 ✓
- Faithful backtest vol>=equal on tgt+oos → Task 3 ✓
- run_once contract unchanged (1|0|-1, no bare floats) → Task 1 emits dicts; Task 2 keeps signals ✓

**Placeholder scan:** none — all steps carry real code/commands.

**Type consistency:** `_apply_equal_weight_sizing(result, prices, portfolio_emulator, top_k, atr_pct_by=None, sizing="equal")` used identically in Task 1 (def) and Task 2 (call). `atr_pct_by: dict[str→float|None]`, `sizing: str`. `buy_cash`/`sell_fraction` dict shapes consistent across tasks.
