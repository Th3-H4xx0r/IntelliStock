import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  applyStrategyLlmDraft,
  getStrategyConfigFieldMeta,
  getStrategyLlmConfigGroups,
  isLlmManagedConfigField,
} from '../src/utils/strategyConfig.js'

// Every header key from spec section 5.1 / 5.2 that renders as a plain field.
// conviction_llm_model_id is deliberately absent: it renders as the model card.
const SWING_KEYS = [
  'strategy_swing_enabled', 'rsi_period', 'rsi_entry_max', 'rsi_overbought',
  'sma_long', 'macd_fast', 'macd_slow', 'macd_signal', 'vol_avg_period',
  'adx_period', 'adx_min', 'spy_buffer', 'vix_max', 'position_size_pct',
  'max_positions', 'max_per_sector', 'profit_target', 'stop_loss',
  'bear_regime_days', 'defensive_universe', 'earnings_hard_block_days',
  'ai_gate_enabled', 'ai_approve_threshold', 'ai_review_threshold', 'scan_time_et',
  'live_max_order_fraction', 'live_max_symbol_fraction', 'live_max_leveraged_fraction',
  'live_soft_drawdown', 'live_hard_drawdown', 'live_kill_drawdown',
  'honour_single_position_cap', 'broker_max_single_position_pct',
]
const WHEEL_KEYS = [
  'strategy_wheel_enabled', 'rsi_min', 'rsi_max', 'sma_trend', 'atr_period',
  'strike_atr_mult', 'min_premium_pct', 'target_delta', 'days_to_expiry',
  'max_collateral_pct', 'max_per_sector', 'auto_covered_call',
  'approve_threshold', 'review_threshold', 'earnings_block_days',
  'limit_bid_mult', 'scan_weekday', 'scan_time_et', 'monitor_time_et',
]
const CONVICTION_KEYS = [
  'conviction_llm_model_id', 'conviction_llm_provider',
  'conviction_llm_model', 'conviction_llm_api_key',
]

for (const [strategy, keys] of [['strategy_swing', SWING_KEYS], ['strategy_wheel', WHEEL_KEYS]]) {
  test(`${strategy}: every header key has a label and a one-line description`, () => {
    for (const key of keys) {
      const meta = getStrategyConfigFieldMeta(strategy, key)
      assert.ok(meta.label.trim(), `${key}: empty label`)
      assert.ok(meta.description.trim(), `${key}: no description`)
      assert.ok(!meta.description.includes('\n'), `${key}: description spans lines`)
    }
  })

  test(`${strategy}: the conviction model is a model card even before its key exists`, () => {
    const groups = getStrategyLlmConfigGroups(strategy, {}, {})
    assert.deepEqual(groups.map(g => g.prefix), ['conviction_'])
    assert.equal(groups[0].label, 'Conviction LLM')
    assert.equal(groups[0].modelIdKey, 'conviction_llm_model_id')
    assert.equal(groups[0].providerKey, 'conviction_llm_provider')
  })

  test(`${strategy}: conviction keys stay out of the plain field list`, () => {
    const cfg = { conviction_llm_model_id: '', rsi_period: 14 }
    for (const key of CONVICTION_KEYS) {
      assert.equal(isLlmManagedConfigField(strategy, key, cfg, cfg), true, key)
    }
    assert.equal(isLlmManagedConfigField(strategy, 'rsi_period', cfg, cfg), false)
  })
}

test('the two lanes describe scan_time_et differently', () => {
  assert.notEqual(
    getStrategyConfigFieldMeta('strategy_swing', 'scan_time_et').description,
    getStrategyConfigFieldMeta('strategy_wheel', 'scan_time_et').description,
  )
})

test('linking a saved model stores the reference and drops inline credentials', () => {
  const [group] = getStrategyLlmConfigGroups('strategy_swing', {}, {})
  const next = applyStrategyLlmDraft(
    { rsi_period: 14, conviction_llm_api_key: 'sk-stale', conviction_llm_model: 'old' },
    group,
    { modelId: 'model-123', provider: 'Anthropic', model: 'claude-x', apiKey: 'never-stored' },
  )
  assert.deepEqual(next, {
    rsi_period: 14,
    conviction_llm_model_id: 'model-123',
    conviction_llm_provider: 'anthropic',
    conviction_llm_model: 'claude-x',
  })
})

test('graph_nexus_analysis role groups are unchanged', () => {
  const groups = getStrategyLlmConfigGroups('graph_nexus_analysis', {}, {})
  assert.deepEqual(groups.map(g => g.prefix), [
    '', 'analyst_panel_', 'company_article_', 'sentiment_',
    'event_maintenance_', 'macro_article_', 'overlay_',
  ])
})
