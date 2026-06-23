<script setup>
import { ref, computed, onMounted } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import KalshiPortfolioChart from '../components/kalshi/KalshiPortfolioChart.vue'
import KalshiCreateInstanceModal from '../components/kalshi/KalshiCreateInstanceModal.vue'
import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')
function authHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

const accounts = ref([])
const selectedId = ref('')
const instances = ref([])
const edges = ref([])
const positions = ref([])
const clv = ref({ overall: { avg_clv: 0, n: 0 }, by_league: {} })
const fills = ref([])
const scanBudget = ref(null)
const loadingAcct = ref(true)
const busy = ref(false)
const dropdownOpen = ref(false)

const selected = computed(() => accounts.value.find((a) => a.id === selectedId.value) || null)
const isLive = computed(() => (selected.value?.kalshi_environment || 'demo') === 'live')
const instance = computed(() => instances.value[0] || null)
const hasInstance = computed(() => !!instance.value)
const running = computed(() => !!instance.value?.running)

async function getJson(path) {
  try {
    const res = await fetch(`${API_BASE}/brokerages/${selectedId.value}${path}`, { headers: authHeaders() })
    if (!res.ok) return null
    return await res.json()
  } catch { return null }
}

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

async function loadInstances() {
  const d = await getJson('/kalshi/instances')
  instances.value = (d && d.instances) || []
}

async function loadAll() {
  if (!selectedId.value) return
  await loadInstances()
  if (!hasInstance.value) return  // no bot yet -> nothing to load
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

function pickAccount(id) { selectedId.value = id; dropdownOpen.value = false; loadAll() }

async function startStop(start) {
  if (!instance.value || busy.value) return
  busy.value = true
  try {
    await fetch(`${API_BASE}/instances/${instance.value.id}/${start ? 'start' : 'stop'}`, { method: 'POST', headers: authHeaders() })
    await loadInstances()
  } finally { busy.value = false }
}

async function kill() {
  if (!selectedId.value || busy.value) return
  if (!confirm('Stop the Kalshi instance and cancel all resting orders on this account?')) return
  busy.value = true
  try {
    await fetch(`${API_BASE}/brokerages/${selectedId.value}/kalshi/kill`, { method: 'POST', headers: authHeaders() })
    await loadAll()
  } finally { busy.value = false }
}

// ── Create modal ──
const showCreate = ref(false)
function openCreate() { showCreate.value = true }
function onCreated(bid) {
  showCreate.value = false
  if (bid) selectedId.value = bid
  loadAll()
}

function pct(v) { return `${(v * 100).toFixed(1)}%` }
function clvLeagues() { return Object.entries(clv.value.by_league || {}) }

onMounted(loadAccounts)
</script>

<template>
  <AppShell>
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10 space-y-6">

      <!-- Header -->
      <div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p class="text-primary text-xs font-bold uppercase tracking-widest mb-2 flex items-center gap-1.5">
            <span class="material-symbols-outlined text-[16px]">sports_soccer</span> Kalshi
          </p>
          <h1 class="text-2xl sm:text-3xl font-bold leading-tight text-slate-100">Prediction markets</h1>
          <p class="text-slate-500 mt-1.5 text-sm">Autonomous soccer event-contract trading.</p>
        </div>

        <div class="flex items-center gap-2 flex-wrap">
          <!-- account selector -->
          <div v-if="accounts.length === 1" class="flex items-center gap-2 bg-surface border border-border-subtle rounded-lg text-sm text-slate-300 px-3 py-2">
            <span class="material-symbols-outlined text-primary text-[16px]">account_circle</span>{{ selected?.account_name }}
          </div>
          <div v-else-if="accounts.length" class="relative">
            <button @click="dropdownOpen = !dropdownOpen" class="flex items-center gap-2 bg-surface border border-border-subtle rounded-lg text-sm text-slate-200 pl-3 pr-2 py-2 hover:border-primary/50 transition-colors">
              <span class="material-symbols-outlined text-primary text-[16px]">account_circle</span>
              <span class="max-w-[150px] truncate">{{ selected?.account_name }}</span>
              <span class="material-symbols-outlined text-slate-500 text-[18px] transition-transform" :class="{ 'rotate-180': dropdownOpen }">expand_more</span>
            </button>
            <div v-if="dropdownOpen" @click="dropdownOpen = false" class="fixed inset-0 z-40"></div>
            <div v-if="dropdownOpen" class="absolute right-0 mt-1.5 w-60 z-50 glass-card rounded-xl overflow-hidden py-1 shadow-xl shadow-black/40">
              <button v-for="a in accounts" :key="a.id" @click="pickAccount(a.id)" class="w-full text-left px-3 py-2.5 text-sm flex items-center gap-2 hover:bg-primary/10 transition-colors" :class="a.id === selectedId ? 'text-primary' : 'text-slate-300'">
                <span class="material-symbols-outlined text-[16px]" :class="a.id === selectedId ? 'text-primary' : 'text-transparent'">check</span>{{ a.account_name }}
              </button>
            </div>
          </div>

          <!-- Lifecycle controls (only when an instance exists) -->
          <template v-if="hasInstance">
            <button @click="startStop(!running)" :disabled="busy"
                    class="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold transition-all disabled:opacity-50"
                    :class="running ? 'border border-amber-500/40 text-amber-400 hover:bg-amber-500/10' : 'bg-primary text-background-dark hover:brightness-110'">
              <span class="material-symbols-outlined text-[18px]">{{ running ? 'pause' : 'play_arrow' }}</span>
              {{ running ? 'Stop' : 'Start' }}
            </button>
            <button v-if="running" @click="kill" :disabled="busy"
                    class="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-red-500/40 text-red-400 text-sm font-semibold hover:bg-red-500/10 transition-all disabled:opacity-50">
              <span class="material-symbols-outlined text-[18px]">stop_circle</span> Kill
            </button>
          </template>
        </div>
      </div>

      <!-- Loading -->
      <div v-if="loadingAcct" class="glass-card rounded-2xl h-[200px] animate-pulse"></div>

      <!-- No Kalshi account -->
      <div v-else-if="!accounts.length"
           class="rounded-2xl border border-border-subtle bg-surface/20 px-8 py-12 flex flex-col items-center gap-3 text-center">
        <span class="material-symbols-outlined text-4xl text-slate-700">sports_soccer</span>
        <p class="text-slate-400 text-sm font-medium">No Kalshi account linked.</p>
        <p class="text-slate-600 text-xs max-w-sm">Link a Kalshi brokerage (demo or live) to create a trading instance here.</p>
        <RouterLink to="/brokerages" class="mt-1 px-4 py-2 rounded-lg bg-primary text-background-dark text-xs font-bold hover:brightness-110 transition-all">Link a brokerage</RouterLink>
      </div>

      <!-- Account but NO instance -> create CTA -->
      <div v-else-if="!hasInstance"
           class="rounded-2xl border border-border-subtle bg-surface/20 px-8 py-14 flex flex-col items-center gap-3 text-center">
        <span class="material-symbols-outlined text-5xl text-primary/70">smart_toy</span>
        <p class="text-slate-200 text-base font-semibold">No trading instance yet</p>
        <p class="text-slate-500 text-sm max-w-md">
          Create a Kalshi trading instance for <span class="text-slate-300">{{ selected?.account_name }}</span> to
          start scanning soccer markets, flagging edge, and (when started) trading.
        </p>
        <p v-if="isLive" class="text-amber-400/90 text-xs max-w-md">⚠ This is a LIVE account — once you start the instance it trades real money.</p>
        <button @click="openCreate" class="mt-2 flex items-center gap-1.5 px-5 py-2.5 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 transition-all">
          <span class="material-symbols-outlined text-[18px]">add</span> Create Kalshi instance
        </button>
      </div>

      <!-- Instance exists -> data -->
      <template v-else>
        <!-- instance status strip -->
        <div class="glass-card rounded-2xl px-4 py-3 flex items-center gap-3 flex-wrap">
          <span class="size-2 rounded-full" :class="running ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'"></span>
          <span class="text-sm font-semibold text-slate-200">{{ instance.name }}</span>
          <span class="text-xs font-medium px-2 py-0.5 rounded-md" :class="running ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-500/15 text-slate-400'">{{ running ? 'Running' : 'Stopped' }}</span>
          <span v-if="instance.live_enabled" class="text-xs font-medium px-2 py-0.5 rounded-md bg-red-500/15 text-red-400">Live · real money</span>
          <span v-else class="text-xs font-medium px-2 py-0.5 rounded-md bg-primary/15 text-primary">Paper</span>
        </div>

        <!-- Chart + summary -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <KalshiPortfolioChart :brokerage-id="selectedId" />
          <div class="glass-card rounded-2xl p-4 sm:p-5">
            <div class="flex items-center gap-2 mb-4">
              <span class="material-symbols-outlined text-primary text-[18px]">account_balance_wallet</span>
              <span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Account summary</span>
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3"><div class="text-lg font-bold text-slate-100 tabular-nums">{{ pct(clv.overall.avg_clv) }}</div><div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Avg CLV</div></div>
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3"><div class="text-lg font-bold text-slate-100 tabular-nums">{{ clv.overall.n }}</div><div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Fixtures</div></div>
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3"><div class="text-lg font-bold text-slate-100 tabular-nums">{{ positions.length }}</div><div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Open</div></div>
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3"><div class="text-lg font-bold tabular-nums" :class="edges.length ? 'text-emerald-400' : 'text-slate-100'">{{ edges.length ? pct(edges[0].edge) : '—' }}</div><div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Top edge</div></div>
            </div>
          </div>
        </div>

        <div class="glass-card rounded-2xl p-4 sm:p-5">
          <div class="flex items-center gap-2 mb-3"><span class="material-symbols-outlined text-primary text-[18px]">bolt</span><span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Edge Radar</span></div>
          <p v-if="!edges.length" class="text-sm text-slate-500">No +EV contracts flagged right now.</p>
          <div v-for="e in edges" :key="e.market_ticker" class="flex items-center justify-between text-sm py-2 border-b border-border-subtle/60 last:border-0"><span class="text-slate-300">{{ e.market_ticker }} <span class="text-slate-500">· {{ e.side }}</span></span><span class="text-emerald-400 font-bold tabular-nums">+{{ pct(e.edge) }}</span></div>
        </div>

        <div class="glass-card rounded-2xl p-4 sm:p-5">
          <div class="flex items-center gap-2 mb-3"><span class="material-symbols-outlined text-primary text-[18px]">receipt_long</span><span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Open positions</span></div>
          <p v-if="!positions.length" class="text-sm text-slate-500">No open positions.</p>
          <div v-for="p in positions" :key="p.market_ticker" class="flex items-center justify-between text-sm py-2 border-b border-border-subtle/60 last:border-0"><span class="text-slate-300">{{ p.market_ticker }} <span class="text-slate-500">{{ p.side }} ×{{ p.contracts }}</span></span><span class="font-bold tabular-nums" :class="(p.unrealized_cents || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'">{{ p.unrealized_cents == null ? '—' : (p.unrealized_cents >= 0 ? '+' : '') + '$' + (p.unrealized_cents / 100).toFixed(2) }}</span></div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <div class="glass-card rounded-2xl p-4 sm:p-5">
            <div class="flex items-center gap-2 mb-3"><span class="material-symbols-outlined text-primary text-[18px]">trending_up</span><span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">CLV scorecard</span></div>
            <p v-if="!clvLeagues().length" class="text-sm text-slate-500">No CLV logged yet.</p>
            <div v-for="[lg, s] in clvLeagues()" :key="lg" class="flex items-center justify-between text-sm py-2 border-b border-border-subtle/60 last:border-0"><span class="text-slate-300">{{ lg }} <span class="text-slate-500">({{ s.n }})</span></span><span class="font-bold tabular-nums" :class="s.avg_clv >= 0 ? 'text-emerald-400' : 'text-red-400'">{{ pct(s.avg_clv) }}</span></div>
          </div>
          <div class="glass-card rounded-2xl p-4 sm:p-5">
            <div class="flex items-center gap-2 mb-3"><span class="material-symbols-outlined text-primary text-[18px]">speed</span><span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Odds-budget guard</span></div>
            <div v-if="scanBudget" class="text-sm text-slate-400">
              <div class="flex justify-between mb-1.5"><span>OddsPapi this month</span><span class="text-slate-300 tabular-nums">{{ scanBudget.used }} / {{ scanBudget.limit }}</span></div>
              <div class="h-2 rounded-full bg-surface overflow-hidden"><div class="h-full bg-primary rounded-full" :style="{ width: Math.min(100, (scanBudget.used / scanBudget.limit) * 100) + '%' }"></div></div>
              <div class="mt-2 text-xs text-slate-500">~{{ scanBudget.fixtures_per_day }} fixtures/day affordable</div>
            </div>
            <p v-else class="text-sm text-slate-500">No budget data.</p>
          </div>
        </div>
      </template>
    </main>

    <!-- Create instance modal -->
    <KalshiCreateInstanceModal
      v-if="showCreate"
      :brokerages="accounts"
      :initial-brokerage-id="selectedId"
      @close="showCreate = false"
      @created="onCreated"
    />
  </AppShell>
</template>
