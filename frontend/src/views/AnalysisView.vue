<!-- frontend/src/views/AnalysisView.vue
     AI Analysis: why a stock moved. Pick a holding or search a symbol; the
     chart marks every unusual day in the range (GET /analysis/moves), the
     strip under it shows each day's move, and the list and detail panel give
     the cause, the market / sector / stock-specific split, the confidence and
     the cited articles. "Explain moves" queues the unexplained days
     (POST /analysis/moves/explain) and the page re-reads every 3 s while any
     are pending. "Today in your account" splits the last session's move for
     each holding (GET /analysis/portfolio). The web twin of the iOS AI
     Analysis screen; spec docs/superpowers/specs/2026-11-14-why-it-moved-design.md.
     URL: /analysis?symbol=NVDA&range=3M -->
<template>
  <AppShell>
    <main class="ui-page">
      <!-- Header -->
      <header class="relative z-30 flex flex-wrap items-end justify-between gap-x-4 gap-y-4">
        <div class="min-w-0">
          <h1 class="ui-page-title">AI Analysis</h1>
          <p class="ui-page-sub">Why a stock moved: each day split into market, sector and stock-specific parts, with the cause named from that day's news</p>
        </div>

        <!-- Account: quick picks and "Today in your account" follow it -->
        <div v-if="accounts.length" ref="switcherEl" class="relative min-w-0 w-full sm:w-auto">
          <button
            type="button"
            class="h-9 w-full sm:w-auto max-w-full pl-3 pr-2 rounded-lg border border-white/[0.08] bg-surface flex items-center gap-2 text-left text-sm transition-colors duration-150 ease-out hover:bg-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/25"
            :class="menuOpen ? 'bg-raised' : ''"
            :aria-expanded="menuOpen"
            aria-haspopup="true"
            @click="menuOpen = !menuOpen"
          >
            <span class="min-w-0 flex-1 truncate text-slate-100">{{ account?.account_name }}</span>
            <span class="shrink-0 text-xs text-slate-500">{{ account ? accountModeLabel(account) : '' }}</span>
            <span class="material-symbols-outlined text-[18px] text-slate-500 shrink-0" aria-hidden="true">expand_more</span>
          </button>
          <div v-if="menuOpen" class="ui-menu left-0 sm:left-auto w-full sm:w-64" role="menu">
            <button
              v-for="a in accounts"
              :key="a.id"
              type="button"
              role="menuitemradio"
              class="ui-menu-item h-12 justify-between"
              :class="String(a.id) === String(accountId) ? 'bg-raised' : ''"
              :aria-checked="String(a.id) === String(accountId)"
              @click="selectAccount(a.id)"
            >
              <span class="min-w-0">
                <span class="block truncate">{{ a.account_name }}</span>
                <span class="block text-xs text-slate-500 mt-0.5">Alpaca · {{ accountModeLabel(a) }}</span>
              </span>
              <span v-if="String(a.id) === String(accountId)" class="material-symbols-outlined text-[16px] text-slate-400" aria-hidden="true">check</span>
            </button>
          </div>
        </div>
      </header>

      <!-- No model chosen -->
      <div v-if="modelConfigured === false" class="mt-6 ui-card px-5 py-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div class="min-w-0">
          <p class="text-sm text-slate-200 flex items-center gap-2"><span class="ui-dot ui-dot-warn"></span>No model chosen for AI Analysis</p>
          <p class="text-sm text-slate-500 mt-0.5">The chart and the split work without one. Naming the cause of a move needs a model</p>
        </div>
        <RouterLink to="/models#ai-analysis" class="ui-btn shrink-0">Choose a model in Settings</RouterLink>
      </div>

      <!-- Symbol picker -->
      <div class="mt-8">
        <SymbolPicker :picks="picks" :current="symbol" @pick="pickSymbol" />
      </div>

      <div v-if="!symbol" class="mt-8 ui-card">
        <div class="ui-empty">
          <p>Pick a holding or search for a stock to see why it moved</p>
        </div>
      </div>

      <div v-else class="analysis-grid mt-8">
        <!-- Chart -->
        <section class="area-chart min-w-0" aria-labelledby="symbol-title">
          <div class="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
            <div class="min-w-0">
              <div class="flex items-baseline gap-3">
                <h2 id="symbol-title" class="text-xl font-medium text-slate-100">{{ symbol }}</h2>
                <RouterLink :to="{ name: 'stock', params: { symbol } }" class="text-xs text-slate-500 hover:text-slate-200 transition-colors duration-150 ease-out">Stock page</RouterLink>
              </div>
              <p class="text-sm text-slate-500 mt-0.5 truncate">
                <template v-if="state === 'loading' && !moves"><span class="inline-block align-middle ui-skel h-3.5 w-48"></span></template>
                <template v-else>{{ symbolMeta }}</template>
              </p>
            </div>
            <div class="flex flex-col items-start sm:items-end gap-1.5">
              <button
                type="button"
                class="ui-btn-primary"
                :disabled="!canExplainAll"
                :title="explainHint"
                @click="explainAll"
              >
                <span v-if="explainBusy" class="material-symbols-outlined text-[16px] animate-spin" aria-hidden="true">progress_activity</span>
                Explain moves
              </button>
              <p v-if="pending > 0" class="text-xs text-slate-400 flex items-center gap-1.5" role="status">
                <span class="material-symbols-outlined text-[14px] text-slate-500 animate-spin" aria-hidden="true">progress_activity</span>
                Explaining {{ pending }} {{ pending === 1 ? 'move' : 'moves' }}
              </p>
              <p v-else-if="explainError" class="text-xs text-loss max-w-xs sm:text-right" role="alert">{{ explainError }}</p>
              <p v-else-if="explainHint" class="text-xs text-slate-500 max-w-xs sm:text-right">{{ explainHint }}</p>
            </div>
          </div>

          <!-- Range and category filter -->
          <div class="mt-5 flex flex-col gap-3 sm:flex-row sm:items-start">
            <div class="ui-seg shrink-0 self-start" role="group" aria-label="Range">
              <button
                v-for="r in ANALYSIS_RANGES"
                :key="r"
                type="button"
                class="ui-seg-btn tabular-nums"
                :class="range === r ? 'ui-seg-btn-active' : ''"
                :aria-pressed="range === r"
                @click="setRange(r)"
              >{{ r }}</button>
            </div>
            <div v-if="events.length" class="min-w-0 flex flex-wrap items-center gap-1.5 sm:pt-0.5" role="group" aria-label="Filter by category">
              <button
                v-for="c in chips"
                :key="c.key"
                type="button"
                class="chip"
                :class="filter === c.key ? 'chip-active' : ''"
                :aria-pressed="filter === c.key"
                @click="setFilter(c.key)"
              >
                <CategoryGlyph v-if="c.key !== ALL_KEY" :category="c.key" :hollow="c.key === UNEXPLAINED_KEY" />
                {{ c.label }}
                <span class="tabular-nums text-slate-500">{{ c.count }}</span>
              </button>
            </div>
          </div>

          <div class="mt-5">
            <div v-if="state === 'loading' && !moves" class="space-y-3" aria-hidden="true">
              <div class="ui-skel h-[280px] w-full rounded-lg"></div>
              <div class="ui-skel h-16 w-full rounded-lg"></div>
            </div>
            <div v-else-if="state === 'notfound'" class="ui-card">
              <div class="ui-empty">
                <p>No price history for {{ symbol }}. Check the ticker, or pick another symbol</p>
              </div>
            </div>
            <div v-else-if="state === 'error' && !moves" class="ui-card px-5 py-4 flex items-center justify-between gap-4">
              <p class="text-sm text-slate-400 min-w-0">{{ loadError }}</p>
              <button type="button" class="ui-btn shrink-0" @click="reloadMoves">Retry</button>
            </div>
            <template v-else-if="moves">
              <MoveChart
                :symbol="symbol"
                :range="range"
                :bars="bars"
                :events="shownEvents"
                :selected-id="eventId"
                :filter-active="filter !== ALL_KEY"
                @select="selectEvent"
              />
              <p v-if="state === 'error'" class="mt-2 text-xs text-slate-500">{{ loadError }}. Showing the last result</p>
            </template>
          </div>
        </section>

        <!-- Selected event -->
        <aside ref="detailEl" class="area-detail min-w-0 lg:sticky lg:top-6 scroll-mt-20" aria-label="Selected move">
          <MoveEventDetail
            v-if="moves"
            :event="selectedEvent"
            :sector-etf="moves.sector_etf || null"
            :model-configured="modelConfigured"
            :busy="!!(selectedEvent && eventBusy[selectedEvent.id])"
            :error="selectedEvent ? (eventErrors[selectedEvent.id] || '') : ''"
            @explain="explainOne"
          />
          <div v-else-if="state === 'loading'" class="ui-card p-5 space-y-3" aria-hidden="true">
            <div class="ui-skel h-3.5 w-32"></div>
            <div class="ui-skel h-5 w-56"></div>
            <div class="ui-skel h-3 w-full"></div>
            <div class="ui-skel h-3 w-2/3"></div>
          </div>
        </aside>

        <!-- Event list -->
        <section v-if="moves" class="area-list min-w-0" aria-labelledby="events-title">
          <div class="mb-3">
            <h2 id="events-title" class="ui-section-title">Unusual days</h2>
            <p class="ui-section-sub tabular-nums">{{ listSubtitle }}</p>
          </div>
          <div class="ui-card overflow-hidden">
            <div v-if="!events.length" class="px-5 py-8 text-center">
              <p class="text-sm text-slate-300">No unusual days for {{ symbol }} in this range</p>
              <p class="text-xs text-slate-500 mt-1">
                A day is flagged when the stock's own move stands out. The thresholds are in
                <RouterLink to="/models#ai-analysis" class="text-slate-400 underline decoration-white/20 underline-offset-2 hover:text-slate-200">Settings</RouterLink>
              </p>
            </div>
            <p v-else-if="!shownEvents.length" class="px-5 py-8 text-center text-sm text-slate-400">No days in this category</p>
            <ul v-else class="ui-rows">
              <li
                v-for="e in shownEvents"
                :key="e.id"
                class="ev-grid grid min-h-14 px-5 py-2.5 items-center cursor-pointer transition-colors duration-150 ease-out focus-visible:outline-none"
                :class="e.id === eventId ? 'bg-raised' : 'hover:bg-white/[0.03] focus-visible:bg-white/[0.04]'"
                role="button"
                tabindex="0"
                :aria-pressed="e.id === eventId"
                @click="selectEvent(e.id, { scroll: true })"
                @keydown.enter.prevent="selectEvent(e.id, { scroll: true })"
                @keydown.space.prevent="selectEvent(e.id, { scroll: true })"
              >
                <span class="text-sm text-slate-300 tabular-nums">{{ fmtEventDate(e.date) }}</span>
                <span class="min-w-0">
                  <span class="block text-sm truncate" :class="e.status === 'explained' ? 'text-slate-100' : 'text-slate-400'">
                    {{ e.status === 'explained' ? (e.headline || e.category_label || 'Explained') : statusText(e) }}
                  </span>
                  <span class="mt-0.5 flex items-center gap-1.5 min-w-0 text-xs text-slate-500">
                    <CategoryGlyph :category="e.category" :hollow="isHollow(e)" />
                    <span class="truncate">
                      <template v-if="e.status === 'explained' && e.category_label">{{ e.category_label }} · </template>Stock-specific {{ fmtPct(e.specific_pct) }}
                    </span>
                  </span>
                </span>
                <span class="text-right text-sm tabular-nums" :class="signClass(e.ret_pct)">{{ fmtPct(e.ret_pct) }}</span>
              </li>
            </ul>
          </div>
        </section>
      </div>

      <!-- Today in your account -->
      <div class="mt-12">
        <PortfolioMoves
          :data="portfolio"
          :loading="portfolioLoading"
          :error="accountsError || portfolioError"
          :account-name="account?.account_name || ''"
          :has-account="!accountsLoaded || accounts.length > 0 || !!accountsError"
          :current="symbol"
          @pick="pickSymbol($event, { scroll: true })"
          @retry="accountsError ? loadAccounts() : loadPortfolio()"
        />
      </div>
    </main>
  </AppShell>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import CategoryGlyph from '../components/analysis/CategoryGlyph.vue'
import MoveChart from '../components/analysis/MoveChart.vue'
import MoveEventDetail from '../components/analysis/MoveEventDetail.vue'
import PortfolioMoves from '../components/analysis/PortfolioMoves.vue'
import SymbolPicker from '../components/analysis/SymbolPicker.vue'
import { apiGet, loadPositions, saveAccountId, savedAccountId } from '../composables/useStockApi.js'
import { explainMoves, getAnalysisSettings, getMoves, getPortfolioMoves } from '../composables/useAnalysisApi.js'
import { fmtPct, signClass } from '../utils/dashboardAnalytics.js'
import { accountModeLabel, isTradableSymbol, normalizeSymbol, pickAccountId, tradableAccounts } from '../utils/trade.js'
import {
  ALL_KEY, ANALYSIS_RANGES, POLL_MS, UNEXPLAINED_KEY, apiFailure, categoryChips, explainableCount,
  filterEvents, fmtEventDate, isHollow, pendingCount, quickPicks, rangeFrom, sortNewestFirst, statusText,
} from '../utils/moveAnalysis.js'

const route = useRoute()
const router = useRouter()
let alive = true

// ── Accounts and quick picks ─────────────────────────────────────────────────
const accounts = ref([])
const accountsLoaded = ref(false)
const accountId = ref(null)
const account = computed(() => accounts.value.find(a => String(a.id) === String(accountId.value)) || null)
const menuOpen = ref(false)
const switcherEl = ref(null)
const picks = ref([])
let accountGen = 0

const accountsError = ref('')

async function loadAccounts() {
  accountsError.value = ''
  try {
    const d = await apiGet('/brokerages')
    accounts.value = tradableAccounts(d?.accounts)
  } catch {
    accounts.value = []
    accountsError.value = "Couldn't load your accounts"
  } finally {
    accountsLoaded.value = true
  }
  accountId.value = pickAccountId(accounts.value, route.query.account || savedAccountId())
}

function selectAccount(id) {
  menuOpen.value = false
  if (String(id) === String(accountId.value)) return
  accountId.value = id
  saveAccountId(id)
}

async function loadPicks() {
  const id = accountId.value
  if (id == null) return
  const gen = accountGen
  try {
    const d = await loadPositions(id)
    if (gen !== accountGen || !alive) return
    picks.value = quickPicks(d.positions)
    // No symbol in the URL: open on the largest holding.
    if (!symbol.value && picks.value.length) pickSymbol(picks.value[0], { replace: true })
  } catch {
    if (gen === accountGen) picks.value = []
  }
}

// ── Today in your account ────────────────────────────────────────────────────
const portfolio = ref(null)
const portfolioLoading = ref(false)
const portfolioError = ref('')

async function loadPortfolio() {
  const id = accountId.value
  if (id == null) return
  const gen = accountGen
  portfolioLoading.value = true
  const res = await getPortfolioMoves(id)
  if (gen !== accountGen || !alive) return
  portfolioLoading.value = false
  if (res.ok) {
    portfolio.value = res.body && typeof res.body === 'object' ? res.body : { holdings: [] }
    portfolioError.value = ''
  } else {
    portfolioError.value = apiFailure(res.status, res.body, "Couldn't load today's split")
  }
}

watch(accountId, id => {
  accountGen += 1
  picks.value = []
  portfolio.value = null
  portfolioError.value = ''
  if (id == null) return
  loadPicks()
  loadPortfolio()
})

// ── Symbol and range (kept in the URL) ───────────────────────────────────────
const symbol = ref(isTradableSymbol(route.query.symbol) ? normalizeSymbol(route.query.symbol) : '')
const range = ref(rangeFrom(route.query.range))

function syncQuery() {
  const query = { ...route.query, range: range.value }
  if (symbol.value) query.symbol = symbol.value
  else delete query.symbol
  router.replace({ query }).catch(() => {})
}

const detailEl = ref(null)
function scrollToChart() {
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  document.getElementById('symbol-title')?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
}

function pickSymbol(s, { scroll = false } = {}) {
  const next = normalizeSymbol(s)
  if (!isTradableSymbol(next)) return
  if (scroll) requestAnimationFrame(scrollToChart)
  if (next === symbol.value) return
  symbol.value = next
  syncQuery()
}

function setRange(r) {
  if (r === range.value) return
  range.value = r
  syncQuery()
}

// ── Moves ────────────────────────────────────────────────────────────────────
const moves = ref(null)          // the GET /analysis/moves payload for symbol + range
const state = ref('idle')        // idle | loading | ready | notfound | error
const loadError = ref('')
const eventId = ref(null)
const filter = ref(ALL_KEY)
let movesGen = 0
let pollTimer = null

function normalizeMoves(body) {
  const d = body && typeof body === 'object' ? body : {}
  const bars = (Array.isArray(d.bars) ? d.bars : []).filter(b => b?.date)
    .sort((a, b) => String(a.date).localeCompare(String(b.date)))
  return {
    ...d,
    bars,
    events: Array.isArray(d.events) ? d.events.filter(e => e?.id && e?.date) : [],
    categories: Array.isArray(d.categories) ? d.categories : [],
  }
}

async function loadMoves({ quiet = false } = {}) {
  const sym = symbol.value
  const rng = range.value
  if (!sym) return
  const gen = movesGen
  if (!quiet) {
    state.value = 'loading'
    loadError.value = ''
  }
  const res = quiet && document.visibilityState !== 'visible' ? null : await getMoves(sym, rng)
  if (gen !== movesGen || !alive) return
  if (res == null) { schedulePoll(); return }          // tab hidden: try again later
  const wasPending = pending.value > 0
  if (res.ok) {
    moves.value = normalizeMoves(res.body)
    state.value = 'ready'
    loadError.value = ''
    keepSelection()
    if (wasPending && pending.value === 0) loadPortfolio()   // fresh headlines for the account table
  } else if (res.status === 404) {
    moves.value = null
    state.value = 'notfound'
  } else {
    loadError.value = apiFailure(res.status, res.body, `Couldn't load moves for ${sym}`)
    state.value = 'error'
  }
  schedulePoll()
}

function schedulePoll() {
  clearTimeout(pollTimer)
  pollTimer = null
  if (!alive || !moves.value || pending.value <= 0) return
  const gen = movesGen
  pollTimer = setTimeout(() => { if (gen === movesGen) loadMoves({ quiet: true }) }, POLL_MS)
}

function resetMoves() {
  movesGen += 1
  clearTimeout(pollTimer)
  pollTimer = null
  moves.value = null
  eventId.value = null
  filter.value = ALL_KEY
  explainError.value = ''
  eventBusy.value = {}
  eventErrors.value = {}
}

function reloadMoves() {
  resetMoves()
  loadMoves()
}

watch([symbol, range], reloadMoves)

const bars = computed(() => moves.value?.bars || [])
const events = computed(() => sortNewestFirst(moves.value?.events || []))
const chips = computed(() => categoryChips(events.value, moves.value?.categories))
const shownEvents = computed(() => filterEvents(events.value, filter.value))
const selectedEvent = computed(() => events.value.find(e => e.id === eventId.value) || null)
const pending = computed(() => (moves.value ? pendingCount(moves.value) : 0))
const explainable = computed(() => explainableCount(events.value))

function keepSelection() {
  if (!chips.value.some(c => c.key === filter.value)) filter.value = ALL_KEY
  if (!shownEvents.value.some(e => e.id === eventId.value)) eventId.value = shownEvents.value[0]?.id ?? null
}

function setFilter(key) {
  filter.value = key
  if (!shownEvents.value.some(e => e.id === eventId.value)) eventId.value = shownEvents.value[0]?.id ?? null
}

function selectEvent(id, { scroll = false } = {}) {
  eventId.value = id
  // Phones and tablets: the panel sits above the list, so bring it into view.
  if (scroll && window.innerWidth < 1024 && detailEl.value) {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    detailEl.value.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
  }
}

function humanize(s) {
  const t = String(s || '').replace(/_/g, ' ').trim()
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : ''
}

const symbolMeta = computed(() => {
  const m = moves.value
  if (!m) return state.value === 'notfound' ? 'Unknown symbol' : ' '
  const parts = []
  if (m.sector) parts.push(humanize(m.sector))
  if (m.sector_etf) parts.push(`Sector ETF ${m.sector_etf}`)
  const beta = Number(m.beta)
  if (m.beta != null && Number.isFinite(beta)) parts.push(`Beta ${beta.toFixed(2)}`)
  return parts.join(' · ') || 'Sector not known'
})

const listSubtitle = computed(() => {
  const n = events.value.length
  if (!n) return 'Nothing flagged in this range'
  const explained = events.value.filter(e => e.status === 'explained').length
  return `${n} flagged · ${explained} explained, newest first`
})

// ── Model ────────────────────────────────────────────────────────────────────
const settingsModelId = ref(undefined)   // undefined until GET /analysis/settings answers

async function loadSettings() {
  const res = await getAnalysisSettings()
  if (!alive) return
  if (res.ok) settingsModelId.value = res.body?.model_id ?? null
}

const modelConfigured = computed(() => {
  const m = moves.value?.model_configured
  if (typeof m === 'boolean') return m
  if (settingsModelId.value === undefined) return null
  return !!settingsModelId.value
})

// ── Explain ──────────────────────────────────────────────────────────────────
const explainBusy = ref(false)
const explainError = ref('')
const eventBusy = ref({})
const eventErrors = ref({})

const canExplainAll = computed(() =>
  !!moves.value && !explainBusy.value && pending.value === 0 && explainable.value > 0 && modelConfigured.value !== false)

const explainHint = computed(() => {
  if (!moves.value || pending.value > 0) return ''
  if (modelConfigured.value === false) return 'Choose a model in Settings first'
  if (!events.value.length) return ''
  if (!explainable.value) return 'Every flagged day in this range is explained'
  return `${explainable.value} ${explainable.value === 1 ? 'day' : 'days'} to explain`
})

/** Show the queued days as pending at once; the re-read right after is the truth. */
function markPending(dates) {
  if (!moves.value) return
  const want = new Set(dates)
  let n = 0
  const evs = moves.value.events.map(e => {
    if (!want.has(e.date) || e.status === 'pending') return e
    n += 1
    return { ...e, status: 'pending' }
  })
  moves.value = { ...moves.value, events: evs, pending: (Number(moves.value.pending) || 0) + n }
}

async function explainAll() {
  if (!canExplainAll.value) return
  const sym = symbol.value
  const rng = range.value
  const gen = movesGen
  const dates = events.value.filter(e => e.status === 'unexplained' || e.status === 'failed').map(e => e.date)
  explainBusy.value = true
  explainError.value = ''
  const res = await explainMoves({ symbol: sym, range: rng })
  if (gen !== movesGen || !alive) return
  explainBusy.value = false
  if (!res.ok) {
    explainError.value = apiFailure(res.status, res.body, "Couldn't start the explanations")
    return
  }
  if ((Number(res.body?.queued) || 0) > 0) markPending(dates)
  await loadMoves({ quiet: true })
}

async function explainOne(force) {
  const ev = selectedEvent.value
  if (!ev || eventBusy.value[ev.id]) return
  const gen = movesGen
  eventBusy.value = { ...eventBusy.value, [ev.id]: true }
  eventErrors.value = { ...eventErrors.value, [ev.id]: '' }
  const res = await explainMoves({ symbol: symbol.value, range: range.value, dates: [ev.date], force: !!force })
  if (gen !== movesGen || !alive) return
  eventBusy.value = { ...eventBusy.value, [ev.id]: false }
  if (!res.ok) {
    eventErrors.value = { ...eventErrors.value, [ev.id]: apiFailure(res.status, res.body, "Couldn't start the explanation") }
    return
  }
  if ((Number(res.body?.queued) || 0) > 0) markPending([ev.date])
  await loadMoves({ quiet: true })
}

// ── Page plumbing ────────────────────────────────────────────────────────────
function onDocPointer(e) {
  if (menuOpen.value && switcherEl.value && !switcherEl.value.contains(e.target)) menuOpen.value = false
}
function onDocKey(e) {
  if (e.key === 'Escape' && menuOpen.value) menuOpen.value = false
}

onMounted(() => {
  loadAccounts()
  loadSettings()
  if (symbol.value) loadMoves()
  syncQuery()
  document.addEventListener('pointerdown', onDocPointer)
  document.addEventListener('keydown', onDocKey)
})

onUnmounted(() => {
  alive = false
  movesGen += 1
  clearTimeout(pollTimer)
  document.removeEventListener('pointerdown', onDocPointer)
  document.removeEventListener('keydown', onDocKey)
})
</script>

<style scoped>
/* Phone: chart, then the selected move, then the list.
   Desktop: chart and list on the left, the selected move pinned on the right. */
.analysis-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  grid-template-areas: "chart" "detail" "list";
  row-gap: 40px;
}
@media (min-width: 1024px) {
  .analysis-grid {
    grid-template-columns: minmax(0, 1fr) 360px;
    grid-template-areas: "chart detail" "list detail";
    column-gap: 32px;
    align-items: start;
  }
}
.area-chart { grid-area: chart; }
.area-detail { grid-area: detail; align-self: start; }
.area-list { grid-area: list; }

.ev-grid {
  grid-template-columns: 52px minmax(0, 1fr) auto;
  column-gap: 16px;
}

.chip {
  @apply h-7 px-2.5 shrink-0 inline-flex items-center gap-1.5 rounded-md border border-white/[0.07] bg-surface text-xs font-medium text-slate-400 whitespace-nowrap
    transition-colors duration-150 ease-out hover:text-slate-100 hover:bg-raised
    focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/25;
}
.chip-active {
  @apply bg-raised text-slate-100 border-white/[0.14];
}
</style>
