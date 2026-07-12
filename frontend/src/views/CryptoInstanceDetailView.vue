<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import CryptoCreateInstanceModal from '../components/crypto/CryptoCreateInstanceModal.vue'
import CryptoBacktestModal from '../components/crypto/CryptoBacktestModal.vue'
import CryptoAllocationChart from '../components/crypto/CryptoAllocationChart.vue'
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
const PALETTE = ['#a78bfa', '#7c9bff', '#5ad1e0', '#5ee6b8', '#f0b354', '#f3799f', '#b98bff']
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
function statusStyle(s) {
  const v = String(s || '').toLowerCase()
  if (['finished', 'completed', 'done'].includes(v)) return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
  if (['running', 'queued', 'pending'].includes(v)) return 'bg-sky-500/10 text-sky-400 border-sky-500/20'
  if (['error', 'failed'].includes(v)) return 'bg-red-500/10 text-red-400 border-red-500/20'
  if (v === 'paused') return 'bg-amber-500/10 text-amber-400 border-amber-500/20'
  return 'bg-slate-500/10 text-slate-400 border-slate-700'
}
function pnlColor(n) { const v = Number(n); return v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-slate-300' }
function fmtPnl(n) { const v = Number(n); if (!Number.isFinite(v)) return '—'; return `${v >= 0 ? '+' : ''}${fmtUsd(v)}` }
function fmtPct(n) { const v = Number(n); if (!Number.isFinite(v)) return '—'; return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` }
function fmtDate(s) { if (!s) return '—'; const d = new Date(s); return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }) }
</script>

<template>
  <AppShell>
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10">
      <!-- Breadcrumb -->
      <div class="flex items-center gap-2 text-sm text-slate-500 mb-5">
        <RouterLink to="/crypto" class="inline-flex items-center gap-1 hover:text-slate-300 transition-colors">
          <span class="material-symbols-outlined text-[18px]">arrow_back</span> Crypto
        </RouterLink>
        <span class="text-slate-700">/</span>
        <span class="text-slate-300 truncate">{{ inst?.name || instanceId }}</span>
      </div>

      <div v-if="loading" class="flex items-center gap-3 text-slate-400 text-sm">
        <span class="material-symbols-outlined animate-spin text-xl">progress_activity</span> Loading…
      </div>
      <div v-else-if="error" class="rounded-xl bg-red-500/10 border border-red-500/20 px-5 py-4 text-red-400 text-sm">{{ error }}</div>

      <template v-else-if="inst">
        <!-- Header -->
        <div class="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4 mb-8">
          <div class="flex items-center gap-4 min-w-0">
            <div class="size-14 rounded-2xl bg-surface border border-border-subtle flex items-center justify-center shrink-0">
              <span class="material-symbols-outlined text-primary text-3xl">currency_bitcoin</span>
            </div>
            <div class="min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <h1 class="text-2xl sm:text-3xl font-bold leading-tight truncate">{{ inst.name || inst.id }}</h1>
                <span class="text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-primary/15 text-primary border border-primary/20">24/7 · crypto</span>
              </div>
              <p class="text-sm text-slate-500 font-mono mt-0.5 truncate">{{ inst.id }}</p>
            </div>
          </div>

          <div class="flex items-center gap-2.5 flex-wrap">
            <div class="flex items-center gap-1.5 text-sm font-semibold px-3 py-1.5 rounded-full"
                 :class="crashed ? 'bg-red-500/10 text-red-400 border border-red-500/20'
                   : running ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                   : 'bg-slate-500/10 text-slate-500 border border-slate-700'">
              <div class="size-1.5 rounded-full" :class="crashed ? 'bg-red-400' : running ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'"></div>
              {{ crashed ? 'Crashed' : running ? 'Running' : 'Stopped' }}
            </div>
            <button @click="toggleRun" :disabled="busy"
                    class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold border transition-colors disabled:opacity-40"
                    :class="running ? 'text-amber-400 border-amber-500/30 hover:bg-amber-500/10' : 'text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/10'">
              <span class="material-symbols-outlined text-[18px]">{{ running ? 'stop' : 'play_arrow' }}</span>
              {{ running ? 'Stop' : 'Start' }}
            </button>
            <button @click="showEdit = true"
                    class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold text-amber-400 border border-amber-500/30 hover:bg-amber-500/10 transition-colors">
              <span class="material-symbols-outlined text-[18px]">tune</span> Edit
            </button>
            <button @click="refresh" title="Refresh"
                    class="inline-flex items-center justify-center px-2.5 py-2 rounded-lg border border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface transition-colors">
              <span class="material-symbols-outlined text-[18px]">refresh</span>
            </button>
          </div>
        </div>

        <!-- Info cards -->
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-5 mb-6">
          <!-- Instance info -->
          <div class="glass-card rounded-2xl p-5 sm:p-6">
            <p class="text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-4">Instance info</p>
            <dl class="space-y-3 text-sm">
              <div class="flex justify-between gap-3"><dt class="text-slate-500">Band</dt><dd class="text-slate-200 font-medium">{{ bandLabel }}</dd></div>
              <div class="flex justify-between gap-3"><dt class="text-slate-500">Cadence</dt><dd class="text-slate-200 font-medium">{{ cadence }}</dd></div>
              <div class="flex justify-between gap-3"><dt class="text-slate-500">Uptime</dt><dd class="text-emerald-400 font-medium tabular-nums">{{ running ? fmtDuration(liveSecs) : '—' }}</dd></div>
              <div class="flex justify-between gap-3"><dt class="text-slate-500">Created by</dt><dd class="text-slate-200 font-medium capitalize">{{ inst.created_by || 'user' }}</dd></div>
            </dl>
          </div>

          <!-- Brokerage -->
          <div class="glass-card rounded-2xl p-5 sm:p-6">
            <p class="text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-4">Brokerage</p>
            <div class="flex items-center gap-2.5 mb-4">
              <div class="size-9 rounded-lg bg-surface border border-border-subtle flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-slate-400 text-lg">account_balance</span>
              </div>
              <div class="min-w-0">
                <p class="text-sm font-semibold text-slate-100 truncate">{{ brokerage?.account_name || inst.brokerage_id || '—' }}</p>
                <p class="text-xs text-slate-500">Alpaca</p>
              </div>
            </div>
            <dl class="space-y-3 text-sm">
              <div class="flex justify-between gap-3">
                <dt class="text-slate-500">Mode</dt>
                <dd class="font-medium" :class="isPaper ? 'text-sky-400' : 'text-emerald-400'">{{ isPaper ? 'Paper' : 'Live' }}</dd>
              </div>
              <div class="flex justify-between gap-3"><dt class="text-slate-500">Account value</dt><dd class="text-slate-200 font-bold tabular-nums">{{ accountValue != null ? fmtUsd(accountValue) : '—' }}</dd></div>
            </dl>
          </div>

          <!-- Allocation -->
          <div class="glass-card rounded-2xl p-5 sm:p-6">
            <p class="text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-4">Allocation</p>
            <div class="flex justify-center">
              <CryptoAllocationChart :allocations="chartAllocs" :dynamic-pct="dynamicPct" :size="160" />
            </div>
            <div class="mt-4 pt-4 border-t border-border-subtle">
              <p class="text-xs text-slate-500 mb-1">Dynamic strategy</p>
              <p class="text-sm font-semibold text-primary">{{ strategyLabel }}</p>
            </div>
          </div>
        </div>

        <!-- Backtests -->
        <div class="glass-card rounded-2xl p-5 sm:p-6">
          <div class="flex items-center justify-between gap-3 mb-4">
            <p class="text-[11px] font-bold uppercase tracking-widest text-slate-500">
              Backtests <span class="text-slate-600">({{ btTotal }})</span>
            </p>
            <button @click="showBacktest = true"
                    class="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-primary/[0.14] border border-primary/45 text-primary font-semibold text-[13px] hover:bg-primary/25 transition-colors">
              <span class="material-symbols-outlined text-[16px]">add</span> New Backtest
            </button>
          </div>

          <div v-if="btLoading && !backtests.length" class="py-8 text-center text-slate-500 text-sm">Loading backtests…</div>
          <div v-else-if="!backtests.length" class="py-10 text-center">
            <span class="material-symbols-outlined text-3xl text-slate-700">analytics</span>
            <p class="text-slate-500 text-sm mt-2">No backtests yet for this instance.</p>
          </div>

          <div v-else class="overflow-x-auto -mx-1">
            <table class="w-full min-w-[720px] text-sm">
              <thead>
                <tr class="text-[10.5px] uppercase tracking-wider text-slate-600 border-b border-border-subtle">
                  <th class="text-left font-medium py-2 px-2">ID</th>
                  <th class="text-left font-medium py-2 px-2">Coins</th>
                  <th class="text-left font-medium py-2 px-2">Period</th>
                  <th class="text-left font-medium py-2 px-2">Completed</th>
                  <th class="text-left font-medium py-2 px-2">Status</th>
                  <th class="text-right font-medium py-2 px-2">Elapsed</th>
                  <th class="text-right font-medium py-2 px-2">P&amp;L</th>
                  <th class="text-right font-medium py-2 px-2">P&amp;L %</th>
                  <th class="py-2 px-2"></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="b in backtests" :key="b.id" class="border-b border-border-subtle/60 hover:bg-surface/40 transition-colors">
                  <td class="py-2.5 px-2 font-mono text-slate-400 text-xs">{{ b.id }}</td>
                  <td class="py-2.5 px-2">
                    <div class="flex flex-wrap gap-1">
                      <span v-for="t in btCoins(b).slice(0, 4)" :key="t" class="text-[11px] font-mono text-slate-300">{{ t }}</span>
                      <span v-if="btCoins(b).length > 4" class="text-[11px] text-slate-600">+{{ btCoins(b).length - 4 }}</span>
                      <span v-if="!btCoins(b).length" class="text-[11px] text-primary">Dynamic</span>
                    </div>
                  </td>
                  <td class="py-2.5 px-2 text-slate-400 text-xs whitespace-nowrap">{{ b.start_date }} → {{ b.end_date }}</td>
                  <td class="py-2.5 px-2 text-slate-400 text-xs whitespace-nowrap">{{ fmtDate(b.completed_at) }}</td>
                  <td class="py-2.5 px-2">
                    <span class="inline-block text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded border" :class="statusStyle(b.status)">{{ b.status }}</span>
                  </td>
                  <td class="py-2.5 px-2 text-right text-slate-400 text-xs tabular-nums whitespace-nowrap">{{ b.time_elapsed_seconds != null ? fmtDuration(b.time_elapsed_seconds) : '—' }}</td>
                  <td class="py-2.5 px-2 text-right font-semibold tabular-nums" :class="pnlColor(b.pnl)">{{ fmtPnl(b.pnl) }}</td>
                  <td class="py-2.5 px-2 text-right font-semibold tabular-nums" :class="pnlColor(b.pnl_percent)">{{ fmtPct(b.pnl_percent) }}</td>
                  <td class="py-2.5 px-2 text-right">
                    <button @click="router.push(`/backtests/${b.id}`)"
                            class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border border-border-subtle text-xs text-slate-300 hover:text-primary hover:border-primary/40 transition-colors">
                      <span class="material-symbols-outlined text-[14px]">open_in_new</span> View
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
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
