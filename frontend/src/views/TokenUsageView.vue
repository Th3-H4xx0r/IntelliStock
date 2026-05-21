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
  if (!ms) return 'Never'
  const deltaMs = Date.now() - ms
  const deltaS = Math.max(0, Math.floor(deltaMs / 1000))
  if (deltaS < 10) return 'Just now'
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
  if (!health) {
    return {
      label: 'Awaiting data',
      classes: 'border-border-subtle bg-surface/60 text-slate-400',
    }
  }
  if (Number(health.write_errors_24h || 0) > 0) {
    return {
      label: 'Degraded',
      classes: 'border-rose-500/30 bg-rose-500/10 text-rose-200',
    }
  }
  if (Number(health.last_flush_age_s || 0) > 30) {
    return {
      label: 'Lagging',
      classes: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
    }
  }
  return {
    label: 'Healthy',
    classes: 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200',
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

const chartOptions = computed(() => ({
  chart: {
    type: 'bar',
    stacked: true,
    toolbar: { show: false },
    background: 'transparent',
    foreColor: '#94a3b8',
  },
  theme: { mode: 'dark' },
  dataLabels: { enabled: false },
  stroke: { width: 0 },
  plotOptions: {
    bar: {
      horizontal: false,
      borderRadius: 4,
      columnWidth: '58%',
    },
  },
  grid: {
    borderColor: 'rgba(148, 163, 184, 0.12)',
    strokeDashArray: 4,
  },
  xaxis: {
    type: 'datetime',
    labels: {
      style: { colors: '#64748b' },
    },
    axisBorder: { color: 'rgba(148, 163, 184, 0.18)' },
    axisTicks: { color: 'rgba(148, 163, 184, 0.18)' },
  },
  yaxis: {
    labels: {
      style: { colors: '#64748b' },
      formatter: (value) => (value < 1 ? value.toFixed(2) : value.toFixed(0)),
    },
    title: {
      text: 'Cost (USD)',
      style: { color: '#64748b' },
    },
  },
  legend: {
    position: 'top',
    horizontalAlign: 'left',
    labels: { colors: '#cbd5e1' },
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
    <main class="flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-10 max-w-7xl space-y-6">
      <section class="flex items-start justify-between gap-4 flex-wrap">
        <div class="flex items-start gap-4">
          <div class="size-12 rounded-2xl bg-surface border border-border-subtle flex items-center justify-center shrink-0">
            <span class="material-symbols-outlined text-primary text-2xl">payments</span>
          </div>
          <div class="space-y-2">
            <div class="flex items-center gap-2 flex-wrap">
              <h1 class="text-2xl font-bold text-slate-100">Token Usage</h1>
              <span
                class="inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em]"
                :class="telemetryState.classes"
              >
                <span class="size-1.5 rounded-full bg-current"></span>
                {{ telemetryState.label }}
              </span>
            </div>
            <p class="max-w-3xl text-sm text-slate-400">
              Live telemetry across providers, models, and strategy call sites, with recent calls
              merged from the in-process buffer and persisted backtest usage rows.
            </p>
            <div class="flex flex-wrap gap-2 text-xs text-slate-500">
              <span class="rounded-full border border-border-subtle bg-surface/50 px-3 py-1">
                {{ rangeLabel }}
              </span>
              <span class="rounded-full border border-border-subtle bg-surface/50 px-3 py-1">
                Auto refresh {{ AUTO_REFRESH_MS / 1000 }}s
              </span>
              <span class="rounded-full border border-border-subtle bg-surface/50 px-3 py-1">
                Last sync {{ fmtRelative(lastLoadedAt) }}
              </span>
              <span v-if="recentCalls.length" class="rounded-full border border-border-subtle bg-surface/50 px-3 py-1">
                Latest call {{ fmtTime(recentCalls[0].ts) }}
              </span>
            </div>
          </div>
        </div>

        <div class="flex items-center gap-2">
          <button
            v-for="option in ['24h', '7d', '30d']"
            :key="option"
            @click="range = option; fetchAll()"
            class="rounded-xl border px-3.5 py-2 text-sm font-medium transition-colors"
            :class="range === option
              ? 'border-primary/40 bg-primary/15 text-primary'
              : 'border-border-subtle bg-surface/60 text-slate-300 hover:text-slate-100 hover:bg-surface'"
          >
            {{ option }}
          </button>
          <button
            @click="fetchAll()"
            class="inline-flex items-center justify-center rounded-xl border border-border-subtle bg-surface/60 px-3.5 py-2 text-slate-300 transition-colors hover:bg-surface hover:text-slate-100"
            title="Refresh telemetry"
          >
            <span class="material-symbols-outlined text-[18px]" :class="loading ? 'animate-spin' : ''">refresh</span>
          </button>
        </div>
      </section>

      <div
        v-if="loadError"
        class="rounded-2xl border border-rose-500/25 bg-rose-500/10 px-4 py-3 text-sm text-rose-200"
      >
        {{ loadError }}
      </div>

      <section class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <article class="glass-card card-hover rounded-2xl border border-border-subtle p-5">
          <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Period cost</p>
          <div class="mt-3 flex items-end justify-between gap-3">
            <p class="text-3xl font-semibold text-slate-100">{{ fmtUSD(summary?.total_cost_usd) }}</p>
            <p class="text-xs text-slate-500">{{ rangeLabel }}</p>
          </div>
          <p class="mt-2 text-sm text-slate-400">
            {{ fmtTokens(totalTokens) }} tokens across {{ totalCalls }} calls
          </p>
          <div class="mt-4 flex flex-wrap gap-2">
            <span
              v-for="provider in topProviders"
              :key="provider.provider || 'unknown'"
              class="rounded-full border border-border-subtle bg-surface/70 px-2.5 py-1 text-[11px] text-slate-300"
            >
              {{ provider.provider || 'unknown' }} · {{ fmtUSD(provider.cost_usd) }}
            </span>
            <span
              v-if="!topProviders.length"
              class="rounded-full border border-border-subtle bg-surface/70 px-2.5 py-1 text-[11px] text-slate-500"
            >
              No provider spend yet
            </span>
          </div>
        </article>

        <article class="glass-card card-hover rounded-2xl border border-border-subtle p-5">
          <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Period calls</p>
          <p class="mt-3 text-3xl font-semibold text-slate-100">{{ totalCalls }}</p>
          <div class="mt-4 grid grid-cols-2 gap-3 text-sm">
            <div>
              <p class="text-[11px] uppercase tracking-widest text-slate-600">Avg cost</p>
              <p class="mt-1 text-slate-300">{{ fmtUSD(averageCostPerCall) }}</p>
            </div>
            <div>
              <p class="text-[11px] uppercase tracking-widest text-slate-600">Recent rows</p>
              <p class="mt-1 text-slate-300">{{ recentCalls.length }}</p>
            </div>
          </div>
        </article>

        <article class="glass-card card-hover rounded-2xl border border-border-subtle p-5">
          <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Max plan estimate</p>
          <p class="mt-3 text-3xl font-semibold text-slate-100">{{ fmtUSD(summary?.max_plan_estimate_usd) }}</p>
          <div class="mt-4 h-2 overflow-hidden rounded-full bg-surface">
            <div
              class="h-full rounded-full bg-gradient-to-r from-primary/70 to-primary transition-[width] duration-300"
              :style="{ width: `${maxPlanPct}%` }"
            ></div>
          </div>
          <p class="mt-2 text-sm text-slate-400">
            {{ maxPlanPct }}% of the ${{ MAX_PLAN_BUDGET_USD.toFixed(0) }} Claude Max reference budget
          </p>
        </article>

        <article class="glass-card card-hover rounded-2xl border border-border-subtle p-5">
          <div class="flex items-center justify-between gap-3">
            <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Telemetry health</p>
            <span
              class="rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.16em]"
              :class="telemetryState.classes"
            >
              {{ telemetryState.label }}
            </span>
          </div>
          <div class="mt-4 grid grid-cols-3 gap-3 text-sm">
            <div>
              <p class="text-[11px] uppercase tracking-widest text-slate-600">Buffer</p>
              <p class="mt-1 text-slate-300">{{ summary?.telemetry_health?.buffer_depth ?? '—' }}</p>
            </div>
            <div>
              <p class="text-[11px] uppercase tracking-widest text-slate-600">Last flush</p>
              <p class="mt-1 text-slate-300">{{ summary?.telemetry_health?.last_flush_age_s ?? '—' }}s</p>
            </div>
            <div>
              <p class="text-[11px] uppercase tracking-widest text-slate-600">Errors 24h</p>
              <p class="mt-1 text-slate-300">{{ summary?.telemetry_health?.write_errors_24h ?? 0 }}</p>
            </div>
          </div>
        </article>
      </section>

      <section class="glass-card rounded-2xl overflow-hidden border border-border-subtle">
        <div class="flex items-center justify-between gap-4 border-b border-border-subtle px-5 py-4 flex-wrap">
          <div>
            <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Spend trend</p>
            <h2 class="mt-1 text-lg font-semibold text-slate-100">Cost over time</h2>
          </div>
          <p class="text-xs text-slate-500">Stacked by provider</p>
        </div>
        <div class="p-5">
          <VueApexCharts
            v-if="chartHasData"
            type="bar"
            height="320"
            :options="chartOptions"
            :series="chartSeries"
          />
          <div v-else class="flex min-h-[220px] items-center justify-center rounded-2xl border border-dashed border-border-subtle bg-surface/30 text-center">
            <div>
              <p class="text-sm font-medium text-slate-300">No usage in this window.</p>
              <p class="mt-1 text-xs text-slate-500">Backtest and chatbot calls will appear here after telemetry flushes.</p>
            </div>
          </div>
        </div>
      </section>

      <section class="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <article class="glass-card rounded-2xl overflow-hidden border border-border-subtle">
          <div class="flex items-center justify-between gap-4 border-b border-border-subtle px-5 py-4">
            <div>
              <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Ranking</p>
              <h2 class="mt-1 text-lg font-semibold text-slate-100">Top spenders by model</h2>
            </div>
            <span class="text-xs text-slate-500">Top 10</span>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-border-subtle/70 text-left">
                  <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500">Model</th>
                  <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500 text-right">Calls</th>
                  <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500 text-right">Tokens</th>
                  <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500 text-right">Cost</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in topByModel"
                  :key="row.key"
                  class="border-b border-border-subtle/40 text-slate-200 transition-colors hover:bg-surface/30"
                >
                  <td class="px-5 py-3 font-mono text-xs text-slate-300">{{ row.key }}</td>
                  <td class="px-5 py-3 text-right">{{ row.calls }}</td>
                  <td class="px-5 py-3 text-right">{{ fmtTokens(row.tokens) }}</td>
                  <td class="px-5 py-3 text-right">{{ fmtUSD(row.cost_usd) }}</td>
                </tr>
                <tr v-if="!topByModel.length">
                  <td colspan="4" class="px-5 py-10 text-center text-sm text-slate-500">No model spend recorded yet.</td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>

        <article class="glass-card rounded-2xl overflow-hidden border border-border-subtle">
          <div class="flex items-center justify-between gap-4 border-b border-border-subtle px-5 py-4">
            <div>
              <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Ranking</p>
              <h2 class="mt-1 text-lg font-semibold text-slate-100">Top spenders by call site</h2>
            </div>
            <span class="text-xs text-slate-500">Top 10</span>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-border-subtle/70 text-left">
                  <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500">Call site</th>
                  <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500 text-right">Calls</th>
                  <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500 text-right">Tokens</th>
                  <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500 text-right">Cost</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in topByCallSite"
                  :key="row.key"
                  class="border-b border-border-subtle/40 text-slate-200 transition-colors hover:bg-surface/30"
                >
                  <td class="px-5 py-3 font-mono text-xs text-slate-300">{{ row.key }}</td>
                  <td class="px-5 py-3 text-right">{{ row.calls }}</td>
                  <td class="px-5 py-3 text-right">{{ fmtTokens(row.tokens) }}</td>
                  <td class="px-5 py-3 text-right">{{ fmtUSD(row.cost_usd) }}</td>
                </tr>
                <tr v-if="!topByCallSite.length">
                  <td colspan="4" class="px-5 py-10 text-center text-sm text-slate-500">No call-site spend recorded yet.</td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>
      </section>

      <section class="glass-card rounded-2xl overflow-hidden border border-border-subtle">
        <div class="flex items-center justify-between gap-4 border-b border-border-subtle px-5 py-4 flex-wrap">
          <div>
            <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Per-run spend</p>
            <h2 class="mt-1 text-lg font-semibold text-slate-100">LLM cost by run</h2>
          </div>
          <span class="text-xs text-slate-500">Sorted by cost · click a Backtest row to open it; Live rows are summary-only</span>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="border-b border-border-subtle/70 text-left">
                <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500">Run</th>
                <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500">Kind</th>
                <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500">Instance</th>
                <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500">Started</th>
                <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500 text-right">Calls</th>
                <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500 text-right">Tokens</th>
                <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500 text-right">Cost</th>
                <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500 text-right">Success</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in byBacktest"
                :key="`${row.kind || 'backtest'}|${row.key || row.backtest_id}`"
                class="border-b border-border-subtle/40 text-slate-200 transition-colors hover:bg-surface/30"
                :class="row.kind === 'backtest' ? 'cursor-pointer' : 'cursor-default'"
                @click="row.kind === 'backtest' && row.backtest_id ? $router.push({ name: 'backtest-detail', params: { id: row.backtest_id } }) : null"
              >
                <td class="px-5 py-3 font-mono text-xs text-slate-300">{{ row.display_label || ('#' + (row.backtest_id || '?')) }}</td>
                <td class="px-5 py-3">
                  <span
                    class="inline-flex rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
                    :class="row.kind === 'live' ? 'bg-emerald-900/40 text-emerald-200' : 'bg-indigo-900/40 text-indigo-200'"
                  >{{ row.kind || 'backtest' }}</span>
                </td>
                <td class="px-5 py-3 text-xs text-slate-400">{{ row.instance_id || '—' }}</td>
                <td class="px-5 py-3 whitespace-nowrap text-slate-400">{{ fmtTime(row.first_ts) }}</td>
                <td class="px-5 py-3 text-right">{{ row.calls }}</td>
                <td class="px-5 py-3 text-right">{{ fmtTokens(row.tokens) }}</td>
                <td class="px-5 py-3 text-right text-slate-100 font-medium">{{ fmtUSD(row.cost_usd) }}</td>
                <td class="px-5 py-3 text-right text-xs">
                  <span :class="row.failed_calls > 0 ? 'text-amber-300' : 'text-emerald-300'">
                    {{ row.ok_calls }}/{{ row.calls }}
                  </span>
                </td>
              </tr>
              <tr v-if="!byBacktest.length">
                <td colspan="8" class="px-5 py-10 text-center text-sm text-slate-500">
                  No LLM cost data in this range. Backtest or live runs that made LLM calls will appear here.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="glass-card rounded-2xl overflow-hidden border border-border-subtle">
        <div class="flex items-center justify-between gap-4 border-b border-border-subtle px-5 py-4 flex-wrap">
          <div>
            <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Activity</p>
            <h2 class="mt-1 text-lg font-semibold text-slate-100">Recent calls</h2>
          </div>
          <span class="text-xs text-slate-500">Latest 50 rows</span>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="border-b border-border-subtle/70 text-left">
                <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500">Time</th>
                <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500">Provider</th>
                <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500">Model</th>
                <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500 text-right">In</th>
                <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500 text-right">Out</th>
                <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500 text-right">Cost</th>
                <th class="px-5 py-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500">Context</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, idx) in recentCalls"
                :key="row.id || `${row.ts}-${idx}`"
                class="border-b border-border-subtle/40 text-slate-200 transition-colors hover:bg-surface/30 cursor-pointer"
                @click="selectedCall = row"
              >
                <td class="px-5 py-3 whitespace-nowrap">{{ fmtTime(row.ts) }}</td>
                <td class="px-5 py-3">{{ row.provider || '—' }}</td>
                <td class="px-5 py-3 font-mono text-xs text-slate-300">{{ row.model || '—' }}</td>
                <td class="px-5 py-3 text-right">{{ fmtTokens(row.input_tokens) }}</td>
                <td class="px-5 py-3 text-right">{{ fmtTokens(row.output_tokens) }}</td>
                <td class="px-5 py-3 text-right">{{ fmtUSD(row.total_cost_usd) }}</td>
                <td class="px-5 py-3 text-xs text-slate-400">
                  {{ row.strategy || '—' }} / {{ row.call_site || '—' }}
                </td>
              </tr>
              <tr v-if="!recentCalls.length">
                <td colspan="7" class="px-5 py-12 text-center text-sm text-slate-500">No calls recorded yet.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <div
        v-if="selectedCall"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
        @click.self="selectedCall = null"
      >
        <div class="w-full max-w-3xl overflow-hidden rounded-2xl border border-border-subtle bg-[#0f1318] shadow-2xl">
          <div class="flex items-center justify-between gap-3 border-b border-border-subtle px-6 py-4">
            <div>
              <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Recent call</p>
              <h3 class="mt-1 text-lg font-semibold text-slate-100">{{ selectedCall.model || selectedCall.provider || 'Call detail' }}</h3>
            </div>
            <button
              @click="selectedCall = null"
              class="inline-flex items-center justify-center rounded-lg border border-border-subtle px-2 py-1 text-slate-400 transition-colors hover:bg-surface hover:text-slate-100"
            >
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>
          <div class="px-6 py-5">
            <pre class="overflow-x-auto rounded-2xl border border-border-subtle bg-[#04040c] p-4 text-xs leading-6 text-slate-300">{{ selectedCallJson }}</pre>
          </div>
        </div>
      </div>
    </main>
  </AppShell>
</template>
