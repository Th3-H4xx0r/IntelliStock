const ACRONYMS = new Set([
  'ai',
  'alpaca',
  'api',
  'atr',
  'bea',
  'cik',
  'etf',
  'finbert',
  'gleif',
  'io',
  'lei',
  'llm',
  'macd',
  'neo4j',
  'openai',
  'pdt',
  'rsi',
  'sec',
  'sic',
  'toon',
  'usd',
  'vwap',
])

const STRATEGY_FIELD_META = {
  graph_nexus_analysis: {
    min_articles: {
      label: 'Minimum Alpaca Articles Before Signal',
      description: 'Nexus waits for at least this many Alpaca articles before trusting the daily article pass.',
    },
    max_daily_alpaca_articles: {
      label: 'Max Daily Alpaca Articles',
      description: 'Upper cap on Alpaca articles fetched per day for live runs and historical learning.',
    },
    max_daily_google_news_articles: {
      label: 'Max Daily Google News Articles',
      description: 'Upper cap on Google News articles fetched per day for macro and geopolitical coverage.',
    },
    num_articles: {
      label: 'Legacy Alpaca Article Cap',
      description: 'Legacy fallback only. Prefer Max Daily Alpaca Articles instead.',
    },
    google_news_max_articles: {
      label: 'Legacy Google News Article Cap',
      description: 'Legacy fallback only. Prefer Max Daily Google News Articles instead.',
    },
    num_articles_for_llm: {
      label: 'Alpaca Articles Sent To Daily LLM Pass',
      description: 'How many Alpaca articles are forwarded into the main daily LLM sentiment/classification pass.',
    },
    trend_max_age_days: {
      label: 'Trend Auto-Expire Age (Days)',
      description: 'Stored trends are ended automatically if they go this many days without confirmation.',
    },
    learning_stage_days: {
      label: 'Legacy Learning Summary Window (Days)',
      description: 'Only used for the older cached text-summary learning stage, not the newer ML retraining window.',
    },
    learning_refresh_hours: {
      label: 'Legacy Learning Summary Refresh Hours',
      description: 'How often the older cached learning summary may be regenerated.',
    },
    lookback_learning_days: {
      label: 'ML Lookback Window (Days)',
      description: 'Historical days loaded before a run or backtest start to retrain Nexus ML and build context.',
    },
    buy_threshold: {
      label: 'Buy Score Threshold',
      description: 'Minimum final Nexus score needed before emitting a buy-oriented signal.',
    },
    sell_threshold: {
      label: 'Sell Score Threshold',
      description: 'Maximum final Nexus score before emitting a sell override signal.',
    },
    consecutive_sell_days_to_prune: {
      label: 'Consecutive Sell Days To Prune',
      description: 'Number of consecutive sell signal days before a discovered stock is pruned. Higher values allow stocks more time to recover from temporary dips.',
    },
    llm_overlay_max_stock_candidates: {
      label: 'Max Stock Overlay Candidates',
      description: 'Maximum number of stock candidates sent to the LLM trade overlay per day. Higher values review more stocks but cost more LLM tokens.',
    },
    llm_overlay_max_etf_candidates: {
      label: 'Max ETF Overlay Candidates',
      description: 'Maximum number of ETF candidates sent to the trend-aware LLM overlay per day. ETFs use a separate prompt focused on trend strength.',
    },
    max_trend_etfs: {
      label: 'Max Trend ETFs',
      description: 'Maximum total trend ETFs that can be actively tracked. Higher values allow broader diversified ETF exposure across gold, energy, defense, etc.',
    },
    max_etf_buys_per_day: {
      label: 'Max ETF Buys Per Day',
      description: 'Cap on how many ETFs can be bought in a single day. Prevents low-conviction ETF buys from diluting capital away from higher-conviction stock positions.',
    },
    max_discovered_stocks: {
      label: 'Max Discovered Stocks',
      description: 'Maximum number of stocks in the active discovered list. When full, new high-signal stocks can evict the weakest existing stock.',
    },
    nexus_portfolio_pct: {
      label: 'Stock Portfolio Allocation',
      description: 'Maximum fraction of portfolio value allocated to Nexus stock buys (0-1). Top-tier stocks get 2x allocation, mid-tier 1x, low-tier 0.5x.',
    },
    etf_portfolio_pct: {
      label: 'ETF Portfolio Allocation',
      description: 'Maximum fraction of portfolio value allocated to Nexus ETF buys (0-1). Split equally across active ETF buy signals.',
    },
    momentum_discovery_enabled: {
      label: 'Momentum Discovery',
      description: 'Discover stocks from pure price momentum (e.g. 20-day return >= 20%). Catches sustained runners that may not appear in news.',
    },
    momentum_discovery_min_20d_return: {
      label: 'Momentum Min 20-Day Return (%)',
      description: 'Minimum 20-day price return to trigger momentum discovery. Lower values discover more stocks but may include noise.',
    },
    momentum_discovery_min_60d_return: {
      label: 'Momentum Min 60-Day Return (%)',
      description: 'Minimum 60-day price return to trigger momentum discovery. Catches longer-term sustained runners.',
    },
    momentum_discovery_max_per_day: {
      label: 'Max Momentum Discoveries Per Day',
      description: 'Maximum number of stocks discovered via momentum per day. Prevents flooding the portfolio with momentum plays.',
    },
    sector_price_context_enabled: {
      label: 'Sector Price Context for LLM',
      description: 'Feed sector/commodity ETF price moves (gold, oil, semis, etc.) to the LLM so it can detect trends from price action, not just headlines.',
    },
    price_trend_detection_enabled: {
      label: 'Price-Based Trend Detection',
      description: 'Detect trends from benchmark ETF price action independently of LLM sentiment. Ensures trend→ETF pipeline works even when sentiment is cached.',
    },
    price_trend_bull_20d: {
      label: 'Price Trend 20-Day Threshold (%)',
      description: 'Minimum 20-day return for a benchmark ETF to trigger a bullish price trend. Lower values detect more trends.',
    },
    price_trend_bull_60d: {
      label: 'Price Trend 60-Day Threshold (%)',
      description: 'Minimum 60-day return for a benchmark ETF to trigger a bullish price trend. Catches longer-term commodity/sector moves.',
    },
    ml_signal_weight: {
      label: 'ML Signal Weight',
      description: 'Weight of ML model predictions in final scoring (0-1). Lower values let news/trend signals override ML downside predictions.',
    },
    analyst_panel_enabled: {
      label: 'Analyst Panel Enabled',
      description: 'Enable the 10-agent adversarial debate framework. Agents independently analyze news, debate each other\'s views, and synthesize a consensus signal each bar.',
    },
    analyst_panel_skip_lookback: {
      label: 'Skip Analyst Panel During Lookback',
      description: 'Skip the analyst panel during historical lookback bars to avoid hundreds of unnecessary LLM calls before the backtest window starts. Recommended: on.',
    },
    analyst_panel_rounds: {
      label: 'Debate Rounds',
      description: '1 = independent analysis only. 2 = + adversarial debate round (agents see each other\'s views). 3 = + moderator synthesis (recommended).',
    },
    analyst_panel_debate_style: {
      label: 'Debate Style',
      description: 'adversarial = agents actively challenge each other. collaborative = agents build on each other. structured = formal point/counterpoint.',
    },
    analyst_panel_moderator_llm_model: {
      label: 'Analyst Panel Moderator Model (R3)',
      description: 'Model for the Round 3 moderator synthesis — can be a stronger/more expensive model since it\'s only one call per bar. Uses the same provider/API key as the Analyst Panel LLM.',
    },
    analyst_panel_max_workers: {
      label: 'Analyst Panel Max Parallel Workers',
      description: 'Max threads for parallel agent LLM calls. Higher values reduce latency but increase concurrent API load.',
    },
    analyst_panel_timeout_sec: {
      label: 'Analyst Panel Agent Timeout (Seconds)',
      description: 'Per-agent LLM call timeout. Agents that exceed this are skipped; the panel proceeds with remaining results.',
    },
    analyst_panel_score_weight: {
      label: 'Analyst Panel Score Weight',
      description: 'Multiplier applied to panel consensus score adjustments before adding to stock scores. 0.15 = panel can shift a score by ±0.15.',
    },
    analyst_panel_memory_days: {
      label: 'Analyst Panel Memory Lookback (Days)',
      description: 'Days of per-agent prediction history loaded for accuracy feedback. Agents see their own past calls and accuracy to calibrate confidence.',
    },
    analyst_panel_max_stocks: {
      label: 'Analyst Panel Max Stocks Per Agent',
      description: 'Maximum number of stock candidates each agent rates per round. Higher values give more complete coverage but increase prompt size.',
    },
    analyst_panel_max_llm_calls: {
      label: 'Analyst Panel Max LLM Calls Per Bar',
      description: 'Hard cap on total LLM calls per bar across all rounds (cost control). 10 agents × 3 rounds = 21 calls; set to 25 for a small buffer.',
    },
    analyst_panel_cooldown_seconds: {
      label: 'Analyst Panel Agent Submission Stagger (Seconds)',
      description: 'Delay between submitting agents to the thread pool. Use to avoid rate-limit bursts. 0 = submit all at once.',
    },
    analyst_panel_sector_specialist_sector: {
      label: 'Analyst Panel Sector Specialist Focus',
      description: 'The industry sector the Sector Specialist agent focuses on. Default: Technology.',
    },
    analyst_panel_cache_enabled: {
      label: 'Analyst Panel LLM Prompt Cache',
      description: 'Cache analyst panel LLM responses (all 3 rounds) in RethinkDB. Same prompt + model + effort returns the cached response instead of calling the LLM — can cut panel latency dramatically on re-runs or within repetitive lookback windows. Works independently of nexus_fast_mode. Default: off.',
    },
    nexus_fast_mode: {
      label: 'Nexus Fast Mode (Global LLM Cache)',
      description: 'Enable LLM prompt-hash caching for ALL Nexus LLM calls (lookback + live). Caches responses by (prompt + model + effort) in RethinkDB. WARNING: can contaminate live-bar decisions with stale responses if LLM context changes. Use for fast iteration, not production runs. Default: off.',
    },
    nexus_lookback_cache_enabled: {
      label: 'Lookback LLM Cache',
      description: 'Enable LLM prompt caching ONLY during the 85-bar historic lookback warmup. Live bars still call the LLM fresh. Much safer than nexus_fast_mode because stale cache hits cannot affect live trading decisions. Default: on.',
    },
    nexus_high_conviction_threshold: {
      label: 'High-Conviction Score Threshold',
      description: 'Raw score threshold above which a blocked-buy candidate is treated as high-conviction. High-conviction items get the top_momentum_break_glass rotation bypass (delta>=0.75 instead of 1.50), shorter min-hold thresholds, and a larger backfill reserve. Default: 1.5.',
    },
    peak_protection_enabled: {
      label: 'Peak Protection Enabled',
      description: 'When ON, positions that previously hit a high P&L peak are protected from fast-loser-cut on first pullback, deferring to the trailing stop instead. Prevents the AGMI-style disaster where +75% peak gets cut at -9%. Default: on.',
    },
    peak_protection_min_peak_pnl_pct: {
      label: 'Peak Protection Min Peak P&L (%)',
      description: 'Minimum peak unrealized P&L (vs entry) for a position to qualify for peak protection. Below this, fast-loser-cut still fires. Default: 30.0.',
    },
    peak_protection_max_drawdown_from_peak_pct: {
      label: 'Peak Protection Max Drawdown From Peak (%)',
      description: 'Maximum allowed drawdown from peak PRICE (not peak P&L) for peak protection to remain active. Drops beyond this re-enable fast-loser-cut. Sized at 60% so an AGMI-style position that peaked at +75% then dropped to -9% (47% drawdown from peak) is still protected with margin. Default: 60.0.',
    },
    high_conviction_loser_min_hold_days: {
      label: 'High-Conviction Loser Rotation Min Hold (Days)',
      description: 'Minimum hold-days before a losing position can be rotated out to fund a high-conviction blocked-buy. Lower than the standard rotation_min_hold_days (10) so SNDK/MU/LITE-style names can land sooner. Default: 5.',
    },
    high_conviction_profitable_min_hold_days: {
      label: 'High-Conviction Profitable Rotation Min Hold (Days)',
      description: 'Minimum hold-days before a profitable winner without an active leader_lock can be rotated out to fund a high-conviction blocked-buy. Lower than standard 20. Default: 10.',
    },
    backfill_budget_reserve_pct_high_conviction: {
      label: 'Backfill Reserve % (High-Conviction)',
      description: 'Backfill budget reserve fraction when the queue contains a high-conviction item. Bumped from the standard 0.20 to give SNDK/MU/LITE-style names actual cash to execute against. Self-disables when no high-conviction item is queued. Default: 0.35 (35%).',
    },
    nexus_discovery_bootstrap_enabled: {
      label: 'Discovery Snapshot Bootstrap',
      description: 'When ON, on the first bar of a fresh scope (e.g. after an LLM model change), import the most recent prior scope\'s discovered tickers from the GraphNexusDiscoverySnapshots table. Filters by per-bucket fingerprint match (LLM/data/graph) so stale entries are dropped automatically. Prevents losing SNDK/MU/LITE-style discoveries when only LLM or trading config changes. Default: on.',
    },
    nexus_discovery_snapshot_enabled: {
      label: 'Discovery Snapshot Writer',
      description: 'When ON, write the current scope\'s discoveries to the GraphNexusDiscoverySnapshots table on every lookback bar. The snapshot is the source for cross-scope bootstrap (above). Disable if you want to keep using bootstrap from existing snapshots without writing new ones. Default: on.',
    },
    nexus_discovery_snapshot_max_age_days: {
      label: 'Discovery Snapshot Max Age (Days)',
      description: 'Maximum age of a snapshot (vs current run\'s start date) before bootstrap refuses to use it. Older snapshots are ignored and lookback runs fresh. Prevents importing very stale discoveries from old runs. Default: 90.',
    },
    nexus_discovery_bootstrap_min_keep_pct: {
      label: 'Discovery Bootstrap Min Keep %',
      description: 'Minimum fraction of snapshot tickers that must survive the per-bucket config-fingerprint filter for bootstrap to proceed. If too many tickers are dropped (e.g. all 3 fingerprints differ), bootstrap rolls back and lookback runs fresh instead. Only applies in non-merge mode. Default: 0.30 (30%).',
    },
    nexus_discovery_bootstrap_merge_mode: {
      label: 'Discovery Bootstrap Merge Mode',
      description: 'When ON, bootstrap also fires on a non-empty current scope to fill in MISSING tickers from the snapshot (existing rows are never overwritten). Off by default; enable when a prior run of the same scope built an incomplete discovery list and you want to top it up from a sibling scope\'s snapshot. The min-keep-pct rollback is bypassed in merge mode (any addition is a win). Default: off.',
    },
    max_rotations_per_day_high_conviction: {
      label: 'Max Rotations Per Day (High-Conviction Bonus)',
      description: 'Additional rotation slots reserved for high-conviction blocked-buys (raw score >= nexus_high_conviction_threshold). Additive on top of max_rotations_per_day, so total rotations per bar = max_rotations_per_day + this value when high-conviction items exist. Without this, max_rotations_per_day=1 starves the queue. Default: 4.',
    },
    queue_rotation_promotion_enabled: {
      label: 'Queue->Rotation Promotion',
      description: 'When ON, aged backfill-queue items (bars_in_queue >= queue_rotation_promotion_min_bars AND raw_score >= nexus_high_conviction_threshold) are promoted into the rotation candidate list (_blocked_buys) so they can compete for rotation slots instead of being starved by fresh new discoveries. The structural fix that lets queued tickers like SNDK actually get bought. Default: on.',
    },
    queue_rotation_promotion_min_bars: {
      label: 'Queue->Rotation Promotion Min Bars',
      description: 'Minimum bars a queued item must wait before being promoted into the rotation candidate list. Lower values are more aggressive (promote sooner). Default: 1 (next bar after queueing).',
    },
    queue_rotation_promotion_max_items: {
      label: 'Queue->Rotation Promotion Max Items',
      description: 'Maximum number of queued items promoted to _blocked_buys per bar to prevent runaway list growth. Items are sorted by age desc then score desc and the top N are taken. Default: 20.',
    },
    blocked_buys_age_boost_per_bar: {
      label: 'Blocked Buys Age Boost Per Bar',
      description: 'Effective score boost added to a queued blocked-buy candidate per bar of queue age. Used in the rotation candidate sort so older items overtake newer items with marginally higher raw scores. Default: 0.05.',
    },
    blocked_buys_age_boost_cap: {
      label: 'Blocked Buys Age Boost Cap',
      description: 'Maximum total age boost (caps blocked_buys_age_boost_per_bar * bars_in_queue). Prevents stale items from dominating forever. Default: 0.40.',
    },
    v28_hold_trim_cooldown_bars: {
      label: 'Per-Hold Trim Cooldown (Bars)',
      description: 'Number of bars a held position is excluded from rotation after being partial-trimmed by a break_glass rotation. Prevents cascade erosion where the same winner is trimmed bar after bar to fund every new high-conviction arrival. Default: 3.',
    },
    v28_hc_profitable_min_delta: {
      label: 'V28.2 HC Profitable Hold Min Delta',
      description: 'Minimum rotation delta required for a high-conviction inflow to rotate against a profitable hold WITHOUT winner_lock active (e.g. pnl 0-3% positions). Lower values are more aggressive. Without this, profitable holds with marginal pnl block all HC rotations even at large deltas. Default: 1.0.',
    },
    v28_hc_losing_min_delta: {
      label: 'V28.2 HC Losing Hold Min Delta',
      description: 'Minimum rotation delta required for a high-conviction inflow to rotate against a LOSING hold even when held_rotation_score is positive. The standard losing_hold gate requires held_rotation_score <= 0 which excludes most losing holds (they often still have positive forward signal). HC inflows can bypass that gate at delta >= this value. Default: 0.75.',
    },
    v28_hc_profitable_max_held_pnl: {
      label: 'V28.4 HC Profitable Rotation Max Held P&L (%)',
      description: 'Maximum unrealized P&L (%) of a held position for v28_hc_profitable_break_glass to fire. Prevents the full-exit V28.3 rotation from evicting genuine mid-range winners (e.g. +4% positions). Only marginal holds (pnl <= this value) can be rotated out via this path. Default: 2.0.',
    },
    v28_hc_losing_max_held_pnl: {
      label: 'V28.5 HC Losing Rotation Max Held P&L (%)',
      description: 'Maximum unrealized P&L (%) of a held LOSING position for v28_hc_losing_break_glass to fire. Decoupled from rotation_replace_loss_threshold_pct (which can be set very strict, e.g. -5%) so the V28.2 losing path can fire on marginal losers (pnl -1 to -4%). Default: -1.5.',
    },
  },
  // Spec 2026-09-24 section 5.1. Defaults are ST's (swing_trader/constants.py).
  strategy_swing: {
    strategy_swing_enabled: {
      label: 'Swing Lane Enabled',
      description: 'Master switch for the swing lane, in backtests and live. Off means the lane emits nothing.',
    },
    rsi_period: {
      label: 'RSI Period (Bars)',
      description: 'Daily bars in the RSI behind both the entry filter and the RSI-cross exit. ST default 14.',
    },
    rsi_entry_max: {
      label: 'Max RSI At Entry',
      description: 'An entry needs RSI at or below this value. ST default 50.',
    },
    rsi_overbought: {
      label: 'RSI Exit Level',
      description: 'The overbought level the RSI-cross exit watches. ST default 70.',
    },
    sma_long: {
      label: 'Long Trend SMA (Bars)',
      description: 'Length of the long-term trend filter in daily bars. ST default 200.',
    },
    macd_fast: {
      label: 'MACD Fast EMA (Bars)',
      description: 'Fast EMA length of the MACD entry filter. ST default 12.',
    },
    macd_slow: {
      label: 'MACD Slow EMA (Bars)',
      description: 'Slow EMA length of the MACD entry filter. ST default 26.',
    },
    macd_signal: {
      label: 'MACD Signal EMA (Bars)',
      description: 'Signal-line EMA length of the MACD entry filter. ST default 9.',
    },
    vol_avg_period: {
      label: 'Volume Average (Bars)',
      description: 'Bars in the volume average the entry filter compares against. ST default 20.',
    },
    adx_period: {
      label: 'ADX Period (Bars)',
      description: 'Lookback of the ADX trend-strength filter. ST default 14.',
    },
    adx_min: {
      label: 'Min ADX',
      description: 'An entry needs ADX at or above this trend-strength floor. ST default 15.',
    },
    spy_buffer: {
      label: 'SPY Regime Buffer',
      description: 'The bull regime needs SPY above its 200-day SMA times this multiplier. ST default 1.03.',
    },
    vix_max: {
      label: 'Max VIX',
      description: 'The bull regime also needs VIX at or below this level. ST default 25.',
    },
    position_size_pct: {
      label: 'Position Size (Fraction Of Equity)',
      description: 'Each entry buys this fraction of equity in whole shares, scaled by the AI size adjustment. ST default 0.125.',
    },
    max_positions: {
      label: 'Max Open Positions',
      description: 'The lane holds at most this many swing positions at once. ST default 8.',
    },
    max_per_sector: {
      label: 'Max Positions Per Sector',
      description: 'At most this many open swing positions may share one GICS sector. ST default 1.',
    },
    profit_target: {
      label: 'Take-Profit (Fraction)',
      description: 'The bracket take-profit sits this far above the prior close. ST default 0.09 (+9%).',
    },
    stop_loss: {
      label: 'Stop-Loss (Fraction)',
      description: 'The bracket stop sits this far below the prior close. ST default 0.06 (-6%).',
    },
    bear_regime_days: {
      label: 'Bear Sessions Before Defensive Mode',
      description: 'Consecutive bear-regime NY sessions before the lane trades the defensive universe. ST default 10.',
    },
    defensive_universe: {
      label: 'Defensive Universe',
      description: 'Symbols the lane may buy in bear mode. ST default XLP, XLU, XLV, GLD, SHY.',
    },
    earnings_hard_block_days: {
      label: 'Earnings Block (Days)',
      description: 'Live only: no entry within this many days of an earnings date. Off in backtests, as in ST. ST default 5.',
    },
    ai_gate_enabled: {
      label: 'AI Conviction Gate',
      description: 'Live only: the linked Conviction LLM scores every candidate before it is placed. With no model linked, live entries are refused.',
    },
    ai_approve_threshold: {
      label: 'AI Auto-Approve Score',
      description: 'Scores at or above this are placed without asking. ST default 75.',
    },
    ai_review_threshold: {
      label: 'AI Review Score',
      description: 'Scores from this up to the auto-approve score wait for your approval on web or iOS. Lower scores are rejected. ST default 50.',
    },
    scan_time_et: {
      label: 'Scan Time (ET)',
      description: 'The live scan runs once per NY session, at the first tick at or after this time, and submits GTC brackets that queue for the open. ST default 09:15.',
    },
    live_max_order_fraction: {
      label: 'Live Max Order Fraction',
      description: 'The order gate refuses any single order larger than this fraction of equity.',
    },
    live_max_symbol_fraction: {
      label: 'Live Max Symbol Fraction',
      description: 'The order gate refuses a buy that would take one symbol above this fraction of equity.',
    },
    live_max_leveraged_fraction: {
      label: 'Live Max Leveraged-ETF Fraction',
      description: 'The order gate caps leveraged-ETF exposure at this fraction of equity.',
    },
    live_soft_drawdown: {
      label: 'Live Soft Drawdown',
      description: 'Drawdown from the equity peak that puts the account at the soft risk level, where new buys freeze.',
    },
    live_hard_drawdown: {
      label: 'Live Hard Drawdown',
      description: 'Drawdown from the equity peak that puts the account at the hard risk level. Must sit above the soft rung.',
    },
    live_kill_drawdown: {
      label: 'Live Kill Drawdown',
      description: 'Drawdown from the equity peak that reaches the kill level: working orders are cancelled and trading halts. Must sit above the hard rung.',
    },
    honour_single_position_cap: {
      label: 'Honour Single-Position Cap',
      description: 'Apply the broker single-position cap below to every swing buy.',
    },
    broker_max_single_position_pct: {
      label: 'Single-Position Cap (Fraction)',
      description: 'No single position may exceed this fraction of equity.',
    },
  },
  // Spec 2026-09-24 section 5.2. Live and paper only; backtests skip the wheel.
  strategy_wheel: {
    strategy_wheel_enabled: {
      label: 'Wheel Lane Enabled',
      description: 'Master switch for the cash-secured-put wheel. Live and paper only; the account needs options level 1 or higher.',
    },
    rsi_min: {
      label: 'Min RSI',
      description: 'A put candidate needs RSI at or above this value. ST default 30.',
    },
    rsi_max: {
      label: 'Max RSI',
      description: 'A put candidate needs RSI at or below this value. ST default 60.',
    },
    sma_trend: {
      label: 'Trend SMA (Bars)',
      description: 'Length of the trend filter in daily bars. ST default 50.',
    },
    atr_period: {
      label: 'ATR Period (Bars)',
      description: 'Lookback of the ATR the candidate screen uses to place the strike. ST default 14.',
    },
    strike_atr_mult: {
      label: 'Strike Offset (ATR Multiple)',
      description: 'How many ATRs below the price the screen looks for a strike. ST default 0.5.',
    },
    min_premium_pct: {
      label: 'Min Premium (Fraction)',
      description: 'Skip a candidate whose premium is below this fraction of the price. ST default 0.005 (0.5%).',
    },
    target_delta: {
      label: 'Target Put Delta',
      description: 'The lane sells the put whose delta is closest to this, read from the full chain. ST default 0.25.',
    },
    days_to_expiry: {
      label: 'Days To Expiry',
      description: 'Target days to expiry for the sold put. ST default 7.',
    },
    max_collateral_pct: {
      label: 'Max Collateral Per Underlying',
      description: 'Cash-secured collateral on one underlying, counting puts already open, stays under this fraction of equity. ST default 0.25.',
    },
    max_per_sector: {
      label: 'Max Puts Per Sector',
      description: 'At most this many open puts may share one sector. ST default 2.',
    },
    auto_covered_call: {
      label: 'Auto Covered Call',
      description: 'After an assignment, sell a covered call automatically. Off logs the candidate only (dry run), as in ST.',
    },
    approve_threshold: {
      label: 'AI Auto-Approve Score',
      description: 'Put candidates scoring at or above this are sold without asking. ST default 75.',
    },
    review_threshold: {
      label: 'AI Review Score',
      description: 'Scores from this up to the auto-approve score wait for your approval on web or iOS. Lower scores are rejected. ST default 50.',
    },
    earnings_block_days: {
      label: 'Earnings Block (Days)',
      description: 'No put is sold within this many days of an earnings date. ST default 7.',
    },
    limit_bid_mult: {
      label: 'Limit Price (Bid Multiple)',
      description: 'The sell-to-open limit starts at the live bid times this, then follows the ST fallback ladder. ST default 0.95.',
    },
    scan_weekday: {
      label: 'Scan Weekday',
      description: '0 is Monday. If the Monday scan does not complete, it runs on Tuesday. ST default 0.',
    },
    scan_time_et: {
      label: 'Scan Time (ET)',
      description: 'The weekly put scan runs at the first tick at or after this time on the scan weekday. ST default 10:30.',
    },
    monitor_time_et: {
      label: 'Monitor Time (ET)',
      description: 'Daily check that buys a put back at 10% ITM, at 5% ITM with 2 or fewer days left, or ITM on expiry day. ST default 15:45.',
    },
  },
}

export const LLM_PROVIDER_OPTIONS = [
  { value: 'gemini', label: 'Google Gemini' },
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'openai', label: 'OpenAI Compatible' },
  { value: 'azure', label: 'Azure OpenAI' },
  { value: 'nvidia', label: 'NVIDIA NIM' },
  { value: 'ollama', label: 'Ollama (local / cloud)' },
  { value: 'bedrock', label: 'AWS Bedrock' },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'claude-cli', label: 'Claude Code CLI (subscription)' },
  { value: 'codex-cli', label: 'OpenAI Codex CLI (subscription)' },
]

export const LLM_REASONING_EFFORT_OPTIONS = [
  { value: '', label: 'Default' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
]

export const NVIDIA_REASONING_EFFORT_OPTIONS = [
  { value: '', label: 'None (thinking disabled)' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High (full reasoning)' },
]

// Ollama's ``think`` parameter accepts a bool (true / false) or an
// effort string (low / medium / high). The same value is sent to every
// model; whether a given model honours it depends on the model itself.
// Most non-reasoning models silently ignore the field.
export const OLLAMA_THINK_OPTIONS = [
  { value: '', label: 'Default (model decides)' },
  { value: 'false', label: 'Off (disable thinking)' },
  { value: 'true', label: 'On (enable thinking)' },
  { value: 'low', label: 'Low effort' },
  { value: 'medium', label: 'Medium effort' },
  { value: 'high', label: 'High effort' },
]

// AWS Bedrock reasoning. Maps to Converse additionalModelRequestFields on the
// backend, model-family-specific: Claude 3.7+ gets a thinking-token budget,
// OpenAI gpt-oss gets reasoning_effort (low/medium/high). Other families are
// omitted. "off" = no reasoning.
export const BEDROCK_REASONING_OPTIONS = [
  { value: 'off', label: 'Off' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
]

// Maps to the claude CLI's `--effort` flag. CC supports five levels;
// the default ("") lets CC decide.
export const CLAUDE_CLI_EFFORT_OPTIONS = [
  { value: '', label: 'Default' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'xhigh', label: 'Extra High' },
  { value: 'max', label: 'Max' },
]

const KNOWN_LLM_ROLE_LABELS = {
  '': 'Default LLM',
  'sentiment_': 'Daily Sentiment LLM',
  'company_article_': 'Company Article LLM',
  'macro_article_': 'Macro Article LLM',
  'event_maintenance_': 'Event Maintenance LLM',
  'overlay_': 'Trade Overlay LLM',
  'analyst_panel_': 'Analyst Panel LLM (R1+R2 Debate)',
  'conviction_': 'Conviction LLM',
}

const KNOWN_LOOKBACK_LLM_ROLE_LABELS = {
  'lookback_': 'Default LLM (Lookback)',
  'lookback_sentiment_': 'Daily Sentiment LLM (Lookback)',
  'lookback_company_article_': 'Company Article LLM (Lookback)',
  'lookback_macro_article_': 'Macro Article LLM (Lookback)',
  'lookback_event_maintenance_': 'Event Maintenance LLM (Lookback)',
  'lookback_overlay_': 'Trade Overlay LLM (Lookback)',
}

const KNOWN_LLM_ROLE_PREFIXES_BY_STRATEGY = {
  graph_nexus_analysis: ['', 'sentiment_', 'company_article_', 'macro_article_', 'event_maintenance_', 'overlay_', 'analyst_panel_'],
  // Registered, not only discovered from a key: a document saved before the
  // header gained conviction_llm_model_id still shows the model card.
  strategy_swing: ['conviction_'],
  strategy_wheel: ['conviction_'],
}

const KNOWN_LOOKBACK_LLM_ROLE_PREFIXES_BY_STRATEGY = {
  graph_nexus_analysis: ['lookback_', 'lookback_sentiment_', 'lookback_company_article_', 'lookback_macro_article_', 'lookback_event_maintenance_', 'lookback_overlay_'],
}

function titleizeToken(token) {
  if (!token) return ''
  const lower = String(token).toLowerCase()
  if (ACRONYMS.has(lower)) return lower.toUpperCase()
  return lower.charAt(0).toUpperCase() + lower.slice(1)
}

export function humanizeStrategyConfigKey(key) {
  return String(key || '')
    .split('_')
    .filter(Boolean)
    .map(titleizeToken)
    .join(' ')
}

export function getStrategyConfigFieldMeta(strategyName, key) {
  const strategyMeta = STRATEGY_FIELD_META[String(strategyName || '').trim()] || {}
  return strategyMeta[key] || {
    label: humanizeStrategyConfigKey(key),
    description: '',
  }
}

function llmRoleLabel(prefix) {
  if (KNOWN_LLM_ROLE_LABELS[prefix] != null) return KNOWN_LLM_ROLE_LABELS[prefix]
  if (KNOWN_LOOKBACK_LLM_ROLE_LABELS[prefix] != null) return KNOWN_LOOKBACK_LLM_ROLE_LABELS[prefix]
  const cleaned = String(prefix || '').replace(/_$/, '')
  return cleaned ? `${humanizeStrategyConfigKey(cleaned)} LLM` : 'Default LLM'
}

function buildLlmGroup(prefix, keySet) {
  const providerKey = `${prefix}llm_provider`
  const modelKey = prefix
    ? `${prefix}llm_model`
    : (keySet.has('llm_model') || !keySet.has('model_name') ? 'llm_model' : 'model_name')
  return {
    prefix,
    label: llmRoleLabel(prefix),
    providerKey,
    modelKey,
    modelIdKey: `${prefix}llm_model_id`,
    apiKeyKey: `${prefix}llm_api_key`,
    azureApiKeyKey: `${prefix}azure_openai_api_key`,
    openaiBaseUrlKey: `${prefix}openai_base_url`,
    azureEndpointKey: `${prefix}azure_openai_endpoint`,
    azureApiVersionKey: `${prefix}azure_openai_api_version`,
    reasoningEffortKey: `${prefix}llm_reasoning_effort`,
    openrouterBaseUrlKey: `${prefix}openrouter_base_url`,
    openrouterRefererKey: `${prefix}openrouter_referer`,
    openrouterTitleKey: `${prefix}openrouter_title`,
  }
}

function allGroupKeys(group) {
  return [
    group.providerKey,
    group.modelKey,
    group.modelIdKey,
    group.apiKeyKey,
    group.azureApiKeyKey,
    group.openaiBaseUrlKey,
    group.azureEndpointKey,
    group.azureApiVersionKey,
    group.reasoningEffortKey,
    group.openrouterBaseUrlKey,
    group.openrouterRefererKey,
    group.openrouterTitleKey,
  ]
}

export function getLlmProviderLabel(provider) {
  const value = String(provider || '').trim().toLowerCase()
  const match = LLM_PROVIDER_OPTIONS.find(option => option.value === value)
  return match ? match.label : humanizeStrategyConfigKey(value || 'llm')
}

export function getStrategyLlmConfigGroups(strategyName, config, refConfig) {
  const strategy = String(strategyName || '').trim()
  const current = config || {}
  const reference = refConfig || {}
  const keySet = new Set([...Object.keys(reference), ...Object.keys(current)])
  const prefixes = new Set(KNOWN_LLM_ROLE_PREFIXES_BY_STRATEGY[strategy] || [])

  for (const key of keySet) {
    if (key.endsWith('llm_provider')) {
      prefixes.add(key.slice(0, -'llm_provider'.length))
    }
    if (key.endsWith('llm_model_id')) {
      prefixes.add(key.slice(0, -'llm_model_id'.length))
    }
  }

  if (
    ['llm_provider', 'llm_api_key', 'llm_model', 'llm_model_id', 'model_name', 'openai_base_url', 'azure_openai_api_key', 'azure_openai_endpoint', 'azure_openai_api_version', 'llm_reasoning_effort']
      .some(key => keySet.has(key))
  ) {
    prefixes.add('')
  }

  return [...prefixes]
    .sort((a, b) => {
      if (a === '') return -1
      if (b === '') return 1
      return llmRoleLabel(a).localeCompare(llmRoleLabel(b))
    })
    .map(prefix => buildLlmGroup(prefix, keySet))
}

export function getStrategyLookbackLlmConfigGroups(strategyName) {
  const strategy = String(strategyName || '').trim()
  const prefixes = KNOWN_LOOKBACK_LLM_ROLE_PREFIXES_BY_STRATEGY[strategy]
  if (!prefixes) return []
  return prefixes.map(prefix => buildLlmGroup(prefix, new Set()))
}

export function isLlmManagedConfigField(strategyName, key, config, refConfig) {
  const groups = [
    ...getStrategyLlmConfigGroups(strategyName, config, refConfig),
    ...getStrategyLookbackLlmConfigGroups(strategyName),
  ]
  return groups.some(group => allGroupKeys(group).includes(key))
}

function _pickFirstNonEmpty(...values) {
  for (const value of values) {
    if (value == null) continue
    const text = String(value).trim()
    if (text) return text
  }
  return ''
}

export function buildStrategyLlmDraft(config, group) {
  const source = config || {}
  const prefix = String(group.prefix || '')
  const isLookback = prefix.startsWith('lookback_')
  // For lookback roles, also fall back to lookback default before main config
  const lbDefaultProvider = isLookback && prefix !== 'lookback_' ? (source.lookback_llm_provider || '') : ''
  const lbDefaultModel = isLookback && prefix !== 'lookback_' ? (source.lookback_llm_model || '') : ''
  // For lookback roles, also fall back to the corresponding main role
  // e.g. lookback_company_article_ → company_article_
  const mainRolePrefix = isLookback ? prefix.replace(/^lookback_/, '') : ''
  const mainRoleProviderKey = mainRolePrefix ? `${mainRolePrefix}llm_provider` : ''
  const mainRoleModelKey = mainRolePrefix ? `${mainRolePrefix}llm_model` : ''

  const inheritedProvider = _pickFirstNonEmpty(
    source[group.providerKey],
    lbDefaultProvider,
    mainRoleProviderKey ? source[mainRoleProviderKey] : '',
    prefix ? source.llm_provider : '',
    'gemini',
  )
  const provider = String(inheritedProvider || 'gemini').trim().toLowerCase() || 'gemini'
  const inheritedModel = _pickFirstNonEmpty(
    source[group.modelKey],
    lbDefaultModel,
    mainRoleModelKey ? source[mainRoleModelKey] : '',
    prefix ? source.llm_model : '',
    prefix ? source.model_name : '',
  )

  // For lookback roles, also check lookback default and main role for keys/endpoints
  const lbDefaultAzureApiKey = isLookback && prefix !== 'lookback_' ? (source.lookback_azure_openai_api_key || '') : ''
  const lbDefaultApiKey = isLookback && prefix !== 'lookback_' ? (source.lookback_llm_api_key || '') : ''
  const lbDefaultOpenaiBaseUrl = isLookback && prefix !== 'lookback_' ? (source.lookback_openai_base_url || '') : ''
  const lbDefaultAzureEndpoint = isLookback && prefix !== 'lookback_' ? (source.lookback_azure_openai_endpoint || '') : ''
  const lbDefaultAzureApiVersion = isLookback && prefix !== 'lookback_' ? (source.lookback_azure_openai_api_version || '') : ''
  const lbDefaultReasoningEffort = isLookback && prefix !== 'lookback_' ? (source.lookback_llm_reasoning_effort || '') : ''
  const mainRoleAzureApiKey = mainRolePrefix ? (source[`${mainRolePrefix}azure_openai_api_key`] || '') : ''
  const mainRoleApiKey = mainRolePrefix ? (source[`${mainRolePrefix}llm_api_key`] || '') : ''
  const mainRoleOpenaiBaseUrl = mainRolePrefix ? (source[`${mainRolePrefix}openai_base_url`] || '') : ''
  const mainRoleAzureEndpoint = mainRolePrefix ? (source[`${mainRolePrefix}azure_openai_endpoint`] || '') : ''
  const mainRoleAzureApiVersion = mainRolePrefix ? (source[`${mainRolePrefix}azure_openai_api_version`] || '') : ''
  const mainRoleReasoningEffort = mainRolePrefix ? (source[`${mainRolePrefix}llm_reasoning_effort`] || '') : ''

  return {
    modelId: (source[group.modelIdKey] || '').trim(),
    provider,
    model: String(inheritedModel || '').trim(),
    apiKey: String(
      provider === 'azure'
        ? _pickFirstNonEmpty(
            source[group.azureApiKeyKey],
            source[group.apiKeyKey],
            lbDefaultAzureApiKey, lbDefaultApiKey,
            mainRoleAzureApiKey, mainRoleApiKey,
            prefix ? source.azure_openai_api_key : '',
            prefix ? source.llm_api_key : '',
          )
        : _pickFirstNonEmpty(
            source[group.apiKeyKey],
            source[group.azureApiKeyKey],
            lbDefaultApiKey, lbDefaultAzureApiKey,
            mainRoleApiKey, mainRoleAzureApiKey,
            prefix ? source.llm_api_key : '',
            prefix ? source.azure_openai_api_key : '',
          )
    ).trim(),
    openaiBaseUrl: String(
      _pickFirstNonEmpty(
        source[group.openaiBaseUrlKey],
        lbDefaultOpenaiBaseUrl,
        mainRoleOpenaiBaseUrl,
        prefix ? source.openai_base_url : '',
      )
    ).trim(),
    nvidiaBaseUrl: String(
      _pickFirstNonEmpty(
        source[`${prefix}nvidia_base_url`],
        isLookback && prefix !== 'lookback_' ? (source.lookback_nvidia_base_url || '') : '',
        mainRolePrefix ? (source[`${mainRolePrefix}nvidia_base_url`] || '') : '',
        prefix ? source.nvidia_base_url : '',
        'https://integrate.api.nvidia.com/v1',
      )
    ).trim(),
    azureEndpoint: String(
      _pickFirstNonEmpty(
        source[group.azureEndpointKey],
        lbDefaultAzureEndpoint,
        mainRoleAzureEndpoint,
        prefix ? source.azure_openai_endpoint : '',
      )
    ).trim(),
    azureApiVersion: String(
      _pickFirstNonEmpty(
        source[group.azureApiVersionKey],
        lbDefaultAzureApiVersion,
        mainRoleAzureApiVersion,
        prefix ? source.azure_openai_api_version : '',
        '2024-10-21',
      )
    ).trim() || '2024-10-21',
    reasoningEffort: String(
      _pickFirstNonEmpty(
        source[group.reasoningEffortKey],
        lbDefaultReasoningEffort,
        mainRoleReasoningEffort,
        prefix ? source.llm_reasoning_effort : '',
      )
    ).trim().toLowerCase(),
    openrouterBaseUrl: String(
      _pickFirstNonEmpty(
        source[group.openrouterBaseUrlKey],
        prefix ? source.openrouter_base_url : '',
        'https://openrouter.ai/api/v1',
      )
    ).trim(),
    openrouterReferer: String(
      _pickFirstNonEmpty(
        source[group.openrouterRefererKey],
        prefix ? source.openrouter_referer : '',
      )
    ).trim(),
    openrouterTitle: String(
      _pickFirstNonEmpty(
        source[group.openrouterTitleKey],
        prefix ? source.openrouter_title : '',
      )
    ).trim(),
  }
}

export function applyStrategyLlmDraft(config, group, draft) {
  const next = { ...(config || {}) }
  for (const key of allGroupKeys(group)) {
    delete next[key]
  }

  const modelId = String(draft?.modelId || '').trim()
  if (modelId) {
    // Store only the model_id reference — runtime resolves credentials
    next[group.modelIdKey] = modelId
    // Also store provider/model for display purposes (read-only summary)
    const provider = String(draft?.provider || 'gemini').trim().toLowerCase() || 'gemini'
    next[group.providerKey] = provider
    const model = String(draft?.model || '').trim()
    if (model) next[group.modelKey] = model
    return next
  }

  // Inline mode — store full credentials (existing behavior)
  const provider = String(draft?.provider || 'gemini').trim().toLowerCase() || 'gemini'
  next[group.providerKey] = provider
  const model = String(draft?.model || '').trim()
  if (model) next[group.modelKey] = model

  const apiKey = String(draft?.apiKey || '').trim()
  if (provider === 'azure') {
    if (apiKey) {
      next[group.azureApiKeyKey] = apiKey
      next[group.apiKeyKey] = apiKey
    }
    const azureEndpoint = String(draft?.azureEndpoint || '').trim()
    const azureApiVersion = String(draft?.azureApiVersion || '2024-10-21').trim() || '2024-10-21'
    if (azureEndpoint) next[group.azureEndpointKey] = azureEndpoint
    if (azureApiVersion) next[group.azureApiVersionKey] = azureApiVersion
  } else if (apiKey) {
    next[group.apiKeyKey] = apiKey
  }

  if (provider === 'openai') {
    const openaiBaseUrl = String(draft?.openaiBaseUrl || '').trim()
    if (openaiBaseUrl) next[group.openaiBaseUrlKey] = openaiBaseUrl
  }
  if (provider === 'nvidia') {
    const nvidiaBaseUrl = String(draft?.nvidiaBaseUrl || '').trim()
    if (nvidiaBaseUrl) next[`${String(group.prefix || '')}nvidia_base_url`] = nvidiaBaseUrl
  }
  if (provider === 'openrouter') {
    const openrouterBaseUrl = String(draft?.openrouterBaseUrl || '').trim()
    if (openrouterBaseUrl) next[group.openrouterBaseUrlKey] = openrouterBaseUrl
    const openrouterReferer = String(draft?.openrouterReferer || '').trim()
    if (openrouterReferer) next[group.openrouterRefererKey] = openrouterReferer
    const openrouterTitle = String(draft?.openrouterTitle || '').trim()
    if (openrouterTitle) next[group.openrouterTitleKey] = openrouterTitle
  }
  if (provider === 'openai' || provider === 'azure' || provider === 'nvidia' || provider === 'openrouter') {
    const reasoningEffort = String(draft?.reasoningEffort || '').trim().toLowerCase()
    if (reasoningEffort) next[group.reasoningEffortKey] = reasoningEffort
  }

  return next
}

export function buildStrategyLlmTestPayload(draft) {
  const provider = String(draft?.provider || 'gemini').trim().toLowerCase() || 'gemini'
  const payload = {
    provider,
    model: String(draft?.model || '').trim(),
    api_key: String(draft?.apiKey || '').trim(),
  }
  if (provider === 'openai') {
    payload.openai_base_url = String(draft?.openaiBaseUrl || '').trim()
  }
  if (provider === 'azure') {
    payload.azure_openai_endpoint = String(draft?.azureEndpoint || '').trim()
    payload.azure_openai_api_version = String(draft?.azureApiVersion || '2024-10-21').trim() || '2024-10-21'
  }
  if (provider === 'nvidia') {
    payload.openai_base_url = String(draft?.nvidiaBaseUrl || 'https://integrate.api.nvidia.com/v1').trim()
  }
  if (provider === 'ollama') {
    payload.ollama_base_url = String(draft?.ollamaBaseUrl || 'http://localhost:11434').trim()
    const keepAlive = String(draft?.ollamaKeepAlive || '').trim()
    if (keepAlive) payload.ollama_keep_alive = keepAlive
    const think = String(draft?.ollamaThink || '').trim()
    if (think) payload.ollama_think = think
    // api_key is already populated above for cloud Ollama; leave empty for local.
  }
  if (provider === 'bedrock') {
    payload.bedrock_region = String(draft?.bedrockRegion || '').trim()
    const reasoning = String(draft?.bedrockReasoning || '').trim().toLowerCase()
    if (reasoning) payload.bedrock_reasoning = reasoning
    // api_key is the Bedrock bearer token, populated above.
  }
  if (provider === 'openrouter') {
    payload.openrouter_base_url = String(draft?.openrouterBaseUrl || 'https://openrouter.ai/api/v1').trim()
    const referer = String(draft?.openrouterReferer || '').trim()
    if (referer) payload.openrouter_referer = referer
    const title = String(draft?.openrouterTitle || '').trim()
    if (title) payload.openrouter_title = title
  }
  if (provider === 'openai' || provider === 'azure' || provider === 'nvidia' || provider === 'openrouter' || provider === 'codex-cli') {
    const reasoningEffort = String(draft?.reasoningEffort || '').trim().toLowerCase()
    if (reasoningEffort) payload.reasoning_effort = reasoningEffort
  }
  return payload
}

export function describeStrategyLlmGroup(config, group, savedModels) {
  const source = config || {}
  const prefix = String(group.prefix || '')
  // For lookback groups, show "inherits" when no explicit lookback provider is configured
  // (neither the specific role nor the lookback default)
  if (prefix.startsWith('lookback_') && !source[group.providerKey]) {
    // Check if lookback default is set (for non-default lookback roles)
    if (prefix === 'lookback_' || !source.lookback_llm_provider) {
      return 'Inherits from main config'
    }
  }
  const draft = buildStrategyLlmDraft(config, group)
  const providerLabel = getLlmProviderLabel(draft.provider)
  const model = draft.model || 'model not set'
  // Show saved model name if referenced
  const modelId = (source[group.modelIdKey] || '').trim()
  if (modelId && Array.isArray(savedModels)) {
    const found = savedModels.find(m => m.id === modelId)
    if (found) return `${found.name}`
  }
  return `${providerLabel} / ${model}`
}

export function shouldHideStrategyConfigField(strategyName, key, config) {
  const strategy = String(strategyName || '').trim()
  const cfg = config || {}
  if (strategy === 'graph_nexus_analysis') {
    if (key === 'num_articles' && cfg.max_daily_alpaca_articles != null) return true
    if (key === 'google_news_max_articles' && cfg.max_daily_google_news_articles != null) return true
  }
  return false
}
