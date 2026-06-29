/// strategy_config.dart
/// Ported from frontend/src/utils/strategyConfig.js
/// Provides key humanization, field metadata, and LLM option lists.
library;

// ── Acronyms ──────────────────────────────────────────────────────────────────

const _acronyms = {
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
};

// ── Humanize ──────────────────────────────────────────────────────────────────

String _titleizeToken(String token) {
  if (token.isEmpty) return '';
  final lower = token.toLowerCase();
  if (_acronyms.contains(lower)) return lower.toUpperCase();
  return lower[0].toUpperCase() + lower.substring(1);
}

/// Converts a snake_case config key to Title Case, uppercasing known acronyms.
/// e.g. `"llm_provider"` → `"LLM Provider"`, `"rsi_period"` → `"RSI Period"`.
String humanizeStrategyConfigKey(String key) {
  return key
      .split('_')
      .where((t) => t.isNotEmpty)
      .map(_titleizeToken)
      .join(' ');
}

// ── Field metadata ────────────────────────────────────────────────────────────

class StrategyFieldMeta {
  const StrategyFieldMeta({required this.label, required this.description});
  final String label;
  final String description;
}

/// Field-level metadata for specific strategy types.
/// Keyed as `STRATEGY_FIELD_META[strategyName][fieldKey]`.
const Map<String, Map<String, StrategyFieldMeta>> strategyFieldMeta = {
  'graph_nexus_analysis': {
    'min_articles': StrategyFieldMeta(
      label: 'Minimum Alpaca Articles Before Signal',
      description:
          'Nexus waits for at least this many Alpaca articles before trusting the daily article pass.',
    ),
    'max_daily_alpaca_articles': StrategyFieldMeta(
      label: 'Max Daily Alpaca Articles',
      description:
          'Upper cap on Alpaca articles fetched per day for live runs and historical learning.',
    ),
    'max_daily_google_news_articles': StrategyFieldMeta(
      label: 'Max Daily Google News Articles',
      description:
          'Upper cap on Google News articles fetched per day for macro and geopolitical coverage.',
    ),
    'num_articles': StrategyFieldMeta(
      label: 'Legacy Alpaca Article Cap',
      description: 'Legacy fallback only. Prefer Max Daily Alpaca Articles instead.',
    ),
    'google_news_max_articles': StrategyFieldMeta(
      label: 'Legacy Google News Article Cap',
      description: 'Legacy fallback only. Prefer Max Daily Google News Articles instead.',
    ),
    'num_articles_for_llm': StrategyFieldMeta(
      label: 'Alpaca Articles Sent To Daily LLM Pass',
      description:
          'How many Alpaca articles are forwarded into the main daily LLM sentiment/classification pass.',
    ),
    'trend_max_age_days': StrategyFieldMeta(
      label: 'Trend Auto-Expire Age (Days)',
      description:
          'Stored trends are ended automatically if they go this many days without confirmation.',
    ),
    'learning_stage_days': StrategyFieldMeta(
      label: 'Legacy Learning Summary Window (Days)',
      description:
          'Only used for the older cached text-summary learning stage, not the newer ML retraining window.',
    ),
    'learning_refresh_hours': StrategyFieldMeta(
      label: 'Legacy Learning Summary Refresh Hours',
      description: 'How often the older cached learning summary may be regenerated.',
    ),
    'lookback_learning_days': StrategyFieldMeta(
      label: 'ML Lookback Window (Days)',
      description:
          'Historical days loaded before a run or backtest start to retrain Nexus ML and build context.',
    ),
    'buy_threshold': StrategyFieldMeta(
      label: 'Buy Score Threshold',
      description:
          'Minimum final Nexus score needed before emitting a buy-oriented signal.',
    ),
    'sell_threshold': StrategyFieldMeta(
      label: 'Sell Score Threshold',
      description: 'Maximum final Nexus score before emitting a sell override signal.',
    ),
    'consecutive_sell_days_to_prune': StrategyFieldMeta(
      label: 'Consecutive Sell Days To Prune',
      description:
          'Number of consecutive sell signal days before a discovered stock is pruned. Higher values allow stocks more time to recover from temporary dips.',
    ),
    'llm_overlay_max_stock_candidates': StrategyFieldMeta(
      label: 'Max Stock Overlay Candidates',
      description:
          'Maximum number of stock candidates sent to the LLM trade overlay per day. Higher values review more stocks but cost more LLM tokens.',
    ),
    'llm_overlay_max_etf_candidates': StrategyFieldMeta(
      label: 'Max ETF Overlay Candidates',
      description:
          'Maximum number of ETF candidates sent to the trend-aware LLM overlay per day. ETFs use a separate prompt focused on trend strength.',
    ),
    'max_trend_etfs': StrategyFieldMeta(
      label: 'Max Trend ETFs',
      description:
          'Maximum total trend ETFs that can be actively tracked. Higher values allow broader diversified ETF exposure across gold, energy, defense, etc.',
    ),
    'max_etf_buys_per_day': StrategyFieldMeta(
      label: 'Max ETF Buys Per Day',
      description:
          'Cap on how many ETFs can be bought in a single day. Prevents low-conviction ETF buys from diluting capital away from higher-conviction stock positions.',
    ),
    'max_discovered_stocks': StrategyFieldMeta(
      label: 'Max Discovered Stocks',
      description:
          'Maximum number of stocks in the active discovered list. When full, new high-signal stocks can evict the weakest existing stock.',
    ),
    'nexus_portfolio_pct': StrategyFieldMeta(
      label: 'Stock Portfolio Allocation',
      description:
          'Maximum fraction of portfolio value allocated to Nexus stock buys (0-1). Top-tier stocks get 2x allocation, mid-tier 1x, low-tier 0.5x.',
    ),
    'etf_portfolio_pct': StrategyFieldMeta(
      label: 'ETF Portfolio Allocation',
      description:
          'Maximum fraction of portfolio value allocated to Nexus ETF buys (0-1). Split equally across active ETF buy signals.',
    ),
    'momentum_discovery_enabled': StrategyFieldMeta(
      label: 'Momentum Discovery',
      description:
          'Discover stocks from pure price momentum (e.g. 20-day return >= 20%). Catches sustained runners that may not appear in news.',
    ),
    'momentum_discovery_min_20d_return': StrategyFieldMeta(
      label: 'Momentum Min 20-Day Return (%)',
      description:
          'Minimum 20-day price return to trigger momentum discovery. Lower values discover more stocks but may include noise.',
    ),
    'momentum_discovery_min_60d_return': StrategyFieldMeta(
      label: 'Momentum Min 60-Day Return (%)',
      description:
          'Minimum 60-day price return to trigger momentum discovery. Catches longer-term sustained runners.',
    ),
    'momentum_discovery_max_per_day': StrategyFieldMeta(
      label: 'Max Momentum Discoveries Per Day',
      description:
          'Maximum number of stocks discovered via momentum per day. Prevents flooding the portfolio with momentum plays.',
    ),
    'sector_price_context_enabled': StrategyFieldMeta(
      label: 'Sector Price Context for LLM',
      description:
          'Feed sector/commodity ETF price moves (gold, oil, semis, etc.) to the LLM so it can detect trends from price action, not just headlines.',
    ),
    'price_trend_detection_enabled': StrategyFieldMeta(
      label: 'Price-Based Trend Detection',
      description:
          'Detect trends from benchmark ETF price action independently of LLM sentiment. Ensures trend→ETF pipeline works even when sentiment is cached.',
    ),
    'price_trend_bull_20d': StrategyFieldMeta(
      label: 'Price Trend 20-Day Threshold (%)',
      description:
          'Minimum 20-day return for a benchmark ETF to trigger a bullish price trend. Lower values detect more trends.',
    ),
    'price_trend_bull_60d': StrategyFieldMeta(
      label: 'Price Trend 60-Day Threshold (%)',
      description:
          'Minimum 60-day return for a benchmark ETF to trigger a bullish price trend. Catches longer-term commodity/sector moves.',
    ),
    'ml_signal_weight': StrategyFieldMeta(
      label: 'ML Signal Weight',
      description:
          'Weight of ML model predictions in final scoring (0-1). Lower values let news/trend signals override ML downside predictions.',
    ),
    'analyst_panel_enabled': StrategyFieldMeta(
      label: 'Analyst Panel Enabled',
      description:
          'Enable the 10-agent adversarial debate framework. Agents independently analyze news, debate each other\'s views, and synthesize a consensus signal each bar.',
    ),
    'analyst_panel_skip_lookback': StrategyFieldMeta(
      label: 'Skip Analyst Panel During Lookback',
      description:
          'Skip the analyst panel during historical lookback bars to avoid hundreds of unnecessary LLM calls before the backtest window starts. Recommended: on.',
    ),
    'analyst_panel_rounds': StrategyFieldMeta(
      label: 'Debate Rounds',
      description:
          '1 = independent analysis only. 2 = + adversarial debate round (agents see each other\'s views). 3 = + moderator synthesis (recommended).',
    ),
    'analyst_panel_debate_style': StrategyFieldMeta(
      label: 'Debate Style',
      description:
          'adversarial = agents actively challenge each other. collaborative = agents build on each other. structured = formal point/counterpoint.',
    ),
    'analyst_panel_moderator_llm_model': StrategyFieldMeta(
      label: 'Analyst Panel Moderator Model (R3)',
      description:
          'Model for the Round 3 moderator synthesis — can be a stronger/more expensive model since it\'s only one call per bar. Uses the same provider/API key as the Analyst Panel LLM.',
    ),
    'analyst_panel_max_workers': StrategyFieldMeta(
      label: 'Analyst Panel Max Parallel Workers',
      description:
          'Max threads for parallel agent LLM calls. Higher values reduce latency but increase concurrent API load.',
    ),
    'analyst_panel_timeout_sec': StrategyFieldMeta(
      label: 'Analyst Panel Agent Timeout (Seconds)',
      description:
          'Per-agent LLM call timeout. Agents that exceed this are skipped; the panel proceeds with remaining results.',
    ),
    'analyst_panel_score_weight': StrategyFieldMeta(
      label: 'Analyst Panel Score Weight',
      description:
          'Multiplier applied to panel consensus score adjustments before adding to stock scores. 0.15 = panel can shift a score by ±0.15.',
    ),
    'analyst_panel_memory_days': StrategyFieldMeta(
      label: 'Analyst Panel Memory Lookback (Days)',
      description:
          'Days of per-agent prediction history loaded for accuracy feedback. Agents see their own past calls and accuracy to calibrate confidence.',
    ),
    'analyst_panel_max_stocks': StrategyFieldMeta(
      label: 'Analyst Panel Max Stocks Per Agent',
      description:
          'Maximum number of stock candidates each agent rates per round. Higher values give more complete coverage but increase prompt size.',
    ),
    'analyst_panel_max_llm_calls': StrategyFieldMeta(
      label: 'Analyst Panel Max LLM Calls Per Bar',
      description:
          'Hard cap on total LLM calls per bar across all rounds (cost control). 10 agents × 3 rounds = 21 calls; set to 25 for a small buffer.',
    ),
    'analyst_panel_cooldown_seconds': StrategyFieldMeta(
      label: 'Analyst Panel Agent Submission Stagger (Seconds)',
      description:
          'Delay between submitting agents to the thread pool. Use to avoid rate-limit bursts. 0 = submit all at once.',
    ),
    'analyst_panel_sector_specialist_sector': StrategyFieldMeta(
      label: 'Analyst Panel Sector Specialist Focus',
      description:
          'The industry sector the Sector Specialist agent focuses on. Default: Technology.',
    ),
    'analyst_panel_cache_enabled': StrategyFieldMeta(
      label: 'Analyst Panel LLM Prompt Cache',
      description:
          'Cache analyst panel LLM responses (all 3 rounds) in RethinkDB. Same prompt + model + effort returns the cached response instead of calling the LLM — can cut panel latency dramatically on re-runs or within repetitive lookback windows. Works independently of nexus_fast_mode. Default: off.',
    ),
    'nexus_fast_mode': StrategyFieldMeta(
      label: 'Nexus Fast Mode (Global LLM Cache)',
      description:
          'Enable LLM prompt-hash caching for ALL Nexus LLM calls (lookback + live). Caches responses by (prompt + model + effort) in RethinkDB. WARNING: can contaminate live-bar decisions with stale responses if LLM context changes. Use for fast iteration, not production runs. Default: off.',
    ),
    'nexus_lookback_cache_enabled': StrategyFieldMeta(
      label: 'Lookback LLM Cache',
      description:
          'Enable LLM prompt caching ONLY during the 85-bar historic lookback warmup. Live bars still call the LLM fresh. Much safer than nexus_fast_mode because stale cache hits cannot affect live trading decisions. Default: on.',
    ),
    'nexus_high_conviction_threshold': StrategyFieldMeta(
      label: 'High-Conviction Score Threshold',
      description:
          'Raw score threshold above which a blocked-buy candidate is treated as high-conviction. High-conviction items get the top_momentum_break_glass rotation bypass (delta>=0.75 instead of 1.50), shorter min-hold thresholds, and a larger backfill reserve. Default: 1.5.',
    ),
    'peak_protection_enabled': StrategyFieldMeta(
      label: 'Peak Protection Enabled',
      description:
          'When ON, positions that previously hit a high P&L peak are protected from fast-loser-cut on first pullback, deferring to the trailing stop instead. Prevents the AGMI-style disaster where +75% peak gets cut at -9%. Default: on.',
    ),
    'peak_protection_min_peak_pnl_pct': StrategyFieldMeta(
      label: 'Peak Protection Min Peak P&L (%)',
      description:
          'Minimum peak unrealized P&L (vs entry) for a position to qualify for peak protection. Below this, fast-loser-cut still fires. Default: 30.0.',
    ),
    'peak_protection_max_drawdown_from_peak_pct': StrategyFieldMeta(
      label: 'Peak Protection Max Drawdown From Peak (%)',
      description:
          'Maximum allowed drawdown from peak PRICE (not peak P&L) for peak protection to remain active. Drops beyond this re-enable fast-loser-cut. Default: 60.0.',
    ),
    'high_conviction_loser_min_hold_days': StrategyFieldMeta(
      label: 'High-Conviction Loser Rotation Min Hold (Days)',
      description:
          'Minimum hold-days before a losing position can be rotated out to fund a high-conviction blocked-buy. Lower than the standard rotation_min_hold_days (10) so SNDK/MU/LITE-style names can land sooner. Default: 5.',
    ),
    'high_conviction_profitable_min_hold_days': StrategyFieldMeta(
      label: 'High-Conviction Profitable Rotation Min Hold (Days)',
      description:
          'Minimum hold-days before a profitable winner without an active leader_lock can be rotated out to fund a high-conviction blocked-buy. Lower than standard 20. Default: 10.',
    ),
    'backfill_budget_reserve_pct_high_conviction': StrategyFieldMeta(
      label: 'Backfill Reserve % (High-Conviction)',
      description:
          'Backfill budget reserve fraction when the queue contains a high-conviction item. Bumped from the standard 0.20 to give SNDK/MU/LITE-style names actual cash to execute against. Self-disables when no high-conviction item is queued. Default: 0.35 (35%).',
    ),
    'nexus_discovery_bootstrap_enabled': StrategyFieldMeta(
      label: 'Discovery Snapshot Bootstrap',
      description:
          'When ON, on the first bar of a fresh scope, import the most recent prior scope\'s discovered tickers from the GraphNexusDiscoverySnapshots table. Default: on.',
    ),
    'nexus_discovery_snapshot_enabled': StrategyFieldMeta(
      label: 'Discovery Snapshot Writer',
      description:
          'When ON, write the current scope\'s discoveries to the GraphNexusDiscoverySnapshots table on every lookback bar. Default: on.',
    ),
    'nexus_discovery_snapshot_max_age_days': StrategyFieldMeta(
      label: 'Discovery Snapshot Max Age (Days)',
      description:
          'Maximum age of a snapshot (vs current run\'s start date) before bootstrap refuses to use it. Default: 90.',
    ),
    'nexus_discovery_bootstrap_min_keep_pct': StrategyFieldMeta(
      label: 'Discovery Bootstrap Min Keep %',
      description:
          'Minimum fraction of snapshot tickers that must survive the per-bucket config-fingerprint filter for bootstrap to proceed. Default: 0.30 (30%).',
    ),
    'nexus_discovery_bootstrap_merge_mode': StrategyFieldMeta(
      label: 'Discovery Bootstrap Merge Mode',
      description:
          'When ON, bootstrap also fires on a non-empty current scope to fill in MISSING tickers from the snapshot. Default: off.',
    ),
    'max_rotations_per_day_high_conviction': StrategyFieldMeta(
      label: 'Max Rotations Per Day (High-Conviction Bonus)',
      description:
          'Additional rotation slots reserved for high-conviction blocked-buys. Additive on top of max_rotations_per_day. Default: 4.',
    ),
    'queue_rotation_promotion_enabled': StrategyFieldMeta(
      label: 'Queue->Rotation Promotion',
      description:
          'When ON, aged backfill-queue items are promoted into the rotation candidate list so they can compete for rotation slots. Default: on.',
    ),
    'queue_rotation_promotion_min_bars': StrategyFieldMeta(
      label: 'Queue->Rotation Promotion Min Bars',
      description:
          'Minimum bars a queued item must wait before being promoted into the rotation candidate list. Default: 1.',
    ),
    'queue_rotation_promotion_max_items': StrategyFieldMeta(
      label: 'Queue->Rotation Promotion Max Items',
      description:
          'Maximum number of queued items promoted to _blocked_buys per bar. Default: 20.',
    ),
    'blocked_buys_age_boost_per_bar': StrategyFieldMeta(
      label: 'Blocked Buys Age Boost Per Bar',
      description:
          'Effective score boost added to a queued blocked-buy candidate per bar of queue age. Default: 0.05.',
    ),
    'blocked_buys_age_boost_cap': StrategyFieldMeta(
      label: 'Blocked Buys Age Boost Cap',
      description:
          'Maximum total age boost. Prevents stale items from dominating forever. Default: 0.40.',
    ),
    'v28_hold_trim_cooldown_bars': StrategyFieldMeta(
      label: 'Per-Hold Trim Cooldown (Bars)',
      description:
          'Number of bars a held position is excluded from rotation after being partial-trimmed by a break_glass rotation. Default: 3.',
    ),
    'v28_hc_profitable_min_delta': StrategyFieldMeta(
      label: 'V28.2 HC Profitable Hold Min Delta',
      description:
          'Minimum rotation delta required for a high-conviction inflow to rotate against a profitable hold WITHOUT winner_lock active. Default: 1.0.',
    ),
    'v28_hc_losing_min_delta': StrategyFieldMeta(
      label: 'V28.2 HC Losing Hold Min Delta',
      description:
          'Minimum rotation delta required for a high-conviction inflow to rotate against a LOSING hold. Default: 0.75.',
    ),
    'v28_hc_profitable_max_held_pnl': StrategyFieldMeta(
      label: 'V28.4 HC Profitable Rotation Max Held P&L (%)',
      description:
          'Maximum unrealized P&L (%) of a held position for v28_hc_profitable_break_glass to fire. Default: 2.0.',
    ),
    'v28_hc_losing_max_held_pnl': StrategyFieldMeta(
      label: 'V28.5 HC Losing Rotation Max Held P&L (%)',
      description:
          'Maximum unrealized P&L (%) of a held LOSING position for v28_hc_losing_break_glass to fire. Default: -1.5.',
    ),
  },
};

/// Returns field metadata for [key] in [strategyName], falling back to
/// [humanizeStrategyConfigKey] if the key is not explicitly catalogued.
StrategyFieldMeta getStrategyConfigFieldMeta(String strategyName, String key) {
  final meta = strategyFieldMeta[strategyName.trim()] ?? const {};
  return meta[key] ??
      StrategyFieldMeta(label: humanizeStrategyConfigKey(key), description: '');
}

// ── LLM option lists ──────────────────────────────────────────────────────────

class SelectOption {
  const SelectOption({required this.value, required this.label});
  final String value;
  final String label;
}

const llmProviderOptions = [
  SelectOption(value: 'gemini', label: 'Google Gemini'),
  SelectOption(value: 'deepseek', label: 'DeepSeek'),
  SelectOption(value: 'openai', label: 'OpenAI Compatible'),
  SelectOption(value: 'azure', label: 'Azure OpenAI'),
  SelectOption(value: 'nvidia', label: 'NVIDIA NIM'),
  SelectOption(value: 'ollama', label: 'Ollama (local / cloud)'),
  SelectOption(value: 'bedrock', label: 'AWS Bedrock'),
  SelectOption(value: 'openrouter', label: 'OpenRouter'),
  SelectOption(value: 'claude-cli', label: 'Claude Code CLI (subscription)'),
  SelectOption(value: 'codex-cli', label: 'OpenAI Codex CLI (subscription)'),
];

const llmReasoningEffortOptions = [
  SelectOption(value: '', label: 'Default'),
  SelectOption(value: 'low', label: 'Low'),
  SelectOption(value: 'medium', label: 'Medium'),
  SelectOption(value: 'high', label: 'High'),
];

const nvidiaReasoningEffortOptions = [
  SelectOption(value: '', label: 'None (thinking disabled)'),
  SelectOption(value: 'low', label: 'Low'),
  SelectOption(value: 'medium', label: 'Medium'),
  SelectOption(value: 'high', label: 'High (full reasoning)'),
];

const ollamaThinkOptions = [
  SelectOption(value: '', label: 'Default (model decides)'),
  SelectOption(value: 'false', label: 'Off (disable thinking)'),
  SelectOption(value: 'true', label: 'On (enable thinking)'),
  SelectOption(value: 'low', label: 'Low effort'),
  SelectOption(value: 'medium', label: 'Medium effort'),
  SelectOption(value: 'high', label: 'High effort'),
];

const bedrockReasoningOptions = [
  SelectOption(value: 'off', label: 'Off'),
  SelectOption(value: 'low', label: 'Low'),
  SelectOption(value: 'medium', label: 'Medium'),
  SelectOption(value: 'high', label: 'High'),
];

const claudeCliEffortOptions = [
  SelectOption(value: '', label: 'Default'),
  SelectOption(value: 'low', label: 'Low'),
  SelectOption(value: 'medium', label: 'Medium'),
  SelectOption(value: 'high', label: 'High'),
  SelectOption(value: 'xhigh', label: 'Extra High'),
  SelectOption(value: 'max', label: 'Max'),
];

/// Human-readable labels for known LLM role prefixes.
const knownLlmRoleLabels = <String, String>{
  '': 'Default LLM',
  'sentiment_': 'Daily Sentiment LLM',
  'company_article_': 'Company Article LLM',
  'macro_article_': 'Macro Article LLM',
  'event_maintenance_': 'Event Maintenance LLM',
  'overlay_': 'Trade Overlay LLM',
  'analyst_panel_': 'Analyst Panel LLM (R1+R2 Debate)',
};

const knownLookbackLlmRoleLabels = <String, String>{
  'lookback_': 'Default LLM (Lookback)',
  'lookback_sentiment_': 'Daily Sentiment LLM (Lookback)',
  'lookback_company_article_': 'Company Article LLM (Lookback)',
  'lookback_macro_article_': 'Macro Article LLM (Lookback)',
  'lookback_event_maintenance_': 'Event Maintenance LLM (Lookback)',
  'lookback_overlay_': 'Trade Overlay LLM (Lookback)',
};
