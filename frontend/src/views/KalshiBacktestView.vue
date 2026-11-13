<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import VueApexCharts from 'vue3-apexcharts'
import AppShell from '../layouts/AppShell.vue'
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
const noSharpPct = ref(5)
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
const marketShrink = ref(30)   // % pull toward the de-vigged market when no sharp
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
        market_shrink: Number(marketShrink.value) / 100,
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
  xaxis: { type: 'numeric', title: { text: 'Bet #', style: { color: '#71717a' } }, labels: { style: { colors: '#71717a', fontSize: '11px' } } },
  yaxis: { labels: { style: { colors: '#71717a', fontSize: '11px' }, formatter: (v) => `$${Math.round(v)}` } },
  colors: ['#ededed'], grid: { borderColor: 'rgba(255,255,255,0.05)' }, tooltip: { theme: 'dark' },
}

function fmtPct(v) { return v == null ? '—' : `${(v * 100).toFixed(1)}%` }
function fmtMoney(c) { return c == null ? '—' : `${c < 0 ? '-' : ''}$${Math.abs(c / 100).toFixed(2)}` }
// Status as a dot and a word: green only while a run is actually running.
function statusColor(s) {
  return s === 'finished' ? 'text-slate-300' : s === 'error' ? 'text-loss'
    : s === 'stopped' ? 'text-slate-500' : 'text-slate-200'
}
function statusDot(s) {
  return s === 'running' ? 'ui-dot-live' : s === 'pending' ? 'ui-dot-warn' : s === 'error' ? 'ui-dot-error' : 'ui-dot-off'
}

const inputCls = 'ui-input [color-scheme:dark]'

onMounted(async () => {
  await Promise.all([loadInstance(), loadModels()])
  await loadBacktests()
  pollTimer = setInterval(tick, 3000)
})
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<template>
  <AppShell>
    <main class="ui-page">
      <button @click="router.push(`/kalshi/instances/${instanceId}`)" class="inline-flex items-center gap-1 h-8 -ml-2 px-2 rounded-md text-sm text-slate-400 hover:text-slate-100 hover:bg-raised transition-colors duration-150 ease-out">
        <span class="material-symbols-outlined text-[16px]">arrow_back</span> Back to instance
      </button>
      <header class="mt-4">
        <h1 class="ui-page-title">Backtest</h1>
        <p class="ui-page-sub">Run this instance's settings over past fixtures</p>
      </header>
      <p v-if="loadErr" class="mt-6 ui-error">{{ loadErr }}</p>

      <section class="mt-8" aria-labelledby="kbt-new">
        <div class="mb-3">
          <h2 id="kbt-new" class="ui-section-title">New backtest</h2>
          <p class="ui-section-sub">Prefilled from the instance config</p>
        </div>
        <div class="ui-card px-5 py-5 space-y-6">
          <!-- Risk tolerance preset -->
          <div>
            <span class="ui-field-label">Risk tolerance</span>
            <div class="ui-seg w-full sm:w-auto" role="group" aria-label="Risk tolerance">
              <button v-for="(p, k) in RISK_PRESETS" :key="k" type="button" @click="applyPreset(k)"
                      class="ui-seg-btn flex-1 sm:flex-none sm:px-5"
                      :class="tier === k ? 'ui-seg-btn-active' : ''"
                      :aria-pressed="tier === k">
                {{ p.label }}
              </button>
            </div>
            <p class="ui-help">{{ PRESET_DESC[tier] }}</p>
          </div>

          <!-- Analyst LLM model -->
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <label class="block">
              <span class="ui-field-label">Analyst LLM model</span>
              <select v-model="modelId" @change="useLlm = !!modelId" :class="inputCls">
                <option value="">None (statistical model only)</option>
                <option v-for="m in models" :key="m.id" :value="m.id">{{ m.name || m.model || m.id }}</option>
              </select>
              <label class="flex items-center gap-2 mt-2 text-xs text-slate-400">
                <input type="checkbox" v-model="useLlm" :disabled="!modelId" class="accent-primary" /> Use LLM analyst in this backtest
              </label>
            </label>
            <div class="grid grid-cols-2 gap-4">
              <label class="block"><span class="ui-field-label">Bankroll ($)</span>
                <input v-model.number="bankroll" type="number" min="1" :class="inputCls" /></label>
              <label class="block"><span class="ui-field-label">Bankroll usage (%)</span>
                <input v-model.number="usagePct" type="number" step="5" :class="inputCls" /></label>
            </div>
          </div>

          <!-- Dates -->
          <div class="grid grid-cols-2 gap-4">
            <label class="block"><span class="ui-field-label">Start date</span>
              <input v-model="startDate" type="date" :class="inputCls" /></label>
            <label class="block"><span class="ui-field-label">End date</span>
              <input v-model="endDate" type="date" :class="inputCls" /></label>
          </div>

          <!-- Leagues -->
          <div>
            <span class="ui-field-label">Leagues <span class="text-slate-500 tabular-nums">· {{ leagues.length }} selected</span></span>
            <div class="flex flex-wrap gap-1.5">
              <button v-for="l in LEAGUES" :key="l" type="button" @click="toggleLeague(l)"
                      class="h-8 px-3 rounded-lg text-[13px] border transition-colors duration-150 ease-out"
                      :class="leagues.includes(l) ? 'bg-raised border-white/[0.12] text-slate-100 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]' : 'border-white/[0.06] text-slate-500 hover:text-slate-200'"
                      :aria-pressed="leagues.includes(l)">
                {{ l }}
              </button>
            </div>
          </div>

          <!-- Tuning grid -->
          <div class="grid grid-cols-2 md:grid-cols-3 gap-4">
            <label class="block"><span class="ui-field-label">Edge threshold (%)</span><input v-model.number="edgePct" type="number" step="0.5" :class="inputCls" /></label>
            <label class="block"><span class="ui-field-label">No-sharp bar (%)</span><input v-model.number="noSharpPct" type="number" step="0.5" :class="inputCls" /></label>
            <label class="block"><span class="ui-field-label">Kelly fraction</span><input v-model.number="kelly" type="number" step="0.05" :class="inputCls" /></label>
            <label class="block"><span class="ui-field-label">Max contracts / market</span><input v-model.number="maxContracts" type="number" :class="inputCls" /></label>
            <label class="block"><span class="ui-field-label">Max open exposure (%)</span><input v-model.number="exposurePct" type="number" :class="inputCls" /></label>
            <label class="block"><span class="ui-field-label">Per-league cap (%)</span><input v-model.number="leagueCapPct" type="number" :class="inputCls" /></label>
            <label class="block"><span class="ui-field-label">Min price (¢)</span><input v-model.number="minPrice" type="number" :class="inputCls" /></label>
            <label class="block"><span class="ui-field-label">Max price (¢)</span><input v-model.number="maxPrice" type="number" :class="inputCls" /></label>
            <label class="block"><span class="ui-field-label">Draw min edge (%)</span><input v-model.number="drawMinEdge" type="number" step="0.5" :class="inputCls" /></label>
            <label class="block"><span class="ui-field-label">Order min ($)</span><input v-model.number="orderMin" type="number" :class="inputCls" /></label>
            <label class="block"><span class="ui-field-label">Order max ($)</span><input v-model.number="orderMax" type="number" :class="inputCls" /></label>
            <label class="block"><span class="ui-field-label">Sharp weight (%)</span><input v-model.number="sharpWeight" type="number" step="5" :class="inputCls" /></label>
            <label class="block"><span class="ui-field-label">Daily loss cap (%)</span><input v-model.number="dailyLossPct" type="number" :class="inputCls" /></label>
            <label class="block"><span class="ui-field-label">Market anchor (%)</span><input v-model.number="marketShrink" type="number" step="5" :class="inputCls" /></label>
          </div>

          <label class="block">
            <span class="ui-field-label">OddsPapi API key</span>
            <input v-model="oddspapiKey" type="text" placeholder="oddspapi apiKey" :class="inputCls" />
            <span class="ui-help block">Sharp line, free tier. Saved for next time; blank means model only</span>
          </label>

          <div class="flex items-center gap-3 pt-1">
            <button @click="submit" :disabled="submitting || !brokerageId" class="ui-btn-primary">
              {{ submitting ? 'Starting…' : 'Run backtest' }}
            </button>
            <span v-if="submitErr" class="text-sm text-loss">{{ submitErr }}</span>
          </div>
        </div>
      </section>

      <!-- Past backtests -->
      <section class="mt-12" aria-labelledby="kbt-list">
        <div class="mb-3">
          <h2 id="kbt-list" class="ui-section-title">Backtests</h2>
          <p class="ui-section-sub">For this Kalshi account</p>
        </div>
        <div class="ui-card overflow-hidden">
          <p v-if="!backtests.length" class="ui-empty">No backtests yet</p>
          <div v-else class="overflow-x-auto">
            <table class="w-full min-w-[640px] text-sm">
              <thead>
                <tr class="text-left text-xs text-slate-500 border-b border-white/[0.06]">
                  <th class="h-10 px-5 font-normal">ID</th>
                  <th class="h-10 px-5 font-normal">Range</th>
                  <th class="h-10 px-5 font-normal">Status</th>
                  <th class="h-10 px-5 font-normal text-right">P&amp;L</th>
                  <th class="h-10 px-5"></th>
                </tr>
              </thead>
              <tbody class="divide-y divide-white/[0.05]">
                <tr v-for="b in backtests" :key="b.id">
                  <td class="h-14 px-5 text-xs text-slate-400 font-mono">{{ b.id.slice(0, 8) }}</td>
                  <td class="h-14 px-5 text-slate-300 tabular-nums whitespace-nowrap">{{ b.start_date }} → {{ b.end_date }}</td>
                  <td class="h-14 px-5">
                    <div class="flex items-center gap-3">
                      <span class="inline-flex items-center gap-2 capitalize" :class="statusColor(b.status)"><span class="ui-dot" :class="statusDot(b.status)"></span>{{ b.status }}</span>
                      <template v-if="b.status === 'running' || b.status === 'pending'">
                        <div class="w-16 h-1 rounded-full bg-raised overflow-hidden">
                          <div class="h-full bg-slate-300 rounded-full" :style="{ width: Math.max(3, Math.round(b.progress || 0)) + '%' }"></div>
                        </div>
                        <span class="text-slate-500 text-xs tabular-nums">{{ Math.round(b.progress || 0) }}%</span>
                      </template>
                    </div>
                  </td>
                  <td class="h-14 px-5 text-right tabular-nums" :class="!b.summary ? 'text-slate-500' : (b.summary.pnl_cents >= 0 ? 'text-gain' : 'text-loss')">{{ b.summary ? fmtMoney(b.summary.pnl_cents) : '—' }}</td>
                  <td class="h-14 px-5">
                    <div class="flex items-center justify-end gap-1">
                      <button @click="openResults(b.id)" class="ui-btn-ghost">View</button>
                      <button v-if="b.status === 'running' || b.status === 'pending'" @click="stopBacktest(b.id)" class="ui-btn-ghost">Stop</button>
                      <button @click="delBacktest(b.id)" class="ui-btn-ghost hover:text-loss">Delete</button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </main>
  </AppShell>
</template>
