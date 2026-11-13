<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import CryptoCreateInstanceModal from '../components/crypto/CryptoCreateInstanceModal.vue'
import CryptoBacktestModal from '../components/crypto/CryptoBacktestModal.vue'
import CryptoAllocationChart from '../components/crypto/CryptoAllocationChart.vue'
import PortfolioChart from '../components/PortfolioChart.vue'
import { getToken } from '../utils/auth.js'

// Detail view for a crypto (kind='crypto') instance — mirrors the equity
// InstanceDetailView layout (info cards + a Backtests section) with crypto
// specifics (band cadence, fixed+dynamic allocation, 24/7).
const route = useRoute()
const router = useRouter()
const instanceId = computed(() => String(route.params.id || ''))

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')
function authHeaders() {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

const inst = ref(null)
const brokerages = ref([])
const accountValue = ref(null)
const loading = ref(true)
const error = ref('')
const busy = ref(false)

// ── Derived instance fields ─────────────────────────────────────────────────────
const running = computed(() => !!(inst.value?.runCommand ?? inst.value?.run_command))
const crashed = computed(() => !!inst.value?.crashed)
const cfg = computed(() => inst.value?.crypto_config || {})
const bandLabel = computed(() => {
  const b = String(cfg.value.band || '').toLowerCase()
  return b ? b.charAt(0).toUpperCase() + b.slice(1) : '—'
})
const CADENCE = { high: '~5 min', medium: '~15 min', low: '~60 min' }
const cadence = computed(() => CADENCE[String(cfg.value.band || '').toLowerCase()] || '~15 min')
const strategyLabel = computed(() => {
  const s = String(cfg.value.strategy || '').toLowerCase()
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '—'
})

const brokerage = computed(() => brokerages.value.find((b) => b.id === inst.value?.brokerage_id) || inst.value?.brokerage || null)
const isPaper = computed(() => !!(brokerage.value?.alpaca_paper))

// ── Allocation ──────────────────────────────────────────────────────────────────
// Neutral steps (shares, not gains or losses), same as the create modal.
const PALETTE = ['#ededed', '#c4c4c8', '#a1a1a6', '#85858b', '#6b6b70', '#55555a']
const allocations = computed(() => (Array.isArray(cfg.value.allocations) ? cfg.value.allocations : []))
const coins = computed(() =>
  allocations.value.map((a, i) => ({
    ticker: String(a.symbol || '').split('/')[0],
    pct: Math.round((Number(a.pct) || 0) * 100),
    color: PALETTE[i % PALETTE.length],
  })),
)
const fixedPct = computed(() => allocations.value.reduce((s, a) => s + (Number(a.pct) || 0), 0) * 100)
const dynamicPct = computed(() => Math.max(0, Math.round(100 - fixedPct.value)))
const chartAllocs = computed(() => coins.value.filter((c) => c.pct > 0).map((c) => ({ symbol: c.ticker, pct: c.pct, color: c.color })))

// ── Uptime ticker ────────────────────────────────────────────────────────────────
const liveSecs = ref(0)
let uptimeTimer = null
function startUptime() {
  clearInterval(uptimeTimer)
  if (running.value) uptimeTimer = setInterval(() => { liveSecs.value++ }, 1000)
}
function fmtDuration(s) {
  s = Math.max(0, Math.floor(Number(s) || 0))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60
  return `${h}h ${m}m ${sec}s`
}
function fmtUsd(n) { return `$${Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}` }

// ── Backtests ─────────────────────────────────────────────────────────────────────
const backtests = ref([])
const btTotal = ref(0)
const btLoading = ref(false)
let btPollTimer = null

async function fetchInstance() {
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId.value)}`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    inst.value = await res.json()
    // The detail endpoint omits crypto_config; backfill it (band + allocations)
    // from the list endpoint, which includes it.
    if (!inst.value.crypto_config) {
      try {
        const lr = await fetch(`${API_BASE}/instances`, { headers: authHeaders() })
        if (lr.ok) {
          const ld = await lr.json()
          const m = (ld.instances || []).find((i) => String(i.id) === instanceId.value)
          if (m) {
            inst.value.crypto_config = m.crypto_config
            if (!(inst.value.stocks || []).length && m.stocks) inst.value.stocks = m.stocks
            if (inst.value.brokerage_id == null) inst.value.brokerage_id = m.brokerage_id
          }
        }
      } catch { /* best-effort backfill */ }
    }
    liveSecs.value = inst.value.uptime_seconds || 0
    startUptime()
  } catch (e) { error.value = `Failed to load instance: ${e.message}` }
}

async function fetchBrokerages() {
  try {
    const res = await fetch(`${API_BASE}/brokerages`, { headers: authHeaders() })
    if (!res.ok) return
    const d = await res.json()
    brokerages.value = (d.accounts || []).filter((a) => a.brokerage_type === 'alpaca')
  } catch { /* non-critical */ }
}

async function fetchValue() {
  const bid = inst.value?.brokerage_id
  if (!bid) return
  try {
    const res = await fetch(`${API_BASE}/brokerages/${bid}/portfolio-history?range=1D`, { headers: authHeaders() })
    if (!res.ok) return
    const d = await res.json()
    const v = Number(d?.current_value)
    if (Number.isFinite(v)) accountValue.value = v
  } catch { /* best-effort */ }
}

async function fetchBacktests() {
  btLoading.value = true
  try {
    const params = new URLSearchParams({ page: '1', per_page: '20', sort_by: 'completed_at', sort_order: 'desc' })
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId.value)}/backtests?${params.toString()}`, { headers: authHeaders() })
    if (!res.ok) return
    const d = await res.json()
    backtests.value = d.backtests || []
    btTotal.value = d.total ?? backtests.value.length
  } catch { /* non-critical */ } finally { btLoading.value = false }
}

function anyRunning() {
  return backtests.value.some((b) => ['running', 'queued', 'pending', 'paused'].includes(String(b.status || '').toLowerCase()))
}
function startBtPolling() {
  clearInterval(btPollTimer)
  btPollTimer = setInterval(() => { if (anyRunning()) fetchBacktests() }, 3000)
}

async function refresh() {
  await Promise.all([fetchInstance(), fetchBacktests()])
  fetchValue()
}

onMounted(async () => {
  loading.value = true
  await Promise.all([fetchInstance(), fetchBrokerages()])
  await fetchBacktests()
  fetchValue()
  startBtPolling()
  loading.value = false
})
onUnmounted(() => { clearInterval(uptimeTimer); clearInterval(btPollTimer) })

// ── Actions ────────────────────────────────────────────────────────────────────
async function toggleRun() {
  busy.value = true
  const action = running.value ? 'stop' : 'start'
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId.value)}/${action}`, { method: 'POST', headers: authHeaders() })
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${res.status}`) }
    await fetchInstance()
  } catch (e) { alert(`${action} failed: ${e.message}`) } finally { busy.value = false }
}

// ── Modals ────────────────────────────────────────────────────────────────────
const showEdit = ref(false)
const showBacktest = ref(false)
function onEditSaved() { showEdit.value = false; refresh() }
function onBacktestCreated() { showBacktest.value = false; fetchBacktests() }

// ── Backtest row formatting ─────────────────────────────────────────────────────
function btCoins(b) { return (b.stocks || []).map((s) => String(s).split('/')[0]) }
function btCoinsLabel(b) { const c = btCoins(b); return c.length ? c.slice(0, 4).join(', ') + (c.length > 4 ? ` +${c.length - 4}` : '') : 'Dynamic' }
// Status as a dot and a word: green only while a run is actually running.
function statusStyle(s) {
  const v = String(s || '').toLowerCase()
  if (['finished', 'completed', 'done'].includes(v)) return 'text-slate-300'
  if (['running', 'queued', 'pending'].includes(v)) return 'text-slate-200'
  if (['error', 'failed'].includes(v)) return 'text-loss'
  if (v === 'paused') return 'text-slate-300'
  return 'text-slate-500'
}
function statusDot(s) {
  const v = String(s || '').toLowerCase()
  if (v === 'running') return 'ui-dot-live'
  if (['queued', 'pending', 'paused'].includes(v)) return 'ui-dot-warn'
  if (['error', 'failed'].includes(v)) return 'ui-dot-error'
  return 'ui-dot-off'
}
function pnlColor(n) { const v = Number(n); return n == null ? 'text-slate-500' : v > 0 ? 'text-gain' : v < 0 ? 'text-loss' : 'text-slate-400' }
function fmtPnl(n) { const v = Number(n); if (n == null || !Number.isFinite(v)) return '—'; return `${v >= 0 ? '+' : '-'}${fmtUsd(Math.abs(v))}` }
function fmtPct(n) { const v = Number(n); if (n == null || !Number.isFinite(v)) return '—'; return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` }
function fmtDate(s) { if (!s) return '—'; const d = new Date(s); return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }) }
</script>

<template>
  <AppShell>
    <main class="ui-page">
      <!-- Breadcrumb -->
      <div class="flex items-center gap-2 text-sm text-slate-500">
        <RouterLink to="/crypto" class="inline-flex items-center gap-1 h-8 -ml-2 px-2 rounded-md text-slate-400 hover:text-slate-100 hover:bg-raised transition-colors duration-150 ease-out">
          <span class="material-symbols-outlined text-[16px]">arrow_back</span> Crypto
        </RouterLink>
        <span class="text-slate-600">/</span>
        <span class="text-slate-300 truncate">{{ inst?.name || instanceId }}</span>
      </div>

      <div v-if="loading" class="mt-4 space-y-3" aria-hidden="true">
        <div class="h-7 w-56 ui-skel"></div>
        <div class="h-4 w-40 ui-skel"></div>
        <div class="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div class="h-[360px] rounded-xl bg-surface"></div>
          <div class="h-[360px] rounded-xl bg-surface"></div>
        </div>
      </div>
      <div v-else-if="error" class="mt-6 ui-error">{{ error }}</div>

      <template v-else-if="inst">
        <!-- Header -->
        <header class="mt-4 flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
          <div class="min-w-0">
            <h1 class="ui-page-title truncate">{{ inst.name || inst.id }}</h1>
            <p class="ui-page-sub flex flex-wrap items-center gap-x-2 gap-y-1">
              <span class="inline-flex items-center gap-1.5" :class="crashed ? 'text-loss' : running ? 'text-slate-300' : ''">
                <span class="ui-dot" :class="crashed ? 'ui-dot-error' : running ? 'ui-dot-live' : 'ui-dot-off'"></span>
                {{ crashed ? 'Crashed' : running ? 'Running' : 'Stopped' }}
              </span>
              <span class="text-slate-600" aria-hidden="true">·</span>
              <span>24/7 crypto · {{ strategyLabel }} strategy</span>
            </p>
          </div>

          <div class="flex items-center gap-2 flex-wrap">
            <button @click="toggleRun" :disabled="busy"
                    :class="running ? 'ui-btn text-loss' : 'ui-btn-primary h-8 px-3 text-[13px]'">
              <span class="material-symbols-outlined text-[16px]">{{ running ? 'stop' : 'play_arrow' }}</span>
              {{ running ? 'Stop' : 'Start' }}
            </button>
            <button @click="showEdit = true" class="ui-btn">
              <span class="material-symbols-outlined text-[16px]">tune</span> Edit
            </button>
            <button @click="refresh" title="Refresh" aria-label="Refresh" class="ui-icon-btn">
              <span class="material-symbols-outlined text-[18px]">refresh</span>
            </button>
          </div>
        </header>

        <!-- Hero: the account's equity, with the allocation beside it -->
        <section class="mt-8 grid grid-cols-1 gap-x-8 gap-y-12 items-start lg:grid-cols-[minmax(0,1fr)_340px]" aria-label="Account and allocation">
          <PortfolioChart v-if="brokerage && brokerage.id" :key="brokerage.id" :account="brokerage" variant="hero" />
          <div v-else>
            <p class="text-sm text-slate-400">Account value</p>
            <p class="mt-4 text-[44px] font-medium text-slate-100 tabular-nums leading-none tracking-[-0.02em]">{{ accountValue != null ? fmtUsd(accountValue) : '—' }}</p>
          </div>

          <div>
            <div class="mb-3">
              <h2 class="ui-section-title">Allocation</h2>
              <p class="ui-section-sub">Fixed weights, the rest dynamic</p>
            </div>
            <div class="ui-card divide-y divide-white/[0.06]">
              <div class="px-5 py-5 flex justify-center">
                <CryptoAllocationChart :allocations="chartAllocs" :dynamic-pct="dynamicPct" :size="168" />
              </div>
              <div class="px-5 py-4 flex justify-between gap-3 text-sm">
                <span class="text-slate-500">Dynamic strategy</span>
                <span class="text-slate-200">{{ strategyLabel }}</span>
              </div>
            </div>
          </div>
        </section>

        <!-- Backtests: one calm row each; the row opens the result -->
        <section class="mt-12" aria-labelledby="ci-backtests">
          <div class="flex items-end justify-between gap-4 mb-3">
            <div>
              <h2 id="ci-backtests" class="ui-section-title">Backtests</h2>
              <p class="ui-section-sub tabular-nums">{{ btTotal }} for this instance</p>
            </div>
            <button @click="showBacktest = true" class="ui-btn">
              <span class="material-symbols-outlined text-[16px]">add</span> New backtest
            </button>
          </div>

          <div class="ui-card overflow-hidden">
            <div v-if="btLoading && !backtests.length" class="ui-rows" aria-hidden="true">
              <div v-for="n in 3" :key="n" class="h-16 px-5 flex items-center"><div class="h-4 w-48 ui-skel"></div></div>
            </div>
            <p v-else-if="!backtests.length" class="ui-empty">No backtests yet for this instance</p>

            <template v-else>
              <div class="bt-row hidden md:grid ui-th">
                <span class="bt-name">Backtest</span>
                <span class="bt-status">Status</span>
                <span class="bt-done">Completed</span>
                <span class="bt-pnl text-right">P&amp;L</span>
                <span class="bt-go"></span>
              </div>
              <ul class="ui-rows">
                <li v-for="b in backtests" :key="b.id"
                    class="bt-row grid items-center min-h-16 px-5 py-3 cursor-pointer transition-colors duration-150 ease-out hover:bg-white/[0.03] focus-visible:outline-none focus-visible:bg-white/[0.03]"
                    tabindex="0"
                    :title="`Open backtest ${b.id}`"
                    @click="router.push(`/backtests/${b.id}`)"
                    @keydown.enter.self="router.push(`/backtests/${b.id}`)">
                  <span class="bt-name min-w-0">
                    <span class="block text-sm text-slate-100 truncate font-mono">{{ b.id }}</span>
                    <span class="block text-xs text-slate-500 mt-0.5 truncate tabular-nums">{{ btCoinsLabel(b) }} · {{ b.start_date }} → {{ b.end_date }}</span>
                  </span>
                  <span class="bt-status inline-flex items-center gap-2 text-sm capitalize" :class="statusStyle(b.status)">
                    <span class="ui-dot" :class="statusDot(b.status)"></span>{{ b.status }}
                  </span>
                  <span class="bt-done min-w-0 tabular-nums">
                    <span class="block text-sm text-slate-300 truncate">{{ fmtDate(b.completed_at) }}</span>
                    <span class="block text-xs text-slate-500 mt-0.5">{{ b.time_elapsed_seconds != null ? fmtDuration(b.time_elapsed_seconds) : '—' }}</span>
                  </span>
                  <span class="bt-pnl text-right tabular-nums">
                    <span class="block text-sm" :class="pnlColor(b.pnl)">{{ fmtPnl(b.pnl) }}</span>
                    <span class="block text-xs mt-0.5" :class="pnlColor(b.pnl_percent)">{{ fmtPct(b.pnl_percent) }}</span>
                  </span>
                  <span class="bt-go hidden md:flex justify-end text-slate-600">
                    <span class="material-symbols-outlined text-[18px]">chevron_right</span>
                  </span>
                </li>
              </ul>
            </template>
          </div>
        </section>

        <!-- Details: rarely needed, so last -->
        <section class="mt-12" aria-labelledby="ci-details">
          <div class="mb-3">
            <h2 id="ci-details" class="ui-section-title">Details</h2>
            <p class="ui-section-sub">Instance and brokerage</p>
          </div>
          <div class="ui-card grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-white/[0.06]">
            <dl class="px-5 py-4 text-sm divide-y divide-white/[0.05]">
              <div class="flex justify-between gap-3 py-2.5"><dt class="text-slate-500">ID</dt><dd class="text-slate-300 font-mono text-xs truncate">{{ inst.id }}</dd></div>
              <div class="flex justify-between gap-3 py-2.5"><dt class="text-slate-500">Band</dt><dd class="text-slate-200">{{ bandLabel }}</dd></div>
              <div class="flex justify-between gap-3 py-2.5"><dt class="text-slate-500">Cadence</dt><dd class="text-slate-200 tabular-nums">{{ cadence }}</dd></div>
              <div class="flex justify-between gap-3 py-2.5"><dt class="text-slate-500">Uptime</dt><dd class="text-slate-200 tabular-nums">{{ running ? fmtDuration(liveSecs) : '—' }}</dd></div>
              <div class="flex justify-between gap-3 py-2.5"><dt class="text-slate-500">Created by</dt><dd class="text-slate-200 capitalize">{{ inst.created_by || 'user' }}</dd></div>
            </dl>
            <dl class="px-5 py-4 text-sm divide-y divide-white/[0.05]">
              <div class="flex justify-between gap-3 py-2.5"><dt class="text-slate-500">Account</dt><dd class="text-slate-200 truncate">{{ brokerage?.account_name || inst.brokerage_id || '—' }}</dd></div>
              <div class="flex justify-between gap-3 py-2.5"><dt class="text-slate-500">Broker</dt><dd class="text-slate-200">Alpaca</dd></div>
              <div class="flex justify-between gap-3 py-2.5"><dt class="text-slate-500">Mode</dt><dd class="text-slate-200">{{ isPaper ? 'Paper' : 'Live' }}</dd></div>
              <div class="flex justify-between gap-3 py-2.5"><dt class="text-slate-500">Account value</dt><dd class="text-slate-100 tabular-nums">{{ accountValue != null ? fmtUsd(accountValue) : '—' }}</dd></div>
            </dl>
          </div>
        </section>
      </template>
    </main>

    <CryptoCreateInstanceModal
      v-if="showEdit && inst"
      :edit-instance="inst"
      @close="showEdit = false"
      @saved="onEditSaved"
    />
    <CryptoBacktestModal
      v-if="showBacktest && inst"
      :instance="inst"
      @close="showBacktest = false"
      @created="onBacktestCreated"
    />
  </AppShell>
</template>

<style scoped>
/* phone   name · P&L / status
   md      name · status · P&L · chevron
   lg      name · status · completed · P&L · chevron */
.bt-row {
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas: "name pnl" "status pnl";
  column-gap: 16px;
  row-gap: 6px;
}
.bt-name { grid-area: name; }
.bt-status { grid-area: status; }
.bt-done { grid-area: done; display: none; }
.bt-pnl { grid-area: pnl; }
.bt-go { grid-area: go; }
@media (min-width: 768px) {
  .bt-row {
    grid-template-columns: minmax(0, 1fr) 112px 120px 24px;
    grid-template-areas: "name status pnl go";
    row-gap: 0;
  }
}
@media (min-width: 1024px) {
  .bt-row {
    grid-template-columns: minmax(0, 1fr) 112px 176px 120px 24px;
    grid-template-areas: "name status done pnl go";
  }
  .bt-done { display: block; }
}
</style>
