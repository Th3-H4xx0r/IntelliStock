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
  return '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPnl(v) {
  if (v == null) return '—'
  const n = Number(v)
  return (n >= 0 ? '+' : '') + fmtMoney(n)
}

function pnlClass(v) {
  if (v == null) return 'text-slate-500'
  return Number(v) >= 0 ? 'text-emerald-400' : 'text-red-400'
}

const statusColor = {
  completed: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  finished:  'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  running:   'text-sky-400 bg-sky-500/10 border-sky-500/20',
  queued:    'text-amber-400 bg-amber-500/10 border-amber-500/20',
  stopped:   'text-red-400 bg-red-500/10 border-red-500/20',
  cancelled: 'text-red-400 bg-red-500/10 border-red-500/20',
  error:     'text-red-400 bg-red-500/10 border-red-500/20',
  failed:    'text-red-400 bg-red-500/10 border-red-500/20',
}

function btStatusClass(s) {
  const key = (s || '').toLowerCase()
  return statusColor[key] || 'text-slate-500 bg-slate-500/10 border-slate-700'
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
    <main class="flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-10 max-w-7xl">

      <!-- Header -->
      <div class="flex items-center justify-between gap-4 mb-8 flex-wrap">
        <div class="flex items-center gap-4">
          <div class="size-12 rounded-2xl bg-surface border border-border-subtle flex items-center justify-center shrink-0">
            <span class="material-symbols-outlined text-primary text-2xl">analytics</span>
          </div>
          <div>
            <h1 class="text-2xl font-bold">All Backtests</h1>
            <p class="text-xs text-slate-500 mt-0.5">{{ btTotal }} total across all instances</p>
          </div>
        </div>
        <button
          @click="fetchBacktests(btPage)"
          class="p-2 rounded-lg border border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface transition-all"
          title="Refresh"
        >
          <span class="material-symbols-outlined text-[18px]" :class="btLoading ? 'animate-spin' : ''">refresh</span>
        </button>
      </div>

      <!-- Table card -->
      <section class="glass-card rounded-2xl overflow-hidden">

        <!-- Table toolbar -->
        <div class="px-5 py-3 border-b border-border-subtle flex items-center justify-between gap-4 flex-wrap">
          <p class="text-xs font-bold uppercase tracking-widest text-slate-500">
            Backtests <span class="text-slate-700 ml-1">({{ btTotal }})</span>
          </p>
          <div class="flex items-center gap-2">
            <label class="text-xs text-slate-500 whitespace-nowrap">Per page</label>
            <select
              :value="btPerPage"
              @change="changePerPage($event.target.value)"
              class="bg-surface border border-border-subtle rounded-lg px-2 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-primary transition-colors cursor-pointer"
            >
              <option
                v-for="opt in PER_PAGE_OPTIONS"
                :key="opt"
                :value="opt"
                class="bg-[#0f1318]"
              >{{ opt }}</option>
            </select>
          </div>
        </div>

        <!-- Loading (initial) -->
        <div v-if="btLoading && !backtests.length" class="px-5 py-16 flex items-center justify-center gap-2 text-slate-500 text-sm">
          <span class="material-symbols-outlined animate-spin text-lg">progress_activity</span>
          Loading backtests...
        </div>

        <!-- Empty -->
        <div v-else-if="!backtests.length" class="px-5 py-16 flex flex-col items-center gap-2 text-center">
          <span class="material-symbols-outlined text-4xl text-slate-700">analytics</span>
          <p class="text-xs text-slate-600">No backtests found</p>
        </div>

        <!-- Table -->
        <div v-else class="overflow-x-auto">
          <table class="w-full text-xs">
            <thead>
              <tr class="border-b border-border-subtle text-left">
                <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider">ID</th>
                <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider">Instance</th>
                <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider">Stocks</th>
                <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider">Period</th>
                <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider">
                  <button
                    @click="toggleBtSort('completed_at')"
                    class="inline-flex items-center gap-1 hover:text-slate-300 transition-colors"
                  >
                    Date Completed
                    <span class="material-symbols-outlined text-[14px]">{{ btSortIcon('completed_at') }}</span>
                  </button>
                </th>
                <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider">Status</th>
                <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider">Elapsed</th>
                <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider text-right">
                  <button
                    @click="toggleBtSort('pnl')"
                    class="inline-flex items-center gap-1 hover:text-slate-300 transition-colors"
                  >
                    P&amp;L
                    <span class="material-symbols-outlined text-[14px]">{{ btSortIcon('pnl') }}</span>
                  </button>
                </th>
                <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider text-right">
                  <button
                    @click="toggleBtSort('pnl_percent')"
                    class="inline-flex items-center gap-1 hover:text-slate-300 transition-colors"
                  >
                    P&amp;L %
                    <span class="material-symbols-outlined text-[14px]">{{ btSortIcon('pnl_percent') }}</span>
                  </button>
                </th>
                <th class="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="bt in backtests"
                :key="bt.id"
                class="border-b border-border-subtle/50 hover:bg-surface/30 transition-colors"
              >
                <!-- ID -->
                <td class="px-4 py-3 font-mono text-slate-400">{{ bt.id }}</td>

                <!-- Instance -->
                <td class="px-4 py-3">
                  <button
                    v-if="instanceLabel(bt) !== '—'"
                    @click="router.push(`/instances/${instanceLabel(bt)}`)"
                    class="font-mono text-xs text-primary hover:underline truncate max-w-[120px] block text-left"
                    :title="instanceLabel(bt)"
                  >{{ instanceLabel(bt) }}</button>
                  <span v-else class="text-slate-600">—</span>
                </td>

                <!-- Stocks -->
                <td class="px-4 py-3">
                  <div class="flex flex-wrap gap-1">
                    <span
                      v-for="s in (bt.stocks || []).slice(0, 4)"
                      :key="s"
                      class="font-mono text-slate-300 bg-surface px-1.5 py-0.5 rounded"
                    >{{ s }}</span>
                    <span v-if="(bt.stocks || []).length > 4" class="text-slate-600">+{{ bt.stocks.length - 4 }}</span>
                  </div>
                </td>

                <!-- Period -->
                <td class="px-4 py-3 text-slate-400 whitespace-nowrap">
                  {{ bt.start_date || '?' }} → {{ bt.end_date || '?' }}
                </td>

                <!-- Date Completed -->
                <td class="px-4 py-3 text-slate-300 whitespace-nowrap font-mono">
                  {{ fmtDateTime(bt.completed_at) }}
                </td>

                <!-- Status + progress -->
                <td class="px-4 py-3">
                  <div class="space-y-1.5">
                    <span
                      class="px-2 py-0.5 rounded border font-semibold uppercase"
                      :class="btStatusClass(btProgress[bt.id]?.status || bt.status)"
                    >{{ btProgress[bt.id]?.status || bt.status || 'queued' }}</span>
                    <div
                      v-if="btProgress[bt.id]?.progress != null && ['running','queued','pending'].includes((btProgress[bt.id]?.status || '').toLowerCase())"
                      class="w-24"
                    >
                      <div class="h-1.5 bg-surface rounded-full overflow-hidden">
                        <div
                          class="h-full bg-sky-400 rounded-full transition-all duration-500"
                          :style="{ width: Math.min(100, Math.max(0, btProgress[bt.id].progress)) + '%' }"
                        ></div>
                      </div>
                      <p class="text-[10px] text-slate-500 mt-0.5">{{ Math.round(btProgress[bt.id].progress) }}%</p>
                    </div>
                    <!-- Nexus lookback training indicator -->
                    <div v-if="btProgress[bt.id]?.nexus_lookback" class="w-24 mt-1">
                      <div class="flex items-center gap-1 mb-0.5">
                        <span class="material-symbols-outlined text-violet-400 text-[11px] animate-pulse">hub</span>
                        <span class="text-[10px] text-violet-400 font-semibold">Lookback</span>
                      </div>
                      <div class="h-1.5 bg-surface rounded-full overflow-hidden">
                        <div
                          class="h-full bg-violet-500 rounded-full transition-all duration-500"
                          :style="{ width: (btProgress[bt.id].nexus_lookback.total > 0 ? Math.round(btProgress[bt.id].nexus_lookback.current / btProgress[bt.id].nexus_lookback.total * 100) : 0) + '%' }"
                        ></div>
                      </div>
                      <p class="text-[10px] text-slate-500 mt-0.5 font-mono">{{ btProgress[bt.id].nexus_lookback.current }}/{{ btProgress[bt.id].nexus_lookback.total }}d</p>
                    </div>
                  </div>
                </td>

                <!-- Elapsed -->
                <td class="px-4 py-3 text-slate-300 whitespace-nowrap font-mono">
                  {{ fmtElapsed(btProgress[bt.id]?.time_elapsed_seconds ?? bt.time_elapsed_seconds) }}
                </td>

                <!-- P&L -->
                <td class="px-4 py-3 text-right font-mono font-semibold" :class="pnlClass(bt.pnl)">
                  {{ fmtPnl(bt.pnl) }}
                </td>

                <!-- P&L % -->
                <td class="px-4 py-3 text-right font-mono" :class="pnlClass(bt.pnl_percent)">
                  {{ bt.pnl_percent != null ? (Number(bt.pnl_percent) >= 0 ? '+' : '') + Number(bt.pnl_percent).toFixed(2) + '%' : '—' }}
                </td>

                <!-- Actions -->
                <td class="px-4 py-3">
                  <div class="flex items-center gap-1.5 justify-end">
                    <template v-if="['running','queued','pending'].includes((btProgress[bt.id]?.status || bt.status || '').toLowerCase())">
                      <button @click="openBtConfirm(bt, 'pause')" title="Pause"
                        class="p-1 rounded-md text-violet-400 hover:bg-violet-500/10 transition-colors">
                        <span class="material-symbols-outlined text-[16px]">pause_circle</span>
                      </button>
                    </template>
                    <template v-if="(btProgress[bt.id]?.status || bt.status || '').toLowerCase() === 'paused'">
                      <button @click="openBtConfirm(bt, 'resume')" title="Resume"
                        class="p-1 rounded-md text-sky-400 hover:bg-sky-500/10 transition-colors">
                        <span class="material-symbols-outlined text-[16px]">play_circle</span>
                      </button>
                    </template>
                    <template v-if="['running','queued','pending','paused'].includes((btProgress[bt.id]?.status || bt.status || '').toLowerCase())">
                      <button @click="openBtConfirm(bt, 'stop')" title="Stop"
                        class="p-1 rounded-md text-red-400 hover:bg-red-500/10 transition-colors">
                        <span class="material-symbols-outlined text-[16px]">stop_circle</span>
                      </button>
                    </template>
                    <button
                      @click="router.push({ name: 'backtest-detail', params: { id: bt.id }, query: { from: '/backtests' } })"
                      class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold text-primary hover:bg-primary/10 border border-primary/20 transition-colors whitespace-nowrap"
                    >
                      <span class="material-symbols-outlined text-[12px]">open_in_new</span>
                      View
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Pagination -->
        <div
          v-if="btTotalPages > 1 || backtests.length > 0"
          class="px-5 py-3 border-t border-border-subtle flex items-center justify-between gap-4 flex-wrap"
        >
          <p class="text-xs text-slate-500">
            Page {{ btPage }} of {{ btTotalPages }}
            <span class="ml-1 text-slate-600">({{ btTotal }} total)</span>
          </p>
          <div class="flex items-center gap-1.5">
            <button
              @click="fetchBacktests(btPage - 1)"
              :disabled="btPage <= 1 || btLoading"
              class="p-1.5 rounded-lg border border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface transition-all disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <span class="material-symbols-outlined text-[16px]">chevron_left</span>
            </button>
            <template v-for="p in btTotalPages" :key="p">
              <button
                v-if="p === 1 || p === btTotalPages || Math.abs(p - btPage) <= 1"
                @click="fetchBacktests(p)"
                :disabled="btLoading"
                class="min-w-[28px] h-7 px-1.5 rounded-lg text-xs font-medium transition-all disabled:opacity-50"
                :class="p === btPage
                  ? 'bg-primary/20 text-primary border border-primary/30'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-surface border border-transparent'"
              >{{ p }}</button>
              <span
                v-else-if="p === btPage - 2 || p === btPage + 2"
                class="text-slate-600 text-xs px-0.5"
              >…</span>
            </template>
            <button
              @click="fetchBacktests(btPage + 1)"
              :disabled="btPage >= btTotalPages || btLoading"
              class="p-1.5 rounded-lg border border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface transition-all disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <span class="material-symbols-outlined text-[16px]">chevron_right</span>
            </button>
          </div>
        </div>

      </section>
    </main>
  </AppShell>

  <!-- ── Action Confirm Modal ──────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showBtConfirm"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeBtConfirm"
      >
        <div class="relative w-full max-w-sm bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">
          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle">
            <div class="flex items-center gap-3">
              <div class="size-9 rounded-xl flex items-center justify-center shrink-0"
                :class="{
                  'bg-red-500/10 border border-red-500/20':    btConfirmAction === 'stop',
                  'bg-violet-500/10 border border-violet-500/20': btConfirmAction === 'pause',
                  'bg-sky-500/10 border border-sky-500/20':    btConfirmAction === 'resume',
                }"
              >
                <span class="material-symbols-outlined text-lg"
                  :class="{
                    'text-red-400':    btConfirmAction === 'stop',
                    'text-violet-400': btConfirmAction === 'pause',
                    'text-sky-400':    btConfirmAction === 'resume',
                  }"
                >{{ ACTION_META[btConfirmAction]?.icon || 'help' }}</span>
              </div>
              <h2 class="text-base font-bold">{{ btConfirmLabel }} Backtest</h2>
            </div>
            <button @click="closeBtConfirm" :disabled="btConfirmBusy" class="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>

          <div class="px-6 py-5 space-y-4">
            <p class="text-sm text-slate-400">{{ ACTION_META[btConfirmAction]?.desc }}</p>
            <p class="text-xs text-slate-600 font-mono">Backtest ID: {{ btConfirmId }}</p>

            <Transition name="slide-up">
              <div
                v-if="btConfirmMsg"
                class="rounded-lg px-4 py-3 text-sm font-medium"
                :class="btConfirmOk
                  ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                  : 'bg-red-500/10 border border-red-500/20 text-red-400'"
              >
                <span v-if="btConfirmBusy" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-base animate-spin">progress_activity</span>
                  {{ btConfirmMsg }}
                </span>
                <span v-else>{{ btConfirmMsg }}</span>
              </div>
            </Transition>
          </div>

          <div class="px-6 pb-6 flex gap-3">
            <button @click="closeBtConfirm" :disabled="btConfirmBusy"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40">
              Cancel
            </button>
            <button @click="executeBtConfirm" :disabled="btConfirmBusy"
              class="flex-1 py-2.5 rounded-lg text-sm font-bold transition-all disabled:opacity-50 flex items-center justify-center gap-2"
              :class="{
                'bg-red-500 hover:bg-red-400 text-white':         btConfirmAction === 'stop',
                'bg-violet-500 hover:bg-violet-400 text-white':   btConfirmAction === 'pause',
                'bg-primary hover:brightness-110 text-white':     btConfirmAction === 'resume',
              }"
            >
              <span v-if="btConfirmBusy" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              <span v-else class="material-symbols-outlined text-base">{{ ACTION_META[btConfirmAction]?.icon || 'check' }}</span>
              {{ btConfirmBusy ? 'Working...' : btConfirmLabel }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.slide-up-enter-active, .slide-up-leave-active { transition: all 0.2s ease; }
.slide-up-enter-from { opacity: 0; transform: translateY(6px); }
.slide-up-leave-to { opacity: 0; }
</style>
