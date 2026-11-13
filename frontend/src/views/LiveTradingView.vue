<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import InstanceLiveLogs from '../components/InstanceLiveLogs.vue'
import VueApexCharts from 'vue3-apexcharts'
import { getToken } from '../utils/auth.js'
import { fullscreenMode } from '../composables/useFullscreen.js'
import {
  canClosePosition,
  describeOption,
  displayQuantity,
  historicalsSymbols,
  isOptionRow,
  isShortPosition,
  quantityLabel,
  tradeTotal,
} from '../utils/optionPositions.js'

// fullscreenMode is a module-level shared ref — flipping it here hides the
// sidebar in AppShell and removes the content's left padding. We pair it
// with the browser Fullscreen API for true full-screen on supported browsers.

async function toggleFullscreen() {
  const next = !fullscreenMode.value
  fullscreenMode.value = next
  try {
    if (next && !document.fullscreenElement) {
      await document.documentElement.requestFullscreen?.()
    } else if (!next && document.fullscreenElement) {
      await document.exitFullscreen?.()
    }
  } catch {
    // Ignore — some browsers reject without a direct user gesture or in
    // certain iframes. The layout-level toggle still works.
  }
}

// If user hits Esc to exit browser fullscreen, sync our flag back.
function onFullscreenChange() {
  if (!document.fullscreenElement && fullscreenMode.value) {
    fullscreenMode.value = false
  }
}

const router = useRouter()
const route = useRoute()
// Reactive so route changes (/instances/A/live -> /instances/B/live) actually
// switch the data source instead of polling the original id forever. The
// watch at the bottom re-kicks the poll loop; components that take this as
// a prop (InstanceLiveLogs) re-render with the new id automatically.
const instanceId = computed(() => route.params.id)

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

// ── Polling cadence ───────────────────────────────────────────────────────────
// 3s while broker is alive, 10s once halted/idle so we still notice restarts.
// Backend now fetches account/positions directly from the broker on every
// request; portfolio_history is server-cached at 60s, so polling at 3s here
// is fine — the cached history rides along transparently.
const POLL_INTERVAL_ACTIVE = 3000
const POLL_INTERVAL_IDLE   = 10000

// ── State ─────────────────────────────────────────────────────────────────────
const liveState        = ref(null)          // the full LiveState row (or null)
const loading          = ref(true)
const lastError        = ref(null)
// 404 now means the instance has no broker creds linked or doesn't exist.
// (Backend fetches direct from broker on every request, so a missing-cache
// case no longer 404s — it returns broker_fetch_error instead.)
const notRunning       = ref(false)         // true when API returns 404
const currentRange     = ref('1D')
// Global chart style toggle. 'area' = filled gradient (RH default), 'line' = thin
// stroke only, 'candle' = OHLC synthesized from bucketed equity samples.
const chartStyle       = ref('area')
const chartStyles      = [
  { id: 'area',   label: 'Area',   icon: 'area_chart' },
  { id: 'line',   label: 'Line',   icon: 'show_chart' },
  { id: 'candle', label: 'Candle', icon: 'candlestick_chart' },
]

const haltModalOpen    = ref(false)
const haltConfirmInput = ref('')
const haltReasonInput  = ref('risk breach')

const closePosModalOpen = ref(false)
const closePosSymbol    = ref(null)
const closePosConfirmInput = ref('')

const orderModalOpen = ref(false)
const orderForm = ref({
  symbol: '',
  side: 'buy',
  order_type: 'market',
  qty: '',
  notional: '',
  limit_price: '',
  tif: 'day',
  extended_hours: false,
})

// Command toast queue — latest command status shown until terminal.
const commandToast = ref(null)  // { id, type, status, error, result }
let commandPollHandle = null

let pollHandle = null
let mounted = true

// Brokerage-style ranges. YTD is computed from the start of the current year;
// 'ALL' returns every point. The shorter intraday ranges (5m/30m/1h/4h) were
// removed because portfolio_history is sampled at 60s broker-side, so they
// rendered as flat lines or empty windows.
const timeRanges = ['1D', '1W', '1M', '3M', 'YTD', '1Y', 'ALL']

// ── Fetchers ──────────────────────────────────────────────────────────────────
async function fetchState() {
  try {
    const res = await fetch(`${API_BASE}/instances/${instanceId.value}/live-state`, {
      headers: authHeaders(),
    })
    if (res.status === 404) {
      notRunning.value = true
      liveState.value = null
      lastError.value = null
      return { pollNext: POLL_INTERVAL_IDLE }
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    liveState.value = data
    notRunning.value = false
    lastError.value = null
    const active = data?.trading_active === true
    return { pollNext: active ? POLL_INTERVAL_ACTIVE : POLL_INTERVAL_IDLE }
  } catch (e) {
    lastError.value = String(e?.message || e)
    return { pollNext: POLL_INTERVAL_IDLE }
  }
}

async function pollLoop() {
  if (!mounted) return
  loading.value = loading.value && liveState.value === null && !notRunning.value
  const { pollNext } = await fetchState()
  loading.value = false
  if (!mounted) return
  if (pollHandle) clearTimeout(pollHandle)
  pollHandle = setTimeout(pollLoop, pollNext)
}

// ── Commands ─────────────────────────────────────────────────────────────────
async function submitCommand(type, payload) {
  try {
    const res = await fetch(`${API_BASE}/instances/${instanceId.value}/live-command`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ type, payload: payload || {} }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
      commandToast.value = { id: null, type, status: 'failed', error: body?.detail || `HTTP ${res.status}`, result: null }
      setTimeout(() => { commandToast.value = null }, 6000)
      return null
    }
    const body = await res.json()
    commandToast.value = {
      id: body.command_id,
      type,
      status: body.status || 'pending',
      error: null,
      result: body.result || null,
    }
    // Cancel any in-flight command poll from a previous submission so only
    // the newest command's status drives the toast.
    if (commandPollHandle) { clearTimeout(commandPollHandle); commandPollHandle = null }
    // If already terminal (synthetic already_halted path), auto-dismiss.
    if (['completed', 'failed'].includes(commandToast.value.status)) {
      setTimeout(() => { commandToast.value = null }, 4000)
    } else {
      pollCommandStatus(body.command_id)
    }
    return body.command_id
  } catch (e) {
    commandToast.value = { id: null, type, status: 'failed', error: String(e?.message || e), result: null }
    setTimeout(() => { commandToast.value = null }, 6000)
    return null
  }
}

async function pollCommandStatus(commandId) {
  if (!commandId || !mounted) return
  try {
    const res = await fetch(`${API_BASE}/live-commands/${commandId}`, { headers: authHeaders() })
    if (!mounted) return
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const doc = await res.json()
    if (!mounted) return
    if (!commandToast.value || commandToast.value.id !== commandId) return
    commandToast.value = {
      id: commandId,
      type: doc.type,
      status: doc.status,
      error: doc.error,
      result: doc.result,
    }
    if (['completed', 'failed'].includes(doc.status)) {
      // Kick the state poll early so the UI reflects the change quickly.
      if (pollHandle) clearTimeout(pollHandle)
      pollHandle = setTimeout(pollLoop, 500)
      setTimeout(() => {
        if (commandToast.value && commandToast.value.id === commandId) {
          commandToast.value = null
        }
      }, 5000)
      return
    }
  } catch (e) {
    // swallow; keep polling until terminal or timeout
  }
  if (!mounted) return
  commandPollHandle = setTimeout(() => pollCommandStatus(commandId), 1000)
}

// ── Halt modal handlers ──────────────────────────────────────────────────────
function openHaltModal() {
  haltConfirmInput.value = ''
  haltReasonInput.value = 'risk breach'
  haltModalOpen.value = true
}

function closeHaltModal() { haltModalOpen.value = false }

async function confirmHalt() {
  if (haltConfirmInput.value.trim().toUpperCase() !== 'HALT') return
  const reason = haltReasonInput.value.trim() || 'manual halt via UI'
  closeHaltModal()
  await submitCommand('halt', { reason })
}

// ── Close-position modal handlers ────────────────────────────────────────────
function openClosePosModal(symbol) {
  closePosSymbol.value = symbol
  closePosConfirmInput.value = ''
  closePosModalOpen.value = true
}

function closeClosePosModal() { closePosModalOpen.value = false }

async function confirmClosePosition() {
  const sym = closePosSymbol.value
  if (!sym) return
  if (closePosConfirmInput.value.trim().toUpperCase() !== `CLOSE ${sym}`) return
  closeClosePosModal()
  await submitCommand('close_position', { symbol: sym })
}

// ── Manual order modal handlers ──────────────────────────────────────────────
function openOrderModal() {
  orderForm.value = {
    symbol: '', side: 'buy', order_type: 'market',
    qty: '', notional: '', limit_price: '', tif: 'day', extended_hours: false,
  }
  orderModalOpen.value = true
}

function closeOrderModal() { orderModalOpen.value = false }

async function confirmOrder() {
  const f = orderForm.value
  const payload = {
    symbol: (f.symbol || '').trim().toUpperCase(),
    side: f.side,
    order_type: f.order_type,
    tif: f.tif,
    extended_hours: !!f.extended_hours,
  }
  if (!payload.symbol) {
    commandToast.value = { id: null, type: 'submit_order', status: 'failed', error: 'Symbol required.', result: null }
    setTimeout(() => { commandToast.value = null }, 4000)
    return
  }
  const qty = f.qty !== '' ? Number(f.qty) : null
  const notional = f.notional !== '' ? Number(f.notional) : null
  if (qty === null && notional === null) {
    commandToast.value = { id: null, type: 'submit_order', status: 'failed', error: 'Enter either qty or notional.', result: null }
    setTimeout(() => { commandToast.value = null }, 4000)
    return
  }
  // Alpaca accepts qty OR notional but not both. Fail fast in the UI so the
  // user isn't surprised by a backend 422.
  if (qty !== null && notional !== null) {
    commandToast.value = { id: null, type: 'submit_order', status: 'failed', error: 'Fill qty OR notional, not both.', result: null }
    setTimeout(() => { commandToast.value = null }, 4000)
    return
  }
  if (qty !== null) {
    if (!Number.isFinite(qty) || qty <= 0) {
      commandToast.value = { id: null, type: 'submit_order', status: 'failed', error: 'qty must be a positive number.', result: null }
      setTimeout(() => { commandToast.value = null }, 4000)
      return
    }
    payload.qty = qty
  }
  if (notional !== null) {
    if (!Number.isFinite(notional) || notional <= 0) {
      commandToast.value = { id: null, type: 'submit_order', status: 'failed', error: 'notional must be a positive number.', result: null }
      setTimeout(() => { commandToast.value = null }, 4000)
      return
    }
    payload.notional = notional
  }
  if (f.order_type === 'limit') {
    const lp = Number(f.limit_price)
    if (!lp || lp <= 0 || !Number.isFinite(lp)) {
      commandToast.value = { id: null, type: 'submit_order', status: 'failed', error: 'Limit order needs positive limit_price.', result: null }
      setTimeout(() => { commandToast.value = null }, 4000)
      return
    }
    payload.limit_price = lp
  }
  // Alpaca: extended_hours=true is only valid for LIMIT + DAY. Match the
  // backend guard so the UI doesn't ship an order the adapter will reject.
  if (payload.extended_hours && (payload.order_type !== 'limit' || payload.tif !== 'day')) {
    commandToast.value = { id: null, type: 'submit_order', status: 'failed', error: 'Extended hours requires limit + TIF=day.', result: null }
    setTimeout(() => { commandToast.value = null }, 4000)
    return
  }
  closeOrderModal()
  await submitCommand('submit_order', payload)
}

// ── Derived ──────────────────────────────────────────────────────────────────
const statusLabel = computed(() => {
  if (notRunning.value) return 'NOT RUNNING'
  const s = String(liveState.value?.status || '').toUpperCase() || 'UNKNOWN'
  return s
})

const statusColor = computed(() => {
  const s = String(liveState.value?.status || '').toLowerCase()
  if (s === 'active') return 'emerald'
  if (s === 'halted') return 'amber'
  return 'slate'
})

// Broker-fetch failed ⇒ page is showing wrong/missing data (red).
// Container writer behind ⇒ page data is fresh from broker, but the in-container
// LiveState writer is lagging (informational gray).
const brokerFetchFailed = computed(() => !!liveState.value?.broker_fetch_error)
const containerStale    = computed(() => !!liveState.value?.container_stale)
const isStale           = computed(() => brokerFetchFailed.value)

function pnlColorClass(v) {
  if (v == null || Number.isNaN(Number(v)) || Number(v) === 0) return 'text-slate-400'
  return Number(v) > 0 ? 'text-gain' : 'text-loss'
}

const lookback = computed(() => {
  const lb = liveState.value?.lookback
  if (!lb || typeof lb !== 'object') return null
  return lb
})
const lookbackPct = computed(() => {
  const lb = lookback.value
  if (!lb || !lb.total || !lb.current) return 0
  const pct = (Number(lb.current) / Number(lb.total)) * 100
  if (!Number.isFinite(pct)) return 0
  return Math.max(0, Math.min(100, Math.round(pct)))
})

const equity = computed(() => Number(liveState.value?.equity ?? 0))
const cash = computed(() => Number(liveState.value?.cash ?? 0))
// Added 2026-04-21: mirror Alpaca's "Buying Power" which is ≈ 2× cash
// for margin accounts. Falls back to cash when not present (older brokers).
const buyingPower = computed(() => {
  const v = Number(liveState.value?.buying_power ?? 0)
  return v > 0 ? v : cash.value
})
const totalPnl = computed(() => Number(liveState.value?.total_pnl ?? 0))
const totalPnlPct = computed(() => Number(liveState.value?.total_pnl_pct ?? 0))
const dayPnl = computed(() => Number(liveState.value?.day_pnl ?? 0))
const dayPnlPct = computed(() => Number(liveState.value?.day_pnl_pct ?? 0))

const uptimeDisplay = computed(() => {
  const s = Number(liveState.value?.uptime_sec ?? 0)
  if (!s) return '—'
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m ${s % 60}s`
})

const positions = computed(() => Array.isArray(liveState.value?.positions) ? liveState.value.positions : [])
const trades = computed(() => Array.isArray(liveState.value?.recent_trades) ? liveState.value.recent_trades : [])

// ── Equity history (broker-side, NOT the in-container snapshot writer) ──────
// Always fetch from /api/instances/{id}/portfolio-history which proxies to
// the broker's authoritative API (Alpaca portfolio-history or RH Bonfire).
// User mandate: never use the broker snapshot cache for the equity chart, even
// for Alpaca — the snapshot writer samples sparsely at the strategy tick.
const equityHistory = ref(null)
const equityHistoryLoading = ref(false)
const equityHistoryError = ref(null)
let equityHistoryAbort = null

async function fetchEquityHistory() {
  if (equityHistoryAbort) {
    try { equityHistoryAbort.abort() } catch {}
  }
  equityHistoryAbort = new AbortController()
  equityHistoryLoading.value = true
  equityHistoryError.value = null
  try {
    const url = `${API_BASE}/instances/${instanceId.value}/portfolio-history?range=${encodeURIComponent(currentRange.value)}`
    const res = await fetch(url, { headers: authHeaders(), signal: equityHistoryAbort.signal })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    equityHistory.value = data
  } catch (e) {
    if (e?.name !== 'AbortError') {
      equityHistoryError.value = String(e?.message || e)
      equityHistory.value = null
    }
  } finally {
    equityHistoryLoading.value = false
  }
}

// ── Per-position historicals ─────────────────────────────────────────────────
// Keyed by symbol → [{ts, value}, ...]. Refetched whenever the position list
// changes shape OR the global range changes. Backend caches at 60s so rapid
// re-polls are cheap.
const positionHistoricals = ref({})
const positionHistError = ref(null)
const positionHistLoading = ref(false)
let positionHistAbort = null

// Stock only: /symbol-historicals has nothing for an OCC contract symbol.
const positionSymbols = computed(() => historicalsSymbols(positions.value))
const positionSymbolsKey = computed(() => [...positionSymbols.value].sort().join(','))

async function fetchPositionHistoricals() {
  const syms = positionSymbols.value
  if (!syms.length) {
    positionHistoricals.value = {}
    return
  }
  if (positionHistAbort) {
    try { positionHistAbort.abort() } catch {}
  }
  positionHistAbort = new AbortController()
  positionHistLoading.value = true
  positionHistError.value = null
  try {
    const url = `${API_BASE}/symbol-historicals?symbols=${encodeURIComponent(syms.join(','))}&range=${encodeURIComponent(currentRange.value)}`
    const res = await fetch(url, { headers: authHeaders(), signal: positionHistAbort.signal })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    if (data?.error) positionHistError.value = String(data.error)
    positionHistoricals.value = data?.results || {}
  } catch (e) {
    if (e?.name !== 'AbortError') {
      positionHistError.value = String(e?.message || e)
    }
  } finally {
    positionHistLoading.value = false
  }
}

function positionPoints(symbol) {
  const pts = positionHistoricals.value?.[symbol] || []
  return pts
    .map(p => [Date.parse(p.ts), Number(p.value || 0)])
    .filter(([t, v]) => Number.isFinite(t) && Number.isFinite(v))
}

function positionSeries(symbol) {
  const pts = positionPoints(symbol)
  if (chartStyle.value === 'candle') {
    return [{ name: symbol, data: bucketCandles(pts, 24) }]
  }
  return [{
    name: symbol,
    data: pts.map((p, i) => [i, p[1]]),
  }]
}

function positionChange(symbol) {
  const pts = positionHistoricals.value?.[symbol] || []
  if (pts.length < 2) return { dollars: 0, pct: 0, isUp: true, hasData: false }
  const start = Number(pts[0].value || 0)
  const end = Number(pts[pts.length - 1].value || 0)
  const dollars = end - start
  const pct = start ? (dollars / start) * 100 : 0
  return { dollars, pct, isUp: dollars >= 0, hasData: true }
}

function positionChartOptions(symbol) {
  const ch = positionChange(symbol)
  const lineColor = ch.isUp ? '#4cc38a' : '#e5484d'   // gain / loss tokens
  const isCandle = chartStyle.value === 'candle'
  const isLine = chartStyle.value === 'line'
  const pts = positionPoints(symbol)
  return {
    theme: { mode: 'dark' },
    chart: { background: 'transparent', toolbar: { show: false }, sparkline: { enabled: !isCandle }, animations: { enabled: false } },
    grid: { show: false },
    stroke: { curve: 'straight', width: isLine ? 1.25 : 1.75 },
    fill: isCandle
      ? { type: 'solid' }
      : (isLine
          ? { type: 'solid', opacity: 0 }
          : { type: 'gradient', gradient: { colorStops: [{ offset: 0, color: lineColor, opacity: 0.12 }, { offset: 100, color: lineColor, opacity: 0 }] } }),
    colors: [lineColor],
    plotOptions: { candlestick: { colors: { upward: '#4cc38a', downward: '#e5484d' }, wick: { useFillColor: true } } },
    xaxis: {
      // Numeric sequential x — same trick as the equity chart, prevents
      // weekend-gap flat lines on per-position charts too.
      type: 'numeric',
      labels: { show: isCandle, style: { colors: '#71717a', fontSize: '11px' } },
      axisBorder: { show: false },
      axisTicks: { show: false },
      tooltip: { enabled: false },
    },
    yaxis: {
      decimalsInFloat: 2,
      labels: { show: isCandle, style: { colors: '#71717a', fontSize: '11px' }, formatter: v => '$' + Number(v || 0).toFixed(2) },
      tooltip: { enabled: false },
    },
    dataLabels: { enabled: false },
    // Tooltip x maps the index back to the underlying timestamp.
    tooltip: {
      theme: 'dark',
      x: {
        formatter: (val) => {
          const i = Math.round(Number(val))
          const ts = isCandle
            ? bucketCandles(pts, 24)[i]?.ts
            : pts[i]?.[0]
          return ts ? new Date(ts).toLocaleString() : ''
        },
      },
      y: { formatter: v => '$' + Number(v || 0).toFixed(2) },
    },
  }
}

// Equity history points come from the broker's own portfolio-history API
// (proxied via /api/instances/{id}/portfolio-history). No client-side range
// filtering — backend already returns the right window per range. Backend
// shape: { timestamps: [ms_epoch], values: [equity], current_value, ... }.
const equityPoints = computed(() => {
  const ts = equityHistory.value?.timestamps || []
  const vs = equityHistory.value?.values || []
  const out = []
  const n = Math.min(ts.length, vs.length)
  for (let i = 0; i < n; i++) {
    const t = Number(ts[i])
    const v = Number(vs[i])
    if (Number.isFinite(t) && Number.isFinite(v)) out.push([t, v])
  }
  return out
})

// Bucket equity samples into ~50 candles for the candle view by SAMPLE COUNT
// (not wall-clock time). Sample-bucketing avoids huge empty candles for
// weekends/overnight gaps the way RH does. Each candle:
//   { x: sequential-index, y: [open, high, low, close], ts: bucket-start-ts }
function bucketCandles(points, bucketCount = 40) {
  if (points.length < 2) return []
  const samplesPerBucket = Math.max(1, Math.ceil(points.length / bucketCount))
  const buckets = []
  for (let i = 0; i < points.length; i += samplesPerBucket) {
    const slice = points.slice(i, i + samplesPerBucket)
    if (!slice.length) continue
    const open = slice[0][1]
    const close = slice[slice.length - 1][1]
    let high = -Infinity, low = Infinity
    for (const [, v] of slice) {
      if (v > high) high = v
      if (v < low) low = v
    }
    buckets.push({ x: buckets.length, y: [open, high, low, close], ts: slice[0][0] })
  }
  return buckets
}

// Format a wall-clock timestamp (ms) into a label appropriate for the active
// range. Sequential x-positioning hides actual dates, so labels carry the
// time context the user expects.
function formatXLabel(ts, range) {
  const d = new Date(ts)
  if (!d || isNaN(d.getTime())) return ''
  if (range === '1D') {
    return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', hour12: false })
  }
  if (range === '1W') {
    return d.toLocaleDateString(undefined, { weekday: 'short' }) + ' ' + d.toLocaleTimeString(undefined, { hour: 'numeric', hour12: false })
  }
  if (range === '1M' || range === '3M' || range === 'YTD') {
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  }
  // 1Y, ALL
  return d.toLocaleDateString(undefined, { month: 'short', year: '2-digit' })
}

// Date label per series index — used by xaxis.labels.formatter and tooltip.
const xLabels = computed(() =>
  equityPoints.value.map(p => formatXLabel(p[0], currentRange.value))
)

// Sequential x indices instead of wall-clock timestamps. This is what RH
// and every consumer brokerage does — the line stays continuous across
// weekends/overnight because each sample gets equal horizontal spacing.
const graphSeries = computed(() => {
  if (chartStyle.value === 'candle') {
    return [{ name: 'Equity', data: bucketCandles(equityPoints.value) }]
  }
  return [{
    name: 'Equity',
    data: equityPoints.value.map((p, i) => [i, p[1]]),
  }]
})

const chartType = computed(() => {
  if (chartStyle.value === 'candle') return 'candlestick'
  if (chartStyle.value === 'line') return 'line'
  return 'area'
})

// Range stats anchor "change over range" on the first visible point.
// When the window has <2 points we fall back to liveState's day_pnl so the
// header isn't blank during early ticks of a fresh session. Note: this is
// computed from raw equityPoints (not graphSeries) so candle-mode bucketing
// doesn't distort the high/low/change numbers.
const rangeStats = computed(() => {
  const series = equityPoints.value
  if (series.length < 2) {
    const dollars = Number(liveState.value?.day_pnl ?? 0)
    const pct = Number(liveState.value?.day_pnl_pct ?? 0)
    return { start: null, end: equity.value, dollars, pct, isUp: dollars >= 0, high: null, low: null }
  }
  const start = series[0][1]
  const end = series[series.length - 1][1]
  const dollars = end - start
  const pct = start ? (dollars / start) * 100 : 0
  let high = -Infinity
  let low = Infinity
  for (const [, v] of series) {
    if (v > high) high = v
    if (v < low) low = v
  }
  return { start, end, dollars, pct, isUp: dollars >= 0, high, low }
})

const rangeLabel = computed(() => {
  const r = currentRange.value
  return {
    '1D': 'today',
    '1W': 'this week',
    '1M': 'this month',
    '3M': 'past 3 months',
    'YTD': 'year to date',
    '1Y': 'past year',
    'ALL': 'all time',
  }[r] || r
})

const chartOptions = computed(() => {
  const up = rangeStats.value.isUp
  const lineColor = up ? '#4cc38a' : '#e5484d'  // gain / loss tokens, as on the dashboard
  const isCandle = chartStyle.value === 'candle'
  const isLine = chartStyle.value === 'line'
  return {
    theme: { mode: 'dark' },
    chart: {
      background: 'transparent',
      toolbar: { show: false },
      sparkline: { enabled: false },
      animations: { enabled: true, easing: 'linear', dynamicAnimation: { speed: 350 } },
    },
    grid: { borderColor: 'rgba(255,255,255,0.05)', strokeDashArray: 0, xaxis: { lines: { show: false } }, padding: { left: 6, right: 6, top: 0, bottom: 0 } },
    stroke: {
      // Straight curve for RH-style jagged tick rendering (broker portfolio
      // history is sampled finely enough that smoothing introduces fake
      // sine-wave artifacts on sparse intraday segments).
      curve: 'straight',
      width: isLine ? 1.75 : 2,
    },
    fill: isCandle
      ? { type: 'solid' }
      : (isLine
          ? { type: 'solid', opacity: 0 }
          : { type: 'gradient', gradient: { colorStops: [{ offset: 0, color: lineColor, opacity: 0.1 }, { offset: 100, color: lineColor, opacity: 0 }] } }),
    colors: [lineColor],
    markers: { size: 0, hover: { size: 4, sizeOffset: 2 } },
    plotOptions: {
      candlestick: {
        colors: { upward: '#4cc38a', downward: '#e5484d' },
        wick: { useFillColor: true },
      },
    },
    xaxis: {
      // Numeric x for line/area lets us space samples evenly (no weekend
      // gaps); candle keeps numeric too because bucketCandles outputs
      // sequential indices.
      type: 'numeric',
      tickAmount: 6,
      labels: {
        style: { colors: '#71717a', fontSize: '11px' },
        formatter: (val) => {
          const i = Math.round(Number(val))
          if (isCandle) {
            const ts = graphSeries.value[0].data[i]?.ts
            return ts ? formatXLabel(ts, currentRange.value) : ''
          }
          return xLabels.value[i] || ''
        },
      },
      crosshairs: { show: true, stroke: { color: 'rgba(237,237,237,0.3)', width: 1, dashArray: 0 } },
      axisBorder: { show: false },
      axisTicks: { show: false },
      tooltip: { enabled: false },
    },
    yaxis: {
      // decimalsInFloat keeps tick labels at 2dp instead of 10dp when the
      // visible range is small (e.g. equity hovering in a $60 band intraday).
      decimalsInFloat: 2,
      forceNiceScale: true,
      labels: {
        style: { colors: '#71717a', fontSize: '11px' },
        formatter: v => '$' + Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
      },
      tooltip: { enabled: false },
    },
    dataLabels: { enabled: false },
    tooltip: {
      theme: 'dark',
      shared: !isCandle,
      x: {
        formatter: (val) => {
          const i = Math.round(Number(val))
          if (isCandle) {
            const ts = graphSeries.value[0].data[i]?.ts
            return ts ? new Date(ts).toLocaleString() : ''
          }
          const ts = equityPoints.value[i]?.[0]
          return ts ? new Date(ts).toLocaleString() : ''
        },
      },
      // Always provide an object for tooltip.y — passing undefined causes
      // ApexCharts to crash on style transitions reading tooltip.y.formatter.
      y: { formatter: v => '$' + Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) },
    },
  }
})

function fmtMoney(v) {
  if (v == null) return '—'
  const n = Number(v)
  return (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPct(v) {
  if (v == null) return '—'
  const n = Number(v)
  const sign = n >= 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

function fmtTime(ts) {
  if (!ts) return ''
  try {
    const d = typeof ts === 'string' ? new Date(ts) : ts
    return d.toISOString().slice(11, 19)
  } catch { return String(ts) }
}

// 2026-04-30: full date+time in user's local timezone for Recent Executions
// readability. e.g. "Apr 30, 8:17:44 PM"
function fmtDateTime(ts) {
  if (!ts) return ''
  try {
    const d = typeof ts === 'string' ? new Date(ts) : ts
    if (!d || isNaN(d.getTime())) return String(ts)
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    })
  } catch { return String(ts) }
}

// ── Lifecycle ────────────────────────────────────────────────────────────────
onMounted(() => {
  mounted = true
  pollLoop()
  document.addEventListener('fullscreenchange', onFullscreenChange)
})

onUnmounted(() => {
  mounted = false
  if (pollHandle) clearTimeout(pollHandle)
  pollHandle = null
  if (commandPollHandle) clearTimeout(commandPollHandle)
  commandPollHandle = null
  if (positionHistAbort) {
    try { positionHistAbort.abort() } catch {}
    positionHistAbort = null
  }
  if (equityHistoryAbort) {
    try { equityHistoryAbort.abort() } catch {}
    equityHistoryAbort = null
  }
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  // Make sure we don't leak the sidebar-hidden state to other pages.
  if (fullscreenMode.value) fullscreenMode.value = false
  if (document.fullscreenElement) {
    try { document.exitFullscreen?.() } catch {}
  }
})

// Reset + re-kick poll on instance change. Must re-fetch regardless of the
// previous timeout state (a call might be mid-await) so we don't serve stale
// data for the new id. Also clear command-poll handle so an old command's
// status doesn't overwrite the toast after switching instances.
watch(() => route.params.id, (newId, oldId) => {
  if (newId === oldId) return
  if (pollHandle) { clearTimeout(pollHandle); pollHandle = null }
  if (commandPollHandle) { clearTimeout(commandPollHandle); commandPollHandle = null }
  liveState.value = null
  notRunning.value = false
  lastError.value = null
  commandToast.value = null
  positionHistoricals.value = {}
  equityHistory.value = null
  pollLoop()
})

// Refetch per-position price history when:
//  - the set of held symbols changes (a new buy/sell)
//  - the user picks a different range tab
// Backend caches each (range, symbol-csv) pair at 60s so rapid live-state
// repolls don't trigger redundant provider calls.
watch([positionSymbolsKey, currentRange], ([key]) => {
  if (!key) {
    positionHistoricals.value = {}
    return
  }
  fetchPositionHistoricals()
}, { immediate: true })

// Refetch broker-side equity history on range change OR when the live-state
// poll first succeeds (so we have a valid instance id and brokerage). Without
// the liveState dep we'd race the route param watcher and lose the initial fetch.
watch([currentRange, () => liveState.value?.id ?? instanceId.value], () => {
  if (!instanceId.value) return
  fetchEquityHistory()
}, { immediate: true })

// Clear the "feed error" banner's visibility when we get a good response
// again; handled implicitly because lastError is set to null on success
// inside fetchState.
// Auto-clear extended_hours if the user switches to an incompatible order
// type or TIF — prevents submitting a value the backend will reject.
watch(
  () => [orderForm.value.order_type, orderForm.value.tif],
  ([ot, tif]) => {
    if (orderForm.value.extended_hours && (ot !== 'limit' || tif !== 'day')) {
      orderForm.value.extended_hours = false
    }
  },
)
</script>

<template>
  <AppShell>
    <main class="ui-page pb-24 sm:pb-8" :class="fullscreenMode ? 'max-w-none' : ''">
      <!-- Breadcrumb + back -->
      <div class="flex items-center gap-2 text-sm text-slate-500">
        <button @click="router.push(`/instances/${instanceId}`)" class="inline-flex items-center gap-1 min-h-[44px] sm:min-h-8 -ml-2 px-2 rounded-md text-slate-400 hover:text-slate-100 hover:bg-raised transition-colors duration-150 ease-out">
          <span class="material-symbols-outlined text-[16px]">arrow_back</span>
          Back to instance
        </button>
        <span class="text-slate-600">/</span>
        <span class="text-slate-300 truncate max-w-xs">Live trading · <span class="font-mono text-xs">{{ instanceId }}</span></span>
      </div>

      <!-- Header -->
      <header class="mt-4 flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <div class="min-w-0">
          <h1 class="ui-page-title truncate">Live trading terminal</h1>
          <p class="ui-page-sub">Real-time positions and executions</p>
        </div>
        <div class="flex items-center gap-2 sm:gap-3 shrink-0 flex-wrap">
          <span class="inline-flex items-center gap-2 text-sm"
                :class="statusColor === 'emerald' ? 'text-slate-200' : statusColor === 'amber' ? 'text-slate-300' : 'text-slate-500'">
            <span class="ui-dot" :class="statusColor === 'emerald' ? 'ui-dot-live' : statusColor === 'amber' ? 'ui-dot-warn' : 'ui-dot-off'"></span>
            {{ statusLabel.charAt(0) + statusLabel.slice(1).toLowerCase() }}
          </span>
          <span
            v-if="brokerFetchFailed"
            class="text-xs text-loss inline-flex items-center gap-1 max-w-[260px]"
            :title="liveState?.broker_fetch_error || ''"
          >
            <span class="material-symbols-outlined text-[16px]">cloud_off</span>
            <span class="truncate">Broker offline ({{ liveState?.broker_fetch_error }})</span>
          </span>
          <span
            v-else-if="containerStale"
            class="text-xs text-slate-500 inline-flex items-center gap-1"
            :title="`Container LiveState writer last updated ${liveState?.container_seconds_since_update}s ago. Live data above is still fresh from the broker.`"
          >
            <span class="material-symbols-outlined text-[16px]">sync_disabled</span>
            Container offline
          </span>
          <span v-if="lastError && liveState" class="text-xs text-loss inline-flex items-center gap-1 max-w-[200px]" :title="lastError">
            <span class="material-symbols-outlined text-[16px]">error</span>
            <span class="truncate">Feed error</span>
          </span>
          <!-- Desktop-only kill switch; mobile version is sticky below -->
          <button
            v-if="!notRunning"
            @click="openHaltModal"
            class="hidden sm:inline-flex ui-btn text-loss"
          >
            <span class="material-symbols-outlined text-[16px]">block</span>
            Halt
          </button>
          <button
            v-if="!notRunning"
            @click="openOrderModal"
            class="ui-btn h-11 sm:h-8"
          >
            <span class="material-symbols-outlined text-[16px]">add_shopping_cart</span>
            Manual order
          </button>
          <!-- Fullscreen toggle: hides sidebar and (where supported) calls
               document.requestFullscreen so the chart fills the viewport. -->
          <button
            @click="toggleFullscreen"
            :title="fullscreenMode ? 'Exit fullscreen' : 'Enter fullscreen'"
            :aria-label="fullscreenMode ? 'Exit fullscreen' : 'Enter fullscreen'"
            class="ui-icon-btn size-11 sm:size-8"
          >
            <span class="material-symbols-outlined text-[18px]">
              {{ fullscreenMode ? 'fullscreen_exit' : 'fullscreen' }}
            </span>
          </button>
        </div>
      </header>

      <!-- Historic lookback banner (mirrors BacktestResults.nexus_lookback UI) -->
      <!-- Guarded against (a) halted/not-running state and (b) stale snapshots
           so a dead broker can't keep animating a fake percentage. -->
      <div
        v-if="lookback && !notRunning && !isStale"
        class="mt-8 ui-card px-5 py-4"
        role="progressbar"
        :aria-valuenow="lookbackPct"
        aria-valuemin="0"
        aria-valuemax="100"
        aria-label="Historic lookback training progress"
      >
        <div class="flex items-center justify-between gap-3 flex-wrap">
          <div class="flex items-center gap-3 min-w-0">
            <span class="material-symbols-outlined text-slate-500 text-[18px]">history</span>
            <div class="min-w-0">
              <p class="text-sm font-medium text-slate-200">Historic lookback training</p>
              <p class="text-xs text-slate-500 mt-0.5 truncate tabular-nums">
                {{ lookback.spec_name || 'graph_nexus_analysis' }}
                · {{ lookback.start_date }} → {{ lookback.end_date }}
              </p>
            </div>
          </div>
          <div class="text-right shrink-0">
            <p class="text-xl font-medium text-slate-100 tabular-nums leading-none">
              {{ lookback.current }}<span class="text-sm text-slate-500">/{{ lookback.total }}</span>
            </p>
            <p class="text-xs text-slate-500 tabular-nums mt-1">{{ lookbackPct }}% · {{ lookback.current_date }}</p>
          </div>
        </div>
        <div class="mt-3 h-1 bg-raised rounded-full overflow-hidden">
          <div
            class="h-full rounded-full bg-slate-300 origin-left transition-transform duration-500 ease-out"
            :style="{ transform: `scaleX(${lookbackPct / 100})` }"
          ></div>
        </div>
        <p class="text-xs text-slate-500 mt-3">
          Trades are deferred until warmup completes.
          The Nexus strategy needs this context before it can make day-1 decisions.
        </p>
      </div>

      <!-- Not-running empty state -->
      <div v-if="notRunning" class="mt-8 ui-card ui-empty">
        <div class="max-w-md space-y-2">
          <p class="text-base text-slate-200">No live session</p>
          <p>This instance has never booted a broker, or its broker is already shut down.</p>
          <p class="text-xs text-slate-500">Start the instance from the Instance page to begin a live session.</p>
        </div>
      </div>

      <!-- Main grid (only shown when liveState exists) -->
      <div v-else-if="liveState" class="mt-8 grid grid-cols-1 lg:grid-cols-2 gap-x-8 gap-y-12 items-start">

        <!-- Left: hero equity card + secondary stats -->
        <div class="flex flex-col gap-6 min-w-0">
          <!-- Brokerage hero card: equity, change, chart, range tabs, stats -->
          <div class="ui-card px-5 pt-5 pb-5 flex flex-col">
            <!-- Hero: equity + uptime -->
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <p class="text-sm text-slate-400">Portfolio equity</p>
                <p class="mt-2 text-[36px] font-medium text-slate-100 tabular-nums leading-none tracking-[-0.02em] break-all">
                  {{ fmtMoney(equity) }}
                </p>
                <!-- Range change -->
                <p class="mt-2 text-sm tabular-nums">
                  <span :class="rangeStats.isUp ? 'text-gain' : 'text-loss'">{{ rangeStats.isUp ? '+' : '' }}{{ fmtMoney(rangeStats.dollars) }} ({{ rangeStats.isUp ? '+' : '' }}{{ rangeStats.pct.toFixed(2) }}%)</span>
                  <span class="text-slate-500 ml-1.5">{{ rangeLabel }}</span>
                </p>
              </div>
              <div class="text-right shrink-0">
                <p class="text-xs text-slate-500">Uptime</p>
                <p class="mt-1 text-sm text-slate-300 tabular-nums">{{ uptimeDisplay }}</p>
              </div>
            </div>
            <!-- Chart (fixed height — tabs sit immediately below, not pushed to card bottom) -->
            <div class="mt-4 -mx-2">
              <div v-if="graphSeries[0].data.length === 0" class="h-[320px] flex items-center justify-center text-sm text-slate-500">
                No equity history yet — broker is fetching…
              </div>
              <VueApexCharts
                v-else
                :key="chartStyle"
                :type="chartType"
                :height="320"
                :options="chartOptions"
                :series="graphSeries"
              />
            </div>
            <!-- Range tabs immediately below the chart + chart-style toggle on the right -->
            <div class="flex items-center justify-between gap-3 mt-3 flex-wrap">
              <div class="overflow-x-auto no-scrollbar max-w-full">
                <div class="ui-seg" role="group" aria-label="Range">
                  <button
                    v-for="r in timeRanges"
                    :key="r"
                    @click="currentRange = r"
                    class="ui-seg-btn tabular-nums whitespace-nowrap"
                    :class="currentRange === r ? 'ui-seg-btn-active' : ''"
                    :aria-pressed="currentRange === r"
                  >{{ r }}</button>
                </div>
              </div>
              <!-- Chart style toggle (line / area / candle) -->
              <div class="ui-seg shrink-0" role="group" aria-label="Chart style">
                <button
                  v-for="s in chartStyles"
                  :key="s.id"
                  @click="chartStyle = s.id"
                  :title="s.label"
                  :aria-label="s.label"
                  :aria-pressed="chartStyle === s.id"
                  class="ui-seg-btn px-2 inline-flex items-center justify-center"
                  :class="chartStyle === s.id ? 'ui-seg-btn-active' : ''"
                >
                  <span class="material-symbols-outlined text-[16px]">{{ s.icon }}</span>
                </button>
              </div>
            </div>
            <!-- Range high / low / day / total -->
            <div v-if="rangeStats.high != null" class="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-5 pt-5 border-t border-white/[0.06]">
              <div>
                <div class="text-xs text-slate-500">Range high</div>
                <div class="mt-1 text-sm font-medium text-slate-200 tabular-nums">{{ fmtMoney(rangeStats.high) }}</div>
              </div>
              <div>
                <div class="text-xs text-slate-500">Range low</div>
                <div class="mt-1 text-sm font-medium text-slate-200 tabular-nums">{{ fmtMoney(rangeStats.low) }}</div>
              </div>
              <div>
                <div class="text-xs text-slate-500">Day P&amp;L</div>
                <div class="mt-1 text-sm font-medium tabular-nums" :class="dayPnl >= 0 ? 'text-gain' : 'text-loss'">
                  {{ fmtPct(dayPnlPct) }}
                </div>
              </div>
              <div>
                <div class="text-xs text-slate-500">Total P&amp;L</div>
                <div class="mt-1 text-sm font-medium tabular-nums" :class="totalPnl >= 0 ? 'text-gain' : 'text-loss'">
                  {{ fmtPct(totalPnlPct) }}
                </div>
              </div>
            </div>
          </div>
          <!-- Compact secondary stats: cash + buying power + total P&L $ -->
          <div class="ui-card grid grid-cols-3 divide-x divide-white/[0.06]">
            <div class="px-4 sm:px-5 py-4 min-w-0">
              <span class="text-xs text-slate-500 block">Cash</span>
              <span class="mt-1 block text-base sm:text-lg font-medium text-slate-100 tabular-nums truncate">{{ fmtMoney(cash) }}</span>
            </div>
            <div class="px-4 sm:px-5 py-4 min-w-0">
              <span class="text-xs text-slate-500 block">Buying power</span>
              <span class="mt-1 block text-base sm:text-lg font-medium text-slate-100 tabular-nums truncate">{{ fmtMoney(buyingPower) }}</span>
            </div>
            <div class="px-4 sm:px-5 py-4 min-w-0">
              <span class="text-xs text-slate-500 block">Total P&amp;L</span>
              <span
                class="mt-1 block text-base sm:text-lg font-medium tabular-nums truncate"
                :class="totalPnl >= 0 ? 'text-gain' : 'text-loss'"
              >{{ fmtMoney(totalPnl) }}</span>
            </div>
          </div>
          <!-- Recent executions — in the left column under cash/BP/Total P&L -->
          <section class="mt-6" aria-labelledby="lt-exec">
            <div class="mb-3">
              <h2 id="lt-exec" class="ui-section-title">Recent executions</h2>
              <p class="ui-section-sub tabular-nums">{{ trades.length }} recorded</p>
            </div>
            <div class="ui-card overflow-hidden">
              <p v-if="trades.length === 0" class="ui-empty">No executions recorded yet</p>
              <template v-else>
                <div class="lt-exec-grid hidden sm:grid ui-th">
                  <span>Execution</span><span class="text-right">Quantity</span><span class="text-right">Fill price</span><span class="text-right">Total</span>
                </div>
                <div class="overflow-y-auto no-scrollbar ui-rows" style="max-height: 420px">
                  <div
                    v-for="(t, i) in trades"
                    :key="(t.order_id || '') + '_' + i"
                    class="lt-exec-grid grid items-center min-h-14 px-5 py-2"
                  >
                    <div class="min-w-0">
                      <div class="flex items-baseline gap-2 min-w-0">
                        <span class="text-xs text-slate-400 capitalize shrink-0">{{ t.side || '—' }}</span>
                        <span class="text-sm font-medium text-slate-100 truncate">{{ t.symbol || '' }}</span>
                        <span v-if="isOptionRow(t)" class="text-xs text-slate-500 shrink-0">Option</span>
                      </div>
                      <div v-if="isOptionRow(t)" class="text-xs text-slate-500 mt-0.5 truncate">{{ describeOption(t) }}</div>
                      <div class="text-xs text-slate-500 mt-0.5 tabular-nums">{{ fmtDateTime(t.ts) }}<span class="sm:hidden"> · {{ displayQuantity(t, 4) }} {{ quantityLabel(t).toLowerCase() }} @ {{ fmtMoney(t.price) }}</span></div>
                    </div>
                    <div class="hidden sm:block text-right tabular-nums">
                      <div class="text-sm text-slate-200" :title="String(t.qty || 0)">{{ displayQuantity(t, 4) }}</div>
                      <div class="text-xs text-slate-500 mt-0.5">{{ quantityLabel(t) }}</div>
                    </div>
                    <div class="hidden sm:block text-right text-sm text-slate-200 tabular-nums">{{ fmtMoney(t.price) }}</div>
                    <div class="text-right text-sm text-slate-100 tabular-nums">{{ fmtMoney(tradeTotal(t)) }}</div>
                  </div>
                </div>
              </template>
            </div>
          </section>
        </div>

        <!-- Right: positions only — 2-column card grid -->
        <section class="min-w-0" aria-labelledby="lt-positions">
          <div class="mb-3">
            <h2 id="lt-positions" class="ui-section-title">Active positions</h2>
            <p class="ui-section-sub tabular-nums">{{ positions.length }} open</p>
          </div>
          <div v-if="positions.length === 0" class="ui-card ui-empty">No open positions</div>
          <div v-else class="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <div
              v-for="p in positions"
              :key="p.symbol"
              class="ui-card px-4 py-4 flex flex-col min-w-0"
            >
              <div class="flex justify-between items-start gap-3">
                <div class="min-w-0 flex-1">
                  <div class="flex items-baseline gap-2 flex-wrap min-w-0">
                    <span class="text-base font-medium text-slate-100 truncate max-w-full">{{ p.symbol }}</span>
                    <span v-if="isOptionRow(p)" class="text-xs text-slate-500">Option</span>
                    <span v-if="isOptionRow(p) && isShortPosition(p)" class="text-xs text-slate-300">Short</span>
                  </div>
                  <div v-if="isOptionRow(p)" class="text-xs text-slate-500 mt-0.5">{{ describeOption(p) }}</div>
                  <div class="text-xs mt-0.5 tabular-nums">
                    <span :class="p.avg_entry_price ? pnlColorClass(p.unrealized_pnl_pct) : 'text-slate-500'">{{ p.avg_entry_price ? fmtPct(p.unrealized_pnl_pct) : '—' }}</span>
                    <span class="text-slate-500 ml-1">unrealized</span>
                  </div>
                  <!-- Range change from historicals (matches global range/style) -->
                  <div
                    v-if="positionChange(p.symbol).hasData"
                    class="text-xs tabular-nums mt-0.5"
                  >
                    <span :class="positionChange(p.symbol).isUp ? 'text-gain' : 'text-loss'">{{ positionChange(p.symbol).isUp ? '+' : '' }}{{ positionChange(p.symbol).pct.toFixed(2) }}%</span>
                    <span class="text-slate-500 ml-1">{{ currentRange }}</span>
                  </div>
                </div>
                <div class="text-right shrink-0">
                  <div class="text-xs text-slate-500">Market value</div>
                  <div class="mt-1 text-base font-medium text-slate-100 tabular-nums">{{ fmtMoney(p.market_value) }}</div>
                </div>
              </div>
              <!-- Per-position price chart -->
              <div class="my-3 -mx-1">
                <div
                  v-if="!(positionHistoricals[p.symbol] && positionHistoricals[p.symbol].length)"
                  class="h-[80px] flex items-center justify-center text-xs text-slate-600"
                >
                  {{ isOptionRow(p) ? 'No price chart for options' : (positionHistLoading ? 'Loading chart…' : 'No price history') }}
                </div>
                <VueApexCharts
                  v-else
                  :key="p.symbol + '-' + chartStyle + '-' + currentRange"
                  :type="chartType"
                  :height="chartStyle === 'candle' ? 110 : 80"
                  :options="positionChartOptions(p.symbol)"
                  :series="positionSeries(p.symbol)"
                />
              </div>
              <div class="grid grid-cols-4 xl:grid-cols-2 gap-x-3 gap-y-3 pt-3 border-t border-white/[0.06]">
                <div class="min-w-0">
                  <div class="text-xs text-slate-500">{{ quantityLabel(p) }}</div>
                  <div class="mt-0.5 text-[13px] text-slate-300 tabular-nums truncate" :title="String(p.qty || 0)">{{ displayQuantity(p, 8) }}</div>
                </div>
                <div class="min-w-0">
                  <div class="text-xs text-slate-500">Last</div>
                  <div class="mt-0.5 text-[13px] text-slate-300 tabular-nums truncate">{{ fmtMoney(p.last_price) }}</div>
                </div>
                <div class="min-w-0">
                  <div class="text-xs text-slate-500">Entry</div>
                  <div class="mt-0.5 text-[13px] text-slate-300 tabular-nums truncate">{{ p.avg_entry_price ? fmtMoney(p.avg_entry_price) : '—' }}</div>
                </div>
                <div class="min-w-0">
                  <div class="text-xs text-slate-500">P&amp;L $</div>
                  <div
                    class="mt-0.5 text-[13px] tabular-nums truncate"
                    :class="p.avg_entry_price ? pnlColorClass(p.unrealized_pnl) : 'text-slate-500'"
                  >{{ p.avg_entry_price ? fmtMoney(p.unrealized_pnl) : '—' }}</div>
                </div>
              </div>
              <div v-if="canClosePosition(p)" class="flex justify-end mt-3">
                <button
                  @click="openClosePosModal(p.symbol)"
                  class="ui-btn text-loss"
                  title="Close position (market sell)"
                >
                  <span class="material-symbols-outlined text-[16px]">logout</span>
                  Close
                </button>
              </div>
              <p v-else class="mt-3 text-xs text-slate-500 text-right leading-snug">
                Managed by the wheel lane, which buys puts back automatically. Close it at the broker if needed.
              </p>
            </div>
          </div>
        </section>
      </div>

      <!-- Loading placeholder -->
      <div v-else class="mt-8" aria-busy="true">
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-8" aria-hidden="true">
          <div class="h-[520px] rounded-xl bg-surface"></div>
          <div class="h-[520px] rounded-xl bg-surface"></div>
        </div>
        <p class="mt-3 text-sm text-slate-500">Loading live state…</p>
        <p v-if="lastError" class="mt-1 text-sm text-loss">{{ lastError }}</p>
      </div>

      <!-- Live log panel (always visible once we have any state; gracefully shows "not running" otherwise) -->
      <section class="mt-12" aria-labelledby="lt-logs">
        <div class="mb-3">
          <h2 id="lt-logs" class="ui-section-title">Live logs</h2>
        </div>
        <InstanceLiveLogs :instance-id="instanceId" />
      </section>

      <!-- Sticky mobile halt button — only on small screens when not already halted -->
      <button
        v-if="!notRunning"
        @click="openHaltModal"
        class="sm:hidden fixed bottom-20 right-4 z-20 inline-flex items-center gap-1.5 h-12 px-5 rounded-full text-sm font-medium border border-loss/30 bg-raised text-loss shadow-[0_8px_24px_rgba(0,0,0,0.5)]"
      >
        <span class="material-symbols-outlined text-[18px]">block</span>
        Halt
      </button>

      <!-- Command toast -->
      <div
        v-if="commandToast"
        class="fixed bottom-4 left-4 sm:left-auto sm:right-4 z-30 max-w-sm bg-raised border rounded-xl shadow-[0_16px_40px_rgba(0,0,0,0.5)] px-4 py-3 text-xs"
        :class="commandToast.status === 'failed' ? 'border-loss/30' : 'border-white/[0.08]'"
      >
        <div class="flex items-start gap-2.5">
          <span
            class="material-symbols-outlined text-[16px] shrink-0"
            :class="{
              'text-slate-400 animate-spin': commandToast.status === 'pending' || commandToast.status === 'running',
              'text-gain': commandToast.status === 'completed',
              'text-loss': commandToast.status === 'failed',
            }"
          >{{ ['pending','running'].includes(commandToast.status) ? 'progress_activity' : (commandToast.status === 'completed' ? 'task_alt' : 'error') }}</span>
          <div class="min-w-0 flex-1">
            <p class="text-sm text-slate-100">{{ String(commandToast.type || '').replace(/_/g, ' ') }} <span class="text-slate-500">· {{ commandToast.status }}</span></p>
            <p v-if="commandToast.error" class="text-loss mt-1 break-words">{{ commandToast.error }}</p>
            <p v-else-if="commandToast.result && Object.keys(commandToast.result).length" class="text-slate-400 mt-1 break-words font-mono">
              {{ JSON.stringify(commandToast.result) }}
            </p>
          </div>
          <button @click="commandToast = null" class="ui-icon-btn size-6 -mr-1 shrink-0" aria-label="Dismiss">
            <span class="material-symbols-outlined text-[16px]">close</span>
          </button>
        </div>
      </div>

      <!-- Halt modal -->
      <Teleport to="body">
        <div
          v-if="haltModalOpen"
          class="ui-overlay z-[120]"
          @click.self="closeHaltModal"
        >
          <div class="ui-modal max-w-md">
            <div class="ui-modal-head">
              <div class="min-w-0">
                <h2 class="text-base font-medium text-slate-100">Halt live trading</h2>
                <p class="text-sm text-slate-400 mt-1">This cancels all open orders and stops the running broker. It <span class="text-slate-100">does not</span> liquidate open positions.</p>
              </div>
            </div>
            <div class="ui-modal-body space-y-4">
              <div>
                <label class="ui-field-label">Reason <span class="text-slate-500">(optional)</span></label>
                <input
                  v-model="haltReasonInput"
                  type="text"
                  class="ui-input"
                  placeholder="e.g. risk breach"
                />
              </div>
              <div>
                <label class="ui-field-label">Type <span class="font-mono text-loss">HALT</span> to confirm</label>
                <input
                  v-model="haltConfirmInput"
                  type="text"
                  class="ui-input font-mono"
                  placeholder="HALT"
                />
              </div>
            </div>
            <div class="ui-modal-foot">
              <button @click="closeHaltModal" class="ui-btn-ghost h-11 sm:h-9 px-4">Cancel</button>
              <button
                @click="confirmHalt"
                :disabled="haltConfirmInput.trim().toUpperCase() !== 'HALT'"
                class="ui-btn text-loss h-11 sm:h-9 px-4"
              >Halt now</button>
            </div>
          </div>
        </div>
      </Teleport>

      <!-- Close-position modal -->
      <Teleport to="body">
        <div
          v-if="closePosModalOpen"
          class="ui-overlay z-[120]"
          @click.self="closeClosePosModal"
        >
          <div class="ui-modal max-w-md">
            <div class="ui-modal-head">
              <div class="min-w-0">
                <h2 class="text-base font-medium text-slate-100">Close position</h2>
                <p class="text-sm text-slate-400 mt-1">Submit a market sell for the full quantity of <span class="text-slate-100 font-medium">{{ closePosSymbol }}</span>.</p>
              </div>
            </div>
            <div class="ui-modal-body">
              <label class="ui-field-label">Type <span class="font-mono text-loss">CLOSE {{ closePosSymbol }}</span> to confirm</label>
              <input
                v-model="closePosConfirmInput"
                type="text"
                class="ui-input font-mono"
                :placeholder="`CLOSE ${closePosSymbol}`"
              />
            </div>
            <div class="ui-modal-foot">
              <button @click="closeClosePosModal" class="ui-btn-ghost h-11 sm:h-9 px-4">Cancel</button>
              <button
                @click="confirmClosePosition"
                :disabled="closePosConfirmInput.trim().toUpperCase() !== `CLOSE ${closePosSymbol}`"
                class="ui-btn text-loss h-11 sm:h-9 px-4"
              >Submit sell</button>
            </div>
          </div>
        </div>
      </Teleport>

      <!-- Manual order modal -->
      <Teleport to="body">
        <div
          v-if="orderModalOpen"
          class="ui-overlay z-[120] items-end sm:items-center p-0 sm:p-4"
          @click.self="closeOrderModal"
        >
          <div class="ui-modal sm:max-w-lg max-h-[92dvh] flex flex-col rounded-b-none sm:rounded-2xl">
            <div class="ui-modal-head shrink-0">
              <div class="min-w-0">
                <h2 class="text-base font-medium text-slate-100">Manual order</h2>
                <p class="text-sm text-slate-500 mt-1">Sent to this instance's broker</p>
              </div>
            </div>
            <div class="ui-modal-body space-y-4 overflow-y-auto">
              <div>
                <label class="ui-field-label">Symbol</label>
                <input
                  v-model="orderForm.symbol"
                  type="text"
                  class="ui-input font-mono uppercase"
                  placeholder="AAPL"
                />
              </div>
              <div class="grid grid-cols-2 gap-4">
                <div>
                  <label class="ui-field-label">Side</label>
                  <select v-model="orderForm.side" class="ui-input max-sm:h-11">
                    <option value="buy">Buy</option>
                    <option value="sell">Sell</option>
                  </select>
                </div>
                <div>
                  <label class="ui-field-label">Order type</label>
                  <select v-model="orderForm.order_type" class="ui-input max-sm:h-11">
                    <option value="market">Market</option>
                    <option value="limit">Limit</option>
                  </select>
                </div>
              </div>
              <div>
                <div class="grid grid-cols-2 gap-4">
                  <div>
                    <label class="ui-field-label">Qty (shares)</label>
                    <input
                      v-model="orderForm.qty"
                      type="number"
                      min="0"
                      step="any"
                      class="ui-input tabular-nums"
                      placeholder="0"
                    />
                  </div>
                  <div>
                    <label class="ui-field-label">Notional ($)</label>
                    <input
                      v-model="orderForm.notional"
                      type="number"
                      min="0"
                      step="any"
                      class="ui-input tabular-nums"
                      placeholder="0.00"
                    />
                  </div>
                </div>
                <p class="ui-help">Fill qty or notional, not both</p>
              </div>
              <div v-if="orderForm.order_type === 'limit'">
                <label class="ui-field-label">Limit price</label>
                <input
                  v-model="orderForm.limit_price"
                  type="number"
                  min="0"
                  step="any"
                  class="ui-input tabular-nums"
                  placeholder="0.00"
                />
              </div>
              <div class="grid grid-cols-2 gap-4">
                <div>
                  <label class="ui-field-label">TIF</label>
                  <select v-model="orderForm.tif" class="ui-input max-sm:h-11">
                    <option value="day">Day</option>
                    <option value="gtc">GTC</option>
                    <option value="ioc">IOC</option>
                    <option value="fok">FOK</option>
                    <option value="opg">OPG</option>
                    <option value="cls">CLS</option>
                  </select>
                </div>
                <div class="flex items-end">
                  <label
                    class="flex items-center gap-2 text-sm min-h-[44px] sm:min-h-9"
                    :class="(orderForm.order_type !== 'limit' || orderForm.tif !== 'day') ? 'text-slate-600 cursor-not-allowed' : 'text-slate-300'"
                    :title="(orderForm.order_type !== 'limit' || orderForm.tif !== 'day') ? 'Requires order_type=limit + tif=day' : ''"
                  >
                    <input
                      v-model="orderForm.extended_hours"
                      :disabled="orderForm.order_type !== 'limit' || orderForm.tif !== 'day'"
                      type="checkbox"
                      class="size-4 accent-primary disabled:opacity-40 disabled:cursor-not-allowed"
                    />
                    Extended hours
                  </label>
                </div>
              </div>
            </div>
            <div class="ui-modal-foot shrink-0">
              <button @click="closeOrderModal" class="ui-btn-ghost h-11 sm:h-9 px-4">Cancel</button>
              <button
                @click="confirmOrder"
                class="ui-btn-primary h-11 sm:h-9"
              >Submit order</button>
            </div>
          </div>
        </div>
      </Teleport>
    </main>
  </AppShell>
</template>

<style scoped>
.no-scrollbar::-webkit-scrollbar { display: none; }
.no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
/* Executions: phone shows execution · total (qty and price ride in the subline). */
.lt-exec-grid {
  grid-template-columns: minmax(0, 1fr) 104px;
  column-gap: 16px;
}
@media (min-width: 640px) {
  .lt-exec-grid { grid-template-columns: minmax(0, 1fr) 80px 96px 104px; }
}
</style>
