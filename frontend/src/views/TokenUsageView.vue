<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import VueApexCharts from 'vue3-apexcharts'
import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')

const range = ref('24h')
const loading = ref(false)
const loadError = ref('')
const summary = ref(null)
const timeseries = ref([])
const topByModel = ref([])
const topByCallSite = ref([])
const recentCalls = ref([])
const selectedCall = ref(null)

let refreshTimer = null

function authHeaders() {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}` } : {}
}
function fmtUSD(v) {
  if (v == null) return '—'
  if (v === 0) return '$0.00'
  if (v < 1) return `$${v.toFixed(4)}`
  return `$${v.toFixed(2)}`
}
function fmtTokens(n) {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}
function fmtTime(ms) {
  if (!ms) return '—'
  const d = new Date(ms)
  return d.toLocaleTimeString('en-US', { hour12: false })
}

async function fetchAll() {
  loading.value = true
  loadError.value = ''
  // Use Promise.allSettled so one failing endpoint doesn't blank the whole
  // dashboard. Each section degrades independently — the widgets show stale
  // data, the chart shows "no data", the recent-calls table goes empty.
  const headers = authHeaders()
  const bucket = range.value === '24h' ? 'hour' : 'day'
  const reqs = [
    fetch(`${API_BASE}/llm-usage/summary?range=${range.value}`, { headers }).then(r => r.json()),
    fetch(`${API_BASE}/llm-usage/timeseries?range=${range.value}&bucket=${bucket}`, { headers }).then(r => r.json()),
    fetch(`${API_BASE}/llm-usage/top-spenders?range=${range.value}&group_by=model&limit=10`, { headers }).then(r => r.json()),
    fetch(`${API_BASE}/llm-usage/top-spenders?range=${range.value}&group_by=call_site&limit=10`, { headers }).then(r => r.json()),
    fetch(`${API_BASE}/llm-usage/calls?limit=50&range=now`, { headers }).then(r => r.json()),
  ]
  const results = await Promise.allSettled(reqs)
  const [s, ts, tm, tc, calls] = results
  if (s.status === 'fulfilled') summary.value = s.value
  if (ts.status === 'fulfilled') timeseries.value = Array.isArray(ts.value) ? ts.value : []
  if (tm.status === 'fulfilled') topByModel.value = Array.isArray(tm.value) ? tm.value : []
  if (tc.status === 'fulfilled') topByCallSite.value = Array.isArray(tc.value) ? tc.value : []
  if (calls.status === 'fulfilled') recentCalls.value = Array.isArray(calls.value) ? calls.value : []
  const failures = results.filter(r => r.status === 'rejected')
  loadError.value = failures.length
    ? `${failures.length} of 5 endpoints failed: ${failures[0].reason?.message || 'unknown'}`
    : ''
  loading.value = false
}

const chartSeries = computed(() => {
  // Group timeseries by provider
  const byProvider = new Map()
  for (const row of timeseries.value) {
    if (!byProvider.has(row.provider)) byProvider.set(row.provider, new Map())
    const m = byProvider.get(row.provider)
    m.set(row.bucket_start_ts, (m.get(row.bucket_start_ts) || 0) + (row.cost_usd || 0))
  }
  const series = []
  for (const [provider, m] of byProvider.entries()) {
    series.push({
      name: provider,
      data: [...m.entries()].sort((a, b) => a[0] - b[0]).map(([ts, v]) => ({ x: ts, y: v })),
    })
  }
  return series
})

const chartOptions = computed(() => ({
  chart: { type: 'bar', stacked: true, toolbar: { show: false } },
  xaxis: { type: 'datetime' },
  yaxis: { title: { text: 'Cost (USD)' } },
  tooltip: { y: { formatter: (v) => fmtUSD(v) } },
}))

// The Claude Max plan budget, in USD. Hard-coded for now; if we ever
// support multiple plans this should come from the summary endpoint.
const MAX_PLAN_BUDGET_USD = 100.0

const maxPlanPct = computed(() => {
  const est = summary.value?.max_plan_estimate_usd
  if (!est || MAX_PLAN_BUDGET_USD <= 0) return 0
  return Math.min(100, Math.round((est / MAX_PLAN_BUDGET_USD) * 100))
})

onMounted(() => {
  fetchAll()
  refreshTimer = setInterval(fetchAll, 30000)
})
onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <AppShell>
    <div class="p-4 space-y-4">
      <div class="flex items-center justify-between">
        <h1 class="text-2xl font-semibold">Token Usage</h1>
        <div class="flex gap-2 items-center">
          <button
            v-for="r in ['24h', '7d', '30d']" :key="r"
            @click="range = r; fetchAll()"
            :class="[
              'px-3 py-1 rounded border text-sm',
              range === r ? 'bg-slate-800 text-white border-slate-800' : 'bg-white border-slate-300',
            ]"
          >{{ r }}</button>
          <button @click="fetchAll" class="px-3 py-1 rounded border bg-white border-slate-300 text-sm">↻</button>
        </div>
      </div>

      <p v-if="loadError" class="text-red-600">{{ loadError }}</p>

      <!-- Widget row -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <div class="border rounded p-3 bg-white">
          <div class="text-xs text-slate-500">Period cost</div>
          <div class="text-2xl font-semibold">{{ fmtUSD(summary?.total_cost_usd) }}</div>
          <div class="text-xs text-slate-500">{{ fmtTokens(summary?.total_tokens) }} tokens · {{ summary?.total_calls ?? 0 }} calls</div>
        </div>
        <div class="border rounded p-3 bg-white">
          <div class="text-xs text-slate-500">Period calls</div>
          <div class="text-2xl font-semibold">{{ summary?.total_calls ?? 0 }}</div>
          <div class="text-xs text-slate-500">over {{ range }}</div>
        </div>
        <div class="border rounded p-3 bg-white">
          <div class="text-xs text-slate-500">Max plan est ($/100)</div>
          <div class="text-2xl font-semibold">{{ fmtUSD(summary?.max_plan_estimate_usd) }}</div>
          <div class="bg-slate-200 h-2 rounded mt-2"><div class="bg-slate-700 h-2 rounded" :style="{ width: maxPlanPct + '%' }"></div></div>
        </div>
        <div class="border rounded p-3 bg-white">
          <div class="text-xs text-slate-500">Telemetry health</div>
          <div class="text-sm">buffer: {{ summary?.telemetry_health?.buffer_depth ?? '—' }}</div>
          <div class="text-sm">last flush: {{ summary?.telemetry_health?.last_flush_age_s ?? '—' }}s ago</div>
          <div class="text-sm">errors 24h: {{ summary?.telemetry_health?.write_errors_24h ?? 0 }}</div>
        </div>
      </div>

      <!-- Chart -->
      <div class="border rounded p-3 bg-white">
        <h2 class="font-semibold mb-2">Cost over time (by provider)</h2>
        <VueApexCharts
          v-if="chartSeries.length"
          type="bar"
          height="280"
          :options="chartOptions"
          :series="chartSeries"
        />
        <p v-else class="text-sm text-slate-500">No data in this window.</p>
      </div>

      <!-- Top spenders -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div class="border rounded p-3 bg-white">
          <h2 class="font-semibold mb-2">Top spenders (by model)</h2>
          <table class="w-full text-sm">
            <thead><tr><th class="text-left">Model</th><th class="text-right">Calls</th><th class="text-right">Tokens</th><th class="text-right">Cost</th></tr></thead>
            <tbody>
              <tr v-for="row in topByModel" :key="row.key" class="border-t">
                <td>{{ row.key }}</td>
                <td class="text-right">{{ row.calls }}</td>
                <td class="text-right">{{ fmtTokens(row.tokens) }}</td>
                <td class="text-right">{{ fmtUSD(row.cost_usd) }}</td>
              </tr>
              <tr v-if="!topByModel.length"><td colspan="4" class="text-slate-500 text-center py-2">—</td></tr>
            </tbody>
          </table>
        </div>
        <div class="border rounded p-3 bg-white">
          <h2 class="font-semibold mb-2">Top spenders (by call site)</h2>
          <table class="w-full text-sm">
            <thead><tr><th class="text-left">Call site</th><th class="text-right">Calls</th><th class="text-right">Tokens</th><th class="text-right">Cost</th></tr></thead>
            <tbody>
              <tr v-for="row in topByCallSite" :key="row.key" class="border-t">
                <td>{{ row.key }}</td>
                <td class="text-right">{{ row.calls }}</td>
                <td class="text-right">{{ fmtTokens(row.tokens) }}</td>
                <td class="text-right">{{ fmtUSD(row.cost_usd) }}</td>
              </tr>
              <tr v-if="!topByCallSite.length"><td colspan="4" class="text-slate-500 text-center py-2">—</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Recent calls -->
      <div class="border rounded p-3 bg-white">
        <h2 class="font-semibold mb-2">Recent calls (latest 50)</h2>
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left">
              <th>Time</th><th>Provider</th><th>Model</th>
              <th class="text-right">In</th><th class="text-right">Out</th><th class="text-right">Cost</th>
              <th>Context</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, idx) in recentCalls" :key="row.id || `${row.ts}-${idx}`" class="border-t cursor-pointer hover:bg-slate-50" @click="selectedCall = row">
              <td>{{ fmtTime(row.ts) }}</td>
              <td>{{ row.provider }}</td>
              <td>{{ row.model }}</td>
              <td class="text-right">{{ fmtTokens(row.input_tokens) }}</td>
              <td class="text-right">{{ fmtTokens(row.output_tokens) }}</td>
              <td class="text-right">{{ fmtUSD(row.total_cost_usd) }}</td>
              <td class="text-xs text-slate-500">{{ row.strategy || '—' }} / {{ row.call_site || '—' }}</td>
            </tr>
            <tr v-if="!recentCalls.length"><td colspan="7" class="text-slate-500 text-center py-2">No calls yet.</td></tr>
          </tbody>
        </table>
      </div>

      <!-- Call detail modal -->
      <div v-if="selectedCall" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="selectedCall = null">
        <div class="bg-white rounded p-4 max-w-2xl w-full max-h-[80vh] overflow-y-auto">
          <div class="flex justify-between items-start mb-3">
            <h3 class="font-semibold">Call detail</h3>
            <button @click="selectedCall = null" class="text-slate-500 hover:text-slate-800">✕</button>
          </div>
          <pre class="text-xs bg-slate-100 p-3 rounded overflow-x-auto">{{ JSON.stringify(selectedCall, null, 2) }}</pre>
        </div>
      </div>
    </div>
  </AppShell>
</template>
