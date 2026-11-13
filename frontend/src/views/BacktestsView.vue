<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import { getToken } from '../utils/auth.js'

const router = useRouter()

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

// ── State ──────────────────────────────────────────────────────────────────
const backtests    = ref([])
const btPage       = ref(1)
const btPerPage    = ref(15)
const btTotal      = ref(0)
const btTotalPages = ref(0)
const btLoading    = ref(false)
const btSortBy     = ref('completed_at')
const btSortOrder  = ref('desc')

const PER_PAGE_OPTIONS = [10, 15, 25, 50, 100]

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

// ── Fetch ──────────────────────────────────────────────────────────────────
async function fetchBacktests(page = btPage.value) {
  btLoading.value = true
  try {
    const params = new URLSearchParams({
      page:       String(page),
      per_page:   String(btPerPage.value),
      sort_by:    btSortBy.value,
      sort_order: btSortOrder.value,
    })
    const res = await fetch(`${API_BASE}/backtests?${params.toString()}`, {
      headers: authHeaders(),
    })
    if (!res.ok) return
    const data        = await res.json()
    backtests.value   = data.backtests || []
    btTotal.value     = data.total ?? 0
    btTotalPages.value = data.total_pages ?? 1
    btPage.value      = data.page ?? page
  } catch { /* non-critical */ } finally {
    btLoading.value = false
  }
}

// ── Sorting ────────────────────────────────────────────────────────────────
function btSortIcon(field) {
  if (btSortBy.value !== field) return 'unfold_more'
  return btSortOrder.value === 'asc' ? 'arrow_upward' : 'arrow_downward'
}

async function toggleBtSort(field) {
  if (btSortBy.value === field) {
    btSortOrder.value = btSortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    btSortBy.value    = field
    btSortOrder.value = 'desc'
  }
  await fetchBacktests(1)
}

async function changePerPage(val) {
  btPerPage.value = Number(val)
  await fetchBacktests(1)
}

// ── Formatting ─────────────────────────────────────────────────────────────
function fmtDateTime(v) {
  if (!v) return '—'
  const n = Number(v)
  const d = Number.isFinite(n) ? new Date(n < 1e12 ? n * 1000 : n) : new Date(v)
  if (isNaN(d.getTime())) return String(v)
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

function fmtElapsed(seconds) {
  const n = Number(seconds)
  if (!Number.isFinite(n) || n < 0) return '—'
  const total = Math.floor(n)
  const d = Math.floor(total / 86400)
  const h = Math.floor((total % 86400) / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m ${s}s`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

function fmtMoney(v) {
  if (v == null) return '—'
  const n = Number(v)
  // Sign before the currency symbol: -$1,234.56, never $-1,234.56.
  return (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPnl(v) {
  if (v == null) return '—'
  const n = Number(v)
  return (n >= 0 ? '+' : '') + fmtMoney(n)
}

function pnlClass(v) {
  if (v == null) return 'text-slate-500'
  return Number(v) >= 0 ? 'text-gain' : 'text-loss'
}

// Status as a dot and a word. Green only for a run that is actually running.
const STATUS_UI = {
  running:             { dot: 'ui-dot-live',   text: 'text-slate-200', label: 'Running' },
  queued:              { dot: 'ui-dot-off',    text: 'text-slate-400', label: 'Queued' },
  pending:             { dot: 'ui-dot-off',    text: 'text-slate-400', label: 'Pending' },
  paused:              { dot: 'ui-dot-warn',   text: 'text-slate-300', label: 'Paused' },
  paused_llm_critical: { dot: 'ui-dot-warn',   text: 'text-slate-300', label: 'Paused · LLM failure' },
  completed:           { dot: 'bg-slate-400',  text: 'text-slate-300', label: 'Completed' },
  finished:            { dot: 'bg-slate-400',  text: 'text-slate-300', label: 'Finished' },
  stopped:             { dot: 'ui-dot-off',    text: 'text-slate-500', label: 'Stopped' },
  cancelled:           { dot: 'ui-dot-off',    text: 'text-slate-500', label: 'Cancelled' },
  error:               { dot: 'ui-dot-error',  text: 'text-slate-300', label: 'Error' },
  failed:              { dot: 'ui-dot-error',  text: 'text-slate-300', label: 'Failed' },
}

function btStatusUi(s) {
  const raw = String(s || '')
  const key = raw.toLowerCase()
  if (STATUS_UI[key]) return STATUS_UI[key]
  const label = raw.replace(/_/g, ' ')
  return { dot: 'ui-dot-off', text: 'text-slate-400', label: label ? label[0].toUpperCase() + label.slice(1) : 'Unknown' }
}

function instanceLabel(bt) {
  return bt.instance_id || bt.instance || '—'
}

// ── Progress polling ───────────────────────────────────────────────────────
const btProgress = ref({})
let btPollTimer  = null

async function pollRunningBacktests() {
  const running = backtests.value.filter(b => {
    const s = (btProgress.value[b.id]?.status || b.status || '').toLowerCase()
    return s === 'running' || s === 'queued' || s === 'pending'
  })
  if (!running.length) { stopBtPolling(); return }
  await Promise.all(running.map(async b => {
    try {
      const res = await fetch(`${API_BASE}/backtests/${b.id}/status`, { headers: authHeaders() })
      if (!res.ok) return
      const d = await res.json()
      btProgress.value = { ...btProgress.value, [b.id]: d }
      if (['completed', 'finished', 'stopped', 'failed', 'error', 'cancelled'].includes((d.status || '').toLowerCase())) {
        await fetchBacktests()
      }
    } catch { /* non-critical */ }
  }))
}

function startBtPolling() {
  if (!btPollTimer) btPollTimer = setInterval(pollRunningBacktests, 3000)
}

function stopBtPolling() {
  if (btPollTimer) { clearInterval(btPollTimer); btPollTimer = null }
}

// ── Inline backtest controls ───────────────────────────────────────────────
const showBtConfirm   = ref(false)
const btConfirmId     = ref(null)
const btConfirmAction = ref('')
const btConfirmBusy   = ref(false)
const btConfirmMsg    = ref('')
const btConfirmOk     = ref(false)
const btConfirmLabel  = ref('')

const ACTION_META = {
  stop:   { label: 'Stop',   color: 'red',    icon: 'stop_circle',  desc: 'This will permanently stop the backtest.' },
  pause:  { label: 'Pause',  color: 'violet', icon: 'pause_circle', desc: 'The backtest will be paused and can be resumed later.' },
  resume: { label: 'Resume', color: 'sky',    icon: 'play_circle',  desc: 'The backtest will continue from where it was paused.' },
}

function openBtConfirm(bt, action) {
  btConfirmId.value     = bt.id
  btConfirmAction.value = action
  btConfirmLabel.value  = ACTION_META[action]?.label || action
  btConfirmMsg.value    = ''
  btConfirmOk.value     = false
  btConfirmBusy.value   = false
  showBtConfirm.value   = true
}

function closeBtConfirm() {
  if (btConfirmBusy.value) return
  showBtConfirm.value = false
}

async function executeBtConfirm() {
  btConfirmBusy.value = true
  btConfirmMsg.value  = 'Working...'
  btConfirmOk.value   = false
  try {
    const res = await fetch(`${API_BASE}/backtests/${btConfirmId.value}/${btConfirmAction.value}`, {
      method: 'POST', headers: authHeaders(),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    btConfirmOk.value  = true
    btConfirmMsg.value = `${btConfirmLabel.value} successful.`
    const statusRes = await fetch(`${API_BASE}/backtests/${btConfirmId.value}/status`, { headers: authHeaders() })
    if (statusRes.ok) {
      const d = await statusRes.json()
      btProgress.value = { ...btProgress.value, [btConfirmId.value]: d }
    }
    await fetchBacktests()
    if (btConfirmAction.value === 'resume') startBtPolling()
    setTimeout(() => { showBtConfirm.value = false }, 1000)
  } catch (e) {
    btConfirmOk.value  = false
    btConfirmMsg.value = e.message || 'Something went wrong'
  } finally {
    btConfirmBusy.value = false
  }
}

onMounted(async () => {
  await fetchBacktests(1)
  startBtPolling()
})
onUnmounted(() => {
  stopBtPolling()
})
</script>

<template>
  <AppShell>
    <main class="ui-page">

      <!-- Header -->
      <header class="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <div class="min-w-0">
          <h1 class="ui-page-title">Backtests</h1>
          <p class="ui-page-sub tabular-nums">{{ btTotal }} across all instances</p>
        </div>
        <div class="flex items-center gap-2">
          <label for="bt-per-page" class="text-xs text-slate-500 whitespace-nowrap">Per page</label>
          <select
            id="bt-per-page"
            :value="btPerPage"
            @change="changePerPage($event.target.value)"
            class="ui-input w-auto h-8 pl-3 pr-8 text-[13px] tabular-nums cursor-pointer"
          >
            <option
              v-for="opt in PER_PAGE_OPTIONS"
              :key="opt"
              :value="opt"
              class="bg-surface"
            >{{ opt }}</option>
          </select>
          <button
            @click="fetchBacktests(btPage)"
            class="ui-icon-btn"
            title="Refresh"
            aria-label="Refresh backtests"
          >
            <span class="material-symbols-outlined text-[18px]" :class="btLoading ? 'animate-spin' : ''">refresh</span>
          </button>
        </div>
      </header>

      <!-- Table card -->
      <section class="mt-8 ui-card overflow-hidden">

        <!-- Loading (initial) -->
        <div v-if="btLoading && !backtests.length" class="ui-rows" aria-hidden="true">
          <div class="h-10 border-b border-white/[0.06]"></div>
          <div v-for="n in 6" :key="n" class="h-14 px-5 flex items-center gap-6">
            <div class="ui-skel h-4 w-28"></div>
            <div class="ui-skel h-4 w-40"></div>
            <div class="ui-skel h-4 w-24 ml-auto"></div>
          </div>
        </div>

        <!-- Empty -->
        <div v-else-if="!backtests.length" class="ui-empty">
          <p>No backtests found</p>
        </div>

        <!-- Table -->
        <div v-else class="overflow-x-auto">
          <table class="w-full min-w-[1040px] text-sm">
            <thead>
              <tr class="border-b border-white/[0.06] text-left text-xs text-slate-500">
                <th class="h-10 pl-5 pr-3 font-normal">Backtest</th>
                <th class="h-10 px-3 font-normal">Stocks</th>
                <th class="h-10 px-3 font-normal w-32">
                  <button
                    @click="toggleBtSort('completed_at')"
                    class="inline-flex items-center gap-1 hover:text-slate-200 transition-colors duration-150 ease-out"
                    :class="btSortBy === 'completed_at' ? 'text-slate-300' : ''"
                  >
                    Completed
                    <span class="material-symbols-outlined text-[14px]">{{ btSortIcon('completed_at') }}</span>
                  </button>
                </th>
                <th class="h-10 px-3 font-normal">Status</th>
                <th class="h-10 px-3 font-normal text-right">Elapsed</th>
                <th class="h-10 px-3 font-normal text-right">
                  <button
                    @click="toggleBtSort('pnl')"
                    class="inline-flex items-center gap-1 hover:text-slate-200 transition-colors duration-150 ease-out"
                    :class="btSortBy === 'pnl' ? 'text-slate-300' : ''"
                  >
                    P&amp;L
                    <span class="material-symbols-outlined text-[14px]">{{ btSortIcon('pnl') }}</span>
                  </button>
                </th>
                <th class="h-10 px-3 font-normal text-right">
                  <button
                    @click="toggleBtSort('pnl_percent')"
                    class="inline-flex items-center gap-1 hover:text-slate-200 transition-colors duration-150 ease-out"
                    :class="btSortBy === 'pnl_percent' ? 'text-slate-300' : ''"
                  >
                    P&amp;L %
                    <span class="material-symbols-outlined text-[14px]">{{ btSortIcon('pnl_percent') }}</span>
                  </button>
                </th>
                <th class="h-10 pl-3 pr-5"><span class="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody class="divide-y divide-white/[0.05]">
              <tr
                v-for="bt in backtests"
                :key="bt.id"
                class="hover:bg-white/[0.03] transition-colors duration-150 ease-out"
              >
                <!-- ID + instance -->
                <td class="pl-5 pr-3 py-2.5 align-middle">
                  <p class="text-sm text-slate-100 tabular-nums">#{{ bt.id }}</p>
                  <button
                    v-if="instanceLabel(bt) !== '—'"
                    @click="router.push(`/instances/${instanceLabel(bt)}`)"
                    class="mt-0.5 block max-w-[136px] truncate text-left text-xs text-slate-500 hover:text-slate-200 transition-colors duration-150 ease-out"
                    :title="instanceLabel(bt)"
                  >{{ instanceLabel(bt) }}</button>
                  <p v-else class="mt-0.5 text-xs text-slate-600">—</p>
                </td>

                <!-- Stocks + period -->
                <td class="px-3 py-2.5 align-middle">
                  <p class="text-sm text-slate-200 whitespace-nowrap">
                    <span
                      v-for="(s, i) in (bt.stocks || []).slice(0, 4)"
                      :key="s"
                    >{{ s }}<span v-if="i < Math.min(4, (bt.stocks || []).length) - 1" class="text-slate-600">, </span></span>
                    <span v-if="((bt.stocks_total ?? (bt.stocks || []).length) - (bt.stocks || []).length) > 0" class="ml-1.5 text-xs text-slate-500 tabular-nums">+{{ (bt.stocks_total ?? (bt.stocks || []).length) - (bt.stocks || []).length }}</span>
                  </p>
                  <p class="mt-0.5 text-xs text-slate-500 tabular-nums whitespace-nowrap">
                    {{ bt.start_date || '?' }} → {{ bt.end_date || '?' }}
                  </p>
                </td>

                <!-- Date completed -->
                <td class="px-3 py-2.5 align-middle text-sm text-slate-300 tabular-nums w-32">
                  {{ fmtDateTime(bt.completed_at) }}
                </td>

                <!-- Status + progress -->
                <td class="px-3 py-2.5 align-middle">
                  <span
                    class="inline-flex items-center gap-2 text-sm whitespace-nowrap"
                    :class="btStatusUi(btProgress[bt.id]?.status || bt.status || 'queued').text"
                  >
                    <span class="ui-dot" :class="btStatusUi(btProgress[bt.id]?.status || bt.status || 'queued').dot"></span>
                    {{ btStatusUi(btProgress[bt.id]?.status || bt.status || 'queued').label }}
                  </span>
                  <div
                    v-if="btProgress[bt.id]?.progress != null && ['running','queued','pending'].includes((btProgress[bt.id]?.status || '').toLowerCase())"
                    class="mt-1.5 flex items-center gap-2"
                  >
                    <span class="bar-track w-14"><span class="bar-fill bg-slate-300" :style="{ transform: `scaleX(${Math.min(100, Math.max(0, btProgress[bt.id].progress)) / 100})` }"></span></span>
                    <span class="text-xs text-slate-500 tabular-nums">{{ Math.round(btProgress[bt.id].progress) }}%</span>
                  </div>
                  <!-- Nexus lookback training indicator -->
                  <div v-if="btProgress[bt.id]?.nexus_lookback" class="mt-1.5 flex items-center gap-2">
                    <span class="bar-track w-14"><span class="bar-fill bg-slate-500" :style="{ transform: `scaleX(${btProgress[bt.id].nexus_lookback.total > 0 ? btProgress[bt.id].nexus_lookback.current / btProgress[bt.id].nexus_lookback.total : 0})` }"></span></span>
                    <span class="text-xs text-slate-500 tabular-nums whitespace-nowrap">Lookback {{ btProgress[bt.id].nexus_lookback.current }}/{{ btProgress[bt.id].nexus_lookback.total }}d</span>
                  </div>
                </td>

                <!-- Elapsed -->
                <td class="px-3 py-2.5 align-middle text-right text-sm text-slate-300 tabular-nums whitespace-nowrap">
                  {{ fmtElapsed(btProgress[bt.id]?.time_elapsed_seconds ?? bt.time_elapsed_seconds) }}
                </td>

                <!-- P&L -->
                <td class="px-3 py-2.5 align-middle text-right text-sm tabular-nums whitespace-nowrap" :class="pnlClass(bt.pnl)">
                  {{ fmtPnl(bt.pnl) }}
                </td>

                <!-- P&L % -->
                <td class="px-3 py-2.5 align-middle text-right text-sm tabular-nums whitespace-nowrap" :class="pnlClass(bt.pnl_percent)">
                  {{ bt.pnl_percent != null ? (Number(bt.pnl_percent) >= 0 ? '+' : '') + Number(bt.pnl_percent).toFixed(2) + '%' : '—' }}
                </td>

                <!-- Actions -->
                <td class="pl-3 pr-5 py-2.5 align-middle">
                  <div class="flex items-center gap-1 justify-end">
                    <template v-if="['running','queued','pending'].includes((btProgress[bt.id]?.status || bt.status || '').toLowerCase())">
                      <button @click="openBtConfirm(bt, 'pause')" title="Pause" aria-label="Pause backtest" class="ui-icon-btn">
                        <span class="material-symbols-outlined text-[18px]">pause</span>
                      </button>
                    </template>
                    <template v-if="['paused','paused_llm_critical'].includes((btProgress[bt.id]?.status || bt.status || '').toLowerCase())">
                      <button @click="openBtConfirm(bt, 'resume')" title="Resume" aria-label="Resume backtest" class="ui-icon-btn">
                        <span class="material-symbols-outlined text-[18px]">play_arrow</span>
                      </button>
                    </template>
                    <template v-if="['running','queued','pending','paused','paused_llm_critical'].includes((btProgress[bt.id]?.status || bt.status || '').toLowerCase())">
                      <button @click="openBtConfirm(bt, 'stop')" title="Stop" aria-label="Stop backtest" class="ui-icon-btn hover:text-loss">
                        <span class="material-symbols-outlined text-[18px]">stop</span>
                      </button>
                    </template>
                    <button
                      @click="router.push({ name: 'backtest-detail', params: { id: bt.id }, query: { from: '/backtests' } })"
                      class="ui-btn-ghost px-2.5"
                    >View</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Pagination -->
        <div
          v-if="btTotalPages > 1 || backtests.length > 0"
          class="h-14 px-5 border-t border-white/[0.06] flex items-center justify-between gap-4"
        >
          <p class="text-xs text-slate-500 tabular-nums">
            Page {{ btPage }} of {{ btTotalPages }}
            <span class="text-slate-600">·</span> {{ btTotal }} total
          </p>
          <div class="flex items-center gap-1">
            <button
              @click="fetchBacktests(btPage - 1)"
              :disabled="btPage <= 1 || btLoading"
              class="ui-icon-btn disabled:opacity-30 disabled:cursor-default"
              aria-label="Previous page"
            >
              <span class="material-symbols-outlined text-[18px]">chevron_left</span>
            </button>
            <div class="ui-seg">
              <template v-for="p in btTotalPages" :key="p">
                <button
                  v-if="p === 1 || p === btTotalPages || Math.abs(p - btPage) <= 1"
                  @click="fetchBacktests(p)"
                  :disabled="btLoading"
                  class="ui-seg-btn min-w-7 px-2 tabular-nums disabled:opacity-50"
                  :class="p === btPage ? 'ui-seg-btn-active' : ''"
                  :aria-current="p === btPage ? 'page' : undefined"
                >{{ p }}</button>
                <span
                  v-else-if="p === btPage - 2 || p === btPage + 2"
                  class="h-7 px-1 inline-flex items-center text-xs text-slate-600"
                >…</span>
              </template>
            </div>
            <button
              @click="fetchBacktests(btPage + 1)"
              :disabled="btPage >= btTotalPages || btLoading"
              class="ui-icon-btn disabled:opacity-30 disabled:cursor-default"
              aria-label="Next page"
            >
              <span class="material-symbols-outlined text-[18px]">chevron_right</span>
            </button>
          </div>
        </div>

      </section>
    </main>
  </AppShell>

  <!-- ── Action confirm modal ──────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showBtConfirm"
        class="ui-overlay"
        @click.self="closeBtConfirm"
      >
        <div class="modal-panel ui-modal max-w-sm">
          <div class="ui-modal-head">
            <div class="min-w-0">
              <h2 class="text-base font-medium text-slate-100">{{ btConfirmLabel }} backtest</h2>
              <p class="text-sm text-slate-500 mt-1 tabular-nums">Backtest #{{ btConfirmId }}</p>
            </div>
            <button @click="closeBtConfirm" :disabled="btConfirmBusy" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="ui-modal-body space-y-4">
            <p class="text-sm text-slate-400">{{ ACTION_META[btConfirmAction]?.desc }}</p>

            <Transition name="slide-up">
              <p
                v-if="btConfirmMsg"
                :class="btConfirmBusy
                  ? 'text-sm text-slate-400'
                  : btConfirmOk ? 'text-sm text-gain' : 'ui-error'"
              >
                <span v-if="btConfirmBusy" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                  {{ btConfirmMsg }}
                </span>
                <span v-else>{{ btConfirmMsg }}</span>
              </p>
            </Transition>
          </div>

          <div class="ui-modal-foot">
            <button @click="closeBtConfirm" :disabled="btConfirmBusy" class="ui-btn-ghost h-9 px-4">
              Cancel
            </button>
            <button
              @click="executeBtConfirm"
              :disabled="btConfirmBusy"
              :class="btConfirmAction === 'stop' ? 'ui-btn h-9 px-4 text-sm text-loss' : 'ui-btn-primary'"
            >
              <span v-if="btConfirmBusy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ btConfirmBusy ? 'Working…' : btConfirmLabel }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.bar-track {
  position: relative;
  display: block;
  height: 4px;
  border-radius: 999px;
  background: #1e1e20;
  overflow: hidden;
}
.bar-fill {
  position: absolute;
  inset: 0;
  border-radius: 999px;
  transform-origin: left center;
  transition: transform 500ms cubic-bezier(0.22, 1, 0.36, 1);
}

.fade-enter-active, .fade-leave-active { transition: opacity 180ms cubic-bezier(0.22, 1, 0.36, 1); }
.fade-enter-active .modal-panel, .fade-leave-active .modal-panel { transition: transform 220ms cubic-bezier(0.22, 1, 0.36, 1); }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.fade-enter-from .modal-panel, .fade-leave-to .modal-panel { transform: translateY(6px); }
.slide-up-enter-active, .slide-up-leave-active { transition: opacity 180ms cubic-bezier(0.22, 1, 0.36, 1), transform 180ms cubic-bezier(0.22, 1, 0.36, 1); }
.slide-up-enter-from { opacity: 0; transform: translateY(4px); }
.slide-up-leave-to { opacity: 0; }
</style>
