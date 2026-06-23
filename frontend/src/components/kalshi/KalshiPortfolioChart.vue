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
const cash = ref(0)
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
    cash.value = d.cash || 0
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
const chartSeries = computed(() => [{ name: 'Value', data: displayed.value }])
const chartOptions = computed(() => ({
  chart: { type: 'area', height: 220, toolbar: { show: false }, animations: { enabled: false }, background: 'transparent' },
  theme: { mode: 'dark' },
  colors: [positive.value ? '#10b981' : '#ef4444'],
  dataLabels: { enabled: false },
  stroke: { curve: 'smooth', width: 2.5 },
  fill: { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.35, opacityTo: 0 } },
  grid: { borderColor: '#1e293b', strokeDashArray: 4 },
  xaxis: { type: 'category', labels: { show: false }, axisBorder: { show: false }, axisTicks: { show: false } },
  yaxis: { labels: { formatter: (v) => `$${Math.round(v)}`, style: { colors: '#64748b' } } },
  tooltip: { theme: 'dark', x: { show: true } },
}))
</script>

<template>
  <div class="rounded-xl border border-border-subtle bg-[#111c30] p-4">
    <div class="text-[11px] font-bold uppercase tracking-wide text-sky-300 mb-2">📊 Portfolio value</div>
    <div class="flex items-baseline gap-2">
      <span class="text-2xl font-extrabold text-slate-50">${{ value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}</span>
      <span class="text-sm font-bold" :class="positive ? 'text-emerald-400' : 'text-red-400'">
        {{ positive ? '▲' : '▼' }} ${{ Math.abs(dayChange).toFixed(2) }} today
      </span>
    </div>
    <div v-if="error" class="text-[11px] text-red-400 mt-1">{{ error }}</div>
    <VueApexCharts v-if="series.length" type="area" height="220" :options="chartOptions" :series="chartSeries" />
    <div v-else class="h-[220px] flex items-center justify-center text-xs text-slate-500">
      {{ loading ? 'Loading…' : 'No snapshots yet' }}
    </div>
    <div class="flex gap-1.5 mt-2">
      <button
        v-for="r in RANGES" :key="r"
        @click="activeRange = r"
        class="text-[10px] font-bold rounded-md px-2.5 py-1 border"
        :class="activeRange === r ? 'bg-slate-900 text-white border-slate-900' : 'text-slate-400 border-slate-600'"
      >{{ r }}</button>
    </div>
  </div>
</template>
