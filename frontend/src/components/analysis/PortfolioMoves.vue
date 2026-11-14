<!-- frontend/src/components/analysis/PortfolioMoves.vue
     "Today in your account" on the AI Analysis screen, from
     GET /analysis/portfolio: the last session's move for each equity holding,
     its contribution to the account, and that contribution split into market,
     sector and stock-specific parts (the parts add up to the contribution),
     with the event's one-line cause when the day was flagged. Largest
     contribution first; a totals row closes the table. A row loads that
     symbol into the chart above. -->
<template>
  <section aria-labelledby="pm-title">
    <div class="mb-3">
      <h2 id="pm-title" class="ui-section-title">Today in your account</h2>
      <p class="ui-section-sub tabular-nums">
        <template v-if="subtitle">{{ subtitle }}</template>
        <template v-else>Each holding's move, and its share of the account's move split three ways</template>
      </p>
    </div>

    <div class="ui-card overflow-hidden">
      <div v-if="!hasAccount" class="px-5 py-8 text-center text-sm text-slate-400">
        Link an Alpaca account on
        <RouterLink to="/brokerages" class="text-slate-300 underline decoration-white/20 underline-offset-2 hover:text-slate-100">Brokerages</RouterLink>
        to see what moved it
      </div>

      <ul v-else-if="loading && !data" class="ui-rows" aria-hidden="true">
        <li v-for="k in 4" :key="k" class="h-14 px-5 flex items-center justify-between gap-4">
          <span class="space-y-2">
            <span class="block h-3.5 w-16 ui-skel"></span>
            <span class="block h-3 w-28 ui-skel"></span>
          </span>
          <span class="block h-3.5 w-20 ui-skel"></span>
        </li>
      </ul>

      <div v-else-if="error && !data" class="h-14 px-5 flex items-center justify-between gap-4 text-sm">
        <span class="text-slate-400 min-w-0">{{ error }}</span>
        <button type="button" class="ui-btn shrink-0" @click="emit('retry')">Retry</button>
      </div>

      <div v-else-if="!rows.length" class="px-5 py-8 text-center">
        <p class="text-sm text-slate-300">No stock positions in this account</p>
        <p class="text-xs text-slate-500 mt-1">Options and crypto aren't split here</p>
      </div>

      <template v-else>
        <div class="pm-grid ui-th hidden md:grid">
          <span>Holding</span>
          <span class="text-right">Return</span>
          <span class="text-right">Contribution</span>
          <span class="text-right">Market</span>
          <span class="text-right">Sector</span>
          <span class="text-right">Stock-specific</span>
        </div>
        <ul class="ui-rows">
          <li
            v-for="r in rows"
            :key="r.symbol"
            class="pm-grid grid min-h-14 px-5 py-2.5 items-center cursor-pointer transition-colors duration-150 ease-out hover:bg-white/[0.03] focus-visible:outline-none focus-visible:bg-white/[0.04]"
            :class="r.symbol === current ? 'bg-white/[0.025]' : ''"
            role="button"
            tabindex="0"
            :aria-label="`Analyze ${r.symbol}`"
            :aria-current="r.symbol === current ? 'true' : undefined"
            @click="emit('pick', r.symbol)"
            @keydown.enter.prevent="emit('pick', r.symbol)"
            @keydown.space.prevent="emit('pick', r.symbol)"
          >
            <span class="min-w-0">
              <span class="flex items-baseline gap-2 min-w-0">
                <span class="text-sm font-medium text-slate-100">{{ r.symbol }}</span>
                <span v-if="r.weight != null" class="text-xs text-slate-500 tabular-nums">{{ r.weight.toFixed(1) }}%</span>
              </span>
              <span v-if="r.note" class="mt-0.5 flex items-center gap-1.5 min-w-0 text-xs" :class="r.noteClass">
                <CategoryGlyph :category="r.event?.category" :hollow="r.hollow" />
                <span class="truncate">{{ r.note }}</span>
              </span>
              <!-- Phone: the split columns are hidden, so the parts ride here -->
              <span class="md:hidden mt-0.5 block text-xs text-slate-500 tabular-nums truncate">
                Market {{ fmtPct(r.market) }} · Sector {{ fmtPct(r.sector) }} · Stock {{ fmtPct(r.specific) }}
              </span>
            </span>
            <span class="hidden md:block text-right text-sm tabular-nums" :class="signClass(r.ret)">{{ fmtPct(r.ret) }}</span>
            <span class="text-right tabular-nums">
              <span class="block text-sm" :class="signClass(r.contribution)">{{ fmtPct(r.contribution) }}</span>
              <span class="md:hidden block text-xs mt-0.5" :class="signClass(r.ret)">{{ fmtPct(r.ret) }} own</span>
            </span>
            <span class="hidden md:block text-right text-sm tabular-nums" :class="signClass(r.market)">{{ fmtPct(r.market) }}</span>
            <span class="hidden md:block text-right text-sm tabular-nums" :class="signClass(r.sector)">{{ fmtPct(r.sector) }}</span>
            <span class="hidden md:block text-right text-sm tabular-nums" :class="signClass(r.specific)">{{ fmtPct(r.specific) }}</span>
          </li>
          <li v-if="totals" class="pm-grid grid min-h-12 px-5 py-2.5 items-center bg-white/[0.015]">
            <span class="min-w-0">
              <span class="block text-sm text-slate-200">Account</span>
              <span class="md:hidden mt-0.5 block text-xs text-slate-500 tabular-nums truncate">
                Market {{ fmtPct(totals.market_pct) }} · Sector {{ fmtPct(totals.sector_pct) }} · Stock {{ fmtPct(totals.specific_pct) }}
              </span>
            </span>
            <span class="hidden md:block text-right text-sm text-slate-600">—</span>
            <span class="text-right text-sm font-medium tabular-nums" :class="signClass(totals.contribution_pct)">{{ fmtPct(totals.contribution_pct) }}</span>
            <span class="hidden md:block text-right text-sm tabular-nums" :class="signClass(totals.market_pct)">{{ fmtPct(totals.market_pct) }}</span>
            <span class="hidden md:block text-right text-sm tabular-nums" :class="signClass(totals.sector_pct)">{{ fmtPct(totals.sector_pct) }}</span>
            <span class="hidden md:block text-right text-sm tabular-nums" :class="signClass(totals.specific_pct)">{{ fmtPct(totals.specific_pct) }}</span>
          </li>
        </ul>
      </template>
    </div>
    <p v-if="error && data" class="mt-2 text-xs text-slate-500">{{ error }}. Showing the last result</p>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import CategoryGlyph from './CategoryGlyph.vue'
import { fmtPct, signClass } from '../../utils/dashboardAnalytics.js'
import { fmtEventDate, isHollow, statusText } from '../../utils/moveAnalysis.js'

const props = defineProps({
  data: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  accountName: { type: String, default: '' },
  hasAccount: { type: Boolean, default: true },
  current: { type: String, default: '' },
})
const emit = defineEmits(['pick', 'retry'])

const n = v => (v == null || !Number.isFinite(Number(v)) ? null : Number(v))

const totals = computed(() => (props.data?.totals && rows.value.length ? props.data.totals : null))

const rows = computed(() => {
  const list = Array.isArray(props.data?.holdings) ? props.data.holdings : []
  return list.filter(h => h?.symbol).map(h => {
    const ev = h.event || null
    let note = ''
    let noteClass = 'text-slate-400'
    if (ev) {
      if (ev.status === 'explained') {
        note = ev.headline || ev.category_label || 'Explained'
        noteClass = 'text-slate-300'
      } else {
        note = `Unusual move. ${statusText(ev)}`
      }
    } else if (h.flagged) {
      note = 'Unusual move'
    }
    return {
      symbol: String(h.symbol).toUpperCase(),
      weight: n(h.weight_pct),
      ret: n(h.ret_pct),
      contribution: n(h.contribution_pct),
      market: n(h.market_pct),
      sector: n(h.sector_pct),
      specific: n(h.specific_pct),
      event: ev,
      hollow: !ev || isHollow(ev),
      note,
      noteClass,
    }
  })
})

const subtitle = computed(() => {
  const d = props.data
  if (!d?.as_of) return ''
  const parts = []
  if (props.accountName) parts.push(props.accountName)
  parts.push(fmtEventDate(d.as_of, { long: true }))
  if (n(d.spy_pct) != null) parts.push(`S&P 500 ${fmtPct(d.spy_pct)}`)
  return parts.join(' · ')
})
</script>

<style scoped>
/* Hidden cells take no grid track:
   phone  holding (note, parts) · contribution
   md+    holding · return · contribution · market · sector · stock-specific */
.pm-grid {
  grid-template-columns: minmax(0, 1fr) auto;
  column-gap: 16px;
}
@media (min-width: 768px) {
  .pm-grid { grid-template-columns: minmax(0, 1.8fr) 84px 104px 84px 84px 112px; }
}
</style>
