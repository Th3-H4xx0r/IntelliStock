<script setup>
import { ref, shallowRef, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import VueApexCharts from 'vue3-apexcharts'
import BacktestLLMPauseBanner from '../components/BacktestLLMPauseBanner.vue'
import { getToken } from '../utils/auth.js'

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

// ── State ─────────────────────────────────────────────────────────────────────
const summary   = ref(null)
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
  return '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtPnl(v) {
  if (v == null) return '—'
  const n = Number(v)
  return (n >= 0 ? '+' : '') + fmtMoney(n)
}
function fmtPct(v) {
  if (v == null) return '—'
  const n = Number(v)
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'
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
  return Number(v) >= 0 ? 'text-emerald-400' : 'text-red-400'
}

const statusColors = {
  completed:            'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  finished:             'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  running:              'text-sky-400 bg-sky-500/10 border-sky-500/20',
  queued:               'text-amber-400 bg-amber-500/10 border-amber-500/20',
  paused:               'text-violet-400 bg-violet-500/10 border-violet-500/20',
  paused_llm_critical:  'text-amber-400 bg-amber-500/10 border-amber-500/20',
  aborted_llm_failure:  'text-red-400 bg-red-500/10 border-red-500/20',
  stopped:              'text-red-400 bg-red-500/10 border-red-500/20',
  cancelled:            'text-red-400 bg-red-500/10 border-red-500/20',
  error:                'text-red-400 bg-red-500/10 border-red-500/20',
  failed:               'text-red-400 bg-red-500/10 border-red-500/20',
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
    case 'stop':   return { label: 'Stop Backtest',   color: 'red',     icon: 'stop_circle',    desc: 'This will permanently stop the backtest. This cannot be undone.' }
    case 'pause':  return { label: 'Pause Backtest',  color: 'violet',  icon: 'pause_circle',   desc: 'The backtest will be paused and can be resumed later.' }
    case 'resume': return { label: 'Resume Backtest', color: 'sky',     icon: 'play_circle',    desc: 'The backtest will continue from where it was paused.' }
    case 'rerun':  return { label: 'Rerun Backtest',  color: 'emerald', icon: 'replay',         desc: `A new backtest will be created with the same settings — ${tickers} · ${dates} · $${(s?.initial_cash ?? 100000).toLocaleString()} initial cash. You will be redirected to the new backtest.` }
    case 'delete':          return { label: 'Delete Backtest',  color: 'red',   icon: 'delete_forever', desc: 'This will permanently delete all results and data for this backtest. This cannot be undone.' }
    case 'delete-strategy': return { label: 'Delete Strategy',  color: 'red',   icon: 'delete_forever', desc: `This will permanently delete the strategy "${strategyData.value?.name || ''}" and all its configuration. This cannot be undone.` }
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
      actionMsg.value = `New backtest #${newId} created! Redirecting...`
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
function statusClass(s) {
  return statusColors[(s || '').toLowerCase()] || 'text-slate-500 bg-slate-500/10 border-slate-700'
}

// ── Computed state ─────────────────────────────────────────────────────────────
const currentStatus = computed(() => statusData.value?.status || summary.value?.status || 'unknown')
const progress      = computed(() => statusData.value?.progress ?? null)
const elapsedSeconds = computed(() => statusData.value?.time_elapsed_seconds ?? summary.value?.time_elapsed_seconds ?? null)
const nexusLookback = computed(() => statusData.value?.nexus_lookback ?? null)
const tickers       = computed(() => summary.value?.tickers || [])

// ── Chart helpers ─────────────────────────────────────────────────────────────
const DARK_GRID   = '#1e2535'
const AXIS_COLOR  = '#64748b'

const baseChartOpts = {
  theme: { mode: 'dark' },
  chart: { background: 'transparent', toolbar: { show: false }, animations: { enabled: false } },
  grid:  { borderColor: DARK_GRID, strokeDashArray: 3 },
  legend: { labels: { colors: '#94a3b8' } },
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
  stroke:  { curve: 'smooth', width: 2.5 },
  fill:    { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.3, opacityTo: 0.03 } },
  colors:  ['#38bdf8'],
  xaxis:   {
    type: 'datetime',
    labels: { style: { colors: AXIS_COLOR } },
    crosshairs: { show: true, stroke: { color: '#38bdf8', width: 1, dashArray: 3 } },
  },
  yaxis:   {
    labels: {
      formatter: v => '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 }),
      style: { colors: AXIS_COLOR },
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
      borderColor: '#475569',
      strokeDashArray: 4,
      label: { text: 'Start', style: { color: '#94a3b8', background: 'transparent' } },
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
    // stroke width 0 for Buy/Sell hides the connecting line, leaving only markers
    stroke:  { width: [2, 0, 0], curve: 'smooth' },
    fill:    {
      type: ['gradient', 'solid', 'solid'],
      gradient: { shadeIntensity: 1, opacityFrom: 0.18, opacityTo: 0.01 },
      opacity: [1, 0, 0],
    },
    markers: {
      size:         [0, 6, 6],
      shape:        ['circle', 'circle', 'circle'],
      strokeWidth:  [0, 4, 4],
      strokeColors: ['transparent', '#34d39940', '#f8717140'],
      fillOpacity:  1,
      hover:        { sizeOffset: 2 },
    },
    colors:  ['#0ea5e9', '#34d399', '#f87171'],
    xaxis:   { type: 'datetime', labels: { style: { colors: AXIS_COLOR } },
               crosshairs: { show: true, stroke: { color: '#475569', width: 1, dashArray: 3 } } },
    yaxis:   { labels: { formatter: v => '$' + Number(v).toFixed(2), style: { colors: AXIS_COLOR } } },
    tooltip: {
      shared: true,
      intersect: false,
      x: { format: 'MMM dd yyyy HH:mm' },
      y: { formatter: (v, { seriesIndex }) => {
        if (v == null) return undefined
        const labels = ['Price', 'Buy', 'Sell']
        return `<span style="color:${['#38bdf8','#34d399','#f87171'][seriesIndex]}">${labels[seriesIndex] ?? ''}</span> $${Number(v).toFixed(2)}`
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

function decisionBadgeClass(decision, action) {
  const label = decisionLabel(decision, action)
  if (label === 'BUY') return 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
  if (label === 'SELL') return 'bg-red-500/10 text-red-400 border border-red-500/20'
  return 'bg-slate-500/10 text-slate-300 border border-slate-500/20'
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
  if (l.includes('error') || l.includes('fail') || l.includes('exception')) return 'text-red-400'
  if (l.includes('warn') || l.includes('yellow') || l.includes('skip')) return 'text-amber-400'
  if (l.includes('success') || l.includes('finished') || l.includes('profit') || l.includes('passed')) return 'text-emerald-400'
  if (l.includes('broker')) return 'text-sky-300'
  return 'text-slate-300'
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
    <main class="flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-10 max-w-7xl">

      <!-- Breadcrumb -->
      <div class="flex items-center gap-2 text-sm text-slate-500 mb-6">
        <button @click="router.push(fromPath)" class="hover:text-primary transition-colors flex items-center gap-1">
          <span class="material-symbols-outlined text-[16px]">arrow_back</span>
          Back
        </button>
        <span>/</span>
        <span class="text-slate-300">Backtest #{{ btId }}</span>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="flex items-center gap-3 text-slate-400 text-sm">
        <span class="material-symbols-outlined animate-spin text-xl">progress_activity</span>
        Loading backtest...
      </div>

      <!-- Error -->
      <div v-else-if="error" class="rounded-xl bg-red-500/10 border border-red-500/20 px-5 py-4 text-red-400 text-sm">
        {{ error }}
      </div>

      <template v-else-if="summary">

        <!-- ── Header ────────────────────────────────────────────────────────── -->
        <div class="flex flex-wrap items-start justify-between gap-4 mb-8">
          <div class="flex items-center gap-4 min-w-0">
            <div class="size-12 rounded-2xl bg-surface border border-border-subtle flex items-center justify-center shrink-0">
              <span class="material-symbols-outlined text-sky-400 text-2xl">analytics</span>
            </div>
            <div class="min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <h1 class="text-2xl font-bold">Backtest #{{ btId }}</h1>
                <span class="px-2 py-0.5 rounded border text-xs font-bold uppercase" :class="statusClass(currentStatus)">
                  {{ currentStatus }}
                </span>
              </div>
              <p class="text-xs text-slate-500 mt-0.5">
                {{ summary.start_date }} → {{ summary.end_date }}
                <span v-if="tickers.length" class="ml-2 font-mono">{{ tickers.join(', ') }}</span>
              </p>
            </div>
          </div>

          <!-- Action controls -->
          <div class="flex items-center gap-2 shrink-0 flex-wrap">
            <!-- Pause (only when running) -->
            <button
              v-if="currentStatus.toLowerCase() === 'running'"
              @click="openConfirm('pause')"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-violet-400 hover:bg-violet-500/10 border border-violet-500/20 transition-colors"
            >
              <span class="material-symbols-outlined text-[15px]">pause_circle</span>
              Pause
            </button>
            <!-- Resume (only when paused) -->
            <button
              v-if="currentStatus.toLowerCase() === 'paused'"
              @click="openConfirm('resume')"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-sky-400 hover:bg-sky-500/10 border border-sky-500/20 transition-colors"
            >
              <span class="material-symbols-outlined text-[15px]">play_circle</span>
              Resume
            </button>
            <!-- Stop (when running or paused) -->
            <button
              v-if="['running','paused','queued'].includes(currentStatus.toLowerCase())"
              @click="openConfirm('stop')"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-red-400 hover:bg-red-500/10 border border-red-500/20 transition-colors"
            >
              <span class="material-symbols-outlined text-[15px]">stop_circle</span>
              Stop
            </button>
            <!-- Rerun -->
            <button
              v-if="summary"
              @click="openConfirm('rerun')"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-emerald-400 hover:bg-emerald-500/10 border border-emerald-500/20 transition-colors"
            >
              <span class="material-symbols-outlined text-[15px]">replay</span>
              Rerun
            </button>
            <!-- Live Playback -->
            <button
              v-if="summary"
              @click="router.push({ name: 'backtest-playback', params: { id: btId } })"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-amber-400 hover:bg-amber-500/10 border border-amber-500/20 transition-colors"
            >
              <span class="material-symbols-outlined text-[15px]">movie</span>
              Live Playback
            </button>
            <!-- Delete -->
            <button
              v-if="summary"
              @click="openConfirm('delete')"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-red-400 hover:bg-red-500/10 border border-red-500/20 transition-colors"
            >
              <span class="material-symbols-outlined text-[15px]">delete_forever</span>
              Delete
            </button>
          </div>

          <!-- Running: progress bar -->
          <div v-if="progress != null && ['running','queued','pending'].includes(currentStatus.toLowerCase())" class="shrink-0 w-48">
            <div class="flex items-center justify-between text-xs text-slate-400 mb-1">
              <span>Progress</span>
              <span class="font-mono font-semibold">{{ Math.round(progress) }}%</span>
            </div>
            <div class="h-2 bg-surface rounded-full overflow-hidden">
              <div
                class="h-full bg-sky-400 rounded-full transition-all duration-500"
                :style="{ width: Math.min(100, Math.max(0, progress)) + '%' }"
              ></div>
            </div>
          </div>
        </div>

        <!-- ── LLM-critical pause banner ────────────────────────────────────── -->
        <BacktestLLMPauseBanner :summary="summary" />

        <!-- ── Nexus Lookback Training Banner ───────────────────────────────── -->
        <div
          v-if="nexusLookback"
          class="mb-6 rounded-2xl border border-violet-500/30 bg-violet-500/5 px-5 py-4"
        >
          <div class="flex items-center gap-2 mb-3">
            <span class="material-symbols-outlined text-violet-400 text-xl animate-pulse">hub</span>
            <span class="font-semibold text-violet-300 text-sm">Nexus Lookback Training</span>
            <span class="ml-auto font-mono text-xs text-slate-400">
              Day {{ nexusLookback.current }} / {{ nexusLookback.total }}
            </span>
          </div>
          <div class="h-2 bg-surface rounded-full overflow-hidden mb-2">
            <div
              class="h-full bg-violet-500 rounded-full transition-all duration-700"
              :style="{ width: (nexusLookback.total > 0 ? Math.round(nexusLookback.current / nexusLookback.total * 100) : 0) + '%' }"
            ></div>
          </div>
          <div class="flex items-center justify-between text-[11px] text-slate-500 font-mono">
            <span>{{ nexusLookback.start_date }}</span>
            <span class="text-violet-400 font-semibold">▶ {{ nexusLookback.current_date }}</span>
            <span>{{ nexusLookback.end_date }}</span>
          </div>
          <p class="mt-2 text-[11px] text-slate-500">Building historical event context before trading begins. This runs once per scope.</p>
        </div>

        <!-- ── Stats grid ─────────────────────────────────────────────────────── -->
        <div class="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-4 mb-8">
          <div class="glass-card rounded-2xl p-4">
            <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-1">Total P&L</p>
            <p class="text-xl font-bold font-mono" :class="pnlClass(summary.pnl)">{{ fmtPnl(summary.pnl) }}</p>
          </div>
          <div class="glass-card rounded-2xl p-4">
            <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-1">P&L %</p>
            <p class="text-xl font-bold font-mono" :class="pnlClass(summary.pnl_percent)">{{ fmtPct(summary.pnl_percent) }}</p>
          </div>
          <div class="glass-card rounded-2xl p-4">
            <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-1">Portfolio</p>
            <p class="text-sm font-mono text-slate-300">{{ fmtMoney(summary.portfolio_start_value) }}</p>
            <p class="text-[10px] text-slate-500">→ <span class="font-mono" :class="pnlClass((summary.portfolio_end_value || 0) - (summary.portfolio_start_value || 0))">{{ fmtMoney(summary.portfolio_end_value) }}</span></p>
          </div>
          <div class="glass-card rounded-2xl p-4">
            <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-1">Trades</p>
            <p class="text-xl font-bold text-slate-200">{{ summary.total_trades ?? '—' }}</p>
            <p class="text-[10px] text-slate-500">{{ summary.total_buys ?? 0 }} buy / {{ summary.total_sells ?? 0 }} sell</p>
          </div>
          <div class="glass-card rounded-2xl p-4">
            <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-1">Elapsed</p>
            <p class="text-xl font-bold text-slate-200 font-mono">{{ fmtElapsed(elapsedSeconds) }}</p>
          </div>
          <div class="glass-card rounded-2xl p-4">
            <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-1">Win Rate</p>
            <p class="text-xl font-bold" :class="(summary.win_rate_percent || 0) >= 50 ? 'text-emerald-400' : 'text-red-400'">
              {{ summary.win_rate_percent != null ? summary.win_rate_percent.toFixed(1) + '%' : '—' }}
            </p>
            <p class="text-[10px] text-slate-500">{{ summary.winning_round_trips ?? 0 }}W / {{ summary.losing_round_trips ?? 0 }}L</p>
          </div>
          <div class="glass-card rounded-2xl p-4">
            <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-1">Portfolio High</p>
            <p class="text-sm font-mono text-emerald-400">{{ fmtMoney(summary.portfolio_value_high) }}</p>
            <p class="text-[10px] text-slate-500">Low: <span class="font-mono text-red-400">{{ fmtMoney(summary.portfolio_value_low) }}</span></p>
          </div>
        </div>

        <!-- ── AI credits / LLM cost card ────────────────────────────────────── -->
        <section class="glass-card rounded-2xl overflow-hidden mb-6 border border-border-subtle">
          <div class="flex items-center justify-between gap-3 px-5 py-4 border-b border-border-subtle">
            <div class="flex items-center gap-3 min-w-0">
              <div class="size-8 rounded-xl bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-primary text-base">payments</span>
              </div>
              <div class="text-left min-w-0">
                <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-0.5">AI Credits</p>
                <p class="text-sm font-semibold text-slate-200 truncate">LLM cost for this backtest</p>
              </div>
            </div>
            <button
              v-if="!llmCostLoading"
              @click="fetchLlmCost"
              class="inline-flex items-center justify-center rounded-lg border border-border-subtle bg-surface/60 px-2 py-1 text-slate-400 transition-colors hover:bg-surface hover:text-slate-100"
              title="Refresh"
            >
              <span class="material-symbols-outlined text-[16px]">refresh</span>
            </button>
            <span v-else class="material-symbols-outlined text-[18px] text-slate-400 animate-spin">refresh</span>
          </div>
          <div v-if="llmCostError" class="px-5 py-4 text-sm text-rose-300">
            {{ llmCostError }}
          </div>
          <div v-else-if="!llmCost && llmCostLoading" class="px-5 py-6 text-sm text-slate-500">
            Loading…
          </div>
          <div v-else-if="!llmCost || !llmCost.total_calls" class="px-5 py-6 text-sm text-slate-500">
            No LLM calls were attributed to this backtest. (Either the strategy ran with no LLM stages
            enabled, or the backtest finished before the telemetry sink flushed.)
          </div>
          <div v-else>
            <!-- Headline totals row -->
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-4 px-5 py-4 border-b border-border-subtle">
              <div>
                <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-1">Total cost</p>
                <p class="text-2xl font-bold text-slate-100 font-mono">{{ fmtUsdCost(llmCost.total_cost_usd) }}</p>
              </div>
              <div>
                <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-1">Calls</p>
                <p class="text-2xl font-bold text-slate-200 font-mono">{{ llmCost.total_calls }}</p>
                <p class="text-[10px] text-slate-500">
                  <span class="text-emerald-400">{{ llmCost.ok_calls }} ok</span>
                  <span v-if="llmCost.failed_calls > 0"> · <span class="text-amber-300">{{ llmCost.failed_calls }} failed</span></span>
                </p>
              </div>
              <div>
                <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-1">Input tokens</p>
                <p class="text-2xl font-bold text-slate-200 font-mono">{{ fmtTokens(llmCost.total_input_tokens) }}</p>
              </div>
              <div>
                <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-1">Output tokens</p>
                <p class="text-2xl font-bold text-slate-200 font-mono">{{ fmtTokens(llmCost.total_output_tokens) }}</p>
                <p v-if="llmCost.total_reasoning_tokens > 0" class="text-[10px] text-slate-500">
                  incl. {{ fmtTokens(llmCost.total_reasoning_tokens) }} reasoning
                </p>
              </div>
            </div>
            <!-- Breakdown tables: model, stage, strategy -->
            <div class="grid grid-cols-1 lg:grid-cols-3 gap-0 divide-x divide-border-subtle">
              <div class="px-5 py-4">
                <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-3">By model</p>
                <div class="space-y-2">
                  <div
                    v-for="row in llmCost.by_model.slice(0, 6)"
                    :key="`m-${row.key}`"
                    class="flex items-center justify-between gap-2 text-sm"
                  >
                    <span class="font-mono text-xs text-slate-300 truncate min-w-0">{{ row.key }}</span>
                    <span class="text-slate-100 font-mono shrink-0">{{ fmtUsdCost(row.cost_usd) }}</span>
                  </div>
                  <p v-if="!llmCost.by_model.length" class="text-xs text-slate-500">No models recorded.</p>
                </div>
              </div>
              <div class="px-5 py-4">
                <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-3">By call site</p>
                <div class="space-y-2">
                  <div
                    v-for="row in llmCost.by_call_site.slice(0, 6)"
                    :key="`c-${row.key}`"
                    class="flex items-center justify-between gap-2 text-sm"
                  >
                    <span class="font-mono text-xs text-slate-300 truncate min-w-0">{{ row.key }}</span>
                    <span class="text-slate-100 font-mono shrink-0">{{ fmtUsdCost(row.cost_usd) }}</span>
                  </div>
                  <p v-if="!llmCost.by_call_site.length" class="text-xs text-slate-500">No call sites recorded.</p>
                </div>
              </div>
              <div class="px-5 py-4">
                <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-3">By provider</p>
                <div class="space-y-2">
                  <div
                    v-for="row in llmCost.by_provider.slice(0, 6)"
                    :key="`p-${row.key}`"
                    class="flex items-center justify-between gap-2 text-sm"
                  >
                    <span class="font-mono text-xs text-slate-300 truncate min-w-0">{{ row.key }}</span>
                    <span class="text-slate-100 font-mono shrink-0">{{ fmtUsdCost(row.cost_usd) }}</span>
                  </div>
                  <p v-if="!llmCost.by_provider.length" class="text-xs text-slate-500">No providers recorded.</p>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- ── Strategy info ─────────────────────────────────────────────────── -->
        <section v-if="strategyData" class="glass-card rounded-2xl overflow-hidden mb-6">
          <!-- Clickable header -->
          <button
            class="w-full flex items-center justify-between gap-4 px-5 py-4 hover:bg-white/[0.02] transition-colors"
            @click="strategyOpen = !strategyOpen"
          >
            <div class="flex items-center gap-3 min-w-0">
              <div class="size-8 rounded-xl bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-primary text-base">schema</span>
              </div>
              <div class="text-left min-w-0">
                <p class="text-[10px] text-slate-500 uppercase tracking-widest mb-0.5">Strategy</p>
                <p class="text-sm font-semibold text-slate-200 truncate">{{ strategyData?.name || '—' }}</p>
              </div>
            </div>
            <span class="material-symbols-outlined text-slate-500 text-[18px] shrink-0 transition-transform" :class="strategyOpen ? 'rotate-180' : ''">
              expand_more
            </span>
          </button>

          <!-- Collapsible body -->
          <div v-if="strategyOpen && strategyData" class="border-t border-border-subtle px-5 pb-5 pt-4 space-y-3">
            <p v-if="strategyData.id" class="text-[10px] text-slate-600 font-mono -mt-1 mb-2">ID: {{ strategyData.id }}</p>

            <p class="text-[10px] font-bold uppercase tracking-widest text-slate-500">Sub-strategies</p>
            <div
              v-for="(sub, idx) in (strategyData.strategies || [])"
              :key="idx"
              class="rounded-xl border border-border-subtle bg-surface/40 p-4 space-y-3"
            >
              <!-- Sub-strategy header -->
              <div class="flex items-center justify-between gap-3 flex-wrap">
                <div class="flex items-center gap-2">
                  <span class="size-5 rounded-md bg-primary/10 text-primary text-[10px] font-bold flex items-center justify-center border border-primary/20">
                    {{ idx + 1 }}
                  </span>
                  <span class="text-sm font-semibold text-slate-100">{{ sub.strategy }}</span>
                </div>
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="px-2 py-0.5 rounded-md bg-surface border border-border-subtle text-xs font-mono text-slate-300">
                    {{ (sub.weight * 100).toFixed(0) }}%
                  </span>
                  <span v-if="sub.execution_position != null" class="px-2 py-0.5 rounded-md bg-surface border border-border-subtle text-xs text-slate-400 font-mono">
                    pos {{ sub.execution_position }}
                  </span>
                  <span v-if="sub.decision_phase" class="px-2 py-0.5 rounded-md bg-surface border border-border-subtle text-xs text-slate-400">
                    {{ sub.decision_phase }}
                  </span>
                  <span v-if="sub.execution_scope" class="px-2 py-0.5 rounded-md bg-surface border border-border-subtle text-xs text-slate-400">
                    {{ sub.execution_scope }}
                  </span>
                </div>
              </div>

              <!-- Config fields -->
              <div v-if="Object.keys({ ...(sub.conditions || {}), ...(sub.config || {}) }).length" class="space-y-1.5">
                <p class="text-[10px] uppercase tracking-wider text-slate-600 font-semibold">Config</p>
                <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
                  <div
                    v-for="(val, key) in { ...(sub.conditions || {}), ...(sub.config || {}) }"
                    :key="key"
                    class="rounded-lg bg-[#04040c] border border-border-subtle px-3 py-2"
                  >
                    <p class="text-[10px] text-slate-600 mb-0.5 truncate">{{ key }}</p>
                    <p class="text-xs font-mono text-slate-300 truncate">{{ val === null ? 'null' : String(val) }}</p>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="!(strategyData.strategies || []).length" class="text-xs text-slate-600 italic">
              No sub-strategies defined
            </div>
          </div>
        </section>

        <!-- ── Logs ──────────────────────────────────────────────────────────── -->
        <div class="glass-card rounded-2xl border border-border-subtle overflow-hidden mb-6">
          <!-- Header row (always visible) -->
          <div class="flex items-center justify-between gap-3 px-4 py-3 bg-[#0d1117]">
            <div class="flex items-center gap-2">
              <span class="size-2 rounded-full" :class="logsOpen ? 'bg-emerald-400' : 'bg-slate-600'"></span>
              <span class="text-xs font-semibold text-slate-300 font-mono">backtest-{{ btId }}.log</span>
              <span v-if="logsData.length" class="text-[10px] text-slate-600 font-mono">{{ logsData.length }} lines</span>
              <span v-if="logsSource === 'db'" class="text-[10px] text-amber-500/70 ml-1">(last 500 — no full log file)</span>
            </div>
            <div class="flex items-center gap-1.5">
              <!-- Search (only when open) -->
              <input
                v-if="logsOpen && logsData.length"
                v-model="logsSearch"
                placeholder="Search logs..."
                class="w-36 sm:w-48 bg-[#161b22] border border-[#30363d] rounded px-2 py-0.5 text-[11px] text-slate-300 placeholder-slate-600 focus:outline-none focus:border-sky-500/50 font-mono"
              />
              <!-- Copy -->
              <button
                v-if="logsOpen && logsData.length"
                @click="copyLogs"
                class="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] text-slate-400 hover:text-slate-200 hover:bg-white/5 transition-colors"
                title="Copy logs"
              >
                <span class="material-symbols-outlined text-[13px]">content_copy</span>
                <span class="hidden sm:inline">Copy</span>
              </button>
              <!-- Download -->
              <button
                v-if="logsOpen && logsData.length"
                @click="downloadLogs"
                class="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] text-slate-400 hover:text-slate-200 hover:bg-white/5 transition-colors"
                title="Download log file"
              >
                <span class="material-symbols-outlined text-[13px]">download</span>
                <span class="hidden sm:inline">Download logs</span>
              </button>
              <!-- Toggle -->
              <button
                @click="toggleLogs"
                class="inline-flex items-center gap-1.5 px-3 py-1 rounded text-[11px] font-semibold border transition-colors"
                :class="logsOpen
                  ? 'border-sky-500/30 bg-sky-500/10 text-sky-400 hover:bg-sky-500/20'
                  : 'border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface'"
              >
                <span class="material-symbols-outlined text-[13px]">{{ logsOpen ? 'visibility_off' : 'terminal' }}</span>
                {{ logsOpen ? 'Hide Logs' : 'View Logs' }}
              </button>
            </div>
          </div>

          <!-- Log terminal body (visible when open) -->
          <div v-if="logsOpen" class="bg-[#0d1117] border-t border-[#21262d]">
            <!-- Loading -->
            <div v-if="logsLoading" class="flex items-center gap-2 px-4 py-6 text-slate-500 text-xs font-mono">
              <span class="material-symbols-outlined text-[14px] animate-spin">progress_activity</span>
              Loading logs...
            </div>
            <!-- Empty -->
            <div v-else-if="logsData.length === 0" class="px-4 py-6 text-slate-600 text-xs font-mono italic">
              No logs available for this backtest.
            </div>
            <!-- Log lines (virtualized for large outputs) -->
            <div
              v-else
              class="overflow-y-auto font-mono text-[11px] leading-5"
              style="max-height: 480px"
              @scroll="onLogScroll"
            >
              <!-- Virtual scroll spacer -->
              <div v-if="virtualLogs.useVirtual" :style="{ height: virtualLogs.totalHeight + 'px', position: 'relative' }">
                <div :style="{ position: 'absolute', top: virtualLogs.offsetY + 'px', left: 0, right: 0 }">
                  <div
                    v-for="(line, idx) in virtualLogs.items"
                    :key="virtualLogs.startIdx + idx"
                    class="flex items-start gap-0 group hover:bg-white/[0.03] border-l-2 border-transparent hover:border-sky-500/40"
                  >
                    <template v-if="parseLine(line).time">
                      <span class="shrink-0 w-36 px-3 py-0.5 text-[10px] text-slate-600 select-none whitespace-nowrap">
                        {{ parseLine(line).time.slice(5).replace(' ', ', ') }}
                      </span>
                      <span class="shrink-0 w-12 px-1 py-0.5">
                        <span class="inline-block px-1.5 py-px rounded text-[9px] font-bold bg-sky-500/15 text-sky-400 border border-sky-500/20 select-none">info</span>
                      </span>
                      <span class="flex-1 px-2 py-0.5 break-all" :class="logLevelClass(line)">
                        {{ parseLine(line).message }}
                      </span>
                    </template>
                    <template v-else>
                      <span class="shrink-0 w-48 select-none"></span>
                      <span class="flex-1 px-2 py-0.5 break-all text-slate-500 italic">{{ line }}</span>
                    </template>
                  </div>
                </div>
              </div>
              <!-- Non-virtual (small log output) -->
              <template v-else>
                <div
                  v-for="(line, idx) in virtualLogs.items"
                  :key="idx"
                  class="flex items-start gap-0 group hover:bg-white/[0.03] border-l-2 border-transparent hover:border-sky-500/40"
                >
                  <template v-if="parseLine(line).time">
                    <span class="shrink-0 w-36 px-3 py-0.5 text-[10px] text-slate-600 select-none whitespace-nowrap">
                      {{ parseLine(line).time.slice(5).replace(' ', ', ') }}
                    </span>
                    <span class="shrink-0 w-12 px-1 py-0.5">
                      <span class="inline-block px-1.5 py-px rounded text-[9px] font-bold bg-sky-500/15 text-sky-400 border border-sky-500/20 select-none">info</span>
                    </span>
                    <span class="flex-1 px-2 py-0.5 break-all" :class="logLevelClass(line)">
                      {{ parseLine(line).message }}
                    </span>
                  </template>
                  <template v-else>
                    <span class="shrink-0 w-48 select-none"></span>
                    <span class="flex-1 px-2 py-0.5 break-all text-slate-500 italic">{{ line }}</span>
                  </template>
                </div>
              </template>
              <!-- No results for search -->
              <div v-if="filteredLogs.length === 0 && logsSearch" class="px-4 py-4 text-slate-600 text-xs italic">
                No lines match "{{ logsSearch }}"
              </div>
            </div>
          </div>
        </div>


        <!-- ── Portfolio value chart ─────────────────────────────────────────── -->
        <section v-if="portfolioSeries[0].data.length" class="glass-card rounded-2xl p-5 mb-6">
          <!-- Dashboard-style scrubbing header -->
          <div class="flex items-end justify-between mb-1">
            <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Portfolio Value Over Time</p>
            <p v-if="hoveredPortfolioDate" class="text-xs text-slate-500">
              {{ new Date(hoveredPortfolioDate).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) }}
            </p>
          </div>
          <div class="flex items-baseline gap-3 mb-5">
            <p class="text-3xl font-bold font-mono text-white transition-all">
              {{ fmtMoney(displayPortfolioValue) }}
            </p>
            <p v-if="displayPortfolioPnl != null" class="text-sm font-semibold font-mono transition-all" :class="pnlClass(displayPortfolioPnl)">
              {{ fmtPnl(displayPortfolioPnl) }}
              <span class="text-xs font-normal opacity-70 ml-0.5">vs start</span>
            </p>
          </div>
          <VueApexCharts type="area" height="240" :series="portfolioSeries" :options="portfolioOpts" />
        </section>

        <!-- ── P&L per stock table ─────────────────────────────────────────── -->
        <section v-if="tickers.length && summary.pnl_per_stock && Object.keys(summary.pnl_per_stock).length" class="glass-card rounded-2xl overflow-hidden mb-6">
          <div class="px-5 py-4 border-b border-border-subtle">
            <p class="text-xs font-bold uppercase tracking-widest text-slate-500">P&L per Stock</p>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-xs">
              <thead>
                <tr class="border-b border-border-subtle text-left">
                  <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider">Stock</th>
                  <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider text-right">P&L</th>
                  <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider text-right">P&L %</th>
                  <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider text-right">Price Change</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="sym in tickers"
                  :key="sym"
                  class="border-b border-border-subtle/50 hover:bg-surface/30 transition-colors"
                >
                  <td class="px-4 py-3 font-mono font-bold text-slate-200">{{ sym }}</td>
                  <td class="px-4 py-3 text-right font-mono font-semibold" :class="pnlClass(summary.pnl_per_stock?.[sym])">
                    {{ fmtPnl(summary.pnl_per_stock?.[sym]) }}
                  </td>
                  <td class="px-4 py-3 text-right font-mono" :class="pnlClass(summary.pnl_percent_per_stock?.[sym])">
                    {{ fmtPct(summary.pnl_percent_per_stock?.[sym]) }}
                  </td>
                  <td class="px-4 py-3 text-right font-mono" :class="pnlClass(summary.stock_price_change?.[sym])">
                    {{ fmtPct(summary.stock_price_change?.[sym]) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <!-- ── Per-stock price charts + trade tables ─────────────────────────── -->
        <template v-if="graphData && tickers.length">
          <!-- Expand / Collapse controls -->
          <div class="flex items-center justify-between gap-3 mb-4">
            <p class="text-xs font-bold uppercase tracking-widest text-slate-500">
              Stock Charts &amp; Trades
              <span class="text-slate-600 font-normal normal-case ml-1">({{ tickers.length }} stocks)</span>
            </p>
            <div class="flex items-center gap-2">
              <button
                @click="expandAllStocks"
                class="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium text-slate-400 hover:text-slate-200 hover:bg-surface border border-border-subtle transition-colors"
              >
                <span class="material-symbols-outlined text-[13px]">unfold_more</span>
                Expand All
              </button>
              <button
                v-if="expandedStocks.size > 0"
                @click="collapseAllStocks"
                class="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium text-slate-400 hover:text-slate-200 hover:bg-surface border border-border-subtle transition-colors"
              >
                <span class="material-symbols-outlined text-[13px]">unfold_less</span>
                Collapse All
              </button>
            </div>
          </div>

          <section
            v-for="sym in tickers"
            :key="sym"
            class="glass-card rounded-2xl overflow-hidden mb-4"
          >
            <!-- Clickable header (always visible) -->
            <button
              class="w-full px-5 py-4 flex items-center justify-between gap-3 hover:bg-white/[0.02] transition-colors cursor-pointer"
              @click="toggleStock(sym)"
            >
              <div class="flex items-center gap-3 min-w-0">
                <span class="font-mono font-bold text-slate-100 text-sm">{{ sym }}</span>
                <span class="text-xs" :class="pnlClass(summary.pnl_per_stock?.[sym])">
                  {{ fmtPnl(summary.pnl_per_stock?.[sym]) }} ({{ fmtPct(summary.pnl_percent_per_stock?.[sym]) }})
                </span>
                <span class="text-[10px] text-slate-600 font-mono">
                  {{ stockTrades(sym).length }} trades
                </span>
              </div>
              <div class="flex items-center gap-3 shrink-0">
                <div class="flex items-center gap-3 text-xs text-slate-500">
                  <span class="flex items-center gap-1"><span class="size-2.5 rounded-full bg-emerald-400 inline-block"></span> Buy</span>
                  <span class="flex items-center gap-1"><span class="size-2.5 rounded bg-red-400 inline-block"></span> Sell</span>
                </div>
                <span
                  class="material-symbols-outlined text-slate-500 text-[18px] transition-transform duration-200"
                  :class="expandedStocks.has(sym) ? 'rotate-180' : ''"
                >expand_more</span>
              </div>
            </button>

            <!-- Lazy-loaded content (only when expanded) -->
            <template v-if="expandedStocks.has(sym)">
              <!-- Price chart (lazy: waits for IntersectionObserver) -->
              <div
                class="px-5 py-4 border-t border-border-subtle"
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
                  <p v-else class="text-xs text-slate-600 italic py-4">No price data for {{ sym }}</p>
                </template>
                <div v-else class="flex items-center justify-center py-16 text-slate-600 text-xs">
                  <span class="material-symbols-outlined text-base animate-spin mr-2">progress_activity</span>
                  Loading chart...
                </div>
              </div>

              <!-- Trade table -->
              <div v-if="stockTrades(sym).length" class="border-t border-border-subtle overflow-x-auto">
                <table class="w-full text-xs">
                  <thead>
                    <tr class="border-b border-border-subtle/50 text-left">
                      <th class="px-4 py-2.5 text-slate-600 font-semibold uppercase tracking-wider">Time</th>
                      <th class="px-4 py-2.5 text-slate-600 font-semibold uppercase tracking-wider">Action</th>
                      <th class="px-4 py-2.5 text-slate-600 font-semibold uppercase tracking-wider text-right">Shares</th>
                      <th class="px-4 py-2.5 text-slate-600 font-semibold uppercase tracking-wider text-right">Price</th>
                      <th class="px-4 py-2.5 text-slate-600 font-semibold uppercase tracking-wider text-right">Total</th>
                      <th class="px-4 py-2.5 text-slate-600 font-semibold uppercase tracking-wider text-right">Cash After</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="(t, i) in stockTrades(sym)"
                      :key="i"
                      class="border-b border-border-subtle/30 hover:bg-surface/20"
                    >
                      <td class="px-4 py-2 text-slate-500 whitespace-nowrap">{{ fmtDate(t.timestamp) }}</td>
                      <td class="px-4 py-2">
                        <span
                          class="px-2 py-0.5 rounded font-bold uppercase text-[10px]"
                          :class="(t.action || '').toLowerCase().includes('buy')
                            ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                            : 'bg-red-500/10 text-red-400 border border-red-500/20'"
                        >{{ t.action }}</span>
                      </td>
                      <td class="px-4 py-2 text-right font-mono text-slate-300">{{ t.shares != null ? Number(t.shares).toLocaleString(undefined, { maximumFractionDigits: 4 }) : '—' }}</td>
                      <td class="px-4 py-2 text-right font-mono text-slate-300">{{ t.price != null ? fmtMoney(t.price) : '—' }}</td>
                      <td class="px-4 py-2 text-right font-mono" :class="(t.action || '').toLowerCase().includes('buy') ? 'text-red-400' : 'text-emerald-400'">
                        {{ t.total != null ? fmtMoney(Math.abs(t.total)) : '—' }}
                      </td>
                      <td class="px-4 py-2 text-right font-mono text-slate-400">{{ t.cash_after != null ? fmtMoney(t.cash_after) : '—' }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <!-- Decision trace (paginated) -->
              <div v-if="stockDecisions(sym).length" class="border-t border-border-subtle px-5 py-4">
                <div class="flex items-center justify-between gap-3 mb-4">
                  <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Decision Trace</p>
                  <p class="text-[11px] text-slate-500">{{ stockDecisions(sym).length }} evaluations</p>
                </div>

                <div class="space-y-3 max-h-[36rem] overflow-y-auto pr-1">
                  <div
                    v-for="(d, i) in visibleDecisions(sym)"
                    :key="`${sym}-decision-${i}-${d.timestamp || 'na'}`"
                    class="rounded-xl border border-border-subtle bg-surface/30 p-4"
                  >
                    <div class="flex items-start justify-between gap-4 flex-wrap mb-3">
                      <div>
                        <p class="text-xs text-slate-500">{{ fmtDate(d.timestamp) }}</p>
                        <div class="flex items-center gap-2 flex-wrap mt-1.5">
                          <span class="px-2 py-0.5 rounded font-bold uppercase text-[10px]" :class="decisionBadgeClass(d.decision, d.action)">
                            {{ decisionLabel(d.decision, d.action) }}
                          </span>
                          <span v-if="d.override_applied" class="px-2 py-0.5 rounded border border-amber-500/20 bg-amber-500/10 text-[10px] font-bold uppercase text-amber-400">
                            Override
                          </span>
                          <span v-if="d.primary_strategy" class="px-2 py-0.5 rounded border border-border-subtle bg-[#04040c] text-[10px] font-mono text-slate-300">
                            {{ d.primary_strategy }}
                          </span>
                          <span v-if="d.primary_action_intent" class="px-2 py-0.5 rounded border border-sky-500/20 bg-sky-500/10 text-[10px] font-mono text-sky-300">
                            {{ d.primary_action_intent }}
                          </span>
                        </div>
                      </div>
                      <div class="text-right">
                        <p class="text-[10px] uppercase tracking-widest text-slate-600">Weighted Score</p>
                        <p class="text-sm font-mono text-slate-300">{{ fmtScore(d.normalized_score) }}</p>
                      </div>
                    </div>

                    <p v-if="d.override_applied && d.pre_override_action" class="text-[11px] text-amber-300 mb-2">
                      Final action changed from {{ decisionLabel(d.pre_override_decision, d.pre_override_action) }} to {{ decisionLabel(d.decision, d.action) }}.
                    </p>
                    <p v-if="d.final_reason" class="text-sm text-slate-200 whitespace-pre-wrap leading-6 mb-3">
                      {{ d.final_reason }}
                    </p>

                    <div v-if="d.post_decision?.length" class="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 mb-3">
                      <p class="text-[10px] font-bold uppercase tracking-widest text-amber-300 mb-2">Post-Decision Output</p>
                      <div
                        v-for="(post, postIdx) in d.post_decision"
                        :key="`${sym}-post-${i}-${postIdx}`"
                        class="text-xs text-slate-200"
                      >
                        <span class="font-mono text-amber-300">{{ post.strategy || 'post_decision' }}</span>
                        <span class="text-slate-500"> -> </span>
                        <span class="font-semibold">{{ decisionLabel(post.decision) }}</span>
                        <span v-if="post.reason" class="text-slate-400"> | {{ shortReason(post.reason, 220) }}</span>
                      </div>
                    </div>

                    <div v-if="d.strategies?.length" class="grid gap-2 sm:grid-cols-2">
                      <div
                        v-for="(s, strategyIdx) in d.strategies"
                        :key="`${sym}-strategy-${i}-${strategyIdx}`"
                        class="rounded-lg border border-border-subtle bg-[#04040c] px-3 py-2.5"
                      >
                        <div class="flex items-center justify-between gap-2 mb-1.5">
                          <p class="text-xs font-semibold text-slate-200 truncate">{{ s.strategy || 'strategy' }}</p>
                          <span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase" :class="decisionBadgeClass(s.decision)">
                            {{ decisionLabel(s.decision) }}
                          </span>
                        </div>
                        <div class="flex items-center gap-2 flex-wrap mb-1.5">
                          <span class="text-[10px] font-mono text-slate-500">weight {{ fmtWeight(s.weight) }}</span>
                          <span v-if="s.action_intent" class="text-[10px] font-mono text-sky-300">{{ s.action_intent }}</span>
                        </div>
                        <p v-if="s.reason" class="text-[11px] text-slate-400 leading-5 whitespace-pre-wrap">
                          {{ shortReason(s.reason) }}
                        </p>
                      </div>
                    </div>

                    <details class="mt-3 rounded-lg border border-border-subtle bg-[#04040c]">
                      <summary class="cursor-pointer select-none px-3 py-2 text-[11px] font-semibold text-slate-400">
                        Structured JSON
                      </summary>
                      <pre class="overflow-x-auto border-t border-border-subtle px-3 py-3 text-[11px] leading-5 text-slate-300">{{ toPrettyJson(d) }}</pre>
                    </details>
                  </div>

                  <!-- Show more button -->
                  <button
                    v-if="remainingDecisions(sym) > 0"
                    @click="showMoreDecisions(sym)"
                    class="w-full py-3 rounded-xl border border-border-subtle bg-surface/30 text-xs font-medium text-slate-400 hover:text-slate-200 hover:bg-surface/60 transition-colors"
                  >
                    Show {{ Math.min(DECISIONS_PAGE, remainingDecisions(sym)) }} more
                    <span class="text-slate-600">({{ remainingDecisions(sym) }} remaining)</span>
                  </button>
                </div>
              </div>
            </template>
          </section>
        </template>

        <!-- ── Extra stats ────────────────────────────────────────────────────── -->
        <section class="glass-card rounded-2xl p-5 mb-6">
          <p class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-4">Round Trip Statistics</p>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
            <div>
              <p class="text-[11px] text-slate-500 mb-0.5">Round Trips</p>
              <p class="font-semibold text-slate-200">{{ summary.round_trips ?? '—' }}</p>
            </div>
            <div>
              <p class="text-[11px] text-slate-500 mb-0.5">Total Round Trip P&L</p>
              <p class="font-semibold font-mono" :class="pnlClass(summary.total_round_trip_pnl)">{{ fmtPnl(summary.total_round_trip_pnl) }}</p>
            </div>
            <div>
              <p class="text-[11px] text-slate-500 mb-0.5">Avg Winning</p>
              <p class="font-semibold font-mono text-emerald-400">{{ fmtMoney(summary.avg_winning_round_trip) }}</p>
            </div>
            <div>
              <p class="text-[11px] text-slate-500 mb-0.5">Avg Losing</p>
              <p class="font-semibold font-mono text-red-400">{{ fmtMoney(summary.avg_losing_round_trip) }}</p>
            </div>
          </div>
        </section>

      </template>

      <!-- Pending/no summary yet -->
      <div v-else class="flex flex-col items-center gap-3 py-20 text-center">
        <span class="material-symbols-outlined text-5xl text-slate-600">hourglass_empty</span>
        <p class="text-slate-400 text-sm">This backtest has not completed yet.</p>
        <div v-if="progress != null" class="w-48">
          <div class="flex items-center justify-between text-xs text-slate-500 mb-1">
            <span>{{ currentStatus }}</span>
            <span>{{ Math.round(progress) }}%</span>
          </div>
          <div class="h-2 bg-surface rounded-full overflow-hidden">
            <div class="h-full bg-sky-400 rounded-full transition-all duration-500" :style="{ width: Math.min(100, progress) + '%' }"></div>
          </div>
        </div>
        <p v-else class="text-xs text-slate-600">Status: {{ currentStatus }}</p>
      </div>

    </main>
  </AppShell>

  <!-- ── Action Confirmation Modal ─────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showConfirm"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeConfirm"
      >
        <div class="relative w-full max-w-sm bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">

          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle">
            <div class="flex items-center gap-3">
              <div
                class="size-9 rounded-xl flex items-center justify-center shrink-0"
                :class="{
                  'bg-red-500/10 border border-red-500/20':         confirmAction === 'stop' || confirmAction === 'delete',
                  'bg-violet-500/10 border border-violet-500/20':   confirmAction === 'pause',
                  'bg-sky-500/10 border border-sky-500/20':         confirmAction === 'resume',
                  'bg-emerald-500/10 border border-emerald-500/20': confirmAction === 'rerun',
                }"
              >
                <span
                  class="material-symbols-outlined text-lg"
                  :class="{
                    'text-red-400':     confirmAction === 'stop' || confirmAction === 'delete',
                    'text-violet-400':  confirmAction === 'pause',
                    'text-sky-400':     confirmAction === 'resume',
                    'text-emerald-400': confirmAction === 'rerun',
                  }"
                >{{ confirmMeta.icon }}</span>
              </div>
              <h2 class="text-base font-bold">{{ confirmMeta.label }}</h2>
            </div>
            <button @click="closeConfirm" :disabled="actionBusy" class="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>

          <div class="px-6 py-5 space-y-4">
            <p class="text-sm text-slate-300">{{ confirmMeta.desc }}</p>
            <p class="text-xs text-slate-500 font-mono">Backtest #{{ btId }}</p>

            <Transition name="slide-up">
              <div
                v-if="actionMsg"
                class="rounded-lg px-4 py-3 text-sm font-medium"
                :class="actionOk
                  ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                  : 'bg-red-500/10 border border-red-500/20 text-red-400'"
              >
                <span v-if="actionBusy" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-base animate-spin">progress_activity</span>
                  {{ actionMsg }}
                </span>
                <span v-else>{{ actionMsg }}</span>
              </div>
            </Transition>
          </div>

          <div class="px-6 pb-6 flex gap-3">
            <button @click="closeConfirm" :disabled="actionBusy"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40">
              Cancel
            </button>
            <button @click="executeAction" :disabled="actionBusy"
              class="flex-1 py-2.5 rounded-lg text-sm font-bold transition-all disabled:opacity-50 flex items-center justify-center gap-2"
              :class="{
                'bg-red-500/80 hover:bg-red-500 text-white':          confirmAction === 'stop' || confirmAction === 'delete',
                'bg-violet-600 hover:bg-violet-500 text-white':       confirmAction === 'pause',
                'bg-primary hover:brightness-110 text-white':         confirmAction === 'resume',
                'bg-emerald-600 hover:bg-emerald-500 text-white':     confirmAction === 'rerun',
              }"
            >
              <span v-if="actionBusy" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              <span v-else class="material-symbols-outlined text-base">{{ confirmMeta.icon }}</span>
              {{ actionBusy ? 'Working...' : confirmMeta.label }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to       { opacity: 0; }

.slide-up-enter-active, .slide-up-leave-active { transition: all 0.2s ease; }
.slide-up-enter-from, .slide-up-leave-to       { opacity: 0; transform: translateY(6px); }
</style>
