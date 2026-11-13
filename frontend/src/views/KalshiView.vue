<script setup>
import { ref, computed, onMounted } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import KalshiPortfolioChart from '../components/kalshi/KalshiPortfolioChart.vue'
import KalshiCreateInstanceModal from '../components/kalshi/KalshiCreateInstanceModal.vue'
import InstanceLiveLogs from '../components/InstanceLiveLogs.vue'
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
const selectedInstanceId = ref('')
// The "active" instance drives the per-instance panels (logs/context); ALL
// instances are listed and individually manageable below.
const instance = computed(() =>
  instances.value.find((i) => i.id === selectedInstanceId.value) || instances.value[0] || null)
const hasInstance = computed(() => instances.value.length > 0)
const anyRunning = computed(() => instances.value.some((i) => i.running))

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
  // keep an active selection (prefer the running one) for the per-instance panels
  if (!instances.value.find((i) => i.id === selectedInstanceId.value)) {
    const run = instances.value.find((i) => i.running)
    selectedInstanceId.value = (run || instances.value[0] || {}).id || ''
  }
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

async function startStop(start, id) {
  if (!id || busy.value) return
  busy.value = true
  try {
    const res = await fetch(`${API_BASE}/instances/${id}/${start ? 'start' : 'stop'}`, { method: 'POST', headers: authHeaders() })
    if (!res.ok) {
      let msg = 'Action failed'
      try { msg = (await res.json()).error || msg } catch { /* ignore */ }
      alert(msg)   // e.g. backend's "one running instance per brokerage" guard
    }
    await loadInstances()
  } finally { busy.value = false }
}

async function removeInstance(inst) {
  if (!inst || busy.value) return
  if (!confirm(`Delete instance "${inst.name}"? This removes it permanently.`)) return
  busy.value = true
  try {
    await fetch(`${API_BASE}/instances/${inst.id}?force=true`, { method: 'DELETE', headers: authHeaders() })
    if (selectedInstanceId.value === inst.id) selectedInstanceId.value = ''
    await loadAll()
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
function posPick(p) { return (p.pick_label || p.side || '').replace(' to win', '') }
function posInitials(p) {
  const name = posPick(p) || p.market_ticker || ''
  return name.replace(/[^A-Za-z ]/g, '').split(' ').filter(Boolean).map((w) => w[0]).slice(0, 2).join('').toUpperCase()
}

onMounted(loadAccounts)
</script>

<template>
  <AppShell>
    <main class="ui-page">

      <!-- Header -->
      <header class="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <div class="min-w-0">
          <h1 class="ui-page-title">Prediction markets</h1>
          <p class="ui-page-sub">Kalshi · autonomous soccer event-contract trading</p>
        </div>

        <div class="flex items-center gap-2 flex-wrap">
          <!-- account selector -->
          <span v-if="accounts.length === 1" class="h-8 px-3 inline-flex items-center rounded-lg border border-white/[0.07] text-[13px] text-slate-300">
            {{ selected?.account_name }}
          </span>
          <div v-else-if="accounts.length" class="relative">
            <button type="button" @click="dropdownOpen = !dropdownOpen" class="ui-btn pr-2" :aria-expanded="dropdownOpen" aria-haspopup="true">
              <span class="max-w-[160px] truncate">{{ selected?.account_name }}</span>
              <span class="material-symbols-outlined text-[18px] text-slate-500 transition-transform duration-150 ease-out" :class="{ 'rotate-180': dropdownOpen }">expand_more</span>
            </button>
            <div v-if="dropdownOpen" @click="dropdownOpen = false" class="fixed inset-0 z-40"></div>
            <div v-if="dropdownOpen" class="absolute right-0 mt-2 w-60 z-50 rounded-xl border border-white/[0.08] bg-surface p-1.5 shadow-[0_16px_40px_rgba(0,0,0,0.5)]">
              <button v-for="a in accounts" :key="a.id" type="button" @click="pickAccount(a.id)"
                      class="w-full h-10 px-3 rounded-lg text-left text-sm flex items-center gap-2 transition-colors duration-150 ease-out"
                      :class="a.id === selectedId ? 'bg-raised text-slate-100 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]' : 'text-slate-300 hover:bg-white/[0.03]'">
                <span class="material-symbols-outlined text-[16px]" :class="a.id === selectedId ? 'text-slate-300' : 'text-transparent'">check</span>
                <span class="truncate">{{ a.account_name }}</span>
              </button>
            </div>
          </div>

          <!-- Lifecycle controls (only when an instance exists) -->
          <template v-if="hasInstance">
            <button @click="openCreate" class="ui-btn-primary h-8 px-3 text-[13px]">
              <span class="material-symbols-outlined text-[16px]">add</span> New instance
            </button>
            <button v-if="anyRunning" @click="kill" :disabled="busy" class="ui-btn text-loss">
              <span class="material-symbols-outlined text-[16px]">stop_circle</span> Kill
            </button>
          </template>
        </div>
      </header>

      <!-- Loading -->
      <div v-if="loadingAcct" class="mt-8 space-y-3" aria-hidden="true">
        <div class="h-[120px] rounded-xl bg-surface"></div>
        <div class="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div class="h-[300px] rounded-xl bg-surface"></div>
          <div class="h-[300px] rounded-xl bg-surface"></div>
        </div>
      </div>

      <!-- No Kalshi account -->
      <div v-else-if="!accounts.length" class="mt-8 ui-card ui-empty">
        <p>No Kalshi account linked. Link a demo or live Kalshi brokerage to create a trading instance here.</p>
        <RouterLink to="/brokerages" class="ui-btn-primary">Link a brokerage</RouterLink>
      </div>

      <!-- Account but NO instance -> create CTA -->
      <div v-else-if="!hasInstance" class="mt-8 ui-card ui-empty">
        <div class="max-w-md space-y-2">
          <p class="text-base text-slate-200">No trading instance yet</p>
          <p>
            Create a Kalshi trading instance for <span class="text-slate-200">{{ selected?.account_name }}</span> to
            start scanning soccer markets, flagging edge and, once started, trading.
          </p>
          <p v-if="isLive" class="inline-flex items-center gap-2 text-xs text-slate-300">
            <span class="ui-dot ui-dot-warn"></span>Live account: once started, the instance trades real money
          </p>
        </div>
        <button @click="openCreate" class="ui-btn-primary">
          <span class="material-symbols-outlined text-[16px]">add</span> Create Kalshi instance
        </button>
      </div>

      <!-- Instance exists -> data -->
      <template v-else>
        <!-- All instances — manage each (start/stop/details/delete). Only ONE may run
             per brokerage, so Start is disabled on the others while one is running. -->
        <section class="mt-8" aria-labelledby="kx-instances">
          <div class="mb-3">
            <h2 id="kx-instances" class="ui-section-title">Instances</h2>
            <p class="ui-section-sub">One may run per brokerage. Select one to follow its logs</p>
          </div>
          <div class="ui-card overflow-hidden">
            <div class="kx-inst-grid hidden sm:grid ui-th">
              <span>Instance</span><span>Status</span><span></span>
            </div>
            <div class="ui-rows">
              <div v-for="inst in instances" :key="inst.id"
                   @click="selectedInstanceId = inst.id"
                   class="kx-inst-grid grid items-center min-h-14 px-5 py-2 cursor-pointer transition-colors duration-150 ease-out"
                   :class="instance && inst.id === instance.id ? 'bg-raised shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]' : 'hover:bg-white/[0.03]'">
                <span class="min-w-0">
                  <span class="block text-sm text-slate-100 truncate">{{ inst.name }}</span>
                  <span class="block text-xs mt-0.5" :class="inst.live_enabled ? 'text-slate-300' : 'text-slate-500'">{{ inst.live_enabled ? 'Live · real money' : 'Paper' }}</span>
                </span>
                <span class="inline-flex items-center gap-2 text-sm" :class="inst.running ? 'text-slate-200' : 'text-slate-500'">
                  <span class="ui-dot" :class="inst.running ? 'ui-dot-live' : 'ui-dot-off'"></span>{{ inst.running ? 'Running' : 'Stopped' }}
                </span>
                <div class="kx-inst-actions flex items-center justify-end gap-1" @click.stop>
                  <button @click="startStop(!inst.running, inst.id)"
                          :disabled="busy || (!inst.running && anyRunning)"
                          :title="(!inst.running && anyRunning) ? 'Stop the running instance first — only one may run per brokerage' : ''"
                          class="ui-btn" :class="inst.running ? 'text-loss' : ''">
                    <span class="material-symbols-outlined text-[16px]">{{ inst.running ? 'pause' : 'play_arrow' }}</span>{{ inst.running ? 'Stop' : 'Start' }}
                  </button>
                  <button @click="$router.push(`/kalshi/instances/${inst.id}`)" class="ui-btn-ghost">
                    Details <span class="material-symbols-outlined text-[16px]">arrow_forward</span>
                  </button>
                  <button @click="removeInstance(inst)" :disabled="busy"
                          class="ui-icon-btn hover:text-loss disabled:opacity-40"
                          title="Delete instance" aria-label="Delete instance">
                    <span class="material-symbols-outlined text-[18px]">delete</span>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- Chart + summary -->
        <section class="mt-12 grid grid-cols-1 gap-8 items-start lg:grid-cols-[minmax(0,1fr)_340px]" aria-label="Portfolio">
          <KalshiPortfolioChart :brokerage-id="selectedId" />
          <div class="ui-card px-5 py-5">
            <p class="text-sm font-medium text-slate-200">Account summary</p>
            <div class="mt-4 grid grid-cols-2 gap-x-4 gap-y-5">
              <div>
                <p class="text-xs text-slate-500">Avg CLV</p>
                <p class="mt-1 text-xl font-medium text-slate-100 tabular-nums">{{ pct(clv.overall.avg_clv) }}</p>
              </div>
              <div>
                <p class="text-xs text-slate-500">Fixtures</p>
                <p class="mt-1 text-xl font-medium text-slate-100 tabular-nums">{{ clv.overall.n }}</p>
              </div>
              <div>
                <p class="text-xs text-slate-500">Open</p>
                <p class="mt-1 text-xl font-medium text-slate-100 tabular-nums">{{ positions.length }}</p>
              </div>
              <div>
                <p class="text-xs text-slate-500">Top edge</p>
                <p class="mt-1 text-xl font-medium tabular-nums" :class="edges.length ? 'text-gain' : 'text-slate-100'">{{ edges.length ? pct(edges[0].edge) : '—' }}</p>
              </div>
            </div>
          </div>
        </section>

        <!-- Edge radar -->
        <section class="mt-12" aria-labelledby="kx-edges">
          <div class="mb-3">
            <h2 id="kx-edges" class="ui-section-title">Edge radar</h2>
            <p class="ui-section-sub">+EV contracts flagged on the last scan</p>
          </div>
          <div class="ui-card overflow-hidden">
            <p v-if="!edges.length" class="ui-empty">No +EV contracts flagged right now</p>
            <div v-else class="ui-rows">
              <div v-for="e in edges" :key="e.market_ticker" class="ui-row flex items-center justify-between gap-4">
                <span class="min-w-0 truncate text-sm text-slate-100">{{ e.market_ticker }} <span class="text-slate-500">· {{ e.side }}</span></span>
                <span class="text-sm text-gain tabular-nums">+{{ pct(e.edge) }}</span>
              </div>
            </div>
          </div>
        </section>

        <!-- Open positions -->
        <section class="mt-12" aria-labelledby="kx-positions">
          <div class="mb-3">
            <h2 id="kx-positions" class="ui-section-title">Open positions</h2>
            <p class="ui-section-sub tabular-nums">{{ positions.length }} position{{ positions.length === 1 ? '' : 's' }}</p>
          </div>
          <div class="ui-card overflow-hidden">
            <p v-if="!positions.length" class="ui-empty">No open positions</p>
            <template v-else>
              <div class="kx-pos-grid hidden sm:grid ui-th">
                <span>Market</span>
                <span class="hidden md:block text-right">Contracts</span>
                <span class="hidden md:block text-right">Bought at</span>
                <span class="text-right">Max payout</span>
                <span class="text-right">Value</span>
              </div>
              <ul class="ui-rows">
                <li v-for="p in positions" :key="p.market_ticker" class="kx-pos-grid grid items-center min-h-14 px-5 py-2">
                  <span class="flex items-center gap-3 min-w-0">
                    <img v-if="p.pick_logo" :src="p.pick_logo" class="size-8 rounded-full object-contain bg-raised shrink-0" @error="(e) => e.target.style.display='none'" />
                    <span v-else class="size-8 rounded-full bg-raised flex items-center justify-center text-[11px] font-medium text-slate-400 shrink-0">{{ posInitials(p) }}</span>
                    <span class="min-w-0">
                      <span class="block text-sm text-slate-100 truncate">{{ p.match || p.market_ticker }}</span>
                      <span class="block text-xs text-slate-500 mt-0.5 truncate">Yes · {{ posPick(p) }}</span>
                    </span>
                  </span>
                  <span class="hidden md:block text-right text-sm text-slate-300 tabular-nums">{{ p.contracts }}×</span>
                  <span class="hidden md:block text-right text-sm text-slate-300 tabular-nums">{{ p.odds_pct == null ? '—' : p.odds_pct.toFixed(0) + '%' }}</span>
                  <span class="hidden sm:block text-right text-sm text-slate-300 tabular-nums">${{ (p.max_payout || 0).toFixed(0) }}</span>
                  <span class="text-right tabular-nums">
                    <span class="block text-sm text-slate-100">{{ p.current_value == null ? '—' : '$' + p.current_value.toFixed(2) }}</span>
                    <span class="block text-xs mt-0.5" :class="(p.unrealized_cents || 0) >= 0 ? 'text-gain' : 'text-loss'">{{ p.unrealized_cents == null ? '' : (p.unrealized_cents >= 0 ? '+' : '-') + '$' + Math.abs(p.unrealized_cents / 100).toFixed(2) }}</span>
                  </span>
                </li>
              </ul>
            </template>
          </div>
        </section>

        <div class="mt-12 grid grid-cols-1 gap-x-8 gap-y-12 lg:grid-cols-2">
          <section aria-labelledby="kx-clv">
            <div class="mb-3">
              <h2 id="kx-clv" class="ui-section-title">CLV scorecard</h2>
              <p class="ui-section-sub">Closing-line value by league</p>
            </div>
            <div class="ui-card overflow-hidden">
              <p v-if="!clvLeagues().length" class="ui-empty">No CLV logged yet</p>
              <div v-else class="ui-rows">
                <div v-for="[lg, s] in clvLeagues()" :key="lg" class="ui-row flex items-center justify-between gap-4">
                  <span class="text-sm text-slate-100">{{ lg }} <span class="text-slate-500 tabular-nums">({{ s.n }})</span></span>
                  <span class="text-sm tabular-nums" :class="s.avg_clv >= 0 ? 'text-gain' : 'text-loss'">{{ pct(s.avg_clv) }}</span>
                </div>
              </div>
            </div>
          </section>
          <section aria-labelledby="kx-budget">
            <div class="mb-3">
              <h2 id="kx-budget" class="ui-section-title">Odds-budget guard</h2>
              <p class="ui-section-sub">OddsPapi calls against the monthly limit</p>
            </div>
            <div class="ui-card px-5 py-5">
              <div v-if="scanBudget">
                <div class="flex items-baseline justify-between gap-4">
                  <span class="text-sm text-slate-400">OddsPapi this month</span>
                  <span class="text-sm text-slate-100 tabular-nums">{{ scanBudget.used }} / {{ scanBudget.limit }}</span>
                </div>
                <div class="mt-3 h-1 rounded-full bg-raised overflow-hidden"><div class="h-full bg-slate-300 rounded-full" :style="{ width: Math.min(100, (scanBudget.used / scanBudget.limit) * 100) + '%' }"></div></div>
                <p class="mt-3 text-xs text-slate-500 tabular-nums">~{{ scanBudget.fixtures_per_day }} fixtures/day affordable</p>
              </div>
              <p v-else class="text-sm text-slate-500">No budget data</p>
            </div>
          </section>
        </div>

        <!-- Live logs (streams the engine's per-instance log) -->
        <section v-if="instance" class="mt-12" aria-labelledby="kx-logs">
          <div class="mb-3">
            <h2 id="kx-logs" class="ui-section-title">Live logs</h2>
            <p class="ui-section-sub">{{ instance.name }}</p>
          </div>
          <InstanceLiveLogs :key="instance.id" :instance-id="instance.id" />
        </section>
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

<style scoped>
.kx-inst-grid {
  grid-template-columns: minmax(0, 1fr) auto;
  column-gap: 16px;
  row-gap: 8px;
}
.kx-inst-grid > .kx-inst-actions { grid-column: 1 / -1; justify-content: flex-start; }
@media (min-width: 640px) {
  .kx-inst-grid { grid-template-columns: minmax(0, 1fr) 112px auto; }
  .kx-inst-grid > .kx-inst-actions { grid-column: auto; justify-content: flex-end; }
}
/* Hidden cells take no track: phone market · value; sm adds payout; md all. */
.kx-pos-grid {
  grid-template-columns: minmax(0, 1fr) 96px;
  column-gap: 16px;
}
@media (min-width: 640px) {
  .kx-pos-grid { grid-template-columns: minmax(0, 1fr) 96px 112px; }
}
@media (min-width: 768px) {
  .kx-pos-grid { grid-template-columns: minmax(0, 1fr) 80px 80px 96px 112px; }
}
</style>
