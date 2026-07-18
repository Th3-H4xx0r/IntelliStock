# Adaptive regime switcher (`adaptive`) — 2026-07-14

## What it is

User ask: "a strategy that detects bull vs bear and uses B&H when required."
New selectable crypto strategy `adaptive` (class `Adaptive`,
`backend/strategies/crypto/adaptive.py`):

- **BULL** — universe EW basket ≥ ramped SMA(`switch_ma`=4800h≈200d) AND
  ≥ ramped SMA(`confirm_ma`=1200h≈50d): buy every non-held coin at pv/N and
  hold ("be the market"). Fail-open to bull below max(600, confirm_ma//2)
  shared bars — holding the market is the benchmark behavior.
- **BEAR** — otherwise: liquidate the basket on the flip tick (mode tracked in
  `strategy_cache`; stateless fallback: held > top_k in bear ⇒ remnants ⇒
  liquidate), then delegate to the real `Meanrev` with `bear_gate_ma`
  defaulted to `confirm_ma` (dip-buys blocked while basket < 50d MA).

Registered in `_CRYPTO_STRATEGY_NAMES` + tunables (`switch_ma`, `confirm_ma`);
web + mobile pickers (recommended band: low).

## Why these parameters

Wave-4 sweeps (job a2a5a542 tmp, eval9_wave4*.py, honest v2 harness):
- Plain switchers (any single MA) fail: fast MAs die in 2022 bear rallies
  (−49..−54) and chop; slow MAs miss bulls. Max 3/9.
- Composite (slow switch + gated-MR bear) recovers 2022 (+11.5) but chop still
  negative; dual-confirm (also ≥ 50d MA) is the best variant. Plateau across
  sw 3600–4800 (means +24.9/+27.0). Margin/slope chop-rescues destroy 2021 or
  do nothing — chop is structurally unwinnable for a switcher; rejected.

## Faithful validation (real Meanrev delegation + real PortfolioEmulator, 0.02%)

| window | B&H | adaptive | MeanRev-only |
|---|---|---|---|
| 2021bull | +190.0 | **+111.2** | +64.8 |
| 2022bear | −67.1 | **+8.6** | −19.3 |
| 2023recov | +57.8 | +16.3 | +17.8 |
| 2324bull | +119.1 | **+74.1** | +30.6 |
| 2024chop | −35.2 | −21.3 | +1.7 |
| late24 | +74.3 | +8.1 | −13.3 |
| OOS | −36.4 | **+13.1** | +8.8 |
| tgt | −20.8 | −3.6 | +13.4 |
| fullrec | −50.0 | **+14.4** | +25.8 |
| **mean** | | **+24.5** | +14.5 |

`scripts/verify_adaptive_switcher.py` reproduces this table.

## Honest positioning (not a strict upgrade)

- Strict regime wins: 3/9 (< gated-MR's 5/9). Mean: +24.5 vs +14.5.
- Adaptive trades chop (−21) and the mildest bears (tgt −3.6) for ~2-4× bull
  capture with the 2022 crash still positive.
- **MeanRev (+bear gate) = max bear safety; Adaptive = bull participation.**
  Per-instance choice, both selectable in the UI.

## Gotchas

- 200d switch MA needs 4800 bars; prod backtest warmup is only 90d (~2160
  bars) — the ramped (expanding) MA handles the shortfall deterministically,
  matching the verified form.
- `confirm_ma` < 600 never gates (bear-gate 600-bar live-safety floor).
- Same live-mode `data=None` gap as all crypto strategies (see
  2026-07-14-crypto-meanrev-bear-gate.md).

## ADDENDUM — prod A/B results (same day, instance `test`, hourly, Binance.US-emulated)

| window | universe | EW B&H | adaptive | meanrev | meanrev+gate |
|---|---|---|---|---|---|
| 2023-10-01..2024-03-14 (bull) | 7 real-data coins | +181.0% | **+132.1%** (bt 792625) | +55.7% (bt 593893) | — |
| 2022-01-01..2022-12-31 (crash) | 7 coins incl. SOL/AVAX | **−74.5%** | −19.1% (bt 535396) | — | −9.9% (bt 644298) |

**Universe-dependence correction:** the faithful "2022 positive" results (adaptive
+8.6, gated-MR +11.2) were computed on the 5 old majors — the only coins with
local 2022 caches. Adding SOL (−94% in 2022) and AVAX (−90%) turns both negative
(−19.1 / −9.9): their violent bear rallies whipsaw false bull flips and open the
gate at rally tops. Both still protect 55–65 pts vs holding. Docstrings and UI
blurbs updated accordingly.

Also found during the A/B: prod serves a FROZEN SOL price ($20.1715) for
2023-24 (no real feed data there; un-purged AlpacaBarsCache) — the first
adaptive bull run (bt 316587, +114.4%) carried SOL as dead 1/8 weight; the
792625 rerun pins `stocks` to real-data coins. And `POST /backtests` granularity
is SECONDS (default "60" = 1-minute stepping) — always pass "3600" for band=low.

## ADDENDUM 2026-07-18 — P&L boost evaluation (faithful, PortfolioEmulator, 0.02%)

**SHIPPED — bull-mode drift rebalancing** (`rebalance_drift`, default 0.5 =
act when a coin's weight leaves [0.5x, 1.5x] of equal-weight target; 0
disables): trim overweight via fractional `sell_fraction`, top up underweight
via `buy_cash` (overlay_allocations semantics; broker honors fractional sells
at ~line 9431). Faithful sweep: mean +25.57 vs +24.56 buy-and-drift
(+1.0 pt/interval), improves 7 of 9 windows (late24 +4.0, chop +2.3, tgt +1.4,
OOS +1.2, fullrec +1.2); bands 0.25/0.10 also positive — robust sign, 0.5 best.

**REJECTED — bear-mode majors-only universe**: restricting gated-MR dip-buys
to the validated majors LOSES on every recent bear (OOS −4.9, tgt −1.0,
fullrec −6.1). The 2022 lesson does not generalize: alt dips hurt in
crash-bears but are PROFITABLE in mild bears. Universe filtering by "safety"
is a net cost — do not re-attempt without new evidence.
