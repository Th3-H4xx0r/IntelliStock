<script setup>
import { ref, shallowRef, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import VueApexCharts from 'vue3-apexcharts'
import BacktestLLMPauseBanner from '../components/BacktestLLMPauseBanner.vue'
import { getToken } from '../utils/auth.js'
import { trScorecardView } from '../utils/trScorecard.js'

const route   = useRoute()
const router  = useRouter()
const btId    = computed(() => route.params.id)
const fromPath = computed(() => route.query.from || '/instances')

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

// ── Crypto fees (per-platform estimate; only for crypto backtests) ──────────────
// `summary.fees` = { total_fees, total_volume, taker_rate } from the backend
// (None for equity runs). Alpaca is the fee actually charged; other venues are
// estimated at their listed taker rate (volume × rate).
const CRYPTO_FEE_PLATFORMS = [
  { id: 'binanceus', name: 'Binance.US', rate: 0.0002 },
  { id: 'alpaca', name: 'Alpaca', rate: 0.0025 },
  { id: 'kraken', name: 'Kraken', rate: 0.0026 },
  { id: 'coinbase', name: 'Coinbase Advanced', rate: 0.006 },
]

// ── State ─────────────────────────────────────────────────────────────────────
const summary   = ref(null)
const feeSummary = computed(() => summary.value?.fees || null)
const feeVolume  = computed(() => Number(feeSummary.value?.total_volume) || 0)
// Rate actually applied to fills (may be an emulated venue's fee).
const appliedRate = computed(() => Number(feeSummary.value?.taker_rate) || 0)
const feeEmulated = computed(() => !!feeSummary.value?.emulated)
const appliedVenueLabel = computed(() => {
  const v = String(feeSummary.value?.venue || '').toLowerCase()
  const byId = CRYPTO_FEE_PLATFORMS.find((p) => p.id === v)
  if (byId) return byId.name
  const byRate = CRYPTO_FEE_PLATFORMS.find((p) => Math.abs(p.rate - appliedRate.value) < 1e-9)
  return byRate ? byRate.name : (feeSummary.value?.venue || '—')
})
const feeRows = computed(() => {
  const fs = feeSummary.value
  if (!fs || feeVolume.value <= 0) return []
  const actual = Number(fs.total_fees) || 0
  const rate = appliedRate.value
  return CRYPTO_FEE_PLATFORMS.map((p) => {
    const applied = Math.abs(p.rate - rate) < 1e-9
    return {
      name: p.name,
      rate: p.rate,
      applied,
      fee: applied ? (actual || feeVolume.value * p.rate) : feeVolume.value * p.rate,
    }
  })
})
// TR scorecard ("vs S&P 500 by market type"): null renders nothing.
const trScorecard = computed(() => trScorecardView(summary.value?.tr_scorecard))
const TR_TONE = { pos: 'text-gain', neg: 'text-loss', muted: 'text-slate-500' }
const graphData = shallowRef(null)
const loading   = ref(true)
const error     = ref('')
const statusData = ref(null)
let pollTimer = null

// Strategy view
const strategyData = ref(null)
const strategyOpen = ref(false)

// AI credits / LLM cost card
const llmCost = ref(null)
const llmCostLoading = ref(false)
const llmCostError = ref('')

async function fetchLlmCost() {
  llmCostLoading.value = true
  llmCostError.value = ''
  try {
    const res = await fetch(`${API_BASE}/backtests/${btId.value}/llm-cost`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    llmCost.value = await res.json()
  } catch (e) {
    llmCostError.value = e?.message || 'failed to load LLM cost'
  } finally {
    llmCostLoading.value = false
  }
}

// ── Data fetching ─────────────────────────────────────────────────────────────
async function fetchSummary() {
  try {
    const res = await fetch(`${API_BASE}/backtests/${btId.value}/summary`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    summary.value = await res.json()
  } catch (e) {
    error.value = e.message
  }
}

async function fetchGraphData() {
  try {
    const res = await fetch(`${API_BASE}/backtests/${btId.value}/graph-data`, { headers: authHeaders() })
    if (!res.ok) return
    graphData.value = await res.json()
  } catch { /* non-critical — not available for pending/failed */ }
}

let _llmCostPollTick = 0
async function fetchStatus() {
  // Refresh the LLM cost card on every ~30s tick regardless of whether
  // /status succeeded. If the API is briefly flaky, we still want the
  // cost card to keep ticking — and we still want the terminal-state
  // check below to run against the last-known status so a transient
  // failure doesn't leave the polling loop spinning forever.
  _llmCostPollTick = (_llmCostPollTick + 1) % 10
  if (_llmCostPollTick === 0) {
    void fetchLlmCost()
  }
  try {
    const res = await fetch(`${API_BASE}/backtests/${btId.value}/status`, { headers: authHeaders() })
    if (!res.ok) return
    statusData.value = await res.json()
    const st = (statusData.value.status || '').toLowerCase()
    if (['completed', 'finished', 'failed', 'stopped', 'error', 'cancelled'].includes(st)) {
      stopPolling()
      await Promise.all([fetchSummary(), fetchGraphData(), fetchLlmCost()])
    } else if (st === 'paused') {
      stopPolling()
    } else {
      // Still running/queued — refresh the summary so the stat tiles and the
      // crypto Fees card update live (the broker rewrites pnl / trades / fees
      // into the result doc each loop iteration).
      void fetchSummary()
    }
  } catch { /* non-critical */ }
}

function startPolling() { if (!pollTimer) pollTimer = setInterval(fetchStatus, 3000) }
function stopPolling()  { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }

function loadStrategyFromSummary() {
  const schema = summary.value?.strategy_schema
  if (schema) {
    // strategy_schema is {name, strategies} — same shape the UI expects
    strategyData.value = {
      id: summary.value?.strategy_id ?? null,
      name: schema.name,
      strategies: schema.strategies || [],
    }
  }
}

async function initPage() {
  stopPolling()
  summary.value      = null
  graphData.value    = null
  statusData.value   = null
  strategyData.value = null
  llmCost.value      = null
  llmCostError.value = ''
  loading.value = true
  error.value   = ''
  // LLM cost is independent of the summary / graph fetches — fire it in
  // parallel so the AI-credits card paints as soon as the backtest_id
  // becomes known, without waiting on the heavier summary payload.
  await Promise.all([fetchSummary(), fetchGraphData(), fetchLlmCost()])
  loading.value = false
  loadStrategyFromSummary()
  const s = (summary.value?.status || '').toLowerCase()
  if (['running', 'queued', 'pending', 'paused', 'paused_llm_critical'].includes(s)) {
    await fetchStatus()
    startPolling()
  }
}

onMounted(initPage)
onUnmounted(stopPolling)

watch(() => route.params.id, (newId, oldId) => {
  if (newId && newId !== oldId) initPage()
})

// ── Formatting ────────────────────────────────────────────────────────────────
function fmtTokens(value) {
  if (value == null) return '—'
  const count = Number(value)
  if (Number.isNaN(count)) return '—'
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`
  if (count >= 1000) return `${(count / 1000).toFixed(1)}k`
  return String(count)
}
function fmtUsdCost(value) {
  if (value == null) return '—'
  const amount = Number(value)
  if (Number.isNaN(amount)) return '—'
  if (amount === 0) return '$0.00'
  if (Math.abs(amount) < 1) return `$${amount.toFixed(4)}`
  return `$${amount.toFixed(2)}`
}
function fmtMoney(v) {
  if (v == null) return '—'
  const n = Number(v)
  // Sign before the currency symbol: -$1,234.56, never $-1,234.56.
  return (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtPnl(v) {
  if (v == null) return '—'
  const n = Number(v)
  return (n >= 0 ? '+' : '') + fmtMoney(n)
}
function fmtPct(v) {
  const n = Number(v)
  if (v == null || Number.isNaN(n)) return '—'
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'
}
// stock_price_change[sym] is a dict {start_price,end_price,change_percent};
// tolerate the legacy flat-number shape too.
function priceChangePct(v) {
  return (v && typeof v === 'object') ? v.change_percent : v
}
function fmtElapsed(seconds) {
  const n = Number(seconds)
  if (!Number.isFinite(n) || n < 0) return '—'
  const total = Math.floor(n)
  const d = Math.floor(total / 86400)
  const h = Math.floor((total % 86400) / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m ${s}s`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}
function pnlClass(v) {
  if (v == null) return 'text-slate-500'
  return Number(v) >= 0 ? 'text-gain' : 'text-loss'
}

// Status as a dot and a word. Green only for a run that is actually running.
const STATUS_UI = {
  running:             { dot: 'ui-dot-live',  label: 'Running' },
  queued:              { dot: 'ui-dot-off',   label: 'Queued' },
  pending:             { dot: 'ui-dot-off',   label: 'Pending' },
  paused:              { dot: 'ui-dot-warn',  label: 'Paused' },
  paused_llm_critical: { dot: 'ui-dot-warn',  label: 'Paused · LLM failure' },
  aborted_llm_failure: { dot: 'ui-dot-error', label: 'Aborted · LLM failure' },
  completed:           { dot: 'bg-slate-400', label: 'Completed' },
  finished:            { dot: 'bg-slate-400', label: 'Finished' },
  stopped:             { dot: 'ui-dot-off',   label: 'Stopped' },
  cancelled:           { dot: 'ui-dot-off',   label: 'Cancelled' },
  error:               { dot: 'ui-dot-error', label: 'Error' },
  failed:              { dot: 'ui-dot-error', label: 'Failed' },
}

// ── Backtest controls (stop / pause / resume / rerun) ─────────────────────────
const showConfirm   = ref(false)
const confirmAction = ref('')   // 'stop' | 'pause' | 'resume' | 'rerun'
const actionBusy    = ref(false)
const actionMsg     = ref('')
const actionOk      = ref(false)

const confirmMeta = computed(() => {
  const s = summary.value
  const tickers = s?.tickers?.join(', ') || '—'
  const dates   = s ? `${(s.start_date||'').slice(0,10)} → ${(s.end_date||'').slice(0,10)}` : '—'
  switch (confirmAction.value) {
    case 'stop':   return { label: 'Stop backtest',   color: 'red',     icon: 'stop_circle',    desc: 'This will permanently stop the backtest. This cannot be undone.' }
    case 'pause':  return { label: 'Pause backtest',  color: 'violet',  icon: 'pause_circle',   desc: 'The backtest will be paused and can be resumed later.' }
    case 'resume': return { label: 'Resume backtest', color: 'sky',     icon: 'play_circle',    desc: currentStatus.value.toLowerCase() === 'paused_llm_critical'
      ? 'The backtest continues from the bar it paused on. If the model provider is still failing, it pauses again.'
      : 'The backtest will continue from where it was paused.' }
    case 'rerun':  return { label: 'Rerun backtest',  color: 'emerald', icon: 'replay',         desc: `A new backtest will be created with the same settings — ${tickers} · ${dates} · $${(s?.initial_cash ?? 100000).toLocaleString()} initial cash. You will be redirected to the new backtest.` }
    case 'delete':          return { label: 'Delete backtest',  color: 'red',   icon: 'delete_forever', desc: 'This will permanently delete all results and data for this backtest. This cannot be undone.' }
    case 'delete-strategy': return { label: 'Delete strategy',  color: 'red',   icon: 'delete_forever', desc: `This will permanently delete the strategy "${strategyData.value?.name || ''}" and all its configuration. This cannot be undone.` }
    default:                return { label: '', color: 'slate', icon: 'help', desc: '' }
  }
})

function openConfirm(action) {
  confirmAction.value = action
  actionMsg.value     = ''
  actionOk.value      = false
  actionBusy.value    = false
  showConfirm.value   = true
}

function closeConfirm() {
  if (actionBusy.value) return
  showConfirm.value = false
}

async function executeAction() {
  actionBusy.value = true
  actionMsg.value  = `${confirmMeta.value.label}...`
  actionOk.value   = false
  try {
    if (confirmAction.value === 'rerun') {
      const s = summary.value
      if (!s.start_date || !s.end_date) throw new Error('Backtest is missing date range') // V7.3: Allow empty tickers for pure discovery mode
      const res = await fetch(`${API_BASE}/backtests`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          ...(s.instance_id != null ? { instance_id: String(s.instance_id) } : {}),
          stocks:       s.tickers || [],
          start_date:   s.start_date,
          end_date:     s.end_date,
          granularity:  String(s.granularity ?? '60'),
          initial_cash: s.initial_cash ?? 100000,
          emulate_fee_venue: s.emulate_fee_venue || 'default',
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        const detail = data.detail
        const msg = Array.isArray(detail)
          ? detail.map(d => d.msg || JSON.stringify(d)).join('; ')
          : (typeof detail === 'string' ? detail : JSON.stringify(data))
        throw new Error(msg || `HTTP ${res.status}`)
      }
      const newId = data.id ?? data.backtest_id
      actionOk.value  = true
      actionMsg.value = `New backtest #${newId} created. Redirecting…`
      setTimeout(() => {
        showConfirm.value = false
        router.push({ name: 'backtest-detail', params: { id: newId }, query: { from: fromPath.value } })
      }, 1000)
      return
    }
    if (confirmAction.value === 'delete') {
      const res = await fetch(`${API_BASE}/backtests/${btId.value}`, {
        method: 'DELETE',
        headers: authHeaders(),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || `HTTP ${res.status}`)
      }
      actionOk.value  = true
      actionMsg.value = 'Backtest deleted. Redirecting...'
      stopPolling()
      setTimeout(() => {
        showConfirm.value = false
        router.push(fromPath.value)
      }, 1000)
      return
    }
    if (confirmAction.value === 'delete-strategy') {
      const sid = strategyData.value?.id
      if (!sid) throw new Error('Strategy ID not found')
      const res = await fetch(`${API_BASE}/strategies/${sid}?force=true`, {
        method: 'DELETE',
        headers: authHeaders(),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || `HTTP ${res.status}`)
      }
      actionOk.value    = true
      actionMsg.value   = 'Strategy deleted.'
      strategyData.value = null
      setTimeout(() => { showConfirm.value = false }, 1200)
      return
    }
    const res = await fetch(`${API_BASE}/backtests/${btId.value}/${confirmAction.value}`, {
      method: 'POST',
      headers: authHeaders(),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    actionOk.value  = true
    actionMsg.value = `${confirmMeta.value.label} successful.`
    await fetchStatus()
    if (confirmAction.value === 'stop') stopPolling()
    else if (confirmAction.value === 'resume') startPolling()
    setTimeout(() => { showConfirm.value = false }, 1200)
  } catch (e) {
    actionOk.value  = false
    actionMsg.value = e.message || 'Something went wrong'
  } finally {
    actionBusy.value = false
  }
}
function statusUi(s) {
  const raw = String(s || '')
  const hit = STATUS_UI[raw.toLowerCase()]
  if (hit) return hit
  const label = raw.replace(/_/g, ' ')
  return { dot: 'ui-dot-off', label: label ? label[0].toUpperCase() + label.slice(1) : 'Unknown' }
}

// ── Computed state ─────────────────────────────────────────────────────────────
const currentStatus = computed(() => statusData.value?.status || summary.value?.status || 'unknown')
const progress      = computed(() => statusData.value?.progress ?? null)
const elapsedSeconds = computed(() => statusData.value?.time_elapsed_seconds ?? summary.value?.time_elapsed_seconds ?? null)
const nexusLookback = computed(() => statusData.value?.nexus_lookback ?? null)
const tickers       = computed(() => summary.value?.tickers || [])

// ── Chart helpers ─────────────────────────────────────────────────────────────
// One graphite ramp: hairline grid, muted 11px axis labels. The equity line
// carries gain/loss; everything else is a zinc step.
const DARK_GRID   = 'rgba(255,255,255,0.05)'
const AXIS_COLOR  = '#71717a'
const AXIS_LABEL  = { colors: AXIS_COLOR, fontSize: '11px', fontFamily: 'inherit' }
const CROSSHAIR   = { show: true, stroke: { color: 'rgba(237,237,237,0.28)', width: 1, dashArray: 3 } }
const GAIN_HEX    = '#4cc38a'
const LOSS_HEX    = '#e5484d'
const PRICE_HEX   = '#b4b4b8'

const baseChartOpts = {
  theme: { mode: 'dark' },
  chart: { background: 'transparent', fontFamily: 'Inter, sans-serif', toolbar: { show: false }, animations: { enabled: false }, parentHeightOffset: 0 },
  grid:  { borderColor: DARK_GRID, strokeDashArray: 0, xaxis: { lines: { show: false } }, padding: { left: 4, right: 8, top: 0, bottom: 0 } },
  legend: { labels: { colors: '#a1a1aa' } },
}

// Scrubbing state for portfolio chart
const hoveredPortfolioValue = ref(null)
const hoveredPortfolioDate  = ref(null)
const displayPortfolioValue = computed(() =>
  hoveredPortfolioValue.value ?? summary.value?.portfolio_end_value
)
const displayPortfolioPnl = computed(() => {
  const start = summary.value?.portfolio_start_value
  const cur   = displayPortfolioValue.value
  if (start == null || cur == null) return null
  return Number(cur) - Number(start)
})

// Portfolio value chart
const portfolioSeries = computed(() => {
  const hist = graphData.value?.portfolio_value_history || []
  return [{
    name: 'Portfolio Value',
    data: hist
      .map(h => [new Date(h.timestamp || h.date || h.time || 0).getTime(), Number(h.value || 0)])
      .filter(([t]) => !isNaN(t) && t > 0),
  }]
})

// The equity line is the money: green when the run is up on its start, red when down.
const portfolioLineColor = computed(() => {
  const start = Number(summary.value?.portfolio_start_value)
  const end   = Number(summary.value?.portfolio_end_value)
  const up = Number.isFinite(start) && Number.isFinite(end) ? end >= start : Number(summary.value?.pnl ?? 0) >= 0
  return up ? GAIN_HEX : LOSS_HEX
})

const portfolioOpts = computed(() => ({
  ...baseChartOpts,
  chart: {
    ...baseChartOpts.chart,
    type: 'area',
    id: 'portfolio',
    zoom: { enabled: true, type: 'x' },
    events: {
      mouseMove: (_e, _ctx, config) => {
        const idx  = config.dataPointIndex
        const data = portfolioSeries.value[0]?.data
        if (data && idx != null && idx >= 0 && idx < data.length) {
          hoveredPortfolioValue.value = data[idx][1]
          hoveredPortfolioDate.value  = data[idx][0]
        }
      },
      mouseLeave: () => {
        hoveredPortfolioValue.value = null
        hoveredPortfolioDate.value  = null
      },
    },
  },
  stroke:  { curve: 'smooth', width: 2 },
  // A faint same-hue fade under the P&L line, as on the dashboard hero.
  fill:    {
    type: 'gradient',
    gradient: {
      colorStops: [
        { offset: 0,   color: portfolioLineColor.value, opacity: 0.12 },
        { offset: 100, color: portfolioLineColor.value, opacity: 0 },
      ],
    },
  },
  colors:  [portfolioLineColor.value],
  markers: { size: 0, strokeWidth: 0, hover: { size: 4 } },
  xaxis:   {
    type: 'datetime',
    labels: { style: AXIS_LABEL },
    axisBorder: { show: false },
    axisTicks: { show: false },
    crosshairs: CROSSHAIR,
    tooltip: { enabled: false },
  },
  yaxis:   {
    // forceNiceScale + decimalsInFloat are CORE options applied on every
    // (re)render, so even when ApexCharts drops the custom formatter on a
    // running-backtest data update the labels stay clean ("$10,500") instead
    // of raw float precision ("10500.000000000000").
    forceNiceScale: true,
    decimalsInFloat: 0,
    labels: {
      formatter: v => '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 }),
      style: AXIS_LABEL,
    },
  },
  dataLabels: { enabled: false },
  tooltip: {
    enabled: true,
    shared: true,
    intersect: false,
    custom: () => '',
  },
  annotations: summary.value?.portfolio_start_value != null ? {
    yaxis: [{
      y: summary.value.portfolio_start_value,
      borderColor: 'rgba(237,237,237,0.28)',
      strokeDashArray: 2,
      label: { text: 'Start', borderWidth: 0, style: { color: AXIS_COLOR, background: 'transparent', fontSize: '11px' } },
    }],
  } : {},
}))

// Per-stock price + trade series.
// All use type:'line' so they share the same canvas layer — mixed line+scatter
// causes scatter markers to be hidden behind the line fill area.
// Buy/Sell use stroke.width=0 so only their markers are drawn (no connecting line).
function stockSeries(sym) {
  const prices = (graphData.value?.backtest_prices || [])
    .filter(p => p.symbol === sym)
    .map(p => [new Date(p.timestamp).getTime(), Number(p.close)])
    .filter(([t]) => !isNaN(t))

  const buys = (graphData.value?.backtest_trades || [])
    .filter(t => t.ticker === sym && (t.action || '').toLowerCase().includes('buy'))
    .map(t => [new Date(t.timestamp).getTime(), Number(t.price)])

  const sells = (graphData.value?.backtest_trades || [])
    .filter(t => t.ticker === sym && (t.action || '').toLowerCase().includes('sell'))
    .map(t => [new Date(t.timestamp).getTime(), Number(t.price)])

  return [
    { name: sym,    type: 'area', data: prices },
    { name: 'Buy',  type: 'line', data: buys   },
    { name: 'Sell', type: 'line', data: sells  },
  ]
}

function stockOpts(sym) {
  return {
    ...baseChartOpts,
    chart: {
      ...baseChartOpts.chart,
      type: 'line',
      id: 'stock-' + sym,
      zoom: { enabled: true, type: 'x' },
    },
    // stroke width 0 for Buy/Sell hides the connecting line, leaving only markers.
    // Price is a plain zinc line (no fill); buys and sells are gain/loss dots
    // with a ring in the card colour so they sit crisply on the line.
    stroke:  { width: [1.5, 0, 0], curve: 'smooth' },
    fill:    {
      type: ['solid', 'solid', 'solid'],
      opacity: [0, 0, 0],
    },
    markers: {
      size:         [0, 5, 5],
      shape:        ['circle', 'circle', 'circle'],
      strokeWidth:  [0, 2, 2],
      strokeColors: ['transparent', '#161617', '#161617'],
      fillOpacity:  1,
      hover:        { sizeOffset: 1 },
    },
    colors:  [PRICE_HEX, GAIN_HEX, LOSS_HEX],
    legend:  { show: false },
    xaxis:   { type: 'datetime', labels: { style: AXIS_LABEL }, axisBorder: { show: false }, axisTicks: { show: false },
               crosshairs: CROSSHAIR },
    yaxis:   { forceNiceScale: true, decimalsInFloat: 2, labels: { formatter: v => '$' + Number(v).toFixed(2), style: AXIS_LABEL } },
    tooltip: {
      shared: true,
      intersect: false,
      x: { format: 'MMM dd yyyy HH:mm' },
      y: { formatter: (v, { seriesIndex }) => {
        if (v == null) return undefined
        const labels = ['Price', 'Buy', 'Sell']
        return `<span style="color:${[PRICE_HEX, GAIN_HEX, LOSS_HEX][seriesIndex]}">${labels[seriesIndex] ?? ''}</span> $${Number(v).toFixed(2)}`
      }},
    },
  }
}

// Per-stock trade list (for table below chart)
function stockTrades(sym) {
  return (graphData.value?.backtest_trades || [])
    .filter(t => t.ticker === sym)
    .sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp))
}

function stockDecisions(sym) {
  return (graphData.value?.backtest_decisions || [])
    .filter(d => d.symbol === sym)
    .sort((a, b) => new Date(b.timestamp || 0) - new Date(a.timestamp || 0))
}

function decisionLabel(decision, action) {
  if (action) return String(action).toUpperCase()
  return ({ 1: 'BUY', 0: 'HOLD', '-1': 'SELL' })[String(decision)] || 'HOLD'
}

// Decision as a dot and a word: buy and sell are trade directions in
// gain/loss, hold is a grey step.
function decisionBadgeClass(decision, action) {
  const label = decisionLabel(decision, action)
  if (label === 'BUY') return 'bg-gain'
  if (label === 'SELL') return 'bg-loss'
  return 'bg-slate-600'
}

function fmtWeight(weight) {
  const n = Number(weight)
  if (!Number.isFinite(n)) return '—'
  return `${(n * 100).toFixed(0)}%`
}

function fmtScore(score) {
  const n = Number(score)
  if (!Number.isFinite(n)) return '—'
  return n.toFixed(3)
}

function shortReason(reason, max = 360) {
  const text = String(reason || '').trim()
  if (!text) return ''
  if (text.length <= max) return text
  return `${text.slice(0, max - 1)}…`
}

function toPrettyJson(value) {
  try { return JSON.stringify(value, null, 2) }
  catch { return String(value ?? '') }
}

function fmtDate(ts) {
  if (!ts) return '—'
  try { return new Date(ts).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) }
  catch { return ts }
}

// ── Logs ──────────────────────────────────────────────────────────────────────
const logsOpen    = ref(false)
const logsData    = ref([])
const logsLoading = ref(false)
const logsSource  = ref('')
const logsSearch  = ref('')

async function loadLogs() {
  if (logsLoading.value) return
  logsLoading.value = true
  try {
    const res = await fetch(`${API_BASE}/backtests/${btId.value}/logs`, { headers: authHeaders() })
    if (!res.ok) return
    const data = await res.json()
    logsData.value   = data.logs || []
    logsSource.value = data.source || 'db'
  } catch { /* non-critical */ } finally {
    logsLoading.value = false
  }
}

function toggleLogs() {
  logsOpen.value = !logsOpen.value
  if (logsOpen.value && logsData.value.length === 0) loadLogs()
}

const filteredLogs = computed(() => {
  const q = logsSearch.value.trim().toLowerCase()
  if (!q) return logsData.value
  return logsData.value.filter(l => l.toLowerCase().includes(q))
})

// Parse a log line into { time, service, level, message }
function parseLine(line) {
  // Format: [2026-03-05 10:53:43] [SERVICE] message
  const m = line.match(/^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\](?:\s+\[([^\]]+)\])?\s*(.*)$/)
  if (m) return { time: m[1], service: m[2] || null, message: m[3] || '' }
  return { time: null, service: null, message: line }
}

function logLevelClass(line) {
  const l = line.toLowerCase()
  if (l.includes('error') || l.includes('fail') || l.includes('exception')) return 'text-loss'
  if (l.includes('warn') || l.includes('yellow') || l.includes('skip')) return 'text-amber-400'
  if (l.includes('success') || l.includes('finished') || l.includes('profit') || l.includes('passed')) return 'text-slate-100'
  if (l.includes('broker')) return 'text-slate-300'
  return 'text-slate-400'
}

function copyLogs() {
  navigator.clipboard.writeText(logsData.value.join('\n')).catch(() => {})
}

function downloadLogs() {
  const blob = new Blob([logsData.value.join('\n')], { type: 'text/plain' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = `backtest-${btId.value}.log`
  a.click()
  URL.revokeObjectURL(url)
}

// ── Lazy loading & accordion (perf optimization) ─────────────────────────────
const expandedStocks   = ref(new Set())
const visibleCharts    = ref(new Set())   // IntersectionObserver tracked
const decisionLimits   = ref({})          // per-stock pagination
const DECISIONS_PAGE   = 5
const LOG_WINDOW_SIZE  = 150              // virtual scroll for logs

function toggleStock(sym) {
  const s = new Set(expandedStocks.value)
  if (s.has(sym)) {
    s.delete(sym)
    visibleCharts.value.delete(sym)
  } else {
    s.add(sym)
  }
  expandedStocks.value = s
}

function expandAllStocks() {
  expandedStocks.value = new Set(tickers.value)
}

function collapseAllStocks() {
  expandedStocks.value = new Set()
  visibleCharts.value  = new Set()
  decisionLimits.value = {}
}

function showMoreDecisions(sym) {
  const current = decisionLimits.value[sym] || DECISIONS_PAGE
  decisionLimits.value = { ...decisionLimits.value, [sym]: current + DECISIONS_PAGE }
}

function visibleDecisions(sym) {
  const all   = stockDecisions(sym)
  const limit = decisionLimits.value[sym] || DECISIONS_PAGE
  return all.slice(0, limit)
}

function remainingDecisions(sym) {
  const all   = stockDecisions(sym).length
  const limit = decisionLimits.value[sym] || DECISIONS_PAGE
  return Math.max(0, all - limit)
}

// IntersectionObserver for lazy chart mounting
const chartObserver = ref(null)

function onChartRef(el, sym) {
  if (!el || !chartObserver.value) return
  el.__sym = sym
  chartObserver.value.observe(el)
}

function setupChartObserver() {
  if (typeof IntersectionObserver === 'undefined') return
  chartObserver.value = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const sym = entry.target.__sym
        if (!sym) continue
        if (entry.isIntersecting) {
          visibleCharts.value.add(sym)
        }
      }
    },
    { rootMargin: '200px 0px' }
  )
}

function teardownChartObserver() {
  if (chartObserver.value) {
    chartObserver.value.disconnect()
    chartObserver.value = null
  }
}

onMounted(setupChartObserver)
onUnmounted(teardownChartObserver)

// ── Virtual scroll for logs ──────────────────────────────────────────────────
const logScrollTop  = ref(0)
const LOG_LINE_H    = 20  // approx line height in px

const virtualLogs = computed(() => {
  const all = filteredLogs.value
  if (all.length <= LOG_WINDOW_SIZE) return { items: all, startIdx: 0, totalHeight: 0, offsetY: 0, useVirtual: false }
  const startIdx  = Math.max(0, Math.floor(logScrollTop.value / LOG_LINE_H) - 10)
  const endIdx    = Math.min(all.length, startIdx + LOG_WINDOW_SIZE)
  return {
    items: all.slice(startIdx, endIdx),
    startIdx,
    totalHeight: all.length * LOG_LINE_H,
    offsetY: startIdx * LOG_LINE_H,
    useVirtual: true,
  }
})

function onLogScroll(e) {
  logScrollTop.value = e.target.scrollTop
}
</script>


<template>
  <AppShell>
    <main class="ui-page">

      <!-- Breadcrumb -->
      <nav class="flex items-center gap-1 text-sm text-slate-500 -ml-3 mb-4" aria-label="Breadcrumb">
        <button @click="router.push(fromPath)" class="ui-btn-ghost px-3">
          <span class="material-symbols-outlined text-[16px]">arrow_back</span>
          Back
        </button>
        <span class="text-slate-600" aria-hidden="true">/</span>
        <span class="px-2 text-slate-400 tabular-nums">Backtest #{{ btId }}</span>
      </nav>

      <!-- Loading -->
      <div v-if="loading" aria-hidden="true">
        <div class="ui-skel h-7 w-56"></div>
        <div class="ui-skel h-4 w-72 mt-3"></div>
        <div class="h-[132px] rounded-xl bg-surface mt-8"></div>
        <div class="h-[240px] rounded-xl bg-surface mt-12"></div>
      </div>

      <!-- Error -->
      <div v-else-if="error" class="ui-error">
        {{ error }}
      </div>

      <template v-else-if="summary">

        <!-- ── Header ────────────────────────────────────────────────────────── -->
        <header class="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
          <div class="min-w-0">
            <h1 class="ui-page-title tabular-nums">Backtest #{{ btId }}</h1>
            <p class="ui-page-sub flex flex-wrap items-center gap-x-2 gap-y-1">
              <span class="inline-flex items-center gap-1.5 text-slate-300">
                <span class="ui-dot" :class="statusUi(currentStatus).dot"></span>
                {{ statusUi(currentStatus).label }}
              </span>
              <span class="text-slate-600" aria-hidden="true">·</span>
              <span class="tabular-nums">{{ summary.start_date }} → {{ summary.end_date }}</span>
              <template v-if="tickers.length">
                <span class="text-slate-600" aria-hidden="true">·</span>
                <span>{{ tickers.join(', ') }}</span>
              </template>
            </p>
          </div>

          <!-- Action controls -->
          <div class="flex items-center gap-2 flex-wrap">
            <!-- Pause (only when running) -->
            <button
              v-if="currentStatus.toLowerCase() === 'running'"
              @click="openConfirm('pause')"
              class="ui-btn"
            >Pause</button>
            <!-- Resume (when paused, by hand or by an LLM failure) -->
            <button
              v-if="['paused','paused_llm_critical'].includes(currentStatus.toLowerCase())"
              @click="openConfirm('resume')"
              class="ui-btn"
            >Resume</button>
            <!-- Stop (when running, paused or waiting), as on the list -->
            <button
              v-if="['running','queued','pending','paused','paused_llm_critical'].includes(currentStatus.toLowerCase())"
              @click="openConfirm('stop')"
              class="ui-btn text-loss"
            >Stop</button>
            <!-- Rerun -->
            <button
              v-if="summary"
              @click="openConfirm('rerun')"
              class="ui-btn"
            >Rerun</button>
            <!-- Live playback -->
            <button
              v-if="summary"
              @click="router.push({ name: 'backtest-playback', params: { id: btId } })"
              class="ui-btn"
            >Live playback</button>
            <!-- Delete -->
            <button
              v-if="summary"
              @click="openConfirm('delete')"
              class="ui-btn text-loss"
            >Delete</button>
          </div>

          <!-- Running: progress bar -->
          <div v-if="progress != null && ['running','queued','pending'].includes(currentStatus.toLowerCase())" class="w-full">
            <div class="flex items-center justify-between text-xs text-slate-500 mb-2">
              <span>Progress</span>
              <span class="text-slate-300 tabular-nums">{{ Math.round(progress) }}%</span>
            </div>
            <div class="bar-track h-1">
              <span class="bar-fill bg-slate-300" :style="{ transform: `scaleX(${Math.min(100, Math.max(0, progress)) / 100})` }"></span>
            </div>
          </div>
        </header>

        <!-- ── LLM-critical pause banner ────────────────────────────────────── -->
        <BacktestLLMPauseBanner class="mt-8" :summary="summary" />

        <!-- ── Nexus lookback training ───────────────────────────────────────── -->
        <section
          v-if="nexusLookback"
          class="mt-8 ui-card px-5 py-4"
          aria-label="Nexus lookback training"
        >
          <div class="flex items-center justify-between gap-4">
            <p class="text-sm font-medium text-slate-100">Nexus lookback training</p>
            <p class="text-xs text-slate-400 tabular-nums">
              Day {{ nexusLookback.current }} of {{ nexusLookback.total }}
            </p>
          </div>
          <div class="bar-track h-1 mt-3">
            <span class="bar-fill bg-slate-300" :style="{ transform: `scaleX(${nexusLookback.total > 0 ? nexusLookback.current / nexusLookback.total : 0})` }"></span>
          </div>
          <div class="mt-2 flex items-center justify-between text-xs text-slate-500 tabular-nums">
            <span>{{ nexusLookback.start_date }}</span>
            <span class="text-slate-300">{{ nexusLookback.current_date }}</span>
            <span>{{ nexusLookback.end_date }}</span>
          </div>
          <p class="mt-3 text-xs text-slate-500">Building historical event context before trading begins. This runs once per scope.</p>
        </section>

        <!-- ── Results ───────────────────────────────────────────────────────── -->
        <section class="mt-8 ui-card grid grid-cols-1 lg:grid-cols-[minmax(240px,1fr)_minmax(0,2.6fr)]" aria-label="Results">
          <div class="px-5 py-5 border-b border-white/[0.06] lg:border-b-0 lg:border-r">
            <p class="ui-label">Total P&amp;L</p>
            <p class="mt-3 text-[32px] leading-none font-medium tabular-nums" :class="pnlClass(summary.pnl)">{{ fmtPnl(summary.pnl) }}</p>
            <p class="mt-3 text-sm tabular-nums">
              <span :class="pnlClass(summary.pnl_percent)">{{ fmtPct(summary.pnl_percent) }}</span>
              <span class="text-slate-500 ml-1.5">return</span>
            </p>
          </div>
          <dl class="px-5 py-5 grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-5 gap-x-6 gap-y-5">
            <div>
              <dt class="stat-label">Portfolio</dt>
              <dd class="stat-value text-slate-100">{{ fmtMoney(summary.portfolio_end_value) }}</dd>
              <dd class="stat-sub">from {{ fmtMoney(summary.portfolio_start_value) }}</dd>
            </div>
            <div>
              <dt class="stat-label">Trades</dt>
              <dd class="stat-value text-slate-100">{{ summary.total_trades ?? '—' }}</dd>
              <dd class="stat-sub">{{ summary.total_buys ?? 0 }} buy · {{ summary.total_sells ?? 0 }} sell</dd>
            </div>
            <div>
              <dt class="stat-label">Elapsed</dt>
              <dd class="stat-value text-slate-100">{{ fmtElapsed(elapsedSeconds) }}</dd>
            </div>
            <div>
              <dt class="stat-label">Win rate</dt>
              <dd class="stat-value text-slate-100">
                {{ summary.win_rate_percent != null ? summary.win_rate_percent.toFixed(1) + '%' : '—' }}
              </dd>
              <dd class="stat-sub">{{ summary.winning_round_trips ?? 0 }}W · {{ summary.losing_round_trips ?? 0 }}L</dd>
            </div>
            <div>
              <dt class="stat-label">Portfolio high</dt>
              <dd class="stat-value text-slate-100">{{ fmtMoney(summary.portfolio_value_high) }}</dd>
              <dd class="stat-sub">Low {{ fmtMoney(summary.portfolio_value_low) }}</dd>
            </div>
          </dl>
        </section>

        <!-- ── TR scorecard: vs S&P 500 by market type ───────────────────────── -->
        <section v-if="trScorecard" class="mt-12" aria-labelledby="tr-title">
          <div class="mb-3">
            <h2 id="tr-title" class="ui-section-title">vs S&amp;P 500 by market type</h2>
            <p v-if="trScorecard.cutoff" class="ui-section-sub tabular-nums">AI training cutoff: {{ trScorecard.cutoff }}</p>
          </div>
          <div class="ui-card overflow-hidden">
            <p v-if="trScorecard.error" class="px-5 py-4 text-sm text-slate-400 flex items-center gap-2">
              <span class="ui-dot ui-dot-warn" aria-hidden="true"></span>{{ trScorecard.error }}
            </p>
            <template v-else>
              <div class="overflow-x-auto">
                <table class="w-full min-w-[880px] text-sm">
                  <thead>
                    <tr class="border-b border-white/[0.06] text-left text-xs text-slate-500">
                      <th class="h-10 pl-5 pr-3 font-normal">Period</th>
                      <th class="h-10 px-3 font-normal text-right">Return/yr</th>
                      <th class="h-10 px-3 font-normal text-right">S&amp;P/yr</th>
                      <th class="h-10 px-3 font-normal text-right">Difference</th>
                      <th class="h-10 px-3 font-normal text-right">Worst drop</th>
                      <th class="h-10 px-3 font-normal text-right">S&amp;P worst drop</th>
                      <th class="h-10 px-3 font-normal text-right">Beat S&amp;P in all market types</th>
                      <th class="h-10 pl-3 pr-5 font-normal text-right">+5 pts/yr target</th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-white/[0.05]">
                    <tr v-for="p in trScorecard.periods" :key="p.key">
                      <td class="pl-5 pr-3 py-2.5">
                        <p class="text-sm text-slate-100">{{ p.label }}</p>
                        <p v-if="p.dates" class="text-xs text-slate-500 mt-0.5 tabular-nums">{{ p.dates }}</p>
                      </td>
                      <td class="px-3 py-2.5 text-right tabular-nums text-slate-200">{{ p.cells.cagr }}</td>
                      <td class="px-3 py-2.5 text-right tabular-nums text-slate-400">{{ p.cells.spyCagr }}</td>
                      <td class="px-3 py-2.5 text-right tabular-nums" :class="TR_TONE[p.diffTone]">{{ p.cells.diff }}</td>
                      <td class="px-3 py-2.5 text-right tabular-nums text-slate-200">{{ p.cells.maxDd }}</td>
                      <td class="px-3 py-2.5 text-right tabular-nums text-slate-400">{{ p.cells.spyMaxDd }}</td>
                      <td class="px-3 py-2.5 text-right" :class="TR_TONE[p.allRegimes.tone]">{{ p.allRegimes.label }}</td>
                      <td class="pl-3 pr-5 py-2.5 text-right" :class="TR_TONE[p.plus5.tone]">{{ p.plus5.label }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-for="p in trScorecard.periods" v-show="!p.untested" :key="`w-${p.key}`" class="px-5 py-4 border-t border-white/[0.06]">
                <p class="text-sm text-slate-100">{{ p.label }}</p>
                <p v-for="(line, i) in p.random" :key="`r-${i}`" class="text-xs text-slate-400 mt-1 tabular-nums">{{ line }}</p>
                <div v-if="p.windows.length" class="overflow-x-auto mt-3 rounded-lg border border-white/[0.06]">
                  <table class="w-full min-w-[560px] text-sm">
                    <thead>
                      <tr class="border-b border-white/[0.06] text-left text-xs text-slate-500">
                        <th class="h-9 pl-4 pr-3 font-normal">Market type</th>
                        <th class="h-9 px-3 font-normal">Dates</th>
                        <th class="h-9 px-3 font-normal text-right">Strategy</th>
                        <th class="h-9 px-3 font-normal text-right">S&amp;P</th>
                        <th class="h-9 pl-3 pr-4 font-normal text-right">Beat?</th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-white/[0.05]">
                      <tr v-for="(w, i) in p.windows" :key="`${i}-${w.key}`">
                        <td class="h-10 pl-4 pr-3 text-slate-200">{{ w.kind }}</td>
                        <td class="h-10 px-3 tabular-nums text-slate-400">{{ w.dates }}</td>
                        <td class="h-10 px-3 text-right tabular-nums text-slate-200">{{ w.strategy }}</td>
                        <td class="h-10 px-3 text-right tabular-nums text-slate-400">{{ w.spy }}</td>
                        <td class="h-10 pl-3 pr-4 text-right" :class="TR_TONE[w.tone]">{{ w.beat }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <p v-else class="text-xs text-slate-500 mt-1">No market-type windows fall inside this period</p>
              </div>
              <ul v-if="trScorecard.notes.length" class="px-5 py-3 border-t border-white/[0.06] space-y-1">
                <li v-for="(note, i) in trScorecard.notes" :key="`n-${i}`" class="text-xs text-slate-500 leading-5">{{ note }}</li>
              </ul>
            </template>
          </div>
        </section>

        <!-- ── Fees (crypto backtests only) ──────────────────────────────────── -->
        <section v-if="feeRows.length" class="mt-12" aria-labelledby="fees-title">
          <div class="mb-3 flex items-end justify-between gap-4">
            <div>
              <h2 id="fees-title" class="ui-section-title">Estimated fees per platform</h2>
              <p class="ui-section-sub">Crypto</p>
            </div>
            <span
              v-if="feeEmulated"
              class="shrink-0 inline-flex items-center gap-2 text-xs text-slate-400"
              :title="`Fills were charged ${appliedVenueLabel} fees, not this instance's brokerage.`"
            >
              <span class="ui-dot ui-dot-warn" aria-hidden="true"></span>
              Emulated · {{ appliedVenueLabel }}
            </span>
          </div>
          <div class="ui-card">
            <div class="h-12 px-5 flex items-center justify-between text-sm border-b border-white/[0.06]">
              <span class="text-slate-400">Crypto volume traded</span>
              <span class="text-slate-100 tabular-nums">{{ fmtMoney(feeVolume) }}</span>
            </div>
            <ul class="ui-rows">
              <li v-for="row in feeRows" :key="row.name" class="h-12 px-5 flex items-center gap-4 text-sm">
                <span class="flex-1 min-w-0 flex items-center gap-2">
                  <span class="truncate" :class="row.applied ? 'text-slate-100' : 'text-slate-300'">{{ row.name }}</span>
                  <span v-if="row.applied" class="shrink-0 text-xs text-slate-500">Applied</span>
                </span>
                <span class="text-slate-500 tabular-nums w-16 text-right">{{ (row.rate * 100).toFixed(2) }}%</span>
                <span class="tabular-nums w-24 text-right" :class="row.applied ? 'text-slate-100' : 'text-slate-400'">{{ fmtMoney(row.fee) }}</span>
              </li>
            </ul>
            <p class="px-5 py-3 border-t border-white/[0.06] text-xs text-slate-500 leading-5">
              <template v-if="feeEmulated"><span class="text-slate-300">{{ appliedVenueLabel }}</span> fees were emulated here (not your instance's brokerage) — it's the “applied” row.</template>
              <template v-else><span class="text-slate-300">{{ appliedVenueLabel }}</span> is the fee actually charged in this backtest.</template>
              Other venues are estimates at their listed taker rate (volume × rate) — actual tiers vary.
            </p>
          </div>
        </section>

        <!-- ── AI credits / LLM cost card ────────────────────────────────────── -->
        <section class="mt-12" aria-labelledby="llm-cost-title">
          <div class="mb-3 flex items-end justify-between gap-4">
            <div class="min-w-0">
              <h2 id="llm-cost-title" class="ui-section-title">LLM cost for this backtest</h2>
              <p class="ui-section-sub">AI credits</p>
            </div>
            <button
              v-if="!llmCostLoading"
              @click="fetchLlmCost"
              class="ui-icon-btn"
              title="Refresh"
              aria-label="Refresh LLM cost"
            >
              <span class="material-symbols-outlined text-[18px]">refresh</span>
            </button>
            <span v-else class="size-8 inline-flex items-center justify-center text-slate-500" aria-label="Loading">
              <span class="material-symbols-outlined text-[18px] animate-spin">refresh</span>
            </span>
          </div>
          <div class="ui-card overflow-hidden">
            <div v-if="llmCostError" class="px-5 py-4 text-sm text-loss">
              {{ llmCostError }}
            </div>
            <div v-else-if="!llmCost && llmCostLoading" class="px-5 py-5 grid grid-cols-2 sm:grid-cols-4 gap-6" aria-hidden="true">
              <div v-for="n in 4" :key="n"><div class="ui-skel h-3 w-16"></div><div class="ui-skel h-6 w-20 mt-3"></div></div>
            </div>
            <div v-else-if="!llmCost || !llmCost.total_calls" class="px-5 py-6 text-sm text-slate-500">
              No LLM calls were attributed to this backtest. (Either the strategy ran with no LLM stages
              enabled, or the backtest finished before the telemetry sink flushed.)
            </div>
            <div v-else>
              <!-- Headline totals row -->
              <dl class="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-5 px-5 py-5 border-b border-white/[0.06]">
                <div>
                  <dt class="ui-label">Total cost</dt>
                  <dd class="mt-2 text-2xl font-medium text-slate-100 tabular-nums leading-none">{{ fmtUsdCost(llmCost.total_cost_usd) }}</dd>
                </div>
                <div>
                  <dt class="ui-label">Calls</dt>
                  <dd class="mt-2 text-xl font-medium text-slate-100 tabular-nums leading-none">{{ llmCost.total_calls }}</dd>
                  <dd class="stat-sub">
                    {{ llmCost.ok_calls }} ok<template v-if="llmCost.failed_calls > 0"> · <span class="text-amber-400">{{ llmCost.failed_calls }} failed</span></template>
                  </dd>
                </div>
                <div>
                  <dt class="ui-label">Input tokens</dt>
                  <dd class="mt-2 text-xl font-medium text-slate-100 tabular-nums leading-none">{{ fmtTokens(llmCost.total_input_tokens) }}</dd>
                </div>
                <div>
                  <dt class="ui-label">Output tokens</dt>
                  <dd class="mt-2 text-xl font-medium text-slate-100 tabular-nums leading-none">{{ fmtTokens(llmCost.total_output_tokens) }}</dd>
                  <dd v-if="llmCost.total_reasoning_tokens > 0" class="stat-sub">
                    incl. {{ fmtTokens(llmCost.total_reasoning_tokens) }} reasoning
                  </dd>
                </div>
              </dl>
              <!-- Breakdown tables: model, stage, strategy -->
              <div class="grid grid-cols-1 lg:grid-cols-3 divide-y lg:divide-y-0 lg:divide-x divide-white/[0.06]">
                <div class="px-5 py-4">
                  <p class="ui-label mb-3">By model</p>
                  <ul class="space-y-2">
                    <li
                      v-for="row in llmCost.by_model.slice(0, 6)"
                      :key="`m-${row.key}`"
                      class="flex items-center justify-between gap-3 text-sm"
                    >
                      <span class="text-slate-300 truncate min-w-0">{{ row.key }}</span>
                      <span class="text-slate-100 tabular-nums shrink-0">{{ fmtUsdCost(row.cost_usd) }}</span>
                    </li>
                    <li v-if="!llmCost.by_model.length" class="text-xs text-slate-500">No models recorded</li>
                  </ul>
                </div>
                <div class="px-5 py-4">
                  <p class="ui-label mb-3">By call site</p>
                  <ul class="space-y-2">
                    <li
                      v-for="row in llmCost.by_call_site.slice(0, 6)"
                      :key="`c-${row.key}`"
                      class="flex items-center justify-between gap-3 text-sm"
                    >
                      <span class="text-slate-300 truncate min-w-0">{{ row.key }}</span>
                      <span class="text-slate-100 tabular-nums shrink-0">{{ fmtUsdCost(row.cost_usd) }}</span>
                    </li>
                    <li v-if="!llmCost.by_call_site.length" class="text-xs text-slate-500">No call sites recorded</li>
                  </ul>
                </div>
                <div class="px-5 py-4">
                  <p class="ui-label mb-3">By provider</p>
                  <ul class="space-y-2">
                    <li
                      v-for="row in llmCost.by_provider.slice(0, 6)"
                      :key="`p-${row.key}`"
                      class="flex items-center justify-between gap-3 text-sm"
                    >
                      <span class="text-slate-300 truncate min-w-0">{{ row.key }}</span>
                      <span class="text-slate-100 tabular-nums shrink-0">{{ fmtUsdCost(row.cost_usd) }}</span>
                    </li>
                    <li v-if="!llmCost.by_provider.length" class="text-xs text-slate-500">No providers recorded</li>
                  </ul>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- ── Strategy info ─────────────────────────────────────────────────── -->
        <section v-if="strategyData" class="mt-12 ui-card overflow-hidden">
          <!-- Clickable header -->
          <button
            class="w-full min-h-14 px-5 py-2 flex items-center justify-between gap-4 text-left hover:bg-white/[0.03] transition-colors duration-150 ease-out"
            :aria-expanded="strategyOpen"
            @click="strategyOpen = !strategyOpen"
          >
            <span class="min-w-0">
              <span class="block text-sm text-slate-100 truncate">{{ strategyData?.name || '—' }}</span>
              <span class="block text-xs text-slate-500 mt-0.5">Strategy</span>
            </span>
            <span class="material-symbols-outlined text-slate-500 text-[18px] shrink-0 transition-transform duration-150 ease-out" :class="strategyOpen ? 'rotate-180' : ''">
              expand_more
            </span>
          </button>

          <!-- Collapsible body -->
          <div v-if="strategyOpen && strategyData" class="border-t border-white/[0.06] px-5 pt-4 pb-5">
            <p v-if="strategyData.id" class="text-xs text-slate-500 tabular-nums mb-4">ID {{ strategyData.id }}</p>

            <p class="ui-label mb-2">Sub-strategies</p>
            <div class="rounded-lg border border-white/[0.06] divide-y divide-white/[0.06]">
              <div
                v-for="(sub, idx) in (strategyData.strategies || [])"
                :key="idx"
                class="px-4 py-3"
              >
                <!-- Sub-strategy header -->
                <div class="flex items-baseline justify-between gap-3 flex-wrap">
                  <p class="flex items-baseline gap-3 min-w-0">
                    <span class="text-xs text-slate-500 tabular-nums w-4">{{ idx + 1 }}</span>
                    <span class="text-sm font-medium text-slate-100 truncate">{{ sub.strategy }}</span>
                  </p>
                  <p class="text-xs text-slate-400 tabular-nums flex flex-wrap items-center gap-x-2">
                    <span>{{ (sub.weight * 100).toFixed(0) }}%</span>
                    <template v-if="sub.execution_position != null"><span class="text-slate-600">·</span><span>pos {{ sub.execution_position }}</span></template>
                    <template v-if="sub.decision_phase"><span class="text-slate-600">·</span><span>{{ sub.decision_phase }}</span></template>
                    <template v-if="sub.execution_scope"><span class="text-slate-600">·</span><span>{{ sub.execution_scope }}</span></template>
                  </p>
                </div>

                <!-- Config fields -->
                <div v-if="Object.keys({ ...(sub.conditions || {}), ...(sub.config || {}) }).length" class="mt-3 pl-7">
                  <p class="text-xs text-slate-500 mb-2">Config</p>
                  <dl class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-x-6 gap-y-2">
                    <div
                      v-for="(val, key) in { ...(sub.conditions || {}), ...(sub.config || {}) }"
                      :key="key"
                      class="min-w-0"
                    >
                      <dt class="text-xs text-slate-500 truncate">{{ key }}</dt>
                      <dd class="text-sm text-slate-200 tabular-nums truncate">{{ val === null ? 'null' : String(val) }}</dd>
                    </div>
                  </dl>
                </div>
              </div>

              <div v-if="!(strategyData.strategies || []).length" class="px-4 py-3 text-sm text-slate-500">
                No sub-strategies defined
              </div>
            </div>
          </div>
        </section>

        <!-- ── Logs ──────────────────────────────────────────────────────────── -->
        <section class="ui-card overflow-hidden" :class="strategyData ? 'mt-3' : 'mt-12'">
          <!-- Header row (always visible) -->
          <div class="min-h-14 px-5 py-2 flex flex-wrap items-center justify-between gap-3">
            <div class="min-w-0">
              <p class="text-sm text-slate-100 truncate">backtest-{{ btId }}.log</p>
              <p v-if="logsData.length || logsSource === 'db'" class="text-xs text-slate-500 mt-0.5 tabular-nums">
                <template v-if="logsData.length">{{ logsData.length }} lines</template>
                <template v-if="logsData.length && logsSource === 'db'"> · </template>
                <template v-if="logsSource === 'db'">last 500 — no full log file</template>
              </p>
            </div>
            <div class="flex items-center gap-1.5">
              <!-- Search (only when open) -->
              <input
                v-if="logsOpen && logsData.length"
                v-model="logsSearch"
                placeholder="Search logs"
                aria-label="Search logs"
                class="ui-input h-8 w-36 sm:w-48 text-[13px]"
              />
              <!-- Copy -->
              <button
                v-if="logsOpen && logsData.length"
                @click="copyLogs"
                class="ui-btn-ghost"
                title="Copy logs"
              >
                <span class="material-symbols-outlined text-[16px]">content_copy</span>
                <span class="hidden sm:inline">Copy</span>
              </button>
              <!-- Download -->
              <button
                v-if="logsOpen && logsData.length"
                @click="downloadLogs"
                class="ui-btn-ghost"
                title="Download log file"
              >
                <span class="material-symbols-outlined text-[16px]">download</span>
                <span class="hidden sm:inline">Download logs</span>
              </button>
              <!-- Toggle -->
              <button
                @click="toggleLogs"
                class="ui-btn"
                :aria-expanded="logsOpen"
              >
                {{ logsOpen ? 'Hide logs' : 'View logs' }}
              </button>
            </div>
          </div>

          <!-- Log terminal body (visible when open) -->
          <div v-if="logsOpen" class="bg-background-dark border-t border-white/[0.06]">
            <!-- Loading -->
            <div v-if="logsLoading" class="px-5 py-5 space-y-2" aria-hidden="true">
              <div class="ui-skel h-3 w-3/5"></div>
              <div class="ui-skel h-3 w-2/5"></div>
              <div class="ui-skel h-3 w-1/2"></div>
            </div>
            <!-- Empty -->
            <div v-else-if="logsData.length === 0" class="px-5 py-6 text-sm text-slate-500">
              No logs available for this backtest
            </div>
            <!-- Log lines (virtualized for large outputs) -->
            <div
              v-else
              class="overflow-y-auto font-mono text-[11px] leading-5 py-1"
              style="max-height: 480px"
              @scroll="onLogScroll"
            >
              <!-- Virtual scroll spacer -->
              <div v-if="virtualLogs.useVirtual" :style="{ height: virtualLogs.totalHeight + 'px', position: 'relative' }">
                <div :style="{ position: 'absolute', top: virtualLogs.offsetY + 'px', left: 0, right: 0 }">
                  <div
                    v-for="(line, idx) in virtualLogs.items"
                    :key="virtualLogs.startIdx + idx"
                    class="log-line flex items-start gap-0"
                  >
                    <template v-if="parseLine(line).time">
                      <span class="shrink-0 w-36 pl-5 pr-3 py-0.5 text-slate-600 select-none whitespace-nowrap tabular-nums">
                        {{ parseLine(line).time.slice(5).replace(' ', ', ') }}
                      </span>
                      <span class="shrink-0 w-12 px-1 py-0.5 text-slate-600 select-none">info</span>
                      <span class="flex-1 px-2 py-0.5 break-all" :class="logLevelClass(line)">
                        {{ parseLine(line).message }}
                      </span>
                    </template>
                    <template v-else>
                      <span class="shrink-0 w-48 select-none"></span>
                      <span class="flex-1 px-2 py-0.5 break-all text-slate-500">{{ line }}</span>
                    </template>
                  </div>
                </div>
              </div>
              <!-- Non-virtual (small log output) -->
              <template v-else>
                <div
                  v-for="(line, idx) in virtualLogs.items"
                  :key="idx"
                  class="log-line flex items-start gap-0"
                >
                  <template v-if="parseLine(line).time">
                    <span class="shrink-0 w-36 pl-5 pr-3 py-0.5 text-slate-600 select-none whitespace-nowrap tabular-nums">
                      {{ parseLine(line).time.slice(5).replace(' ', ', ') }}
                    </span>
                    <span class="shrink-0 w-12 px-1 py-0.5 text-slate-600 select-none">info</span>
                    <span class="flex-1 px-2 py-0.5 break-all" :class="logLevelClass(line)">
                      {{ parseLine(line).message }}
                    </span>
                  </template>
                  <template v-else>
                    <span class="shrink-0 w-48 select-none"></span>
                    <span class="flex-1 px-2 py-0.5 break-all text-slate-500">{{ line }}</span>
                  </template>
                </div>
              </template>
              <!-- No results for search -->
              <div v-if="filteredLogs.length === 0 && logsSearch" class="px-5 py-4 font-display text-sm text-slate-500">
                No lines match “{{ logsSearch }}”
              </div>
            </div>
          </div>
        </section>


        <!-- ── Portfolio value chart ─────────────────────────────────────────── -->
        <section v-if="portfolioSeries[0].data.length" class="mt-12" aria-labelledby="pv-title">
          <!-- Dashboard-style scrubbing header -->
          <div class="flex items-baseline justify-between gap-4">
            <h2 id="pv-title" class="ui-section-title">Portfolio value over time</h2>
            <p v-if="hoveredPortfolioDate" class="text-xs text-slate-500 tabular-nums">
              {{ new Date(hoveredPortfolioDate).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) }}
            </p>
          </div>
          <p class="mt-4 text-[40px] leading-none font-medium tracking-[-0.02em] text-slate-100 tabular-nums">
            {{ fmtMoney(displayPortfolioValue) }}
          </p>
          <p v-if="displayPortfolioPnl != null" class="mt-2 text-sm tabular-nums">
            <span :class="pnlClass(displayPortfolioPnl)">{{ fmtPnl(displayPortfolioPnl) }}</span>
            <span class="text-slate-500 ml-1.5">vs start</span>
          </p>
          <div class="chart mt-6 -ml-2">
            <VueApexCharts type="area" height="300" :series="portfolioSeries" :options="portfolioOpts" />
          </div>
        </section>

        <!-- ── P&L per stock table ─────────────────────────────────────────── -->
        <section v-if="tickers.length && summary.pnl_per_stock && Object.keys(summary.pnl_per_stock).length" class="mt-12" aria-labelledby="pnl-stock-title">
          <h2 id="pnl-stock-title" class="ui-section-title mb-3">P&amp;L per stock</h2>
          <div class="ui-card overflow-x-auto">
            <table class="w-full min-w-[520px] text-sm">
              <thead>
                <tr class="border-b border-white/[0.06] text-left text-xs text-slate-500">
                  <th class="h-10 pl-5 pr-3 font-normal">Stock</th>
                  <th class="h-10 px-3 font-normal text-right">P&amp;L</th>
                  <th class="h-10 px-3 font-normal text-right">P&amp;L %</th>
                  <th class="h-10 pl-3 pr-5 font-normal text-right">Price change</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-white/[0.05]">
                <tr
                  v-for="sym in tickers"
                  :key="sym"
                  class="hover:bg-white/[0.03] transition-colors duration-150 ease-out"
                >
                  <td class="h-14 pl-5 pr-3 font-medium text-slate-100">{{ sym }}</td>
                  <td class="h-14 px-3 text-right tabular-nums" :class="pnlClass(summary.pnl_per_stock?.[sym])">
                    {{ fmtPnl(summary.pnl_per_stock?.[sym]) }}
                  </td>
                  <td class="h-14 px-3 text-right tabular-nums" :class="pnlClass(summary.pnl_percent_per_stock?.[sym])">
                    {{ fmtPct(summary.pnl_percent_per_stock?.[sym]) }}
                  </td>
                  <td class="h-14 pl-3 pr-5 text-right tabular-nums text-slate-400">
                    {{ fmtPct(priceChangePct(summary.stock_price_change?.[sym])) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <!-- ── Per-stock price charts + trade tables ─────────────────────────── -->
        <section v-if="graphData && tickers.length" class="mt-12" aria-labelledby="stock-charts-title">
          <!-- Expand / Collapse controls -->
          <div class="flex items-end justify-between gap-3 mb-3">
            <div>
              <h2 id="stock-charts-title" class="ui-section-title">Stock charts and trades</h2>
              <p class="ui-section-sub tabular-nums">{{ tickers.length }} stocks</p>
            </div>
            <div class="flex items-center gap-2">
              <button @click="expandAllStocks" class="ui-btn">Expand all</button>
              <button
                v-if="expandedStocks.size > 0"
                @click="collapseAllStocks"
                class="ui-btn"
              >Collapse all</button>
            </div>
          </div>

          <div class="space-y-3">
            <article
              v-for="sym in tickers"
              :key="sym"
              class="ui-card overflow-hidden"
            >
              <!-- Clickable header (always visible) -->
              <button
                class="w-full min-h-14 px-5 py-2 flex items-center justify-between gap-3 text-left hover:bg-white/[0.03] transition-colors duration-150 ease-out"
                :aria-expanded="expandedStocks.has(sym)"
                @click="toggleStock(sym)"
              >
                <span class="flex items-baseline gap-3 min-w-0">
                  <span class="text-sm font-medium text-slate-100">{{ sym }}</span>
                  <span class="text-sm tabular-nums" :class="pnlClass(summary.pnl_per_stock?.[sym])">
                    {{ fmtPnl(summary.pnl_per_stock?.[sym]) }} ({{ fmtPct(summary.pnl_percent_per_stock?.[sym]) }})
                  </span>
                  <span class="text-xs text-slate-500 tabular-nums">
                    {{ stockTrades(sym).length }} trades
                  </span>
                </span>
                <span class="flex items-center gap-4 shrink-0">
                  <span class="hidden sm:flex items-center gap-3 text-xs text-slate-500">
                    <span class="inline-flex items-center gap-1.5"><span class="size-1.5 rounded-full bg-gain"></span>Buy</span>
                    <span class="inline-flex items-center gap-1.5"><span class="size-1.5 rounded-full bg-loss"></span>Sell</span>
                  </span>
                  <span
                    class="material-symbols-outlined text-slate-500 text-[18px] transition-transform duration-150 ease-out"
                    :class="expandedStocks.has(sym) ? 'rotate-180' : ''"
                  >expand_more</span>
                </span>
              </button>

              <!-- Lazy-loaded content (only when expanded) -->
              <template v-if="expandedStocks.has(sym)">
                <!-- Price chart (lazy: waits for IntersectionObserver) -->
                <div
                  class="chart px-3 pt-4 pb-2 border-t border-white/[0.06]"
                  :ref="(el) => onChartRef(el, sym)"
                >
                  <template v-if="visibleCharts.has(sym)">
                    <VueApexCharts
                      v-if="stockSeries(sym)[0].data.length"
                      type="line"
                      height="240"
                      :series="stockSeries(sym)"
                      :options="stockOpts(sym)"
                    />
                    <p v-else class="px-2 py-4 text-sm text-slate-500">No price data for {{ sym }}</p>
                  </template>
                  <div v-else class="mx-2 mb-2 h-[232px] rounded-lg bg-raised/50" aria-hidden="true"></div>
                </div>

                <!-- Trade table -->
                <div v-if="stockTrades(sym).length" class="border-t border-white/[0.06] overflow-x-auto">
                  <table class="w-full min-w-[640px] text-sm">
                    <thead>
                      <tr class="border-b border-white/[0.06] text-left text-xs text-slate-500">
                        <th class="h-10 pl-5 pr-3 font-normal">Time</th>
                        <th class="h-10 px-3 font-normal">Action</th>
                        <th class="h-10 px-3 font-normal text-right">Shares</th>
                        <th class="h-10 px-3 font-normal text-right">Price</th>
                        <th class="h-10 px-3 font-normal text-right">Total</th>
                        <th class="h-10 pl-3 pr-5 font-normal text-right">Cash after</th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-white/[0.05]">
                      <tr
                        v-for="(t, i) in stockTrades(sym)"
                        :key="i"
                        class="hover:bg-white/[0.03] transition-colors duration-150 ease-out"
                      >
                        <td class="h-12 pl-5 pr-3 text-slate-400 tabular-nums whitespace-nowrap">{{ fmtDate(t.timestamp) }}</td>
                        <td class="h-12 px-3">
                          <span class="inline-flex items-center gap-2 text-slate-200">
                            <span
                              class="size-1.5 rounded-full shrink-0"
                              :class="(t.action || '').toLowerCase().includes('buy') ? 'bg-gain' : 'bg-loss'"
                            ></span>
                            <span class="inline-block lowercase first-letter:uppercase">{{ t.action }}</span>
                          </span>
                        </td>
                        <td class="h-12 px-3 text-right tabular-nums text-slate-300">{{ t.shares != null ? Number(t.shares).toLocaleString(undefined, { maximumFractionDigits: 4 }) : '—' }}</td>
                        <td class="h-12 px-3 text-right tabular-nums text-slate-300">{{ t.price != null ? fmtMoney(t.price) : '—' }}</td>
                        <td class="h-12 px-3 text-right tabular-nums" :class="(t.action || '').toLowerCase().includes('buy') ? 'text-loss' : 'text-gain'">
                          {{ t.total != null ? fmtMoney(Math.abs(t.total)) : '—' }}
                        </td>
                        <td class="h-12 pl-3 pr-5 text-right tabular-nums text-slate-400">{{ t.cash_after != null ? fmtMoney(t.cash_after) : '—' }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                <!-- Decision trace (paginated) -->
                <div v-if="stockDecisions(sym).length" class="border-t border-white/[0.06] px-5 py-4">
                  <div class="flex items-baseline justify-between gap-3 mb-3">
                    <p class="text-sm font-medium text-slate-100">Decision trace</p>
                    <p class="text-xs text-slate-500 tabular-nums">{{ stockDecisions(sym).length }} evaluations</p>
                  </div>

                  <div class="space-y-3 max-h-[36rem] overflow-y-auto pr-1">
                    <div
                      v-for="(d, i) in visibleDecisions(sym)"
                      :key="`${sym}-decision-${i}-${d.timestamp || 'na'}`"
                      class="rounded-lg border border-white/[0.06] bg-background-dark p-4"
                    >
                      <div class="flex items-start justify-between gap-4 flex-wrap mb-3">
                        <div class="min-w-0">
                          <p class="text-xs text-slate-500 tabular-nums">{{ fmtDate(d.timestamp) }}</p>
                          <p class="flex items-center gap-x-2 gap-y-1 flex-wrap mt-1.5 text-xs text-slate-400">
                            <span class="inline-flex items-center gap-2 text-sm text-slate-100">
                              <span class="size-1.5 rounded-full shrink-0" :class="decisionBadgeClass(d.decision, d.action)"></span>
                              <span class="inline-block lowercase first-letter:uppercase">{{ decisionLabel(d.decision, d.action) }}</span>
                            </span>
                            <span v-if="d.override_applied" class="text-amber-400">
                              Override
                            </span>
                            <span v-if="d.primary_strategy">
                              {{ d.primary_strategy }}
                            </span>
                            <span v-if="d.primary_action_intent" class="text-slate-500">
                              {{ d.primary_action_intent }}
                            </span>
                          </p>
                        </div>
                        <div class="text-right">
                          <p class="text-xs text-slate-500">Weighted score</p>
                          <p class="text-sm text-slate-200 tabular-nums mt-0.5">{{ fmtScore(d.normalized_score) }}</p>
                        </div>
                      </div>

                      <p v-if="d.override_applied && d.pre_override_action" class="text-xs text-slate-400 mb-2">
                        Final action changed from {{ decisionLabel(d.pre_override_decision, d.pre_override_action) }} to {{ decisionLabel(d.decision, d.action) }}.
                      </p>
                      <p v-if="d.final_reason" class="text-sm text-slate-200 whitespace-pre-wrap leading-6 mb-3">
                        {{ d.final_reason }}
                      </p>

                      <div v-if="d.post_decision?.length" class="rounded-lg border border-white/[0.06] px-3 py-2 mb-3">
                        <p class="text-xs text-slate-500 mb-1.5">Post-decision output</p>
                        <div
                          v-for="(post, postIdx) in d.post_decision"
                          :key="`${sym}-post-${i}-${postIdx}`"
                          class="text-xs text-slate-200"
                        >
                          <span class="text-slate-300">{{ post.strategy || 'post_decision' }}</span>
                          <span class="text-slate-600"> → </span>
                          <span class="font-medium">{{ decisionLabel(post.decision) }}</span>
                          <span v-if="post.reason" class="text-slate-400"> · {{ shortReason(post.reason, 220) }}</span>
                        </div>
                      </div>

                      <div v-if="d.strategies?.length" class="grid gap-2 sm:grid-cols-2">
                        <div
                          v-for="(s, strategyIdx) in d.strategies"
                          :key="`${sym}-strategy-${i}-${strategyIdx}`"
                          class="rounded-lg border border-white/[0.06] bg-surface px-3 py-2.5"
                        >
                          <div class="flex items-center justify-between gap-2 mb-1">
                            <p class="text-sm text-slate-200 truncate">{{ s.strategy || 'strategy' }}</p>
                            <span class="inline-flex items-center gap-1.5 text-xs text-slate-300 shrink-0">
                              <span class="size-1.5 rounded-full" :class="decisionBadgeClass(s.decision)"></span>
                              <span class="inline-block lowercase first-letter:uppercase">{{ decisionLabel(s.decision) }}</span>
                            </span>
                          </div>
                          <p class="flex items-center gap-x-1.5 flex-wrap mb-1 text-xs text-slate-500 tabular-nums">
                            <span>Weight {{ fmtWeight(s.weight) }}</span>
                            <template v-if="s.action_intent"><span class="text-slate-600">·</span><span>{{ s.action_intent }}</span></template>
                          </p>
                          <p v-if="s.reason" class="text-xs text-slate-400 leading-5 whitespace-pre-wrap">
                            {{ shortReason(s.reason) }}
                          </p>
                        </div>
                      </div>

                      <details class="mt-3 rounded-lg border border-white/[0.06]">
                        <summary class="cursor-pointer select-none px-3 py-2 text-xs text-slate-400 hover:text-slate-200 transition-colors duration-150 ease-out">
                          Structured JSON
                        </summary>
                        <pre class="overflow-x-auto border-t border-white/[0.06] px-3 py-3 text-[11px] leading-5 text-slate-300">{{ toPrettyJson(d) }}</pre>
                      </details>
                    </div>

                    <!-- Show more button -->
                    <button
                      v-if="remainingDecisions(sym) > 0"
                      @click="showMoreDecisions(sym)"
                      class="w-full h-10 rounded-lg border border-white/[0.06] text-sm text-slate-400 hover:text-slate-100 hover:bg-raised transition-colors duration-150 ease-out"
                    >
                      Show {{ Math.min(DECISIONS_PAGE, remainingDecisions(sym)) }} more
                      <span class="text-slate-500 tabular-nums">· {{ remainingDecisions(sym) }} remaining</span>
                    </button>
                  </div>
                </div>
              </template>
            </article>
          </div>
        </section>

        <!-- ── Extra stats ────────────────────────────────────────────────────── -->
        <section class="mt-12" aria-labelledby="round-trips-title">
          <h2 id="round-trips-title" class="ui-section-title mb-3">Round trip statistics</h2>
          <dl class="ui-card px-5 py-5 grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-5">
            <div>
              <dt class="ui-label">Round trips</dt>
              <dd class="mt-2 text-xl font-medium text-slate-100 tabular-nums leading-none">{{ summary.round_trips ?? '—' }}</dd>
            </div>
            <div>
              <dt class="ui-label">Total round trip P&amp;L</dt>
              <dd class="mt-2 text-xl font-medium tabular-nums leading-none" :class="pnlClass(summary.total_round_trip_pnl)">{{ fmtPnl(summary.total_round_trip_pnl) }}</dd>
            </div>
            <div>
              <dt class="ui-label">Avg winning</dt>
              <dd class="mt-2 text-xl font-medium tabular-nums leading-none text-gain">{{ fmtMoney(summary.avg_winning_round_trip) }}</dd>
            </div>
            <div>
              <dt class="ui-label">Avg losing</dt>
              <dd class="mt-2 text-xl font-medium tabular-nums leading-none text-loss">{{ fmtMoney(summary.avg_losing_round_trip) }}</dd>
            </div>
          </dl>
        </section>

      </template>

      <!-- Pending/no summary yet -->
      <div v-else class="ui-card ui-empty">
        <p class="text-slate-300">This backtest has not completed yet</p>
        <div v-if="progress != null" class="w-56">
          <div class="flex items-center justify-between text-xs text-slate-500 mb-2">
            <span>{{ statusUi(currentStatus).label }}</span>
            <span class="tabular-nums">{{ Math.round(progress) }}%</span>
          </div>
          <div class="bar-track h-1">
            <span class="bar-fill bg-slate-300" :style="{ transform: `scaleX(${Math.min(100, progress) / 100})` }"></span>
          </div>
        </div>
        <p v-else class="text-xs text-slate-500">Status: {{ statusUi(currentStatus).label }}</p>
      </div>

    </main>
  </AppShell>

  <!-- ── Action confirmation modal ─────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showConfirm"
        class="ui-overlay"
        @click.self="closeConfirm"
      >
        <div class="modal-panel ui-modal max-w-sm">

          <div class="ui-modal-head">
            <div class="min-w-0">
              <h2 class="text-base font-medium text-slate-100">{{ confirmMeta.label }}</h2>
              <p class="text-sm text-slate-500 mt-1 tabular-nums">Backtest #{{ btId }}</p>
            </div>
            <button @click="closeConfirm" :disabled="actionBusy" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="ui-modal-body space-y-4">
            <p class="text-sm text-slate-400">{{ confirmMeta.desc }}</p>

            <Transition name="slide-up">
              <p
                v-if="actionMsg"
                :class="actionBusy
                  ? 'text-sm text-slate-400'
                  : actionOk ? 'text-sm text-gain' : 'ui-error'"
              >
                <span v-if="actionBusy" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                  {{ actionMsg }}
                </span>
                <span v-else>{{ actionMsg }}</span>
              </p>
            </Transition>
          </div>

          <div class="ui-modal-foot">
            <button @click="closeConfirm" :disabled="actionBusy" class="ui-btn-ghost h-9 px-4">
              Cancel
            </button>
            <button
              @click="executeAction"
              :disabled="actionBusy"
              :class="['stop', 'delete', 'delete-strategy'].includes(confirmAction) ? 'ui-btn h-9 px-4 text-sm text-loss' : 'ui-btn-primary'"
            >
              <span v-if="actionBusy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ actionBusy ? 'Working…' : confirmMeta.label }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* No colour here: a scoped colour would outrank the gain/loss utilities. */
.stat-label { font-size: 12px; line-height: 16px; color: #71717a; }
.stat-value { margin-top: 8px; font-size: 16px; line-height: 20px; font-weight: 500; font-variant-numeric: tabular-nums; }
.stat-sub { margin-top: 4px; font-size: 12px; line-height: 16px; color: #71717a; font-variant-numeric: tabular-nums; }

.bar-track {
  position: relative;
  display: block;
  border-radius: 999px;
  background: #1e1e20;
  overflow: hidden;
}
.bar-fill {
  position: absolute;
  inset: 0;
  border-radius: 999px;
  transform-origin: left center;
  transition: transform 500ms cubic-bezier(0.22, 1, 0.36, 1);
}

.log-line { border-left: 2px solid transparent; }
.log-line:hover { background: rgba(255, 255, 255, 0.03); border-left-color: rgba(255, 255, 255, 0.16); }

/* Chart axis figures line up like the rest of the page. */
.chart :deep(.apexcharts-text) { font-variant-numeric: tabular-nums; }

.fade-enter-active, .fade-leave-active { transition: opacity 180ms cubic-bezier(0.22, 1, 0.36, 1); }
.fade-enter-active .modal-panel, .fade-leave-active .modal-panel { transition: transform 220ms cubic-bezier(0.22, 1, 0.36, 1); }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.fade-enter-from .modal-panel, .fade-leave-to .modal-panel { transform: translateY(6px); }

.slide-up-enter-active, .slide-up-leave-active { transition: opacity 180ms cubic-bezier(0.22, 1, 0.36, 1), transform 180ms cubic-bezier(0.22, 1, 0.36, 1); }
.slide-up-enter-from, .slide-up-leave-to       { opacity: 0; transform: translateY(4px); }
</style>
