<script setup>
// Open orders and order history for the selected stock account, read straight
// from the broker, so manual orders show next to the bot's. The web twin of
// the iOS "Orders" section. Read-only: nothing here changes an order.
//
// Laid out as a blotter: status as the app's dot, size with a fill bar for
// partial fills, price, filled value and time. History is grouped by day, and
// a row expands to the order's full record.
import { ref, computed, watch, toRef } from 'vue'
import { useOrderBook } from '../../composables/useDashboardData.js'
import { fmtUsd } from '../../utils/dashboardAnalytics.js'
import {
  defaultOrdersMode, orderSideType, orderQtyLabel, orderPriceLabel,
  orderStatusLabel, orderStatusClass, orderStatusDot, orderFillFraction,
  orderValue, orderTimestamp, orderClock, orderWhen, groupOrdersByDay, orderDetails,
} from '../../utils/orderBook.js'

const props = defineProps({
  brokerageId: { type: [String, Number], default: null },
})

const { book, error, reload } = useOrderBook(toRef(props, 'brokerageId'))

const ROW_CAP = 8
const mode = ref(null)       // 'open' | 'recent'; picked once per account, then the user's
const showAll = ref(false)
const openId = ref(null)     // the expanded row

watch(() => props.brokerageId, () => { mode.value = null; showAll.value = false; openId.value = null })
// Choose the first tab when the book first arrives, and keep it on later polls.
watch(book, b => { if (b && mode.value == null) mode.value = defaultOrdersMode(b) })

function setMode(m) {
  mode.value = m
  showAll.value = false
  openId.value = null
}
function toggle(id) { openId.value = openId.value === id ? null : id }

const openCount = computed(() => book.value?.open.length ?? 0)
const historyCount = computed(() => book.value?.recent.length ?? 0)
const list = computed(() => (mode.value === 'open' ? book.value?.open : book.value?.recent) || [])
const shown = computed(() => (showAll.value ? list.value : list.value.slice(0, ROW_CAP)))

function row(o) {
  const value = orderValue(o)
  const ts = orderTimestamp(o)
  return {
    id: o.id,
    symbol: o.symbol,
    sideType: orderSideType(o),
    qty: orderQtyLabel(o),
    fill: orderFillFraction(o),
    price: orderPriceLabel(o),
    value: value == null ? '—' : fmtUsd(value),
    status: orderStatusLabel(o),
    statusClass: orderStatusClass(o),
    dot: orderStatusDot(o),
    time: mode.value === 'open' ? orderWhen(ts) : orderClock(ts),
    details: orderDetails(o),
  }
}

// Open orders are one block; history is split into days.
const groups = computed(() => {
  if (mode.value === 'open') return [{ key: 'open', label: null, rows: shown.value.map(row) }]
  return groupOrdersByDay(shown.value).map(g => ({ key: g.key, label: g.label, rows: g.orders.map(row) }))
})

const subtitle = computed(() => {
  if (!book.value) return ' '
  const open = openCount.value ? `${openCount.value} working` : 'Nothing working'
  return `${open} · ${historyCount.value} in history`
})
</script>

<template>
  <section v-if="brokerageId && book?.supported !== false" aria-labelledby="orders-title">
    <div class="flex items-center justify-between gap-4 mb-3">
      <div>
        <h2 id="orders-title" class="ui-section-title">Orders</h2>
        <p class="ui-section-sub tabular-nums">{{ subtitle }}</p>
      </div>
      <div class="ui-seg" role="group" aria-label="Orders view">
        <button
          v-for="m in [['open', 'Open'], ['recent', 'History']]"
          :key="m[0]"
          type="button"
          class="ui-seg-btn inline-flex items-center gap-1.5"
          :class="mode === m[0] ? 'ui-seg-btn-active' : ''"
          :aria-pressed="mode === m[0]"
          @click="setMode(m[0])"
        >
          {{ m[1] }}
          <span v-if="m[0] === 'open' && openCount" class="tabular-nums text-slate-400">{{ openCount }}</span>
        </button>
      </div>
    </div>

    <div class="ui-card overflow-hidden">
      <div class="ord-grid ui-th hidden md:grid">
        <span>Symbol</span>
        <span>Status</span>
        <span class="text-right">Quantity</span>
        <span class="text-right">Price</span>
        <span class="hidden lg:block text-right">Value</span>
        <span class="text-right">{{ mode === 'open' ? 'Placed' : 'Time' }}</span>
        <span></span>
      </div>

      <!-- Loading -->
      <ul v-if="!book && !error" class="ui-rows" aria-hidden="true">
        <li v-for="n in 3" :key="n" class="h-14 px-5 flex items-center justify-between gap-4">
          <span class="space-y-2">
            <span class="block h-3.5 w-16 ui-skel"></span>
            <span class="block h-3 w-24 ui-skel"></span>
          </span>
          <span class="block h-3.5 w-20 ui-skel"></span>
        </li>
      </ul>

      <!-- First load failed -->
      <div v-else-if="!book" class="h-14 px-5 flex items-center justify-between gap-4 text-sm">
        <span class="text-slate-400">{{ error }}</span>
        <button type="button" class="ui-btn" @click="reload">Retry</button>
      </div>

      <!-- Empty -->
      <div v-else-if="!list.length" class="px-5 py-8 text-center">
        <p class="text-sm text-slate-300">{{ mode === 'open' ? 'No working orders' : 'No orders yet' }}</p>
        <p class="text-xs text-slate-500 mt-1">
          {{ mode === 'open' ? 'Orders show here until they fill or are canceled' : 'Filled, canceled and expired orders show here' }}
        </p>
      </div>

      <template v-else>
        <template v-for="(g, gi) in groups" :key="g.key">
          <!-- Day header (history) -->
          <div
            v-if="g.label"
            class="h-8 px-5 flex items-center justify-between text-xs text-slate-500 bg-white/[0.015] border-b border-white/[0.05]"
            :class="gi ? 'border-t border-white/[0.06]' : ''"
          >
            <span>{{ g.label }}</span>
            <span class="tabular-nums">{{ g.rows.length }} {{ g.rows.length === 1 ? 'order' : 'orders' }}</span>
          </div>

          <ul class="ui-rows">
            <li v-for="r in g.rows" :key="r.id">
              <button
                type="button"
                class="ord-grid grid w-full min-h-14 px-5 py-2 items-center text-left transition-colors duration-150 ease-out hover:bg-white/[0.03] focus-visible:outline-none focus-visible:bg-white/[0.04]"
                :class="openId === r.id ? 'bg-white/[0.03]' : ''"
                :aria-expanded="openId === r.id"
                @click="toggle(r.id)"
              >
                <span class="min-w-0">
                  <span class="block text-sm font-medium text-slate-100 truncate">{{ r.symbol }}</span>
                  <span class="block text-xs text-slate-500 mt-0.5 truncate">{{ r.sideType }}</span>
                </span>

                <!-- md+: one cell per column -->
                <span class="hidden md:flex items-center gap-2 min-w-0 text-sm">
                  <span class="ui-dot" :class="r.dot"></span>
                  <span class="truncate" :class="r.statusClass">{{ r.status }}</span>
                </span>
                <span class="hidden md:block text-right tabular-nums">
                  <span class="block text-sm text-slate-300">{{ r.qty }}</span>
                  <span v-if="r.fill != null" class="fill-track ml-auto mt-1.5" aria-hidden="true">
                    <span class="fill-bar" :style="{ transform: `scaleX(${r.fill})` }"></span>
                  </span>
                </span>
                <span class="hidden md:block text-right text-sm text-slate-100 tabular-nums">{{ r.price }}</span>
                <span class="hidden lg:block text-right text-sm text-slate-300 tabular-nums">{{ r.value }}</span>
                <span class="hidden md:block text-right text-xs text-slate-500 tabular-nums whitespace-nowrap">{{ r.time }}</span>

                <!-- Phone: one right-hand block -->
                <span class="md:hidden text-right tabular-nums min-w-0">
                  <span class="block text-sm text-slate-100">{{ r.price }} <span class="text-slate-500">· {{ r.qty }}</span></span>
                  <span class="mt-0.5 flex items-center justify-end gap-1.5 text-xs">
                    <span class="ui-dot" :class="r.dot"></span>
                    <span :class="r.statusClass">{{ r.status }}</span>
                    <span class="text-slate-500">· {{ r.time }}</span>
                  </span>
                </span>

                <span
                  class="material-symbols-outlined text-[18px] text-slate-600 transition-transform duration-150 ease-out justify-self-end"
                  :class="openId === r.id ? 'rotate-180' : ''"
                  aria-hidden="true"
                >expand_more</span>
              </button>

              <!-- The order's full record -->
              <div v-if="openId === r.id" class="px-5 pt-1 pb-4 bg-white/[0.03]">
                <dl class="grid grid-cols-2 sm:grid-cols-3 gap-x-6 gap-y-3 rounded-lg border border-white/[0.06] bg-background-dark/60 px-4 py-3">
                  <div v-for="d in r.details" :key="d.label" class="min-w-0" :class="d.label === 'Order ID' ? 'col-span-2 sm:col-span-3' : ''">
                    <dt class="text-xs text-slate-500">{{ d.label }}</dt>
                    <dd
                      class="text-sm text-slate-200 mt-0.5 tabular-nums truncate"
                      :class="d.label === 'Order ID' ? 'font-mono text-xs text-slate-400 select-all' : ''"
                      :title="d.value"
                    >{{ d.value }}</dd>
                  </div>
                </dl>
              </div>
            </li>
          </ul>
        </template>

        <button
          v-if="list.length > ROW_CAP"
          type="button"
          class="w-full h-10 px-5 flex items-center justify-between text-sm text-slate-400 border-t border-white/[0.06] transition-colors duration-150 ease-out hover:text-slate-100 hover:bg-white/[0.03]"
          @click="showAll = !showAll"
        >
          <span>{{ showAll ? 'Show fewer' : `Show all ${list.length} orders` }}</span>
          <span class="material-symbols-outlined text-[16px]" aria-hidden="true">{{ showAll ? 'expand_less' : 'expand_more' }}</span>
        </button>
      </template>
    </div>
  </section>
</template>

<style scoped>
/* Hidden cells take no grid track:
   phone  symbol · price/size over status/time · chevron
   md     symbol · status · quantity · price · time · chevron
   lg+    symbol · status · quantity · price · value · time · chevron */
.ord-grid {
  grid-template-columns: minmax(0, 1fr) auto 18px;
  column-gap: 16px;
}
@media (min-width: 768px) {
  .ord-grid { grid-template-columns: minmax(0, 1.2fr) 136px 104px 96px 88px 18px; }
}
@media (min-width: 1024px) {
  .ord-grid { grid-template-columns: minmax(0, 1.2fr) 136px 104px 96px 104px 88px 18px; }
}
.fill-track {
  position: relative;
  display: block;
  height: 3px;
  width: 64px;
  border-radius: 999px;
  background: #26262a;
  overflow: hidden;
}
.fill-bar {
  position: absolute;
  inset: 0;
  border-radius: 999px;
  background: #4cc38a;
  transform-origin: left center;
  transition: transform 300ms cubic-bezier(0.22, 1, 0.36, 1);
}
</style>
