<!-- frontend/src/components/analysis/SymbolPicker.vue
     Which symbol the AI Analysis screen explains: the selected account's
     holdings as one-tap picks, and a search box over GET /symbols/search
     (250 ms after the last keystroke). Arrow keys move, Enter picks (a
     typed ticker works even when search finds nothing), Escape closes.
     Unlike the app-wide SymbolSearch it loads the symbol here instead of
     opening the stock page. -->
<template>
  <div class="flex flex-col gap-3 md:flex-row md:items-center md:gap-4">
    <div ref="wrapEl" class="relative w-full md:w-72 shrink-0">
      <span class="material-symbols-outlined pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[18px] text-slate-500" aria-hidden="true">search</span>
      <input
        v-model="query"
        type="text"
        role="combobox"
        aria-label="Search a stock or ETF"
        aria-autocomplete="list"
        :aria-expanded="menuOpen"
        :aria-controls="listId"
        :aria-activedescendant="menuOpen && items[active] ? `${listId}-${active}` : undefined"
        placeholder="Search a stock or ETF"
        autocomplete="off"
        autocapitalize="off"
        spellcheck="false"
        class="ui-input pl-9"
        @focus="focused = true"
        @input="focused = true"
        @keydown="onKey"
      />
      <span v-if="loading" class="material-symbols-outlined absolute right-3 top-1/2 -translate-y-1/2 text-[16px] text-slate-500 animate-spin" aria-hidden="true">progress_activity</span>

      <div v-if="menuOpen" class="ui-menu left-0 right-auto w-full max-h-72 overflow-y-auto">
        <p v-if="error" class="px-3 py-3 text-sm text-slate-400">{{ error }}</p>
        <ul v-else-if="items.length" :id="listId" role="listbox" aria-label="Symbols">
          <li
            v-for="(it, i) in items"
            :id="`${listId}-${i}`"
            :key="it.symbol"
            role="option"
            :aria-selected="i === active"
            class="h-11 px-3 rounded-lg flex items-center justify-between gap-3 cursor-pointer"
            :class="i === active ? 'bg-raised' : ''"
            @mousemove="active = i"
            @mousedown.prevent="choose(it.symbol)"
          >
            <span class="min-w-0">
              <span class="block text-sm font-medium text-slate-100 truncate">{{ it.symbol }}</span>
              <span v-if="it.sub" class="block text-xs text-slate-500 truncate">{{ it.sub }}</span>
            </span>
          </li>
        </ul>
        <p v-else-if="!loading" class="px-3 py-3 text-sm text-slate-400">No matching symbols</p>
        <p v-else class="px-3 py-3 text-sm text-slate-500">Searching…</p>
      </div>
    </div>

    <div v-if="picks.length" class="min-w-0 flex items-center gap-1.5 overflow-x-auto pb-0.5" role="group" aria-label="Your holdings">
      <span class="shrink-0 mr-1 text-xs text-slate-500">Holdings</span>
      <button
        v-for="s in picks"
        :key="s"
        type="button"
        class="pick"
        :class="sameSymbol(s, current) ? 'pick-active' : ''"
        :aria-pressed="sameSymbol(s, current)"
        @click="choose(s)"
      >{{ s }}</button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { searchSymbols } from '../../composables/useAnalysisApi.js'
import { normalizeSymbol } from '../../utils/trade.js'
import { analyzableResults, isAnalyzableSymbol, sameSymbol } from '../../utils/moveAnalysis.js'

const props = defineProps({
  picks: { type: Array, default: () => [] },
  current: { type: String, default: '' },
})
const emit = defineEmits(['pick'])

const DEBOUNCE_MS = 250
const listId = `analysis-symbols-${Math.random().toString(36).slice(2, 8)}`
const query = ref('')
const results = ref([])
const loading = ref(false)
const error = ref('')
const active = ref(0)
const focused = ref(false)
const wrapEl = ref(null)
let debounce = null
let gen = 0

const items = computed(() => {
  const out = results.value.map(r => ({
    symbol: normalizeSymbol(r.symbol),
    sub: [r.name, r.type].filter(Boolean).join(' · '),
  }))
  const typed = normalizeSymbol(query.value)
  if (isAnalyzableSymbol(typed) && !out.some(it => sameSymbol(it.symbol, typed))) {
    out.push({ symbol: typed, sub: `Analyze ${typed}` })
  }
  return out
})

const menuOpen = computed(() => focused.value && query.value.trim().length > 0)

watch(query, text => {
  clearTimeout(debounce)
  active.value = 0
  const q = text.trim()
  if (!q) {
    gen += 1
    results.value = []
    loading.value = false
    error.value = ''
    return
  }
  loading.value = true
  debounce = setTimeout(() => run(q), DEBOUNCE_MS)
})

async function run(q) {
  const mine = ++gen
  error.value = ''
  const res = await searchSymbols(q)
  if (mine !== gen) return
  loading.value = false
  if (!res.ok) {
    results.value = []
    error.value = res.status ? "Search isn't available right now. Type a ticker and press Enter" : "Couldn't reach search. Type a ticker and press Enter"
    return
  }
  // Crypto and options can't be analyzed, so they aren't offered.
  results.value = analyzableResults(Array.isArray(res.body?.results) ? res.body.results : []).slice(0, 12)
}

function choose(symbol) {
  const s = normalizeSymbol(symbol)
  if (!isAnalyzableSymbol(s)) return
  emit('pick', s)
  clearTimeout(debounce)
  gen += 1
  query.value = ''
  focused.value = false
}

function onKey(e) {
  const n = items.value.length
  if (e.key === 'ArrowDown' && n) { e.preventDefault(); focused.value = true; active.value = (active.value + 1) % n }
  else if (e.key === 'ArrowUp' && n) { e.preventDefault(); active.value = (active.value - 1 + n) % n }
  else if (e.key === 'Enter') {
    e.preventDefault()
    const it = items.value[active.value]
    choose(it ? it.symbol : query.value)
  } else if (e.key === 'Escape') {
    if (query.value) query.value = ''
    focused.value = false
  }
}

function onDocPointer(e) {
  if (wrapEl.value && !wrapEl.value.contains(e.target)) focused.value = false
}

onMounted(() => document.addEventListener('pointerdown', onDocPointer))
onUnmounted(() => {
  document.removeEventListener('pointerdown', onDocPointer)
  clearTimeout(debounce)
})
</script>

<style scoped>
.pick {
  @apply h-7 px-2.5 shrink-0 inline-flex items-center rounded-md border border-white/[0.07] bg-surface text-xs font-medium text-slate-400
    transition-colors duration-150 ease-out hover:text-slate-100 hover:bg-raised
    focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/25;
}
.pick-active {
  @apply bg-raised text-slate-100 border-white/[0.14];
}
</style>
