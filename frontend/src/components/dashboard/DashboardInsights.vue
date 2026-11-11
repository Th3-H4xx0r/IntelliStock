<script setup>
// Account insights beside the holdings — the web twin of the iOS "Insights"
// group: today's P&L, diversification, risk from the 1Y curve, sector
// allocation and today's movers among the holdings.
import { ref, computed, watch } from 'vue'
import { concentration, todaysMovers, fmtSignedUsd, fmtPct, signClass } from '../../utils/dashboardAnalytics.js'
import { loadRisk, loadSectorAllocation } from '../../composables/useDashboardData.js'

const props = defineProps({
  brokerageId: { type: String, default: null },
  holdings: { type: Object, default: null },
  dailySparks: { type: Object, default: null },
  today: { type: Object, default: null }, // { change_abs, change_pct } for the account today
})

const risk = ref(null)
const slices = ref(null)

watch(() => props.brokerageId, async id => {
  risk.value = null
  if (!id) return
  const r = await loadRisk(id)
  if (id === props.brokerageId) risk.value = r
}, { immediate: true })

// Sectors only change with the set of symbols held, not with every price poll.
const symbolKey = computed(() => (props.holdings?.positions || []).map(p => p.symbol).sort().join(','))
watch([() => props.brokerageId, symbolKey], async ([id]) => {
  slices.value = null
  if (!id || !props.holdings) return
  const s = await loadSectorAllocation(props.holdings.positions)
  if (id === props.brokerageId) slices.value = s
}, { immediate: true })

const conc = computed(() => concentration((props.holdings?.positions || []).map(p => p.marketValue)))
const scoreClass = computed(() => {
  const s = conc.value.score
  return s >= 66 ? 'text-gain' : s >= 33 ? 'text-amber-400' : 'text-loss'
})

const movers = computed(() => {
  if (!props.dailySparks) return null
  return todaysMovers(props.dailySparks, (props.holdings?.positions || []).map(p => p.symbol))
})

// ETFs report no sector, so an all-ETF book is one "Other" slice: say nothing
// rather than show a single bar that explains nothing.
const showSectors = computed(() =>
  !!slices.value?.length && !(slices.value.length === 1 && slices.value[0].sector === 'Other'))
// Neutral steps, no hue: the bar shows shares, not gains or losses.
const SLICE_SHADES = ['#ededed', '#b4b4b8', '#85858b', '#5f5f64', '#45454a', '#333336']
const shade = i => SLICE_SHADES[Math.min(i, SLICE_SHADES.length - 1)]
const muted = 'text-slate-600'
</script>

<template>
  <section v-if="holdings" aria-labelledby="insights-title">
    <div class="mb-3">
      <h2 id="insights-title" class="text-base font-medium text-slate-100">Insights</h2>
      <p class="text-sm text-slate-500 mt-0.5">For the selected account</p>
    </div>

    <div class="rounded-xl border border-white/[0.07] bg-surface divide-y divide-white/[0.06]">
      <div class="px-5 py-4">
        <p class="text-xs text-slate-500">Today</p>
        <div class="mt-2 grid grid-cols-2 gap-4">
          <div>
            <p class="stat-label">Day P&L</p>
            <p class="stat-value" :class="today ? signClass(today.change_abs) : muted">{{ today ? fmtSignedUsd(today.change_abs) : '—' }}</p>
          </div>
          <div>
            <p class="stat-label">Change</p>
            <p class="stat-value" :class="today ? signClass(today.change_pct) : muted">{{ today ? fmtPct(today.change_pct) : '—' }}</p>
          </div>
        </div>
      </div>

      <div class="px-5 py-4">
        <p class="text-xs text-slate-500">Diversification</p>
        <div class="mt-2 grid grid-cols-3 gap-4">
          <div>
            <p class="stat-label">Score</p>
            <p class="stat-value" :class="conc.count ? scoreClass : muted">{{ conc.count ? `${conc.score}/100` : '—' }}</p>
          </div>
          <div>
            <p class="stat-label">Top holding</p>
            <p class="stat-value text-slate-100">{{ conc.count ? `${Math.round(conc.topWeight)}%` : '—' }}</p>
          </div>
          <div>
            <p class="stat-label">Holdings</p>
            <p class="stat-value text-slate-100">{{ conc.count }}</p>
          </div>
        </div>
      </div>

      <div v-if="!risk || risk.points >= 2" class="px-5 py-4">
        <p class="text-xs text-slate-500">Risk · past year</p>
        <div class="mt-2 grid grid-cols-3 gap-4">
          <div>
            <p class="stat-label">Volatility</p>
            <p class="stat-value text-slate-100">{{ risk ? `${Math.round(risk.volatility)}%` : '—' }}</p>
          </div>
          <div>
            <p class="stat-label">Max drawdown</p>
            <p class="stat-value text-slate-100">{{ risk ? `${Math.round(risk.maxDrawdown)}%` : '—' }}</p>
          </div>
          <div>
            <p class="stat-label">Sharpe</p>
            <p class="stat-value text-slate-100">{{ risk ? (risk.sharpe == null ? '—' : risk.sharpe.toFixed(2)) : '—' }}</p>
          </div>
        </div>
      </div>

      <div v-if="showSectors" class="px-5 py-4">
        <p class="text-xs text-slate-500">Sector allocation</p>
        <div class="mt-3 flex h-2 rounded-full overflow-hidden gap-px bg-raised">
          <span v-for="(s, i) in slices" :key="s.sector" :style="{ width: `${s.pct}%`, background: shade(i) }"></span>
        </div>
        <ul class="mt-3 space-y-2">
          <li v-for="(s, i) in slices" :key="s.sector" class="flex items-center gap-2 text-sm">
            <span class="size-2 rounded-sm shrink-0" :style="{ background: shade(i) }"></span>
            <span class="text-slate-300 truncate">{{ s.sector }}</span>
            <span class="ml-auto text-slate-400 tabular-nums">{{ s.pct.toFixed(1) }}%</span>
          </li>
        </ul>
      </div>

      <div v-if="movers && movers.length" class="px-5 py-4">
        <p class="text-xs text-slate-500">Today's movers</p>
        <ul class="mt-2 space-y-2">
          <li v-for="m in movers.slice(0, 5)" :key="m.symbol" class="flex items-center justify-between text-sm">
            <span class="text-slate-200 font-medium">{{ m.symbol }}</span>
            <span class="tabular-nums" :class="signClass(m.pct)">{{ fmtPct(m.pct) }}</span>
          </li>
        </ul>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* No colour here: a scoped colour would outrank the gain/loss utilities. */
.stat-label { font-size: 12px; line-height: 16px; color: #85858b; }
.stat-value { margin-top: 4px; font-size: 16px; line-height: 20px; font-weight: 500; font-variant-numeric: tabular-nums; }
</style>
