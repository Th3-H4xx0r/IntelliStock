<script setup>
import { ref, computed, onMounted } from 'vue'
import VueApexCharts from 'vue3-apexcharts'
import { getToken } from '../utils/auth.js'

const props = defineProps({
  account: { type: Object, required: true }, // {id, account_name, brokerage_type, status, ...}
})

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

// ── State ─────────────────────────────────────────────────────────────────────
const RANGES = ['1D', '1W', '1M', '3M', 'YTD', '1Y', 'ALL']
const activeRange = ref('1M')
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

async function loadHistory(range) {
  loading.value = true
  error.value   = ''
  scrubIndex.value = null
  scrubLinePercent.value = null
  try {
    const res = await fetch(
      `${API_BASE}/brokerages/${props.account.id}/portfolio-history?range=${range}`,
      { headers: authHeaders() }
    )
    if (!res.ok) {
      const d = await res.json().catch(() => ({}))
      throw new Error(d.detail || `HTTP ${res.status}`)
    }
    portfolioData.value = await res.json()
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
  const sign = v >= 0 ? '+' : ''
  return sign + new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(v)
})

const changePctFormatted = computed(() => {
  const v = changePct.value
  if (v == null) return ''
  const sign = v >= 0 ? '+' : ''
  return `(${sign}${v.toFixed(2)}%)`
})

const brokerageIcon = computed(() =>
  props.account.brokerage_type === 'alpaca' ? 'show_chart' : 'savings'
)

const brokerageLabel = computed(() => {
  const t = props.account.brokerage_type === 'alpaca'
    ? 'Alpaca'
    : String(props.account.brokerage_type || 'Brokerage')
  const paper = props.account.brokerage_type === 'alpaca' && props.account.alpaca_paper
  return paper ? `${t} · Paper` : t
})

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

const chartOptions = computed(() => {
  const positive = chartIsPositive.value
  const lineColor = positive ? '#34d399' : '#f87171'

  // Determine x-axis format based on range
  const range = activeRange.value
  const xFmt = (range === '1D')
    ? 'HH:mm'
    : (range === '1W' || range === '1M')
      ? 'MMM dd'
      : 'MMM yyyy'

  return {
    chart: {
      type: 'area',
      background: 'transparent',
      toolbar: { show: false },
      zoom: { enabled: false },
      sparkline: { enabled: false },
      animations: {
        enabled: true,
        easing: 'easeinout',
        speed: 400,
      },
    },
    colors: [lineColor],
    fill: {
      type: 'gradient',
      gradient: {
        shadeIntensity: 1,
        opacityFrom: 0.45,
        opacityTo: 0.02,
        stops: [0, 95],
        colorStops: [
          { offset: 0,   color: lineColor, opacity: 0.35 },
          { offset: 100, color: lineColor, opacity: 0.0  },
        ],
      },
    },
    stroke: {
      curve: 'smooth',
      width: 2,
    },
    dataLabels: { enabled: false },
    grid: {
      borderColor: 'rgba(255,255,255,0.05)',
      strokeDashArray: 4,
      xaxis: { lines: { show: false } },
      yaxis: { lines: { show: true } },
      padding: { left: 0, right: 8 },
    },
    xaxis: {
      type: 'datetime',
      labels: {
        style: { colors: '#64748b', fontSize: '11px', fontFamily: 'inherit' },
        datetimeUTC: false,
        format: xFmt,
      },
      axisBorder: { show: false },
      axisTicks: { show: false },
      crosshairs: {
        stroke: { color: 'rgba(255,255,255,0.15)', width: 1, dashArray: 4 },
      },
      tooltip: { enabled: false },
    },
    yaxis: {
      labels: {
        style: { colors: '#64748b', fontSize: '11px', fontFamily: 'inherit' },
        offsetX: -20,
        minWidth: 64,
        formatter: (v) => {
          const n = Number(v)
          if (!Number.isFinite(n)) return '$0.00'
          return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: 0,
            maximumFractionDigits: 2,
          }).format(n)
        },
      },
      tickAmount: 4,
    },
    tooltip: { enabled: false },
    markers: {
      size: 0,
      hover: { size: 4, strokeColors: lineColor, strokeWidth: 2 },
    },
    theme: { mode: 'dark' },
  }
})

onMounted(() => loadHistory(activeRange.value))
</script>

<template>
  <div class="glass-card rounded-2xl overflow-hidden flex flex-col">

    <!-- Card header: brokerage info + value + change -->
    <div class="px-4 sm:px-6 pt-4 sm:pt-5 pb-4">
      <!-- Top row: brokerage label + status -->
      <div class="flex items-start justify-between gap-2 mb-3">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-primary text-[18px]">{{ brokerageIcon }}</span>
          <span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">{{ brokerageLabel }}</span>
        </div>
        <div
          class="flex items-center gap-1.5 text-[11px] sm:text-xs font-medium"
          :class="account.status === 'active' ? 'text-emerald-400' : 'text-red-400'"
        >
          <div class="size-1.5 rounded-full" :class="account.status === 'active' ? 'bg-emerald-400' : 'bg-red-400'"></div>
          {{ account.status }}
        </div>
      </div>

      <!-- Account name -->
      <p class="text-sm font-medium text-slate-300 mb-3">{{ account.account_name }}</p>

      <!-- Value + change -->
      <div v-if="portfolioData && !loading" class="flex flex-wrap items-end gap-2 sm:gap-3">
        <span class="text-xl sm:text-2xl font-bold text-slate-100 tabular-nums break-all">{{ currentValue }}</span>
        <div
          class="flex items-center gap-1 text-xs sm:text-sm font-semibold mb-0.5"
          :class="isPositive ? 'text-emerald-400' : 'text-red-400'"
        >
          <span class="material-symbols-outlined text-[16px]">{{ isPositive ? 'trending_up' : 'trending_down' }}</span>
          <span>{{ changeAbsFormatted }} {{ changePctFormatted }}</span>
        </div>
      </div>
      <div v-else-if="loading" class="h-8 w-40 rounded-lg bg-surface animate-pulse"></div>
      <div v-else-if="error" class="text-xs text-red-400">{{ error }}</div>

      <!-- Range tabs -->
      <div class="flex flex-wrap items-center gap-1 mt-4">
        <button
          v-for="r in RANGES"
          :key="r"
          @click="setRange(r)"
          class="px-2 py-1 sm:px-2.5 rounded-md text-[11px] sm:text-xs font-semibold transition-all"
          :class="activeRange === r
            ? 'bg-primary/15 text-primary'
            : 'text-slate-500 hover:text-slate-300'"
        >
          {{ r }}
        </button>
      </div>
    </div>

    <!-- Chart area -->
    <div class="relative flex-1 min-h-[170px] sm:min-h-[180px] px-1 sm:px-2 pb-3">
      <div
        v-if="activeDataIndex != null"
        class="absolute top-2 bottom-3 w-px bg-primary/40 pointer-events-none z-20"
        :style="{ left: `${scrubLineLeft}%` }"
      ></div>

      <div
        v-if="portfolioData && portfolioData.timestamps?.length && !loading"
        class="absolute inset-0 z-10 cursor-ew-resize touch-none"
        @pointerdown="onScrubPointerDown"
        @pointermove="onScrubPointerMove"
        @pointerup="onScrubPointerUp"
        @pointercancel="onScrubPointerLeave"
        @pointerleave="onScrubPointerLeave"
      ></div>

      <!-- Loading skeleton -->
      <div v-if="loading" class="absolute inset-0 flex items-center justify-center">
        <span class="material-symbols-outlined animate-spin text-slate-600 text-3xl">progress_activity</span>
      </div>

      <!-- No data -->
      <div v-else-if="!portfolioData || !portfolioData.timestamps?.length" class="absolute inset-0 flex items-center justify-center">
        <div class="text-center">
          <span class="material-symbols-outlined text-3xl text-slate-700">bar_chart_4_bars</span>
          <p class="text-xs text-slate-600 mt-2">{{ error || 'No data for this range' }}</p>
        </div>
      </div>

      <!-- Chart -->
      <VueApexCharts
        v-else
        type="area"
        height="180"
        :options="chartOptions"
        :series="chartSeries"
      />
    </div>

  </div>
</template>
