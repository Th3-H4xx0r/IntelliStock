<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { getToken } from '../../utils/auth.js'
import CryptoAllocationChart from './CryptoAllocationChart.vue'

// Create + edit a crypto instance. Mirrors KalshiCreateInstanceModal's shell +
// editInstance-prop pattern. The allocation table + live donut replicate the
// approved mockup (docs/superpowers/specs/crypto_alloc_mockup.html): pin fixed
// per-coin weights, leave the rest Dynamic (auto-discovered). Empty = 100% dynamic.
const props = defineProps({
  editInstance: { type: Object, default: null }, // { id, name, brokerage_id, strategy_id, crypto_config, stocks }
  initialBrokerageId: { type: String, default: '' },
})
const emit = defineEmits(['close', 'created', 'saved'])

const isEdit = computed(() => !!props.editInstance)

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')
function authHeaders() {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

// Violet-metallic palette (matches the mockup) assigned to coins as they're added.
const PALETTE = ['#a78bfa', '#7c9bff', '#5ad1e0', '#5ee6b8', '#f0b354', '#f3799f', '#b98bff']

// Alpaca crypto pairs. `sym` is the API/Alpaca slash-pair (goes into stocks +
// crypto_config.allocations); `ticker`/`name` are display-only.
const CATALOG = [
  { sym: 'BTC/USD', ticker: 'BTC', name: 'Bitcoin' },
  { sym: 'ETH/USD', ticker: 'ETH', name: 'Ethereum' },
  { sym: 'SOL/USD', ticker: 'SOL', name: 'Solana' },
  { sym: 'LINK/USD', ticker: 'LINK', name: 'Chainlink' },
  { sym: 'AVAX/USD', ticker: 'AVAX', name: 'Avalanche' },
  { sym: 'DOT/USD', ticker: 'DOT', name: 'Polkadot' },
  { sym: 'LTC/USD', ticker: 'LTC', name: 'Litecoin' },
  { sym: 'UNI/USD', ticker: 'UNI', name: 'Uniswap' },
  { sym: 'AAVE/USD', ticker: 'AAVE', name: 'Aave' },
  { sym: 'DOGE/USD', ticker: 'DOGE', name: 'Dogecoin' },
  { sym: 'BCH/USD', ticker: 'BCH', name: 'Bitcoin Cash' },
  { sym: 'MKR/USD', ticker: 'MKR', name: 'Maker' },
  { sym: 'XTZ/USD', ticker: 'XTZ', name: 'Tezos' },
  { sym: 'SHIB/USD', ticker: 'SHIB', name: 'Shiba Inu' },
]
function catalogFor(sym) {
  const s = String(sym || '').toUpperCase()
  return CATALOG.find((c) => c.sym === s) || { sym: s, ticker: s.split('/')[0], name: s }
}

const BANDS = [
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
]
const BAND_BLURB = {
  high: 'High — checks every ~5 min. Most reactive to fast moves; more trades and turnover.',
  medium: 'Medium — checks every ~15 min. Balanced reactivity vs. turnover. The default.',
  low: 'Low — checks every ~60 min. Calmest; fewest trades, lowest fees, slower to react.',
}

// What each dynamic strategy does with the auto-discovered (Dynamic) portion.
const STRATEGY_BLURB = {
  meanrev: 'Mean-Reversion (RSI) — buys oversold majors (RSI-14 below 35) only while they hold above their 200-hour trend MA ("healthy dips, not falling knives"), holds 2, and sells when RSI recovers past 55. Sits in cash ~75% of the time. Top backtest performer: +24% over 400 days at Binance.US fees while BTC fell −42%, ~8% drawdown. Needs a low-fee venue — loses money at Alpaca 0.25%.',
  connors: 'Connors (fast RSI) — the quicker cousin of Mean-Reversion: buys deeply oversold dips (short RSI) above the trend MA and exits fast once price snaps back above a short MA. Higher turnover, quicker in/out. Backtest: modest but robust +6% over 400 days. Also needs a low-fee venue.',
  momentum: 'Momentum — trend-follows the auto-discovered universe, leaning into coins whose momentum is strengthening. Higher conviction in movers. Note: trend strategies were whipsawed in choppy/down backtests — best in a sustained bull run.',
  allocator: 'Allocator — risk-weights across coins toward balanced target weights. Diversified, steadier exposure; a slower rebalancer.',
  fast: 'Fast — tactical Donchian breakout trend-follower; quick in/out on short-term signals. Most responsive, highest turnover.',
  reference: 'Reference — a simple buy-and-rebalance baseline to benchmark the others against.',
}

// Recommended band per strategy (the cadence each was designed / validated at).
// Mean-reversion strategies were validated on 60-min (Low) bars.
const STRATEGY_RECOMMENDED_BAND = {
  meanrev: 'low',
  connors: 'low',
  momentum: 'medium',
  allocator: 'low',
  fast: 'high',
  reference: 'low',
}

// The crypto strategy modules live in strategies/crypto/. We surface them from
// /strategies/available (filtered), falling back to a hardcoded list.
const CRYPTO_STRATEGY_IDS = ['meanrev', 'connors', 'momentum', 'allocator', 'fast', 'reference']
const FALLBACK_STRATEGIES = [
  { id: 'meanrev', name: 'Mean-Reversion' },
  { id: 'connors', name: 'Connors (fast RSI)' },
  { id: 'momentum', name: 'Momentum' },
  { id: 'allocator', name: 'Allocator' },
  { id: 'fast', name: 'Fast' },
  { id: 'reference', name: 'Reference' },
]

// ── Form state ────────────────────────────────────────────────────────────────
const instanceId = ref('')
const name = ref('')
const brokerageId = ref('')
const band = ref('medium')
const strategyId = ref('momentum')
const mode = ref('pct') // 'pct' | 'usd'
const coins = ref([]) // [{ sym, ticker, name, pct(0-100), color }]

const brokerages = ref([])
const strategies = ref(FALLBACK_STRATEGIES)

const bandBlurb = computed(() => BAND_BLURB[band.value] || '')
const strategyBlurb = computed(() => STRATEGY_BLURB[String(strategyId.value || '').toLowerCase()] || '')

// Per-strategy recommended band. Auto-applied when the USER picks a strategy
// (via @change so it never clobbers the edit-mode prefill), and surfaced as a
// hint + one-tap "Use" when the current band differs.
const recommendedBand = computed(() => STRATEGY_RECOMMENDED_BAND[String(strategyId.value || '').toLowerCase()] || null)
const recommendedBandLabel = computed(() => (BANDS.find((b) => b.value === recommendedBand.value) || {}).label || '')
const bandMatchesRecommended = computed(() => !recommendedBand.value || band.value === recommendedBand.value)
function onStrategyChange() { if (recommendedBand.value) band.value = recommendedBand.value }
function applyRecommendedBand() { if (recommendedBand.value) band.value = recommendedBand.value }

// Account equity (best-effort from portfolio-history) with a manual fallback.
const equity = ref(0)
const loadingEquity = ref(false)
const manualEquity = ref(0)
const hasLiveEquity = computed(() => equity.value > 0)
const effectiveEquity = computed(() => (hasLiveEquity.value ? equity.value : Number(manualEquity.value) || 0))

const creating = ref(false)
const err = ref('')

// ── Allocation math ───────────────────────────────────────────────────────────
const fixedSum = computed(() => coins.value.reduce((s, c) => s + (Number(c.pct) || 0), 0))
const dynamicPct = computed(() => Math.max(0, 100 - fixedSum.value))
const over = computed(() => fixedSum.value > 100 + 1e-9)

const allocationsForChart = computed(() =>
  coins.value.filter((c) => Number(c.pct) > 0).map((c) => ({ symbol: c.ticker, pct: Number(c.pct), color: c.color })),
)

const availableCatalog = computed(() => {
  const have = new Set(coins.value.map((c) => c.sym))
  return CATALOG.filter((c) => !have.has(c.sym))
})
const addPick = ref('')

// ── Formatting + conversions ──────────────────────────────────────────────────
function fmtUsd(n) { return `$${Math.round(Number(n) || 0).toLocaleString()}` }
function usdFor(pct) { return (Number(pct) || 0) / 100 * effectiveEquity.value }

function setPct(i, val) {
  const v = Math.max(0, Math.min(100, Number(val) || 0))
  coins.value[i].pct = v
}
function setUsd(i, val) {
  if (effectiveEquity.value <= 0) return
  const pct = ((Number(val) || 0) / effectiveEquity.value) * 100
  coins.value[i].pct = Math.max(0, Math.min(100, pct))
}

function addCoin() {
  const sym = addPick.value
  if (!sym) return
  const c = catalogFor(sym)
  coins.value.push({ ...c, pct: 0, color: PALETTE[coins.value.length % PALETTE.length] })
  addPick.value = ''
}
function removeCoin(i) { coins.value.splice(i, 1) }

// ── Data loads ────────────────────────────────────────────────────────────────
async function loadBrokerages() {
  try {
    const res = await fetch(`${API_BASE}/brokerages`, { headers: authHeaders() })
    if (!res.ok) return
    const d = await res.json()
    // Crypto trades through Alpaca.
    brokerages.value = (d.accounts || []).filter((a) => ['alpaca', 'binanceus'].includes(a.brokerage_type))
    if (!brokerageId.value && brokerages.value.length) brokerageId.value = brokerages.value[0].id
  } catch { /* non-critical */ }
}

async function loadStrategies() {
  try {
    const res = await fetch(`${API_BASE}/strategies/available`, { headers: authHeaders() })
    if (!res.ok) return
    const d = await res.json()
    const all = d.strategies || []
    const crypto = all
      .filter((s) => {
        const id = String(s?.id || '').toLowerCase()
        const nm = String(s?.name || '').toLowerCase()
        const canon = String(s?.schema?.strategy || '').toLowerCase()
        return CRYPTO_STRATEGY_IDS.includes(id) || CRYPTO_STRATEGY_IDS.includes(nm) || CRYPTO_STRATEGY_IDS.includes(canon)
      })
      .map((s) => ({ id: s?.schema?.strategy || s?.id || s?.name, name: s?.name || s?.schema?.strategy || s?.id }))
    if (crypto.length) {
      strategies.value = crypto
      if (!strategies.value.some((s) => String(s.id) === String(strategyId.value))) {
        strategyId.value = strategies.value[0].id
      }
    }
  } catch { /* fall back to hardcoded list */ }
}

async function loadEquity() {
  equity.value = 0
  if (!brokerageId.value) return
  loadingEquity.value = true
  try {
    const res = await fetch(`${API_BASE}/brokerages/${brokerageId.value}/portfolio-history?range=1D`, { headers: authHeaders() })
    if (res.ok) {
      const d = await res.json()
      const v = Number(d?.current_value)
      equity.value = Number.isFinite(v) && v > 0 ? v : 0
    }
  } catch { equity.value = 0 } finally { loadingEquity.value = false }
}

watch(brokerageId, loadEquity)

function prefillFromEdit() {
  const inst = props.editInstance || {}
  name.value = inst.name || ''
  brokerageId.value = inst.brokerage_id || ''
  if (inst.strategy_id != null) strategyId.value = inst.strategy_id
  const cc = inst.crypto_config || {}
  if (cc.strategy) strategyId.value = String(cc.strategy).toLowerCase()
  if (cc.band) band.value = String(cc.band).toLowerCase()
  const allocs = Array.isArray(cc.allocations) ? cc.allocations : []
  coins.value = allocs.map((a, idx) => {
    const c = catalogFor(a.symbol)
    // Stored allocations use a fraction (0-1); the UI works in percent.
    return { ...c, pct: Math.round((Number(a.pct) || 0) * 1000) / 10, color: PALETTE[idx % PALETTE.length] }
  })
}

onMounted(() => {
  brokerageId.value = props.initialBrokerageId || ''
  loadBrokerages()
  loadStrategies()
  if (isEdit.value) prefillFromEdit()
  loadEquity()
})

// ── Submit ────────────────────────────────────────────────────────────────────
function buildAllocations() {
  // Only fixed coins with a positive weight; pct as a fraction 0-1.
  return coins.value
    .filter((c) => Number(c.pct) > 0)
    .map((c) => ({ symbol: c.sym, pct: Math.round((Number(c.pct) / 100) * 1e6) / 1e6 }))
}
function buildStocks() {
  return coins.value.filter((c) => Number(c.pct) > 0).map((c) => c.sym)
}

async function submit() {
  if (creating.value) return
  err.value = ''
  if (over.value) { err.value = `Over-allocated — reduce fixed weights by ${Math.round(fixedSum.value - 100)}%`; return }
  if (!isEdit.value && !instanceId.value.trim()) { err.value = 'Instance ID is required'; return }
  if (!brokerageId.value) { err.value = 'Select a brokerage'; return }

  creating.value = true
  try {
    const cryptoConfig = {
      band: band.value,
      // The chosen dynamic strategy travels in crypto_config; the broker
      // synthesizes its run_once spec from this (no Strategies row needed).
      strategy: String(strategyId.value || 'momentum').toLowerCase(),
      allocations: buildAllocations(),
    }
    const stocks = buildStocks()

    if (isEdit.value) {
      const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(props.editInstance.id)}`, {
        method: 'PATCH', headers: authHeaders(),
        body: JSON.stringify({ crypto_config: cryptoConfig, stocks }),
      })
      if (!res.ok) { const d = await res.json().catch(() => ({})); err.value = d.detail || `Save failed (HTTP ${res.status})`; return }
      emit('saved')
    } else {
      const payload = {
        id: instanceId.value.trim(),
        name: name.value.trim() || undefined,
        granularity: '900',
        run_command: false,
        kind: 'crypto',
        brokerage_id: brokerageId.value,
        stocks,
        crypto_config: cryptoConfig,
      }
      // strategy_id: the backend model types this Optional[int] (a Strategies-row
      // id). /strategies/available exposes crypto strategies by *string* module id
      // ("momentum"…), so we only attach strategy_id when it is a real integer id —
      // sending a non-numeric id would 422 the whole create. A module-id selection
      // is therefore not transmitted (crypto_config stays exactly {band, allocations}
      // per spec); wiring the four crypto modules to selectable integer strategy
      // rows is the remaining backend step.
      if (strategyId.value !== '' && strategyId.value != null) {
        const n = Number(strategyId.value)
        if (Number.isInteger(n) && String(n) === String(strategyId.value)) {
          payload.strategy_id = n
        }
      }
      const res = await fetch(`${API_BASE}/instances`, {
        method: 'POST', headers: authHeaders(), body: JSON.stringify(payload),
      })
      if (!res.ok) { const d = await res.json().catch(() => ({})); err.value = d.detail || `Create failed (HTTP ${res.status})`; return }
      emit('created', brokerageId.value)
    }
  } catch (e) { err.value = String(e.message || e) } finally { creating.value = false }
}
</script>

<template>
  <div class="fixed inset-0 z-[60] flex items-center justify-center p-4">
    <div @click="emit('close')" class="absolute inset-0 bg-black/60 backdrop-blur-sm"></div>
    <div class="relative glass-card rounded-2xl w-full max-w-3xl max-h-[92vh] overflow-y-auto">
      <!-- Header -->
      <div class="px-6 pt-5 pb-3 border-b border-border-subtle flex items-center justify-between sticky top-0 bg-surface/80 backdrop-blur z-10">
        <h3 class="text-base font-bold text-slate-100 flex items-center gap-2">
          <span class="material-symbols-outlined text-primary text-[20px]">currency_bitcoin</span>
          {{ isEdit ? 'Edit crypto instance' : 'New crypto instance' }}
        </h3>
        <button @click="emit('close')" class="text-slate-500 hover:text-slate-300"><span class="material-symbols-outlined">close</span></button>
      </div>

      <div class="px-6 py-5 space-y-5">
        <p class="text-[13.5px] text-slate-400 leading-relaxed max-w-2xl">
          Pin fixed weights for the coins you want, and leave the rest
          <b class="text-primary">Dynamic</b> — auto-discovered and traded for you. Empty means 100% dynamic.
        </p>

        <!-- Base fields -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div v-if="!isEdit">
            <label class="block text-xs font-medium text-slate-400 mb-1.5">Instance ID</label>
            <input v-model="instanceId" type="text" placeholder="e.g. crypto-main"
                   class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary" />
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-400 mb-1.5">Name</label>
            <input v-model="name" type="text" placeholder="e.g. Crypto — core allocation"
                   class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary" />
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-400 mb-1.5">Brokerage</label>
            <div class="relative">
              <select v-model="brokerageId" class="w-full appearance-none bg-surface border border-border-subtle rounded-lg px-3 pr-9 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary">
                <option v-if="!brokerages.length" value="">No Alpaca account linked</option>
                <option v-for="b in brokerages" :key="b.id" :value="b.id">
                  {{ b.account_name }}<span v-if="b.alpaca_paper"> · Paper</span>
                </option>
              </select>
              <span class="material-symbols-outlined text-slate-500 text-[18px] absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none">expand_more</span>
            </div>
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-400 mb-1.5">Band <span class="text-slate-600 font-normal">· monitor cadence</span></label>
            <div class="relative">
              <select v-model="band" class="w-full appearance-none bg-surface border border-border-subtle rounded-lg px-3 pr-9 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary">
                <option v-for="b in BANDS" :key="b.value" :value="b.value">{{ b.label }}</option>
              </select>
              <span class="material-symbols-outlined text-slate-500 text-[18px] absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none">expand_more</span>
            </div>
            <p class="text-[11px] text-slate-500 mt-1.5 leading-snug">{{ bandBlurb }}</p>
            <p v-if="!bandMatchesRecommended" class="text-[11px] text-primary mt-1 leading-snug">
              Recommended <b>{{ recommendedBandLabel }}</b> for this strategy.
              <button type="button" @click="applyRecommendedBand" class="underline hover:no-underline ml-0.5">Use {{ recommendedBandLabel }}</button>
            </p>
          </div>
          <div class="sm:col-span-2">
            <label class="block text-xs font-medium text-slate-400 mb-1.5">Dynamic strategy <span class="text-slate-600 font-normal">· how the Dynamic portion trades</span></label>
            <div class="relative">
              <select v-model="strategyId" @change="onStrategyChange"
                      class="w-full appearance-none bg-surface border border-border-subtle rounded-lg px-3 pr-9 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary">
                <option v-for="s in strategies" :key="s.id" :value="s.id">{{ s.name }}</option>
              </select>
              <span class="material-symbols-outlined text-slate-500 text-[18px] absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none">expand_more</span>
            </div>
            <p class="text-[11px] text-slate-500 mt-1.5 leading-snug">{{ strategyBlurb }}</p>
          </div>
        </div>

        <!-- Unit toggle + equity -->
        <div class="flex items-center gap-3 flex-wrap">
          <div class="inline-flex bg-surface border border-border-subtle rounded-lg p-0.5">
            <button type="button" @click="mode = 'pct'"
                    class="px-3 py-1.5 rounded-md text-xs font-semibold transition-colors"
                    :class="mode === 'pct' ? 'bg-primary text-background-dark' : 'text-slate-400 hover:text-slate-200'">%</button>
            <button type="button" @click="mode = 'usd'"
                    class="px-3 py-1.5 rounded-md text-xs font-semibold transition-colors"
                    :class="mode === 'usd' ? 'bg-primary text-background-dark' : 'text-slate-400 hover:text-slate-200'">$</button>
          </div>
          <span class="text-xs text-slate-500">
            Account equity
            <span v-if="loadingEquity" class="text-slate-400">…</span>
            <b v-else-if="hasLiveEquity" class="text-slate-300 tabular-nums">{{ fmtUsd(equity) }}</b>
            <span v-else class="inline-flex items-center gap-1">
              <input v-model.number="manualEquity" type="number" min="0" step="100" placeholder="enter equity"
                     class="w-28 bg-surface border border-border-subtle rounded-md px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-primary" />
              <span class="text-amber-400/70">manual</span>
            </span>
          </span>
        </div>

        <!-- Donut + allocation table -->
        <div class="grid grid-cols-1 md:grid-cols-[300px_1fr] gap-4 items-stretch">
          <!-- Donut -->
          <div class="glass-card rounded-2xl p-4 flex items-center justify-center">
            <CryptoAllocationChart :allocations="allocationsForChart" :dynamic-pct="dynamicPct" />
          </div>

          <!-- Table -->
          <div class="glass-card rounded-2xl p-4 flex flex-col">
            <!-- head -->
            <div class="grid grid-cols-[22px_1fr_84px_92px_26px] gap-2 items-center px-1 pb-2 text-[10.5px] uppercase tracking-wider text-slate-600">
              <span></span><span>Coin</span><span class="text-right">Weight</span><span class="text-right">≈ USD</span><span></span>
            </div>

            <!-- coin rows -->
            <div
              v-for="(c, i) in coins" :key="c.sym"
              class="grid grid-cols-[22px_1fr_84px_92px_26px] gap-2 items-center px-1 py-2 border-t border-border-subtle/70"
            >
              <span class="w-2.5 h-2.5 rounded-[3px] ring-1 ring-white/5" :style="{ background: c.color }"></span>
              <span class="min-w-0">
                <span class="text-sm font-semibold text-slate-100">{{ c.ticker }}</span>
                <span class="block text-[11px] text-slate-500 truncate">{{ c.name }}</span>
              </span>
              <input
                :value="Math.round((Number(c.pct) || 0) * 10) / 10"
                @input="setPct(i, $event.target.value)"
                inputmode="decimal"
                class="w-full bg-surface border border-border-subtle rounded-lg px-2 py-1.5 text-[13.5px] text-right text-slate-100 focus:outline-none focus:border-primary"
                :class="mode === 'usd' ? 'opacity-60' : ''"
              />
              <input
                :value="effectiveEquity > 0 ? Math.round(usdFor(c.pct)) : ''"
                @input="setUsd(i, $event.target.value)"
                :disabled="effectiveEquity <= 0"
                inputmode="decimal"
                :placeholder="effectiveEquity <= 0 ? '—' : ''"
                class="w-full bg-surface border border-border-subtle rounded-lg px-2 py-1.5 text-[13.5px] text-right text-slate-300 focus:outline-none focus:border-primary disabled:opacity-50"
                :class="mode === 'pct' ? 'opacity-60' : ''"
              />
              <button type="button" @click="removeCoin(i)" title="Remove"
                      class="text-slate-600 hover:text-red-400 hover:bg-red-500/10 rounded-md p-1 transition-colors text-base leading-none">×</button>
            </div>

            <!-- dynamic row -->
            <div class="grid grid-cols-[22px_1fr_84px_92px_26px] gap-2 items-center px-1 py-2 mt-0.5 rounded-lg border-t border-dashed border-primary/40 bg-gradient-to-r from-primary/[0.08] to-transparent">
              <span class="w-2.5 h-2.5 rounded-[3px] ring-1 ring-primary/40" style="background:#7c5ce6"></span>
              <span class="min-w-0">
                <span class="flex items-center gap-1.5 min-w-0">
                  <span class="text-sm font-semibold text-slate-100">Dynamic</span>
                  <span class="shrink-0 text-[9px] font-semibold uppercase tracking-wide text-primary border border-primary/40 bg-primary/10 rounded-full px-1.5 py-[1px] leading-none">auto</span>
                </span>
                <span class="block text-[11px] text-primary truncate">auto-discovered</span>
              </span>
              <span class="text-right text-[13.5px] font-semibold text-primary tabular-nums">{{ Math.round(dynamicPct) }}%</span>
              <span class="text-right text-[13.5px] text-slate-300 tabular-nums">{{ effectiveEquity > 0 ? fmtUsd(usdFor(dynamicPct)) : '—' }}</span>
              <span></span>
            </div>

            <!-- add bar -->
            <div class="flex items-center gap-2 mt-3 pt-3 border-t border-border-subtle">
              <div class="relative">
                <select v-model="addPick" :disabled="!availableCatalog.length"
                        class="appearance-none bg-surface border border-border-subtle rounded-lg pl-3 pr-8 py-2 text-[13px] text-slate-100 focus:outline-none focus:border-primary disabled:opacity-50">
                  <option value="">{{ availableCatalog.length ? 'Select coin…' : 'All added' }}</option>
                  <option v-for="c in availableCatalog" :key="c.sym" :value="c.sym">{{ c.ticker }} · {{ c.name }}</option>
                </select>
                <span class="material-symbols-outlined text-slate-500 text-[16px] absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none">expand_more</span>
              </div>
              <button type="button" @click="addCoin" :disabled="!addPick"
                      class="ml-auto inline-flex items-center gap-1 bg-primary/[0.14] border border-primary/45 text-primary font-semibold text-[13px] px-3.5 py-2 rounded-lg hover:bg-primary/25 transition-colors disabled:opacity-40">
                <span class="material-symbols-outlined text-[16px]">add</span> Add coin
              </button>
            </div>

            <!-- meter -->
            <div class="mt-4">
              <div class="h-2 rounded-full bg-surface overflow-hidden flex ring-1 ring-inset ring-border-subtle">
                <i v-for="c in coins.filter((x) => Number(x.pct) > 0)" :key="c.sym"
                   class="block h-full" :style="{ width: Math.min(Number(c.pct), 100) + '%', background: c.color }"></i>
                <i v-if="dynamicPct > 0" class="block h-full"
                   :style="{ width: dynamicPct + '%', background: 'repeating-linear-gradient(45deg,#5a4a86,#5a4a86 4px,#4a3c6e 4px,#4a3c6e 8px)' }"></i>
              </div>
              <div class="flex justify-between mt-2.5 text-xs text-slate-500">
                <span>Fixed <b class="text-slate-100">{{ Math.round(fixedSum) }}%</b> · Dynamic <b class="text-primary">{{ Math.round(dynamicPct) }}%</b></span>
                <span v-if="over" class="text-red-400 font-medium">Over-allocated — reduce by {{ Math.round(fixedSum - 100) }}%</span>
                <span v-else-if="effectiveEquity > 0">{{ fmtUsd(usdFor(dynamicPct)) }} flexible</span>
                <span v-else>{{ Math.round(dynamicPct) }}% flexible</span>
              </div>
              <p v-if="!over && dynamicPct <= 0" class="mt-2 text-[11px] leading-snug text-amber-400">
                ⚠ Dynamic 0% — the <b>{{ strategyId }}</b> strategy won't trade (this is buy-and-hold). Lower a fixed weight to give it a dynamic budget to trade.
              </p>
            </div>
          </div>
        </div>

        <p v-if="err" class="text-xs text-red-400">{{ err }}</p>
      </div>

      <!-- Footer -->
      <div class="px-6 py-4 border-t border-border-subtle flex gap-3 sticky bottom-0 bg-surface/80 backdrop-blur">
        <button @click="emit('close')" class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200">Cancel</button>
        <button @click="submit" :disabled="creating || over"
                class="flex-1 py-2.5 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 disabled:opacity-50">
          {{ creating ? (isEdit ? 'Saving…' : 'Creating…') : (isEdit ? 'Save changes' : 'Create instance') }}
        </button>
      </div>
    </div>
  </div>
</template>
