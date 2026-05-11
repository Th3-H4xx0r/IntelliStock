<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import { getToken } from '../utils/auth.js'

const router = useRouter()

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
const strategies      = ref([])
const agentResults    = ref([])
const top5            = ref([])   // sorted rank 1..5
const allBestByStrat  = ref({})   // strategy_id(str) -> {best_pnl, best_pct, backtest_id} from BacktestResults
const loading         = ref(true)
const sortBy       = ref('best_pnl')
const sortOrder    = ref('desc')
const page         = ref(1)
const perPage      = ref(20)
const PER_PAGE_OPTIONS = [10, 20, 50]

// ── Fetch ─────────────────────────────────────────────────────────────────────
async function fetchAll() {
  loading.value = true
  try {
    const [sRes, aRes, t5Res, bpsRes] = await Promise.all([
      fetch(`${API_BASE}/strategies`, { headers: authHeaders() }),
      fetch(`${API_BASE}/agent/results?limit=10000`, { headers: authHeaders() }),
      fetch(`${API_BASE}/agent/top5`, { headers: authHeaders() }),
      fetch(`${API_BASE}/backtests/best-per-strategy`, { headers: authHeaders() }),
    ])
    if (sRes.ok)   strategies.value    = (await sRes.json()).strategies  || []
    if (aRes.ok)   agentResults.value  = (await aRes.json()).results     || []
    if (t5Res.ok)  top5.value          = (await t5Res.json()).top5       || []
    if (bpsRes.ok) allBestByStrat.value = (await bpsRes.json()).by_strategy || {}
    // Sort top5 by rank ascending (rank 1 = best)
    top5.value.sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99))
  } catch { /* non-critical */ } finally {
    loading.value = false
  }
}

// Enrich top5 entries with derived display fields
const top5Enriched = computed(() =>
  top5.value.map(e => ({
    ...e,
    strategy_name: e.strategy_snapshot?.name || (e.strategy_id != null ? `Strategy #${e.strategy_id}` : 'Strategy'),
    sub_strategies: (e.strategy_snapshot?.strategies || []).map(s => s.strategy || s.type || '?'),
  }))
)

// Set of strategy_ids that are in top5 (for table highlighting)
const top5StrategyIds = computed(() => new Set(top5.value.map(e => e.strategy_id).filter(x => x != null)))

// ── Computed: best backtest per strategy ──────────────────────────────────────
const bestByStrategy = computed(() => {
  const m = new Map()
  for (const r of agentResults.value) {
    const sid = r.strategy_id
    if (sid == null) continue
    const profit = parseFloat(r.overall_profit) || 0
    const pct    = parseFloat(r.pnl_percent)    || 0
    const existing = m.get(sid)
    if (!existing) {
      m.set(sid, {
        best_pnl: profit, best_pnl_bid: r.backtest_id,
        best_pct: pct,    best_pct_bid: r.backtest_id,
        count: 1, latest: r.created_at,
      })
    } else {
      if (profit > existing.best_pnl) { existing.best_pnl = profit; existing.best_pnl_bid = r.backtest_id }
      if (pct    > existing.best_pct) { existing.best_pct = pct;    existing.best_pct_bid = r.backtest_id }
      existing.count++
      if ((r.created_at || '') > (existing.latest || '')) existing.latest = r.created_at
    }
  }
  return m
})

// Rank lookup: strategy_id -> rank number (1-5)
const top5RankMap = computed(() => {
  const m = new Map()
  for (const e of top5.value) {
    if (e.strategy_id != null) m.set(e.strategy_id, e.rank ?? null)
  }
  return m
})

const enrichedStrategies = computed(() =>
  strategies.value.map(s => {
    const best = bestByStrategy.value.get(s.id) || null
    const top5entry = top5.value.find(e => e.strategy_id === s.id) || null
    const rank = top5RankMap.value.get(s.id) ?? null
    // Fallback: best BacktestResult even if negative (for strategies with no passed agent results)
    const allBest = allBestByStrat.value[String(s.id)] || null
    const bestPnl = best?.best_pnl ?? (top5entry ? parseFloat(top5entry.overall_profit ?? 0) : (allBest ? allBest.best_pnl : null))
    const bestPct = best?.best_pct ?? (top5entry ? parseFloat(top5entry.pnl_percent    ?? 0) : (allBest ? allBest.best_pct : null))
    const bestBid = best?.best_pnl_bid ?? (top5entry ? top5entry.backtest_id : null) ?? allBest?.backtest_id ?? null
    return {
      ...s,
      best_pnl:     bestPnl != null ? parseFloat(bestPnl) : null,
      best_pnl_bid: bestBid,
      best_pct:     bestPct != null ? parseFloat(bestPct) : null,
      best_pct_bid: best?.best_pct_bid ?? bestBid,
      run_count:    best?.count ?? 0,
      latest:       best?.latest ?? null,
      top5_rank:    rank,
      is_top5:      rank != null,
    }
  })
)

const sortedStrategies = computed(() => {
  const list = [...enrichedStrategies.value]
  list.sort((a, b) => {
    let av, bv
    if (sortBy.value === 'best_pnl')   { av = a.best_pnl ?? -Infinity; bv = b.best_pnl ?? -Infinity }
    else if (sortBy.value === 'best_pct') { av = a.best_pct ?? -Infinity; bv = b.best_pct ?? -Infinity }
    else if (sortBy.value === 'runs')  { av = a.run_count; bv = b.run_count }
    else { av = (a.name || '').toLowerCase(); bv = (b.name || '').toLowerCase() }
    if (sortOrder.value === 'asc') return av > bv ? 1 : av < bv ? -1 : 0
    return av < bv ? 1 : av > bv ? -1 : 0
  })
  return list
})

const totalPages  = computed(() => Math.max(1, Math.ceil(sortedStrategies.value.length / perPage.value)))
const pagedRows   = computed(() => {
  const s = (page.value - 1) * perPage.value
  return sortedStrategies.value.slice(s, s + perPage.value)
})

function setSort(col) {
  if (sortBy.value === col) sortOrder.value = sortOrder.value === 'desc' ? 'asc' : 'desc'
  else { sortBy.value = col; sortOrder.value = 'desc' }
  page.value = 1
}

// ── Helpers ───────────────────────────────────────────────────────────────────
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
function fmtDate(v) {
  if (!v) return '—'
  try {
    const d = new Date(v)
    if (isNaN(d.getTime())) return '—'
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  } catch { return '—' }
}

function rankMedal(rank) {
  if (rank === 1) return '🥇'
  if (rank === 2) return '🥈'
  if (rank === 3) return '🥉'
  return `#${rank}`
}

function rankBorderClass(rank) {
  if (rank === 1) return 'border-amber-500/40'
  if (rank === 2) return 'border-slate-400/40'
  if (rank === 3) return 'border-orange-600/40'
  return 'border-primary/30'
}
function rankBgClass(rank) {
  if (rank === 1) return 'bg-amber-500/5'
  if (rank === 2) return 'bg-slate-400/5'
  if (rank === 3) return 'bg-orange-600/5'
  return 'bg-primary/5'
}
function rankAccentClass(rank) {
  if (rank === 1) return 'from-amber-500/60 via-amber-400 to-amber-500/60'
  if (rank === 2) return 'from-slate-400/60 via-slate-300 to-slate-400/60'
  if (rank === 3) return 'from-orange-600/60 via-orange-500 to-orange-600/60'
  return 'from-primary/40 via-primary/60 to-primary/40'
}
function rankTextClass(rank) {
  if (rank === 1) return 'text-amber-300'
  if (rank === 2) return 'text-slate-300'
  if (rank === 3) return 'text-orange-400'
  return 'text-primary'
}
function rankIconBg(rank) {
  if (rank === 1) return 'bg-amber-500/15 border-amber-500/30'
  if (rank === 2) return 'bg-slate-400/15 border-slate-400/30'
  if (rank === 3) return 'bg-orange-600/15 border-orange-600/30'
  return 'bg-primary/10 border-primary/20'
}
function rankPillClass(rank) {
  if (rank === 1) return 'bg-amber-500/20 text-amber-400 border-amber-500/30'
  if (rank === 2) return 'bg-slate-400/20 text-slate-300 border-slate-400/30'
  if (rank === 3) return 'bg-orange-600/20 text-orange-400 border-orange-600/30'
  return 'bg-primary/10 text-primary border-primary/20'
}
function rankSubPillClass(rank) {
  if (rank === 1) return 'bg-amber-500/10 border-amber-500/20 text-amber-300/80'
  if (rank === 2) return 'bg-slate-400/10 border-slate-400/20 text-slate-300/80'
  if (rank === 3) return 'bg-orange-600/10 border-orange-600/20 text-orange-300/80'
  return 'bg-primary/5 border-primary/10 text-primary/70'
}
function rankViewBtn(rank) {
  if (rank === 1) return 'border-amber-500/30 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20'
  if (rank === 2) return 'border-slate-400/30 bg-slate-400/10 text-slate-300 hover:bg-slate-400/20'
  if (rank === 3) return 'border-orange-600/30 bg-orange-600/10 text-orange-400 hover:bg-orange-600/20'
  return 'border-primary/30 bg-primary/10 text-primary hover:bg-primary/20'
}
function rankTableRowClass(rank) {
  if (rank === 1) return 'bg-amber-500/5'
  if (rank === 2) return 'bg-slate-400/5'
  if (rank === 3) return 'bg-orange-600/5'
  return 'bg-primary/5'
}

function viewStrategy(id)  { router.push(`/strategies/${id}`) }
function viewBacktest(bid) { if (bid != null) router.push(`/backtests/${bid}`) }

onMounted(fetchAll)
</script>

<template>
  <AppShell>
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10">

      <!-- Header -->
      <div class="flex items-start justify-between gap-4 mb-8 flex-wrap">
        <div>
          <h1 class="text-2xl font-bold text-slate-100">Strategies</h1>
          <p class="text-slate-500 text-sm mt-1">All trading strategies with their best AI backtest results.</p>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <button
            @click="router.push({ path: '/instances', query: { createStrategy: '1' } })"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-primary/30 bg-primary/10 text-primary text-xs font-semibold hover:bg-primary/20 transition-all"
          >
            <span class="material-symbols-outlined text-[15px]">add_circle</span>
            Create Strategy
          </button>
          <select
            v-model="perPage"
            @change="page = 1"
            class="bg-surface border border-border-subtle rounded-lg px-2 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-primary/40"
          >
            <option v-for="n in PER_PAGE_OPTIONS" :key="n" :value="n">{{ n }}/page</option>
          </select>
          <button
            @click="fetchAll"
            class="p-1.5 rounded-lg border border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface transition-all"
            title="Refresh"
          >
            <span class="material-symbols-outlined text-[16px]" :class="loading ? 'animate-spin' : ''">refresh</span>
          </button>
        </div>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="space-y-4">
        <div v-for="n in 4" :key="n" class="glass-card rounded-2xl h-20 animate-pulse"></div>
      </div>

      <template v-else>

        <!-- ── Top 5 Best Strategies ──────────────────────────────────────── -->
        <div v-if="top5.length > 0" class="mb-8">
          <div class="flex items-center gap-2 mb-4">
            <span class="material-symbols-outlined text-amber-400 text-[18px]">emoji_events</span>
            <p class="text-xs font-semibold text-amber-400 uppercase tracking-widest">Top {{ top5.length }} Best Strategies</p>
            <span class="text-[10px] text-slate-600 ml-1">ranked by P&L%</span>
          </div>

          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <div
              v-for="entry in top5Enriched"
              :key="entry.strategy_id ?? entry.rank"
              class="glass-card rounded-2xl border overflow-hidden"
              :class="[rankBorderClass(entry.rank), rankBgClass(entry.rank)]"
            >
              <!-- Rank accent line -->
              <div class="h-0.5 w-full bg-gradient-to-r" :class="rankAccentClass(entry.rank)"></div>
              <div class="p-4">
                <!-- Header row -->
                <div class="flex items-start justify-between gap-3 mb-3">
                  <div class="flex items-center gap-2.5 min-w-0">
                    <!-- Rank icon -->
                    <div
                      class="size-9 rounded-xl border flex items-center justify-center shrink-0 text-base leading-none"
                      :class="rankIconBg(entry.rank)"
                    >
                      <span v-if="entry.rank <= 3">{{ rankMedal(entry.rank) }}</span>
                      <span v-else class="font-bold text-sm" :class="rankTextClass(entry.rank)">#{{ entry.rank }}</span>
                    </div>
                    <div class="min-w-0">
                      <div class="flex items-center gap-1.5 flex-wrap">
                        <p class="text-sm font-bold truncate" :class="rankTextClass(entry.rank)">
                          {{ entry.strategy_name || entry.strategy_id || 'Strategy' }}
                        </p>
                        <span
                          class="text-[9px] font-bold px-1.5 py-0.5 rounded border uppercase tracking-wide shrink-0"
                          :class="rankPillClass(entry.rank)"
                        >Rank {{ entry.rank }}</span>
                      </div>
                      <p class="text-[10px] text-slate-600 mt-0.5">
                        {{ (entry.sub_strategies || []).length }} sub-strategies
                      </p>
                    </div>
                  </div>
                </div>

                <!-- P&L stats -->
                <div class="flex items-center gap-4 mb-3">
                  <div>
                    <p class="text-[9px] text-slate-600 uppercase tracking-widest mb-0.5">Best P&L</p>
                    <p class="text-base font-bold font-mono" :class="pnlColor(entry.overall_profit)">
                      {{ fmtPnl(entry.overall_profit) }}
                    </p>
                  </div>
                  <div>
                    <p class="text-[9px] text-slate-600 uppercase tracking-widest mb-0.5">Best P&L%</p>
                    <p class="text-base font-bold font-mono" :class="pnlColor(entry.pnl_percent)">
                      {{ fmtPct(entry.pnl_percent) }}
                    </p>
                  </div>
                </div>

                <!-- Sub-strategy pills -->
                <div v-if="(entry.sub_strategies || []).length" class="flex flex-wrap gap-1 mb-3">
                  <span
                    v-for="(sub, i) in (entry.sub_strategies || []).slice(0, 5)"
                    :key="i"
                    class="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-mono border"
                    :class="rankSubPillClass(entry.rank)"
                  >{{ sub }}</span>
                  <span
                    v-if="(entry.sub_strategies || []).length > 5"
                    class="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] text-slate-600"
                  >+{{ (entry.sub_strategies || []).length - 5 }} more</span>
                </div>

                <!-- Actions -->
                <div class="flex items-center gap-1.5">
                  <button
                    v-if="entry.strategy_id"
                    @click="viewStrategy(entry.strategy_id)"
                    class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold border transition-colors"
                    :class="rankViewBtn(entry.rank)"
                  >
                    <span class="material-symbols-outlined text-[12px]">open_in_new</span>
                    View Strategy
                  </button>
                  <button
                    v-if="entry.backtest_id"
                    @click="viewBacktest(entry.backtest_id)"
                    class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold border border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface transition-colors"
                  >
                    <span class="material-symbols-outlined text-[12px]">analytics</span>
                    Backtest
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- ── Strategies Table ────────────────────────────────────────────── -->
        <div class="glass-card rounded-2xl border border-border-subtle overflow-hidden">
          <!-- Table -->
          <div class="overflow-x-auto">
            <table class="w-full min-w-[700px] text-sm">
              <thead>
                <tr class="border-b border-border-subtle bg-surface/50">
                  <th
                    class="px-4 py-3 text-left text-[11px] font-semibold text-slate-500 uppercase tracking-widest cursor-pointer hover:text-slate-300 transition-colors select-none"
                    @click="setSort('name')"
                  >
                    <span class="flex items-center gap-1">
                      Name
                      <span v-if="sortBy === 'name'" class="material-symbols-outlined text-[12px] text-primary">
                        {{ sortOrder === 'desc' ? 'arrow_downward' : 'arrow_upward' }}
                      </span>
                    </span>
                  </th>
                  <th class="px-4 py-3 text-center text-[11px] font-semibold text-slate-500 uppercase tracking-widest">Subs</th>
                  <th class="px-4 py-3 text-center text-[11px] font-semibold text-slate-500 uppercase tracking-widest">Instances</th>
                  <th
                    class="px-4 py-3 text-center text-[11px] font-semibold text-slate-500 uppercase tracking-widest cursor-pointer hover:text-slate-300 transition-colors select-none"
                    @click="setSort('runs')"
                  >
                    <span class="flex items-center justify-center gap-1">
                      Backtests
                      <span v-if="sortBy === 'runs'" class="material-symbols-outlined text-[12px] text-primary">
                        {{ sortOrder === 'desc' ? 'arrow_downward' : 'arrow_upward' }}
                      </span>
                    </span>
                  </th>
                  <th
                    class="px-4 py-3 text-right text-[11px] font-semibold text-slate-500 uppercase tracking-widest cursor-pointer hover:text-slate-300 transition-colors select-none"
                    @click="setSort('best_pnl')"
                  >
                    <span class="flex items-center justify-end gap-1">
                      Best P&amp;L
                      <span v-if="sortBy === 'best_pnl'" class="material-symbols-outlined text-[12px] text-primary">
                        {{ sortOrder === 'desc' ? 'arrow_downward' : 'arrow_upward' }}
                      </span>
                    </span>
                  </th>
                  <th
                    class="px-4 py-3 text-right text-[11px] font-semibold text-slate-500 uppercase tracking-widest cursor-pointer hover:text-slate-300 transition-colors select-none"
                    @click="setSort('best_pct')"
                  >
                    <span class="flex items-center justify-end gap-1">
                      Best P&amp;L%
                      <span v-if="sortBy === 'best_pct'" class="material-symbols-outlined text-[12px] text-primary">
                        {{ sortOrder === 'desc' ? 'arrow_downward' : 'arrow_upward' }}
                      </span>
                    </span>
                  </th>
                  <th class="px-4 py-3 text-right text-[11px] font-semibold text-slate-500 uppercase tracking-widest">Actions</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-border-subtle">
                <template v-if="pagedRows.length === 0">
                  <tr>
                    <td colspan="7" class="px-4 py-16 text-center">
                      <div class="flex flex-col items-center gap-3">
                        <span class="material-symbols-outlined text-slate-700 text-4xl">schema</span>
                        <p class="text-slate-500 text-sm">No strategies found.</p>
                      </div>
                    </td>
                  </tr>
                </template>
                <tr
                  v-for="row in pagedRows"
                  :key="row.id"
                  class="transition-colors hover:bg-surface/40"
                  :class="row.is_top5 ? rankTableRowClass(row.top5_rank) : ''"
                >
                  <!-- Name -->
                  <td class="px-4 py-3">
                    <div class="flex items-center gap-2 min-w-0">
                      <div
                        class="size-7 rounded-lg flex items-center justify-center shrink-0 text-sm leading-none"
                        :class="row.is_top5 ? rankIconBg(row.top5_rank) : 'bg-surface border border-border-subtle'"
                      >
                        <template v-if="row.is_top5 && row.top5_rank <= 3">
                          <span>{{ rankMedal(row.top5_rank) }}</span>
                        </template>
                        <template v-else-if="row.is_top5">
                          <span class="text-[11px] font-bold" :class="rankTextClass(row.top5_rank)">#{{ row.top5_rank }}</span>
                        </template>
                        <template v-else>
                          <span class="material-symbols-outlined text-[14px] text-slate-500">schema</span>
                        </template>
                      </div>
                      <div class="min-w-0">
                        <div class="flex items-center gap-1.5 flex-wrap">
                          <span
                            class="text-sm font-semibold truncate"
                            :class="row.is_top5 ? rankTextClass(row.top5_rank) : 'text-slate-200'"
                          >{{ row.name }}</span>
                          <span
                            v-if="row.is_top5"
                            class="text-[9px] font-bold px-1.5 py-0.5 rounded border uppercase tracking-wide shrink-0"
                            :class="rankPillClass(row.top5_rank)"
                          >Rank {{ row.top5_rank }}</span>
                        </div>
                        <p class="text-[10px] text-slate-600 font-mono mt-0.5">ID {{ row.id }}</p>
                      </div>
                    </div>
                  </td>

                  <!-- Subs -->
                  <td class="px-4 py-3 text-center">
                    <span class="text-sm text-slate-300 font-mono">{{ (row.strategies || []).length }}</span>
                  </td>

                  <!-- Instances -->
                  <td class="px-4 py-3 text-center">
                    <span class="text-sm text-slate-400 font-mono">{{ (row.instances_using || []).length || '—' }}</span>
                  </td>

                  <!-- Backtests -->
                  <td class="px-4 py-3 text-center">
                    <span class="text-sm font-mono" :class="row.run_count > 0 ? 'text-slate-300' : 'text-slate-600'">
                      {{ row.run_count || '—' }}
                    </span>
                  </td>

                  <!-- Best P&L -->
                  <td class="px-4 py-3 text-right">
                    <span class="text-sm font-mono font-semibold" :class="pnlColor(row.best_pnl)">
                      {{ fmtPnl(row.best_pnl) }}
                    </span>
                  </td>

                  <!-- Best P&L% -->
                  <td class="px-4 py-3 text-right">
                    <span class="text-sm font-mono font-semibold" :class="pnlColor(row.best_pct)">
                      {{ fmtPct(row.best_pct) }}
                    </span>
                  </td>

                  <!-- Actions -->
                  <td class="px-4 py-3 text-right">
                    <div class="flex items-center justify-end gap-1.5">
                      <button
                        @click="viewStrategy(row.id)"
                        class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold border transition-colors"
                        :class="row.is_top5
                          ? rankViewBtn(row.top5_rank)
                          : 'border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface'"
                      >
                        <span class="material-symbols-outlined text-[12px]">open_in_new</span>
                        View
                      </button>
                      <button
                        v-if="row.best_pnl_bid"
                        @click="viewBacktest(row.best_pnl_bid)"
                        class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold border border-border-subtle text-slate-400 hover:text-primary hover:border-primary/30 hover:bg-primary/5 transition-colors"
                        title="View best backtest"
                      >
                        <span class="material-symbols-outlined text-[12px]">analytics</span>
                        Backtest
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Pagination footer -->
          <div v-if="totalPages > 1" class="flex items-center justify-between gap-4 px-4 py-3 border-t border-border-subtle">
            <p class="text-xs text-slate-500">{{ sortedStrategies.length }} strategies · page {{ page }} of {{ totalPages }}</p>
            <div class="flex items-center gap-1">
              <button
                @click="page = Math.max(1, page - 1)"
                :disabled="page <= 1"
                class="px-2 py-1.5 rounded-lg border border-border-subtle text-xs text-slate-400 hover:text-slate-200 hover:bg-surface transition-colors disabled:opacity-30"
              >
                <span class="material-symbols-outlined text-[14px]">chevron_left</span>
              </button>
              <template v-for="p in totalPages" :key="p">
                <button
                  @click="page = p"
                  class="min-w-[28px] px-2 py-1.5 rounded-lg border text-xs transition-colors"
                  :class="p === page ? 'border-primary/40 bg-primary/10 text-primary font-semibold' : 'border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface'"
                >{{ p }}</button>
              </template>
              <button
                @click="page = Math.min(totalPages, page + 1)"
                :disabled="page >= totalPages"
                class="px-2 py-1.5 rounded-lg border border-border-subtle text-xs text-slate-400 hover:text-slate-200 hover:bg-surface transition-colors disabled:opacity-30"
              >
                <span class="material-symbols-outlined text-[14px]">chevron_right</span>
              </button>
            </div>
          </div>
        </div>

      </template>
    </main>
  </AppShell>
</template>
