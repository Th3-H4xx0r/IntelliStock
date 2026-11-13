<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import VueApexCharts from 'vue3-apexcharts'
import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')
const AUTO_REFRESH_MS = 10_000
const MAX_PLAN_BUDGET_USD = 100.0

const range = ref('24h')
const loading = ref(false)
const loadError = ref('')
const summary = ref(null)
const timeseries = ref([])
const topByModel = ref([])
const topByCallSite = ref([])
const byBacktest = ref([])
const recentCalls = ref([])
const selectedCall = ref(null)
const lastLoadedAt = ref(0)

let refreshTimer = null
let fetchGeneration = 0

function authHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function fetchJson(url, headers) {
  const res = await fetch(url, { headers })
  const raw = await res.text()
  let body = null
  if (raw) {
    try {
      body = JSON.parse(raw)
    } catch {
      body = raw
    }
  }
  if (!res.ok) {
    const detail = typeof body === 'object' && body !== null
      ? body.detail || body.message
      : body
    throw new Error(detail ? String(detail) : `HTTP ${res.status}`)
  }
  return body
}

function fmtUSD(value) {
  if (value == null) return '—'
  const amount = Number(value)
  if (Number.isNaN(amount)) return '—'
  if (amount === 0) return '$0.00'
  if (Math.abs(amount) < 1) return `$${amount.toFixed(4)}`
  return `$${amount.toFixed(2)}`
}

function fmtTokens(value) {
  if (value == null) return '—'
  const count = Number(value)
  if (Number.isNaN(count)) return '—'
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`
  if (count >= 1000) return `${(count / 1000).toFixed(1)}k`
  return String(count)
}

function fmtTime(ms) {
  if (!ms) return '—'
  return new Date(ms).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function fmtRelative(ms) {
  if (!ms) return 'never'
  const deltaMs = Date.now() - ms
  const deltaS = Math.max(0, Math.floor(deltaMs / 1000))
  if (deltaS < 10) return 'just now'
  if (deltaS < 60) return `${deltaS}s ago`
  const deltaM = Math.floor(deltaS / 60)
  if (deltaM < 60) return `${deltaM}m ago`
  const deltaH = Math.floor(deltaM / 60)
  if (deltaH < 24) return `${deltaH}h ago`
  return `${Math.floor(deltaH / 24)}d ago`
}

async function fetchAll(silent = false) {
  const generation = ++fetchGeneration
  if (!silent) loading.value = true
  loadError.value = ''

  try {
    const headers = authHeaders()
    const bucket = range.value === '24h' ? 'hour' : 'day'
    const requests = [
      fetchJson(`${API_BASE}/llm-usage/summary?range=${range.value}`, headers),
      fetchJson(`${API_BASE}/llm-usage/timeseries?range=${range.value}&bucket=${bucket}`, headers),
      fetchJson(`${API_BASE}/llm-usage/top-spenders?range=${range.value}&group_by=model&limit=10`, headers),
      fetchJson(`${API_BASE}/llm-usage/top-spenders?range=${range.value}&group_by=call_site&limit=10`, headers),
      fetchJson(`${API_BASE}/llm-usage/by-backtest?range=${range.value}&limit=50`, headers),
      fetchJson(`${API_BASE}/llm-usage/calls?limit=50&range=now`, headers),
    ]
    const results = await Promise.allSettled(requests)
    if (generation !== fetchGeneration) return

    const [summaryResult, timeseriesResult, modelResult, callSiteResult, byBacktestResult, callsResult] = results
    if (summaryResult.status === 'fulfilled') summary.value = summaryResult.value
    if (timeseriesResult.status === 'fulfilled') timeseries.value = Array.isArray(timeseriesResult.value) ? timeseriesResult.value : []
    if (modelResult.status === 'fulfilled') topByModel.value = Array.isArray(modelResult.value) ? modelResult.value : []
    if (callSiteResult.status === 'fulfilled') topByCallSite.value = Array.isArray(callSiteResult.value) ? callSiteResult.value : []
    if (byBacktestResult.status === 'fulfilled') byBacktest.value = Array.isArray(byBacktestResult.value) ? byBacktestResult.value : []
    if (callsResult.status === 'fulfilled') recentCalls.value = Array.isArray(callsResult.value) ? callsResult.value : []

    const failures = results.filter((result) => result.status === 'rejected')
    loadError.value = failures.length
      ? `${failures.length} of 6 telemetry requests failed: ${failures[0].reason?.message || 'unknown'}`
      : ''
    lastLoadedAt.value = Date.now()
  } finally {
    if (generation === fetchGeneration && !silent) {
      loading.value = false
    }
  }
}

function handleVisibilityChange() {
  if (!document.hidden) {
    void fetchAll(true)
  }
}

const rangeLabel = computed(() => {
  if (range.value === '24h') return 'Last 24 hours'
  if (range.value === '7d') return 'Last 7 days'
  if (range.value === '30d') return 'Last 30 days'
  return range.value
})

const totalCalls = computed(() => Number(summary.value?.total_calls || 0))
const totalTokens = computed(() => Number(summary.value?.total_tokens || 0))
const averageCostPerCall = computed(() => {
  if (!totalCalls.value) return 0
  return Number(summary.value?.total_cost_usd || 0) / totalCalls.value
})

const topProviders = computed(() => {
  const providers = Array.isArray(summary.value?.by_provider) ? summary.value.by_provider : []
  return [...providers]
    .sort((left, right) => Number(right.cost_usd || 0) - Number(left.cost_usd || 0))
    .slice(0, 3)
})

const telemetryState = computed(() => {
  const health = summary.value?.telemetry_health
  // A dot and a word: green only while telemetry is actually flowing.
  if (!health) {
    return {
      label: 'Awaiting data',
      dot: 'ui-dot-off',
    }
  }
  if (Number(health.write_errors_24h || 0) > 0) {
    return {
      label: 'Degraded',
      dot: 'ui-dot-error',
    }
  }
  if (Number(health.last_flush_age_s || 0) > 30) {
    return {
      label: 'Lagging',
      dot: 'ui-dot-warn',
    }
  }
  return {
    label: 'Healthy',
    dot: 'ui-dot-live',
  }
})

const maxPlanPct = computed(() => {
  const estimate = Number(summary.value?.max_plan_estimate_usd || 0)
  if (!estimate || MAX_PLAN_BUDGET_USD <= 0) return 0
  return Math.min(100, Math.round((estimate / MAX_PLAN_BUDGET_USD) * 100))
})

const chartSeries = computed(() => {
  const grouped = new Map()
  for (const row of timeseries.value) {
    const provider = row.provider || 'unknown'
    if (!grouped.has(provider)) grouped.set(provider, new Map())
    const providerBuckets = grouped.get(provider)
    providerBuckets.set(
      row.bucket_start_ts,
      (providerBuckets.get(row.bucket_start_ts) || 0) + Number(row.cost_usd || 0),
    )
  }

  return [...grouped.entries()].map(([provider, buckets]) => ({
    name: provider,
    data: [...buckets.entries()]
      .sort((left, right) => left[0] - right[0])
      .map(([ts, value]) => ({ x: ts, y: value })),
  }))
})

const chartHasData = computed(() => chartSeries.value.some((series) => series.data.length))

// Providers are categories, not P&L: zinc steps, lightest for the biggest.
const chartOptions = computed(() => ({
  chart: {
    type: 'bar',
    stacked: true,
    toolbar: { show: false },
    background: 'transparent',
    foreColor: '#71717a',
    fontFamily: 'Inter, sans-serif',
    animations: { enabled: false },
  },
  colors: ['#ededed', '#8b8b91', '#5c5c62', '#3f3f46', '#a1a1aa', '#71717a'],
  theme: { mode: 'dark' },
  dataLabels: { enabled: false },
  stroke: { width: 0 },
  plotOptions: {
    bar: {
      horizontal: false,
      borderRadius: 2,
      borderRadiusApplication: 'end',
      borderRadiusWhenStacked: 'last',
      columnWidth: '50%',
    },
  },
  grid: {
    borderColor: 'rgba(255,255,255,0.05)',
    strokeDashArray: 0,
    xaxis: { lines: { show: false } },
  },
  xaxis: {
    type: 'datetime',
    labels: {
      style: { colors: '#71717a', fontSize: '11px', fontFamily: 'inherit' },
    },
    axisBorder: { show: false },
    axisTicks: { show: false },
  },
  yaxis: {
    labels: {
      style: { colors: '#71717a', fontSize: '11px', fontFamily: 'inherit' },
      formatter: (value) => (value < 1 ? value.toFixed(2) : value.toFixed(0)),
    },
    title: {
      text: 'Cost (USD)',
      style: { color: '#71717a', fontSize: '11px', fontWeight: 400 },
    },
  },
  legend: {
    position: 'top',
    horizontalAlign: 'left',
    fontSize: '12px',
    labels: { colors: '#a1a1aa' },
    markers: { size: 4, shape: 'square' },
    itemMargin: { horizontal: 8 },
  },
  tooltip: {
    theme: 'dark',
    y: { formatter: (value) => fmtUSD(value) },
  },
}))

const selectedCallJson = computed(() => (
  selectedCall.value ? JSON.stringify(selectedCall.value, null, 2) : ''
))

onMounted(() => {
  void fetchAll()
  refreshTimer = window.setInterval(() => {
    void fetchAll(true)
  }, AUTO_REFRESH_MS)
  document.addEventListener('visibilitychange', handleVisibilityChange)
})

onUnmounted(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})
</script>

<template>
  <AppShell>
    <main class="ui-page">
      <!-- Header -->
      <header class="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <div class="min-w-0">
          <h1 class="ui-page-title">Token usage</h1>
          <p class="ui-page-sub max-w-3xl">
            Live telemetry across providers, models and strategy call sites. Recent calls merge
            the in-process buffer with persisted backtest usage rows
          </p>
          <p class="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500 tabular-nums">
            <span class="inline-flex items-center gap-1.5 text-slate-300">
              <span class="ui-dot" :class="telemetryState.dot"></span>
              {{ telemetryState.label }}
            </span>
            <span class="text-slate-600" aria-hidden="true">·</span>
            <span>{{ rangeLabel }}</span>
            <span class="text-slate-600" aria-hidden="true">·</span>
            <span>Auto refresh {{ AUTO_REFRESH_MS / 1000 }}s</span>
            <span class="text-slate-600" aria-hidden="true">·</span>
            <span>Last sync {{ fmtRelative(lastLoadedAt) }}</span>
            <template v-if="recentCalls.length">
              <span class="text-slate-600" aria-hidden="true">·</span>
              <span>Latest call {{ fmtTime(recentCalls[0].ts) }}</span>
            </template>
          </p>
        </div>

        <div class="flex items-center gap-2">
          <div class="ui-seg" role="group" aria-label="Range">
            <button
              v-for="option in ['24h', '7d', '30d']"
              :key="option"
              @click="range = option; fetchAll()"
              class="ui-seg-btn tabular-nums"
              :class="range === option ? 'ui-seg-btn-active' : ''"
              :aria-pressed="range === option"
            >
              {{ option }}
            </button>
          </div>
          <button
            @click="fetchAll()"
            class="ui-icon-btn"
            title="Refresh telemetry"
            aria-label="Refresh telemetry"
          >
            <span class="material-symbols-outlined text-[18px]" :class="loading ? 'animate-spin' : ''">refresh</span>
          </button>
        </div>
      </header>

      <div v-if="loadError" class="mt-6 ui-error">
        {{ loadError }}
      </div>

      <!-- Summary: one card, four columns -->
      <section class="mt-8 ui-card grid grid-cols-1 lg:grid-cols-4 divide-y lg:divide-y-0 lg:divide-x divide-white/[0.06]" aria-label="Summary">
        <div class="px-5 py-4">
          <p class="text-xs text-slate-500">Period cost <span class="text-slate-600">· {{ rangeLabel }}</span></p>
          <p class="mt-2 text-2xl font-medium text-slate-100 tabular-nums leading-none">{{ fmtUSD(summary?.total_cost_usd) }}</p>
          <p class="mt-2 text-xs text-slate-500 tabular-nums">
            {{ fmtTokens(totalTokens) }} tokens across {{ totalCalls.toLocaleString() }} calls
          </p>
          <ul class="mt-3 space-y-1 text-xs tabular-nums">
            <li
              v-for="provider in topProviders"
              :key="provider.provider || 'unknown'"
              class="flex items-center justify-between gap-3"
            >
              <span class="text-slate-400 truncate">{{ provider.provider || 'unknown' }}</span>
              <span class="text-slate-300">{{ fmtUSD(provider.cost_usd) }}</span>
            </li>
            <li v-if="!topProviders.length" class="text-slate-500">
              No provider spend yet
            </li>
          </ul>
        </div>

        <div class="px-5 py-4">
          <p class="text-xs text-slate-500">Period calls</p>
          <p class="mt-2 text-2xl font-medium text-slate-100 tabular-nums leading-none">{{ totalCalls.toLocaleString() }}</p>
          <div class="mt-4 grid grid-cols-2 gap-4">
            <div>
              <p class="text-xs text-slate-500">Avg cost</p>
              <p class="mt-1 text-sm text-slate-200 tabular-nums">{{ fmtUSD(averageCostPerCall) }}</p>
            </div>
            <div>
              <p class="text-xs text-slate-500">Recent rows</p>
              <p class="mt-1 text-sm text-slate-200 tabular-nums">{{ recentCalls.length }}</p>
            </div>
          </div>
        </div>

        <div class="px-5 py-4">
          <p class="text-xs text-slate-500">Max plan estimate</p>
          <p class="mt-2 text-2xl font-medium text-slate-100 tabular-nums leading-none">{{ fmtUSD(summary?.max_plan_estimate_usd) }}</p>
          <div class="mt-4 h-1 overflow-hidden rounded-full bg-raised">
            <div
              class="h-full rounded-full bg-slate-300 origin-left transition-transform duration-150 ease-out"
              :style="{ transform: `scaleX(${maxPlanPct / 100})` }"
            ></div>
          </div>
          <p class="mt-2 text-xs text-slate-500 tabular-nums">
            {{ maxPlanPct }}% of the ${{ MAX_PLAN_BUDGET_USD.toFixed(0) }} Claude Max reference budget
          </p>
        </div>

        <div class="px-5 py-4">
          <div class="flex items-center justify-between gap-3">
            <p class="text-xs text-slate-500">Telemetry health</p>
            <span class="inline-flex items-center gap-1.5 text-xs text-slate-300">
              <span class="ui-dot" :class="telemetryState.dot"></span>
              {{ telemetryState.label }}
            </span>
          </div>
          <div class="mt-4 grid grid-cols-3 gap-4">
            <div>
              <p class="text-xs text-slate-500">Buffer</p>
              <p class="mt-1 text-sm text-slate-200 tabular-nums">{{ summary?.telemetry_health?.buffer_depth ?? '—' }}</p>
            </div>
            <div>
              <p class="text-xs text-slate-500">Last flush</p>
              <p class="mt-1 text-sm text-slate-200 tabular-nums">{{ summary?.telemetry_health?.last_flush_age_s ?? '—' }}s</p>
            </div>
            <div>
              <p class="text-xs text-slate-500">Errors 24h</p>
              <p class="mt-1 text-sm text-slate-200 tabular-nums">{{ summary?.telemetry_health?.write_errors_24h ?? 0 }}</p>
            </div>
          </div>
        </div>
      </section>

      <!-- Spend trend -->
      <section class="mt-12">
        <div class="mb-3">
          <h2 class="ui-section-title">Cost over time</h2>
          <p class="ui-section-sub">Stacked by provider</p>
        </div>
        <div class="ui-card p-5">
          <VueApexCharts
            v-if="chartHasData"
            type="bar"
            height="320"
            :options="chartOptions"
            :series="chartSeries"
          />
          <div v-else class="ui-empty gap-1">
            <p class="text-slate-300">No usage in this window</p>
            <p class="text-xs text-slate-500">Backtest and chatbot calls will appear here after telemetry flushes</p>
          </div>
        </div>
      </section>

      <!-- Rankings -->
      <section class="mt-12 grid grid-cols-1 gap-x-8 gap-y-12 xl:grid-cols-2">
        <div class="min-w-0">
          <div class="mb-3 flex items-end justify-between gap-4">
            <h2 class="ui-section-title">Top spenders by model</h2>
            <span class="text-xs text-slate-500">Top 10</span>
          </div>
          <div class="ui-card overflow-hidden">
            <div class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="h-10 text-left text-xs text-slate-500 border-b border-white/[0.06]">
                    <th class="px-5 font-normal">Model</th>
                    <th class="px-5 font-normal text-right">Calls</th>
                    <th class="px-5 font-normal text-right">Tokens</th>
                    <th class="px-5 font-normal text-right">Cost</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/[0.05]">
                  <tr
                    v-for="row in topByModel"
                    :key="row.key"
                    class="h-14 whitespace-nowrap text-slate-300 tabular-nums transition-colors duration-150 ease-out hover:bg-white/[0.03]"
                  >
                    <td class="px-5 text-slate-100">{{ row.key }}</td>
                    <td class="px-5 text-right">{{ row.calls }}</td>
                    <td class="px-5 text-right">{{ fmtTokens(row.tokens) }}</td>
                    <td class="px-5 text-right text-slate-100">{{ fmtUSD(row.cost_usd) }}</td>
                  </tr>
                  <tr v-if="!topByModel.length">
                    <td colspan="4" class="px-5 py-12 text-center text-sm text-slate-500">No model spend recorded yet</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div class="min-w-0">
          <div class="mb-3 flex items-end justify-between gap-4">
            <h2 class="ui-section-title">Top spenders by call site</h2>
            <span class="text-xs text-slate-500">Top 10</span>
          </div>
          <div class="ui-card overflow-hidden">
            <div class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="h-10 text-left text-xs text-slate-500 border-b border-white/[0.06]">
                    <th class="px-5 font-normal">Call site</th>
                    <th class="px-5 font-normal text-right">Calls</th>
                    <th class="px-5 font-normal text-right">Tokens</th>
                    <th class="px-5 font-normal text-right">Cost</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/[0.05]">
                  <tr
                    v-for="row in topByCallSite"
                    :key="row.key"
                    class="h-14 whitespace-nowrap text-slate-300 tabular-nums transition-colors duration-150 ease-out hover:bg-white/[0.03]"
                  >
                    <td class="px-5 text-slate-100">{{ row.key }}</td>
                    <td class="px-5 text-right">{{ row.calls }}</td>
                    <td class="px-5 text-right">{{ fmtTokens(row.tokens) }}</td>
                    <td class="px-5 text-right text-slate-100">{{ fmtUSD(row.cost_usd) }}</td>
                  </tr>
                  <tr v-if="!topByCallSite.length">
                    <td colspan="4" class="px-5 py-12 text-center text-sm text-slate-500">No call-site spend recorded yet</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      <!-- Per-run spend -->
      <section class="mt-12">
        <div class="mb-3">
          <h2 class="ui-section-title">LLM cost by run</h2>
          <p class="ui-section-sub">Sorted by cost. A backtest row opens that backtest; live rows are summary only</p>
        </div>
        <div class="ui-card overflow-hidden">
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="h-10 text-left text-xs text-slate-500 border-b border-white/[0.06]">
                  <th class="px-5 font-normal">Run</th>
                  <th class="px-5 font-normal">Kind</th>
                  <th class="px-5 font-normal">Instance</th>
                  <th class="px-5 font-normal">Started</th>
                  <th class="px-5 font-normal text-right">Calls</th>
                  <th class="px-5 font-normal text-right">Tokens</th>
                  <th class="px-5 font-normal text-right">Cost</th>
                  <th class="px-5 font-normal text-right">Success</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-white/[0.05]">
                <tr
                  v-for="row in byBacktest"
                  :key="`${row.kind || 'backtest'}|${row.key || row.backtest_id}`"
                  class="h-14 whitespace-nowrap text-slate-300 tabular-nums transition-colors duration-150 ease-out hover:bg-white/[0.03]"
                  :class="row.kind === 'backtest' ? 'cursor-pointer' : 'cursor-default'"
                  @click="row.kind === 'backtest' && row.backtest_id ? $router.push({ name: 'backtest-detail', params: { id: row.backtest_id } }) : null"
                >
                  <td class="px-5 text-slate-100 whitespace-nowrap">{{ row.display_label || ('#' + (row.backtest_id || '?')) }}</td>
                  <td class="px-5 text-slate-400 capitalize">{{ row.kind || 'backtest' }}</td>
                  <td class="px-5 text-slate-400">{{ row.instance_id || '—' }}</td>
                  <td class="px-5 whitespace-nowrap text-slate-400">{{ fmtTime(row.first_ts) }}</td>
                  <td class="px-5 text-right">{{ row.calls }}</td>
                  <td class="px-5 text-right">{{ fmtTokens(row.tokens) }}</td>
                  <td class="px-5 text-right text-slate-100">{{ fmtUSD(row.cost_usd) }}</td>
                  <td class="px-5 text-right">
                    <span :class="row.failed_calls > 0 ? 'text-loss' : 'text-slate-400'">
                      {{ row.ok_calls }}/{{ row.calls }}
                    </span>
                  </td>
                </tr>
                <tr v-if="!byBacktest.length">
                  <td colspan="8" class="px-5 py-12 text-center text-sm text-slate-500">
                    No LLM cost data in this range. Backtest or live runs that made LLM calls will appear here
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- Recent calls -->
      <section class="mt-12">
        <div class="mb-3 flex items-end justify-between gap-4">
          <h2 class="ui-section-title">Recent calls</h2>
          <span class="text-xs text-slate-500">Latest 50 rows</span>
        </div>
        <div class="ui-card overflow-hidden">
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="h-10 text-left text-xs text-slate-500 border-b border-white/[0.06]">
                  <th class="px-5 font-normal">Time</th>
                  <th class="px-5 font-normal">Provider</th>
                  <th class="px-5 font-normal">Model</th>
                  <th class="px-5 font-normal text-right">In</th>
                  <th class="px-5 font-normal text-right">Out</th>
                  <th class="px-5 font-normal text-right">Cost</th>
                  <th class="px-5 font-normal">Context</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-white/[0.05]">
                <tr
                  v-for="(row, idx) in recentCalls"
                  :key="row.id || `${row.ts}-${idx}`"
                  class="h-14 whitespace-nowrap text-slate-300 tabular-nums transition-colors duration-150 ease-out hover:bg-white/[0.03] cursor-pointer"
                  @click="selectedCall = row"
                >
                  <td class="px-5 whitespace-nowrap text-slate-400">{{ fmtTime(row.ts) }}</td>
                  <td class="px-5">{{ row.provider || '—' }}</td>
                  <td class="px-5 text-slate-100">{{ row.model || '—' }}</td>
                  <td class="px-5 text-right">{{ fmtTokens(row.input_tokens) }}</td>
                  <td class="px-5 text-right">{{ fmtTokens(row.output_tokens) }}</td>
                  <td class="px-5 text-right text-slate-100">{{ fmtUSD(row.total_cost_usd) }}</td>
                  <td class="px-5 text-xs text-slate-500">
                    {{ row.strategy || '—' }} / {{ row.call_site || '—' }}
                  </td>
                </tr>
                <tr v-if="!recentCalls.length">
                  <td colspan="7" class="px-5 py-12 text-center text-sm text-slate-500">No calls recorded yet</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <div
        v-if="selectedCall"
        class="ui-overlay"
        @click.self="selectedCall = null"
      >
        <div class="ui-modal max-w-3xl">
          <div class="ui-modal-head">
            <div class="min-w-0">
              <h3 class="text-base font-medium text-slate-100 truncate">{{ selectedCall.model || selectedCall.provider || 'Call detail' }}</h3>
              <p class="text-sm text-slate-500 mt-1">Recent call</p>
            </div>
            <button
              @click="selectedCall = null"
              class="ui-icon-btn ml-auto shrink-0"
              aria-label="Close"
            >
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>
          <div class="ui-modal-body">
            <pre class="max-h-[60vh] overflow-auto rounded-lg border border-white/[0.06] bg-background-dark p-4 text-xs leading-6 text-slate-300 tabular-nums">{{ selectedCallJson }}</pre>
          </div>
        </div>
      </div>
    </main>
  </AppShell>
</template>
