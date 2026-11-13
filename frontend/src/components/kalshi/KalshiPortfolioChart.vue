<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import VueApexCharts from 'vue3-apexcharts'
import { getToken } from '../../utils/auth.js'

const props = defineProps({
  brokerageId: { type: String, required: true },
  // When true the instance is placing REAL orders -> show the real portfolio value
  // curve, never the paper P&L curve (even if leftover paper snapshots exist).
  isReal: { type: Boolean, default: false },
})

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')
function authHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

const RANGES = ['1D', '1W', '1M', 'ALL']
const activeRange = ref('1W')
const value = ref(0)
const dayChange = ref(0)
const series = ref([])
const paperSeries = ref([])      // progress-over-time of paper P&L (paper instances)
const paperPnl = ref(null)
const loading = ref(false)
const error = ref('')

async function load() {
  if (!props.brokerageId) return
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`${API_BASE}/brokerages/${props.brokerageId}/kalshi/portfolio`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const d = await res.json()
    value.value = d.value || 0
    dayChange.value = d.day_change || 0
    series.value = (d.series || []).map((p) => [p.ts, p.value])
    paperSeries.value = (d.paper_series || []).map((p) => [p.ts, p.pnl])
    paperPnl.value = (d.paper_pnl != null ? d.paper_pnl : null)
  } catch (e) {
    error.value = String(e.message || e)
    series.value = []
    paperSeries.value = []
  } finally {
    loading.value = false
  }
}

let timer = null
onMounted(() => { load(); timer = setInterval(load, 10000) })   // auto-refresh every 10s
onUnmounted(() => { if (timer) clearInterval(timer) })
watch(() => props.brokerageId, load)

// Paper instances have a static demo broker balance, so when paper P&L history exists
// we show the paper P&L PROGRESS curve instead of the (flat) portfolio value.
const isPaper = computed(() => !props.isReal && paperSeries.value.length > 0)
const activeSeries = computed(() => (isPaper.value ? paperSeries.value : series.value))

// Slice the full server series to the selected range client-side. 'ALL' shows
// everything; unparseable timestamps fall through so the chart never goes blank.
const displayed = computed(() => {
  const src = activeSeries.value
  if (activeRange.value === 'ALL') return src
  const days = { '1D': 1, '1W': 7, '1M': 30 }[activeRange.value] || 36500
  const cutoff = Date.now() - days * 86400000
  const sliced = src.filter(([ts]) => {
    const t = Date.parse(ts)
    return Number.isNaN(t) ? true : t >= cutoff
  })
  return sliced.length ? sliced : src
})

const headlineLabel = computed(() => (isPaper.value ? 'Paper P&L · progress' : 'Portfolio value'))
const headlineValue = computed(() => (isPaper.value ? (paperPnl.value || 0) : value.value))
const fmtValue = computed(() => {
  const v = headlineValue.value
  const sign = isPaper.value && v < 0 ? '-' : ''
  return `${sign}$${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
})

// Delta: paper -> change across the displayed window; real -> backend day_change.
const delta = computed(() => {
  if (isPaper.value) {
    const d = displayed.value
    return d.length >= 2 ? (d[d.length - 1][1] - d[0][1]) : 0
  }
  return dayChange.value
})
const positive = computed(() => delta.value >= 0)

// Numeric-index x (like LiveTradingView) so ApexCharts actually plots the line — a
// 'category' axis with [isoString, value] tuples computes the y-range but draws no line.
// Range filtering already happened in `displayed`; labels are hidden so the index never shows.
const chartSeries = computed(() => [{ name: isPaper.value ? 'Paper P&L' : 'Value', data: displayed.value.map((p, i) => [i, p[1]]) }])
// Gain/loss colour when up/down — for BOTH the real portfolio value and paper
// P&L (matches mobile and the dashboard chart).
const chartColor = computed(() => (positive.value ? '#4cc38a' : '#e5484d'))
const axisLabel = { colors: '#71717a', fontSize: '11px', fontFamily: 'inherit' }
const chartOptions = computed(() => ({
  chart: { type: 'area', height: 180, toolbar: { show: false }, animations: { enabled: false }, background: 'transparent', fontFamily: 'Inter, sans-serif', parentHeightOffset: 0 },
  theme: { mode: 'dark' },
  colors: [chartColor.value],
  dataLabels: { enabled: false },
  stroke: { curve: 'smooth', width: 1.5 },
  // A faint same-hue fade under the line, as on the dashboard.
  fill: { type: 'gradient', gradient: { colorStops: [{ offset: 0, color: chartColor.value, opacity: 0.1 }, { offset: 100, color: chartColor.value, opacity: 0 }] } },
  grid: { borderColor: 'rgba(255,255,255,0.05)', strokeDashArray: 0, xaxis: { lines: { show: false } }, padding: { left: 4, right: 8, top: 0, bottom: 0 } },
  xaxis: { type: 'numeric', labels: { show: false }, axisBorder: { show: false }, axisTicks: { show: false }, tooltip: { enabled: false } },
  yaxis: { tickAmount: 3, labels: { formatter: (v) => `$${Math.round(v)}`, style: axisLabel } },
  tooltip: { theme: 'dark', x: { show: false } },
}))
// What the headline change is measured against.
const RANGE_CAPTIONS = { '1D': 'Past day', '1W': 'Past week', '1M': 'Past month', ALL: 'All time' }
const deltaCaption = computed(() => (isPaper.value ? RANGE_CAPTIONS[activeRange.value] : 'Today'))
</script>

<template>
  <div class="ui-card overflow-hidden flex flex-col min-w-0">
    <div class="px-5 pt-5 pb-3">
      <!-- Header -->
      <div class="flex items-center justify-between gap-3">
        <p class="text-sm font-medium text-slate-200 truncate">{{ headlineLabel }}</p>
        <span v-if="isPaper" class="shrink-0 text-xs text-slate-500">Mock</span>
      </div>

      <!-- Value + delta -->
      <p class="mt-3 text-2xl font-medium leading-none tabular-nums break-all"
         :class="isPaper ? (headlineValue >= 0 ? 'text-gain' : 'text-loss') : 'text-slate-100'">{{ fmtValue }}</p>
      <p class="mt-2 text-sm tabular-nums">
        <span :class="positive ? 'text-gain' : 'text-loss'">{{ positive ? '+' : '-' }}${{ Math.abs(delta).toFixed(2) }}</span>
        <span class="text-slate-500 ml-1.5">{{ deltaCaption }}</span>
      </p>
      <p v-if="error" class="mt-2 text-sm text-loss">{{ error }}</p>

      <!-- Range selector -->
      <div class="ui-seg mt-4" role="group" aria-label="Range">
        <button v-for="r in RANGES" :key="r" type="button" @click="activeRange = r"
                class="ui-seg-btn tabular-nums"
                :class="activeRange === r ? 'ui-seg-btn-active' : ''"
                :aria-pressed="activeRange === r">
          {{ r }}
        </button>
      </div>
    </div>

    <!-- Chart area -->
    <div class="relative flex-1 min-h-[180px] px-1 pb-2">
      <div v-if="loading" class="absolute inset-0"></div>
      <div v-else-if="!displayed.length" class="absolute inset-0 flex items-center justify-center">
        <p class="text-sm text-slate-500">No snapshots yet</p>
      </div>
      <VueApexCharts v-else type="area" height="180" :options="chartOptions" :series="chartSeries" />
    </div>
  </div>
</template>
