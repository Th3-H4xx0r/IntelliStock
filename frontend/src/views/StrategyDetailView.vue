<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import { getToken } from '../utils/auth.js'

const router = useRouter()
const route  = useRoute()
const strategyId = computed(() => route.params.id)

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
const strategy     = ref(null)
const agentResults = ref([])
const agentBestId  = ref(null)
const loading      = ref(true)
const btSortBy     = ref('created_at')
const btSortOrder  = ref('desc')

// ── Fetch ─────────────────────────────────────────────────────────────────────
async function fetchAll() {
  loading.value = true
  try {
    const [sRes, aRes, bRes] = await Promise.all([
      fetch(`${API_BASE}/strategies/${strategyId.value}`, { headers: authHeaders() }),
      fetch(`${API_BASE}/agent/results?limit=10000`, { headers: authHeaders() }),
      fetch(`${API_BASE}/agent/best`, { headers: authHeaders() }),
    ])
    if (sRes.ok) strategy.value = await sRes.json()
    if (aRes.ok) agentResults.value = (await aRes.json()).results || []
    if (bRes.ok) {
      const d = await bRes.json()
      agentBestId.value = (d && d.id != null) ? d.id : null
    }
  } catch { /* non-critical */ } finally {
    loading.value = false
  }
}

// ── Computed ──────────────────────────────────────────────────────────────────
const isAgentBest = computed(() => strategy.value?.id === agentBestId.value)

// All backtests for this strategy from AIBacktestingResults
const strategyBacktests = computed(() => {
  const sid = strategy.value?.id
  if (sid == null) return []
  return agentResults.value.filter(r => r.strategy_id === sid)
})

const sortedBacktests = computed(() => {
  const list = [...strategyBacktests.value]
  list.sort((a, b) => {
    let av, bv
    if (btSortBy.value === 'pnl')     { av = parseFloat(a.overall_profit) || 0; bv = parseFloat(b.overall_profit) || 0 }
    else if (btSortBy.value === 'pct') { av = parseFloat(a.pnl_percent) || 0;    bv = parseFloat(b.pnl_percent) || 0 }
    else { av = a.created_at || ''; bv = b.created_at || '' }
    if (btSortOrder.value === 'asc') return av > bv ? 1 : av < bv ? -1 : 0
    return av < bv ? 1 : av > bv ? -1 : 0
  })
  return list
})

const bestPnlBacktest = computed(() => {
  let best = null
  for (const r of strategyBacktests.value) {
    if (!best || parseFloat(r.overall_profit) > parseFloat(best.overall_profit)) best = r
  }
  return best
})

// ── Sub-strategy helpers ──────────────────────────────────────────────────────
function schemaFields(sub) {
  // Return interesting config fields, folding legacy conditions into config.
  const rows = []
  const config = { ...(sub.conditions || {}), ...(sub.config || {}) }
  for (const [k, v] of Object.entries(config)) {
    if (v !== null && v !== undefined && v !== '') rows.push({ key: k, val: v, section: 'config' })
  }
  return rows
}

function fmtVal(v) {
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (typeof v === 'object' && v !== null) return JSON.stringify(v)
  return String(v)
}

// ── Formatters ────────────────────────────────────────────────────────────────
function fmtPnl(v) {
  if (v == null) return '—'
  const n = parseFloat(v)
  return (n >= 0 ? '+$' : '-$') + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtPct(v) {
  if (v == null) return '—'
  const n = parseFloat(v)
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'
}
function pnlColor(v) {
  if (v == null) return 'text-slate-500'
  return parseFloat(v) >= 0 ? 'text-emerald-400' : 'text-red-400'
}
function fmtDateTime(v) {
  if (!v) return '—'
  try {
    const d = new Date(v)
    if (isNaN(d.getTime())) return '—'
    return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
  } catch { return '—' }
}
function fmtDate(v) {
  if (!v) return '—'
  try {
    const d = new Date(v)
    if (isNaN(d.getTime())) return '—'
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  } catch { return '—' }
}

function setBtSort(col) {
  if (btSortBy.value === col) btSortOrder.value = btSortOrder.value === 'desc' ? 'asc' : 'desc'
  else { btSortBy.value = col; btSortOrder.value = 'desc' }
}

function viewBacktest(bid) { if (bid != null) router.push(`/backtests/${bid}`) }

const PHASE_COLOR = { pre: 'text-sky-400 bg-sky-500/10 border-sky-500/20', post: 'text-purple-400 bg-purple-500/10 border-purple-500/20', entry: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20', exit: 'text-red-400 bg-red-500/10 border-red-500/20' }
function phaseClass(p) { return PHASE_COLOR[p] || 'text-slate-400 bg-slate-500/10 border-slate-600' }

// ── Backtest this strategy modal ───────────────────────────────────────────────
const showBtModal     = ref(false)
const btModalLoading  = ref(false)
const btModalBusy     = ref(false)
const btModalMsg      = ref('')
const btModalOk       = ref(false)
const allInstances    = ref([])    // all instances from API
const selectedInstId  = ref('')    // '' means "create new"
const newInstName     = ref('')

const GRANULARITIES = [
  { label: '1 min',  value: '60' },
  { label: '5 min',  value: '300' },
  { label: '15 min', value: '900' },
  { label: '1 hour', value: '3600' },
  { label: '1 day',  value: '86400' },
]
const btForm = ref({ stocks: '', start_date: '', end_date: '', granularity: '86400', initial_cash: '10000' })

// Instances already linked to this strategy (show highlighted at top)
const linkedInstances = computed(() =>
  allInstances.value.filter(i => i.strategy_id === strategy.value?.id)
)
// Instances with no strategy at all (available to link)
const freeInstances = computed(() =>
  allInstances.value.filter(i => i.strategy_id == null)
)

async function openBtModal() {
  btModalMsg.value  = ''
  btModalOk.value   = false
  btModalBusy.value = false
  btForm.value      = { stocks: '', start_date: '', end_date: '', granularity: '86400', initial_cash: '10000' }
  newInstName.value = ''
  showBtModal.value = true
  btModalLoading.value = true
  try {
    const res = await fetch(`${API_BASE}/instances`, { headers: authHeaders() })
    if (res.ok) allInstances.value = (await res.json()).instances || []
  } catch { /* non-critical */ } finally {
    btModalLoading.value = false
    // Default: first linked instance, else first free instance, else "create new"
    if (linkedInstances.value.length) selectedInstId.value = String(linkedInstances.value[0].id)
    else if (freeInstances.value.length) selectedInstId.value = String(freeInstances.value[0].id)
    else selectedInstId.value = ''
  }
}

function closeBtModal() {
  if (btModalBusy.value) return
  showBtModal.value = false
}

async function submitBtModal() {
  const f = btForm.value
  const stocks = f.stocks.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
  if (!stocks.length)     { btModalMsg.value = 'At least one stock is required'; return }
  if (!f.start_date)      { btModalMsg.value = 'Start date is required'; return }
  if (!f.end_date)        { btModalMsg.value = 'End date is required'; return }
  if (f.start_date >= f.end_date) { btModalMsg.value = 'End date must be after start date'; return }
  if (selectedInstId.value === '' && !newInstName.value.trim()) {
    btModalMsg.value = 'Instance name is required when creating a new one'; return
  }

  btModalBusy.value = true
  btModalMsg.value  = 'Working...'
  btModalOk.value   = false
  try {
    let instanceId = selectedInstId.value

    // Create new instance if needed
    if (!instanceId) {
      const newId = Math.floor(100000 + Math.random() * 900000).toString()
      const cr = await fetch(`${API_BASE}/instances`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ id: newId, name: newInstName.value.trim(), run_command: false }),
      })
      const cd = await cr.json().catch(() => ({}))
      if (!cr.ok) throw new Error(cd.detail || `HTTP ${cr.status}`)
      instanceId = String(cd.id || cd.instance_id || '')
      if (!instanceId) throw new Error('Instance created but no ID returned')
    }

    // Link strategy (skip if this instance is already linked to this strategy)
    const isAlreadyLinked = linkedInstances.value.some(i => String(i.id) === instanceId)
    if (!isAlreadyLinked) {
      const lr = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId)}/link-strategy`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ strategy_id: strategy.value.id }),
      })
      const ld = await lr.json().catch(() => ({}))
      if (!lr.ok) throw new Error(ld.detail || `HTTP ${lr.status}`)
    }

    // Create the backtest
    const br = await fetch(`${API_BASE}/backtests`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({
        instance_id:  instanceId,
        stocks,
        start_date:   f.start_date,
        end_date:     f.end_date,
        granularity:  String(f.granularity ?? '60'),
        initial_cash: parseFloat(f.initial_cash) || 10000,
      }),
    })
    const bd = await br.json().catch(() => ({}))
    if (!br.ok) throw new Error(bd.detail || `HTTP ${br.status}`)

    btModalOk.value  = true
    const btId = bd.id ?? bd.backtest_id
    btModalMsg.value = `Backtest #${btId} queued!`
    setTimeout(() => {
      showBtModal.value = false
      router.push(`/backtests/${btId}`)
    }, 1000)
  } catch (e) {
    btModalOk.value  = false
    btModalMsg.value = e.message || 'Something went wrong'
  } finally {
    btModalBusy.value = false
  }
}

onMounted(fetchAll)
</script>

<template>
  <AppShell>
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10">

      <!-- Top nav row -->
      <div class="flex items-center justify-between mb-6">
        <button
          @click="router.push('/strategies')"
          class="inline-flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-200 transition-colors"
        >
          <span class="material-symbols-outlined text-[16px]">arrow_back</span>
          All Strategies
        </button>
        <button
          v-if="strategy"
          @click="openBtModal"
          class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold border border-sky-500/30 bg-sky-500/10 text-sky-400 hover:bg-sky-500/20 transition-colors"
        >
          <span class="material-symbols-outlined text-[15px]">play_circle</span>
          Backtest this strategy
        </button>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="space-y-4">
        <div v-for="n in 3" :key="n" class="glass-card rounded-2xl animate-pulse" :style="{ height: n === 1 ? '96px' : '180px' }"></div>
      </div>

      <template v-else-if="strategy">

        <!-- ── Strategy header ─────────────────────────────────────────────── -->
        <div
          class="glass-card rounded-2xl p-5 mb-6 border overflow-hidden"
          :class="isAgentBest ? 'border-amber-500/30 bg-amber-500/5' : 'border-border-subtle'"
        >
          <div v-if="isAgentBest" class="h-0.5 w-full bg-gradient-to-r from-amber-500/60 via-amber-400 to-amber-500/60 -mx-5 -mt-5 mb-5" style="width: calc(100% + 40px)"></div>
          <div class="flex items-start justify-between gap-4 flex-wrap">
            <div class="flex items-center gap-3 min-w-0">
              <div
                class="size-12 rounded-2xl flex items-center justify-center shrink-0"
                :class="isAgentBest ? 'bg-amber-500/15 border border-amber-500/30' : 'bg-surface border border-border-subtle'"
              >
                <span class="material-symbols-outlined text-xl" :class="isAgentBest ? 'text-amber-400' : 'text-slate-400'">
                  {{ isAgentBest ? 'star' : 'schema' }}
                </span>
              </div>
              <div class="min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                  <h1 class="text-xl font-bold" :class="isAgentBest ? 'text-amber-300' : 'text-slate-100'">
                    {{ strategy.name }}
                  </h1>
                  <span
                    v-if="isAgentBest"
                    class="text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 border border-amber-500/30 uppercase tracking-wide"
                  >Agent Best</span>
                </div>
                <p class="text-sm text-slate-500 mt-0.5">
                  Strategy ID {{ strategy.id }} · {{ (strategy.strategies || []).length }} sub-strategies · {{ strategyBacktests.length }} backtests
                </p>
              </div>
            </div>

            <!-- Best backtest summary -->
            <div v-if="bestPnlBacktest" class="flex items-center gap-4 flex-wrap">
              <div class="text-right">
                <p class="text-[10px] text-slate-600 uppercase tracking-widest mb-0.5">Best P&amp;L</p>
                <p class="text-lg font-bold font-mono" :class="pnlColor(bestPnlBacktest.overall_profit)">
                  {{ fmtPnl(bestPnlBacktest.overall_profit) }}
                </p>
              </div>
              <div class="text-right">
                <p class="text-[10px] text-slate-600 uppercase tracking-widest mb-0.5">Best P&amp;L%</p>
                <p class="text-lg font-bold font-mono" :class="pnlColor(bestPnlBacktest.pnl_percent)">
                  {{ fmtPct(bestPnlBacktest.pnl_percent) }}
                </p>
              </div>
              <button
                @click="viewBacktest(bestPnlBacktest.backtest_id)"
                class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold border border-primary/30 bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
              >
                <span class="material-symbols-outlined text-[13px]">analytics</span>
                View Best Backtest
              </button>
            </div>
          </div>
        </div>

        <!-- ── Sub-strategies ──────────────────────────────────────────────── -->
        <div class="mb-8">
          <h2 class="text-xs font-semibold text-slate-400 uppercase tracking-widest mb-4">Sub-strategies ({{ (strategy.strategies || []).length }})</h2>
          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <div
              v-for="(sub, si) in strategy.strategies"
              :key="si"
              class="glass-card rounded-2xl p-4 border border-border-subtle flex flex-col gap-3"
            >
              <!-- Sub-strategy header -->
              <div class="flex items-start justify-between gap-2">
                <div>
                  <p class="text-sm font-semibold text-slate-100 font-mono">{{ sub.strategy }}</p>
                  <p class="text-[10px] text-slate-600 mt-0.5">position {{ sub.execution_position ?? si }}</p>
                </div>
                <span
                  class="shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded border uppercase tracking-wide"
                  :class="phaseClass(sub.decision_phase)"
                >{{ sub.decision_phase || 'pre' }}</span>
              </div>

              <!-- Core fields -->
              <div class="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
                <div>
                  <span class="text-slate-600">Weight</span>
                  <p class="text-slate-300 font-mono">{{ sub.weight ?? '—' }}</p>
                </div>
                <div>
                  <span class="text-slate-600">Scope</span>
                  <p class="text-slate-300 font-mono">{{ sub.execution_scope || '—' }}</p>
                </div>
              </div>

              <!-- Config -->
              <div v-if="schemaFields(sub).length" class="border-t border-border-subtle pt-3 space-y-1.5">
                <p class="text-[10px] text-slate-600 uppercase tracking-widest mb-2">Config</p>
                <div v-for="f in schemaFields(sub)" :key="f.key" class="flex items-start justify-between gap-2">
                  <div class="flex items-center gap-1 min-w-0">
                    <span class="text-[11px] text-slate-500 font-mono truncate">{{ f.key }}</span>
                  </div>
                  <span class="text-[11px] text-slate-300 font-mono text-right shrink-0 max-w-[120px] truncate" :title="fmtVal(f.val)">{{ fmtVal(f.val) }}</span>
                </div>
              </div>
              <div v-else class="text-[11px] text-slate-700 italic">No config</div>
            </div>

            <div v-if="!(strategy.strategies || []).length" class="col-span-full py-10 text-center">
              <p class="text-slate-600 text-sm">No sub-strategies defined.</p>
            </div>
          </div>
        </div>

        <!-- ── Backtests table ─────────────────────────────────────────────── -->
        <div>
          <h2 class="text-xs font-semibold text-slate-400 uppercase tracking-widest mb-4">
            Backtests ({{ strategyBacktests.length }})
          </h2>

          <div v-if="!strategyBacktests.length" class="glass-card rounded-2xl border border-border-subtle p-12 text-center">
            <span class="material-symbols-outlined text-slate-700 text-4xl">analytics</span>
            <p class="text-slate-500 text-sm mt-3">No backtests recorded for this strategy yet.</p>
          </div>

          <div v-else class="glass-card rounded-2xl border border-border-subtle overflow-hidden">
            <div class="overflow-x-auto">
              <table class="w-full min-w-[640px] text-sm">
                <thead>
                  <tr class="border-b border-border-subtle bg-surface/50">
                    <th
                      class="px-4 py-3 text-left text-[11px] font-semibold text-slate-500 uppercase tracking-widest cursor-pointer hover:text-slate-300 transition-colors select-none"
                      @click="setBtSort('created_at')"
                    >
                      <span class="flex items-center gap-1">
                        Date
                        <span v-if="btSortBy === 'created_at'" class="material-symbols-outlined text-[12px] text-primary">
                          {{ btSortOrder === 'desc' ? 'arrow_downward' : 'arrow_upward' }}
                        </span>
                      </span>
                    </th>
                    <th class="px-4 py-3 text-left text-[11px] font-semibold text-slate-500 uppercase tracking-widest">Stocks</th>
                    <th class="px-4 py-3 text-left text-[11px] font-semibold text-slate-500 uppercase tracking-widest">Period</th>
                    <th
                      class="px-4 py-3 text-right text-[11px] font-semibold text-slate-500 uppercase tracking-widest cursor-pointer hover:text-slate-300 transition-colors select-none"
                      @click="setBtSort('pnl')"
                    >
                      <span class="flex items-center justify-end gap-1">
                        P&amp;L
                        <span v-if="btSortBy === 'pnl'" class="material-symbols-outlined text-[12px] text-primary">
                          {{ btSortOrder === 'desc' ? 'arrow_downward' : 'arrow_upward' }}
                        </span>
                      </span>
                    </th>
                    <th
                      class="px-4 py-3 text-right text-[11px] font-semibold text-slate-500 uppercase tracking-widest cursor-pointer hover:text-slate-300 transition-colors select-none"
                      @click="setBtSort('pct')"
                    >
                      <span class="flex items-center justify-end gap-1">
                        P&amp;L%
                        <span v-if="btSortBy === 'pct'" class="material-symbols-outlined text-[12px] text-primary">
                          {{ btSortOrder === 'desc' ? 'arrow_downward' : 'arrow_upward' }}
                        </span>
                      </span>
                    </th>
                    <th class="px-4 py-3 text-right text-[11px] font-semibold text-slate-500 uppercase tracking-widest"></th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-border-subtle">
                  <tr
                    v-for="bt in sortedBacktests"
                    :key="bt.id"
                    class="hover:bg-surface/40 transition-colors"
                    :class="bt.backtest_id === bestPnlBacktest?.backtest_id ? 'bg-emerald-500/5' : ''"
                  >
                    <td class="px-4 py-3">
                      <div class="flex items-center gap-2">
                        <span
                          v-if="bt.backtest_id === bestPnlBacktest?.backtest_id"
                          class="material-symbols-outlined text-[14px] text-amber-400 shrink-0"
                          title="Best backtest"
                        >star</span>
                        <span class="text-xs text-slate-400">{{ fmtDateTime(bt.created_at) }}</span>
                      </div>
                    </td>
                    <td class="px-4 py-3">
                      <p class="text-xs text-slate-300 font-mono truncate max-w-[160px]" :title="(bt.stocks_used || []).join(', ')">
                        {{ (bt.stocks_used || []).slice(0, 4).join(', ') }}{{ (bt.stocks_used || []).length > 4 ? ` +${bt.stocks_used.length - 4}` : '' }}
                      </p>
                    </td>
                    <td class="px-4 py-3">
                      <p class="text-xs text-slate-500 font-mono whitespace-nowrap">
                        {{ fmtDate(bt.start_date) }} – {{ fmtDate(bt.end_date) }}
                      </p>
                    </td>
                    <td class="px-4 py-3 text-right">
                      <span class="text-sm font-mono font-semibold" :class="pnlColor(bt.overall_profit)">
                        {{ fmtPnl(bt.overall_profit) }}
                      </span>
                    </td>
                    <td class="px-4 py-3 text-right">
                      <span class="text-sm font-mono font-semibold" :class="pnlColor(bt.pnl_percent)">
                        {{ fmtPct(bt.pnl_percent) }}
                      </span>
                    </td>
                    <td class="px-4 py-3 text-right">
                      <button
                        v-if="bt.backtest_id"
                        @click="viewBacktest(bt.backtest_id)"
                        class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold border border-border-subtle text-slate-400 hover:text-primary hover:border-primary/30 hover:bg-primary/5 transition-colors"
                      >
                        <span class="material-symbols-outlined text-[12px]">analytics</span>
                        View
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

      </template>

      <!-- Not found -->
      <div v-else class="flex flex-col items-center justify-center py-24 text-center">
        <span class="material-symbols-outlined text-slate-700 text-5xl mb-4">schema</span>
        <p class="text-slate-400 font-medium">Strategy not found</p>
        <button @click="router.push('/strategies')" class="mt-4 px-4 py-2 rounded-lg border border-border-subtle text-sm text-slate-400 hover:text-slate-200 transition-colors">
          Back to Strategies
        </button>
      </div>

    </main>

    <!-- ── Backtest this strategy modal ────────────────────────────────────── -->
    <Teleport to="body">
      <Transition name="modal-fade">
        <div
          v-if="showBtModal"
          class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
          @click.self="closeBtModal"
        >
          <div class="w-full max-w-lg bg-[#0d1117] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
            <!-- Header -->
            <div class="flex items-center gap-3 px-6 py-4 border-b border-border-subtle shrink-0">
              <div class="size-9 rounded-xl bg-sky-500/10 border border-sky-500/20 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-sky-400 text-base">play_circle</span>
              </div>
              <div class="flex-1 min-w-0">
                <p class="text-sm font-semibold text-slate-100">Backtest this strategy</p>
                <p class="text-[11px] text-slate-500 truncate mt-0.5">{{ strategy?.name }}</p>
              </div>
              <button @click="closeBtModal" class="text-slate-600 hover:text-slate-400 transition-colors">
                <span class="material-symbols-outlined text-[18px]">close</span>
              </button>
            </div>

            <!-- Body -->
            <div class="overflow-y-auto flex-1 px-6 py-5 space-y-5">
              <div v-if="btModalLoading" class="flex items-center justify-center py-8 text-slate-500 gap-2 text-sm">
                <span class="material-symbols-outlined animate-spin text-lg">progress_activity</span>
                Loading instances...
              </div>
              <template v-else>

                <!-- Instance selector -->
                <div>
                  <p class="text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-3">Select Instance</p>

                  <!-- Already-linked instances (highlighted) -->
                  <div v-if="linkedInstances.length" class="mb-2">
                    <p class="text-[10px] text-emerald-500/70 uppercase tracking-widest font-semibold mb-1.5 px-1">Already linked to this strategy</p>
                    <div class="space-y-1.5">
                      <label
                        v-for="inst in linkedInstances"
                        :key="inst.id"
                        class="flex items-center gap-3 px-3 py-2.5 rounded-xl border cursor-pointer transition-colors"
                        :class="selectedInstId === String(inst.id)
                          ? 'border-emerald-500/40 bg-emerald-500/10'
                          : 'border-emerald-500/20 bg-emerald-500/5 hover:border-emerald-500/30'"
                      >
                        <input type="radio" :value="String(inst.id)" v-model="selectedInstId" class="accent-emerald-500 shrink-0" />
                        <div class="min-w-0 flex-1">
                          <p class="text-sm font-semibold text-emerald-300 truncate">{{ inst.name || inst.id }}</p>
                          <p class="text-[10px] text-emerald-600 font-mono">{{ inst.id }}</p>
                        </div>
                        <span class="shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">Linked</span>
                      </label>
                    </div>
                  </div>

                  <!-- Free instances -->
                  <div v-if="freeInstances.length" class="mb-2">
                    <p v-if="linkedInstances.length" class="text-[10px] text-slate-600 uppercase tracking-widest font-semibold mb-1.5 px-1 mt-3">Available instances (no strategy)</p>
                    <div class="space-y-1.5">
                      <label
                        v-for="inst in freeInstances"
                        :key="inst.id"
                        class="flex items-center gap-3 px-3 py-2.5 rounded-xl border cursor-pointer transition-colors"
                        :class="selectedInstId === String(inst.id)
                          ? 'border-primary/40 bg-primary/10'
                          : 'border-border-subtle hover:border-primary/20 hover:bg-surface'"
                      >
                        <input type="radio" :value="String(inst.id)" v-model="selectedInstId" class="accent-sky-500 shrink-0" />
                        <div class="min-w-0">
                          <p class="text-sm text-slate-200 truncate">{{ inst.name || inst.id }}</p>
                          <p class="text-[10px] text-slate-600 font-mono">{{ inst.id }}</p>
                        </div>
                      </label>
                    </div>
                  </div>

                  <!-- Create new -->
                  <label
                    class="flex items-center gap-3 px-3 py-2.5 rounded-xl border cursor-pointer transition-colors mt-2"
                    :class="selectedInstId === ''
                      ? 'border-primary/40 bg-primary/10'
                      : 'border-border-subtle hover:border-primary/20 hover:bg-surface'"
                  >
                    <input type="radio" value="" v-model="selectedInstId" class="accent-sky-500 shrink-0" />
                    <span class="material-symbols-outlined text-[16px] text-slate-400">add_circle</span>
                    <span class="text-sm text-slate-300 font-medium">Create new instance</span>
                  </label>

                  <!-- New instance name field -->
                  <div v-if="selectedInstId === ''" class="mt-3">
                    <label class="block text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">New Instance Name</label>
                    <input
                      v-model="newInstName"
                      type="text"
                      placeholder="e.g. My Strategy Test"
                      class="w-full px-3 py-2 rounded-lg bg-surface border border-border-subtle text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-primary/50 transition-colors"
                    />
                  </div>
                </div>

                <div class="border-t border-border-subtle"></div>

                <!-- Backtest parameters -->
                <div class="space-y-4">
                  <p class="text-[11px] font-semibold text-slate-500 uppercase tracking-widest">Backtest Parameters</p>

                  <div>
                    <label class="block text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">Stocks <span class="text-slate-600 normal-case">(comma-separated)</span></label>
                    <input
                      v-model="btForm.stocks"
                      type="text"
                      placeholder="AAPL, MSFT, NVDA"
                      class="w-full px-3 py-2 rounded-lg bg-surface border border-border-subtle text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-primary/50 transition-colors font-mono"
                    />
                  </div>

                  <div class="grid grid-cols-2 gap-3">
                    <div>
                      <label class="block text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">Start Date</label>
                      <input
                        v-model="btForm.start_date"
                        type="date"
                        class="w-full px-3 py-2 rounded-lg bg-surface border border-border-subtle text-sm text-slate-200 focus:outline-none focus:border-primary/50 transition-colors"
                      />
                    </div>
                    <div>
                      <label class="block text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">End Date</label>
                      <input
                        v-model="btForm.end_date"
                        type="date"
                        class="w-full px-3 py-2 rounded-lg bg-surface border border-border-subtle text-sm text-slate-200 focus:outline-none focus:border-primary/50 transition-colors"
                      />
                    </div>
                  </div>

                  <div class="grid grid-cols-2 gap-3">
                    <div>
                      <label class="block text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">Granularity</label>
                      <select
                        v-model="btForm.granularity"
                        class="w-full px-3 py-2 rounded-lg bg-surface border border-border-subtle text-sm text-slate-200 focus:outline-none focus:border-primary/50 transition-colors"
                      >
                        <option v-for="g in GRANULARITIES" :key="g.value" :value="g.value">{{ g.label }}</option>
                      </select>
                    </div>
                    <div>
                      <label class="block text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">Initial Cash ($)</label>
                      <input
                        v-model="btForm.initial_cash"
                        type="number"
                        min="1"
                        step="1000"
                        class="w-full px-3 py-2 rounded-lg bg-surface border border-border-subtle text-sm text-slate-200 focus:outline-none focus:border-primary/50 transition-colors font-mono"
                      />
                    </div>
                  </div>
                </div>

              </template>
            </div>

            <!-- Footer -->
            <div class="px-6 py-4 border-t border-border-subtle shrink-0">
              <div v-if="btModalMsg" class="mb-3 flex items-center gap-2 text-xs px-3 py-2 rounded-lg" :class="btModalOk ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'">
                <span class="material-symbols-outlined text-[14px]">{{ btModalOk ? 'check_circle' : 'error' }}</span>
                {{ btModalMsg }}
              </div>
              <div class="flex items-center justify-end gap-2">
                <button
                  @click="closeBtModal"
                  :disabled="btModalBusy"
                  class="px-4 py-2 rounded-lg text-sm font-medium text-slate-400 hover:text-slate-200 hover:bg-surface border border-transparent hover:border-border-subtle transition-colors disabled:opacity-40"
                >Cancel</button>
                <button
                  @click="submitBtModal"
                  :disabled="btModalBusy || btModalLoading"
                  class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold bg-primary hover:brightness-110 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  <span v-if="btModalBusy" class="material-symbols-outlined text-[14px] animate-spin">progress_activity</span>
                  <span v-else class="material-symbols-outlined text-[14px]">play_circle</span>
                  Run Backtest
                </button>
              </div>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

  </AppShell>
</template>

<style scoped>
.modal-fade-enter-active, .modal-fade-leave-active { transition: opacity 0.2s ease; }
.modal-fade-enter-from, .modal-fade-leave-to { opacity: 0; }
</style>
