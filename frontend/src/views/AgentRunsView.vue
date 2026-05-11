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
const STATUS_META = {
  running:   { icon: null,           spin: true,  color: 'text-sky-400',    bg: 'bg-sky-500/10 border-sky-500/30',    label: 'Running'   },
  passed:    { icon: 'check_circle', spin: false, color: 'text-emerald-400', bg: 'bg-emerald-500/10 border-emerald-500/30', label: 'Passed'  },
  failed:    { icon: 'cancel',       spin: false, color: 'text-red-400',    bg: 'bg-red-500/10 border-red-500/30',    label: 'Failed'    },
  tossed:    { icon: 'do_not_disturb_on', spin: false, color: 'text-amber-400', bg: 'bg-amber-500/10 border-amber-500/30', label: 'Tossed' },
  duplicate: { icon: 'content_copy', spin: false, color: 'text-slate-500',  bg: 'bg-slate-500/10 border-slate-500/20', label: 'Duplicate' },
  stopped:   { icon: 'stop_circle',  spin: false, color: 'text-slate-500',  bg: 'bg-slate-500/10 border-slate-500/20', label: 'Stopped'   },
  error:     { icon: 'error',        spin: false, color: 'text-red-400',    bg: 'bg-red-500/10 border-red-500/30',    label: 'Error'     },
}

function stageMeta(status) {
  return STATUS_META[status] || STATUS_META.stopped
}

function runMeta(run) {
  return STATUS_META[run.status] || STATUS_META.stopped
}

// Card border/accent color per overall status
const RUN_BORDER = {
  running:   'border-sky-500/20',
  passed:    'border-emerald-500/20',
  failed:    'border-red-500/20',
  tossed:    'border-amber-500/20',
  duplicate: 'border-slate-700',
  stopped:   'border-slate-700',
  error:     'border-red-500/20',
}
function runBorder(run) { return RUN_BORDER[run.status] || 'border-slate-700' }

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
  return parseFloat(v) >= 0 ? 'text-emerald-400' : 'text-red-400'
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
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10">

      <!-- Header -->
      <div class="mb-6">
        <!-- Title row -->
        <div class="flex items-start justify-between gap-3 mb-3">
          <div class="min-w-0">
            <h1 class="text-xl sm:text-2xl font-bold text-slate-100">AI Agent Runs</h1>
            <p class="text-slate-500 text-xs sm:text-sm mt-1 hidden sm:block">Strategy attempts by the AI Backtest Agent — live progress, stage results, and outcomes.</p>
          </div>
          <!-- Status pill always visible top-right -->
          <span
            class="shrink-0 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border"
            :class="agentRunning
              ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
              : agentPaused
                ? 'text-amber-400 bg-amber-500/10 border-amber-500/20'
                : 'text-slate-500 bg-slate-500/10 border-slate-700'"
          >
            <span
              class="size-1.5 rounded-full"
              :class="agentRunning ? 'bg-emerald-400 animate-pulse' : agentPaused ? 'bg-amber-400' : 'bg-slate-600'"
            ></span>
            {{ agentRunning ? 'Running' : agentPaused ? 'Paused' : 'Stopped' }}
          </span>
        </div>

        <!-- Controls row -->
        <div class="flex items-center gap-2 flex-wrap">
          <!-- Start -->
          <button
            v-if="!agentRunning && !agentPaused"
            @click="openStartAgent"
            :disabled="svcBusy"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border border-emerald-500/30 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 transition-colors disabled:opacity-50"
          >
            <span class="material-symbols-outlined text-[14px]">play_arrow</span>
            <span>Start</span>
          </button>

          <!-- Pause (only when running) -->
          <button
            v-if="agentRunning"
            @click="pauseAgent"
            :disabled="svcBusy"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border border-amber-500/30 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 transition-colors disabled:opacity-50"
          >
            <span class="material-symbols-outlined text-[14px]">pause</span>
            <span>Pause</span>
          </button>

          <!-- Unpause / Resume (only when paused and no timer running) -->
          <button
            v-if="agentPaused && !scheduledUnpauseAt"
            @click="openUnpauseModal"
            :disabled="svcBusy"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border border-sky-500/30 bg-sky-500/10 text-sky-400 hover:bg-sky-500/20 transition-colors disabled:opacity-50"
          >
            <span class="material-symbols-outlined text-[14px]">play_arrow</span>
            <span>Unpause</span>
          </button>

          <!-- Scheduled-unpause timer ring -->
          <div
            v-if="scheduledUnpauseAt"
            class="flex items-center gap-2"
          >
            <!-- Ring + stop button -->
            <div class="relative flex items-center justify-center" style="width:44px;height:44px">
              <svg
                class="absolute inset-0"
                style="transform: rotate(-90deg)"
                viewBox="0 0 44 44"
                width="44"
                height="44"
              >
                <!-- Track -->
                <circle
                  cx="22" cy="22" r="18"
                  fill="none"
                  stroke="rgba(148,163,184,0.12)"
                  stroke-width="3.5"
                />
                <!-- Countdown arc (depletes as time passes) -->
                <circle
                  cx="22" cy="22" r="18"
                  fill="none"
                  stroke="rgb(56,189,248)"
                  stroke-width="3.5"
                  stroke-linecap="round"
                  :stroke-dasharray="RING_C"
                  :stroke-dashoffset="RING_C * (unpauseProgress / 100)"
                  class="transition-all duration-1000 ease-linear"
                />
              </svg>
              <!-- Square stop / cancel button in centre -->
              <button
                @click="cancelUnpauseTimer"
                class="relative z-10 size-[22px] rounded-sm flex items-center justify-center bg-red-500/15 border border-red-500/40 text-red-400 hover:bg-red-500/30 transition-colors"
                title="Cancel scheduled resume"
              >
                <span class="material-symbols-outlined" style="font-size:12px;font-variation-settings:'FILL' 1">stop</span>
              </button>
            </div>
            <!-- Countdown label -->
            <div class="flex flex-col leading-none">
              <span class="text-[10px] text-sky-400 font-mono font-semibold">{{ fmtCountdown(unpauseRemaining) }}</span>
              <span class="text-[9px] text-slate-600 mt-0.5">resuming</span>
            </div>
          </div>

          <!-- Stop -->
          <button
            v-if="agentRunning || agentPaused"
            @click="stopAgent"
            :disabled="svcBusy"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border border-red-500/30 bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors disabled:opacity-50"
          >
            <span class="material-symbols-outlined text-[14px]">stop</span>
            <span>Stop</span>
          </button>

          <!-- Spacer -->
          <div class="flex-1"></div>

          <!-- Per-page -->
          <select
            v-model="perPage"
            @change="onPerPageChange"
            class="bg-surface border border-border-subtle rounded-lg px-2 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-primary/40"
          >
            <option v-for="n in PER_PAGE_OPTIONS" :key="n" :value="n">{{ n }}/page</option>
          </select>
          <!-- Refresh -->
          <button
            @click="refresh"
            class="p-1.5 rounded-lg border border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface transition-all"
            title="Refresh"
          >
            <span class="material-symbols-outlined text-[16px]" :class="loading ? 'animate-spin' : ''">refresh</span>
          </button>
        </div>
      </div>

      <!-- Loading skeletons -->
      <div v-if="loading && runs.length === 0" class="space-y-4">
        <div v-for="n in 3" :key="n" class="glass-card rounded-2xl h-48 animate-pulse"></div>
      </div>

      <!-- Empty state -->
      <div v-else-if="!loading && runs.length === 0" class="flex flex-col items-center justify-center py-24 text-center">
        <div class="size-16 rounded-2xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center mb-4">
          <span class="material-symbols-outlined text-amber-400 text-3xl">smart_toy</span>
        </div>
        <p class="text-slate-400 font-medium">No agent runs yet</p>
        <p class="text-slate-600 text-sm mt-1">Start the AI Backtest Agent to see strategy attempts here.</p>
      </div>

      <!-- Cycles / cards -->
      <div v-else class="space-y-8">
        <div v-for="cycle in cycles" :key="cycle.cycle_id">

          <!-- Cycle header -->
          <div class="flex items-center gap-2 mb-3">
            <div class="h-px flex-1 bg-border-subtle"></div>
            <span class="text-[10px] text-slate-600 font-mono uppercase tracking-wider shrink-0 px-1">
              {{ fmtDateTime(cycle.started_at) }}
            </span>
            <div class="h-px flex-1 bg-border-subtle"></div>
          </div>

          <!-- Strategy cards grid -->
          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <div
              v-for="run in cycle.strategies"
              :key="run.id"
              class="glass-card rounded-2xl p-5 flex flex-col gap-4 border"
              :class="runBorder(run)"
            >
              <!-- Card header -->
              <div class="flex items-start justify-between gap-3">
                <div class="flex items-center gap-3 min-w-0">
                  <div class="size-9 rounded-xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center shrink-0">
                    <span class="material-symbols-outlined text-amber-400 text-base">smart_toy</span>
                  </div>
                  <div class="min-w-0">
                    <p class="text-sm font-semibold text-slate-100 truncate">{{ run.name || 'Unnamed Strategy' }}</p>
                    <p class="text-[11px] text-slate-500 mt-0.5 font-mono">{{ fmtDateTime(run.created_at) }}</p>
                  </div>
                </div>
                <!-- Status badge -->
                <span
                  class="shrink-0 text-[10px] font-bold px-2 py-0.5 rounded-full border uppercase tracking-wide"
                  :class="stageMeta(run.status).color + ' ' + stageMeta(run.status).bg"
                >
                  {{ stageMeta(run.status).label }}
                </span>
              </div>

              <!-- Vertical stepper -->
              <div class="flex flex-col gap-0">
                <template v-for="(stage, si) in run.stages" :key="si">
                  <div class="flex gap-3">
                    <!-- Connector + icon column -->
                    <div class="flex flex-col items-center">
                      <!-- Icon -->
                      <div
                        class="size-7 rounded-full border flex items-center justify-center shrink-0 z-10"
                        :class="stageMeta(stage.status).bg"
                      >
                        <span
                          v-if="stageMeta(stage.status).spin && run.status === 'running'"
                          class="material-symbols-outlined text-[14px] animate-spin text-sky-400"
                        >progress_activity</span>
                        <span
                          v-else
                          class="material-symbols-outlined text-[14px]"
                          :class="stageMeta(stage.status).color"
                        >{{ stageMeta(stage.status).icon || 'radio_button_unchecked' }}</span>
                      </div>
                      <!-- Line to next step -->
                      <div
                        v-if="si < run.stages.length - 1"
                        class="w-px flex-1 mt-1 mb-1"
                        :class="stage.status === 'running' ? 'bg-sky-500/30' : (stage.status === 'passed' ? 'bg-emerald-500/30' : 'bg-slate-700')"
                      ></div>
                    </div>

                    <!-- Stage content -->
                    <div class="pb-3 flex-1 min-w-0">
                      <p class="text-xs font-semibold text-slate-300 leading-none mt-0.5">{{ stage.label }}</p>
                      <!-- Stocks used -->
                      <p v-if="stage.stocks && stage.stocks.length" class="text-[10px] text-slate-600 mt-1 truncate">
                        {{ stage.stocks.join(', ') }}
                      </p>
                      <!-- P&L result -->
                      <div v-if="stage.pnl != null" class="flex items-center gap-2 mt-1.5">
                        <span class="text-[11px] font-mono font-semibold" :class="pnlColor(stage.pnl)">
                          {{ fmtPnl(stage.pnl) }}
                        </span>
                        <span class="text-[10px] font-mono text-slate-500">{{ fmtPct(stage.pnl_pct) }}</span>
                      </div>
                      <!-- Details / verdict -->
                      <p v-if="stage.details" class="text-[10px] text-slate-600 mt-1 truncate">
                        {{ stage.details }}
                      </p>
                      <!-- Running indicator (only if parent run is still running) -->
                      <p v-else-if="stage.status === 'running' && run.status === 'running'" class="text-[10px] text-sky-500 mt-1 italic">
                        In progress...
                      </p>
                    </div>
                  </div>
                </template>

                <!-- No stages yet (queued) -->
                <div v-if="!run.stages || run.stages.length === 0" class="flex gap-3">
                  <div class="flex flex-col items-center">
                    <div class="size-7 rounded-full border border-slate-700 bg-slate-500/10 flex items-center justify-center shrink-0">
                      <span class="material-symbols-outlined text-[14px] text-slate-500 animate-pulse">hourglass_empty</span>
                    </div>
                  </div>
                  <div class="pb-3 flex-1">
                    <p class="text-xs text-slate-500 italic mt-0.5">Queued...</p>
                  </div>
                </div>
              </div>

              <!-- Final result footer -->
              <div
                v-if="run.final_result || (run.status === 'running' && !agentRunning && !agentPaused)"
                class="mt-auto pt-3 border-t border-border-subtle"
              >
                <p v-if="run.final_result" class="text-[11px] text-slate-500 leading-snug">
                  <span class="font-semibold" :class="run.status === 'passed' ? 'text-emerald-400' : run.status === 'failed' ? 'text-red-400' : 'text-slate-400'">
                    {{ run.status === 'passed' ? '✓' : run.status === 'failed' ? '✗' : '○' }}
                  </span>
                  {{ run.final_result }}
                </p>
                <!-- Stale running indicator with force-stop button -->
                <div v-if="run.status === 'running' && !agentRunning && !agentPaused" class="flex items-center justify-between gap-2">
                  <p class="text-[11px] text-amber-500/70 italic">Agent stopped — run may be stale</p>
                  <button
                    @click="forceStopRun(run.id)"
                    class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold border border-red-500/30 bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors shrink-0"
                  >
                    <span class="material-symbols-outlined text-[11px]">close</span>
                    Mark Stopped
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Pagination -->
      <div v-if="totalPages > 1" class="flex flex-col sm:flex-row items-center justify-between gap-3 mt-8">
        <p class="text-xs text-slate-500 self-start sm:self-auto">{{ total }} runs · page {{ page }} of {{ totalPages }}</p>
        <div class="flex items-center gap-1 flex-wrap justify-center">
          <button
            @click="goToPage(page - 1)"
            :disabled="page <= 1"
            class="px-2 py-1.5 rounded-lg border border-border-subtle text-xs text-slate-400 hover:text-slate-200 hover:bg-surface transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <span class="material-symbols-outlined text-[14px]">chevron_left</span>
          </button>
          <template v-for="p in pageNumbers" :key="p">
            <span v-if="p === '...'" class="px-1 text-slate-600 text-xs">…</span>
            <button
              v-else
              @click="goToPage(p)"
              class="min-w-[30px] px-2 py-1.5 rounded-lg border text-xs transition-colors"
              :class="p === page
                ? 'border-primary/40 bg-primary/10 text-primary font-semibold'
                : 'border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface'"
            >{{ p }}</button>
          </template>
          <button
            @click="goToPage(page + 1)"
            :disabled="page >= totalPages"
            class="px-2 py-1.5 rounded-lg border border-border-subtle text-xs text-slate-400 hover:text-slate-200 hover:bg-surface transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <span class="material-symbols-outlined text-[14px]">chevron_right</span>
          </button>
        </div>
      </div>

    </main>

    <!-- ── Start Agent modal ───────────────────────────────────────────────── -->
    <Teleport to="body">
      <Transition name="modal-fade">
        <div
          v-if="showStartAgentModal"
          class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
          @click.self="closeStartAgent"
        >
          <div class="w-full max-w-md bg-[#0d1117] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">
            <!-- Modal header -->
            <div class="flex items-center gap-3 px-6 py-4 border-b border-border-subtle">
              <div class="size-9 rounded-xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-amber-400 text-base">smart_toy</span>
              </div>
              <div>
                <p class="text-sm font-semibold text-slate-100">Start AI Backtest Agent</p>
                <p class="text-[11px] text-slate-500 mt-0.5">Optionally provide a special instruction for strategy generation.</p>
              </div>
            </div>

            <!-- Modal body -->
            <div class="px-6 py-5">
              <label class="block text-xs font-medium text-slate-400 mb-2">
                Special Request
                <span class="text-slate-600 font-normal ml-1">(optional)</span>
              </label>
              <textarea
                v-model="startAgentRequest"
                placeholder="e.g. Focus on high-volatility tech stocks, avoid leveraged ETFs…"
                rows="4"
                class="w-full bg-surface border border-border-subtle rounded-xl px-3 py-2.5 text-sm text-slate-200 placeholder-slate-600 resize-none focus:outline-none focus:border-primary/40 transition-colors"
                @keydown.ctrl.enter="confirmStartAgent"
              ></textarea>
              <p class="text-[10px] text-slate-600 mt-1.5">Ctrl+Enter to start</p>
            </div>

            <!-- Modal footer -->
            <div class="flex items-center justify-end gap-2 px-6 py-4 border-t border-border-subtle">
              <button
                @click="closeStartAgent"
                class="px-4 py-2 rounded-lg text-sm font-medium text-slate-400 hover:text-slate-200 hover:bg-surface border border-transparent hover:border-border-subtle transition-colors"
              >Cancel</button>
              <button
                @click="confirmStartAgent"
                class="px-4 py-2 rounded-lg text-sm font-semibold text-background-dark bg-emerald-500 hover:bg-emerald-400 transition-colors"
              >
                <span class="material-symbols-outlined text-[14px] align-middle mr-1">play_arrow</span>
                Start Agent
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- ── Unpause / Schedule Resume modal ───────────────────────────────── -->
    <Teleport to="body">
      <Transition name="modal-fade">
        <div
          v-if="showUnpauseModal"
          class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
          @click.self="closeUnpauseModal"
        >
          <div class="w-full max-w-sm bg-[#0d1117] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">
            <!-- Modal header -->
            <div class="flex items-center gap-3 px-6 py-4 border-b border-border-subtle">
              <div class="size-9 rounded-xl bg-sky-500/10 border border-sky-500/20 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-sky-400 text-base">play_circle</span>
              </div>
              <div>
                <p class="text-sm font-semibold text-slate-100">Resume Agent</p>
                <p class="text-[11px] text-slate-500 mt-0.5">Resume now or schedule an automatic resume.</p>
              </div>
              <button @click="closeUnpauseModal" class="ml-auto text-slate-600 hover:text-slate-300 transition-colors">
                <span class="material-symbols-outlined text-[18px]">close</span>
              </button>
            </div>

            <!-- Modal body -->
            <div class="px-6 py-5 space-y-4">
              <!-- Preset chips -->
              <div>
                <p class="text-[11px] text-slate-500 font-medium mb-2 uppercase tracking-wider">Resume in</p>
                <div class="flex flex-wrap gap-2">
                  <button
                    v-for="preset in UNPAUSE_PRESETS"
                    :key="preset.minutes"
                    @click="confirmUnpause(preset.minutes)"
                    class="px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors"
                    :class="preset.minutes === 0
                      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20'
                      : 'border-sky-500/30 bg-sky-500/10 text-sky-400 hover:bg-sky-500/20'"
                  >
                    {{ preset.label }}
                  </button>
                </div>
              </div>

              <!-- Custom delay input -->
              <div class="pt-1">
                <p class="text-[11px] text-slate-500 font-medium mb-2 uppercase tracking-wider">Custom delay</p>
                <div class="flex items-center gap-2">
                  <input
                    v-model.number="unpauseCustomMins"
                    type="number"
                    min="1"
                    max="1440"
                    placeholder="minutes"
                    class="w-24 bg-surface border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-sky-500/40 transition-colors"
                    @keydown.enter="unpauseCustomMins > 0 && confirmUnpause(unpauseCustomMins)"
                  />
                  <span class="text-xs text-slate-500">minutes</span>
                  <button
                    @click="unpauseCustomMins > 0 && confirmUnpause(unpauseCustomMins)"
                    :disabled="!unpauseCustomMins || unpauseCustomMins < 1"
                    class="px-3 py-2 rounded-lg text-xs font-semibold border border-sky-500/30 bg-sky-500/10 text-sky-400 hover:bg-sky-500/20 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >Schedule</button>
                </div>
              </div>
            </div>

            <!-- Modal footer -->
            <div class="flex items-center justify-end px-6 py-4 border-t border-border-subtle">
              <button
                @click="closeUnpauseModal"
                class="px-4 py-2 rounded-lg text-sm font-medium text-slate-400 hover:text-slate-200 hover:bg-surface border border-transparent hover:border-border-subtle transition-colors"
              >Cancel</button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

  </AppShell>
</template>

<style scoped>
.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: opacity 0.2s ease;
}
.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}
</style>
