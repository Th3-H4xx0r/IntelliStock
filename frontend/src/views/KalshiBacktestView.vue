<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import VueApexCharts from 'vue3-apexcharts'
import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')
function authHeaders() {
  return { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` }
}

const route = useRoute()
const router = useRouter()
const instanceId = route.params.id

const LEAGUES = [
  'World Cup', 'World Cup Qualifiers', 'Champions League', 'Europa League',
  'EPL', 'EFL Championship', 'Serie A', 'Serie B', 'La Liga', 'La Liga 2',
  'Bundesliga', '2. Bundesliga', 'Ligue 1', 'Ligue 2', 'Eredivisie',
  'Primeira Liga', 'MLS', 'Brasileirão',
]

const brokerageId = ref('')
const loadErr = ref('')

// --- create form ---
const name = ref('')
const leagues = ref(['World Cup'])
const startDate = ref('')
const endDate = ref('')
const bankroll = ref(54)          // dollars
const edgePct = ref(3)            // %
const noSharpPct = ref(8)         // %
const kelly = ref(0.2)
const orderMin = ref(5)           // $
const orderMax = ref(10)          // $
const sharpWeight = ref(85)       // %
const oddspapiKey = ref('')
const submitting = ref(false)
const submitErr = ref('')

function toggleLeague(l) {
  const i = leagues.value.indexOf(l)
  if (i >= 0) leagues.value.splice(i, 1)
  else leagues.value.push(l)
}

async function loadInstance() {
  try {
    const res = await fetch(`${API_BASE}/instances/${instanceId}/kalshi/detail`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`detail ${res.status}`)
    const d = await res.json()
    brokerageId.value = d.brokerage_id
    const c = d.config || {}
    if (c.edge_threshold != null) edgePct.value = Math.round(c.edge_threshold * 1000) / 10
    if (c.no_sharp_edge_threshold != null) noSharpPct.value = Math.round(c.no_sharp_edge_threshold * 1000) / 10
    if (c.kelly_fraction != null) kelly.value = c.kelly_fraction
    if (c.order_size_min_cents != null) orderMin.value = Math.round(c.order_size_min_cents) / 100
    if (c.order_size_max_cents != null) orderMax.value = Math.round(c.order_size_max_cents) / 100
    if (c.sharp_weight != null) sharpWeight.value = Math.round(c.sharp_weight * 100)
    if (c.bankroll_cents != null) bankroll.value = Math.round(c.bankroll_cents / 100)
    if (Array.isArray(c.leagues) && c.leagues.length) leagues.value = [...c.leagues]
    if (c.oddspapi_api_key) oddspapiKey.value = c.oddspapi_api_key
  } catch (e) {
    loadErr.value = "Couldn't load the instance config."
  }
}

async function submit() {
  submitErr.value = ''
  if (!startDate.value || !endDate.value) { submitErr.value = 'Pick a start and end date.'; return }
  if (startDate.value > endDate.value) { submitErr.value = 'Start date must be on/before end date.'; return }
  if (!leagues.value.length) { submitErr.value = 'Select at least one league.'; return }
  submitting.value = true
  try {
    const body = {
      name: name.value || `Backtest ${startDate.value}..${endDate.value}`,
      instance_id: instanceId,
      leagues: leagues.value,
      start_date: startDate.value,
      end_date: endDate.value,
      bankroll_dollars: Number(bankroll.value) || 0,
      config: {
        edge_threshold: Number(edgePct.value) / 100,
        no_sharp_edge_threshold: Number(noSharpPct.value) / 100,
        kelly_fraction: Number(kelly.value),
        order_size_min_dollars: Number(orderMin.value) || 0,
        order_size_max_dollars: Number(orderMax.value) || 0,
        sharp_weight: Number(sharpWeight.value) / 100,
        oddspapi_api_key: oddspapiKey.value || undefined,
      },
    }
    const res = await fetch(`${API_BASE}/brokerages/${brokerageId.value}/kalshi/backtests`, {
      method: 'POST', headers: authHeaders(), body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(`create ${res.status}`)
    const d = await res.json()
    await loadBacktests()
    openResults(d.id)
  } catch (e) {
    submitErr.value = 'Failed to start the backtest.'
  } finally {
    submitting.value = false
  }
}

// --- list + polling ---
const backtests = ref([])
let pollTimer = null

async function loadBacktests() {
  if (!brokerageId.value) return
  try {
    const res = await fetch(`${API_BASE}/brokerages/${brokerageId.value}/kalshi/backtests`, { headers: authHeaders() })
    if (!res.ok) return
    const d = await res.json()
    backtests.value = d.backtests || []
  } catch (e) { /* ignore transient */ }
}

function anyRunning() {
  return backtests.value.some((b) => b.status === 'pending' || b.status === 'running')
}

async function tick() {
  await loadBacktests()
  if (selected.value && (selected.value.status === 'pending' || selected.value.status === 'running')) {
    await refreshResults(selected.value.id)
  }
}

async function stopBacktest(id) {
  await fetch(`${API_BASE}/kalshi/backtests/${id}/stop`, { method: 'POST', headers: authHeaders() })
  await loadBacktests()
}
async function delBacktest(id) {
  if (!confirm('Delete this backtest?')) return
  await fetch(`${API_BASE}/kalshi/backtests/${id}`, { method: 'DELETE', headers: authHeaders() })
  if (selected.value && selected.value.id === id) selected.value = null
  await loadBacktests()
}

// --- results ---
const selected = ref(null)     // status row
const result = ref(null)       // heavy result

async function refreshResults(id) {
  try {
    const res = await fetch(`${API_BASE}/kalshi/backtests/${id}/results`, { headers: authHeaders() })
    if (!res.ok) return
    const d = await res.json()
    selected.value = { ...(selected.value || {}), id, status: d.status, summary: d.summary || {} }
    result.value = d.result || null
  } catch (e) { /* ignore */ }
}
function openResults(id) {
  selected.value = { id, status: 'pending', summary: {} }
  result.value = null
  refreshResults(id)
}

const equitySeries = computed(() => {
  const ec = (result.value && result.value.equity_curve) || []
  return [{ name: 'Cumulative P&L ($)', data: ec.map((p) => [Number(p.ts) * 1000, (p.cum_pnl_cents || 0) / 100]) }]
})
const equityOpts = {
  chart: { type: 'area', toolbar: { show: false }, animations: { enabled: false } },
  dataLabels: { enabled: false },
  stroke: { curve: 'straight', width: 2 },
  xaxis: { type: 'datetime', labels: { style: { colors: '#94a3b8' } } },
  yaxis: { labels: { style: { colors: '#94a3b8' }, formatter: (v) => `$${Math.round(v)}` } },
  colors: ['#22d3ee'], grid: { borderColor: '#1e293b' }, tooltip: { theme: 'dark' },
}

function fmtPct(v) { return v == null ? '—' : `${(v * 100).toFixed(1)}%` }
function fmtMoney(c) { return c == null ? '—' : `$${(c / 100).toFixed(2)}` }
function statusColor(s) {
  return s === 'finished' ? 'text-emerald-400' : s === 'error' ? 'text-rose-400'
    : s === 'stopped' ? 'text-slate-400' : 'text-amber-400'
}

onMounted(async () => {
  await loadInstance()
  await loadBacktests()
  pollTimer = setInterval(tick, 3000)
})
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<template>
  <main class="max-w-6xl mx-auto px-4 py-6 space-y-6">
    <div class="flex items-center justify-between">
      <div>
        <button @click="router.push(`/kalshi/instances/${instanceId}`)" class="text-sm text-slate-400 hover:text-slate-200">← Back to instance</button>
        <h1 class="text-xl font-semibold text-slate-100 mt-1">Backtest</h1>
      </div>
    </div>
    <p v-if="loadErr" class="text-sm text-amber-400">{{ loadErr }}</p>

    <!-- Create form -->
    <section class="bg-surface border border-border-subtle rounded-xl p-4 space-y-4">
      <h2 class="text-sm font-semibold text-slate-200">New backtest</h2>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label class="block">
          <span class="text-xs text-slate-400">Name</span>
          <input v-model="name" type="text" placeholder="e.g. WC group stage" class="w-full mt-1 bg-surface border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-100" />
        </label>
        <label class="block">
          <span class="text-xs text-slate-400">Bankroll ($)</span>
          <input v-model.number="bankroll" type="number" min="1" class="w-full mt-1 bg-surface border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-100" />
        </label>
        <label class="block">
          <span class="text-xs text-slate-400">Start date</span>
          <input v-model="startDate" type="date" class="w-full mt-1 bg-surface border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-100" />
        </label>
        <label class="block">
          <span class="text-xs text-slate-400">End date</span>
          <input v-model="endDate" type="date" class="w-full mt-1 bg-surface border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-100" />
        </label>
      </div>

      <div>
        <span class="text-xs text-slate-400">Leagues</span>
        <div class="flex flex-wrap gap-2 mt-1">
          <button v-for="l in LEAGUES" :key="l" type="button" @click="toggleLeague(l)"
                  class="px-2.5 py-1 rounded-full text-xs border transition-colors"
                  :class="leagues.includes(l) ? 'bg-primary/20 border-primary text-primary' : 'border-border-subtle text-slate-400 hover:text-slate-200'">
            {{ l }}
          </button>
        </div>
      </div>

      <div class="grid grid-cols-2 md:grid-cols-3 gap-3">
        <label class="block"><span class="text-xs text-slate-400">Edge bar (%)</span>
          <input v-model.number="edgePct" type="number" step="0.5" class="w-full mt-1 bg-surface border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-100" /></label>
        <label class="block"><span class="text-xs text-slate-400">No-sharp bar (%)</span>
          <input v-model.number="noSharpPct" type="number" step="0.5" class="w-full mt-1 bg-surface border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-100" /></label>
        <label class="block"><span class="text-xs text-slate-400">Kelly fraction</span>
          <input v-model.number="kelly" type="number" step="0.05" class="w-full mt-1 bg-surface border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-100" /></label>
        <label class="block"><span class="text-xs text-slate-400">Order min ($)</span>
          <input v-model.number="orderMin" type="number" step="1" class="w-full mt-1 bg-surface border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-100" /></label>
        <label class="block"><span class="text-xs text-slate-400">Order max ($)</span>
          <input v-model.number="orderMax" type="number" step="1" class="w-full mt-1 bg-surface border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-100" /></label>
        <label class="block"><span class="text-xs text-slate-400">Sharp weight (%)</span>
          <input v-model.number="sharpWeight" type="number" step="5" class="w-full mt-1 bg-surface border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-100" /></label>
      </div>

      <label class="block">
        <span class="text-xs text-slate-400">OddsPapi API key <span class="text-slate-500">(for the sharp line — free tier; leave blank for model-only)</span></span>
        <input v-model="oddspapiKey" type="text" placeholder="oddspapi apiKey" class="w-full mt-1 bg-surface border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-100" />
      </label>

      <div class="flex items-center gap-3">
        <button @click="submit" :disabled="submitting || !brokerageId"
                class="px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium disabled:opacity-50">
          {{ submitting ? 'Starting…' : 'Run backtest' }}
        </button>
        <span v-if="submitErr" class="text-sm text-rose-400">{{ submitErr }}</span>
      </div>
    </section>

    <!-- Past backtests -->
    <section class="bg-surface border border-border-subtle rounded-xl p-4">
      <h2 class="text-sm font-semibold text-slate-200 mb-3">Backtests</h2>
      <div v-if="!backtests.length" class="text-sm text-slate-500">No backtests yet.</div>
      <table v-else class="w-full text-sm">
        <thead><tr class="text-left text-slate-500 text-xs">
          <th class="py-1">Name</th><th>Range</th><th>Status</th><th>P&L</th><th></th>
        </tr></thead>
        <tbody>
          <tr v-for="b in backtests" :key="b.id" class="border-t border-border-subtle/50">
            <td class="py-2 text-slate-200">{{ b.name || b.id.slice(0, 8) }}</td>
            <td class="text-slate-400">{{ b.start_date }} → {{ b.end_date }}</td>
            <td>
              <span :class="statusColor(b.status)">{{ b.status }}</span>
              <span v-if="b.status === 'running' || b.status === 'pending'" class="text-slate-500"> {{ Math.round(b.progress || 0) }}%</span>
            </td>
            <td :class="(b.summary && b.summary.pnl_cents >= 0) ? 'text-emerald-400' : 'text-rose-400'">
              {{ b.summary ? fmtMoney(b.summary.pnl_cents) : '—' }}
            </td>
            <td class="text-right">
              <button @click="openResults(b.id)" class="text-xs text-primary hover:underline mr-2">View</button>
              <button v-if="b.status === 'running' || b.status === 'pending'" @click="stopBacktest(b.id)" class="text-xs text-amber-400 hover:underline mr-2">Stop</button>
              <button @click="delBacktest(b.id)" class="text-xs text-slate-500 hover:text-rose-400">Delete</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- Results -->
    <section v-if="selected" class="bg-surface border border-border-subtle rounded-xl p-4 space-y-4">
      <div class="flex items-center justify-between">
        <h2 class="text-sm font-semibold text-slate-200">Results</h2>
        <span :class="statusColor(selected.status)" class="text-xs">{{ selected.status }}</span>
      </div>

      <div v-if="selected.summary" class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div class="bg-surface border border-border-subtle rounded-lg p-3">
          <div class="text-xs text-slate-500">Total P&L</div>
          <div class="text-lg font-semibold" :class="(selected.summary.pnl_cents >= 0) ? 'text-emerald-400' : 'text-rose-400'">{{ fmtMoney(selected.summary.pnl_cents) }}</div>
        </div>
        <div class="bg-surface border border-border-subtle rounded-lg p-3">
          <div class="text-xs text-slate-500">ROI</div><div class="text-lg font-semibold text-slate-100">{{ fmtPct(selected.summary.roi) }}</div>
        </div>
        <div class="bg-surface border border-border-subtle rounded-lg p-3">
          <div class="text-xs text-slate-500">Bets · Win rate</div><div class="text-lg font-semibold text-slate-100">{{ selected.summary.n_bets ?? '—' }} · {{ fmtPct(selected.summary.win_rate) }}</div>
        </div>
        <div class="bg-surface border border-border-subtle rounded-lg p-3">
          <div class="text-xs text-slate-500">Avg CLV · API/cache</div><div class="text-lg font-semibold text-slate-100">{{ fmtPct(selected.summary.clv_avg) }} · {{ selected.summary.api_calls ?? 0 }}/{{ selected.summary.cache_hits ?? 0 }}</div>
        </div>
      </div>

      <div v-if="result && result.equity_curve && result.equity_curve.length">
        <VueApexCharts type="area" height="260" :options="equityOpts" :series="equitySeries" />
      </div>

      <div v-if="result && result.trades && result.trades.length" class="overflow-x-auto">
        <h3 class="text-xs font-semibold text-slate-400 mb-2">Trades ({{ result.trades.length }})</h3>
        <table class="w-full text-xs">
          <thead><tr class="text-left text-slate-500">
            <th class="py-1">Ticker</th><th>Side</th><th>Entry</th><th>Size</th><th>Model</th><th>Sharp</th><th>Edge</th><th>Outcome</th><th>P&L</th><th>CLV</th>
          </tr></thead>
          <tbody>
            <tr v-for="(t, i) in result.trades" :key="i" class="border-t border-border-subtle/40">
              <td class="py-1 text-slate-300">{{ t.market_ticker }}</td>
              <td class="text-slate-400">{{ t.side }}</td>
              <td class="text-slate-400">{{ t.entry_cents }}¢</td>
              <td class="text-slate-400">{{ t.size }}</td>
              <td class="text-slate-400">{{ t.model_prob != null ? t.model_prob.toFixed(2) : '—' }}</td>
              <td class="text-slate-400">{{ t.sharp_prob != null ? t.sharp_prob.toFixed(2) : '—' }}</td>
              <td class="text-slate-400">{{ (t.edge * 100).toFixed(1) }}%</td>
              <td class="text-slate-400">{{ t.outcome }}</td>
              <td :class="(t.realized_pnl_cents >= 0) ? 'text-emerald-400' : 'text-rose-400'">{{ fmtMoney(t.realized_pnl_cents) }}</td>
              <td class="text-slate-400">{{ t.clv != null ? (t.clv * 100).toFixed(1) + '%' : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="result && result.per_league && Object.keys(result.per_league).length" class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <h3 class="text-xs font-semibold text-slate-400 mb-2">By league</h3>
          <table class="w-full text-xs">
            <thead><tr class="text-left text-slate-500"><th>League</th><th>Bets</th><th>P&L</th><th>ROI</th></tr></thead>
            <tbody>
              <tr v-for="(v, k) in result.per_league" :key="k" class="border-t border-border-subtle/40">
                <td class="py-1 text-slate-300">{{ k }}</td><td class="text-slate-400">{{ v.n }}</td>
                <td :class="(v.pnl_cents >= 0) ? 'text-emerald-400' : 'text-rose-400'">{{ fmtMoney(v.pnl_cents) }}</td>
                <td class="text-slate-400">{{ fmtPct(v.roi) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="result.calibration && result.calibration.length">
          <h3 class="text-xs font-semibold text-slate-400 mb-2">Calibration</h3>
          <table class="w-full text-xs">
            <thead><tr class="text-left text-slate-500"><th>Bucket</th><th>Predicted</th><th>Actual</th><th>n</th></tr></thead>
            <tbody>
              <tr v-for="(c, i) in result.calibration" :key="i" class="border-t border-border-subtle/40">
                <td class="py-1 text-slate-400">{{ (c.bucket_lo * 100).toFixed(0) }}–{{ (c.bucket_hi * 100).toFixed(0) }}%</td>
                <td class="text-slate-400">{{ (c.predicted * 100).toFixed(0) }}%</td>
                <td class="text-slate-400">{{ (c.actual * 100).toFixed(0) }}%</td>
                <td class="text-slate-400">{{ c.n }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <p v-if="result && (!result.trades || !result.trades.length) && selected.status === 'finished'" class="text-sm text-slate-500">
        No bets were placed under these settings over this range.
      </p>
    </section>
  </main>
</template>
