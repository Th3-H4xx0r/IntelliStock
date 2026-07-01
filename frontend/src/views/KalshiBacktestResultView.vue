<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import VueApexCharts from 'vue3-apexcharts'
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
const equityOpts = computed(() => ({
  chart: { type: 'area', toolbar: { show: false }, animations: { enabled: false },
    background: 'transparent', fontFamily: 'Inter, sans-serif', zoom: { enabled: false },
    events: {
      dataPointSelection: (e, ctx, cfg) => pickIdx(cfg.dataPointIndex),
      markerClick: (e, ctx, { dataPointIndex }) => pickIdx(dataPointIndex),
    } },
  dataLabels: { enabled: false },
  stroke: { curve: 'smooth', width: 2.5 },
  fill: { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.28, opacityTo: 0 } },
  markers: { size: 0, hover: { size: 5 } },
  xaxis: { type: 'datetime', axisBorder: { show: false }, axisTicks: { show: false },
    labels: { style: { colors: '#64748b', fontSize: '11px' } } },
  yaxis: { labels: { style: { colors: '#64748b', fontSize: '11px' }, formatter: (v) => `$${Math.round(v)}` } },
  colors: ['#34d399'], grid: { borderColor: '#1e293b', strokeDashArray: 4 },
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
function fmtMoney(c) { return c == null ? '—' : `$${(c / 100).toFixed(2)}` }
const s = computed(() => (status.value && status.value.summary) || {})
function statusColor(st) { return st === 'finished' ? 'text-emerald-400' : st === 'error' ? 'text-rose-400' : st === 'stopped' ? 'text-slate-400' : 'text-amber-400' }

onMounted(async () => {
  await load()
  pollTimer = setInterval(() => { if (running.value) load() }, 2000)
  clockTimer = setInterval(() => { now.value = Date.now() }, 1000)
})
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer); if (clockTimer) clearInterval(clockTimer) })
</script>

<template>
  <main class="max-w-6xl mx-auto px-4 py-6 space-y-6">
    <div>
      <button @click="router.back()" class="text-sm text-slate-400 hover:text-slate-200">← Back</button>
      <div class="flex items-center gap-3 mt-1">
        <h1 class="text-xl font-semibold text-slate-100">Backtest <span class="font-mono text-slate-400 text-base">{{ id.slice(0, 8) }}</span></h1>
        <span v-if="status" :class="statusColor(status.status)" class="text-sm">{{ status.status }}</span>
        <span v-if="status && elapsed" class="text-xs text-slate-500">· {{ elapsed }}</span>
      </div>
      <div v-if="running" class="flex items-center gap-2 mt-2 max-w-md">
        <div class="flex-1 h-2 rounded-full bg-slate-700/60 overflow-hidden">
          <div class="h-full bg-amber-400 rounded-full transition-all" :style="{ width: Math.max(3, Math.round((status && status.progress) || 0)) + '%' }"></div>
        </div>
        <span class="text-xs text-slate-400">{{ Math.round((status && status.progress) || 0) }}%</span>
      </div>
    </div>

    <div v-if="status && status.error" class="bg-rose-500/10 border border-rose-500/40 rounded-lg p-3 text-sm text-rose-300">
      <span class="font-semibold">Error:</span> {{ status.error }}
    </div>

    <!-- Summary hero -->
    <section class="bg-surface border border-border-subtle rounded-xl p-4">
      <div class="grid grid-cols-2 md:grid-cols-6 gap-3">
        <div><div class="text-xs text-slate-500">Total P&L</div><div class="text-lg font-semibold" :class="(s.pnl_cents >= 0) ? 'text-emerald-400' : 'text-rose-400'">{{ fmtMoney(s.pnl_cents) }}</div></div>
        <div><div class="text-xs text-slate-500">ROI</div><div class="text-lg font-semibold text-slate-100">{{ fmtPct(s.roi) }}</div></div>
        <div><div class="text-xs text-slate-500">Bets</div><div class="text-lg font-semibold text-slate-100">{{ s.n_bets ?? '—' }}</div></div>
        <div><div class="text-xs text-slate-500">Win rate</div><div class="text-lg font-semibold text-slate-100">{{ fmtPct(s.win_rate) }}</div></div>
        <div><div class="text-xs text-slate-500">Avg CLV</div><div class="text-lg font-semibold text-slate-100">{{ fmtPct(s.clv_avg) }}</div></div>
        <div><div class="text-xs text-slate-500">API / cache</div><div class="text-lg font-semibold text-slate-100">{{ s.api_calls ?? 0 }} / {{ s.cache_hits ?? 0 }}</div></div>
      </div>
      <div class="mt-3 pt-3 border-t border-border-subtle/50 text-xs text-slate-500 flex flex-wrap gap-x-4 gap-y-1">
        <span>Fixtures: <span class="text-slate-300">{{ s.n_fixtures ?? 0 }}</span></span>
        <span>Bet: <span class="text-emerald-400">{{ s.bet ?? 0 }}</span></span>
        <span>No-edge: <span class="text-slate-300">{{ s.no_bet ?? 0 }}</span></span>
        <span>Unsettled: <span class="text-slate-300">{{ s.unsettled ?? 0 }}</span></span>
        <span>Unmatched: <span class="text-amber-400">{{ s.unmatched ?? 0 }}</span></span>
        <span>No-price: <span class="text-slate-300">{{ s.no_candle_data ?? 0 }}</span></span>
      </div>
    </section>

    <!-- Scrubbable equity chart -->
    <section v-if="trades.length" class="bg-surface border border-border-subtle rounded-xl p-4">
      <div class="flex items-center justify-between mb-2">
        <h2 class="text-sm font-semibold text-slate-200">Equity curve</h2>
        <span v-if="selectedDay" class="text-xs text-primary">{{ selectedDay === 'all' ? 'All' : selectedDay }} · {{ selectedTrades.length }} trade(s)</span>
      </div>
      <VueApexCharts type="area" height="280" :options="equityOpts" :series="equitySeries" />
      <!-- day chips -->
      <div class="flex flex-wrap gap-2 mt-3">
        <button @click="selectedDay = 'all'; tab = 'trades'"
                class="px-2.5 py-1 rounded-full text-xs border transition-colors"
                :class="selectedDay === 'all' ? 'bg-primary/20 border-primary text-primary' : 'border-border-subtle text-slate-400 hover:text-slate-200'">
          All · {{ trades.length }}
        </button>
        <button v-for="d in dayKeys" :key="d" @click="selectedDay = d; tab = 'trades'"
                class="px-2.5 py-1 rounded-full text-xs border transition-colors"
                :class="selectedDay === d ? 'bg-primary/20 border-primary text-primary' : 'border-border-subtle text-slate-400 hover:text-slate-200'">
          {{ d }} · {{ tradesByDay[d].length }}
        </button>
      </div>
    </section>

    <!-- Tabs -->
    <section class="bg-surface border border-border-subtle rounded-xl p-4">
      <div class="flex gap-4 border-b border-border-subtle/50 mb-3">
        <button v-for="t in ['trades', 'decisions', 'logs']" :key="t" @click="tab = t"
                class="pb-2 text-sm capitalize" :class="tab === t ? 'text-primary border-b-2 border-primary' : 'text-slate-400 hover:text-slate-200'">
          {{ t === 'decisions' ? 'Decision log' : t }}
        </button>
      </div>

      <!-- Trades (cards for the selected day) -->
      <div v-if="tab === 'trades'">
        <div v-if="!selectedTrades.length" class="text-sm text-slate-500">
          {{ trades.length ? 'Pick a day above to see its trades.' : 'No bets were placed under these settings over this range.' }}
        </div>
        <div v-else class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div v-for="(t, i) in selectedTrades" :key="i" class="bg-surface border border-border-subtle rounded-lg p-3">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-1.5 text-sm text-slate-100 font-medium min-w-0">
                <img v-if="t.home_flag" :src="t.home_flag" class="w-4 h-3 rounded-sm object-cover shrink-0" alt="" />
                <span class="truncate">{{ t.home || t.side }}</span>
                <span class="text-slate-500 text-xs shrink-0">v</span>
                <img v-if="t.away_flag" :src="t.away_flag" class="w-4 h-3 rounded-sm object-cover shrink-0" alt="" />
                <span class="truncate">{{ t.away }}</span>
              </div>
              <div class="text-sm font-semibold shrink-0 ml-2" :class="(t.realized_pnl_cents >= 0) ? 'text-emerald-400' : 'text-rose-400'">{{ fmtMoney(t.realized_pnl_cents) }}</div>
            </div>
            <div class="text-xs text-slate-400 mt-1">
              <span v-if="t.league" class="mr-1 text-slate-500">{{ t.league }}</span>
              <span class="text-slate-200">{{ pickLabel(t) }}</span> · entry {{ t.entry_cents }}¢ × {{ t.size }}
            </div>
            <!-- flags / badges -->
            <div class="flex flex-wrap gap-1.5 mt-2">
              <span class="px-1.5 py-0.5 rounded text-[10px] bg-slate-700/50 text-slate-300">edge {{ (t.edge * 100).toFixed(1) }}%</span>
              <span class="px-1.5 py-0.5 rounded text-[10px]" :class="t.sharp_prob != null ? 'bg-sky-500/20 text-sky-300' : 'bg-amber-500/20 text-amber-300'">{{ t.sharp_prob != null ? 'sharp' : 'model-only' }}</span>
              <span class="px-1.5 py-0.5 rounded text-[10px]" :class="t.outcome === 'win' ? 'bg-emerald-500/20 text-emerald-300' : 'bg-rose-500/20 text-rose-300'">{{ t.outcome }}</span>
              <span v-if="t.clv != null" class="px-1.5 py-0.5 rounded text-[10px] bg-slate-700/50 text-slate-300">CLV {{ (t.clv * 100).toFixed(1) }}%</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Decision log -->
      <div v-else-if="tab === 'decisions'" class="overflow-x-auto">
        <table class="w-full text-xs">
          <thead><tr class="text-left text-slate-500"><th class="py-1">Fixture</th><th>Decision</th><th>Reason</th></tr></thead>
          <tbody>
            <tr v-for="(d, i) in (result && result.decision_log) || []" :key="i" class="border-t border-border-subtle/40">
              <td class="py-1 text-slate-300">{{ d.label }}</td>
              <td><span :class="d.decision === 'placed' ? 'text-emerald-400' : d.decision === 'no_bet' ? 'text-slate-300' : 'text-amber-400'">{{ d.decision }}</span></td>
              <td class="text-slate-500">{{ d.reason || (d.bets ? d.bets.length + ' side(s)' : '') }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!(result && result.decision_log && result.decision_log.length)" class="text-sm text-slate-500">No decision log recorded.</p>
      </div>

      <!-- Logs -->
      <div v-else class="bg-black/40 rounded-lg p-3 font-mono text-[11px] text-slate-300 max-h-96 overflow-y-auto whitespace-pre-wrap">
        <div v-for="(line, i) in (result && result.logs) || []" :key="i">{{ line }}</div>
        <p v-if="!(result && result.logs && result.logs.length)" class="text-slate-500">No logs recorded.</p>
      </div>
    </section>
  </main>
</template>
