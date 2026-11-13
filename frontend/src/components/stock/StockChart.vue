<!-- frontend/src/components/stock/StockChart.vue
     The stock page's hero: the live price and its move over the range, over
     the price chart and its ranges — the web twin of the iOS StockView hero
     (ios/IntelliStock/Features/Stock/Views/StockView.swift). Reads
     GET /symbol-historicals, polled every 10 s on 1D and 30 s otherwise while
     the tab is visible; plotted by index (no overnight gaps), as iOS does.
     Hover or drag to read a price. Emits the loaded series so the page can
     price the order ticket and fill the range's open/high/low. -->
<template>
  <div class="min-w-0">
    <!-- Price and change, following the scrub -->
    <div class="min-h-[76px]">
      <template v-if="hero">
        <p class="text-[44px] font-medium text-slate-100 tabular-nums leading-none tracking-[-0.02em]">{{ hero.price }}</p>
        <p class="mt-2 text-sm tabular-nums">
          <span :class="hero.tone">{{ hero.change }}</span>
          <span class="text-slate-500 ml-1.5">{{ caption }}</span>
        </p>
      </template>
      <div v-else-if="loading" class="space-y-3" aria-hidden="true">
        <div class="ui-skel rounded-md h-11 w-48"></div>
        <div class="ui-skel h-4 w-40"></div>
      </div>
      <template v-else>
        <p class="text-[44px] font-medium text-slate-600 leading-none">—</p>
        <p class="mt-2 text-sm text-slate-500">No price data</p>
      </template>
    </div>

    <div ref="boxEl" class="relative mt-6" :style="{ height: `${height}px` }">
      <div v-if="!usable && loading" class="ui-skel h-full w-full rounded-lg" aria-hidden="true"></div>
      <div v-else-if="!usable" class="h-full flex items-center justify-center text-sm text-slate-500 text-center px-6">
        {{ failed ? "Couldn't load prices" : `No chart data for ${symbol}` }}
      </div>
      <svg
        v-else
        :width="width"
        :height="height"
        :viewBox="`0 0 ${width} ${height}`"
        class="block select-none touch-none cursor-crosshair"
        role="img"
        :aria-label="`${symbol} price, ${range}`"
        @pointermove="onPointer"
        @pointerdown="onPointer"
        @pointerleave="scrub = null"
      >
        <defs>
          <linearGradient :id="fadeId" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" :stop-color="lineColor" stop-opacity="0.12" />
            <stop offset="100%" :stop-color="lineColor" stop-opacity="0" />
          </linearGradient>
        </defs>

        <g>
          <template v-for="t in ticks" :key="`t-${t}`">
            <line :x1="0" :x2="plotW" :y1="y(t)" :y2="y(t)" stroke="rgba(255,255,255,0.05)" />
            <text :x="width - 2" :y="y(t) + 3.5" text-anchor="end" class="axis">{{ fmtAxis(t) }}</text>
          </template>
          <text
            v-for="xl in xLabels"
            :key="`x-${xl.i}`"
            :x="x(xl.i)"
            :y="height - 4"
            :text-anchor="xl.anchor"
            class="axis"
          >{{ xl.text }}</text>
        </g>

        <path :d="areaPath" :fill="`url(#${fadeId})`" />
        <path :d="linePath" fill="none" :stroke="lineColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />

        <!-- The range's opening price -->
        <line
          :x1="0" :x2="plotW" :y1="y(series.vals[0])" :y2="y(series.vals[0])"
          stroke="rgba(237,237,237,0.28)" stroke-dasharray="2 3"
        />

        <template v-if="scrub != null">
          <line :x1="x(scrub)" :x2="x(scrub)" :y1="PLOT_TOP" :y2="plotBottom" stroke="rgba(237,237,237,0.25)" />
          <circle :cx="x(scrub)" :cy="y(series.vals[scrub])" r="3.5" :fill="lineColor" stroke="#0f0f10" stroke-width="2" />
        </template>
      </svg>
    </div>

    <div class="mt-4 pb-4 border-b border-white/[0.06] overflow-x-auto">
      <div class="ui-seg" role="group" aria-label="Chart range">
        <button
          v-for="r in STOCK_RANGES"
          :key="r"
          type="button"
          class="ui-seg-btn tabular-nums"
          :class="range === r ? 'ui-seg-btn-active' : ''"
          :aria-pressed="range === r"
          @click="setRange(r)"
        >{{ r }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { apiGet } from '../../composables/useStockApi.js'
import { fmtUsd, fmtSignedUsd, fmtPct, fmtPrice } from '../../utils/dashboardAnalytics.js'
import { chartPollMs, niceTicks, paddedBounds, seriesFromPoints } from '../../utils/swingReview.js'
import { STOCK_RANGES, RANGE_CAPTIONS } from '../../utils/stock.js'

const props = defineProps({
  symbol: { type: String, required: true },
})
const emit = defineEmits(['series'])

const PLOT_TOP = 10
const AXIS_W = 64

const range = ref('1D')
const series = ref(null)     // { ts: ms[], vals: number[] }
const loading = ref(true)
const failed = ref(false)
const scrub = ref(null)
const width = ref(720)
const boxEl = ref(null)
const fadeId = `stock-chart-fade-${Math.random().toString(36).slice(2, 8)}`
let timer = null
let ro = null
let generation = 0
let alive = true

const height = computed(() => (width.value < 520 ? 220 : 300))
const plotBottom = computed(() => height.value - 22)
const usable = computed(() => (series.value?.vals.length || 0) >= 2)

async function fetchSeries() {
  const r = range.value
  const gen = generation
  try {
    const q = new URLSearchParams({ symbols: props.symbol, range: r })
    const body = await apiGet(`/symbol-historicals?${q}`)
    if (gen !== generation || !alive) return
    series.value = seriesFromPoints(body?.results?.[props.symbol] || [], r)
    failed.value = false
    emit('series', { range: r, series: series.value })
  } catch {
    // Keep the last good series on a failed poll (StockModel.refreshHistory).
    if (gen === generation && !series.value) failed.value = true
  } finally {
    if (gen === generation) loading.value = false
  }
}

function schedule() {
  clearTimeout(timer)
  if (!alive) return
  timer = setTimeout(async () => {
    if (document.visibilityState === 'visible') await fetchSeries()
    schedule()
  }, chartPollMs(range.value))
}

function restart() {
  generation += 1
  series.value = null
  scrub.value = null
  loading.value = true
  failed.value = false
  emit('series', { range: range.value, series: null })
  fetchSeries()
  schedule()
}

function setRange(r) {
  if (r === range.value) return
  range.value = r
  restart()
}

watch(() => props.symbol, restart)

onMounted(() => {
  if (boxEl.value && 'ResizeObserver' in window) {
    ro = new ResizeObserver(entries => {
      const w = Math.round(entries[0]?.contentRect?.width || 0)
      if (w > 0) width.value = w
    })
    ro.observe(boxEl.value)
  } else if (boxEl.value) {
    width.value = boxEl.value.clientWidth || 720
  }
  fetchSeries()
  schedule()
})

onUnmounted(() => {
  alive = false
  clearTimeout(timer)
  ro?.disconnect()
})

// ── Geometry ──────────────────────────────────────────────────────────────────
const plotW = computed(() => Math.max(10, width.value - AXIS_W))
const bounds = computed(() => paddedBounds(series.value?.vals || []))
const ticks = computed(() => niceTicks(bounds.value.min, bounds.value.max, 3))

function x(i) {
  const n = series.value?.vals.length || 1
  return n <= 1 ? 0 : (i / (n - 1)) * plotW.value
}
function y(v) {
  const { min, max } = bounds.value
  const span = max - min || 1
  return PLOT_TOP + (1 - (v - min) / span) * (plotBottom.value - PLOT_TOP)
}

const linePath = computed(() => {
  const vals = series.value?.vals || []
  return vals.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('')
})
const areaPath = computed(() => {
  const vals = series.value?.vals || []
  if (vals.length < 2) return ''
  return `${linePath.value}L${x(vals.length - 1).toFixed(1)},${plotBottom.value}L0,${plotBottom.value}Z`
})

const up = computed(() => {
  const v = series.value?.vals || []
  return v.length < 2 || v[v.length - 1] >= v[0]
})
const lineColor = computed(() => (up.value ? '#4cc38a' : '#e5484d'))

const hero = computed(() => {
  const vals = series.value?.vals
  if (!vals || vals.length < 2) return null
  const i = scrub.value != null && scrub.value >= 0 && scrub.value < vals.length ? scrub.value : vals.length - 1
  const shown = vals[i]
  const change = shown - vals[0]
  const pct = vals[0] ? (change / vals[0]) * 100 : 0
  return {
    price: fmtPrice(shown),
    change: `${fmtSignedUsd(change)} (${fmtPct(pct)})`,
    tone: Math.abs(change) < 0.005 ? 'text-slate-400' : change > 0 ? 'text-gain' : 'text-loss',
  }
})

const caption = computed(() => {
  if (scrub.value != null && series.value) return fmtTime(series.value.ts[scrub.value], true)
  return RANGE_CAPTIONS[range.value]
})

function fmtTime(ms, long = false) {
  if (!Number.isFinite(ms)) return ''
  const d = new Date(ms)
  const r = range.value
  if (r === '1D') return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
  if ((r === '1Y' || r === 'ALL') && !long) return d.toLocaleDateString('en-US', { month: 'short', year: '2-digit' })
  if (r === 'ALL' || r === '1Y') return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  return d.toLocaleDateString('en-US', long && r === '1W'
    ? { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }
    : { month: 'short', day: 'numeric' })
}

const xLabels = computed(() => {
  const ts = series.value?.ts || []
  const n = ts.length
  if (n < 2) return []
  const count = width.value < 420 ? 3 : 4
  const out = []
  for (let k = 0; k < count; k++) {
    const i = Math.round((k / (count - 1)) * (n - 1))
    out.push({ i, text: fmtTime(ts[i]), anchor: k === 0 ? 'start' : k === count - 1 ? 'end' : 'middle' })
  }
  return out
})

function fmtAxis(v) {
  return Math.abs(v) >= 1000 ? fmtUsd(v).replace(/\.\d\d$/, '') : fmtUsd(v)
}

function onPointer(e) {
  const vals = series.value?.vals
  if (!vals?.length) return
  const rect = e.currentTarget.getBoundingClientRect()
  const px = Math.min(Math.max(e.clientX - rect.left, 0), plotW.value)
  scrub.value = Math.round((px / plotW.value) * (vals.length - 1))
}
</script>

<style scoped>
.axis {
  font-size: 11px;
  fill: #71717a;
  font-variant-numeric: tabular-nums;
}
</style>
