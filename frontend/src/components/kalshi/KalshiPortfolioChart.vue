<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import VueApexCharts from 'vue3-apexcharts'
import { getToken } from '../../utils/auth.js'

const props = defineProps({
  brokerageId: { type: String, required: true },
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
  } catch (e) {
    error.value = String(e.message || e)
    series.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => props.brokerageId, load)

const positive = computed(() => dayChange.value >= 0)

// Slice the full server series to the selected range client-side (the backend
// returns the whole snapshot history). 'ALL' shows everything; unparseable
// timestamps fall through to the full series so the chart never goes blank.
const displayed = computed(() => {
  if (activeRange.value === 'ALL') return series.value
  const days = { '1D': 1, '1W': 7, '1M': 30 }[activeRange.value] || 36500
  const cutoff = Date.now() - days * 86400000
  const sliced = series.value.filter(([ts]) => {
    const t = Date.parse(ts)
    return Number.isNaN(t) ? true : t >= cutoff
  })
  return sliced.length ? sliced : series.value
})

const fmtValue = computed(() => `$${value.value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)
const chartSeries = computed(() => [{ name: 'Value', data: displayed.value }])
const chartOptions = computed(() => ({
  chart: { type: 'area', height: 180, toolbar: { show: false }, animations: { enabled: false }, background: 'transparent', fontFamily: 'Inter, sans-serif' },
  theme: { mode: 'dark' },
  colors: ['#a78bfa'],
  dataLabels: { enabled: false },
  stroke: { curve: 'smooth', width: 2.5 },
  fill: { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.3, opacityTo: 0 } },
  grid: { borderColor: 'rgba(188,154,255,0.08)', strokeDashArray: 4, padding: { left: 8, right: 8 } },
  xaxis: { type: 'category', labels: { show: false }, axisBorder: { show: false }, axisTicks: { show: false }, tooltip: { enabled: false } },
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
        <span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Portfolio value</span>
      </div>

      <!-- Value + delta -->
      <div class="flex flex-wrap items-end gap-2 sm:gap-3">
        <span class="text-xl sm:text-2xl font-bold text-slate-100 tabular-nums break-all">{{ fmtValue }}</span>
        <span class="flex items-center gap-1 text-xs sm:text-sm font-semibold mb-0.5"
              :class="positive ? 'text-emerald-400' : 'text-red-400'">
          <span class="material-symbols-outlined text-[16px]">{{ positive ? 'trending_up' : 'trending_down' }}</span>
          {{ positive ? '+' : '-' }}${{ Math.abs(dayChange).toFixed(2) }}
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
      <div v-else-if="!series.length" class="absolute inset-0 flex items-center justify-center">
        <div class="text-center">
          <span class="material-symbols-outlined text-3xl text-slate-700">bar_chart_4_bars</span>
          <p class="text-xs text-slate-600 mt-2">No snapshots yet</p>
        </div>
      </div>
      <VueApexCharts v-else type="area" height="180" :options="chartOptions" :series="chartSeries" />
    </div>
  </div>
</template>
