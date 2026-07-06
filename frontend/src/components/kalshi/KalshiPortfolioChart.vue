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
// Green when up, red when down — for BOTH the real portfolio value and paper P&L
// (matches mobile), instead of a static purple for the real line.
const chartColor = computed(() => (positive.value ? '#34d399' : '#f87171'))
const chartOptions = computed(() => ({
  chart: { type: 'area', height: 180, toolbar: { show: false }, animations: { enabled: false }, background: 'transparent', fontFamily: 'Inter, sans-serif' },
  theme: { mode: 'dark' },
  colors: [chartColor.value],
  dataLabels: { enabled: false },
  stroke: { curve: 'smooth', width: 2.5 },
  fill: { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.3, opacityTo: 0 } },
  grid: { borderColor: 'rgba(188,154,255,0.08)', strokeDashArray: 4, padding: { left: 8, right: 8 } },
  xaxis: { type: 'numeric', labels: { show: false }, axisBorder: { show: false }, axisTicks: { show: false }, tooltip: { enabled: false } },
  yaxis: { labels: { formatter: (v) => `$${Math.round(v)}`, style: { colors: '#64748b', fontSize: '11px' } } },
  tooltip: { theme: 'dark', x: { show: false } },
}))
</script>

<template>
  <div class="glass-card rounded-2xl overflow-hidden flex flex-col">
    <div class="px-4 sm:px-6 pt-4 sm:pt-5 pb-3">
      <!-- Header -->
      <div class="flex items-center gap-2 mb-3">
        <span class="material-symbols-outlined text-primary text-[18px]">monitoring</span>
        <span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">{{ headlineLabel }}</span>
        <span v-if="isPaper" class="text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">MOCK</span>
      </div>

      <!-- Value + delta -->
      <div class="flex flex-wrap items-end gap-2 sm:gap-3">
        <span class="text-xl sm:text-2xl font-bold tabular-nums break-all"
              :class="isPaper ? (headlineValue >= 0 ? 'text-emerald-400' : 'text-red-400') : 'text-slate-100'">{{ fmtValue }}</span>
        <span class="flex items-center gap-1 text-xs sm:text-sm font-semibold mb-0.5"
              :class="positive ? 'text-emerald-400' : 'text-red-400'">
          <span class="material-symbols-outlined text-[16px]">{{ positive ? 'trending_up' : 'trending_down' }}</span>
          {{ positive ? '+' : '-' }}${{ Math.abs(delta).toFixed(2) }}
        </span>
      </div>
      <div v-if="error" class="text-xs text-red-400 mt-1">{{ error }}</div>

      <!-- Range selector -->
      <div class="flex flex-wrap items-center gap-1 mt-4">
        <button v-for="r in RANGES" :key="r" @click="activeRange = r"
                class="px-2 py-1 sm:px-2.5 rounded-md text-[11px] sm:text-xs font-semibold transition-all"
                :class="activeRange === r ? 'bg-primary/15 text-primary' : 'text-slate-500 hover:text-slate-300'">
          {{ r }}
        </button>
      </div>
    </div>

    <!-- Chart area -->
    <div class="relative flex-1 min-h-[180px] px-1 sm:px-2 pb-3">
      <div v-if="loading" class="absolute inset-0 flex items-center justify-center">
        <span class="material-symbols-outlined animate-spin text-slate-600 text-3xl">progress_activity</span>
      </div>
      <div v-else-if="!displayed.length" class="absolute inset-0 flex items-center justify-center">
        <div class="text-center">
          <span class="material-symbols-outlined text-3xl text-slate-700">bar_chart_4_bars</span>
          <p class="text-xs text-slate-600 mt-2">No snapshots yet</p>
        </div>
      </div>
      <VueApexCharts v-else type="area" height="180" :options="chartOptions" :series="chartSeries" />
    </div>
  </div>
</template>
