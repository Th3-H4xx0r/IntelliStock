<script setup>
import { ref, computed, onMounted } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import KalshiPortfolioChart from '../components/kalshi/KalshiPortfolioChart.vue'
import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')
function authHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

const accounts = ref([])         // kalshi brokerages
const selectedId = ref('')
const edges = ref([])
const positions = ref([])
const clv = ref({ overall: { avg_clv: 0, n: 0 }, by_league: {} })
const fills = ref([])
const scanBudget = ref(null)
const killing = ref(false)
const loadingAcct = ref(true)

const selected = computed(() => accounts.value.find((a) => a.id === selectedId.value) || null)

async function loadAccounts() {
  loadingAcct.value = true
  try {
    const res = await fetch(`${API_BASE}/brokerages`, { headers: authHeaders() })
    const d = await res.json()
    accounts.value = (d.accounts || []).filter((a) => a.brokerage_type === 'kalshi')
    if (accounts.value.length && !selectedId.value) selectedId.value = accounts.value[0].id
    if (selectedId.value) await loadAll()
  } finally {
    loadingAcct.value = false
  }
}

async function getJson(path) {
  try {
    const res = await fetch(`${API_BASE}/brokerages/${selectedId.value}${path}`, { headers: authHeaders() })
    if (!res.ok) return null
    return await res.json()
  } catch { return null }
}

async function loadAll() {
  if (!selectedId.value) return
  const [e, p, c, f, b] = await Promise.all([
    getJson('/kalshi/edges?limit=10'),
    getJson('/kalshi/positions'),
    getJson('/kalshi/clv'),
    getJson('/kalshi/fills?limit=20'),
    getJson('/kalshi/scan-budget'),
  ])
  edges.value = (e && e.edges) || []
  positions.value = (p && p.positions) || []
  clv.value = c || clv.value
  fills.value = (f && f.fills) || []
  scanBudget.value = b
}

function selectAccount(id) { selectedId.value = id; loadAll() }

async function kill() {
  if (!selectedId.value || killing.value) return
  if (!confirm('Stop the Kalshi instance and cancel all resting orders on this account?')) return
  killing.value = true
  try {
    await fetch(`${API_BASE}/brokerages/${selectedId.value}/kalshi/kill`, { method: 'POST', headers: authHeaders() })
    await loadAll()
  } finally { killing.value = false }
}

function pct(v) { return `${(v * 100).toFixed(1)}%` }
function clvLeagues() { return Object.entries(clv.value.by_league || {}) }

onMounted(loadAccounts)
</script>

<template>
  <AppShell>
    <div class="max-w-5xl mx-auto px-4 py-6">
      <!-- Header / account bar -->
      <div class="flex flex-wrap items-center gap-2 mb-4">
        <h1 class="text-xl font-extrabold text-slate-50 mr-2">⚽ Kalshi</h1>
        <select v-model="selectedId" @change="selectAccount(selectedId)"
                class="bg-[#0d1626] border border-slate-600 rounded-md text-xs text-slate-200 px-2 py-1">
          <option v-for="a in accounts" :key="a.id" :value="a.id">
            {{ a.account_name }} · {{ a.kalshi_environment || 'demo' }}
          </option>
        </select>
        <button @click="kill" :disabled="killing || !selectedId"
                class="ml-auto bg-red-600 hover:bg-red-700 text-white text-xs font-bold rounded-md px-3 py-1.5 disabled:opacity-50">
          ■ {{ killing ? 'KILLING…' : 'KILL' }}
        </button>
      </div>

      <div v-if="loadingAcct" class="text-sm text-slate-500 py-12 text-center">Loading…</div>
      <div v-else-if="!accounts.length" class="rounded-xl border border-border-subtle bg-[#111c30] p-8 text-center text-slate-400">
        No Kalshi account linked yet. Add one in <RouterLink to="/brokerages" class="text-sky-400">Brokerages</RouterLink>.
      </div>

      <div v-else class="flex flex-col gap-3">
        <!-- Chart hero (half on desktop) + account KPIs -->
        <div class="grid md:grid-cols-2 gap-3">
          <KalshiPortfolioChart :brokerage-id="selectedId" />
          <div class="rounded-xl border border-border-subtle bg-[#111c30] p-4">
            <div class="text-[11px] font-bold uppercase tracking-wide text-sky-300 mb-2">💼 Account summary</div>
            <div class="grid grid-cols-2 gap-2">
              <div class="rounded-lg bg-[#0d1626] border border-slate-800 p-2">
                <div class="text-base font-extrabold text-slate-50">{{ pct(clv.overall.avg_clv) }}</div>
                <div class="text-[9px] uppercase text-slate-500 font-bold">Avg CLV</div>
              </div>
              <div class="rounded-lg bg-[#0d1626] border border-slate-800 p-2">
                <div class="text-base font-extrabold text-slate-50">{{ clv.overall.n }}</div>
                <div class="text-[9px] uppercase text-slate-500 font-bold">Fixtures</div>
              </div>
              <div class="rounded-lg bg-[#0d1626] border border-slate-800 p-2">
                <div class="text-base font-extrabold text-slate-50">{{ positions.length }}</div>
                <div class="text-[9px] uppercase text-slate-500 font-bold">Open</div>
              </div>
              <div class="rounded-lg bg-[#0d1626] border border-slate-800 p-2">
                <div class="text-base font-extrabold text-emerald-400">{{ edges.length ? pct(edges[0].edge) : '—' }}</div>
                <div class="text-[9px] uppercase text-slate-500 font-bold">Top edge</div>
              </div>
            </div>
          </div>
        </div>

        <!-- Edge Radar -->
        <div class="rounded-xl border border-border-subtle bg-[#111c30] p-4">
          <div class="text-[11px] font-bold uppercase tracking-wide text-sky-300 mb-2">⚡ Edge Radar</div>
          <div v-if="!edges.length" class="text-xs text-slate-500">No +EV contracts flagged right now.</div>
          <div v-for="e in edges" :key="e.market_ticker" class="flex justify-between text-xs py-1.5 border-b border-slate-800/60">
            <span class="text-slate-300">{{ e.market_ticker }} · {{ e.side }}</span>
            <span class="text-emerald-400 font-bold">+{{ pct(e.edge) }}</span>
          </div>
        </div>

        <!-- Positions -->
        <div class="rounded-xl border border-border-subtle bg-[#111c30] p-4">
          <div class="text-[11px] font-bold uppercase tracking-wide text-sky-300 mb-2">📑 Open positions</div>
          <div v-if="!positions.length" class="text-xs text-slate-500">No open positions.</div>
          <div v-for="p in positions" :key="p.market_ticker" class="flex justify-between text-xs py-1.5 border-b border-slate-800/60">
            <span class="text-slate-300">{{ p.market_ticker }} {{ p.side }} ×{{ p.contracts }}</span>
            <span :class="(p.unrealized_cents || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'" class="font-bold">
              {{ p.unrealized_cents == null ? '—' : (p.unrealized_cents >= 0 ? '+' : '') + '$' + (p.unrealized_cents / 100).toFixed(2) }}
            </span>
          </div>
        </div>

        <!-- CLV + Scan budget -->
        <div class="grid md:grid-cols-2 gap-3">
          <div class="rounded-xl border border-border-subtle bg-[#111c30] p-4">
            <div class="text-[11px] font-bold uppercase tracking-wide text-sky-300 mb-2">📈 CLV scorecard</div>
            <div v-if="!clvLeagues().length" class="text-xs text-slate-500">No CLV logged yet.</div>
            <div v-for="[lg, s] in clvLeagues()" :key="lg" class="flex justify-between text-xs py-1.5 border-b border-slate-800/60">
              <span class="text-slate-300">{{ lg }} ({{ s.n }})</span>
              <span :class="s.avg_clv >= 0 ? 'text-emerald-400' : 'text-red-400'" class="font-bold">{{ pct(s.avg_clv) }}</span>
            </div>
          </div>
          <div class="rounded-xl border border-border-subtle bg-[#111c30] p-4">
            <div class="text-[11px] font-bold uppercase tracking-wide text-sky-300 mb-2">📡 Odds-budget guard</div>
            <div v-if="scanBudget" class="text-xs text-slate-400">
              OddsPapi this month: {{ scanBudget.used }} / {{ scanBudget.limit }} req
              <div class="h-2 rounded bg-slate-800 mt-1 overflow-hidden">
                <div class="h-full bg-indigo-500" :style="{ width: Math.min(100, (scanBudget.used / scanBudget.limit) * 100) + '%' }"></div>
              </div>
              <div class="mt-1 text-slate-500">~{{ scanBudget.fixtures_per_day }} fixtures/day affordable</div>
            </div>
            <div v-else class="text-xs text-slate-500">No budget data.</div>
          </div>
        </div>

        <!-- Recent fills -->
        <div class="rounded-xl border border-border-subtle bg-[#111c30] p-4">
          <div class="text-[11px] font-bold uppercase tracking-wide text-sky-300 mb-2">🧾 Recent fills</div>
          <div v-if="!fills.length" class="text-xs text-slate-500">No fills.</div>
          <div v-for="(f, i) in fills" :key="i" class="flex justify-between text-xs py-1.5 border-b border-slate-800/60">
            <span class="text-slate-300">{{ f.action }} {{ f.market_ticker }} {{ f.side }} ×{{ f.contracts }} @ {{ f.price_cents }}¢</span>
            <span class="text-slate-500">{{ f.ts }}</span>
          </div>
        </div>
      </div>
    </div>
  </AppShell>
</template>
