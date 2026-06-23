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

const dropdownOpen = ref(false)
function pickAccount(id) {
  selectedId.value = id
  dropdownOpen.value = false
  loadAll()
}

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
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10 space-y-6">

      <!-- Header -->
      <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p class="text-primary text-xs font-bold uppercase tracking-widest mb-2 flex items-center gap-1.5">
            <span class="material-symbols-outlined text-[16px]">sports_soccer</span> Kalshi
          </p>
          <h1 class="text-2xl sm:text-3xl font-bold leading-tight text-slate-100">Prediction markets</h1>
          <p class="text-slate-500 mt-1.5 text-sm">Autonomous soccer event-contract trading.</p>
        </div>
        <div class="flex items-center gap-2">
          <!-- Single account: static chip. Multiple: themed custom dropdown
               (a native <select> can't theme its OS-rendered option list). -->
          <div v-if="accounts.length === 1"
               class="flex items-center gap-2 bg-surface border border-border-subtle rounded-lg text-sm text-slate-300 px-3 py-2">
            <span class="material-symbols-outlined text-primary text-[16px]">account_circle</span>
            {{ selected?.account_name }}
          </div>
          <div v-else-if="accounts.length" class="relative">
            <button @click="dropdownOpen = !dropdownOpen"
                    class="flex items-center gap-2 bg-surface border border-border-subtle rounded-lg text-sm text-slate-200 pl-3 pr-2 py-2 hover:border-primary/50 transition-colors">
              <span class="material-symbols-outlined text-primary text-[16px]">account_circle</span>
              <span class="max-w-[160px] truncate">{{ selected?.account_name }}</span>
              <span class="material-symbols-outlined text-slate-500 text-[18px] transition-transform" :class="{ 'rotate-180': dropdownOpen }">expand_more</span>
            </button>
            <div v-if="dropdownOpen" @click="dropdownOpen = false" class="fixed inset-0 z-40"></div>
            <div v-if="dropdownOpen" class="absolute right-0 mt-1.5 w-60 z-50 glass-card rounded-xl overflow-hidden py-1 shadow-xl shadow-black/40">
              <button v-for="a in accounts" :key="a.id" @click="pickAccount(a.id)"
                      class="w-full text-left px-3 py-2.5 text-sm flex items-center gap-2 hover:bg-primary/10 transition-colors"
                      :class="a.id === selectedId ? 'text-primary' : 'text-slate-300'">
                <span class="material-symbols-outlined text-[16px]" :class="a.id === selectedId ? 'text-primary' : 'text-transparent'">check</span>
                {{ a.account_name }}
              </button>
            </div>
          </div>
          <button
            v-if="selectedId" @click="kill" :disabled="killing"
            class="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-red-500/40 text-red-400 text-sm font-semibold hover:bg-red-500/10 transition-all disabled:opacity-50"
          >
            <span class="material-symbols-outlined text-[18px]">stop_circle</span>
            {{ killing ? 'Killing…' : 'Kill' }}
          </button>
        </div>
      </div>

      <!-- Loading -->
      <div v-if="loadingAcct" class="grid grid-cols-1 md:grid-cols-2 gap-5">
        <div v-for="n in 2" :key="n" class="glass-card rounded-2xl h-[260px] animate-pulse"></div>
      </div>

      <!-- No accounts -->
      <div v-else-if="!accounts.length"
           class="rounded-2xl border border-border-subtle bg-surface/20 px-8 py-12 flex flex-col items-center gap-3 text-center">
        <span class="material-symbols-outlined text-4xl text-slate-700">sports_soccer</span>
        <p class="text-slate-400 text-sm font-medium">No Kalshi account linked.</p>
        <p class="text-slate-600 text-xs max-w-sm">Link a Kalshi brokerage (demo or live) to monitor your portfolio, edge, and positions here.</p>
        <RouterLink to="/brokerages"
                    class="mt-1 px-4 py-2 rounded-lg bg-primary text-background-dark text-xs font-bold hover:brightness-110 transition-all">
          Link a brokerage
        </RouterLink>
      </div>

      <template v-else>
        <!-- Chart (half on desktop) + account summary -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <KalshiPortfolioChart :brokerage-id="selectedId" />

          <div class="glass-card rounded-2xl p-4 sm:p-5">
            <div class="flex items-center gap-2 mb-4">
              <span class="material-symbols-outlined text-primary text-[18px]">account_balance_wallet</span>
              <span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Account summary</span>
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3">
                <div class="text-lg font-bold text-slate-100 tabular-nums">{{ pct(clv.overall.avg_clv) }}</div>
                <div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Avg CLV</div>
              </div>
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3">
                <div class="text-lg font-bold text-slate-100 tabular-nums">{{ clv.overall.n }}</div>
                <div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Fixtures</div>
              </div>
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3">
                <div class="text-lg font-bold text-slate-100 tabular-nums">{{ positions.length }}</div>
                <div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Open</div>
              </div>
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3">
                <div class="text-lg font-bold tabular-nums" :class="edges.length ? 'text-emerald-400' : 'text-slate-100'">{{ edges.length ? pct(edges[0].edge) : '—' }}</div>
                <div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Top edge</div>
              </div>
            </div>
          </div>
        </div>

        <!-- Edge Radar -->
        <div class="glass-card rounded-2xl p-4 sm:p-5">
          <div class="flex items-center gap-2 mb-3">
            <span class="material-symbols-outlined text-primary text-[18px]">bolt</span>
            <span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Edge Radar</span>
          </div>
          <p v-if="!edges.length" class="text-sm text-slate-500">No +EV contracts flagged right now.</p>
          <div v-for="e in edges" :key="e.market_ticker" class="flex items-center justify-between text-sm py-2 border-b border-border-subtle/60 last:border-0">
            <span class="text-slate-300">{{ e.market_ticker }} <span class="text-slate-500">· {{ e.side }}</span></span>
            <span class="text-emerald-400 font-bold tabular-nums">+{{ pct(e.edge) }}</span>
          </div>
        </div>

        <!-- Positions -->
        <div class="glass-card rounded-2xl p-4 sm:p-5">
          <div class="flex items-center gap-2 mb-3">
            <span class="material-symbols-outlined text-primary text-[18px]">receipt_long</span>
            <span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Open positions</span>
          </div>
          <p v-if="!positions.length" class="text-sm text-slate-500">No open positions.</p>
          <div v-for="p in positions" :key="p.market_ticker" class="flex items-center justify-between text-sm py-2 border-b border-border-subtle/60 last:border-0">
            <span class="text-slate-300">{{ p.market_ticker }} <span class="text-slate-500">{{ p.side }} ×{{ p.contracts }}</span></span>
            <span class="font-bold tabular-nums" :class="(p.unrealized_cents || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'">
              {{ p.unrealized_cents == null ? '—' : (p.unrealized_cents >= 0 ? '+' : '') + '$' + (p.unrealized_cents / 100).toFixed(2) }}
            </span>
          </div>
        </div>

        <!-- CLV + Scan budget -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <div class="glass-card rounded-2xl p-4 sm:p-5">
            <div class="flex items-center gap-2 mb-3">
              <span class="material-symbols-outlined text-primary text-[18px]">trending_up</span>
              <span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">CLV scorecard</span>
            </div>
            <p v-if="!clvLeagues().length" class="text-sm text-slate-500">No CLV logged yet.</p>
            <div v-for="[lg, s] in clvLeagues()" :key="lg" class="flex items-center justify-between text-sm py-2 border-b border-border-subtle/60 last:border-0">
              <span class="text-slate-300">{{ lg }} <span class="text-slate-500">({{ s.n }})</span></span>
              <span class="font-bold tabular-nums" :class="s.avg_clv >= 0 ? 'text-emerald-400' : 'text-red-400'">{{ pct(s.avg_clv) }}</span>
            </div>
          </div>
          <div class="glass-card rounded-2xl p-4 sm:p-5">
            <div class="flex items-center gap-2 mb-3">
              <span class="material-symbols-outlined text-primary text-[18px]">speed</span>
              <span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Odds-budget guard</span>
            </div>
            <div v-if="scanBudget" class="text-sm text-slate-400">
              <div class="flex justify-between mb-1.5">
                <span>OddsPapi this month</span>
                <span class="text-slate-300 tabular-nums">{{ scanBudget.used }} / {{ scanBudget.limit }}</span>
              </div>
              <div class="h-2 rounded-full bg-surface overflow-hidden">
                <div class="h-full bg-primary rounded-full transition-all" :style="{ width: Math.min(100, (scanBudget.used / scanBudget.limit) * 100) + '%' }"></div>
              </div>
              <div class="mt-2 text-xs text-slate-500">~{{ scanBudget.fixtures_per_day }} fixtures/day affordable</div>
            </div>
            <p v-else class="text-sm text-slate-500">No budget data.</p>
          </div>
        </div>

        <!-- Recent fills -->
        <div class="glass-card rounded-2xl p-4 sm:p-5">
          <div class="flex items-center gap-2 mb-3">
            <span class="material-symbols-outlined text-primary text-[18px]">history</span>
            <span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Recent fills</span>
          </div>
          <p v-if="!fills.length" class="text-sm text-slate-500">No fills.</p>
          <div v-for="(f, i) in fills" :key="i" class="flex items-center justify-between text-sm py-2 border-b border-border-subtle/60 last:border-0">
            <span class="text-slate-300">
              <span class="font-semibold" :class="f.action === 'buy' ? 'text-emerald-400' : 'text-red-400'">{{ (f.action || '').toUpperCase() }}</span>
              {{ f.market_ticker }} <span class="text-slate-500">{{ f.side }} ×{{ f.contracts }} @ {{ f.price_cents }}¢</span>
            </span>
            <span class="text-slate-500 text-xs tabular-nums">{{ f.ts }}</span>
          </div>
        </div>
      </template>
    </main>
  </AppShell>
</template>
