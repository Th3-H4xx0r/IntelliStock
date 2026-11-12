<script setup>
// Open orders and recent order history for the selected stock account, read
// straight from the broker, so manual orders show next to the bot's. The web
// twin of the iOS "Orders" section. Read-only: nothing here changes an order.
import { ref, computed, watch, toRef } from 'vue'
import { useOrderBook } from '../../composables/useDashboardData.js'
import {
  defaultOrdersMode, orderSideType, orderQtyLabel, orderPriceLabel,
  orderStatusLabel, orderStatusClass, orderTimestamp, orderTimeLabel,
} from '../../utils/orderBook.js'

const props = defineProps({
  brokerageId: { type: [String, Number], default: null },
})

const { book, error, reload } = useOrderBook(toRef(props, 'brokerageId'))

const ROW_CAP = 8
const mode = ref(null)       // 'open' | 'recent'; picked once per account, then the user's
const expanded = ref(false)

watch(() => props.brokerageId, () => { mode.value = null; expanded.value = false })
// Choose the first tab when the book first arrives, and keep it on later polls.
watch(book, b => { if (b && mode.value == null) mode.value = defaultOrdersMode(b) })

function setMode(m) {
  mode.value = m
  expanded.value = false
}

const openCount = computed(() => book.value?.open.length ?? 0)
const list = computed(() => (mode.value === 'open' ? book.value?.open : book.value?.recent) || [])
const shown = computed(() => (expanded.value ? list.value : list.value.slice(0, ROW_CAP)))
const rows = computed(() => shown.value.map(o => ({
  id: o.id,
  symbol: o.symbol,
  sideType: orderSideType(o),
  qty: orderQtyLabel(o),
  price: orderPriceLabel(o),
  status: orderStatusLabel(o),
  statusClass: orderStatusClass(o),
  time: orderTimeLabel(orderTimestamp(o)),
})))
</script>

<template>
  <section v-if="brokerageId && book?.supported !== false" aria-labelledby="orders-title">
    <div class="flex items-center justify-between gap-4 mb-3">
      <div>
        <h2 id="orders-title" class="text-base font-medium text-slate-100">Orders</h2>
        <p class="text-sm text-slate-500 mt-0.5 tabular-nums">
          <template v-if="book">{{ openCount ? `${openCount} open` : 'Nothing working' }}</template>
          <template v-else>&nbsp;</template>
        </p>
      </div>
      <div class="inline-flex p-0.5 rounded-lg bg-surface border border-white/[0.07]" role="group" aria-label="Orders view">
        <button
          v-for="m in [['open', 'Open'], ['recent', 'Recent']]"
          :key="m[0]"
          type="button"
          class="h-7 px-3 rounded-md text-xs font-medium transition-colors inline-flex items-center gap-1.5"
          :class="mode === m[0] ? 'bg-raised text-slate-100 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]' : 'text-slate-500 hover:text-slate-200'"
          :aria-pressed="mode === m[0]"
          @click="setMode(m[0])"
        >
          {{ m[1] }}
          <span v-if="m[0] === 'open' && openCount" class="tabular-nums text-slate-400">{{ openCount }}</span>
        </button>
      </div>
    </div>

    <div class="rounded-xl border border-white/[0.07] bg-surface overflow-hidden">
      <div class="ord-grid hidden sm:grid h-10 px-5 items-center text-xs text-slate-500 border-b border-white/[0.06]">
        <span>Symbol</span>
        <span class="text-right">Quantity</span>
        <span class="text-right">Price</span>
        <span>Status</span>
        <span class="text-right">{{ mode === 'open' ? 'Placed' : 'Updated' }}</span>
      </div>

      <!-- Loading -->
      <ul v-if="!book && !error" class="divide-y divide-white/[0.05]" aria-hidden="true">
        <li v-for="n in 3" :key="n" class="h-14 px-5 flex items-center">
          <div class="h-4 w-40 rounded bg-raised"></div>
        </li>
      </ul>

      <!-- First load failed -->
      <div v-else-if="!book" class="h-14 px-5 flex items-center justify-between gap-4 text-sm">
        <span class="text-slate-400">{{ error }}</span>
        <button type="button" class="h-8 px-3 rounded-md text-slate-300 hover:text-slate-100 hover:bg-raised transition-colors" @click="reload">Retry</button>
      </div>

      <!-- Empty -->
      <p v-else-if="!rows.length" class="h-14 px-5 flex items-center text-sm text-slate-500">
        {{ mode === 'open' ? 'No open orders' : 'No recent orders' }}
      </p>

      <ul v-else class="divide-y divide-white/[0.05]">
        <li v-for="r in rows" :key="r.id" class="ord-grid grid min-h-14 px-5 py-2 items-center">
          <span class="min-w-0">
            <span class="block text-sm font-medium text-slate-100 truncate">{{ r.symbol }}</span>
            <span class="block text-xs text-slate-500 mt-0.5">{{ r.sideType }}</span>
          </span>

          <!-- Phone: one right-hand block, price and size over status and time -->
          <span class="sm:hidden text-right tabular-nums">
            <span class="block text-sm text-slate-100">{{ r.price }} <span class="text-slate-500">· {{ r.qty }}</span></span>
            <span class="block text-xs mt-0.5"><span :class="r.statusClass">{{ r.status }}</span><span class="text-slate-500"> · {{ r.time }}</span></span>
          </span>

          <span class="hidden sm:block text-right text-sm text-slate-300 tabular-nums">{{ r.qty }}</span>
          <span class="hidden sm:block text-right text-sm text-slate-100 tabular-nums">{{ r.price }}</span>
          <span class="hidden sm:block text-sm truncate" :class="r.statusClass">{{ r.status }}</span>
          <span class="hidden sm:block text-right text-xs text-slate-500 tabular-nums">{{ r.time }}</span>
        </li>
      </ul>

      <button
        v-if="book && list.length > ROW_CAP"
        type="button"
        class="w-full h-10 px-5 text-left text-sm text-slate-400 border-t border-white/[0.06] hover:text-slate-100 hover:bg-white/[0.03] transition-colors"
        @click="expanded = !expanded"
      >{{ expanded ? 'Show fewer' : `Show all ${list.length}` }}</button>
    </div>
  </section>
</template>

<style scoped>
/* Hidden cells take no grid track:
   phone  symbol · price/size over status/time
   sm+    symbol · quantity · price · status · time */
.ord-grid {
  grid-template-columns: minmax(0, 1fr) auto;
  column-gap: 16px;
}
@media (min-width: 640px) {
  .ord-grid { grid-template-columns: minmax(0, 1.1fr) 112px 104px minmax(0, 1fr) 80px; }
}
</style>
