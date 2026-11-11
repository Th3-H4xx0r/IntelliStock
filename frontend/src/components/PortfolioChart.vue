<script setup>
import { ref, computed, onMounted } from 'vue'
import VueApexCharts from 'vue3-apexcharts'
import { getToken } from '../utils/auth.js'

const props = defineProps({
  account: { type: Object, required: true }, // {id, account_name, brokerage_type, status, ...}
  // 'card' — bordered tile (Kalshi pages, chat). 'hero' — the dashboard's
  // headline figure and large chart, drawn straight on the page.
  variant: { type: String, default: 'card' },
})
const isHero = computed(() => props.variant === 'hero')

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

// ── State ─────────────────────────────────────────────────────────────────────
const RANGES = ['1D', '1W', '1M', '3M', 'YTD', '1Y', 'ALL']
// The dashboard hero opens on today, like the account list beside it.
const activeRange = ref(props.variant === 'hero' ? '1D' : '1M')
const loading     = ref(false)
const error       = ref('')
const scrubIndex  = ref(null)
const isScrubbing = ref(false)
const scrubLinePercent = ref(null)

const portfolioData = ref(null)
// portfolioData shape: { timestamps[], values[], current_value, open_value, change_abs, change_pct }

// ── Helpers ───────────────────────────────────────────────────────────────────
function authHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

const isKalshi = computed(
  () => String(props.account?.brokerage_type || '').toLowerCase() === 'kalshi'
)

// Kalshi keeps its equity curve behind its own endpoint and returns a
// different shape. The generic /portfolio-history route is Alpaca-only and
// answers "Unsupported brokerage type: kalshi", which rendered as a red error
// on every Kalshi card. Map its payload onto the chart's shape instead.
function fromKalshi(payload) {
  const series = Array.isArray(payload?.series) ? payload.series : []
  const values = series.map((p) => Number(p.value) || 0)
  const openValue = values.length ? values[0] : 0
  const currentValue = Number(payload?.value) || (values.length ? values[values.length - 1] : 0)
  const changeAbs = Number(payload?.day_change) || 0
  return {
    timestamps: series.map((p) => new Date(p.ts).getTime()),
    values,
    current_value: currentValue,
    open_value: openValue,
    change_abs: changeAbs,
    change_pct: openValue ? (changeAbs / openValue) * 100 : 0,
  }
}

async function loadHistory(range) {
  loading.value = true
  error.value   = ''
  scrubIndex.value = null
  scrubLinePercent.value = null
  try {
    const url = isKalshi.value
      ? `${API_BASE}/brokerages/${props.account.id}/kalshi/portfolio`
      : `${API_BASE}/brokerages/${props.account.id}/portfolio-history?range=${range}`
    const res = await fetch(url, { headers: authHeaders() })
    if (!res.ok) {
      const d = await res.json().catch(() => ({}))
      throw new Error(d.detail || `HTTP ${res.status}`)
    }
    const body = await res.json()
    portfolioData.value = isKalshi.value ? fromKalshi(body) : body
  } catch (e) {
    error.value = e.message || 'Failed to load portfolio history'
    portfolioData.value = null
  } finally {
    loading.value = false
  }
}

function setRange(r) {
  if (activeRange.value === r) return
  activeRange.value = r
  scrubIndex.value = null
  scrubLinePercent.value = null
  loadHistory(r)
}

// ── Computed display ─────────────────────────────────────────────────────────
const activeDataIndex = computed(() => {
  const values = portfolioData.value?.values
  if (!values?.length || scrubIndex.value == null) return null
  return Math.min(Math.max(scrubIndex.value, 0), values.length - 1)
})

const activeValueRaw = computed(() => {
  const d = portfolioData.value
  if (!d) return null
  if (activeDataIndex.value != null) return d.values?.[activeDataIndex.value] ?? null
  return d.current_value ?? null
})

const currentValue = computed(() => {
  const v = activeValueRaw.value
  if (v == null) return '—'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(v)
})

const changeAbs = computed(() => {
  const d = portfolioData.value
  if (!d) return null
  if (activeDataIndex.value != null) {
    const baseline = d.open_value ?? d.values?.[0]
    const active = activeValueRaw.value
    if (baseline == null || active == null) return null
    return active - baseline
  }
  const v = d.change_abs
  if (v == null) return null
  return v
})

const changePct = computed(() => {
  const d = portfolioData.value
  if (!d) return null
  if (activeDataIndex.value != null) {
    const baseline = d.open_value ?? d.values?.[0]
    const abs = changeAbs.value
    if (baseline == null || abs == null || baseline === 0) return null
    return (abs / baseline) * 100
  }
  const v = d.change_pct
  if (v == null) return null
  return v
})

const isPositive = computed(() => (changeAbs.value ?? 0) >= 0)
const chartIsPositive = computed(() => (portfolioData.value?.change_abs ?? 0) >= 0)

const changeAbsFormatted = computed(() => {
  const v = changeAbs.value
  if (v == null) return '—'
  const sign = v >= 0 ? '+' : '−'
  return sign + new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(Math.abs(v))
})

const changePctFormatted = computed(() => {
  const v = changePct.value
  if (v == null) return ''
  const sign = v >= 0 ? '+' : '−'
  return `(${sign}${Math.abs(v).toFixed(2)}%)`
})

// What the change is measured against. Kalshi's endpoint only reports the
// day change, so it is always "Today".
const RANGE_CAPTIONS = {
  '1D': 'Today', '1W': 'Past week', '1M': 'Past month', '3M': 'Past 3 months',
  YTD: 'Year to date', '1Y': 'Past year', ALL: 'All time',
}
const changeCaption = computed(() => {
  if (activeDataIndex.value != null) {
    const t = normalizedTimestamps.value[activeDataIndex.value]
    if (!Number.isFinite(t)) return ''
    const withTime = activeRange.value === '1D' || activeRange.value === '1W' || isKalshi.value
    return new Date(t).toLocaleString('en-US', withTime
      ? { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }
      : { month: 'short', day: 'numeric', year: 'numeric' })
  }
  return isKalshi.value ? 'Today' : RANGE_CAPTIONS[activeRange.value] || ''
})

const brokerageLabel = computed(() => {
  const type = String(props.account.brokerage_type || '').toLowerCase()
  const name = type === 'alpaca' ? 'Alpaca' : type === 'kalshi' ? 'Kalshi' : (props.account.brokerage_type || 'Brokerage')
  const paper = type === 'alpaca' ? props.account.alpaca_paper : /paper|demo/i.test(props.account.account_name || '')
  return `${name} · ${paper ? 'Paper' : 'Live'}`
})

const lineColor = computed(() => (chartIsPositive.value ? '#4cc38a' : '#e5484d'))

// ── ApexCharts config ─────────────────────────────────────────────────────────
const chartSeries = computed(() => {
  const d = portfolioData.value
  if (!d || !d.timestamps?.length) return []
  return [{
    name: 'Portfolio Value',
    data: d.timestamps.map((t, i) => [t, d.values[i]]),
  }]
})

const normalizedTimestamps = computed(() => {
  const ts = portfolioData.value?.timestamps || []
  return ts.map((t) => {
    if (typeof t === 'number') return t
    const parsed = Date.parse(t)
    return Number.isFinite(parsed) ? parsed : NaN
  })
})

const scrubLineLeft = computed(() => {
  const idx = activeDataIndex.value
  const ts = normalizedTimestamps.value
  if (scrubLinePercent.value != null) return scrubLinePercent.value
  if (idx == null || ts.length <= 1) return 0
  const minTs = ts[0]
  const maxTs = ts[ts.length - 1]
  const selTs = ts[idx]
  if (!Number.isFinite(minTs) || !Number.isFinite(maxTs) || !Number.isFinite(selTs) || maxTs <= minTs) {
    return (idx / (ts.length - 1)) * 100
  }
  return ((selTs - minTs) / (maxTs - minTs)) * 100
})

function nearestTimestampIndex(targetTs, timestamps) {
  let lo = 0
  let hi = timestamps.length - 1
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2)
    if (timestamps[mid] < targetTs) lo = mid + 1
    else hi = mid
  }
  if (lo <= 0) return 0
  const prev = lo - 1
  return Math.abs(timestamps[lo] - targetTs) < Math.abs(timestamps[prev] - targetTs) ? lo : prev
}

function updateScrubIndex(clientX, overlayEl) {
  const values = portfolioData.value?.values
  const timestamps = normalizedTimestamps.value
  if (!values?.length || !overlayEl) return

  const overlayRect = overlayEl.getBoundingClientRect()
  const gridEl = overlayEl.parentElement?.querySelector('.apexcharts-grid')
  const plotRect = gridEl?.getBoundingClientRect()
  const left = (plotRect && plotRect.width > 0) ? plotRect.left : overlayRect.left
  const width = (plotRect && plotRect.width > 0) ? plotRect.width : overlayRect.width

  const clampedX = Math.min(Math.max(clientX, left), left + width)
  scrubLinePercent.value = overlayRect.width > 0
    ? ((clampedX - overlayRect.left) / overlayRect.width) * 100
    : 0

  if (values.length === 1 || width <= 0) {
    scrubIndex.value = 0
    return
  }

  const frac = (clampedX - left) / width
  const minTs = timestamps[0]
  const maxTs = timestamps[timestamps.length - 1]
  if (
    timestamps.length === values.length &&
    timestamps.length > 1 &&
    Number.isFinite(minTs) &&
    Number.isFinite(maxTs) &&
    maxTs > minTs
  ) {
    const targetTs = minTs + frac * (maxTs - minTs)
    scrubIndex.value = nearestTimestampIndex(targetTs, timestamps)
    return
  }

  const nextIdx = Math.round(frac * (values.length - 1))
  scrubIndex.value = Math.min(Math.max(nextIdx, 0), values.length - 1)
}

function onScrubPointerDown(e) {
  isScrubbing.value = true
  updateScrubIndex(e.clientX, e.currentTarget)
  if (e.currentTarget?.setPointerCapture) {
    try { e.currentTarget.setPointerCapture(e.pointerId) } catch { /* noop */ }
  }
}

function onScrubPointerMove(e) {
  if (e.pointerType === 'mouse' || isScrubbing.value) {
    updateScrubIndex(e.clientX, e.currentTarget)
  }
}

function onScrubPointerUp(e) {
  isScrubbing.value = false
  if (e.pointerType !== 'mouse') {
    scrubIndex.value = null
    scrubLinePercent.value = null
  }
}

function onScrubPointerLeave() {
  isScrubbing.value = false
  scrubIndex.value = null
  scrubLinePercent.value = null
}

// The hero has no axis, so fit the line (and the opening baseline) to the
// plot instead of leaving Apex's rounded headroom as a dead band.
const heroRange = computed(() => {
  if (!isHero.value) return {}
  const vals = (portfolioData.value?.values || []).map(Number).filter(Number.isFinite)
  if (!vals.length) return {}
  const base = Number(portfolioData.value?.open_value ?? vals[0])
  let lo = vals[0], hi = vals[0]
  for (const v of vals) { if (v < lo) lo = v; if (v > hi) hi = v }
  if (Number.isFinite(base)) { lo = Math.min(lo, base); hi = Math.max(hi, base) }
  const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.002 || 1
  return { min: lo - pad, max: hi + pad, forceNiceScale: false }
})

const usd = (n, digits = 2) => new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', minimumFractionDigits: digits, maximumFractionDigits: digits,
}).format(n)

const chartOptions = computed(() => {
  const color = lineColor.value
  const hero = isHero.value
  const range = activeRange.value
  const xFmt = (range === '1D')
    ? 'HH:mm'
    : (range === '1W' || range === '1M')
      ? 'MMM dd'
      : 'MMM yyyy'
  const base = portfolioData.value?.open_value ?? portfolioData.value?.values?.[0]
  const axisLabel = { colors: '#85858b', fontSize: '11px', fontFamily: 'inherit' }

  return {
    chart: {
      type: 'area',
      background: 'transparent',
      fontFamily: 'Inter, sans-serif',
      toolbar: { show: false },
      zoom: { enabled: false },
      sparkline: { enabled: false },
      animations: { enabled: true, easing: 'easeout', speed: 300 },
      parentHeightOffset: 0,
    },
    colors: [color],
    fill: {
      type: 'gradient',
      gradient: {
        colorStops: [
          { offset: 0,   color, opacity: hero ? 0.12 : 0.10 },
          { offset: 100, color, opacity: 0 },
        ],
      },
    },
    stroke: { curve: 'smooth', width: hero ? 2 : 1.5 },
    dataLabels: { enabled: false },
    grid: {
      borderColor: 'rgba(255,255,255,0.05)',
      strokeDashArray: 0,
      xaxis: { lines: { show: false } },
      yaxis: { lines: { show: !hero } },
      padding: { left: hero ? 0 : 4, right: hero ? 0 : 8, top: 0, bottom: 0 },
    },
    // Dotted line at the period's opening value — above it is a gain,
    // below it a loss.
    annotations: Number.isFinite(base) ? {
      yaxis: [{ y: base, borderColor: 'rgba(237,237,237,0.28)', strokeDashArray: 2, borderWidth: 1 }],
    } : {},
    xaxis: {
      type: 'datetime',
      labels: { show: !hero, style: axisLabel, datetimeUTC: false, format: xFmt },
      axisBorder: { show: false },
      axisTicks: { show: false },
      crosshairs: { show: false },
      tooltip: { enabled: false },
    },
    yaxis: {
      show: !hero,
      opposite: true,
      ...heroRange.value,
      labels: {
        style: axisLabel,
        formatter: (v) => {
          const n = Number(v)
          if (!Number.isFinite(n)) return ''
          return usd(n, Math.abs(n) >= 1000 ? 0 : 2)
        },
      },
      tickAmount: 3,
    },
    tooltip: { enabled: false },
    markers: { size: 0, hover: { size: 0 } },
    theme: { mode: 'dark' },
  }
})

onMounted(() => loadHistory(activeRange.value))
</script>

<template>
  <div
    class="flex flex-col min-w-0"
    :class="isHero ? '' : 'rounded-xl border border-white/[0.07] bg-surface overflow-hidden'"
  >
    <!-- Header: account, value, change -->
    <div :class="isHero ? 'pb-4' : 'px-5 pt-5 pb-3'">
      <div class="flex items-baseline justify-between gap-3">
        <p class="min-w-0 truncate" :class="isHero ? 'text-sm text-slate-400' : 'text-sm font-medium text-slate-200'">
          <span v-if="isHero" class="text-slate-200 font-medium">{{ account.account_name }}</span>
          <template v-else>{{ account.account_name }}</template>
        </p>
        <span v-if="account.status && account.status !== 'active'" class="shrink-0 text-xs text-loss">{{ account.status }}</span>
      </div>
      <p class="text-xs text-slate-500 mt-1">{{ brokerageLabel }}</p>

      <div v-if="portfolioData && !loading" :class="isHero ? 'mt-4' : 'mt-3'">
        <p
          class="font-medium text-slate-100 tabular-nums leading-none"
          :class="isHero ? 'text-[44px] tracking-[-0.02em]' : 'text-2xl'"
        >{{ currentValue }}</p>
        <p class="mt-2 text-sm tabular-nums">
          <span :class="isPositive ? 'text-gain' : 'text-loss'">{{ changeAbsFormatted }} {{ changePctFormatted }}</span>
          <span class="text-slate-500 ml-1.5">{{ changeCaption }}</span>
        </p>
      </div>
      <div v-else-if="loading" :class="isHero ? 'mt-4 space-y-3' : 'mt-3 space-y-2'">
        <div class="rounded-md bg-raised" :class="isHero ? 'h-11 w-64' : 'h-6 w-36'"></div>
        <div class="h-4 w-40 rounded bg-raised"></div>
      </div>
      <p v-else-if="error" class="mt-3 text-sm text-loss">{{ error }}</p>
    </div>

    <!-- Chart -->
    <div
      class="relative"
      :class="isHero ? 'h-[300px]' : 'h-[180px] px-1'"
    >
      <div
        v-if="activeDataIndex != null"
        class="absolute top-0 bottom-0 w-px bg-white/30 pointer-events-none z-20"
        :style="{ left: `${scrubLineLeft}%` }"
      ></div>

      <div
        v-if="portfolioData && portfolioData.timestamps?.length && !loading"
        class="absolute inset-0 z-10 cursor-crosshair touch-none"
        @pointerdown="onScrubPointerDown"
        @pointermove="onScrubPointerMove"
        @pointerup="onScrubPointerUp"
        @pointercancel="onScrubPointerLeave"
        @pointerleave="onScrubPointerLeave"
      ></div>

      <div v-if="loading" class="absolute inset-0"></div>

      <div v-else-if="!portfolioData || !portfolioData.timestamps?.length" class="absolute inset-0 flex items-center justify-center">
        <p class="text-sm text-slate-500">{{ error || 'No data for this range' }}</p>
      </div>

      <VueApexCharts
        v-else
        type="area"
        :height="isHero ? 300 : 180"
        :options="chartOptions"
        :series="chartSeries"
      />
    </div>

    <!-- Range. Hidden for Kalshi: its portfolio endpoint returns one fixed
         snapshot series and takes no range, so the buttons would offer a
         filter that does nothing. -->
    <div
      v-if="!isKalshi"
      class="flex items-center gap-1"
      :class="isHero ? 'mt-4 pb-4 border-b border-white/[0.06]' : 'px-4 py-3 border-t border-white/[0.05]'"
    >
      <button
        v-for="r in RANGES"
        :key="r"
        @click="setRange(r)"
        class="h-8 px-3 rounded-md text-xs font-medium tabular-nums transition-colors"
        :class="activeRange === r ? 'bg-white/[0.06]' : 'text-slate-500 hover:text-slate-200'"
        :style="activeRange === r ? { color: lineColor } : null"
      >
        {{ r }}
      </button>
    </div>
  </div>
</template>
