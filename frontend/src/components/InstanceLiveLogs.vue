<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { getToken } from '../utils/auth.js'

// Per-instance broker live-tail. Same cursor contract as NexusLiveLogs but
// hits /instances/{id}/live-logs. Intentionally duplicated (~10% deltas) so
// the nexus log UI cannot regress when this one changes and vice versa.

const props = defineProps({
  instanceId: { type: [String, Number], required: true },
})

// ── Config ────────────────────────────────────────────────────────────────────
// Polling cadence: 5s while the broker is running, 15s once halted. Shorter
// than nexus (which uses 2s) because broker log volume is lower and a 5s
// cadence still feels near-real-time while being gentler on the API.
const POLL_INTERVAL_RUNNING = 5000
const POLL_INTERVAL_IDLE    = 15000
// Exponential backoff on network error — caps at 30s. Resets on success.
const ERROR_BACKOFF_SEQ     = [2000, 5000, 10000, 30000]
// Safety cap on in-memory lines; avoids UI getting wedged on a runaway build.
const MAX_LINES_IN_MEMORY   = 10000
// Virtual scroll tuning. LINE height must match the actual rendered row
// height: leading-5 (20px) + py-0.5 (4px total) = 24px. If you change the
// row padding in the template, update this constant or the viewport will
// drift during scroll.
const LOG_LINE_H    = 24
const LOG_WINDOW_SIZE = 150

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

// ── State ─────────────────────────────────────────────────────────────────────
// buildId is reused as "broker session id" — for live trading it maps to the
// instance id returned by the API. The variable name stays the same so the
// (otherwise-identical) poll logic below doesn't need adjusting.
const buildId       = ref(null)
const status        = ref('unknown')
const source        = ref('file')
const lines         = ref([])
const nextLine      = ref(0)
const totalLines    = ref(0)
const droppedCount  = ref(0)      // lines evicted past the MAX_LINES_IN_MEMORY cap
const open          = ref(false)
const loading       = ref(false)
const error         = ref(null)
const paused        = ref(false)
const isAutoScroll  = ref(true)
const search        = ref('')
const errorStreak   = ref(0)
const truncatedResponse = ref(false)

let pollHandle = null
let pollGen    = 0                // monotonically increases; races bail when stale
let abortController = null        // aborts in-flight fetch on unmount / pause / new build
let mounted    = true
let isProgrammaticScroll = false

// Virtual scroll
const logScrollTop = ref(0)
const scrollerRef  = ref(null)

// ── Poll loop ─────────────────────────────────────────────────────────────────
function schedule(ms) {
  if (!mounted || paused.value || !open.value) return
  if (pollHandle) clearTimeout(pollHandle)
  pollHandle = setTimeout(poll, ms)
}

async function fetchLogs(sinceLine, gen) {
  if (abortController) abortController.abort()
  abortController = new AbortController()
  const res = await fetch(
    `${API_BASE}/instances/${props.instanceId}/live-logs?since_line=${sinceLine}`,
    { headers: authHeaders(), signal: abortController.signal },
  )
  // After the await, if our generation is stale bail without touching state.
  if (gen !== pollGen || !mounted || paused.value || !open.value) return null
  return res
}

async function poll() {
  if (!mounted || paused.value || !open.value) return
  const gen = ++pollGen
  try {
    const res = await fetchLogs(nextLine.value || 0, gen)
    if (res === null) return
    if (res.status === 404) {
      // "No nexus graph builds found" — distinct from real failures.
      buildId.value = null
      status.value  = 'none'
      lines.value   = []
      nextLine.value = 0
      droppedCount.value = 0
      error.value   = null
      errorStreak.value = 0
      schedule(POLL_INTERVAL_IDLE)
      return
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    if (gen !== pollGen || !mounted || paused.value || !open.value) return
    errorStreak.value = 0
    error.value = null

    const responseBuildId = data.id
    const responseSource  = data.source || 'file'
    const isNewBuild = responseBuildId && responseBuildId !== buildId.value
    const sourceChanged = responseSource !== source.value && lines.value.length > 0

    if (isNewBuild || sourceChanged) {
      // Reseed unconditionally — simpler than trying to reconcile cursor
      // against a new index space. Also resets if the underlying source
      // swaps db↔file (indices aren't comparable across sources).
      lines.value   = []
      nextLine.value = 0
      droppedCount.value = 0
      truncatedResponse.value = false
      const reseed = await fetchLogs(0, gen)
      if (reseed === null) return
      if (!reseed.ok) throw new Error(`HTTP ${reseed.status}`)
      const seedData = await reseed.json()
      if (gen !== pollGen || !mounted || paused.value || !open.value) return
      buildId.value    = seedData.id || responseBuildId
      source.value     = seedData.source || responseSource
      status.value     = String(seedData.final_status || 'unknown').toLowerCase()
      totalLines.value = seedData.total_lines || 0
      truncatedResponse.value = !!seedData.truncated
      appendLines(seedData)
    } else {
      buildId.value    = responseBuildId || buildId.value
      source.value     = responseSource
      status.value     = String(data.final_status || 'unknown').toLowerCase()
      totalLines.value = data.total_lines || 0
      truncatedResponse.value = !!data.truncated
      appendLines(data)
    }

    // If the server truncated, poll again immediately to catch up — no cadence wait.
    if (truncatedResponse.value) {
      schedule(0)
      return
    }

    const effectiveStatus = String(status.value || '').toLowerCase()
    const nextMs = (effectiveStatus === 'running')
      ? POLL_INTERVAL_RUNNING
      : POLL_INTERVAL_IDLE
    schedule(nextMs)
  } catch (e) {
    if (e?.name === 'AbortError') return
    if (gen !== pollGen || !mounted) return
    error.value = String(e?.message || e || 'error')
    const idx = Math.min(errorStreak.value, ERROR_BACKOFF_SEQ.length - 1)
    const backoff = ERROR_BACKOFF_SEQ[idx]
    errorStreak.value += 1
    schedule(backoff)
  }
}

function appendLines(payload) {
  const incoming = Array.isArray(payload?.logs) ? payload.logs : []
  if (incoming.length === 0) {
    if (typeof payload?.next_line === 'number' && payload.next_line > (nextLine.value || 0)) {
      nextLine.value = payload.next_line
    }
    return
  }
  const merged = lines.value.concat(incoming)
  if (merged.length > MAX_LINES_IN_MEMORY) {
    const drop = merged.length - MAX_LINES_IN_MEMORY
    droppedCount.value += drop
    lines.value = merged.slice(drop)
  } else {
    lines.value = merged
  }
  if (typeof payload?.next_line === 'number') {
    // Monotonic floor — never retreat past what the server echoed, even if
    // totalLines regresses (e.g. log rotation). The backend already applies
    // max(since_line, total) so this is defense-in-depth.
    const serverCursor = payload.next_line
    nextLine.value = Math.max(nextLine.value || 0, serverCursor)
  }
  if (isAutoScroll.value) {
    nextTick(() => { scrollToBottom() })
  }
}

function start() {
  loading.value = true
  errorStreak.value = 0
  poll().finally(() => { loading.value = false })
}

function stop() {
  if (pollHandle) {
    clearTimeout(pollHandle)
    pollHandle = null
  }
  if (abortController) {
    try { abortController.abort() } catch (_e) { /* ignore */ }
    abortController = null
  }
}

function toggleOpen() {
  open.value = !open.value
  if (open.value) {
    start()
  } else {
    stop()
  }
}

function togglePause() {
  paused.value = !paused.value
  if (paused.value) {
    stop()
  } else {
    schedule(0)
  }
}

// ── Scroll handling ───────────────────────────────────────────────────────────
function onScroll(e) {
  // Ignore the scroll event fired by our own scrollToBottom() call — otherwise
  // a programmatic scroll could trip the nearBottom check on a virtualized
  // scroller whose scrollHeight is still mid-update, flipping isAutoScroll=false.
  if (isProgrammaticScroll) {
    isProgrammaticScroll = false
    return
  }
  logScrollTop.value = e.target.scrollTop
  const el = e.target
  const nearBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < 40
  isAutoScroll.value = nearBottom
}

function scrollToBottom() {
  const el = scrollerRef.value
  if (!el) return
  isProgrammaticScroll = true
  el.scrollTop = el.scrollHeight
  isAutoScroll.value = true
  // Clear the guard after the scroll event has had a chance to fire. Two
  // animation frames cover reflow + event dispatch across browsers.
  requestAnimationFrame(() => {
    requestAnimationFrame(() => { isProgrammaticScroll = false })
  })
}

// When the search query changes, reset virtual scroll to the top of the
// filtered view so the computed startIdx lands inside filteredLines bounds.
watch(search, () => {
  logScrollTop.value = 0
  nextTick(() => {
    if (scrollerRef.value) scrollerRef.value.scrollTop = 0
  })
})

// ── Filtering ─────────────────────────────────────────────────────────────────
const filteredLines = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return lines.value
  return lines.value.filter(l => l.toLowerCase().includes(q))
})

function parseLine(line) {
  if (!line) return { time: '', message: '' }
  const m = line.match(/^\[?(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]?\s*(.*)$/)
  if (m) return { time: m[1], message: m[2] }
  return { time: '', message: line }
}

function logLevelClass(line) {
  const l = (line || '').toLowerCase()
  if (l.includes(' error') || l.includes('traceback') || l.includes('failed')) return 'text-red-400'
  if (l.includes(' warn') || l.includes('warning') || l.includes('retry')) return 'text-amber-400'
  if (l.includes('completed') || l.includes(' ok ') || l.includes('success')) return 'text-emerald-400'
  return 'text-slate-300'
}

// ── Virtual scroll ────────────────────────────────────────────────────────────
const virtualLogs = computed(() => {
  const all = filteredLines.value
  if (all.length <= LOG_WINDOW_SIZE * 2) {
    return { useVirtual: false, items: all, startIdx: 0, totalHeight: 0, offsetY: 0 }
  }
  const startIdx  = Math.max(0, Math.floor(logScrollTop.value / LOG_LINE_H) - 10)
  const endIdx    = Math.min(all.length, startIdx + LOG_WINDOW_SIZE)
  return {
    useVirtual: true,
    items: all.slice(startIdx, endIdx),
    startIdx,
    totalHeight: all.length * LOG_LINE_H,
    offsetY: startIdx * LOG_LINE_H,
  }
})

// ── Controls ──────────────────────────────────────────────────────────────────
function copyLogs() {
  navigator.clipboard.writeText(lines.value.join('\n')).catch(() => {})
}

async function downloadFullLog() {
  // Fetch the full log (since_line=0). Note: server caps responses at 5000
  // lines; if the full log exceeds that we'd need to loop. For a typical
  // nexus build (~5k lines) this is fine; in pathological cases the user
  // can still fall back to the in-memory copy below.
  try {
    const res = await fetch(
      `${API_BASE}/instances/${props.instanceId}/live-logs?since_line=0`,
      { headers: authHeaders() },
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    const banner = data.truncated
      ? `# WARNING: server truncated log at 5000 lines. Total in source: ${data.total_lines}. For full log, read the file on the api container: /app/live_trading_logs/instance_${data.id}.log\n`
      : ''
    const full = banner + (Array.isArray(data.logs) ? data.logs.join('\n') : '')
    const blob = new Blob([full], { type: 'text/plain' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href = url
    a.download = `instance-${data.id ?? props.instanceId ?? 'latest'}${data.truncated ? '-first5k' : ''}.log`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  } catch (_e) {
    // Fallback: download what we have in memory. Label as partial if we've
    // evicted older lines past the in-memory cap.
    const banner = droppedCount.value > 0
      ? `# WARNING: UI truncated — first ${droppedCount.value} lines not available in-memory.\n`
      : ''
    const full = banner + lines.value.join('\n')
    const blob = new Blob([full], { type: 'text/plain' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href = url
    a.download = `instance-${buildId.value ?? props.instanceId ?? 'latest'}${droppedCount.value > 0 ? '-partial' : ''}.log`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }
}

// ── Derived ───────────────────────────────────────────────────────────────────
const statusDotClass = computed(() => {
  const s = String(status.value || '').toLowerCase()
  if (s === 'running') return 'bg-sky-400 animate-pulse'
  if (s === 'halted') return 'bg-slate-500'
  if (s === 'completed') return 'bg-slate-500'
  if (s === 'failed') return 'bg-red-500'
  return 'bg-slate-700'
})

const shortBuildId = computed(() => {
  // Instance ids are short-ish (integer or uuid); truncate to last 12 chars
  // for the header badge. Use nullish coalescing so a legitimate 0 id (or
  // similar falsy value from a test fixture) isn't displayed as "instance".
  if (!buildId.value) {
    const raw = props.instanceId ?? 'instance'
    return String(raw)
  }
  const s = String(buildId.value)
  return s.length > 12 ? s.slice(-12) : s
})

const friendlyStatus = computed(() => {
  const s = String(status.value || '').toLowerCase()
  if (s === 'running') return 'Running'
  if (s === 'halted') return 'Halted'
  if (s === 'completed') return 'Completed'
  if (s === 'failed') return 'Failed'
  if (s === 'none') return 'Not running'
  return 'Unknown'
})

const showReconnecting = computed(() => errorStreak.value > 0)

// Reset state if the component is reused with a different instanceId prop
// (e.g. router switches from /instances/A/live to /instances/B/live without
// unmounting). Without this, the stale `buildId` would survive until the
// next poll detected the new id, showing old data in the header meanwhile.
watch(() => props.instanceId, (newId, oldId) => {
  if (String(newId ?? '') === String(oldId ?? '')) return
  buildId.value = null
  lines.value = []
  nextLine.value = 0
  totalLines.value = 0
  droppedCount.value = 0
  status.value = 'unknown'
  source.value = 'file'
  error.value = null
  errorStreak.value = 0
  truncatedResponse.value = false
  pollGen += 1  // invalidate any in-flight poll targeting the old id
  if (abortController) {
    try { abortController.abort() } catch (_e) { /* ignore */ }
    abortController = null
  }
  if (open.value) {
    if (pollHandle) { clearTimeout(pollHandle); pollHandle = null }
    schedule(0)
  }
})

// ── Lifecycle ─────────────────────────────────────────────────────────────────
onMounted(() => {
  mounted = true
})

onUnmounted(() => {
  mounted = false
  // Invalidate any in-flight poll so their post-await state writes bail out.
  pollGen += 1
  stop()
})
</script>

<template>
  <div class="glass-card rounded-2xl border border-border-subtle overflow-hidden mb-6">
    <!-- Header (always visible) -->
    <div class="flex items-center justify-between gap-3 px-4 py-3 bg-[#0d1117]">
      <div class="flex items-center gap-2 min-w-0">
        <span class="size-2 rounded-full shrink-0" :class="statusDotClass"></span>
        <span class="text-xs font-semibold text-slate-300 font-mono truncate">
          instance-{{ shortBuildId }}.log
        </span>
        <span v-if="status && status !== 'none'" class="text-[10px] text-slate-500 shrink-0">· {{ friendlyStatus }}</span>
        <span v-if="lines.length" class="text-[10px] text-slate-600 font-mono shrink-0">{{ lines.length }} lines</span>
        <span v-if="totalLines > lines.length + droppedCount" class="text-[10px] text-slate-600 font-mono shrink-0">
          of {{ totalLines }}
        </span>
        <span v-if="droppedCount > 0" class="text-[10px] text-amber-500/70 shrink-0" title="Older lines evicted — use Download for full log">
          (last 10k)
        </span>
        <span v-if="source === 'db'" class="text-[10px] text-amber-500/70 shrink-0">
          (last 500 — log file not available)
        </span>
        <span v-if="showReconnecting" class="text-[10px] text-amber-500/80 shrink-0 inline-flex items-center gap-1">
          <span class="material-symbols-outlined text-[11px] animate-spin">progress_activity</span>
          reconnecting
        </span>
      </div>
      <div class="flex items-center gap-1.5 shrink-0">
        <input
          v-if="open && lines.length"
          v-model="search"
          placeholder="Search logs..."
          class="w-32 sm:w-44 bg-[#161b22] border border-[#30363d] rounded px-2 py-0.5 text-[11px] text-slate-300 placeholder-slate-600 focus:outline-none focus:border-sky-500/50 font-mono"
        />
        <button
          v-if="open && lines.length"
          @click="togglePause"
          class="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] transition-colors"
          :class="paused
            ? 'text-amber-400 bg-amber-500/10 hover:bg-amber-500/20'
            : 'text-slate-400 hover:text-slate-200 hover:bg-white/5'"
          :title="paused ? 'Resume live tail' : 'Pause live tail'"
        >
          <span class="material-symbols-outlined text-[13px]">{{ paused ? 'play_arrow' : 'pause' }}</span>
          <span class="hidden sm:inline">{{ paused ? 'Resume' : 'Pause' }}</span>
        </button>
        <button
          v-if="open && lines.length"
          @click="copyLogs"
          class="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] text-slate-400 hover:text-slate-200 hover:bg-white/5 transition-colors"
          title="Copy visible logs"
        >
          <span class="material-symbols-outlined text-[13px]">content_copy</span>
          <span class="hidden sm:inline">Copy</span>
        </button>
        <button
          v-if="open && lines.length"
          @click="downloadFullLog"
          class="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] text-slate-400 hover:text-slate-200 hover:bg-white/5 transition-colors"
          title="Download full log file"
        >
          <span class="material-symbols-outlined text-[13px]">download</span>
          <span class="hidden sm:inline">Download</span>
        </button>
        <button
          @click="toggleOpen"
          class="inline-flex items-center gap-1.5 px-3 py-1 rounded text-[11px] font-semibold border transition-colors"
          :class="open
            ? 'border-sky-500/30 bg-sky-500/10 text-sky-400 hover:bg-sky-500/20'
            : 'border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface'"
        >
          <span class="material-symbols-outlined text-[13px]">{{ open ? 'visibility_off' : 'terminal' }}</span>
          {{ open ? 'Hide Logs' : 'View Live Logs' }}
        </button>
      </div>
    </div>

    <!-- Body -->
    <div v-if="open" class="bg-[#0d1117] border-t border-[#21262d] relative">
      <!-- Loading -->
      <div v-if="loading && !lines.length" class="flex items-center gap-2 px-4 py-6 text-slate-500 text-xs font-mono">
        <span class="material-symbols-outlined text-[14px] animate-spin">progress_activity</span>
        Loading logs...
      </div>
      <!-- Empty -->
      <div
        v-else-if="lines.length === 0"
        class="px-4 py-6 text-slate-600 text-xs font-mono italic"
      >
        <template v-if="status === 'none'">
          Broker not running. Start the instance to begin live trading logs.
        </template>
        <template v-else-if="error">
          Can't reach logs endpoint. Retrying...
        </template>
        <template v-else>
          Waiting for log output...
        </template>
      </div>
      <!-- Log lines (virtualized) -->
      <div
        v-else
        ref="scrollerRef"
        class="overflow-y-auto font-mono text-[11px] leading-5"
        style="max-height: 480px"
        @scroll="onScroll"
      >
        <div v-if="virtualLogs.useVirtual" :style="{ height: virtualLogs.totalHeight + 'px', position: 'relative' }">
          <div :style="{ position: 'absolute', top: virtualLogs.offsetY + 'px', left: 0, right: 0 }">
            <div
              v-for="(line, idx) in virtualLogs.items"
              :key="virtualLogs.startIdx + idx"
              class="flex items-start gap-0 group hover:bg-white/[0.03] border-l-2 border-transparent hover:border-sky-500/40"
            >
              <span class="shrink-0 w-28 px-3 py-0.5 text-[10px] text-slate-600 select-none whitespace-nowrap">
                <template v-if="parseLine(line).time">
                  {{ parseLine(line).time.slice(5).replace(' ', ', ') }}
                </template>
              </span>
              <span class="flex-1 px-2 py-0.5 whitespace-pre-wrap break-all" :class="logLevelClass(line)">
                {{ parseLine(line).message || line }}
              </span>
            </div>
          </div>
        </div>
        <div v-else>
          <div
            v-for="(line, idx) in virtualLogs.items"
            :key="idx"
            class="flex items-start gap-0 group hover:bg-white/[0.03] border-l-2 border-transparent hover:border-sky-500/40"
          >
            <span class="shrink-0 w-28 px-3 py-0.5 text-[10px] text-slate-600 select-none whitespace-nowrap">
              <template v-if="parseLine(line).time">
                {{ parseLine(line).time.slice(5).replace(' ', ', ') }}
              </template>
            </span>
            <span class="flex-1 px-2 py-0.5 whitespace-pre-wrap break-all" :class="logLevelClass(line)">
              {{ parseLine(line).message || line }}
            </span>
          </div>
        </div>
      </div>
      <!-- "Jump to latest" pill — only when user has scrolled away from bottom -->
      <button
        v-if="open && lines.length && !isAutoScroll"
        @click="scrollToBottom"
        class="absolute bottom-3 right-3 inline-flex items-center gap-1 px-3 py-1 rounded-full text-[11px] font-semibold border border-sky-500/30 bg-sky-500/15 text-sky-300 hover:bg-sky-500/25 transition-colors shadow-md"
      >
        <span class="material-symbols-outlined text-[13px]">arrow_downward</span>
        Jump to latest
      </button>
    </div>
  </div>
</template>
