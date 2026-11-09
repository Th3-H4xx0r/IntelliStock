# Strategy HT paper forward test — TQQQ stepped 0.375 (pre-registered 2026-11-09)

## The rule under test (config of the `strategy_ht` lane; nothing else)
- Filter: QQQ close vs its 200-day SMA. Enter the market state above +1%, exit below −3%,
  keep the state in between (replayed from closes; no cache dependency for the state).
- In-state exposure: `vol_target` 0.375 / 21-day realised QQQ vol, on the 0.5 grid with 0.15
  hysteresis, `exp_min` 1.0, `exp_max` 2.0. With TQQQ as the fund the grid levels mean:
  2.0 → 100% TQQQ (3x), 1.5 → 50% TQQQ + 50% QQQ (2x), 1.0 → 100% QQQ (1x).
  In words: 3x while realised vol is below ~19%, 2x up to ~30%, 1x above.
- Out-state: 100% BIL.
- Bounce overlay off. Drift band 5%; idle cash above the band is redeployed.
- Decision from completed daily bars at each daily tick; orders next session. Same lag the
  engine backtests carried.

## Evidence (engine, real ETF prices, etf-liquid costs, $6k; .claude/handoffs/sweep-2026-11-08/engine/)
Nov 2021–Aug 2026: +125.3% vs SPY +66.8% price / +78.8% total return; maxDD −42% vs −25%;
2022 −25.7% (SPY −19.3%), 2023 +92.5%, 2024 +56.0%, 2025 +17.0%, 2026 YTD −30.5% (SPY +7.6%);
Feb–Apr 2025 −32.4% (SPY −11.6%). 121 trades. Beats SPY in 7 of 10 standard windows.
Offline real-ETF 2010–2026 for the family (TQQQ 30–35% vol target): ~31–33% CAGR, maxDD −28 to −32%.
Known: all excess return is Nasdaq beta with leverage in the 2010–2026 regime; the modelled
1986–2012 Nasdaq 3x trend rule lost 76% in 2000. The fast-crash tail (Apr 2025, Mar 2026) is
not fixable with daily price triggers (ht_crash_cap.txt: 24 variants, no change).

## Where it runs
- Instance `strategy-ht-paper`, document "Strategy HT paper (TQQQ stepped 0.375)", brokerage
  "Alpaca Paper" bf78ad0c-3073-4aac-97a5-a29c7b043404 (PA3IBY5S84PG), shared with lab
  instances, so the instance runs in clean-room mode with its own $10,000 baseline. Granularity
  86400 like alpaca-main. Universe QQQ / TQQQ / BIL. Never alpaca-main; never the F14 mirror's
  account (the swing account was the operator's first choice and was ruled out for that reason).
- Live envelope (declared by the lane): 100% order/symbol/leveraged fractions; drawdown rungs
  soft −35% (buys freeze), hard −50%, kill −60% (book flattened). These ARE the kill rule.

## Pass / kill (decided before the first tick)
- Score monthly against SPY total return and against the engine's replay of the same dates.
- Checkpoints at 12 and 24 months.
- PASS at 24 months: (a) tracking error vs the engine replay < 2%/yr (the implementation is
  faithful), and (b) cumulative return ≥ SPY total return, or the rule was in 1x/out during a
  ≥ 15% QQQ drawdown and lost less than 3x QQQ over that window.
- KILL: the −60% rung fires; or tracking error > 5%/yr without an explained cause; or a switch
  is missed or mis-sized twice; or the operator wants the account back.
- No parameter changes during the test. A changed config is a new test.

## What a pass would and would not mean
A pass says the implementation is faithful and the 2021–2026 regime continued. It does not
say the rule survives a 2000-style Nasdaq bear; nothing in the available data can.
