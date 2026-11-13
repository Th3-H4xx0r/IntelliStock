<script setup>
// The selected account's cash and open positions — the web twin of the iOS
// "Holdings" section. Each row: symbol and size, share of the account, a
// line for the chosen period, price, value and P&L. Daily is today's move;
// Total is the lifetime unrealized P&L since purchase.
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import Sparkline from './Sparkline.vue'
import { holdingPnl, fmtUsd, fmtPrice, fmtSignedUsd, fmtPct, fmtQty, signClass } from '../../utils/dashboardAnalytics.js'

const props = defineProps({
  holdings: { type: Object, default: null },  // { cash, positions } | null while loading
  sparks: { type: Object, default: () => ({ daily: null, total: null }) },
  mode: { type: String, default: 'daily' },
})
const emit = defineEmits(['update:mode'])

const positions = computed(() =>
  [...(props.holdings?.positions || [])].sort((a, b) => b.marketValue - a.marketValue))
const cash = computed(() => props.holdings?.cash ?? null)
const total = computed(() => (cash.value ?? 0) + positions.value.reduce((a, p) => a + p.marketValue, 0))
const modeSparks = computed(() => props.sparks?.[props.mode] ?? null)

// A holding row opens that stock's page.
const router = useRouter()
function openStock(symbol) { router.push({ name: 'stock', params: { symbol } }) }

function weight(v) { return total.value > 0 ? (v / total.value) * 100 : 0 }

const rows = computed(() => positions.value.map(p => {
  const spark = modeSparks.value?.[p.symbol] ?? null
  return { ...p, spark, pnl: holdingPnl(p, spark, props.mode), weight: weight(p.marketValue) }
}))

</script>

<template>
  <section v-if="holdings && (positions.length || cash != null)" aria-labelledby="holdings-title">
    <div class="flex items-center justify-between gap-4 mb-3">
      <div>
        <h2 id="holdings-title" class="text-base font-medium text-slate-100">Holdings</h2>
        <!-- No summed P&L here: holding lines start at today's open, the
             account's day change at yesterday's close, and two different
             "today" figures on one screen would contradict each other. -->
        <p class="text-sm text-slate-500 mt-0.5 tabular-nums">
          {{ positions.length }} position{{ positions.length === 1 ? '' : 's' }}
        </p>
      </div>
      <div class="inline-flex p-0.5 rounded-lg bg-surface border border-white/[0.07]" role="group" aria-label="P&L period">
        <button
          v-for="m in [['daily', 'Daily'], ['total', 'Total']]"
          :key="m[0]"
          type="button"
          class="h-7 px-3 rounded-md text-xs font-medium transition-colors"
          :class="mode === m[0] ? 'bg-raised text-slate-100 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]' : 'text-slate-500 hover:text-slate-200'"
          :aria-pressed="mode === m[0]"
          @click="emit('update:mode', m[0])"
        >{{ m[1] }}</button>
      </div>
    </div>

    <div class="rounded-xl border border-white/[0.07] bg-surface overflow-hidden">
      <div class="hold-grid hidden sm:grid h-10 px-5 items-center text-xs text-slate-500 border-b border-white/[0.06]">
        <span>Symbol</span>
        <span class="hidden md:block">Weight</span>
        <span>{{ mode === 'daily' ? 'Today' : 'Since bought' }}</span>
        <span class="hidden md:block text-right">Price</span>
        <span class="text-right">Value</span>
        <span class="text-right">{{ mode === 'daily' ? 'Day P&L' : 'Total P&L' }}</span>
      </div>

      <ul class="divide-y divide-white/[0.05]">
        <li v-if="cash != null" class="hold-grid grid min-h-14 px-5 py-2 items-center">
          <span class="min-w-0">
            <span class="block text-sm text-slate-100">Cash</span>
            <span class="block text-xs text-slate-500 mt-0.5">Available to invest</span>
          </span>
          <span class="hidden md:flex items-center gap-2">
            <span class="weight-track"><span class="weight-fill bg-slate-500" :style="{ transform: `scaleX(${weight(cash) / 100})` }"></span></span>
            <span class="text-xs text-slate-400 tabular-nums w-10">{{ weight(cash).toFixed(0) }}%</span>
          </span>
          <span></span>
          <span class="hidden md:block"></span>
          <span class="text-right text-sm text-slate-100 tabular-nums">{{ fmtUsd(cash) }}</span>
          <span class="hidden sm:block text-right text-sm text-slate-600">—</span>
        </li>

        <li
          v-for="r in rows"
          :key="r.symbol"
          class="hold-grid grid min-h-14 px-5 py-2 items-center cursor-pointer transition-colors duration-150 ease-out hover:bg-white/[0.03]"
          role="link"
          tabindex="0"
          @click="openStock(r.symbol)"
          @keydown.enter="openStock(r.symbol)"
        >
          <span class="min-w-0">
            <span class="block text-sm font-medium text-slate-100 truncate">{{ r.symbol }}</span>
            <span class="block text-xs text-slate-500 mt-0.5 tabular-nums">{{ fmtQty(r.qty) }}</span>
          </span>
          <span class="hidden md:flex items-center gap-2">
            <span class="weight-track"><span class="weight-fill bg-slate-300" :style="{ transform: `scaleX(${r.weight / 100})` }"></span></span>
            <span class="text-xs text-slate-400 tabular-nums w-10">{{ r.weight.toFixed(0) }}%</span>
          </span>
          <span class="flex items-center">
            <Sparkline v-if="r.spark" :values="r.spark" :width="88" :height="24" />
            <span v-else-if="modeSparks == null" class="block h-[2px] w-[88px] rounded bg-raised"></span>
          </span>
          <span class="hidden md:block text-right text-sm text-slate-300 tabular-nums">{{ fmtPrice(r.lastPrice) }}</span>
          <span class="text-right tabular-nums">
            <span class="block text-sm text-slate-100">{{ fmtUsd(r.marketValue) }}</span>
            <!-- Phone: the P&L column is hidden, so the % rides under the value. -->
            <span v-if="r.pnl.hasPnl" class="sm:hidden block text-xs mt-0.5" :class="signClass(r.pnl.pct)">{{ fmtPct(r.pnl.pct) }}</span>
          </span>
          <span class="hidden sm:block text-right tabular-nums">
            <template v-if="r.pnl.hasPnl">
              <span class="block text-sm" :class="signClass(r.pnl.abs)">{{ fmtSignedUsd(r.pnl.abs) }}</span>
              <span class="block text-xs mt-0.5" :class="signClass(r.pnl.pct)">{{ fmtPct(r.pnl.pct) }}</span>
            </template>
            <span v-else class="text-sm text-slate-600">—</span>
          </span>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
/* Hidden cells take no grid track, so each breakpoint lists only what shows:
   phone    symbol · line · value(+%)
   sm       symbol · line · value · P&L
   md+      symbol · weight · line · price · value · P&L */
.hold-grid {
  grid-template-columns: minmax(0, 1fr) 88px 104px;
  column-gap: 16px;
}
@media (min-width: 640px) {
  .hold-grid { grid-template-columns: minmax(0, 1fr) 88px 112px 112px; }
}
@media (min-width: 768px) {
  .hold-grid { grid-template-columns: minmax(0, 1.2fr) 120px 88px 96px 112px 112px; }
}
.weight-track {
  position: relative;
  display: block;
  height: 4px;
  width: 64px;
  border-radius: 999px;
  background: #1e1e20;
  overflow: hidden;
}
.weight-fill {
  position: absolute;
  inset: 0;
  border-radius: 999px;
  transform-origin: left center;
}
</style>
