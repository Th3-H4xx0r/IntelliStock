<script setup>
import { ref, computed, onMounted, onUnmounted, defineComponent, h } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import PortfolioChart from '../components/PortfolioChart.vue'
import KalshiDashboardCard from '../components/KalshiDashboardCard.vue'
import { getToken } from '../utils/auth.js'

// Status as a dot plus a word. Green only for things that are actually
// running — the one live signal on the page.
const STATUS_STYLES = {
  running: { dot: 'bg-gain shadow-[0_0_0_3px_rgba(76,195,138,0.15)]', text: 'text-slate-200', label: 'Running' },
  paused:  { dot: 'bg-amber-400', text: 'text-slate-300', label: 'Paused' },
  stopped: { dot: 'bg-slate-600', text: 'text-slate-500', label: 'Stopped' },
}
const StatusBadge = defineComponent({
  props: { status: String },
  setup(props) {
    return () => {
      const key = (props.status || 'stopped').toLowerCase().replace(/\s*\(.*\)/, '').trim()
      const st = STATUS_STYLES[key] || { ...STATUS_STYLES.stopped, label: props.status || 'Stopped' }
      return h('span', { class: `inline-flex items-center gap-2 text-sm ${st.text}` }, [
        h('span', { class: `size-1.5 rounded-full shrink-0 ${st.dot}` }),
        st.label,
      ])
    }
  },
})

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
    // Live money first, then paper; API order within each group.
    const list = data.accounts || []
    accounts.value = [...list.filter(a => !isPaper(a)), ...list.filter(a => isPaper(a))]
    pickInitialAccount()
    fetchSummaries()
  } catch (e) {
    bkError.value = e.message
  } finally {
    bkLoading.value = false
  }
}

function isKalshi(a) { return String(a?.brokerage_type || '').toLowerCase() === 'kalshi' }
function isPaper(a) {
  return String(a?.brokerage_type || '').toLowerCase() === 'alpaca'
    ? !!a.alpaca_paper
    : /paper|demo/i.test(a?.account_name || '')
}
function accountKind(a) {
  const type = String(a?.brokerage_type || '').toLowerCase()
  const name = type === 'alpaca' ? 'Alpaca' : type === 'kalshi' ? 'Kalshi' : (a?.brokerage_type || 'Brokerage')
  return `${name} · ${isPaper(a) ? 'Paper' : 'Live'}`
}

// The selected account fills the headline chart. Remembered per browser.
const SELECTED_KEY = 'intellistock_dashboard_account'
const selectedId = ref(null)
const selectedAccount = computed(() => accounts.value.find(a => a.id === selectedId.value) || accounts.value[0] || null)
function pickInitialAccount() {
  let saved = null
  try { saved = localStorage.getItem(SELECTED_KEY) } catch { /* storage blocked */ }
  const ids = accounts.value.map(a => String(a.id))
  selectedId.value = saved && ids.includes(saved)
    ? accounts.value[ids.indexOf(saved)].id
    : accounts.value[0]?.id ?? null
}
function selectAccount(a) {
  selectedId.value = a.id
  try { localStorage.setItem(SELECTED_KEY, String(a.id)) } catch { /* storage blocked */ }
}

// Today's value and change for every account, for the list beside the chart.
const summaries = ref({}) // { [id]: { value, change_abs, change_pct } }
async function fetchSummary(a) {
  const url = isKalshi(a)
    ? `${API_BASE}/brokerages/${a.id}/kalshi/portfolio`
    : `${API_BASE}/brokerages/${a.id}/portfolio-history?range=1D`
  try {
    const res = await fetch(url, { headers: authHeaders() })
    if (!res.ok) return
    const d = await res.json()
    let row
    if (isKalshi(a)) {
      const series = Array.isArray(d?.series) ? d.series : []
      const open = series.length ? Number(series[0].value) || 0 : 0
      const change = Number(d?.day_change) || 0
      row = { value: Number(d?.value) || 0, change_abs: change, change_pct: open ? (change / open) * 100 : 0 }
    } else {
      row = { value: d.current_value, change_abs: d.change_abs, change_pct: d.change_pct }
    }
    summaries.value = { ...summaries.value, [a.id]: row }
  } catch { /* row stays blank */ }
}
function fetchSummaries() { accounts.value.forEach(fetchSummary) }

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 })
function fmtUsd(v) { return v == null || !Number.isFinite(Number(v)) ? '—' : usd.format(Number(v)) }
function fmtPct(v) {
  if (v == null || !Number.isFinite(Number(v))) return ''
  const n = Number(v)
  if (Math.abs(n) < 0.005) return '0.00%'
  return `${n > 0 ? '+' : '−'}${Math.abs(n).toFixed(2)}%`
}
function changeClass(row) {
  const n = Number(row?.change_pct)
  if (!Number.isFinite(n) || Math.abs(n) < 0.005) return 'text-slate-500'
  return n > 0 ? 'text-gain' : 'text-loss'
}

const todayLabel = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })

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
    <main class="dash flex-1 w-full max-w-[1440px] px-4 py-6 sm:px-6 sm:py-8 lg:px-8">

      <!-- Page header -->
      <header class="flex items-end justify-between gap-4">
        <div>
          <h1 class="text-2xl font-medium text-slate-100 leading-tight">Dashboard</h1>
          <p class="text-sm text-slate-500 mt-1 whitespace-nowrap">{{ todayLabel }}</p>
        </div>
        <RouterLink
          to="/brokerages"
          class="shrink-0 whitespace-nowrap inline-flex items-center gap-1.5 h-8 px-3 -mr-3 rounded-md text-sm text-slate-400 hover:text-slate-100 hover:bg-raised transition-colors"
        >
          Manage brokerages
          <span class="material-symbols-outlined text-[16px]">arrow_forward</span>
        </RouterLink>
      </header>

      <!-- ── Portfolio ──────────────────────────────────────────────────── -->
      <section class="mt-8" aria-label="Portfolio">

        <!-- Loading -->
        <div v-if="bkLoading" class="grid gap-8 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div class="space-y-3">
            <div class="h-4 w-32 rounded bg-raised"></div>
            <div class="h-11 w-64 rounded-md bg-raised"></div>
            <div class="h-[300px] rounded-lg bg-surface mt-6"></div>
          </div>
          <div class="h-[420px] rounded-xl bg-surface"></div>
        </div>

        <!-- Error -->
        <div v-else-if="bkError" class="rounded-xl border border-loss/20 bg-loss/5 px-5 py-4 text-sm text-loss">
          Couldn't load brokerages: {{ bkError }}
        </div>

        <!-- No brokerages -->
        <div
          v-else-if="accounts.length === 0"
          class="rounded-xl border border-white/[0.07] bg-surface px-6 py-12 flex flex-col items-center gap-4 text-center"
        >
          <p class="text-sm text-slate-400">No brokerage linked yet. Link one to see its equity here.</p>
          <RouterLink
            to="/brokerages"
            class="h-9 px-4 inline-flex items-center rounded-lg bg-primary text-background-dark text-sm font-medium hover:bg-white transition-colors"
          >
            Link a brokerage
          </RouterLink>
        </div>

        <!-- Headline chart + account list -->
        <div v-else class="grid gap-8 lg:grid-cols-[minmax(0,1fr)_340px] items-start">
          <PortfolioChart
            v-if="selectedAccount"
            :key="selectedAccount.id"
            :account="selectedAccount"
            variant="hero"
          />

          <div class="rounded-xl border border-white/[0.07] bg-surface overflow-hidden">
            <div class="h-12 px-4 flex items-center justify-between border-b border-white/[0.06]">
              <h2 class="text-sm font-medium text-slate-200">Accounts</h2>
              <span class="text-xs text-slate-500">Today</span>
            </div>
            <ul class="p-1.5">
              <li v-for="acct in accounts" :key="acct.id">
                <button
                  type="button"
                  @click="selectAccount(acct)"
                  class="w-full h-14 px-3 rounded-lg flex items-center justify-between gap-3 text-left transition-colors"
                  :class="selectedAccount?.id === acct.id ? 'bg-raised shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]' : 'hover:bg-white/[0.03]'"
                  :aria-pressed="selectedAccount?.id === acct.id"
                >
                  <span class="min-w-0">
                    <span class="block text-sm text-slate-100 truncate">{{ acct.account_name }}</span>
                    <span class="block text-xs text-slate-500 mt-0.5">{{ accountKind(acct) }}</span>
                  </span>
                  <span class="text-right shrink-0 tabular-nums">
                    <span class="block text-sm text-slate-100">{{ fmtUsd(summaries[acct.id]?.value) }}</span>
                    <span
                      class="block text-xs mt-0.5"
                      :class="changeClass(summaries[acct.id])"
                    >{{ fmtPct(summaries[acct.id]?.change_pct) || '\u00a0' }}</span>
                  </span>
                </button>
              </li>
            </ul>
          </div>
        </div>
      </section>

      <!-- ── Kalshi ─────────────────────────────────────────────────────── -->
      <!-- Self-hides (renders its own <section>) when no Kalshi account is linked. -->
      <KalshiDashboardCard class="mt-12" />

      <!-- ── Services ───────────────────────────────────────────────────── -->
      <section class="mt-12">
        <div class="flex items-center justify-between mb-3">
          <div>
            <h2 class="text-base font-medium text-slate-100">Services</h2>
            <p class="text-sm text-slate-500 mt-0.5">Background engines that feed the bots</p>
          </div>
          <button
            @click="fetchServices"
            class="size-8 inline-flex items-center justify-center rounded-md text-slate-500 hover:text-slate-200 hover:bg-raised transition-colors"
            title="Refresh"
            aria-label="Refresh services"
          >
            <span class="material-symbols-outlined text-[18px]">refresh</span>
          </button>
        </div>

        <div class="rounded-xl border border-white/[0.07] bg-surface overflow-hidden">
          <!-- Column labels -->
          <div class="hidden md:grid svc-grid h-10 px-5 items-center text-xs text-slate-500 border-b border-white/[0.06]">
            <span>Service</span><span>Status</span><span>Activity</span><span></span>
          </div>

          <div v-if="svcLoading" class="divide-y divide-white/[0.05]">
            <div v-for="n in 5" :key="n" class="h-[68px] px-5 flex items-center">
              <div class="h-4 w-40 rounded bg-raised"></div>
            </div>
          </div>

          <div v-else class="divide-y divide-white/[0.05]">

            <!-- Price engine -->
            <div class="svc-row">
              <div class="min-w-0">
                <p class="text-sm text-slate-100">Price engine</p>
                <p class="text-xs text-slate-500 mt-0.5">Live market data</p>
              </div>
              <StatusBadge :status="engineById('price_engine').status" />
              <p class="svc-meta">{{ engineById('price_engine').details || '—' }}</p>
              <div class="svc-actions">
                <button v-if="isRunning('price_engine')" @click="terminatePrice" :disabled="svcBusy['price_engine']" class="svc-btn text-loss">Terminate</button>
                <button v-else @click="togglePrice" :disabled="svcBusy['price_engine']" class="svc-btn">Start broker</button>
              </div>
            </div>

            <!-- Discover engine -->
            <div class="svc-row">
              <div class="min-w-0">
                <p class="text-sm text-slate-100">Discover engine</p>
                <p class="text-xs text-slate-500 mt-0.5">Opportunity discovery</p>
              </div>
              <StatusBadge :status="engineById('discover_engine').status" />
              <p class="svc-meta">{{ engineById('discover_engine').details || '—' }}</p>
              <div class="svc-actions">
                <button @click="toggleDiscover" :disabled="svcBusy['discover_engine']" class="svc-btn" :class="isRunning('discover_engine') ? 'text-loss' : ''">
                  {{ isRunning('discover_engine') ? 'Stop' : 'Start' }}
                </button>
              </div>
            </div>

            <!-- AI backtest agent -->
            <div class="svc-row">
              <div class="min-w-0">
                <p class="text-sm text-slate-100">AI backtest agent</p>
                <p class="text-xs text-slate-500 mt-0.5">Automated strategy search</p>
              </div>
              <StatusBadge :status="engineById('ai_backtest_engine').status" />
              <p class="svc-meta">
                {{ agentStatus?.count_today ?? 0 }} backtests today
                <span class="text-slate-600">·</span> last run {{ agentStatus?.last_run_date || '—' }}
                <template v-if="agentStatus?.resume_at"><span class="text-slate-600">·</span> resumes {{ agentStatus.resume_at }}</template>
              </p>
              <div class="svc-actions">
                <button v-if="isPaused('ai_backtest_engine')" @click="resumeAgent" :disabled="svcBusy['ai_backtest_engine']" class="svc-btn">Resume</button>
                <button v-else-if="isRunning('ai_backtest_engine')" @click="pauseAgent" :disabled="svcBusy['ai_backtest_engine']" class="svc-btn">Pause</button>
                <button
                  @click="toggleAgent"
                  :disabled="svcBusy['ai_backtest_engine']"
                  class="svc-btn"
                  :class="isRunning('ai_backtest_engine') || isPaused('ai_backtest_engine') ? 'text-loss' : ''"
                >{{ isRunning('ai_backtest_engine') || isPaused('ai_backtest_engine') ? 'Stop' : 'Start' }}</button>
              </div>
            </div>

            <!-- Daily digest -->
            <div class="svc-row">
              <div class="min-w-0">
                <p class="text-sm text-slate-100">Daily digest</p>
                <p class="text-xs text-slate-500 mt-0.5">Discord market summaries</p>
              </div>
              <StatusBadge :status="digestStatus?.running ? 'Running' : 'Stopped'" />
              <p class="svc-meta">
                Morning {{ fmtDateTime(digestStatus?.last_morning_at) || '—' }}
                <span class="text-slate-600">·</span> evening {{ fmtDateTime(digestStatus?.last_evening_at) || '—' }}
              </p>
              <div class="svc-actions">
                <button @click="sendDigestNow" :disabled="svcBusy['daily_digest_engine']" class="svc-btn">Send now</button>
                <button @click="toggleDigest" :disabled="svcBusy['daily_digest_engine']" class="svc-btn" :class="digestStatus?.running ? 'text-loss' : ''">
                  {{ digestStatus?.running ? 'Stop' : 'Start' }}
                </button>
              </div>
            </div>

            <!-- Nexus graph engine -->
            <div class="svc-row">
              <div class="min-w-0">
                <p class="text-sm text-slate-100">Nexus graph engine</p>
                <p class="text-xs text-slate-500 mt-0.5">Knowledge graph builder</p>
              </div>
              <StatusBadge :status="engineById('nexus_graph_engine').status" />
              <div class="min-w-0">
                <div v-if="nexusProgress() != null" class="flex items-center gap-3">
                  <div class="h-1 w-24 rounded-full bg-raised overflow-hidden shrink-0">
                    <div class="h-full rounded-full bg-slate-300 progress-fill" :style="{ transform: `scaleX(${nexusProgress() / 100})` }"></div>
                  </div>
                  <span class="text-xs text-slate-300 tabular-nums">{{ nexusProgress() }}%</span>
                </div>
                <p class="svc-meta mt-1 truncate" :title="nexusPhase() || ''">{{ nexusPhase() || (nexusProgress() == null ? 'No build in progress' : '') }}</p>
              </div>
              <div class="svc-actions">
                <button @click="toggleNexus" :disabled="svcBusy['nexus_graph_engine']" class="svc-btn" :class="isRunning('nexus_graph_engine') ? 'text-loss' : ''">
                  {{ isRunning('nexus_graph_engine') ? 'Stop' : 'Start' }}
                </button>
              </div>
            </div>

          </div>
        </div>
      </section>

      <!-- ── Re-run onboarding ──────────────────────────────────────────── -->
      <section class="mt-12 pt-6 border-t border-white/[0.06] flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div class="min-w-0">
          <p class="text-sm text-slate-200">Re-run onboarding</p>
          <p class="text-sm text-slate-500 mt-0.5">Add another model, link a brokerage, or spin up a new instance</p>
        </div>
        <RouterLink to="/onboarding" class="svc-btn shrink-0 self-start sm:self-auto">Open onboarding</RouterLink>
      </section>

    </main>
  </AppShell>

  <!-- ── Start agent modal ──────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showStartAgentModal"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60"
        @click.self="closeStartAgent"
      >
        <div class="modal-panel w-full max-w-md bg-surface border border-white/[0.08] rounded-2xl shadow-[0_24px_48px_rgba(0,0,0,0.5)] overflow-hidden">
          <div class="flex items-start gap-3 px-6 pt-5 pb-4">
            <div class="min-w-0">
              <h2 class="text-base font-medium text-slate-100">Start AI backtest agent</h2>
              <p class="text-sm text-slate-500 mt-1">Optionally give this run a special request</p>
            </div>
            <button @click="closeStartAgent" class="ml-auto size-8 inline-flex items-center justify-center rounded-md text-slate-500 hover:text-slate-200 hover:bg-raised transition-colors shrink-0" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="px-6 pb-5">
            <label for="agent-request" class="block text-sm text-slate-300 mb-2">Special request <span class="text-slate-500">(optional)</span></label>
            <textarea
              id="agent-request"
              v-model="startAgentRequest"
              rows="3"
              placeholder="e.g. Include Nexus graph analysis, focus on high-volatility stocks"
              class="w-full bg-background-dark border border-white/[0.08] rounded-lg px-3 py-2.5 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-white/25 transition-colors resize-none"
              @keydown.ctrl.enter="confirmStartAgent"
            ></textarea>
            <p class="text-xs text-slate-500 mt-2">Passed to the agent's strategy-generation model every cycle</p>
          </div>

          <div class="px-6 py-4 flex items-center justify-end gap-2 border-t border-white/[0.06]">
            <button @click="closeStartAgent" class="h-9 px-4 rounded-lg text-sm text-slate-400 hover:text-slate-100 hover:bg-raised transition-colors">Cancel</button>
            <button
              @click="confirmStartAgent"
              class="h-9 px-4 rounded-lg bg-primary text-background-dark text-sm font-medium hover:bg-white transition-colors shadow-[inset_0_1px_0_rgba(255,255,255,0.4)]"
            >Start agent</button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.svc-grid,
.svc-row {
  grid-template-columns: minmax(0, 1.1fr) 112px minmax(0, 1.7fr) 176px;
  column-gap: 16px;
}
.svc-row {
  display: grid;
  align-items: center;
  min-height: 68px;
  padding: 12px 20px;
}
.svc-meta {
  font-size: 12px;
  line-height: 16px;
  color: #85858b;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.svc-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.svc-btn {
  height: 32px;
  padding: 0 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: #ededed;
  background: #1e1e20;
  border: 1px solid rgba(237, 237, 237, 0.07);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
  white-space: nowrap;
  transition: background-color 140ms cubic-bezier(0.22, 1, 0.36, 1), transform 120ms cubic-bezier(0.22, 1, 0.36, 1);
}
.svc-btn:hover:not(:disabled) { background: #26262a; }
.svc-btn:active:not(:disabled) { transform: scale(0.98); }
.svc-btn:disabled { opacity: 0.4; cursor: default; }
.svc-btn.text-loss { color: #e5484d; }

.progress-fill {
  transform-origin: left center;
  transition: transform 500ms cubic-bezier(0.22, 1, 0.36, 1);
}

/* Small screens: name and status on one line, activity below, actions last. */
@media (max-width: 767px) {
  .svc-row {
    grid-template-columns: minmax(0, 1fr) auto;
    row-gap: 8px;
  }
  .svc-row > :nth-child(3) { grid-column: 1 / -1; }
  .svc-row > :nth-child(4) { grid-column: 1 / -1; justify-content: flex-start; }
}

/* One short entrance for the page content. */
.dash > * { animation: dash-in 360ms cubic-bezier(0.22, 1, 0.36, 1) both; }
.dash > :nth-child(2) { animation-delay: 40ms; }
.dash > :nth-child(3) { animation-delay: 80ms; }
.dash > :nth-child(4) { animation-delay: 120ms; }
@keyframes dash-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) { .dash > * { animation: none; } }

.fade-enter-active, .fade-leave-active { transition: opacity 180ms cubic-bezier(0.22, 1, 0.36, 1); }
.fade-enter-active .modal-panel, .fade-leave-active .modal-panel { transition: transform 220ms cubic-bezier(0.22, 1, 0.36, 1); }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.fade-enter-from .modal-panel, .fade-leave-to .modal-panel { transform: translateY(6px); }
</style>
