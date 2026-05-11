<script setup>
import { ref, computed, onMounted, onUnmounted, defineComponent, h } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import PortfolioChart from '../components/PortfolioChart.vue'
import { getUser, getToken } from '../utils/auth.js'

const STATUS_COLORS = {
  running: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  paused:  'text-violet-400 bg-violet-500/10 border-violet-500/20',
  stopped: 'text-slate-500 bg-slate-500/10 border-slate-700',
}
const StatusBadge = defineComponent({
  props: { status: String },
  setup(props) {
    return () => {
      const key = (props.status || 'stopped').toLowerCase().replace(/\s*\(.*\)/, '').trim()
      const cls = STATUS_COLORS[key] || STATUS_COLORS.stopped
      return h('span', { class: `shrink-0 text-[10px] font-bold px-2 py-0.5 rounded-full border uppercase tracking-wide ${cls}` }, props.status || 'Stopped')
    }
  },
})

const user = computed(() => getUser())

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

function authHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

// ── Brokerages ────────────────────────────────────────────────────────────────
const accounts  = ref([])
const bkLoading = ref(true)
const bkError   = ref('')

async function fetchAccounts() {
  bkLoading.value = true
  bkError.value   = ''
  try {
    const res = await fetch(`${API_BASE}/brokerages`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    accounts.value = data.accounts || []
  } catch (e) {
    bkError.value = e.message
  } finally {
    bkLoading.value = false
  }
}

// ── Services ──────────────────────────────────────────────────────────────────
const engines     = ref([])   // from GET /status
const agentStatus = ref(null) // from GET /agent/control
const digestStatus = ref(null) // from GET /digest/control
const nexusStatus  = ref(null) // from GET /nexus/status
const svcLoading  = ref(true)
const svcBusy     = ref({})   // { [engineId]: true } while action in flight
let svcPollTimer  = null

async function fetchServices() {
  try {
    const [statusRes, agentRes, digestRes, nexusRes] = await Promise.all([
      fetch(`${API_BASE}/status`,          { headers: authHeaders() }),
      fetch(`${API_BASE}/agent/control`,   { headers: authHeaders() }),
      fetch(`${API_BASE}/digest/control`,  { headers: authHeaders() }),
      fetch(`${API_BASE}/nexus/status`,    { headers: authHeaders() }),
    ])
    if (statusRes.ok)  { const d = await statusRes.json();  engines.value      = d.engines || [] }
    if (agentRes.ok)   { agentStatus.value  = await agentRes.json() }
    if (digestRes.ok)  { digestStatus.value = await digestRes.json() }
    if (nexusRes.ok)   { nexusStatus.value  = await nexusRes.json() }
  } catch { /* non-critical */ } finally {
    svcLoading.value = false
  }
}

async function svcPost(url, body = {}) {
  const res = await fetch(`${API_BASE}${url}`, {
    method: 'POST', headers: authHeaders(), body: JSON.stringify(body),
  })
  if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${res.status}`) }
  return res.json().catch(() => ({}))
}

async function svcAction(engineId, fn) {
  if (svcBusy.value[engineId]) return
  svcBusy.value = { ...svcBusy.value, [engineId]: true }
  try {
    await fn()
    await fetchServices()
  } catch (e) {
    console.error('Service action failed:', e.message)
  } finally {
    svcBusy.value = { ...svcBusy.value, [engineId]: false }
  }
}

// Per-engine control helpers
function engineById(id) {
  return engines.value.find(e => e.id === id) || {}
}
function isRunning(id) {
  return (engineById(id).status || '').toLowerCase() === 'running'
}
function isPaused(id) {
  return (engineById(id).status || '').toLowerCase() === 'paused'
}

// Price engine
function togglePrice()       { svcAction('price_engine',   () => isRunning('price_engine')   ? svcPost('/config/terminate-price')   : svcPost('/config/run-price-service')) }
function terminatePrice()    { svcAction('price_engine',   () => svcPost('/config/terminate-price')) }

// Discover engine
function toggleDiscover()    { svcAction('discover_engine', () => svcPost('/discover/control', { running: !isRunning('discover_engine') })) }

// AI Backtest Agent — start modal
const showStartAgentModal = ref(false)
const startAgentRequest   = ref('')

function openStartAgent() {
  startAgentRequest.value = ''
  showStartAgentModal.value = true
}
function closeStartAgent() { showStartAgentModal.value = false }
async function confirmStartAgent() {
  closeStartAgent()
  const sr = startAgentRequest.value.trim() || null
  await svcAction('ai_backtest_engine', () => svcPost('/agent/control', { running: true, ...(sr ? { special_request: sr } : {}) }))
}

function toggleAgent() {
  const active = isRunning('ai_backtest_engine') || isPaused('ai_backtest_engine')
  if (active) {
    svcAction('ai_backtest_engine', () => svcPost('/agent/control', { running: false }))
  } else {
    openStartAgent()
  }
}
function pauseAgent()  { svcAction('ai_backtest_engine', () => svcPost('/agent/control', { paused: true })) }
function resumeAgent() { svcAction('ai_backtest_engine', () => svcPost('/agent/control', { paused: false })) }

// Daily Digest
function toggleDigest()      { svcAction('daily_digest_engine', () => svcPost('/digest/control', { running: !digestStatus.value?.running })) }
function sendDigestNow()     { svcAction('daily_digest_engine', () => svcPost('/digest/send-now')) }

// Nexus Graph
function toggleNexus()       { svcAction('nexus_graph_engine', () => svcPost('/nexus/control', { running: !isRunning('nexus_graph_engine') })) }

// Formatting
function fmtDateTime(v) {
  if (!v) return null
  try { return new Date(v).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) }
  catch { return String(v) }
}

function nexusProgress() {
  const gb = nexusStatus.value?.graph_build || {}
  return gb.progress_pct != null ? Math.round(gb.progress_pct) : null
}

function nexusPhase() {
  const stages = nexusStatus.value?.graph_build?.stages || []
  const last = stages[stages.length - 1]
  return last?.message || null
}

onMounted(() => {
  fetchAccounts()
  fetchServices()
  svcPollTimer = setInterval(fetchServices, 10000)
})
onUnmounted(() => {
  if (svcPollTimer) clearInterval(svcPollTimer)
})
</script>

<template>
  <AppShell>
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10 space-y-8 sm:space-y-10 lg:space-y-12">

      <!-- Welcome row -->
      <div>
        <p class="text-primary text-xs font-bold uppercase tracking-widest mb-2">Dashboard</p>
        <h1 class="text-2xl sm:text-3xl font-bold leading-tight">
          Welcome back, <span class="text-primary">{{ user?.username }}</span>.
        </h1>
        <p class="text-slate-400 mt-2 text-sm">Your workspace is ready.</p>
      </div>

      <!-- ── Portfolio Section ─────────────────────────────────────────────── -->
      <section>
        <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-5">
          <div>
            <h2 class="text-lg font-bold text-slate-100">Portfolio</h2>
            <p class="text-slate-500 text-xs mt-0.5">Live equity history from your linked brokerages.</p>
          </div>
          <RouterLink
            to="/brokerages"
            class="flex items-center gap-1.5 text-xs font-medium text-primary hover:brightness-110 transition-all"
          >
            Manage brokerages
            <span class="material-symbols-outlined text-[14px]">arrow_forward</span>
          </RouterLink>
        </div>

        <!-- Loading -->
        <div v-if="bkLoading" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
          <div v-for="n in 2" :key="n" class="glass-card rounded-2xl h-[320px] animate-pulse"></div>
        </div>

        <!-- Error -->
        <div v-else-if="bkError" class="rounded-xl bg-red-500/10 border border-red-500/20 px-5 py-4 text-red-400 text-sm">
          {{ bkError }}
        </div>

        <!-- No brokerages -->
        <div
          v-else-if="accounts.length === 0"
          class="rounded-2xl border border-border-subtle bg-surface/20 px-8 py-12 flex flex-col items-center gap-3 text-center"
        >
          <span class="material-symbols-outlined text-4xl text-slate-700">account_balance</span>
          <p class="text-slate-400 text-sm font-medium">No brokerages linked.</p>
          <RouterLink
            to="/brokerages"
            class="px-4 py-2 rounded-lg bg-primary text-background-dark text-xs font-bold hover:brightness-110 transition-all"
          >
            Link a brokerage
          </RouterLink>
        </div>

        <!-- Charts grid -->
        <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
          <PortfolioChart
            v-for="acct in accounts"
            :key="acct.id"
            :account="acct"
          />
        </div>
      </section>

      <!-- ── Services ─────────────────────────────────────────────────────── -->
      <section>
        <div class="flex items-center justify-between mb-5">
          <div>
            <h2 class="text-lg font-bold text-slate-100">Services</h2>
            <p class="text-slate-500 text-xs mt-0.5">Status and controls for all IntelliStock background services.</p>
          </div>
          <button
            @click="fetchServices"
            class="p-1.5 rounded-lg border border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface transition-all"
            title="Refresh"
          >
            <span class="material-symbols-outlined text-[16px]" :class="svcLoading ? 'animate-spin' : ''">refresh</span>
          </button>
        </div>

        <!-- Loading skeleton -->
        <div v-if="svcLoading" class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          <div v-for="n in 5" :key="n" class="glass-card rounded-2xl h-36 animate-pulse"></div>
        </div>

        <div v-else class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">

          <!-- ── Price Engine ───────────────────────────────────────────────── -->
          <div class="glass-card rounded-2xl p-5 flex flex-col gap-4">
            <div class="flex items-start justify-between gap-3">
              <div class="flex items-center gap-3 min-w-0">
                <div class="size-10 rounded-xl bg-sky-500/10 border border-sky-500/20 flex items-center justify-center shrink-0">
                  <span class="material-symbols-outlined text-sky-400 text-xl">trending_up</span>
                </div>
                <div class="min-w-0">
                  <p class="text-sm font-semibold text-slate-100 truncate">Price Engine</p>
                  <p class="text-[11px] text-slate-500 mt-0.5">Live market data</p>
                </div>
              </div>
              <StatusBadge :status="engineById('price_engine').status" />
            </div>
            <div class="text-xs text-slate-500">
              <span v-if="engineById('price_engine').details" class="font-mono">{{ engineById('price_engine').details }}</span>
              <span v-else class="italic">No extra details</span>
            </div>
            <div class="flex items-center gap-2 mt-auto">
              <button
                v-if="isRunning('price_engine')"
                @click="terminatePrice"
                :disabled="svcBusy['price_engine']"
                class="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-semibold border border-red-500/20 text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-40"
              >
                <span class="material-symbols-outlined text-[13px]">stop_circle</span> Terminate
              </button>
              <button
                v-else
                @click="togglePrice"
                :disabled="svcBusy['price_engine']"
                class="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-semibold border border-emerald-500/20 text-emerald-400 hover:bg-emerald-500/10 transition-colors disabled:opacity-40"
              >
                <span class="material-symbols-outlined text-[13px]">play_circle</span> Start Broker
              </button>
            </div>
          </div>

          <!-- ── Discover Engine ────────────────────────────────────────────── -->
          <div class="glass-card rounded-2xl p-5 flex flex-col gap-4">
            <div class="flex items-start justify-between gap-3">
              <div class="flex items-center gap-3 min-w-0">
                <div class="size-10 rounded-xl bg-violet-500/10 border border-violet-500/20 flex items-center justify-center shrink-0">
                  <span class="material-symbols-outlined text-violet-400 text-xl">search</span>
                </div>
                <div class="min-w-0">
                  <p class="text-sm font-semibold text-slate-100 truncate">Discover Engine</p>
                  <p class="text-[11px] text-slate-500 mt-0.5">Opportunity discovery</p>
                </div>
              </div>
              <StatusBadge :status="engineById('discover_engine').status" />
            </div>
            <div class="text-xs text-slate-500 flex-1">
              <span v-if="engineById('discover_engine').details" class="font-mono">{{ engineById('discover_engine').details }}</span>
              <span v-else class="italic">No extra details</span>
            </div>
            <div class="flex items-center gap-2 mt-auto">
              <button
                v-if="isRunning('discover_engine')"
                @click="toggleDiscover"
                :disabled="svcBusy['discover_engine']"
                class="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-semibold border border-red-500/20 text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-40"
              >
                <span class="material-symbols-outlined text-[13px]">stop_circle</span> Stop
              </button>
              <button
                v-else
                @click="toggleDiscover"
                :disabled="svcBusy['discover_engine']"
                class="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-semibold border border-emerald-500/20 text-emerald-400 hover:bg-emerald-500/10 transition-colors disabled:opacity-40"
              >
                <span class="material-symbols-outlined text-[13px]">play_circle</span> Start
              </button>
            </div>
          </div>

          <!-- ── AI Backtest Agent ───────────────────────────────────────────── -->
          <div class="glass-card rounded-2xl p-5 flex flex-col gap-4">
            <div class="flex items-start justify-between gap-3">
              <div class="flex items-center gap-3 min-w-0">
                <div class="size-10 rounded-xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center shrink-0">
                  <span class="material-symbols-outlined text-amber-400 text-xl">smart_toy</span>
                </div>
                <div class="min-w-0">
                  <p class="text-sm font-semibold text-slate-100 truncate">AI Backtest Agent</p>
                  <p class="text-[11px] text-slate-500 mt-0.5">Automated strategy search</p>
                </div>
              </div>
              <StatusBadge :status="engineById('ai_backtest_engine').status" />
            </div>
            <!-- Stats -->
            <div class="grid grid-cols-2 gap-2 text-xs">
              <div class="rounded-lg bg-surface/50 px-3 py-2">
                <p class="text-slate-600 mb-0.5">Backtests today</p>
                <p class="font-mono font-semibold text-slate-200">{{ agentStatus?.count_today ?? '—' }}</p>
              </div>
              <div class="rounded-lg bg-surface/50 px-3 py-2">
                <p class="text-slate-600 mb-0.5">Last run</p>
                <p class="font-mono text-slate-300 truncate">{{ agentStatus?.last_run_date || '—' }}</p>
              </div>
              <div v-if="agentStatus?.resume_at" class="col-span-2 rounded-lg bg-amber-500/5 border border-amber-500/15 px-3 py-2">
                <p class="text-slate-600 mb-0.5">Resume at</p>
                <p class="font-mono text-amber-400 text-[11px]">{{ agentStatus.resume_at }}</p>
              </div>
            </div>
            <!-- Controls -->
            <div class="flex items-center gap-2 mt-auto flex-wrap">
              <button
                v-if="isPaused('ai_backtest_engine')"
                @click="resumeAgent"
                :disabled="svcBusy['ai_backtest_engine']"
                class="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-semibold border border-sky-500/20 text-sky-400 hover:bg-sky-500/10 transition-colors disabled:opacity-40"
              >
                <span class="material-symbols-outlined text-[13px]">play_circle</span> Resume
              </button>
              <button
                v-else-if="isRunning('ai_backtest_engine')"
                @click="pauseAgent"
                :disabled="svcBusy['ai_backtest_engine']"
                class="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-semibold border border-violet-500/20 text-violet-400 hover:bg-violet-500/10 transition-colors disabled:opacity-40"
              >
                <span class="material-symbols-outlined text-[13px]">pause_circle</span> Pause
              </button>
              <button
                @click="toggleAgent"
                :disabled="svcBusy['ai_backtest_engine']"
                class="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-semibold border transition-colors disabled:opacity-40"
                :class="isRunning('ai_backtest_engine') || isPaused('ai_backtest_engine')
                  ? 'border-red-500/20 text-red-400 hover:bg-red-500/10'
                  : 'border-emerald-500/20 text-emerald-400 hover:bg-emerald-500/10'"
              >
                <span class="material-symbols-outlined text-[13px]">{{ isRunning('ai_backtest_engine') || isPaused('ai_backtest_engine') ? 'stop_circle' : 'play_circle' }}</span>
                {{ isRunning('ai_backtest_engine') || isPaused('ai_backtest_engine') ? 'Stop' : 'Start' }}
              </button>
            </div>
          </div>

          <!-- ── Daily Digest Engine ────────────────────────────────────────── -->
          <div class="glass-card rounded-2xl p-5 flex flex-col gap-4">
            <div class="flex items-start justify-between gap-3">
              <div class="flex items-center gap-3 min-w-0">
                <div class="size-10 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center shrink-0">
                  <span class="material-symbols-outlined text-emerald-400 text-xl">newspaper</span>
                </div>
                <div class="min-w-0">
                  <p class="text-sm font-semibold text-slate-100 truncate">Daily Digest</p>
                  <p class="text-[11px] text-slate-500 mt-0.5">Discord market summaries</p>
                </div>
              </div>
              <StatusBadge :status="digestStatus?.running ? 'Running' : 'Stopped'" />
            </div>
            <!-- Stats -->
            <div class="grid grid-cols-2 gap-2 text-xs">
              <div class="rounded-lg bg-surface/50 px-3 py-2">
                <p class="text-slate-600 mb-0.5">Last morning</p>
                <p class="font-mono text-slate-300 text-[11px]">{{ fmtDateTime(digestStatus?.last_morning_at) || '—' }}</p>
              </div>
              <div class="rounded-lg bg-surface/50 px-3 py-2">
                <p class="text-slate-600 mb-0.5">Last evening</p>
                <p class="font-mono text-slate-300 text-[11px]">{{ fmtDateTime(digestStatus?.last_evening_at) || '—' }}</p>
              </div>
            </div>
            <!-- Controls -->
            <div class="flex items-center gap-2 mt-auto">
              <button
                @click="toggleDigest"
                :disabled="svcBusy['daily_digest_engine']"
                class="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-semibold border transition-colors disabled:opacity-40"
                :class="digestStatus?.running
                  ? 'border-red-500/20 text-red-400 hover:bg-red-500/10'
                  : 'border-emerald-500/20 text-emerald-400 hover:bg-emerald-500/10'"
              >
                <span class="material-symbols-outlined text-[13px]">{{ digestStatus?.running ? 'stop_circle' : 'play_circle' }}</span>
                {{ digestStatus?.running ? 'Stop' : 'Start' }}
              </button>
              <button
                @click="sendDigestNow"
                :disabled="svcBusy['daily_digest_engine']"
                class="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-semibold border border-primary/20 text-primary hover:bg-primary/10 transition-colors disabled:opacity-40"
              >
                <span class="material-symbols-outlined text-[13px]">send</span> Send Now
              </button>
            </div>
          </div>

          <!-- ── Nexus Graph Engine ──────────────────────────────────────────── -->
          <div class="glass-card rounded-2xl p-5 flex flex-col gap-4">
            <div class="flex items-start justify-between gap-3">
              <div class="flex items-center gap-3 min-w-0">
                <div class="size-10 rounded-xl bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
                  <span class="material-symbols-outlined text-primary text-xl">hub</span>
                </div>
                <div class="min-w-0">
                  <p class="text-sm font-semibold text-slate-100 truncate">Nexus Graph Engine</p>
                  <p class="text-[11px] text-slate-500 mt-0.5">Knowledge graph builder</p>
                </div>
              </div>
              <StatusBadge :status="engineById('nexus_graph_engine').status" />
            </div>
            <!-- Progress -->
            <div class="space-y-2 text-xs">
              <div v-if="nexusProgress() != null">
                <div class="flex items-center justify-between text-slate-500 mb-1">
                  <span>Build progress</span>
                  <span class="font-mono font-semibold text-slate-300">{{ nexusProgress() }}%</span>
                </div>
                <div class="h-1.5 bg-surface rounded-full overflow-hidden">
                  <div
                    class="h-full bg-primary rounded-full transition-all duration-700"
                    :style="{ width: nexusProgress() + '%' }"
                  ></div>
                </div>
              </div>
              <p v-if="nexusPhase()" class="text-slate-500 truncate font-mono">{{ nexusPhase() }}</p>
              <p v-else-if="nexusProgress() == null" class="text-slate-600 italic">No build in progress</p>
            </div>
            <!-- Controls -->
            <div class="flex items-center gap-2 mt-auto">
              <button
                @click="toggleNexus"
                :disabled="svcBusy['nexus_graph_engine']"
                class="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-semibold border transition-colors disabled:opacity-40"
                :class="isRunning('nexus_graph_engine')
                  ? 'border-red-500/20 text-red-400 hover:bg-red-500/10'
                  : 'border-emerald-500/20 text-emerald-400 hover:bg-emerald-500/10'"
              >
                <span class="material-symbols-outlined text-[13px]">{{ isRunning('nexus_graph_engine') ? 'stop_circle' : 'play_circle' }}</span>
                {{ isRunning('nexus_graph_engine') ? 'Stop' : 'Start' }}
              </button>
            </div>
          </div>

        </div>
      </section>

      <!-- ── Re-run onboarding ──────────────────────────────────────────────── -->
      <section class="pt-2">
        <div class="rounded-2xl border border-border-subtle bg-surface/30 px-5 py-4 sm:px-6 sm:py-5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div class="min-w-0">
            <p class="text-sm font-semibold text-slate-200 flex items-center gap-2">
              <span class="material-symbols-outlined text-primary text-[18px]">replay</span>
              Re-run onboarding
            </p>
            <p class="text-xs text-slate-500 mt-1 leading-relaxed">
              Walk through the welcome flow again to add another model, link a brokerage, or spin up a new instance.
            </p>
          </div>
          <RouterLink
            to="/onboarding"
            class="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg border border-primary/40 text-primary text-xs font-bold hover:bg-primary/10 transition-all shrink-0"
          >
            Open onboarding
            <span class="material-symbols-outlined text-[14px]">arrow_forward</span>
          </RouterLink>
        </div>
      </section>

    </main>
  </AppShell>

  <!-- ── Start Agent Modal ──────────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showStartAgentModal"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeStartAgent"
      >
        <div class="w-full max-w-md bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">
          <!-- Header -->
          <div class="flex items-center gap-3 px-6 py-5 border-b border-border-subtle">
            <div class="size-9 rounded-xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center shrink-0">
              <span class="material-symbols-outlined text-amber-400 text-lg">smart_toy</span>
            </div>
            <div class="min-w-0">
              <h2 class="text-base font-bold text-slate-100">Start AI Backtest Agent</h2>
              <p class="text-[11px] text-slate-500 mt-0.5">Optionally provide a special request for this run.</p>
            </div>
            <button @click="closeStartAgent" class="ml-auto text-slate-500 hover:text-slate-300 transition-colors shrink-0">
              <span class="material-symbols-outlined text-lg">close</span>
            </button>
          </div>

          <!-- Body -->
          <div class="px-6 py-5 space-y-4">
            <div>
              <label class="block text-xs font-semibold text-slate-400 mb-2">Special Request <span class="text-slate-600 font-normal">(optional)</span></label>
              <textarea
                v-model="startAgentRequest"
                rows="3"
                placeholder="e.g. Include nexus graph analysis in each strategy, focus on high-volatility stocks..."
                class="w-full bg-surface border border-border-subtle rounded-xl px-3 py-2.5 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-primary/40 resize-none"
                @keydown.ctrl.enter="confirmStartAgent"
              ></textarea>
              <p class="text-[10px] text-slate-600 mt-1.5">This instruction will be passed to the agent's strategy generation LLM each cycle.</p>
            </div>
          </div>

          <!-- Footer -->
          <div class="px-6 pb-5 flex items-center gap-3">
            <button
              @click="closeStartAgent"
              class="flex-1 py-2.5 rounded-xl border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors"
            >Cancel</button>
            <button
              @click="confirmStartAgent"
              class="flex-1 py-2.5 rounded-xl border border-emerald-500/30 bg-emerald-500/10 text-sm font-semibold text-emerald-400 hover:bg-emerald-500/20 transition-colors"
            >
              <span class="material-symbols-outlined text-[14px] mr-1" style="vertical-align: middle">play_circle</span>
              Start Agent
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to       { opacity: 0; }
</style>
