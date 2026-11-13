<!-- frontend/src/views/StockView.vue
     One stock: the web twin of the iOS StockView
     (ios/IntelliStock/Features/Stock/Views/StockView.swift). The live price
     and chart with its ranges, your position in the chosen account, that
     account's open and recent orders in the symbol (open ones can be
     canceled), the bot's decisions, key statistics and About. Buy and Sell
     open the order ticket (components/stock/TradeTicket.vue). -->
<template>
  <AppShell>
    <main class="ui-page">
      <!-- Header -->
      <header class="relative z-30 flex flex-wrap items-end justify-between gap-x-4 gap-y-4">
        <div class="min-w-0 flex items-start gap-2">
          <button type="button" class="ui-icon-btn -ml-2 mt-0.5 shrink-0" aria-label="Back" @click="goBack">
            <span class="material-symbols-outlined text-[18px]">arrow_back</span>
          </button>
          <div class="min-w-0">
            <h1 class="ui-page-title">{{ symbol }}</h1>
            <p class="ui-page-sub truncate">
              <template v-if="info == null"><span class="inline-block align-middle ui-skel h-3.5 w-36"></span></template>
              <template v-else>{{ subtitle }}</template>
            </p>
          </div>
        </div>

        <div class="flex items-center gap-2 w-full sm:w-auto">
          <!-- Account: positions, orders and the ticket follow it -->
          <div v-if="accounts.length" ref="switcherEl" class="relative min-w-0 flex-1 sm:flex-none">
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
                :class="String(a.id) === String(selectedId) ? 'bg-raised' : ''"
                :aria-checked="String(a.id) === String(selectedId)"
                @click="selectAccount(a.id)"
              >
                <span class="min-w-0">
                  <span class="block truncate">{{ a.account_name }}</span>
                  <span class="block text-xs text-slate-500 mt-0.5">Alpaca · {{ accountModeLabel(a) }}</span>
                </span>
                <span v-if="String(a.id) === String(selectedId)" class="material-symbols-outlined text-[16px] text-slate-400" aria-hidden="true">check</span>
              </button>
            </div>
          </div>
          <button type="button" class="ui-btn h-9 px-4 shrink-0" :disabled="!canTrade" @click="openTicket('sell')">Sell</button>
          <button type="button" class="ui-btn-primary shrink-0" :disabled="!canTrade" @click="openTicket('buy')">Buy</button>
        </div>
      </header>

      <p v-if="!tradable" class="mt-4 text-sm text-slate-500">Trading options and crypto from the app isn't supported. Stocks and ETFs only</p>
      <p v-else-if="accountsLoaded && !accounts.length" class="mt-4 text-sm text-slate-500">
        Link an Alpaca account on <RouterLink to="/brokerages" class="text-slate-300 underline decoration-white/20 underline-offset-2 hover:text-slate-100">Brokerages</RouterLink> to trade from here
      </p>

      <div class="mt-8 grid grid-cols-1 gap-x-8 gap-y-12 items-start lg:grid-cols-[minmax(0,1fr)_340px]">
        <div class="min-w-0 flex flex-col gap-12">
          <StockChart :symbol="symbol" @series="onSeries" />

          <!-- Your position -->
          <section v-if="accounts.length" aria-labelledby="position-title">
            <div class="mb-3">
              <h2 id="position-title" class="ui-section-title">Your position</h2>
              <p class="ui-section-sub truncate">{{ account?.account_name }} · {{ account ? accountModeLabel(account) : '' }}</p>
            </div>
            <div v-if="holdings == null" class="ui-card p-5" aria-hidden="true">
              <div class="grid grid-cols-2 sm:grid-cols-4 gap-6">
                <div v-for="n in 4" :key="n" class="space-y-2">
                  <div class="ui-skel h-3 w-16"></div>
                  <div class="ui-skel h-5 w-24"></div>
                </div>
              </div>
            </div>
            <div v-else-if="position" class="ui-card p-5">
              <div class="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-5">
                <div class="min-w-0">
                  <p class="ui-label">Value</p>
                  <p class="text-2xl font-medium text-slate-100 tabular-nums mt-1 truncate">{{ fmtUsd(position.marketValue) }}</p>
                  <p v-if="weight != null" class="text-xs text-slate-500 mt-0.5 tabular-nums">{{ weight.toFixed(1) }}% of the account</p>
                </div>
                <div class="min-w-0">
                  <p class="ui-label">Total P&amp;L</p>
                  <p class="text-2xl font-medium tabular-nums mt-1 truncate" :class="signClass(position.unrealizedPnl)">{{ fmtSignedUsd(position.unrealizedPnl) }}</p>
                  <p class="text-xs mt-0.5 tabular-nums" :class="signClass(position.unrealizedPnlPct)">{{ fmtPct(position.unrealizedPnlPct) }} since bought</p>
                </div>
                <div class="min-w-0">
                  <p class="ui-label">Shares</p>
                  <p class="text-base text-slate-100 tabular-nums mt-1.5 truncate">{{ plainQtyText(position.qty) }}<span v-if="position.qty < 0" class="text-slate-500"> short</span></p>
                </div>
                <div class="min-w-0">
                  <p class="ui-label">Average cost</p>
                  <p class="text-base text-slate-100 tabular-nums mt-1.5 truncate">{{ fmtPrice(position.avgEntryPrice) }}</p>
                </div>
              </div>
            </div>
            <div v-else class="ui-card">
              <div class="ui-empty py-10">
                <p>You don't hold {{ symbol }} in {{ account?.account_name }}</p>
                <button v-if="canTrade" type="button" class="ui-btn" @click="openTicket('buy')">Buy {{ symbol }}</button>
              </div>
            </div>
          </section>

          <!-- Orders in this symbol -->
          <section v-if="accounts.length" aria-labelledby="orders-title">
            <div class="mb-3">
              <h2 id="orders-title" class="ui-section-title">Orders</h2>
              <p class="ui-section-sub tabular-nums">{{ ordersSubtitle }}</p>
            </div>
            <div class="ui-card overflow-hidden">
              <div class="sym-ord-grid ui-th hidden md:grid">
                <span>Order</span>
                <span>Status</span>
                <span class="text-right">Price</span>
                <span class="text-right">Value</span>
                <span class="text-right">Time</span>
                <span></span>
              </div>

              <ul v-if="book == null && !bookError" class="ui-rows" aria-hidden="true">
                <li v-for="n in 3" :key="n" class="h-14 px-5 flex items-center justify-between gap-4">
                  <span class="space-y-2">
                    <span class="block h-3.5 w-24 ui-skel"></span>
                    <span class="block h-3 w-16 ui-skel"></span>
                  </span>
                  <span class="block h-3.5 w-20 ui-skel"></span>
                </li>
              </ul>
              <div v-else-if="book == null" class="h-14 px-5 flex items-center justify-between gap-4 text-sm">
                <span class="text-slate-400">{{ bookError }}</span>
                <button type="button" class="ui-btn" @click="loadBook">Retry</button>
              </div>
              <div v-else-if="!orders.open.length && !orders.recent.length" class="px-5 py-8 text-center">
                <p class="text-sm text-slate-300">No orders for {{ symbol }} in this account</p>
                <p class="text-xs text-slate-500 mt-1">Your orders and the bot's show here</p>
              </div>
              <template v-else>
                <template v-for="(g, gi) in orderGroups" :key="g.key">
                  <div
                    class="h-8 px-5 flex items-center justify-between text-xs text-slate-500 bg-white/[0.015] border-b border-white/[0.05]"
                    :class="gi ? 'border-t border-white/[0.06]' : ''"
                  >
                    <span>{{ g.label }}</span>
                    <span class="tabular-nums">{{ g.rows.length }}</span>
                  </div>
                  <ul class="ui-rows">
                    <li v-for="r in g.rows" :key="r.id" class="sym-ord-grid grid min-h-14 px-5 py-2 items-center">
                      <span class="min-w-0">
                        <span class="block text-sm text-slate-100 truncate">{{ r.sideType }}</span>
                        <span class="block text-xs text-slate-500 mt-0.5 tabular-nums truncate">{{ r.qty }}<span class="md:hidden"> · {{ r.time }}</span></span>
                      </span>
                      <span class="hidden md:flex items-center gap-2 min-w-0 text-sm">
                        <span class="ui-dot" :class="r.dot"></span>
                        <span class="truncate" :class="r.statusClass">{{ r.status }}</span>
                      </span>
                      <span class="hidden md:block text-right text-sm text-slate-100 tabular-nums">{{ r.price }}</span>
                      <span class="hidden md:block text-right text-sm text-slate-300 tabular-nums">{{ r.value }}</span>
                      <span class="hidden md:block text-right text-xs text-slate-500 tabular-nums whitespace-nowrap">{{ r.time }}</span>
                      <!-- Phone: one right-hand block -->
                      <span class="md:hidden text-right tabular-nums min-w-0">
                        <span class="block text-sm text-slate-100">{{ r.price }}</span>
                        <span class="mt-0.5 flex items-center justify-end gap-1.5 text-xs">
                          <span class="ui-dot" :class="r.dot"></span>
                          <span :class="r.statusClass">{{ r.status }}</span>
                        </span>
                      </span>
                      <span class="flex justify-end">
                        <button
                          v-if="r.cancelable"
                          type="button"
                          class="ui-btn text-loss"
                          :aria-label="`Cancel ${r.sideType} order`"
                          @click="askCancel(r.order)"
                        >Cancel</button>
                      </span>
                    </li>
                  </ul>
                </template>
                <button
                  v-if="orders.recent.length > HISTORY_CAP"
                  type="button"
                  class="w-full h-10 px-5 flex items-center justify-between text-sm text-slate-400 border-t border-white/[0.06] transition-colors duration-150 ease-out hover:text-slate-100 hover:bg-white/[0.03]"
                  @click="showAllHistory = !showAllHistory"
                >
                  <span>{{ showAllHistory ? 'Show fewer' : `Show all ${orders.recent.length} orders` }}</span>
                  <span class="material-symbols-outlined text-[16px]" aria-hidden="true">{{ showAllHistory ? 'expand_less' : 'expand_more' }}</span>
                </button>
              </template>
            </div>
          </section>

          <!-- The bot's decisions -->
          <section v-if="accounts.length" aria-labelledby="bot-title">
            <div class="mb-3">
              <h2 id="bot-title" class="ui-section-title">Bot activity</h2>
              <p class="ui-section-sub">Why the bot bought or sold {{ symbol }}</p>
            </div>
            <div class="ui-card overflow-hidden">
              <ul v-if="botEvents == null" class="ui-rows" aria-hidden="true">
                <li v-for="n in 2" :key="n" class="px-5 py-4 space-y-2">
                  <span class="block h-3.5 w-40 ui-skel"></span>
                  <span class="block h-3 w-64 max-w-full ui-skel"></span>
                </li>
              </ul>
              <p v-else-if="!botEvents.length" class="px-5 py-8 text-center text-sm text-slate-400">No bot trades logged yet for {{ symbol }}</p>
              <ul v-else class="ui-rows">
                <li v-for="(e, i) in botRows" :key="i" class="px-5 py-3.5">
                  <div class="flex items-baseline justify-between gap-4">
                    <p class="min-w-0 text-sm text-slate-100 truncate">
                      <span class="text-slate-400">{{ e.side }}</span>
                      <span class="text-slate-600 mx-1.5" aria-hidden="true">·</span>{{ e.title }}
                    </p>
                    <p class="shrink-0 text-xs text-slate-500 tabular-nums">
                      <template v-if="e.price != null">{{ fmtPrice(e.price) }} · </template>{{ e.when }}
                    </p>
                  </div>
                  <p v-if="e.reason" class="text-xs text-slate-400 mt-1 leading-relaxed line-clamp-3">{{ e.reason }}</p>
                  <p v-if="e.backers.length || e.overridden" class="text-xs text-slate-500 mt-1">
                    <template v-if="e.backers.length">Backed by {{ e.backers.join(', ') }}</template>
                    <template v-if="e.backers.length && e.overridden"> · </template>
                    <template v-if="e.overridden">Overridden</template>
                  </p>
                </li>
              </ul>
            </div>
          </section>
        </div>

        <aside class="min-w-0 flex flex-col gap-12">
          <!-- Key statistics -->
          <section aria-labelledby="stats-title">
            <h2 id="stats-title" class="ui-section-title mb-3">Key statistics</h2>
            <div class="ui-card overflow-hidden">
              <dl v-if="info == null" class="ui-rows" aria-hidden="true">
                <div v-for="n in 6" :key="n" class="h-11 px-5 flex items-center justify-between">
                  <span class="ui-skel h-3 w-20"></span>
                  <span class="ui-skel h-3 w-16"></span>
                </div>
              </dl>
              <p v-else-if="!statCells.length" class="px-5 py-8 text-center text-sm text-slate-400">No statistics for {{ symbol }}</p>
              <dl v-else class="ui-rows">
                <div v-for="c in statCells" :key="c.label" class="h-11 px-5 flex items-center justify-between gap-4">
                  <dt class="text-sm text-slate-400">{{ c.label }}</dt>
                  <dd class="text-sm text-slate-100 tabular-nums">{{ c.value }}</dd>
                </div>
              </dl>
            </div>
          </section>

          <!-- About -->
          <section v-if="info == null || summary" aria-labelledby="about-title">
            <h2 id="about-title" class="ui-section-title mb-3">About</h2>
            <div class="ui-card p-5">
              <div v-if="info == null" class="space-y-2" aria-hidden="true">
                <div class="ui-skel h-3 w-full"></div>
                <div class="ui-skel h-3 w-full"></div>
                <div class="ui-skel h-3 w-2/3"></div>
              </div>
              <template v-else>
                <p v-if="tags.length" class="text-xs text-slate-500 mb-3">{{ tags.join(' · ') }}</p>
                <p class="text-sm text-slate-300 leading-relaxed" :class="aboutOpen ? '' : 'line-clamp-6'">{{ summary }}</p>
                <button
                  v-if="summary.length > 360"
                  type="button"
                  class="mt-2 text-sm text-slate-400 hover:text-slate-100 transition-colors duration-150 ease-out"
                  @click="aboutOpen = !aboutOpen"
                >{{ aboutOpen ? 'Show less' : 'Show more' }}</button>
              </template>
            </div>
          </section>
        </aside>
      </div>
    </main>
  </AppShell>

  <TradeTicket
    v-if="ticketSide && account"
    :key="ticketKey"
    :symbol="symbol"
    :side="ticketSide"
    :accounts="accounts"
    :account-id="selectedId"
    :last-price="lastPrice"
    :instances="instances"
    :held-by-account="heldKnown"
    @close="ticketSide = null"
    @placed="onPlaced"
  />

  <!-- Cancel an open order -->
  <Teleport to="body">
    <div v-if="cancelTarget" class="ui-overlay" @click.self="closeCancel">
      <div class="ui-modal max-w-sm" role="alertdialog" aria-modal="true" aria-labelledby="cancel-title">
        <div class="ui-modal-head">
          <div class="min-w-0">
            <h2 id="cancel-title" class="text-base font-medium text-slate-100">Cancel order</h2>
            <p class="text-sm text-slate-400 mt-1 leading-relaxed">{{ cancelPrompt(cancelTarget) }}</p>
            <p class="text-xs text-slate-500 mt-2">{{ account?.account_name }} · {{ account ? accountModeLabel(account) : '' }}</p>
          </div>
        </div>
        <div v-if="cancelError" class="px-6 pb-4">
          <div class="ui-error py-3" role="alert">{{ cancelError }}</div>
        </div>
        <div class="ui-modal-foot">
          <button type="button" class="ui-btn-ghost h-9 px-4" :disabled="cancelBusy" @click="closeCancel">Keep order</button>
          <button type="button" class="ui-btn h-9 px-4 text-loss" :disabled="cancelBusy" @click="confirmCancel">
            <span v-if="cancelBusy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
            {{ cancelBusy ? 'Canceling…' : 'Cancel order' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import StockChart from '../components/stock/StockChart.vue'
import TradeTicket from '../components/stock/TradeTicket.vue'
import {
  apiGet, cancelOrder, loadPositions, saveAccountId, savedAccountId,
} from '../composables/useStockApi.js'
import {
  fmtUsd, fmtPrice, fmtSignedUsd, fmtPct, signClass,
} from '../utils/dashboardAnalytics.js'
import {
  isOpenOrder, orderSideType, orderQtyLabel, orderPriceLabel, orderStatusLabel, orderStatusClass,
  orderStatusDot, orderValue, orderTimestamp, orderWhen, orderTimeLabel,
} from '../utils/orderBook.js'
import {
  accountModeLabel, cancelOrderFailure, cancelPrompt, isTradableSymbol, normalizeSymbol,
  pickAccountId, plainQtyText, tradableAccounts,
} from '../utils/trade.js'
import {
  botEventBackers, botEventTime, botEventTitle, infoText, ordersForSymbol, positionFor, stockStatCells,
} from '../utils/stock.js'

const route = useRoute()
const router = useRouter()

const symbol = computed(() => normalizeSymbol(route.params.symbol))
const tradable = computed(() => isTradableSymbol(symbol.value))

// ── Accounts ─────────────────────────────────────────────────────────────────
const accounts = ref([])
const accountsLoaded = ref(false)
const selectedId = ref(null)
const account = computed(() => accounts.value.find(a => String(a.id) === String(selectedId.value)) || null)
const canTrade = computed(() => tradable.value && !!account.value)

const menuOpen = ref(false)
const switcherEl = ref(null)

async function loadAccounts() {
  try {
    const d = await apiGet('/brokerages')
    accounts.value = tradableAccounts(d?.accounts)
  } catch {
    accounts.value = []
  } finally {
    accountsLoaded.value = true
  }
  selectedId.value = pickAccountId(accounts.value, route.query.account || savedAccountId())
}

function selectAccount(id) {
  menuOpen.value = false
  if (String(id) === String(selectedId.value)) return
  selectedId.value = id
  saveAccountId(id)
}

// ── Per-account data ─────────────────────────────────────────────────────────
const holdings = ref(null)   // { cash, positions } | null while loading
const book = ref(null)       // { open, recent } | null while loading
const bookError = ref('')
const botEvents = ref(null)
const instances = ref([])
let accountGen = 0

async function loadHoldings() {
  const id = selectedId.value
  if (id == null) return
  const gen = accountGen
  try {
    const d = await loadPositions(id)
    if (gen === accountGen) holdings.value = d
  } catch {
    if (gen === accountGen && holdings.value == null) holdings.value = { cash: null, positions: [] }
  }
}

async function loadBook() {
  const id = selectedId.value
  if (id == null) return
  const gen = accountGen
  try {
    const d = await apiGet(`/brokerages/${encodeURIComponent(id)}/order-book?recent_limit=100`)
    if (gen !== accountGen) return
    book.value = { open: Array.isArray(d?.open) ? d.open : [], recent: Array.isArray(d?.recent) ? d.recent : [] }
    bookError.value = ''
  } catch {
    if (gen === accountGen && book.value == null) bookError.value = "Couldn't load orders"
  }
}

async function loadBot() {
  const id = selectedId.value
  const sym = symbol.value
  if (id == null) return
  const gen = accountGen
  try {
    const q = new URLSearchParams({ symbol: sym, per_page: '20' })
    const d = await apiGet(`/brokerages/${encodeURIComponent(id)}/bot-activity?${q}`)
    if (gen === accountGen) botEvents.value = Array.isArray(d?.events) ? d.events : []
  } catch {
    if (gen === accountGen) botEvents.value = []
  }
}

async function loadInstances() {
  try {
    const d = await apiGet('/instances')
    instances.value = Array.isArray(d?.instances) ? d.instances : (Array.isArray(d) ? d : [])
  } catch {
    instances.value = []
  }
}

function reloadAccountData() {
  accountGen += 1
  holdings.value = null
  book.value = null
  bookError.value = ''
  botEvents.value = null
  showAllHistory.value = false
  loadHoldings()
  loadBook()
  loadBot()
}

watch(selectedId, id => { if (id != null) reloadAccountData() })

// ── Symbol data ──────────────────────────────────────────────────────────────
const info = ref(null)       // null while loading, {} on failure
const chart = ref({ range: '1D', series: null })
let symbolGen = 0

async function loadInfo() {
  const gen = ++symbolGen
  info.value = null
  try {
    const d = await apiGet(`/symbols/${encodeURIComponent(symbol.value)}/info`)
    if (gen === symbolGen) info.value = d && typeof d === 'object' ? d : {}
  } catch {
    if (gen === symbolGen) info.value = {}
  }
}

function onSeries(payload) {
  chart.value = payload
}

watch(symbol, () => {
  ticketSide.value = null
  loadInfo()
  if (selectedId.value != null) reloadAccountData()
})

const subtitle = computed(() => {
  const name = infoText(info.value, 'name')
  const exchange = infoText(info.value, 'exchange')
  return [name, exchange].filter(Boolean).join(' · ') || 'Stock'
})
const summary = computed(() => infoText(info.value, 'summary'))
const tags = computed(() => [infoText(info.value, 'sector'), infoText(info.value, 'industry')].filter(Boolean))
const aboutOpen = ref(false)
const statCells = computed(() => stockStatCells(info.value || {}, chart.value.series, chart.value.range))

// ── Position ─────────────────────────────────────────────────────────────────
const position = computed(() => positionFor(holdings.value?.positions, symbol.value))
const weight = computed(() => {
  const h = holdings.value
  if (!h || !position.value) return null
  const total = (h.cash ?? 0) + h.positions.reduce((a, p) => a + (p.marketValue || 0), 0)
  return total > 0 ? (position.value.marketValue / total) * 100 : null
})

const lastPrice = computed(() => {
  const vals = chart.value.series?.vals
  if (vals?.length) return vals[vals.length - 1]
  if (position.value?.lastPrice != null) return position.value.lastPrice
  const prev = Number(info.value?.previousClose)
  return Number.isFinite(prev) && prev > 0 ? prev : null
})

// ── Orders ───────────────────────────────────────────────────────────────────
const HISTORY_CAP = 10
const showAllHistory = ref(false)
const orders = computed(() => ordersForSymbol(book.value, symbol.value))

function orderRow(o) {
  const value = orderValue(o)
  return {
    id: o.id,
    order: o,
    sideType: orderSideType(o),
    qty: orderQtyLabel(o),
    price: orderPriceLabel(o),
    value: value == null ? '—' : fmtUsd(value),
    status: orderStatusLabel(o),
    statusClass: orderStatusClass(o),
    dot: orderStatusDot(o),
    time: orderWhen(orderTimestamp(o)),
    cancelable: isOpenOrder(o) && String(o.status) !== 'pending_cancel',
  }
}

const orderGroups = computed(() => {
  const groups = []
  if (orders.value.open.length) groups.push({ key: 'open', label: 'Working', rows: orders.value.open.map(orderRow) })
  const recent = showAllHistory.value ? orders.value.recent : orders.value.recent.slice(0, HISTORY_CAP)
  if (recent.length) groups.push({ key: 'recent', label: 'History', rows: recent.map(orderRow) })
  return groups
})

const ordersSubtitle = computed(() => {
  if (!book.value) return ' '
  const open = orders.value.open.length
  return `${open ? `${open} working` : 'Nothing working'} · ${orders.value.recent.length} in history`
})

// ── Bot activity ─────────────────────────────────────────────────────────────
const botRows = computed(() => (botEvents.value || []).map(e => {
  const when = botEventTime(e)
  const price = Number(e?.price)
  return {
    side: String(e?.side || '').toLowerCase() === 'sell' ? 'Sell' : 'Buy',
    title: botEventTitle(e),
    reason: String(e?.reason || '').trim(),
    backers: botEventBackers(e),
    overridden: !!e?.override_applied,
    price: e?.price != null && Number.isFinite(price) ? price : null,
    when: when ? orderTimeLabel(when) : '',
  }
}))

// ── Ticket ───────────────────────────────────────────────────────────────────
const ticketSide = ref(null)
const ticketKey = ref(0)
const heldKnown = computed(() => {
  if (selectedId.value == null || holdings.value == null) return {}
  return { [selectedId.value]: position.value ? position.value.qty : 0 }
})

function openTicket(side) {
  if (!canTrade.value) return
  menuOpen.value = false
  ticketKey.value += 1      // a fresh ticket, and a fresh client_order_id
  ticketSide.value = side
}

let refreshTimer = null
function onPlaced({ accountId }) {
  if (String(accountId) !== String(selectedId.value)) return
  // Positions and the order book now, and again once a market order has had a moment to fill.
  loadHoldings()
  loadBook()
  clearTimeout(refreshTimer)
  refreshTimer = setTimeout(() => { loadHoldings(); loadBook() }, 3000)
}

// ── Cancel ───────────────────────────────────────────────────────────────────
const cancelTarget = ref(null)
const cancelBusy = ref(false)
const cancelError = ref('')

function askCancel(order) {
  cancelTarget.value = order
  cancelError.value = ''
}
function closeCancel() {
  if (cancelBusy.value) return
  cancelTarget.value = null
}
async function confirmCancel() {
  const order = cancelTarget.value
  const id = selectedId.value
  if (!order || id == null || cancelBusy.value) return
  cancelBusy.value = true
  cancelError.value = ''
  const res = await cancelOrder(id, order.id)
  cancelBusy.value = false
  if (res.ok) {
    cancelTarget.value = null
    loadBook()
    return
  }
  cancelError.value = cancelOrderFailure(res.status, res.body)
}

// ── Page plumbing ────────────────────────────────────────────────────────────
function goBack() {
  if (window.history.state?.back) router.back()
  else router.push('/dashboard')
}

function onDocPointer(e) {
  if (menuOpen.value && switcherEl.value && !switcherEl.value.contains(e.target)) menuOpen.value = false
}
function onDocKey(e) {
  if (e.key === 'Escape') {
    if (cancelTarget.value) closeCancel()
    else if (menuOpen.value) menuOpen.value = false
  }
}

let pollTimer = null
onMounted(() => {
  loadAccounts()
  loadInfo()
  loadInstances()
  document.addEventListener('pointerdown', onDocPointer)
  document.addEventListener('keydown', onDocKey)
  pollTimer = setInterval(() => {
    if (document.visibilityState !== 'visible' || selectedId.value == null) return
    loadHoldings()
    loadBook()
  }, 30_000)
})
onUnmounted(() => {
  clearInterval(pollTimer)
  clearTimeout(refreshTimer)
  document.removeEventListener('pointerdown', onDocPointer)
  document.removeEventListener('keydown', onDocKey)
})
</script>

<style scoped>
/* Hidden cells take no grid track:
   phone  order (size · time) · price/status · cancel
   md+    order · status · price · value · time · cancel */
.sym-ord-grid {
  grid-template-columns: minmax(0, 1fr) auto auto;
  column-gap: 16px;
}
@media (min-width: 768px) {
  .sym-ord-grid { grid-template-columns: minmax(0, 1.2fr) 132px 96px 104px 120px 72px; }
}
</style>
