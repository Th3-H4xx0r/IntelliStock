<!-- frontend/src/components/SymbolSearch.vue
     Search stocks and ETFs from anywhere in the app, the web twin of the iOS
     SymbolSearchView (ios/IntelliStock/Features/SymbolSearch). Press / or
     ⌘K (Ctrl K), or a search trigger in the app shell (they set the shared
     `symbolSearchOpen`), type a name or ticker; GET /symbols/search runs
     250 ms after the last keystroke. Your holdings across the linked Alpaca
     accounts come first when they match. Arrow keys move, Enter opens the
     stock page, Escape closes. Each row is priced from today's history.
     AppShell mounts exactly one of these. -->
<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="ui-overlay items-start p-0 sm:p-4 sm:pt-[12vh]"
      @click.self="hide"
    >
      <div
        class="ui-modal max-w-xl h-full sm:h-auto rounded-none sm:rounded-2xl border-0 sm:border flex flex-col"
        role="dialog"
        aria-modal="true"
        aria-label="Search stocks"
      >
        <div class="h-14 px-4 flex items-center gap-3 border-b border-white/[0.06] shrink-0">
          <span class="material-symbols-outlined text-[20px] text-slate-500" aria-hidden="true">search</span>
          <input
            ref="inputEl"
            v-model="query"
            type="text"
            role="combobox"
            aria-autocomplete="list"
            :aria-expanded="items.length > 0"
            :aria-controls="listId"
            :aria-activedescendant="items[active] ? `${listId}-${active}` : undefined"
            placeholder="Search stocks and ETFs"
            autocomplete="off"
            autocapitalize="off"
            spellcheck="false"
            class="flex-1 min-w-0 h-full bg-transparent text-base text-slate-100 placeholder-slate-600 focus:outline-none"
            @keydown="onInputKey"
          />
          <span v-if="loading" class="material-symbols-outlined text-[18px] text-slate-500 animate-spin" aria-hidden="true">progress_activity</span>
          <button type="button" class="kbd hidden sm:inline-flex hover:text-slate-300" @click="hide">Esc</button>
          <button type="button" class="ui-icon-btn sm:hidden" aria-label="Close search" @click="hide">
            <span class="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>

        <div ref="listEl" class="flex-1 sm:flex-none sm:max-h-[min(60vh,520px)] overflow-y-auto py-2">
          <!-- Failed search -->
          <div v-if="error" class="px-4 py-8 text-center">
            <p class="text-sm text-slate-300">Couldn't reach market search</p>
            <p class="text-xs text-slate-500 mt-1">{{ error }}</p>
            <button type="button" class="ui-btn mt-4" @click="runSearch(query)">Retry</button>
          </div>

          <ul v-else-if="items.length" :id="listId" role="listbox" aria-label="Results">
            <template v-for="(it, i) in items" :key="it.key">
              <li
                v-if="it.groupLabel"
                role="presentation"
                class="px-4 pt-3 pb-1.5 text-xs text-slate-500 first:pt-1"
              >{{ it.groupLabel }}</li>
              <li
                :id="`${listId}-${i}`"
                role="option"
                :aria-selected="i === active"
                :data-index="i"
                class="mx-2 h-12 px-3 rounded-lg flex items-center justify-between gap-4 cursor-pointer"
                :class="i === active ? 'bg-raised' : ''"
                @mousemove="active = i"
                @click="choose(it)"
              >
                <span class="min-w-0">
                  <span class="block text-sm font-medium text-slate-100 truncate">{{ it.symbol }}</span>
                  <span class="block text-xs text-slate-500 mt-0.5 truncate">{{ it.sub }}</span>
                </span>
                <span v-if="!it.isOpen" class="shrink-0 text-right tabular-nums">
                  <template v-if="quotes[it.symbol]">
                    <span class="block text-sm text-slate-200">{{ fmtPrice(quotes[it.symbol].price) }}</span>
                    <span class="block text-xs mt-0.5" :class="signClass(quotes[it.symbol].changePct)">{{ fmtPct(quotes[it.symbol].changePct) }}</span>
                  </template>
                  <template v-else-if="quotesLoading">
                    <span class="block ui-skel h-3.5 w-14 ml-auto"></span>
                    <span class="block ui-skel h-3 w-10 ml-auto mt-1.5"></span>
                  </template>
                </span>
                <span v-else class="material-symbols-outlined text-[18px] text-slate-500 shrink-0" aria-hidden="true">arrow_forward</span>
              </li>
            </template>
          </ul>

          <!-- Searching, nothing to show yet -->
          <ul v-else-if="loading" aria-hidden="true">
            <li v-for="n in 5" :key="n" class="mx-2 h-12 px-3 flex items-center justify-between">
              <span class="space-y-1.5">
                <span class="block ui-skel h-3.5 w-14"></span>
                <span class="block ui-skel h-3 w-40"></span>
              </span>
              <span class="ui-skel h-3.5 w-14"></span>
            </li>
          </ul>

          <p v-else-if="query.trim()" class="px-4 py-10 text-center text-sm text-slate-400">No matching symbols</p>
          <p v-else class="px-4 py-10 text-center text-sm text-slate-400">Find a stock or ETF by name or ticker</p>
        </div>

        <div class="hidden sm:flex h-10 px-4 items-center gap-4 border-t border-white/[0.06] text-xs text-slate-500 shrink-0">
          <span class="inline-flex items-center gap-1.5"><kbd class="kbd inline-flex">↑</kbd><kbd class="kbd inline-flex">↓</kbd> to move</span>
          <span class="inline-flex items-center gap-1.5"><kbd class="kbd inline-flex">Enter</kbd> to open</span>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { apiGet, loadDayValues, loadPositions } from '../composables/useStockApi.js'
import { symbolSearchOpen as open } from '../composables/useSymbolSearch.js'
import { fmtPct, fmtPrice, signClass } from '../utils/dashboardAnalytics.js'
import { isTradableSymbol, normalizeSymbol, plainQtyText, tradableAccounts } from '../utils/trade.js'
import { matchingHoldings, resultsWithoutHoldings, searchQuote, uniqueSymbols } from '../utils/stock.js'

const router = useRouter()
const DEBOUNCE_MS = 250
const listId = `symbol-search-${Math.random().toString(36).slice(2, 8)}`
const query = ref('')
const results = ref(null)    // null before the first search and while one runs
const loading = ref(false)
const error = ref('')
const active = ref(0)
const quotes = ref({})
const quotesLoading = ref(false)
const inputEl = ref(null)
const listEl = ref(null)

let debounce = null
let searchGen = 0
let quoteGen = 0

// ── Holdings across the linked Alpaca accounts (loaded once per page) ────────
const holdings = ref([])     // [{ symbol, qty, marketValue, accounts: [name] }]
let holdingsLoaded = false

async function loadHoldings() {
  if (holdingsLoaded) return
  holdingsLoaded = true
  try {
    const d = await apiGet('/brokerages')
    const accounts = tradableAccounts(d?.accounts)
    const books = await Promise.all(accounts.map(a => loadPositions(a.id).then(b => ({ a, b })).catch(() => null)))
    const bySymbol = new Map()
    for (const entry of books) {
      if (!entry) continue
      for (const p of entry.b.positions) {
        const cur = bySymbol.get(p.symbol) || { symbol: p.symbol, qty: 0, marketValue: 0, accounts: [], accountId: entry.a.id }
        cur.qty += p.qty
        cur.marketValue += p.marketValue
        cur.accounts.push(entry.a.account_name)
        bySymbol.set(p.symbol, cur)
      }
    }
    holdings.value = [...bySymbol.values()]
    if (open.value) loadQuotes()
  } catch {
    holdings.value = []
  }
}

function holdingSub(h) {
  const where = h.accounts.length === 1 ? h.accounts[0] : `${h.accounts.length} accounts`
  return `${plainQtyText(h.qty)} sh · ${where}`
}

// ── The list: holdings first, then search results, then "Open SYMBOL" ────────
const items = computed(() => {
  const q = query.value.trim()
  const held = matchingHoldings(holdings.value, q).slice(0, q ? 5 : 8)
  const out = held.map((h, i) => ({
    key: `h-${h.symbol}`,
    groupLabel: i === 0 ? 'Your holdings' : '',
    symbol: h.symbol,
    sub: holdingSub(h),
    accountId: h.accounts.length === 1 ? h.accountId : null,
  }))
  if (!q) return out
  const found = resultsWithoutHoldings(results.value || [], held)
  found.forEach((r, i) => out.push({
    key: `r-${r.symbol}`,
    groupLabel: i === 0 ? 'Stocks and ETFs' : '',
    symbol: String(r.symbol).toUpperCase(),
    sub: [r.name, r.type].filter(Boolean).join(' · '),
  }))
  const typed = normalizeSymbol(q)
  if (results.value && isTradableSymbol(typed) && !out.some(it => it.symbol === typed)) {
    out.push({ key: `o-${typed}`, groupLabel: out.length ? '' : 'Go to', symbol: typed, sub: `Open the ${typed} page`, isOpen: true })
  }
  return out
})

watch(items, list => { if (active.value >= list.length) active.value = 0 })

// ── Search ───────────────────────────────────────────────────────────────────
async function runSearch(text) {
  const trimmed = String(text || '').trim()
  const gen = ++searchGen
  if (!trimmed) {
    results.value = null
    loading.value = false
    error.value = ''
    return
  }
  loading.value = true
  error.value = ''
  try {
    const d = await apiGet(`/symbols/search?${new URLSearchParams({ q: trimmed })}`)
    if (gen !== searchGen) return
    results.value = (Array.isArray(d?.results) ? d.results : []).filter(r => r?.symbol)
    loadQuotes()
  } catch {
    if (gen !== searchGen) return
    error.value = 'Check your connection and try again'
    results.value = null
  } finally {
    if (gen === searchGen) loading.value = false
  }
}

watch(query, text => {
  clearTimeout(debounce)
  active.value = 0
  if (!text.trim()) {
    runSearch('')
    loadQuotes()
    return
  }
  results.value = null
  loading.value = true
  debounce = setTimeout(() => runSearch(text), DEBOUNCE_MS)
})

/** One 1D history batch for the symbols in view. */
async function loadQuotes() {
  const symbols = uniqueSymbols(items.value.filter(it => !it.isOpen)).filter(s => !(s in quotes.value)).slice(0, 20)
  if (!symbols.length) return
  const gen = ++quoteGen
  quotesLoading.value = true
  try {
    const values = await loadDayValues(symbols)
    const next = { ...quotes.value }
    for (const s of symbols) {
      const q = searchQuote(values[s])
      next[s] = q
    }
    quotes.value = next
  } catch { /* rows show no price */ } finally {
    if (gen === quoteGen) quotesLoading.value = false
  }
}

// ── Open / close / choose ────────────────────────────────────────────────────
function show() {
  open.value = true
}

function onOpened() {
  active.value = 0
  loadHoldings()
  loadQuotes()
  nextTick(() => inputEl.value?.focus())
}

function hide() {
  open.value = false
  clearTimeout(debounce)
}

function choose(it) {
  if (!it) return
  hide()
  query.value = ''
  const target = { name: 'stock', params: { symbol: it.symbol } }
  if (it.accountId != null) target.query = { account: String(it.accountId) }
  router.push(target)
}

function move(delta) {
  const n = items.value.length
  if (!n) return
  active.value = (active.value + delta + n) % n
  nextTick(() => {
    listEl.value?.querySelector(`[data-index="${active.value}"]`)?.scrollIntoView({ block: 'nearest' })
  })
}

function onInputKey(e) {
  if (e.key === 'ArrowDown') { e.preventDefault(); move(1) }
  else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1) }
  else if (e.key === 'Enter') { e.preventDefault(); choose(items.value[active.value]) }
  else if (e.key === 'Escape') { e.preventDefault(); hide() }
}

function isEditable(target) {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return target.isContentEditable || tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
}

function onGlobalKey(e) {
  if ((e.metaKey || e.ctrlKey) && !e.altKey && String(e.key).toLowerCase() === 'k') {
    if (!open.value && document.querySelector('[aria-modal="true"]')) return   // another dialog is up
    e.preventDefault()
    if (open.value) hide()
    else show()
    return
  }
  if (e.key === '/' && !open.value && !e.metaKey && !e.ctrlKey && !e.altKey && !isEditable(e.target)) {
    if (document.querySelector('[aria-modal="true"]')) return   // another dialog is up
    e.preventDefault()
    show()
  }
}

// A trigger in the shell opens the palette by setting the shared ref.
watch(open, isOpen => { if (isOpen) onOpened() })

onMounted(() => {
  window.addEventListener('keydown', onGlobalKey)
  if (open.value) onOpened()
})
onUnmounted(() => {
  window.removeEventListener('keydown', onGlobalKey)
  clearTimeout(debounce)
  open.value = false
})
</script>

<style scoped>
.kbd {
  align-items: center;
  height: 20px;
  padding: 0 6px;
  border-radius: 5px;
  border: 1px solid rgba(237, 237, 237, 0.1);
  background: #1e1e20;
  font-family: inherit;
  font-size: 11px;
  line-height: 1;
  color: #85858b;
  font-variant-numeric: tabular-nums;
}
</style>
