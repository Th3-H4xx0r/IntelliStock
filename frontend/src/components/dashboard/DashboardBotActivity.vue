<script setup>
// The bots' latest decisions on the selected account, with their reasons:
// the "why" next to Orders' "what". A stock row opens its stock page.
import { computed, toRef } from 'vue'
import { RouterLink } from 'vue-router'
import { useBotActivity } from '../../composables/useDashboardData.js'
import { botEventTitle, botEventTime } from '../../utils/stock.js'
import { orderTimeLabel } from '../../utils/orderBook.js'
import { fmtPrice } from '../../utils/dashboardAnalytics.js'

const props = defineProps({
  brokerageId: { type: [String, Number], default: null },
})

const { events, total } = useBotActivity(toRef(props, 'brokerageId'), 6)

// Option contracts (swing/wheel lanes) have no stock page of their own.
const EQUITY = /^[A-Z][A-Z0-9.-]{0,9}$/

const rows = computed(() => (events.value || []).map((e, i) => {
  const symbol = String(e?.symbol || '').toUpperCase()
  const when = botEventTime(e)
  const price = Number(e?.price)
  return {
    key: e?.id ?? `${symbol}-${when}-${i}`,
    side: String(e?.side || '').toLowerCase() === 'sell' ? 'Sell' : 'Buy',
    symbol,
    link: EQUITY.test(symbol) ? `/stocks/${encodeURIComponent(symbol)}` : null,
    title: botEventTitle(e),
    reason: String(e?.reason || '').trim(),
    price: e?.price != null && Number.isFinite(price) ? price : null,
    when: when ? orderTimeLabel(when) : '',
  }
}))

const subtitle = computed(() => {
  if (events.value == null) return ' '
  if (!rows.value.length) return 'Why the bots bought or sold'
  return total.value > rows.value.length ? `Latest ${rows.value.length} of ${total.value}` : 'Why the bots bought or sold'
})
</script>

<template>
  <section aria-labelledby="bot-act-title">
    <div class="mb-3">
      <h2 id="bot-act-title" class="ui-section-title">Bot activity</h2>
      <p class="ui-section-sub tabular-nums">{{ subtitle }}</p>
    </div>

    <div class="ui-card overflow-hidden">
      <ul v-if="events == null" class="ui-rows" aria-hidden="true">
        <li v-for="n in 3" :key="n" class="px-5 py-4 space-y-2">
          <span class="block h-3.5 w-32 ui-skel"></span>
          <span class="block h-3 w-full ui-skel"></span>
        </li>
      </ul>

      <p v-else-if="!rows.length" class="px-5 py-6 text-sm text-slate-400">No bot trades logged yet</p>

      <ul v-else class="ui-rows">
        <li v-for="r in rows" :key="r.key">
          <component
            :is="r.link ? RouterLink : 'div'"
            :to="r.link || undefined"
            class="block px-5 py-3.5"
            :class="r.link ? 'transition-colors duration-150 ease-out hover:bg-white/[0.03] focus-visible:outline-none focus-visible:bg-white/[0.04]' : ''"
          >
            <div class="flex items-baseline justify-between gap-3">
              <p class="min-w-0 text-sm text-slate-100 truncate">
                <span class="text-slate-400">{{ r.side }}</span>
                <span class="ml-1.5 font-medium tabular-nums">{{ r.symbol }}</span>
              </p>
              <p class="shrink-0 text-xs text-slate-500 tabular-nums">
                <template v-if="r.price != null">{{ fmtPrice(r.price) }} · </template>{{ r.when }}
              </p>
            </div>
            <p class="text-xs text-slate-500 mt-0.5 truncate">{{ r.title }}</p>
            <p v-if="r.reason" class="text-xs text-slate-400 mt-1.5 leading-relaxed line-clamp-2">{{ r.reason }}</p>
          </component>
        </li>
      </ul>
    </div>
  </section>
</template>
