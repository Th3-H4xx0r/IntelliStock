<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import VueApexCharts from 'vue3-apexcharts'
import AppShell from '../layouts/AppShell.vue'
import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')
function authHeaders() { return { Authorization: `Bearer ${getToken()}` } }

const route = useRoute()
const router = useRouter()
const id = route.params.id

const status = ref(null)
const result = ref(null)
const tab = ref('trades')  // trades | decisions | logs
const selectedDay = ref(null)
const now = ref(Date.now())
let pollTimer = null
let clockTimer = null

const running = computed(() => status.value && (status.value.status === 'running' || status.value.status === 'pending'))
const elapsed = computed(() => {
  const s = status.value
  if (!s) return ''
  const start = s.started_at || s.created_at
  if (!start) return ''
  const endMs = (!running.value && s.finished_at) ? new Date(s.finished_at).getTime() : now.value
  const secs = Math.max(0, Math.floor((endMs - new Date(start).getTime()) / 1000))
  const m = Math.floor(secs / 60), sc = secs % 60
  return m > 0 ? `${m}m ${sc}s` : `${sc}s`
})

async function load() {
  try {
    const res = await fetch(`${API_BASE}/kalshi/backtests/${id}/results`, { headers: authHeaders() })
    if (!res.ok) return
    const d = await res.json()
    status.value = { status: d.status, summary: d.summary || {}, error: d.error,
                     progress: d.progress, created_at: d.created_at,
                     started_at: d.started_at, finished_at: d.finished_at }
    result.value = d.result || null
    if (!selectedDay.value && dayKeys.value.length) selectedDay.value = dayKeys.value[dayKeys.value.length - 1]
  } catch (e) { /* ignore */ }
}

// --- trades grouped by kickoff day ---
function dayOf(ts) {
  if (!ts) return 'unknown'
  const d = new Date(Number(ts) * 1000)
  return d.toISOString().slice(0, 10)
}
const trades = computed(() => (result.value && result.value.trades) || [])
const tradesByDay = computed(() => {
  const m = {}
  for (const t of trades.value) (m[dayOf(t.kickoff)] ||= []).push(t)
  return m
})
const dayKeys = computed(() => Object.keys(tradesByDay.value).sort())
const selectedTrades = computed(() => {
  if (selectedDay.value === 'all') return trades.value
  return (selectedDay.value && tradesByDay.value[selectedDay.value]) || []
})

// --- equity chart: one point per trade, x = kickoff date ---
const equitySeries = computed(() => {
  const ec = (result.value && result.value.equity_curve) || []
  return [{ name: 'Cumulative P&L ($)', data: trades.value.map((t, i) => ({ x: Number(t.kickoff) * 1000 || (i + 1), y: (ec[i] || 0) / 100, day: dayOf(t.kickoff) })) }]
})
// The curve is P&L, so it takes the gain/loss colour of where it ends.
const equityColor = computed(() => {
  const ec = (result.value && result.value.equity_curve) || []
  return (ec.length ? ec[ec.length - 1] : 0) >= 0 ? '#4cc38a' : '#e5484d'
})
const axisLabel = { colors: '#71717a', fontSize: '11px', fontFamily: 'inherit' }
const equityOpts = computed(() => ({
  chart: { type: 'area', toolbar: { show: false }, animations: { enabled: false },
    background: 'transparent', fontFamily: 'Inter, sans-serif', zoom: { enabled: false },
    events: {
      dataPointSelection: (e, ctx, cfg) => pickIdx(cfg.dataPointIndex),
      markerClick: (e, ctx, { dataPointIndex }) => pickIdx(dataPointIndex),
    } },
  dataLabels: { enabled: false },
  stroke: { curve: 'smooth', width: 1.5 },
  fill: { type: 'gradient', gradient: { colorStops: [{ offset: 0, color: equityColor.value, opacity: 0.1 }, { offset: 100, color: equityColor.value, opacity: 0 }] } },
  markers: { size: 0, hover: { size: 4 } },
  xaxis: { type: 'datetime', axisBorder: { show: false }, axisTicks: { show: false },
    labels: { style: axisLabel } },
  yaxis: { tickAmount: 4, labels: { style: axisLabel, formatter: (v) => `$${Math.round(v)}` } },
  colors: [equityColor.value], grid: { borderColor: 'rgba(255,255,255,0.05)', strokeDashArray: 0, xaxis: { lines: { show: false } } },
  tooltip: { theme: 'dark', x: { format: 'MMM dd' } },
}))
function pickIdx(i) {
  const t = trades.value[i]
  if (t) { selectedDay.value = dayOf(t.kickoff); tab.value = 'trades' }
}

function pickLabel(t) {
  if (t.side === 'draw') return 'Draw'
  if (t.side === 'home') return `${t.home || 'Home'} to win`
  if (t.side === 'away') return `${t.away || 'Away'} to win`
  return t.side
}
function fmtPct(v) { return v == null ? '—' : `${(v * 100).toFixed(1)}%` }
function fmtMoney(c) { return c == null ? '—' : `${c < 0 ? '-' : ''}$${Math.abs(c / 100).toFixed(2)}` }
const s = computed(() => (status.value && status.value.summary) || {})
const confColor = computed(() => {
  const p = s.value.profit_confidence ?? 0
  return p >= 0.9 ? 'text-gain' : p >= 0.75 ? 'text-amber-400' : 'text-loss'
})
// Status as a dot and a word: green only while the run is actually running.
function statusColor(st) { return st === 'finished' ? 'text-slate-300' : st === 'error' ? 'text-loss' : st === 'stopped' ? 'text-slate-500' : 'text-slate-200' }
function statusDot(st) { return st === 'running' ? 'ui-dot-live' : st === 'pending' ? 'ui-dot-warn' : st === 'error' ? 'ui-dot-error' : 'ui-dot-off' }

onMounted(async () => {
  await load()
  pollTimer = setInterval(() => { if (running.value) load() }, 2000)
  clockTimer = setInterval(() => { now.value = Date.now() }, 1000)
})
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer); if (clockTimer) clearInterval(clockTimer) })
</script>

<template>
  <AppShell>
    <main class="ui-page">
      <button @click="router.back()" class="inline-flex items-center gap-1 h-8 -ml-2 px-2 rounded-md text-sm text-slate-400 hover:text-slate-100 hover:bg-raised transition-colors duration-150 ease-out">
        <span class="material-symbols-outlined text-[16px]">arrow_back</span> Back
      </button>
      <header class="mt-4">
        <h1 class="ui-page-title">Backtest <span class="text-slate-500 font-mono text-xl">{{ id.slice(0, 8) }}</span></h1>
        <p class="ui-page-sub flex flex-wrap items-center gap-x-2 gap-y-1">
          <span v-if="status" class="inline-flex items-center gap-1.5 capitalize" :class="statusColor(status.status)"><span class="ui-dot" :class="statusDot(status.status)"></span>{{ status.status }}</span>
          <template v-if="status && elapsed"><span class="text-slate-600" aria-hidden="true">·</span><span class="tabular-nums">{{ elapsed }}</span></template>
        </p>
        <div v-if="running" class="flex items-center gap-3 mt-3 max-w-md">
          <div class="flex-1 h-1 rounded-full bg-raised overflow-hidden">
            <div class="h-full bg-slate-300 rounded-full" :style="{ width: Math.max(3, Math.round((status && status.progress) || 0)) + '%' }"></div>
          </div>
          <span class="text-xs text-slate-400 tabular-nums">{{ Math.round((status && status.progress) || 0) }}%</span>
        </div>
      </header>

      <div v-if="status && status.error" class="mt-6 ui-error">
        <span class="font-medium">Error:</span> {{ status.error }}
      </div>

      <!-- Summary -->
      <section class="mt-8 ui-card divide-y divide-white/[0.06]" aria-label="Summary">
        <div class="px-5 py-5 grid grid-cols-2 md:grid-cols-[minmax(0,1.4fr)_repeat(5,minmax(0,1fr))] gap-x-6 gap-y-5 items-end">
          <div class="col-span-2 md:col-span-1">
            <p class="text-xs text-slate-500">Total P&amp;L</p>
            <p class="mt-1 text-2xl font-medium tabular-nums leading-tight" :class="(s.pnl_cents >= 0) ? 'text-gain' : 'text-loss'">{{ fmtMoney(s.pnl_cents) }}</p>
          </div>
          <div><p class="text-xs text-slate-500">ROI</p><p class="mt-1 text-base font-medium text-slate-100 tabular-nums">{{ fmtPct(s.roi) }}</p></div>
          <div><p class="text-xs text-slate-500">Bets</p><p class="mt-1 text-base font-medium text-slate-100 tabular-nums">{{ s.n_bets ?? '—' }}</p></div>
          <div><p class="text-xs text-slate-500">Win rate</p><p class="mt-1 text-base font-medium text-slate-100 tabular-nums">{{ fmtPct(s.win_rate) }}</p></div>
          <div><p class="text-xs text-slate-500">Avg CLV</p><p class="mt-1 text-base font-medium text-slate-100 tabular-nums">{{ fmtPct(s.clv_avg) }}</p></div>
          <div><p class="text-xs text-slate-500">API / cache</p><p class="mt-1 text-base font-medium text-slate-100 tabular-nums">{{ s.api_calls ?? 0 }} / {{ s.cache_hits ?? 0 }}</p></div>
        </div>
        <div class="px-5 py-3 text-xs text-slate-500 flex flex-wrap gap-x-5 gap-y-1 tabular-nums">
          <span>Fixtures <span class="text-slate-300">{{ s.n_fixtures ?? 0 }}</span></span>
          <span>Bet <span class="text-slate-300">{{ s.bet ?? 0 }}</span></span>
          <span>No edge <span class="text-slate-300">{{ s.no_bet ?? 0 }}</span></span>
          <span>Unsettled <span class="text-slate-300">{{ s.unsettled ?? 0 }}</span></span>
          <span>Unmatched <span class="text-slate-300">{{ s.unmatched ?? 0 }}</span></span>
          <span>No price <span class="text-slate-300">{{ s.no_candle_data ?? 0 }}</span></span>
        </div>
        <div v-if="s.profit_confidence != null" class="px-5 py-3 text-xs flex flex-wrap items-center gap-x-5 gap-y-1 tabular-nums">
          <span class="text-slate-500">Trust</span>
          <span :class="confColor">Profit confidence {{ (s.profit_confidence * 100).toFixed(0) }}%</span>
          <span class="text-slate-500">90% P&amp;L range <span class="text-slate-300">{{ fmtMoney(s.pnl_ci_low_cents) }} → {{ fmtMoney(s.pnl_ci_high_cents) }}</span></span>
          <span v-if="s.pnl_ci_low_cents < 0" class="inline-flex items-center gap-2 text-slate-400"><span class="ui-dot ui-dot-warn"></span>The range spans a loss, so this edge is not statistically proven</span>
        </div>
      </section>

      <!-- Scrubbable equity chart -->
      <section v-if="trades.length" class="mt-12" aria-labelledby="kbr-equity">
        <div class="flex items-end justify-between gap-4 mb-3">
          <div>
            <h2 id="kbr-equity" class="ui-section-title">Equity curve</h2>
            <p class="ui-section-sub">Cumulative P&amp;L by kickoff. Pick a point or a day to see its trades</p>
          </div>
          <span v-if="selectedDay" class="text-xs text-slate-400 tabular-nums shrink-0">{{ selectedDay === 'all' ? 'All' : selectedDay }} · {{ selectedTrades.length }} trade(s)</span>
        </div>
        <div class="ui-card px-5 pt-4 pb-5">
          <div class="-mx-2">
            <VueApexCharts type="area" height="280" :options="equityOpts" :series="equitySeries" />
          </div>
          <!-- day filter -->
          <div class="flex flex-wrap gap-1.5 mt-4 pt-4 border-t border-white/[0.06]">
            <button @click="selectedDay = 'all'; tab = 'trades'"
                    class="h-7 px-2.5 rounded-md text-xs tabular-nums transition-colors duration-150 ease-out"
                    :class="selectedDay === 'all' ? 'bg-raised text-slate-100 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]' : 'text-slate-500 hover:text-slate-200'">
              All · {{ trades.length }}
            </button>
            <button v-for="d in dayKeys" :key="d" @click="selectedDay = d; tab = 'trades'"
                    class="h-7 px-2.5 rounded-md text-xs tabular-nums transition-colors duration-150 ease-out"
                    :class="selectedDay === d ? 'bg-raised text-slate-100 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]' : 'text-slate-500 hover:text-slate-200'">
              {{ d }} · {{ tradesByDay[d].length }}
            </button>
          </div>
        </div>
      </section>

      <!-- Tabs -->
      <section class="mt-12" aria-label="Details">
        <div class="ui-seg mb-3" role="tablist">
          <button v-for="t in ['trades', 'decisions', 'logs']" :key="t" @click="tab = t"
                  class="ui-seg-btn" :class="tab === t ? 'ui-seg-btn-active' : ''" role="tab" :aria-selected="tab === t">
            {{ t === 'decisions' ? 'Decision log' : (t === 'trades' ? 'Trades' : 'Logs') }}
          </button>
        </div>

        <!-- Trades (rows for the selected day) -->
        <div v-if="tab === 'trades'" class="ui-card overflow-hidden">
          <p v-if="!selectedTrades.length" class="ui-empty">
            {{ trades.length ? 'Pick a day above to see its trades' : 'No bets were placed under these settings over this range' }}
          </p>
          <ul v-else class="ui-rows">
            <li v-for="(t, i) in selectedTrades" :key="i" class="min-h-14 px-5 py-3 flex items-start justify-between gap-4">
              <div class="min-w-0">
                <div class="flex items-center gap-1.5 text-sm text-slate-100 min-w-0">
                  <img v-if="t.home_flag" :src="t.home_flag" class="w-4 h-3 rounded-sm object-cover shrink-0" alt="" />
                  <span class="truncate">{{ t.home || t.side }}</span>
                  <span class="text-slate-500 text-xs shrink-0">v</span>
                  <img v-if="t.away_flag" :src="t.away_flag" class="w-4 h-3 rounded-sm object-cover shrink-0" alt="" />
                  <span class="truncate">{{ t.away }}</span>
                </div>
                <p class="text-xs text-slate-500 mt-0.5 tabular-nums">
                  <span v-if="t.league">{{ t.league }} · </span><span class="text-slate-300">{{ pickLabel(t) }}</span> · entry {{ t.entry_cents }}¢ × {{ t.size }}
                </p>
                <p class="text-xs text-slate-500 mt-0.5 tabular-nums">
                  Edge {{ (t.edge * 100).toFixed(1) }}% · {{ t.sharp_prob != null ? 'Sharp' : 'Model only' }} · <span class="capitalize">{{ t.outcome }}</span><span v-if="t.clv != null"> · CLV {{ (t.clv * 100).toFixed(1) }}%</span>
                </p>
              </div>
              <div class="text-sm tabular-nums shrink-0" :class="(t.realized_pnl_cents >= 0) ? 'text-gain' : 'text-loss'">{{ fmtMoney(t.realized_pnl_cents) }}</div>
            </li>
          </ul>
        </div>

        <!-- Decision log -->
        <div v-else-if="tab === 'decisions'" class="ui-card overflow-hidden">
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="text-left text-xs text-slate-500 border-b border-white/[0.06]">
                  <th class="h-10 px-5 font-normal">Fixture</th><th class="h-10 px-5 font-normal">Decision</th><th class="h-10 px-5 font-normal">Reason</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-white/[0.05]">
                <tr v-for="(d, i) in (result && result.decision_log) || []" :key="i">
                  <td class="h-12 px-5 text-slate-200">{{ d.label }}</td>
                  <td class="h-12 px-5"><span :class="d.decision === 'placed' ? 'text-slate-200' : d.decision === 'no_bet' ? 'text-slate-500' : 'text-slate-400'">{{ d.decision }}</span></td>
                  <td class="h-12 px-5 text-slate-500">{{ d.reason || (d.bets ? d.bets.length + ' side(s)' : '') }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="!(result && result.decision_log && result.decision_log.length)" class="ui-empty">No decision log recorded</p>
        </div>

        <!-- Logs -->
        <div v-else class="ui-card p-2">
          <div class="rounded-lg bg-background-dark px-4 py-3 font-mono text-xs leading-relaxed text-slate-300 max-h-96 overflow-y-auto whitespace-pre-wrap">
            <div v-for="(line, i) in (result && result.logs) || []" :key="i">{{ line }}</div>
            <p v-if="!(result && result.logs && result.logs.length)" class="text-slate-500">No logs recorded</p>
          </div>
        </div>
      </section>
    </main>
  </AppShell>
</template>
