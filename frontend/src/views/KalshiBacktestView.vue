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

// Risk presets tune every config value (mirrors instance creation).
const RISK_PRESETS = {
  low: { label: 'Low', edgePct: 5, kelly: 0.10, maxContracts: 25, exposurePct: 10, leagueCapPct: 12, usagePct: 40, dailyLossPct: 5, orderMin: 1, orderMax: 3 },
  medium: { label: 'Medium', edgePct: 4, kelly: 0.125, maxContracts: 50, exposurePct: 15, leagueCapPct: 25, usagePct: 50, dailyLossPct: 8, orderMin: 2, orderMax: 5 },
  high: { label: 'High', edgePct: 3, kelly: 0.15, maxContracts: 75, exposurePct: 25, leagueCapPct: 30, usagePct: 60, dailyLossPct: 10, orderMin: 5, orderMax: 10 },
  max: { label: 'Max', edgePct: 2, kelly: 0.20, maxContracts: 100, exposurePct: 40, leagueCapPct: 40, usagePct: 70, dailyLossPct: 15, orderMin: 8, orderMax: 15 },
}
const PRESET_DESC = {
  low: 'Conservative — fewer, higher-conviction bets at small size.',
  medium: 'Balanced edge, size, and exposure.',
  high: 'More +EV spots at larger size; higher variance.',
  max: 'Aggressive — most +EV spots at largest size. Highest variance.',
}

const brokerageId = ref('')
const loadErr = ref('')

// --- form state ---
const tier = ref('max')
const leagues = ref(['World Cup'])
const startDate = ref('')
const endDate = ref('')
const bankroll = ref(54)
const edgePct = ref(2)
const noSharpPct = ref(3)
const kelly = ref(0.2)
const maxContracts = ref(100)
const exposurePct = ref(40)
const leagueCapPct = ref(40)
const usagePct = ref(70)
const dailyLossPct = ref(15)
const minPrice = ref(15)
const maxPrice = ref(90)
const drawMinEdge = ref(10)
const orderMin = ref(8)
const orderMax = ref(15)
const sharpWeight = ref(85)
const oddspapiKey = ref('')

// LLM analyst
const models = ref([])
const modelId = ref('')
const useLlm = ref(false)
const analystMaxCalls = ref(10)

const submitting = ref(false)
const submitErr = ref('')

function applyPreset(key) {
  tier.value = key
  const p = RISK_PRESETS[key]
  edgePct.value = p.edgePct; kelly.value = p.kelly; maxContracts.value = p.maxContracts
  exposurePct.value = p.exposurePct; leagueCapPct.value = p.leagueCapPct
  usagePct.value = p.usagePct; dailyLossPct.value = p.dailyLossPct
  orderMin.value = p.orderMin; orderMax.value = p.orderMax
}
function toggleLeague(l) {
  const i = leagues.value.indexOf(l)
  if (i >= 0) leagues.value.splice(i, 1); else leagues.value.push(l)
}

async function loadModels() {
  try {
    const res = await fetch(`${API_BASE}/models`, { headers: authHeaders() })
    if (!res.ok) return
    const d = await res.json()
    models.value = (d.models || d || []).filter((m) => m && m.id)
  } catch (e) { /* ignore */ }
}

async function loadInstance() {
  try {
    const res = await fetch(`${API_BASE}/instances/${instanceId}/kalshi/detail`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`detail ${res.status}`)
    const d = await res.json()
    brokerageId.value = d.brokerage_id
    const c = d.config || {}
    if (c.tier) tier.value = c.tier
    if (c.edge_threshold != null) edgePct.value = Math.round(c.edge_threshold * 1000) / 10
    if (c.no_sharp_edge_threshold != null) noSharpPct.value = Math.round(c.no_sharp_edge_threshold * 1000) / 10
    if (c.kelly_fraction != null) kelly.value = c.kelly_fraction
    if (c.max_contracts_per_market != null) maxContracts.value = c.max_contracts_per_market
    if (c.max_open_exposure_frac != null) exposurePct.value = Math.round(c.max_open_exposure_frac * 100)
    if (c.per_league_cap_frac != null) leagueCapPct.value = Math.round(c.per_league_cap_frac * 100)
    if (c.bankroll_usage_pct != null) usagePct.value = c.bankroll_usage_pct
    if (c.daily_loss_cap_frac != null) dailyLossPct.value = Math.round(c.daily_loss_cap_frac * 100)
    if (c.min_price_cents != null) minPrice.value = c.min_price_cents
    if (c.max_price_cents != null) maxPrice.value = c.max_price_cents
    if (c.draw_min_edge != null) drawMinEdge.value = Math.round(c.draw_min_edge * 1000) / 10
    if (c.order_size_min_cents != null) orderMin.value = Math.round(c.order_size_min_cents) / 100
    if (c.order_size_max_cents != null) orderMax.value = Math.round(c.order_size_max_cents) / 100
    if (c.sharp_weight != null) sharpWeight.value = Math.round(c.sharp_weight * 100)
    if (c.bankroll_cents != null) bankroll.value = Math.round(c.bankroll_cents / 100)
    if (Array.isArray(c.leagues) && c.leagues.length) leagues.value = [...c.leagues]
    if (c.oddspapi_api_key) oddspapiKey.value = c.oddspapi_api_key
    if (c.model) { modelId.value = c.model; useLlm.value = true }
    if (c.analyst_max_calls != null) analystMaxCalls.value = c.analyst_max_calls
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
      instance_id: instanceId,
      leagues: leagues.value,
      start_date: startDate.value,
      end_date: endDate.value,
      bankroll_dollars: Number(bankroll.value) || 0,
      config: {
        tier: tier.value,
        edge_threshold: Number(edgePct.value) / 100,
        no_sharp_edge_threshold: Number(noSharpPct.value) / 100,
        kelly_fraction: Number(kelly.value),
        max_contracts_per_market: Number(maxContracts.value),
        max_open_exposure_frac: Number(exposurePct.value) / 100,
        per_league_cap_frac: Number(leagueCapPct.value) / 100,
        bankroll_usage_pct: Number(usagePct.value),
        daily_loss_cap_frac: Number(dailyLossPct.value) / 100,
        min_price_cents: Number(minPrice.value),
        max_price_cents: Number(maxPrice.value),
        draw_min_edge: Number(drawMinEdge.value) / 100,
        order_size_min_dollars: Number(orderMin.value) || 0,
        order_size_max_dollars: Number(orderMax.value) || 0,
        sharp_weight: Number(sharpWeight.value) / 100,
        oddspapi_api_key: oddspapiKey.value || undefined,
        model: useLlm.value ? (modelId.value || undefined) : undefined,
        use_llm: useLlm.value && !!modelId.value,
        analyst_max_calls: Number(analystMaxCalls.value) || 10,
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
  } catch (e) { /* ignore */ }
}

async function tick() {
  await loadBacktests()
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
const selected = ref(null)
const result = ref(null)

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
  router.push(`/kalshi/backtests/${id}`)
}

const equitySeries = computed(() => {
  const ec = (result.value && result.value.equity_curve) || []
  return [{ name: 'Cumulative P&L ($)', data: ec.map((v, i) => [i + 1, (v || 0) / 100]) }]
})
const equityOpts = {
  chart: { type: 'area', toolbar: { show: false }, animations: { enabled: false } },
  dataLabels: { enabled: false },
  stroke: { curve: 'straight', width: 2 },
  xaxis: { type: 'numeric', title: { text: 'Bet #', style: { color: '#64748b' } }, labels: { style: { colors: '#94a3b8' } } },
  yaxis: { labels: { style: { colors: '#94a3b8' }, formatter: (v) => `$${Math.round(v)}` } },
  colors: ['#22d3ee'], grid: { borderColor: '#1e293b' }, tooltip: { theme: 'dark' },
}

function fmtPct(v) { return v == null ? '—' : `${(v * 100).toFixed(1)}%` }
function fmtMoney(c) { return c == null ? '—' : `$${(c / 100).toFixed(2)}` }
function statusColor(s) {
  return s === 'finished' ? 'text-emerald-400' : s === 'error' ? 'text-rose-400'
    : s === 'stopped' ? 'text-slate-400' : 'text-amber-400'
}

const inputCls = 'w-full mt-1 bg-surface border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-100'

onMounted(async () => {
  await Promise.all([loadInstance(), loadModels()])
  await loadBacktests()
  pollTimer = setInterval(tick, 3000)
})
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<template>
  <main class="max-w-6xl mx-auto px-4 py-6 space-y-6">
    <div>
      <button @click="router.push(`/kalshi/instances/${instanceId}`)" class="text-sm text-slate-400 hover:text-slate-200">← Back to instance</button>
      <h1 class="text-xl font-semibold text-slate-100 mt-1">Backtest</h1>
    </div>
    <p v-if="loadErr" class="text-sm text-amber-400">{{ loadErr }}</p>

    <section class="bg-surface border border-border-subtle rounded-xl p-4 space-y-4">
      <h2 class="text-sm font-semibold text-slate-200">New backtest</h2>

      <!-- Risk tolerance preset -->
      <div>
        <span class="text-xs text-slate-400">Risk tolerance</span>
        <div class="flex gap-2 mt-1">
          <button v-for="(p, k) in RISK_PRESETS" :key="k" type="button" @click="applyPreset(k)"
                  class="flex-1 px-3 py-2 rounded-lg text-sm border transition-colors"
                  :class="tier === k ? 'bg-primary/20 border-primary text-primary' : 'border-border-subtle text-slate-400 hover:text-slate-200'">
            {{ p.label }}
          </button>
        </div>
        <p class="text-[11px] text-slate-500 mt-1">{{ PRESET_DESC[tier] }}</p>
      </div>

      <!-- Analyst LLM model -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label class="block">
          <span class="text-xs text-slate-400">Analyst LLM model</span>
          <select v-model="modelId" @change="useLlm = !!modelId" :class="inputCls">
            <option value="">None (statistical model only)</option>
            <option v-for="m in models" :key="m.id" :value="m.id">{{ m.name || m.model || m.id }}</option>
          </select>
          <label class="flex items-center gap-2 mt-2 text-[11px] text-slate-400">
            <input type="checkbox" v-model="useLlm" :disabled="!modelId" /> Use LLM analyst in this backtest
          </label>
        </label>
        <div class="grid grid-cols-2 gap-3">
          <label class="block"><span class="text-xs text-slate-400">Bankroll ($)</span>
            <input v-model.number="bankroll" type="number" min="1" :class="inputCls" /></label>
          <label class="block"><span class="text-xs text-slate-400">Bankroll usage (%)</span>
            <input v-model.number="usagePct" type="number" step="5" :class="inputCls" /></label>
        </div>
      </div>

      <!-- Dates -->
      <div class="grid grid-cols-2 gap-4">
        <label class="block"><span class="text-xs text-slate-400">Start date</span>
          <input v-model="startDate" type="date" :class="inputCls" /></label>
        <label class="block"><span class="text-xs text-slate-400">End date</span>
          <input v-model="endDate" type="date" :class="inputCls" /></label>
      </div>

      <!-- Leagues -->
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

      <!-- Tuning grid -->
      <div class="grid grid-cols-2 md:grid-cols-3 gap-3">
        <label class="block"><span class="text-xs text-slate-400">Edge threshold (%)</span><input v-model.number="edgePct" type="number" step="0.5" :class="inputCls" /></label>
        <label class="block"><span class="text-xs text-slate-400">No-sharp bar (%)</span><input v-model.number="noSharpPct" type="number" step="0.5" :class="inputCls" /></label>
        <label class="block"><span class="text-xs text-slate-400">Kelly fraction</span><input v-model.number="kelly" type="number" step="0.05" :class="inputCls" /></label>
        <label class="block"><span class="text-xs text-slate-400">Max contracts / market</span><input v-model.number="maxContracts" type="number" :class="inputCls" /></label>
        <label class="block"><span class="text-xs text-slate-400">Max open exposure (%)</span><input v-model.number="exposurePct" type="number" :class="inputCls" /></label>
        <label class="block"><span class="text-xs text-slate-400">Per-league cap (%)</span><input v-model.number="leagueCapPct" type="number" :class="inputCls" /></label>
        <label class="block"><span class="text-xs text-slate-400">Min price (¢)</span><input v-model.number="minPrice" type="number" :class="inputCls" /></label>
        <label class="block"><span class="text-xs text-slate-400">Max price (¢)</span><input v-model.number="maxPrice" type="number" :class="inputCls" /></label>
        <label class="block"><span class="text-xs text-slate-400">Draw min edge (%)</span><input v-model.number="drawMinEdge" type="number" step="0.5" :class="inputCls" /></label>
        <label class="block"><span class="text-xs text-slate-400">Order min ($)</span><input v-model.number="orderMin" type="number" :class="inputCls" /></label>
        <label class="block"><span class="text-xs text-slate-400">Order max ($)</span><input v-model.number="orderMax" type="number" :class="inputCls" /></label>
        <label class="block"><span class="text-xs text-slate-400">Sharp weight (%)</span><input v-model.number="sharpWeight" type="number" step="5" :class="inputCls" /></label>
        <label class="block"><span class="text-xs text-slate-400">Daily loss cap (%)</span><input v-model.number="dailyLossPct" type="number" :class="inputCls" /></label>
      </div>

      <label class="block">
        <span class="text-xs text-slate-400">OddsPapi API key <span class="text-slate-500">(sharp line — free tier; saved for next time; blank = model-only)</span></span>
        <input v-model="oddspapiKey" type="text" placeholder="oddspapi apiKey" :class="inputCls" />
      </label>

      <div class="flex items-center gap-3">
        <button @click="submit" :disabled="submitting || !brokerageId" class="px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium disabled:opacity-50">
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
        <thead><tr class="text-left text-slate-500 text-xs"><th class="py-1">ID</th><th>Range</th><th>Status</th><th>P&L</th><th></th></tr></thead>
        <tbody>
          <tr v-for="b in backtests" :key="b.id" class="border-t border-border-subtle/50">
            <td class="py-2 text-slate-300 font-mono text-xs">{{ b.id.slice(0, 8) }}</td>
            <td class="text-slate-400">{{ b.start_date }} → {{ b.end_date }}</td>
            <td>
              <div class="flex items-center gap-2">
                <span :class="statusColor(b.status)">{{ b.status }}</span>
                <template v-if="b.status === 'running' || b.status === 'pending'">
                  <div class="w-16 h-1.5 rounded-full bg-slate-700/60 overflow-hidden">
                    <div class="h-full bg-amber-400 rounded-full transition-all" :style="{ width: Math.max(3, Math.round(b.progress || 0)) + '%' }"></div>
                  </div>
                  <span class="text-slate-500 text-[11px]">{{ Math.round(b.progress || 0) }}%</span>
                </template>
              </div>
            </td>
            <td :class="(b.summary && b.summary.pnl_cents >= 0) ? 'text-emerald-400' : 'text-rose-400'">{{ b.summary ? fmtMoney(b.summary.pnl_cents) : '—' }}</td>
            <td class="text-right">
              <button @click="openResults(b.id)" class="text-xs text-primary hover:underline mr-2">View</button>
              <button v-if="b.status === 'running' || b.status === 'pending'" @click="stopBacktest(b.id)" class="text-xs text-amber-400 hover:underline mr-2">Stop</button>
              <button @click="delBacktest(b.id)" class="text-xs text-slate-500 hover:text-rose-400">Delete</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

  </main>
</template>
