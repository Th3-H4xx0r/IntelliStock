<!-- frontend/src/components/swing/SwingOrderChart.vue
     The underlying's price on the order review, mirroring iOS
     SwingOrderChart (ios/IntelliStock/Features/Swing/Views/
     SwingOrderReviewSheet.swift): GET /symbol-historicals over 1D / 1W / 1M /
     3M / 1Y, polled every 10 s on 1D and 30 s otherwise while the review is
     open. For a sold put the strike is one labelled dashed line over a faint
     loss-coloured assignment zone; without a strike a dotted line marks the
     range's opening price. Plotted by index (no overnight gaps), as iOS does.
     Hover or drag to read a price. Read-only: nothing is sent from here. -->
<template>
  <div class="min-w-0">
    <div class="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
      <div class="min-w-0">
        <template v-if="line">
          <p class="text-2xl font-medium text-slate-100 tabular-nums leading-none">{{ line.price }}</p>
          <p class="mt-1.5 text-sm tabular-nums" :class="toneClass(line.tone)">
            {{ line.text }}
            <span class="text-slate-500 ml-1">{{ caption }}</span>
          </p>
        </template>
        <p v-else class="text-sm text-slate-400">{{ symbol }} price</p>
      </div>
      <div class="ui-seg" role="group" aria-label="Chart range">
        <button
          v-for="r in ranges"
          :key="r"
          type="button"
          class="ui-seg-btn tabular-nums"
          :class="range === r ? 'ui-seg-btn-active' : ''"
          :aria-pressed="range === r"
          @click="setRange(r)"
        >{{ r }}</button>
      </div>
    </div>

    <div ref="boxEl" class="relative mt-4" :style="{ height: `${HEIGHT}px` }">
      <div v-if="!series && loading" class="ui-skel h-full w-full rounded-lg" aria-hidden="true"></div>
      <div v-else-if="!series || series.vals.length < 2" class="h-full flex items-center justify-center text-sm text-slate-500">
        Couldn't load prices for {{ symbol }}
      </div>
      <svg
        v-else
        :width="width"
        :height="HEIGHT"
        :viewBox="`0 0 ${width} ${HEIGHT}`"
        class="block select-none touch-none"
        role="img"
        :aria-label="`${symbol} price, ${range}`"
        @pointermove="onPointer"
        @pointerdown="onPointer"
        @pointerleave="scrub = null"
      >
        <defs>
          <linearGradient :id="gradId" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" :stop-color="lineColor" stop-opacity="0.12" />
            <stop offset="100%" :stop-color="lineColor" stop-opacity="0" />
          </linearGradient>
        </defs>

        <!-- Hairline grid and price labels -->
        <g>
          <template v-for="t in ticks" :key="`t-${t}`">
            <line :x1="0" :x2="plotW" :y1="y(t)" :y2="y(t)" stroke="rgba(255,255,255,0.05)" />
            <text :x="width - 2" :y="y(t) + 3.5" text-anchor="end" class="axis">{{ fmtAxis(t) }}</text>
          </template>
          <text
            v-for="xl in xLabels"
            :key="`x-${xl.i}`"
            :x="x(xl.i)"
            :y="HEIGHT - 4"
            :text-anchor="xl.anchor"
            class="axis"
          >{{ xl.text }}</text>
        </g>

        <!-- Assignment zone under a sold put's strike -->
        <rect
          v-if="strikeY != null && riskBelow"
          :x="0" :y="strikeY" :width="plotW" :height="Math.max(0, PLOT_BOTTOM - strikeY)"
          fill="#e5484d" fill-opacity="0.06"
        />

        <path :d="areaPath" :fill="`url(#${gradId})`" />
        <path :d="linePath" fill="none" :stroke="lineColor" stroke-width="1.75" stroke-linejoin="round" stroke-linecap="round" />

        <!-- Strike, or the range's opening price -->
        <template v-if="strikeY != null">
          <line :x1="0" :x2="plotW" :y1="strikeY" :y2="strikeY" stroke="rgba(237,237,237,0.45)" stroke-dasharray="4 3" />
          <text :x="4" :y="strikeY - 5" class="level">Strike {{ fmtUsd(strike) }}</text>
        </template>
        <line
          v-else
          :x1="0" :x2="plotW" :y1="y(series.vals[0])" :y2="y(series.vals[0])"
          stroke="rgba(237,237,237,0.28)" stroke-dasharray="2 3"
        />

        <!-- Scrub -->
        <template v-if="scrub != null">
          <line :x1="x(scrub)" :x2="x(scrub)" :y1="PLOT_TOP" :y2="PLOT_BOTTOM" stroke="rgba(237,237,237,0.25)" />
          <circle :cx="x(scrub)" :cy="y(series.vals[scrub])" r="3.5" :fill="lineColor" stroke="#161617" stroke-width="2" />
        </template>
      </svg>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { getToken } from '../../utils/auth.js'
import { fmtUsd } from '../../utils/swing.js'
import {
  REVIEW_CHART_RANGES, chartPollMs, niceTicks, paddedBounds, priceLine, seriesFromPoints,
} from '../../utils/swingReview.js'

const props = defineProps({
  symbol: { type: String, required: true },
  apiBase: { type: String, required: true },
  /** A sold put's strike; null for a swing order. */
  strike: { type: Number, default: null },
  /** Shade the assignment zone below the strike. */
  riskBelow: { type: Boolean, default: true },
})

const ranges = REVIEW_CHART_RANGES
const HEIGHT = 200
const PLOT_TOP = 10
const PLOT_BOTTOM = HEIGHT - 22
const AXIS_W = 60

const range = ref('1D')
const series = ref(null)     // { ts: ms[], vals: number[] }
const loading = ref(true)
const scrub = ref(null)      // index under the pointer
const width = ref(640)
const boxEl = ref(null)
const gradId = `swing-chart-fade-${Math.random().toString(36).slice(2, 8)}`
let timer = null
let ro = null
let generation = 0
let alive = true

function authHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function fetchSeries() {
  const r = range.value
  const gen = generation
  try {
    const q = new URLSearchParams({ symbols: props.symbol, range: r })
    const res = await fetch(`${props.apiBase}/symbol-historicals?${q}`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const body = await res.json()
    if (gen !== generation || !alive) return
    series.value = seriesFromPoints(body?.results?.[props.symbol] || [], r)
  } catch {
    // Keep the last good series on a failed poll (StockModel.refreshHistory).
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

function setRange(r) {
  if (r === range.value) return
  range.value = r
  generation += 1
  series.value = null
  scrub.value = null
  loading.value = true
  fetchSeries()
  schedule()
}

watch(() => props.symbol, () => {
  generation += 1
  series.value = null
  loading.value = true
  fetchSeries()
  schedule()
})

onMounted(() => {
  if (boxEl.value && 'ResizeObserver' in window) {
    ro = new ResizeObserver(entries => {
      const w = Math.round(entries[0]?.contentRect?.width || 0)
      if (w > 0) width.value = w
    })
    ro.observe(boxEl.value)
  } else if (boxEl.value) {
    width.value = boxEl.value.clientWidth || 640
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
const bounds = computed(() => {
  const vals = series.value?.vals || []
  const extra = props.strike != null && props.strike > 0 ? [props.strike] : [vals[0]]
  return paddedBounds([...vals, ...extra])
})
const ticks = computed(() => niceTicks(bounds.value.min, bounds.value.max, 3))

function x(i) {
  const n = series.value?.vals.length || 1
  return n <= 1 ? 0 : (i / (n - 1)) * plotW.value
}
function y(v) {
  const { min, max } = bounds.value
  const span = max - min || 1
  return PLOT_TOP + (1 - (v - min) / span) * (PLOT_BOTTOM - PLOT_TOP)
}

const linePath = computed(() => {
  const vals = series.value?.vals || []
  return vals.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('')
})
const areaPath = computed(() => {
  const vals = series.value?.vals || []
  if (vals.length < 2) return ''
  return `${linePath.value}L${x(vals.length - 1).toFixed(1)},${PLOT_BOTTOM}L0,${PLOT_BOTTOM}Z`
})
const strikeY = computed(() => (props.strike != null && props.strike > 0 && series.value ? y(props.strike) : null))

const up = computed(() => {
  const v = series.value?.vals || []
  return v.length < 2 || v[v.length - 1] >= v[0]
})
const lineColor = computed(() => (up.value ? '#4cc38a' : '#e5484d'))

const line = computed(() => priceLine(series.value?.vals, scrub.value, props.strike))

const RANGE_CAPTIONS = { '1D': 'Today', '1W': 'Past week', '1M': 'Past month', '3M': 'Past 3 months', '1Y': 'Past year' }
const caption = computed(() => {
  if (scrub.value != null && series.value) return fmtTime(series.value.ts[scrub.value], true)
  return props.strike != null ? '' : RANGE_CAPTIONS[range.value]
})

function fmtTime(ms, long = false) {
  if (!Number.isFinite(ms)) return ''
  const d = new Date(ms)
  if (range.value === '1D') return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
  if (range.value === '1Y' && !long) return d.toLocaleDateString('en-US', { month: 'short', year: '2-digit' })
  return d.toLocaleDateString('en-US', long && range.value === '1W'
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
  return fmtUsd(v)
}

function toneClass(tone) {
  return tone === 'gain' ? 'text-gain' : tone === 'loss' ? 'text-loss' : 'text-slate-400'
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
.level {
  font-size: 11px;
  fill: #a1a1aa;
  font-variant-numeric: tabular-nums;
}
</style>
