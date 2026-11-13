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
  return parseFloat(v) >= 0 ? 'text-gain' : 'text-loss'
}
function fmtDate(v) {
  if (!v) return '—'
  try {
    const d = new Date(v)
    if (isNaN(d.getTime())) return '—'
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  } catch { return '—' }
}

function viewStrategy(id)  { router.push(`/strategies/${id}`) }
function viewBacktest(bid) { if (bid != null) router.push(`/backtests/${bid}`) }

onMounted(fetchAll)
</script>

<template>
  <AppShell>
    <main class="ui-page">

      <!-- Header -->
      <header class="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <div class="min-w-0">
          <h1 class="ui-page-title">Strategies</h1>
          <p class="ui-page-sub">All trading strategies with their best AI backtest results</p>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <button
            @click="router.push({ path: '/instances', query: { createStrategy: '1' } })"
            class="ui-btn-primary"
          >
            Create strategy
          </button>
          <select
            v-model="perPage"
            @change="page = 1"
            aria-label="Strategies per page"
            class="ui-input w-auto h-8 pl-3 pr-8 text-[13px] tabular-nums cursor-pointer"
          >
            <option v-for="n in PER_PAGE_OPTIONS" :key="n" :value="n">{{ n }} per page</option>
          </select>
          <button
            @click="fetchAll"
            class="ui-icon-btn"
            title="Refresh"
            aria-label="Refresh strategies"
          >
            <span class="material-symbols-outlined text-[18px]" :class="loading ? 'animate-spin' : ''">refresh</span>
          </button>
        </div>
      </header>

      <!-- Loading -->
      <div v-if="loading" class="mt-8" aria-hidden="true">
        <div class="ui-skel h-4 w-32"></div>
        <div class="ui-card mt-3 ui-rows">
          <div v-for="n in 5" :key="n" class="h-14 px-5 flex items-center gap-6">
            <div class="ui-skel h-4 w-44"></div>
            <div class="ui-skel h-4 w-20 ml-auto"></div>
          </div>
        </div>
      </div>

      <template v-else>

        <!-- ── Top 5 best strategies ──────────────────────────────────────── -->
        <section v-if="top5.length > 0" class="mt-8" aria-labelledby="top5-title">
          <div class="mb-3">
            <h2 id="top5-title" class="ui-section-title">Top {{ top5.length }} strategies</h2>
            <p class="ui-section-sub">Ranked by P&amp;L %</p>
          </div>

          <div class="ui-card overflow-hidden">
            <div class="top-grid ui-th hidden md:grid">
              <span>#</span>
              <span>Strategy</span>
              <span class="text-right">Best P&amp;L</span>
              <span class="text-right">Best P&amp;L %</span>
              <span></span>
            </div>
            <ul class="ui-rows">
              <li
                v-for="entry in top5Enriched"
                :key="entry.strategy_id ?? entry.rank"
                class="top-grid min-h-14 px-5 py-3"
              >
                <span class="text-sm text-slate-500 tabular-nums">{{ entry.rank }}</span>
                <span class="min-w-0">
                  <span class="block text-sm text-slate-100 truncate">{{ entry.strategy_name || entry.strategy_id || 'Strategy' }}</span>
                  <span class="block text-xs text-slate-500 mt-0.5 truncate">
                    <span class="tabular-nums">{{ (entry.sub_strategies || []).length }}</span> sub-strategies<template v-if="(entry.sub_strategies || []).length"><span class="text-slate-600"> · </span><span
                      v-for="(sub, i) in (entry.sub_strategies || []).slice(0, 5)"
                      :key="i"
                    >{{ sub }}<template v-if="i < Math.min(5, (entry.sub_strategies || []).length) - 1">, </template></span><span
                      v-if="(entry.sub_strategies || []).length > 5"
                      class="tabular-nums"
                    > +{{ (entry.sub_strategies || []).length - 5 }} more</span></template>
                  </span>
                </span>
                <span class="top-pnl text-right text-sm tabular-nums" :class="pnlColor(entry.overall_profit)">
                  {{ fmtPnl(entry.overall_profit) }}
                </span>
                <span class="text-right text-sm tabular-nums" :class="pnlColor(entry.pnl_percent)">
                  {{ fmtPct(entry.pnl_percent) }}
                </span>
                <span class="top-actions flex items-center gap-1">
                  <button
                    v-if="entry.strategy_id"
                    @click="viewStrategy(entry.strategy_id)"
                    class="ui-btn"
                  >View strategy</button>
                  <button
                    v-if="entry.backtest_id"
                    @click="viewBacktest(entry.backtest_id)"
                    class="ui-btn-ghost"
                  >Backtest</button>
                </span>
              </li>
            </ul>
          </div>
        </section>

        <!-- ── Strategies table ────────────────────────────────────────────── -->
        <section :class="top5.length > 0 ? 'mt-12' : 'mt-8'" aria-labelledby="all-strategies-title">
          <div class="mb-3">
            <h2 id="all-strategies-title" class="ui-section-title">All strategies</h2>
            <p class="ui-section-sub tabular-nums">{{ sortedStrategies.length }} strategies</p>
          </div>
          <div class="ui-card overflow-hidden">
            <!-- Table -->
            <div class="overflow-x-auto">
              <table class="w-full min-w-[760px] text-sm">
                <thead>
                  <tr class="border-b border-white/[0.06] text-xs text-slate-500">
                    <th
                      class="h-10 pl-5 pr-3 text-left font-normal cursor-pointer select-none hover:text-slate-200 transition-colors duration-150 ease-out"
                      :class="sortBy === 'name' ? 'text-slate-300' : ''"
                      @click="setSort('name')"
                    >
                      <span class="inline-flex items-center gap-1">
                        Name
                        <span v-if="sortBy === 'name'" class="material-symbols-outlined text-[14px]">
                          {{ sortOrder === 'desc' ? 'arrow_downward' : 'arrow_upward' }}
                        </span>
                      </span>
                    </th>
                    <th class="h-10 px-3 text-right font-normal">Subs</th>
                    <th class="h-10 px-3 text-right font-normal">Instances</th>
                    <th
                      class="h-10 px-3 text-right font-normal cursor-pointer select-none hover:text-slate-200 transition-colors duration-150 ease-out"
                      :class="sortBy === 'runs' ? 'text-slate-300' : ''"
                      @click="setSort('runs')"
                    >
                      <span class="inline-flex items-center justify-end gap-1">
                        Backtests
                        <span v-if="sortBy === 'runs'" class="material-symbols-outlined text-[14px]">
                          {{ sortOrder === 'desc' ? 'arrow_downward' : 'arrow_upward' }}
                        </span>
                      </span>
                    </th>
                    <th
                      class="h-10 px-3 text-right font-normal cursor-pointer select-none hover:text-slate-200 transition-colors duration-150 ease-out"
                      :class="sortBy === 'best_pnl' ? 'text-slate-300' : ''"
                      @click="setSort('best_pnl')"
                    >
                      <span class="inline-flex items-center justify-end gap-1">
                        Best P&amp;L
                        <span v-if="sortBy === 'best_pnl'" class="material-symbols-outlined text-[14px]">
                          {{ sortOrder === 'desc' ? 'arrow_downward' : 'arrow_upward' }}
                        </span>
                      </span>
                    </th>
                    <th
                      class="h-10 px-3 text-right font-normal cursor-pointer select-none hover:text-slate-200 transition-colors duration-150 ease-out"
                      :class="sortBy === 'best_pct' ? 'text-slate-300' : ''"
                      @click="setSort('best_pct')"
                    >
                      <span class="inline-flex items-center justify-end gap-1">
                        Best P&amp;L %
                        <span v-if="sortBy === 'best_pct'" class="material-symbols-outlined text-[14px]">
                          {{ sortOrder === 'desc' ? 'arrow_downward' : 'arrow_upward' }}
                        </span>
                      </span>
                    </th>
                    <th class="h-10 pl-3 pr-5 font-normal"><span class="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/[0.05]">
                  <template v-if="pagedRows.length === 0">
                    <tr>
                      <td colspan="7">
                        <div class="ui-empty">
                          <p>No strategies found</p>
                        </div>
                      </td>
                    </tr>
                  </template>
                  <tr
                    v-for="row in pagedRows"
                    :key="row.id"
                    class="hover:bg-white/[0.03] transition-colors duration-150 ease-out"
                  >
                    <!-- Name -->
                    <td class="pl-5 pr-3 py-2.5">
                      <p class="text-sm text-slate-100 truncate max-w-[320px]">{{ row.name }}</p>
                      <p class="text-xs text-slate-500 mt-0.5 tabular-nums">
                        ID {{ row.id }}<template v-if="row.is_top5"><span class="text-slate-600"> · </span>Rank {{ row.top5_rank }}</template>
                      </p>
                    </td>

                    <!-- Subs -->
                    <td class="px-3 py-2.5 text-right tabular-nums text-slate-300">
                      {{ (row.strategies || []).length }}
                    </td>

                    <!-- Instances -->
                    <td class="px-3 py-2.5 text-right tabular-nums text-slate-400">
                      {{ (row.instances_using || []).length || '—' }}
                    </td>

                    <!-- Backtests -->
                    <td class="px-3 py-2.5 text-right tabular-nums" :class="row.run_count > 0 ? 'text-slate-300' : 'text-slate-600'">
                      {{ row.run_count || '—' }}
                    </td>

                    <!-- Best P&L -->
                    <td class="px-3 py-2.5 text-right tabular-nums" :class="pnlColor(row.best_pnl)">
                      {{ fmtPnl(row.best_pnl) }}
                    </td>

                    <!-- Best P&L% -->
                    <td class="px-3 py-2.5 text-right tabular-nums" :class="pnlColor(row.best_pct)">
                      {{ fmtPct(row.best_pct) }}
                    </td>

                    <!-- Actions -->
                    <td class="pl-3 pr-5 py-2.5">
                      <div class="flex items-center justify-end gap-1">
                        <button
                          @click="viewStrategy(row.id)"
                          class="ui-btn-ghost px-2.5"
                        >View</button>
                        <button
                          v-if="row.best_pnl_bid"
                          @click="viewBacktest(row.best_pnl_bid)"
                          class="ui-btn-ghost px-2.5"
                          title="View best backtest"
                        >Backtest</button>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- Pagination footer -->
            <div v-if="totalPages > 1" class="h-14 px-5 flex items-center justify-between gap-4 border-t border-white/[0.06]">
              <p class="text-xs text-slate-500 tabular-nums">{{ sortedStrategies.length }} strategies <span class="text-slate-600">·</span> page {{ page }} of {{ totalPages }}</p>
              <div class="flex items-center gap-1">
                <button
                  @click="page = Math.max(1, page - 1)"
                  :disabled="page <= 1"
                  class="ui-icon-btn disabled:opacity-30 disabled:cursor-default"
                  aria-label="Previous page"
                >
                  <span class="material-symbols-outlined text-[18px]">chevron_left</span>
                </button>
                <div class="ui-seg">
                  <template v-for="p in totalPages" :key="p">
                    <button
                      @click="page = p"
                      class="ui-seg-btn min-w-7 px-2 tabular-nums"
                      :class="p === page ? 'ui-seg-btn-active' : ''"
                      :aria-current="p === page ? 'page' : undefined"
                    >{{ p }}</button>
                  </template>
                </div>
                <button
                  @click="page = Math.min(totalPages, page + 1)"
                  :disabled="page >= totalPages"
                  class="ui-icon-btn disabled:opacity-30 disabled:cursor-default"
                  aria-label="Next page"
                >
                  <span class="material-symbols-outlined text-[18px]">chevron_right</span>
                </button>
              </div>
            </div>
          </div>
        </section>

      </template>
    </main>
  </AppShell>
</template>

<style scoped>
/* Top strategies, one ranked list.
   phone  rank · strategy · P&L %, actions on their own line
   md+    rank · strategy · best P&L · best P&L % · actions */
.top-grid {
  grid-template-columns: 16px minmax(0, 1fr) auto;
  column-gap: 16px;
  row-gap: 8px;
  align-items: center;
}
li.top-grid { display: grid; }
.top-grid > .top-pnl { display: none; }
.top-grid > .top-actions { grid-column: 2 / -1; justify-content: flex-start; }
@media (min-width: 768px) {
  .top-grid { grid-template-columns: 16px minmax(0, 1fr) 120px 96px 200px; }
  .top-grid > .top-pnl { display: block; }
  .top-grid > .top-actions { grid-column: auto; justify-content: flex-end; }
}
</style>
