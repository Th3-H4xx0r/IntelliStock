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
  return parseFloat(v) >= 0 ? 'text-gain' : 'text-loss'
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
    btModalMsg.value = `Backtest #${btId} queued`
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
    <main class="ui-page">

      <!-- Top nav row -->
      <div class="flex items-center justify-between gap-4 mb-4">
        <button
          @click="router.push('/strategies')"
          class="ui-btn-ghost -ml-3"
        >
          <span class="material-symbols-outlined text-[16px]">arrow_back</span>
          All strategies
        </button>
        <button
          v-if="strategy"
          @click="openBtModal"
          class="ui-btn-primary"
        >
          Backtest this strategy
        </button>
      </div>

      <!-- Loading -->
      <div v-if="loading" aria-hidden="true">
        <div class="ui-skel h-7 w-64"></div>
        <div class="ui-skel h-4 w-80 mt-3"></div>
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 mt-12">
          <div v-for="n in 3" :key="n" class="h-[180px] rounded-xl bg-surface"></div>
        </div>
      </div>

      <template v-else-if="strategy">

        <!-- ── Strategy header ─────────────────────────────────────────────── -->
        <header class="flex flex-wrap items-end justify-between gap-x-8 gap-y-5">
          <div class="min-w-0">
            <div class="flex items-center gap-x-3 gap-y-1 flex-wrap">
              <h1 class="ui-page-title">{{ strategy.name }}</h1>
              <span v-if="isAgentBest" class="inline-flex items-center gap-1 text-xs text-slate-400">
                <span class="material-symbols-outlined text-[16px] text-slate-500">star</span>
                Agent best
              </span>
            </div>
            <p class="ui-page-sub tabular-nums">
              Strategy ID {{ strategy.id }} <span class="text-slate-600">·</span> {{ (strategy.strategies || []).length }} sub-strategies <span class="text-slate-600">·</span> {{ strategyBacktests.length }} backtests
            </p>
          </div>

          <!-- Best backtest summary -->
          <div v-if="bestPnlBacktest" class="flex items-end gap-8 flex-wrap">
            <div>
              <p class="ui-label">Best P&amp;L</p>
              <p class="mt-2 text-2xl leading-none font-medium tabular-nums" :class="pnlColor(bestPnlBacktest.overall_profit)">
                {{ fmtPnl(bestPnlBacktest.overall_profit) }}
              </p>
            </div>
            <div>
              <p class="ui-label">Best P&amp;L %</p>
              <p class="mt-2 text-2xl leading-none font-medium tabular-nums" :class="pnlColor(bestPnlBacktest.pnl_percent)">
                {{ fmtPct(bestPnlBacktest.pnl_percent) }}
              </p>
            </div>
            <button
              @click="viewBacktest(bestPnlBacktest.backtest_id)"
              class="ui-btn"
            >
              View best backtest
            </button>
          </div>
        </header>

        <!-- ── Sub-strategies ──────────────────────────────────────────────── -->
        <section class="mt-12" aria-labelledby="subs-title">
          <h2 id="subs-title" class="ui-section-title mb-3">
            Sub-strategies <span class="ml-1 font-normal text-slate-500 tabular-nums">{{ (strategy.strategies || []).length }}</span>
          </h2>
          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <article
              v-for="(sub, si) in strategy.strategies"
              :key="si"
              class="ui-card p-5 flex flex-col gap-4"
            >
              <!-- Sub-strategy header -->
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <p class="text-sm font-medium text-slate-100 truncate">{{ sub.strategy }}</p>
                  <p class="text-xs text-slate-500 mt-0.5 tabular-nums">Position {{ sub.execution_position ?? si }}</p>
                </div>
                <span class="shrink-0 text-xs text-slate-400">{{ sub.decision_phase || 'pre' }}</span>
              </div>

              <!-- Core fields -->
              <dl class="grid grid-cols-2 gap-x-4">
                <div>
                  <dt class="text-xs text-slate-500">Weight</dt>
                  <dd class="mt-1 text-sm text-slate-200 tabular-nums">{{ sub.weight ?? '—' }}</dd>
                </div>
                <div>
                  <dt class="text-xs text-slate-500">Scope</dt>
                  <dd class="mt-1 text-sm text-slate-200">{{ sub.execution_scope || '—' }}</dd>
                </div>
              </dl>

              <!-- Config -->
              <div v-if="schemaFields(sub).length" class="border-t border-white/[0.06] pt-4">
                <p class="text-xs text-slate-500 mb-2">Config</p>
                <dl class="space-y-1.5">
                  <div v-for="f in schemaFields(sub)" :key="f.key" class="flex items-baseline justify-between gap-3">
                    <dt class="text-xs text-slate-400 truncate min-w-0">{{ f.key }}</dt>
                    <dd class="text-xs text-slate-200 tabular-nums text-right shrink-0 max-w-[140px] truncate" :title="fmtVal(f.val)">{{ fmtVal(f.val) }}</dd>
                  </div>
                </dl>
              </div>
              <p v-else class="text-xs text-slate-500">No config</p>
            </article>

            <div v-if="!(strategy.strategies || []).length" class="col-span-full ui-card ui-empty">
              <p>No sub-strategies defined</p>
            </div>
          </div>
        </section>

        <!-- ── Backtests table ─────────────────────────────────────────────── -->
        <section class="mt-12" aria-labelledby="sd-backtests-title">
          <h2 id="sd-backtests-title" class="ui-section-title mb-3">
            Backtests <span class="ml-1 font-normal text-slate-500 tabular-nums">{{ strategyBacktests.length }}</span>
          </h2>

          <div v-if="!strategyBacktests.length" class="ui-card ui-empty">
            <p>No backtests recorded for this strategy yet</p>
          </div>

          <div v-else class="ui-card overflow-hidden">
            <div class="overflow-x-auto">
              <table class="w-full min-w-[680px] text-sm">
                <thead>
                  <tr class="border-b border-white/[0.06] text-xs text-slate-500">
                    <th
                      class="h-10 pl-5 pr-3 text-left font-normal cursor-pointer select-none hover:text-slate-200 transition-colors duration-150 ease-out"
                      :class="btSortBy === 'created_at' ? 'text-slate-300' : ''"
                      @click="setBtSort('created_at')"
                    >
                      <span class="inline-flex items-center gap-1">
                        Date
                        <span v-if="btSortBy === 'created_at'" class="material-symbols-outlined text-[14px]">
                          {{ btSortOrder === 'desc' ? 'arrow_downward' : 'arrow_upward' }}
                        </span>
                      </span>
                    </th>
                    <th class="h-10 px-3 text-left font-normal">Stocks</th>
                    <th class="h-10 px-3 text-left font-normal">Period</th>
                    <th
                      class="h-10 px-3 text-right font-normal cursor-pointer select-none hover:text-slate-200 transition-colors duration-150 ease-out"
                      :class="btSortBy === 'pnl' ? 'text-slate-300' : ''"
                      @click="setBtSort('pnl')"
                    >
                      <span class="inline-flex items-center justify-end gap-1">
                        P&amp;L
                        <span v-if="btSortBy === 'pnl'" class="material-symbols-outlined text-[14px]">
                          {{ btSortOrder === 'desc' ? 'arrow_downward' : 'arrow_upward' }}
                        </span>
                      </span>
                    </th>
                    <th
                      class="h-10 px-3 text-right font-normal cursor-pointer select-none hover:text-slate-200 transition-colors duration-150 ease-out"
                      :class="btSortBy === 'pct' ? 'text-slate-300' : ''"
                      @click="setBtSort('pct')"
                    >
                      <span class="inline-flex items-center justify-end gap-1">
                        P&amp;L %
                        <span v-if="btSortBy === 'pct'" class="material-symbols-outlined text-[14px]">
                          {{ btSortOrder === 'desc' ? 'arrow_downward' : 'arrow_upward' }}
                        </span>
                      </span>
                    </th>
                    <th class="h-10 pl-3 pr-5 font-normal"><span class="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/[0.05]">
                  <tr
                    v-for="bt in sortedBacktests"
                    :key="bt.id"
                    class="hover:bg-white/[0.03] transition-colors duration-150 ease-out"
                  >
                    <td class="h-14 pl-5 pr-3">
                      <div class="flex items-center gap-2">
                        <span class="text-slate-300 tabular-nums whitespace-nowrap">{{ fmtDateTime(bt.created_at) }}</span>
                        <span
                          v-if="bt.backtest_id === bestPnlBacktest?.backtest_id"
                          class="material-symbols-outlined text-[16px] text-slate-400 shrink-0"
                          title="Best backtest"
                        >star</span>
                      </div>
                    </td>
                    <td class="h-14 px-3">
                      <p class="text-slate-200 truncate max-w-[200px]" :title="(bt.stocks_used || []).join(', ')">
                        {{ (bt.stocks_used || []).slice(0, 4).join(', ') }}<span v-if="(bt.stocks_used || []).length > 4" class="text-xs text-slate-500 tabular-nums">{{ ` +${bt.stocks_used.length - 4}` }}</span>
                      </p>
                    </td>
                    <td class="h-14 px-3">
                      <p class="text-slate-400 tabular-nums whitespace-nowrap">
                        {{ fmtDate(bt.start_date) }} – {{ fmtDate(bt.end_date) }}
                      </p>
                    </td>
                    <td class="h-14 px-3 text-right tabular-nums" :class="pnlColor(bt.overall_profit)">
                      {{ fmtPnl(bt.overall_profit) }}
                    </td>
                    <td class="h-14 px-3 text-right tabular-nums" :class="pnlColor(bt.pnl_percent)">
                      {{ fmtPct(bt.pnl_percent) }}
                    </td>
                    <td class="h-14 pl-3 pr-5 text-right">
                      <button
                        v-if="bt.backtest_id"
                        @click="viewBacktest(bt.backtest_id)"
                        class="ui-btn-ghost px-2.5"
                      >View</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

      </template>

      <!-- Not found -->
      <div v-else class="ui-card ui-empty">
        <p class="text-slate-200">Strategy not found</p>
        <button @click="router.push('/strategies')" class="ui-btn">
          Back to strategies
        </button>
      </div>

    </main>

    <!-- ── Backtest this strategy modal ────────────────────────────────────── -->
    <Teleport to="body">
      <Transition name="modal-fade">
        <div
          v-if="showBtModal"
          class="ui-overlay"
          @click.self="closeBtModal"
        >
          <div class="modal-panel ui-modal max-w-lg flex flex-col max-h-[90vh]">
            <!-- Header -->
            <div class="ui-modal-head shrink-0">
              <div class="flex-1 min-w-0">
                <h2 class="text-base font-medium text-slate-100">Backtest this strategy</h2>
                <p class="text-sm text-slate-500 truncate mt-1">{{ strategy?.name }}</p>
              </div>
              <button @click="closeBtModal" class="ui-icon-btn shrink-0" aria-label="Close">
                <span class="material-symbols-outlined text-[18px]">close</span>
              </button>
            </div>

            <!-- Body -->
            <div class="overflow-y-auto flex-1 px-6 pb-6 space-y-6">
              <div v-if="btModalLoading" class="flex items-center justify-center py-8 text-slate-500 gap-2 text-sm">
                <span class="material-symbols-outlined animate-spin text-[18px]">progress_activity</span>
                Loading instances…
              </div>
              <template v-else>

                <!-- Instance selector -->
                <div>
                  <p class="ui-field-label">Instance</p>

                  <!-- Already-linked instances -->
                  <div v-if="linkedInstances.length" class="mb-3">
                    <p class="text-xs text-slate-500 mb-2">Already linked to this strategy</p>
                    <div class="space-y-1.5">
                      <label
                        v-for="inst in linkedInstances"
                        :key="inst.id"
                        class="flex items-center gap-3 px-3 min-h-12 py-2 rounded-lg border cursor-pointer transition-colors duration-150 ease-out"
                        :class="selectedInstId === String(inst.id)
                          ? 'border-white/20 bg-raised shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]'
                          : 'border-white/[0.07] hover:bg-white/[0.03]'"
                      >
                        <input type="radio" :value="String(inst.id)" v-model="selectedInstId" class="accent-[#ededed] shrink-0" />
                        <div class="min-w-0 flex-1">
                          <p class="text-sm text-slate-100 truncate">{{ inst.name || inst.id }}</p>
                          <p class="text-xs text-slate-500 mt-0.5">{{ inst.id }}</p>
                        </div>
                        <span class="shrink-0 text-xs text-slate-400">Linked</span>
                      </label>
                    </div>
                  </div>

                  <!-- Free instances -->
                  <div v-if="freeInstances.length" class="mb-3">
                    <p v-if="linkedInstances.length" class="text-xs text-slate-500 mb-2">Available instances (no strategy)</p>
                    <div class="space-y-1.5">
                      <label
                        v-for="inst in freeInstances"
                        :key="inst.id"
                        class="flex items-center gap-3 px-3 min-h-12 py-2 rounded-lg border cursor-pointer transition-colors duration-150 ease-out"
                        :class="selectedInstId === String(inst.id)
                          ? 'border-white/20 bg-raised shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]'
                          : 'border-white/[0.07] hover:bg-white/[0.03]'"
                      >
                        <input type="radio" :value="String(inst.id)" v-model="selectedInstId" class="accent-[#ededed] shrink-0" />
                        <div class="min-w-0">
                          <p class="text-sm text-slate-100 truncate">{{ inst.name || inst.id }}</p>
                          <p class="text-xs text-slate-500 mt-0.5">{{ inst.id }}</p>
                        </div>
                      </label>
                    </div>
                  </div>

                  <!-- Create new -->
                  <label
                    class="flex items-center gap-3 px-3 min-h-12 py-2 rounded-lg border cursor-pointer transition-colors duration-150 ease-out"
                    :class="selectedInstId === ''
                      ? 'border-white/20 bg-raised shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]'
                      : 'border-white/[0.07] hover:bg-white/[0.03]'"
                  >
                    <input type="radio" value="" v-model="selectedInstId" class="accent-[#ededed] shrink-0" />
                    <span class="text-sm text-slate-200">Create new instance</span>
                  </label>

                  <!-- New instance name field -->
                  <div v-if="selectedInstId === ''" class="mt-4">
                    <label for="sd-new-inst" class="ui-field-label">New instance name</label>
                    <input
                      id="sd-new-inst"
                      v-model="newInstName"
                      type="text"
                      placeholder="e.g. My strategy test"
                      class="ui-input"
                    />
                  </div>
                </div>

                <div class="border-t border-white/[0.06]"></div>

                <!-- Backtest parameters -->
                <div class="space-y-4">
                  <p class="text-sm font-medium text-slate-100">Backtest parameters</p>

                  <div>
                    <label for="sd-stocks" class="ui-field-label">Stocks <span class="text-slate-500">(comma-separated)</span></label>
                    <input
                      id="sd-stocks"
                      v-model="btForm.stocks"
                      type="text"
                      placeholder="AAPL, MSFT, NVDA"
                      class="ui-input"
                    />
                  </div>

                  <div class="grid grid-cols-2 gap-3">
                    <div>
                      <label for="sd-start" class="ui-field-label">Start date</label>
                      <input
                        id="sd-start"
                        v-model="btForm.start_date"
                        type="date"
                        class="ui-input tabular-nums"
                      />
                    </div>
                    <div>
                      <label for="sd-end" class="ui-field-label">End date</label>
                      <input
                        id="sd-end"
                        v-model="btForm.end_date"
                        type="date"
                        class="ui-input tabular-nums"
                      />
                    </div>
                  </div>

                  <div class="grid grid-cols-2 gap-3">
                    <div>
                      <label for="sd-gran" class="ui-field-label">Granularity</label>
                      <select
                        id="sd-gran"
                        v-model="btForm.granularity"
                        class="ui-input"
                      >
                        <option v-for="g in GRANULARITIES" :key="g.value" :value="g.value">{{ g.label }}</option>
                      </select>
                    </div>
                    <div>
                      <label for="sd-cash" class="ui-field-label">Initial cash ($)</label>
                      <input
                        id="sd-cash"
                        v-model="btForm.initial_cash"
                        type="number"
                        min="1"
                        step="1000"
                        class="ui-input tabular-nums"
                      />
                    </div>
                  </div>
                </div>

              </template>
            </div>

            <!-- Footer -->
            <div class="px-6 py-4 border-t border-white/[0.06] shrink-0">
              <p
                v-if="btModalMsg"
                class="mb-3 flex items-center gap-2 text-sm"
                :class="btModalBusy ? 'text-slate-400' : btModalOk ? 'text-gain' : 'text-loss'"
              >
                <span v-if="!btModalBusy" class="material-symbols-outlined text-[16px]">{{ btModalOk ? 'check_circle' : 'error' }}</span>
                {{ btModalMsg }}
              </p>
              <div class="flex items-center justify-end gap-2">
                <button
                  @click="closeBtModal"
                  :disabled="btModalBusy"
                  class="ui-btn-ghost h-9 px-4"
                >Cancel</button>
                <button
                  @click="submitBtModal"
                  :disabled="btModalBusy || btModalLoading"
                  class="ui-btn-primary"
                >
                  <span v-if="btModalBusy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                  Run backtest
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
.modal-fade-enter-active, .modal-fade-leave-active { transition: opacity 180ms cubic-bezier(0.22, 1, 0.36, 1); }
.modal-fade-enter-active .modal-panel, .modal-fade-leave-active .modal-panel { transition: transform 220ms cubic-bezier(0.22, 1, 0.36, 1); }
.modal-fade-enter-from, .modal-fade-leave-to { opacity: 0; }
.modal-fade-enter-from .modal-panel, .modal-fade-leave-to .modal-panel { transform: translateY(6px); }
</style>
