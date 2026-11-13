<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

// ── State ───────────────────────────────────────────────────────────────────
const runs        = ref([])
const page        = ref(1)
const perPage     = ref(20)
const total       = ref(0)
const totalPages  = ref(1)
const loading     = ref(false)
const agentRunning = ref(false)
const agentPaused  = ref(false)
const svcBusy      = ref(false)

// Start-agent modal
const showStartAgentModal = ref(false)
const startAgentRequest   = ref('')

// Unpause modal
const showUnpauseModal    = ref(false)
const unpauseCustomMins   = ref(15)
const unpauseUseCustom    = ref(false)

const UNPAUSE_PRESETS = [
  { label: 'Now',    minutes: 0  },
  { label: '5 min',  minutes: 5  },
  { label: '15 min', minutes: 15 },
  { label: '30 min', minutes: 30 },
  { label: '1 hr',   minutes: 60 },
]

// Scheduled-unpause timer
const scheduledUnpauseAt      = ref(null)   // Date when it fires
const scheduledUnpauseTotalMs = ref(0)      // total duration ms
const unpauseTimerHandle      = ref(null)   // setInterval handle
const unpauseProgress         = ref(0)      // 0-100 (pct elapsed)
const unpauseRemaining        = ref(0)      // seconds remaining

// SVG ring constants  r=18, cx/cy=22, viewBox 44×44
const RING_C = 113.097  // 2 * π * 18

let pollTimer = null

const PER_PAGE_OPTIONS = [10, 20, 50, 100]

// ── Fetch ───────────────────────────────────────────────────────────────────
async function fetchRuns(p = page.value) {
  loading.value = true
  try {
    const params = new URLSearchParams({ page: String(p), per_page: String(perPage.value) })
    const res = await fetch(`${API_BASE}/agent/runs?${params}`, { headers: authHeaders() })
    if (!res.ok) return
    const data = await res.json()
    runs.value       = data.runs || []
    total.value      = data.total ?? 0
    totalPages.value = data.total_pages ?? 1
    page.value       = data.page ?? p
  } catch { /* non-critical */ } finally {
    loading.value = false
  }
}

async function fetchAgentStatus() {
  try {
    const res = await fetch(`${API_BASE}/agent/control`, { headers: authHeaders() })
    if (!res.ok) return
    const data = await res.json()
    agentRunning.value = !!(data.running && !data.paused)
    agentPaused.value  = !!(data.running && data.paused)

    // If agent resumed externally while our timer was counting, cancel timer
    if (!agentPaused.value && scheduledUnpauseAt.value) {
      cancelUnpauseTimer()
    }
  } catch { /* non-critical */ }
}

// ── Agent controls ───────────────────────────────────────────────────────────
function openStartAgent() {
  startAgentRequest.value = ''
  showStartAgentModal.value = true
}
function closeStartAgent() {
  showStartAgentModal.value = false
}

async function agentPost(payload) {
  svcBusy.value = true
  try {
    await fetch(`${API_BASE}/agent/control`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(payload),
    })
    await fetchAgentStatus()
  } catch { /* non-critical */ } finally {
    svcBusy.value = false
  }
}

async function confirmStartAgent() {
  closeStartAgent()
  const sr = startAgentRequest.value.trim() || null
  await agentPost({ running: true, ...(sr ? { special_request: sr } : {}) })
}

function stopAgent()  { agentPost({ running: false }) }
function pauseAgent() { agentPost({ paused: true }) }

async function resumeAgentNow() {
  await agentPost({ paused: false })
}

// ── Unpause modal ────────────────────────────────────────────────────────────
function openUnpauseModal() {
  unpauseCustomMins.value = 15
  unpauseUseCustom.value  = false
  showUnpauseModal.value  = true
}
function closeUnpauseModal() {
  showUnpauseModal.value = false
}

async function confirmUnpause(minutes) {
  closeUnpauseModal()
  if (minutes === 0) {
    await resumeAgentNow()
    return
  }
  // Start client-side countdown
  const durationMs = minutes * 60 * 1000
  scheduledUnpauseAt.value      = new Date(Date.now() + durationMs)
  scheduledUnpauseTotalMs.value = durationMs
  unpauseProgress.value         = 0
  unpauseRemaining.value        = minutes * 60

  if (unpauseTimerHandle.value) clearInterval(unpauseTimerHandle.value)

  unpauseTimerHandle.value = setInterval(async () => {
    const now = Date.now()
    const end = scheduledUnpauseAt.value?.getTime() ?? now
    const remaining = Math.max(0, end - now)
    const elapsed   = durationMs - remaining
    unpauseProgress.value  = Math.min(100, (elapsed / durationMs) * 100)
    unpauseRemaining.value = Math.ceil(remaining / 1000)

    if (remaining <= 0) {
      clearInterval(unpauseTimerHandle.value)
      unpauseTimerHandle.value = null
      scheduledUnpauseAt.value = null
      unpauseProgress.value    = 0
      await resumeAgentNow()
    }
  }, 1000)
}

function cancelUnpauseTimer() {
  if (unpauseTimerHandle.value) {
    clearInterval(unpauseTimerHandle.value)
    unpauseTimerHandle.value = null
  }
  scheduledUnpauseAt.value = null
  unpauseProgress.value    = 0
  unpauseRemaining.value   = 0
}

function fmtCountdown(secs) {
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

async function forceStopRun(logId) {
  try {
    await fetch(`${API_BASE}/agent/runs/${logId}/force-stop`, {
      method: 'POST', headers: authHeaders(),
    })
    await fetchRuns()
  } catch { /* non-critical */ }
}

async function refresh() {
  await Promise.all([fetchRuns(), fetchAgentStatus()])
}

// ── Pagination ───────────────────────────────────────────────────────────────
function goToPage(p) {
  if (p < 1 || p > totalPages.value) return
  fetchRuns(p)
}

function onPerPageChange() {
  fetchRuns(1)
}

const pageNumbers = computed(() => {
  const total = totalPages.value
  const cur   = page.value
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const pages = new Set([1, total, cur])
  for (let d = -2; d <= 2; d++) {
    const v = cur + d
    if (v >= 1 && v <= total) pages.add(v)
  }
  const sorted = [...pages].sort((a, b) => a - b)
  const result = []
  let prev = null
  for (const p of sorted) {
    if (prev !== null && p - prev > 1) result.push('...')
    result.push(p)
    prev = p
  }
  return result
})

// ── Stepper helpers ─────────────────────────────────────────────────────────
// Status as a dot and a word. The green ring is only for a run or stage that
// is actually running; a pass is a plain green dot.
const STATUS_META = {
  running:   { dot: 'ui-dot-live',  text: 'text-slate-200', label: 'Running'   },
  passed:    { dot: 'bg-gain',      text: 'text-slate-200', label: 'Passed'    },
  failed:    { dot: 'ui-dot-error', text: 'text-slate-300', label: 'Failed'    },
  tossed:    { dot: 'ui-dot-warn',  text: 'text-slate-300', label: 'Tossed'    },
  duplicate: { dot: 'ui-dot-off',   text: 'text-slate-500', label: 'Duplicate' },
  stopped:   { dot: 'ui-dot-off',   text: 'text-slate-500', label: 'Stopped'   },
  error:     { dot: 'ui-dot-error', text: 'text-slate-300', label: 'Error'     },
}

function stageMeta(status) {
  return STATUS_META[status] || STATUS_META.stopped
}

function runMeta(run) {
  return STATUS_META[run.status] || STATUS_META.stopped
}

function fmtDateTime(v) {
  if (!v) return '—'
  try {
    const d = new Date(v)
    if (isNaN(d.getTime())) return '—'
    return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
  } catch { return '—' }
}

function fmtPnl(v) {
  if (v == null) return null
  const n = parseFloat(v)
  return (n >= 0 ? '+$' : '-$') + Math.abs(n).toFixed(2)
}
function fmtPct(v) {
  if (v == null) return null
  const n = parseFloat(v)
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'
}
function pnlColor(v) {
  return parseFloat(v) >= 0 ? 'text-gain' : 'text-loss'
}

// Cycle grouping — group runs by cycle_id
const cycles = computed(() => {
  const map = new Map()
  for (const run of runs.value) {
    const cid = run.cycle_id || run.id
    if (!map.has(cid)) {
      map.set(cid, { cycle_id: cid, started_at: run.created_at, strategies: [] })
    }
    map.get(cid).strategies.push(run)
  }
  return [...map.values()]
})

// ── Polling ─────────────────────────────────────────────────────────────────
function startPoll() { if (!pollTimer) pollTimer = setInterval(refresh, 5000) }
function stopPoll()  { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }

onMounted(async () => {
  await refresh()
  startPoll()
})
onUnmounted(() => {
  stopPoll()
  cancelUnpauseTimer()
})
</script>

<template>
  <AppShell>
    <main class="ui-page">

      <!-- Header -->
      <header>
        <!-- Title row -->
        <div class="flex items-start justify-between gap-4">
          <div class="min-w-0">
            <h1 class="ui-page-title">AI agent runs</h1>
            <p class="ui-page-sub hidden sm:block">Strategy attempts by the AI backtest agent: live progress, stage results and outcomes</p>
          </div>
          <!-- Agent status, always visible top-right -->
          <span
            class="shrink-0 mt-1 inline-flex items-center gap-2 text-sm"
            :class="agentRunning ? 'text-slate-200' : agentPaused ? 'text-slate-300' : 'text-slate-500'"
          >
            <span
              class="ui-dot"
              :class="agentRunning ? 'ui-dot-live' : agentPaused ? 'ui-dot-warn' : 'ui-dot-off'"
            ></span>
            {{ agentRunning ? 'Running' : agentPaused ? 'Paused' : 'Stopped' }}
          </span>
        </div>

        <!-- Controls row -->
        <div class="mt-6 flex items-center gap-2 flex-wrap">
          <!-- Start -->
          <button
            v-if="!agentRunning && !agentPaused"
            @click="openStartAgent"
            :disabled="svcBusy"
            class="ui-btn-primary h-8 px-3 text-[13px]"
          >
            Start
          </button>

          <!-- Pause (only when running) -->
          <button
            v-if="agentRunning"
            @click="pauseAgent"
            :disabled="svcBusy"
            class="ui-btn"
          >
            Pause
          </button>

          <!-- Unpause / Resume (only when paused and no timer running) -->
          <button
            v-if="agentPaused && !scheduledUnpauseAt"
            @click="openUnpauseModal"
            :disabled="svcBusy"
            class="ui-btn"
          >
            Unpause
          </button>

          <!-- Scheduled-unpause timer ring -->
          <div
            v-if="scheduledUnpauseAt"
            class="flex items-center gap-2"
          >
            <!-- Ring + cancel button -->
            <div class="relative size-11 flex items-center justify-center">
              <svg
                class="absolute inset-0 -rotate-90"
                viewBox="0 0 44 44"
                width="44"
                height="44"
                aria-hidden="true"
              >
                <!-- Track -->
                <circle
                  cx="22" cy="22" r="18"
                  fill="none"
                  stroke="rgba(255,255,255,0.08)"
                  stroke-width="3"
                />
                <!-- Countdown arc (depletes as time passes) -->
                <circle
                  cx="22" cy="22" r="18"
                  fill="none"
                  stroke="#ededed"
                  stroke-width="3"
                  stroke-linecap="round"
                  :stroke-dasharray="RING_C"
                  :stroke-dashoffset="RING_C * (unpauseProgress / 100)"
                  class="transition-[stroke-dashoffset] duration-1000 ease-linear"
                />
              </svg>
              <!-- Cancel button in the centre -->
              <button
                @click="cancelUnpauseTimer"
                class="relative z-10 size-6 rounded-md flex items-center justify-center text-slate-400 hover:text-loss hover:bg-raised transition-colors duration-150 ease-out"
                title="Cancel scheduled resume"
                aria-label="Cancel scheduled resume"
              >
                <span class="material-symbols-outlined text-[14px]" style="font-variation-settings:'FILL' 1">stop</span>
              </button>
            </div>
            <!-- Countdown label -->
            <div class="flex flex-col">
              <span class="text-sm text-slate-200 tabular-nums leading-4">{{ fmtCountdown(unpauseRemaining) }}</span>
              <span class="text-xs text-slate-500 leading-4">until resume</span>
            </div>
          </div>

          <!-- Stop -->
          <button
            v-if="agentRunning || agentPaused"
            @click="stopAgent"
            :disabled="svcBusy"
            class="ui-btn text-loss"
          >
            Stop
          </button>

          <!-- Spacer -->
          <div class="flex-1"></div>

          <!-- Per-page -->
          <select
            v-model="perPage"
            @change="onPerPageChange"
            aria-label="Runs per page"
            class="ui-input w-auto h-8 pl-3 pr-8 text-[13px] tabular-nums cursor-pointer"
          >
            <option v-for="n in PER_PAGE_OPTIONS" :key="n" :value="n">{{ n }} per page</option>
          </select>
          <!-- Refresh -->
          <button
            @click="refresh"
            class="ui-icon-btn"
            title="Refresh"
            aria-label="Refresh runs"
          >
            <span class="material-symbols-outlined text-[18px]" :class="loading ? 'animate-spin' : ''">refresh</span>
          </button>
        </div>
      </header>

      <!-- Loading skeletons -->
      <div v-if="loading && runs.length === 0" class="mt-8 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4" aria-hidden="true">
        <div v-for="n in 3" :key="n" class="ui-card p-5 h-48">
          <div class="ui-skel h-4 w-40"></div>
          <div class="ui-skel h-3 w-24 mt-2"></div>
          <div class="ui-skel h-3 w-48 mt-8"></div>
          <div class="ui-skel h-3 w-36 mt-4"></div>
        </div>
      </div>

      <!-- Empty state -->
      <div v-else-if="!loading && runs.length === 0" class="mt-8 ui-card ui-empty">
        <div>
          <p class="text-slate-200">No agent runs yet</p>
          <p class="mt-1 text-slate-500">Start the AI backtest agent to see strategy attempts here</p>
        </div>
      </div>

      <!-- Cycles / cards -->
      <div v-else class="mt-8 space-y-10">
        <section v-for="cycle in cycles" :key="cycle.cycle_id" :aria-label="`Cycle started ${fmtDateTime(cycle.started_at)}`">

          <!-- Cycle header -->
          <div class="flex items-center gap-3 mb-3">
            <span class="text-xs text-slate-500 tabular-nums shrink-0">
              {{ fmtDateTime(cycle.started_at) }}
            </span>
            <div class="h-px flex-1 bg-white/[0.06]"></div>
          </div>

          <!-- Strategy cards grid -->
          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <article
              v-for="run in cycle.strategies"
              :key="run.id"
              class="ui-card p-5 flex flex-col gap-5"
            >
              <!-- Card header -->
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <p class="text-sm font-medium text-slate-100 truncate">{{ run.name || 'Unnamed strategy' }}</p>
                  <p class="text-xs text-slate-500 mt-0.5 tabular-nums">{{ fmtDateTime(run.created_at) }}</p>
                </div>
                <!-- Status -->
                <span
                  class="shrink-0 inline-flex items-center gap-2 text-xs"
                  :class="stageMeta(run.status).text"
                >
                  <span class="ui-dot" :class="stageMeta(run.status).dot"></span>
                  {{ stageMeta(run.status).label }}
                </span>
              </div>

              <!-- Vertical stepper -->
              <ol class="flex flex-col">
                <template v-for="(stage, si) in run.stages" :key="si">
                  <li class="flex gap-3">
                    <!-- Connector + node column -->
                    <div class="w-3 shrink-0 flex flex-col items-center pt-1.5">
                      <span
                        class="ui-dot"
                        :class="stage.status === 'running' && run.status !== 'running' ? 'ui-dot-off' : stageMeta(stage.status).dot"
                      ></span>
                      <!-- Line to next step -->
                      <div
                        v-if="si < run.stages.length - 1"
                        class="w-px flex-1 mt-1.5 bg-white/[0.08]"
                      ></div>
                    </div>

                    <!-- Stage content -->
                    <div class="pb-4 flex-1 min-w-0">
                      <p class="text-sm text-slate-200">{{ stage.label }}</p>
                      <!-- Stocks used -->
                      <p v-if="stage.stocks && stage.stocks.length" class="text-xs text-slate-500 mt-0.5 truncate">
                        {{ stage.stocks.join(', ') }}
                      </p>
                      <!-- P&L result -->
                      <p v-if="stage.pnl != null" class="flex items-baseline gap-2 mt-1 text-xs tabular-nums">
                        <span :class="pnlColor(stage.pnl)">{{ fmtPnl(stage.pnl) }}</span>
                        <span class="text-slate-500">{{ fmtPct(stage.pnl_pct) }}</span>
                      </p>
                      <!-- Details / verdict -->
                      <p v-if="stage.details" class="text-xs text-slate-500 mt-1 truncate">
                        {{ stage.details }}
                      </p>
                      <!-- Running indicator (only if parent run is still running) -->
                      <p v-else-if="stage.status === 'running' && run.status === 'running'" class="text-xs text-slate-400 mt-1">
                        In progress
                      </p>
                    </div>
                  </li>
                </template>

                <!-- No stages yet (queued) -->
                <li v-if="!run.stages || run.stages.length === 0" class="flex gap-3">
                  <div class="w-3 shrink-0 flex justify-center pt-1.5">
                    <span class="ui-dot ui-dot-off"></span>
                  </div>
                  <p class="pb-4 text-sm text-slate-500">Queued</p>
                </li>
              </ol>

              <!-- Final result footer -->
              <div
                v-if="run.final_result || (run.status === 'running' && !agentRunning && !agentPaused)"
                class="mt-auto pt-4 border-t border-white/[0.06]"
              >
                <p v-if="run.final_result" class="flex items-baseline gap-2 text-xs text-slate-400 leading-5">
                  <span
                    class="ui-dot relative -top-px"
                    :class="run.status === 'passed' ? 'bg-gain' : run.status === 'failed' ? 'ui-dot-error' : 'ui-dot-off'"
                  ></span>
                  {{ run.final_result }}
                </p>
                <!-- Stale running indicator with force-stop button -->
                <div v-if="run.status === 'running' && !agentRunning && !agentPaused" class="flex items-center justify-between gap-2">
                  <p class="text-xs text-amber-400">Agent stopped — run may be stale</p>
                  <button
                    @click="forceStopRun(run.id)"
                    class="ui-btn h-7 px-2.5 text-xs text-loss shrink-0"
                  >
                    Mark stopped
                  </button>
                </div>
              </div>
            </article>
          </div>
        </section>
      </div>

      <!-- Pagination -->
      <div v-if="totalPages > 1" class="flex flex-col sm:flex-row items-center justify-between gap-3 mt-8">
        <p class="text-xs text-slate-500 tabular-nums self-start sm:self-auto">{{ total }} runs <span class="text-slate-600">·</span> page {{ page }} of {{ totalPages }}</p>
        <div class="flex items-center gap-1 flex-wrap justify-center">
          <button
            @click="goToPage(page - 1)"
            :disabled="page <= 1"
            class="ui-icon-btn disabled:opacity-30 disabled:cursor-default"
            aria-label="Previous page"
          >
            <span class="material-symbols-outlined text-[18px]">chevron_left</span>
          </button>
          <div class="ui-seg">
            <template v-for="p in pageNumbers" :key="p">
              <span v-if="p === '...'" class="h-7 px-1 inline-flex items-center text-xs text-slate-600">…</span>
              <button
                v-else
                @click="goToPage(p)"
                class="ui-seg-btn min-w-7 px-2 tabular-nums"
                :class="p === page ? 'ui-seg-btn-active' : ''"
                :aria-current="p === page ? 'page' : undefined"
              >{{ p }}</button>
            </template>
          </div>
          <button
            @click="goToPage(page + 1)"
            :disabled="page >= totalPages"
            class="ui-icon-btn disabled:opacity-30 disabled:cursor-default"
            aria-label="Next page"
          >
            <span class="material-symbols-outlined text-[18px]">chevron_right</span>
          </button>
        </div>
      </div>

    </main>

    <!-- ── Start agent modal ───────────────────────────────────────────────── -->
    <Teleport to="body">
      <Transition name="modal-fade">
        <div
          v-if="showStartAgentModal"
          class="ui-overlay"
          @click.self="closeStartAgent"
        >
          <div class="modal-panel ui-modal max-w-md">
            <!-- Modal header -->
            <div class="ui-modal-head">
              <div class="min-w-0">
                <h2 class="text-base font-medium text-slate-100">Start AI backtest agent</h2>
                <p class="text-sm text-slate-500 mt-1">Optionally give a special instruction for strategy generation</p>
              </div>
            </div>

            <!-- Modal body -->
            <div class="ui-modal-body">
              <label for="ar-request" class="ui-field-label">
                Special request
                <span class="text-slate-500">(optional)</span>
              </label>
              <textarea
                id="ar-request"
                v-model="startAgentRequest"
                placeholder="e.g. Focus on high-volatility tech stocks, avoid leveraged ETFs"
                rows="4"
                class="ui-input resize-none"
                @keydown.ctrl.enter="confirmStartAgent"
              ></textarea>
              <p class="ui-help">Ctrl+Enter to start</p>
            </div>

            <!-- Modal footer -->
            <div class="ui-modal-foot">
              <button
                @click="closeStartAgent"
                class="ui-btn-ghost h-9 px-4"
              >Cancel</button>
              <button
                @click="confirmStartAgent"
                class="ui-btn-primary"
              >
                Start agent
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- ── Unpause / schedule resume modal ───────────────────────────────── -->
    <Teleport to="body">
      <Transition name="modal-fade">
        <div
          v-if="showUnpauseModal"
          class="ui-overlay"
          @click.self="closeUnpauseModal"
        >
          <div class="modal-panel ui-modal max-w-sm">
            <!-- Modal header -->
            <div class="ui-modal-head">
              <div class="min-w-0">
                <h2 class="text-base font-medium text-slate-100">Resume agent</h2>
                <p class="text-sm text-slate-500 mt-1">Resume now or schedule an automatic resume</p>
              </div>
              <button @click="closeUnpauseModal" class="ui-icon-btn ml-auto shrink-0" aria-label="Close">
                <span class="material-symbols-outlined text-[18px]">close</span>
              </button>
            </div>

            <!-- Modal body -->
            <div class="ui-modal-body space-y-5">
              <!-- Presets -->
              <div>
                <p class="ui-field-label">Resume in</p>
                <div class="flex flex-wrap gap-2">
                  <button
                    v-for="preset in UNPAUSE_PRESETS"
                    :key="preset.minutes"
                    @click="confirmUnpause(preset.minutes)"
                    :class="preset.minutes === 0
                      ? 'ui-btn-primary h-8 px-3 text-[13px]'
                      : 'ui-btn tabular-nums'"
                  >
                    {{ preset.label }}
                  </button>
                </div>
              </div>

              <!-- Custom delay input -->
              <div>
                <label for="ar-custom-delay" class="ui-field-label">Custom delay</label>
                <div class="flex items-center gap-2">
                  <input
                    id="ar-custom-delay"
                    v-model.number="unpauseCustomMins"
                    type="number"
                    min="1"
                    max="1440"
                    placeholder="minutes"
                    class="ui-input w-24 tabular-nums"
                    @keydown.enter="unpauseCustomMins > 0 && confirmUnpause(unpauseCustomMins)"
                  />
                  <span class="text-sm text-slate-500">minutes</span>
                  <button
                    @click="unpauseCustomMins > 0 && confirmUnpause(unpauseCustomMins)"
                    :disabled="!unpauseCustomMins || unpauseCustomMins < 1"
                    class="ui-btn ml-auto h-9"
                  >Schedule</button>
                </div>
              </div>
            </div>

            <!-- Modal footer -->
            <div class="ui-modal-foot">
              <button
                @click="closeUnpauseModal"
                class="ui-btn-ghost h-9 px-4"
              >Cancel</button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

  </AppShell>
</template>

<style scoped>
.modal-fade-enter-active, .modal-fade-leave-active { transition: opacity 180ms cubic-bezier(0.22, 1, 0.36, 1); }
.modal-fade-enter-active .modal-panel, .modal-fade-leave-active .modal-panel { transition: transform 220ms cubic-bezier(0.22, 1, 0.36, 1); }
.modal-fade-enter-from, .modal-fade-leave-to { opacity: 0; }
.modal-fade-enter-from .modal-panel, .modal-fade-leave-to .modal-panel { transform: translateY(6px); }
</style>
