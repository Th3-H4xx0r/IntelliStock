<!-- frontend/src/components/analysis/MoveChart.vue
     The AI Analysis chart: daily closes with a marker on every shown event
     (colour and shape by category, hollow until explained), and under it
     the daily-move strip (green up, red down; flagged days at full strength,
     the rest faded). Drawn by hand in SVG like components/stock/StockChart.vue,
     plotted by index so weekends leave no gaps. Hover or drag to read a day;
     click a marker or a flagged bar to select its event. -->
<template>
  <div ref="boxEl" class="relative min-w-0 select-none">
    <div v-if="bars.length < 2" class="h-[220px] flex items-center justify-center text-sm text-slate-500 text-center px-6">
      No price history for {{ symbol }} in this range
    </div>

    <template v-else>
      <!-- Hover read-out -->
      <div
        v-if="tip"
        class="pointer-events-none absolute z-10 top-0 w-[232px] rounded-lg border border-white/[0.08] bg-surface px-3 py-2.5 shadow-[0_12px_32px_rgba(0,0,0,0.45)]"
        :style="{ left: `${tip.left}px` }"
      >
        <p class="text-xs text-slate-500">{{ tip.date }}</p>
        <p class="mt-1 flex items-baseline justify-between gap-3 tabular-nums">
          <span class="text-sm text-slate-100">{{ tip.close }}</span>
          <span class="text-sm" :class="tip.tone">{{ tip.ret }}</span>
        </p>
        <p v-if="tip.split" class="mt-1 text-xs text-slate-400 tabular-nums leading-relaxed">{{ tip.split }}</p>
        <p v-if="tip.headline" class="mt-1.5 text-xs text-slate-200 leading-snug line-clamp-2">{{ tip.headline }}</p>
      </div>

      <svg
        :width="width"
        :height="height"
        :viewBox="`0 0 ${width} ${height}`"
        class="block touch-none cursor-crosshair"
        role="group"
        :aria-label="`${symbol} daily closes, ${range}, with ${markers.length} marked moves`"
        @pointermove="onPointer"
        @pointerdown="onPointer"
        @pointerleave="scrub = null"
        @click="onPriceClick"
      >
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

        <path :d="linePath" fill="none" stroke="rgba(237,237,237,0.55)" stroke-width="1.75" stroke-linejoin="round" stroke-linecap="round" />

        <line
          v-if="scrub != null"
          :x1="x(scrub)" :x2="x(scrub)" :y1="PLOT_TOP" :y2="plotBottom"
          stroke="rgba(237,237,237,0.25)"
        />

        <!-- Event markers; the selected one is drawn last so it sits on top -->
        <g
          v-for="m in orderedMarkers"
          :key="m.event.id"
          class="marker cursor-pointer"
          role="button"
          tabindex="0"
          :aria-label="markerLabel(m)"
          :aria-pressed="m.event.id === selectedId"
          @click.stop="emit('select', m.event.id)"
          @keydown.enter.prevent="emit('select', m.event.id)"
          @keydown.space.prevent="emit('select', m.event.id)"
        >
          <circle
            class="marker-ring"
            :class="m.event.id === selectedId ? 'opacity-100' : 'opacity-0'"
            :cx="x(m.index)" :cy="y(m.close)" r="10"
            fill="none" stroke="#ededed" stroke-width="1.5"
          />
          <circle :cx="x(m.index)" :cy="y(m.close)" r="12" fill="transparent" />
          <circle
            v-if="m.shape === 'circle'"
            :cx="x(m.index)" :cy="y(m.close)" r="5"
            v-bind="m.paint"
          />
          <rect
            v-else-if="m.shape === 'square'"
            :x="x(m.index) - 4.5" :y="y(m.close) - 4.5" width="9" height="9" rx="1.5"
            v-bind="m.paint"
          />
          <path
            v-else
            :d="diamond(x(m.index), y(m.close))"
            v-bind="m.paint"
          />
        </g>
      </svg>

      <!-- Daily-move strip -->
      <div class="mt-3 flex items-center justify-between text-xs text-slate-500">
        <span>Daily move</span>
        <span class="tabular-nums">Flagged days at full strength</span>
      </div>
      <svg
        :width="width"
        :height="STRIP_H"
        :viewBox="`0 0 ${width} ${STRIP_H}`"
        class="mt-1.5 block touch-none cursor-crosshair"
        role="img"
        :aria-label="`${symbol} daily percent moves, ${range}`"
        @pointermove="onPointer"
        @pointerdown="onPointer"
        @pointerleave="scrub = null"
        @click="onStripClick"
      >
        <rect
          v-if="selectedIndex != null"
          :x="x(selectedIndex) - slot / 2" :y="0" :width="Math.max(slot, 2)" :height="STRIP_H"
          fill="rgba(237,237,237,0.07)"
        />
        <line :x1="0" :x2="plotW" :y1="STRIP_H / 2" :y2="STRIP_H / 2" stroke="rgba(255,255,255,0.08)" />
        <rect
          v-for="b in stripBars"
          :key="b.i"
          :x="b.x" :y="b.y" :width="b.w" :height="b.h" :rx="b.rx"
          :fill="b.fill" :fill-opacity="b.opacity"
        />
        <line
          v-if="scrub != null"
          :x1="x(scrub)" :x2="x(scrub)" :y1="0" :y2="STRIP_H"
          stroke="rgba(237,237,237,0.25)"
        />
        <text :x="width - 2" :y="10" text-anchor="end" class="axis">{{ fmtPct(stripScale) }}</text>
        <text :x="width - 2" :y="STRIP_H - 2" text-anchor="end" class="axis">{{ fmtPct(-stripScale) }}</text>
      </svg>

      <!-- What the markers mean -->
      <ul class="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-slate-500" aria-label="Marker legend">
        <li v-for="l in MARKER_LEGEND" :key="l.key" class="inline-flex items-center gap-1.5">
          <CategoryGlyph :category="l.category" :status="l.status" />
          {{ l.label }}
        </li>
      </ul>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { fmtPct, fmtPrice, fmtUsd } from '../../utils/dashboardAnalytics.js'
import { niceTicks, paddedBounds } from '../../utils/swingReview.js'
import CategoryGlyph from './CategoryGlyph.vue'
import {
  MARKER_LEGEND, eventMarkers, fmtEventDate, markerStyle, statusText, stripMax,
} from '../../utils/moveAnalysis.js'

const props = defineProps({
  symbol: { type: String, required: true },
  range: { type: String, required: true },
  bars: { type: Array, default: () => [] },          // oldest first
  events: { type: Array, default: () => [] },        // the events shown (after the filter)
  selectedId: { type: String, default: null },
  filterActive: { type: Boolean, default: false },
})
const emit = defineEmits(['select'])

const PLOT_TOP = 12
const AXIS_W = 60
const STRIP_H = 64
const PAGE_BG = '#0f0f10'
const GAIN = '#4cc38a'
const LOSS = '#e5484d'

const width = ref(720)
const boxEl = ref(null)
const scrub = ref(null)
let ro = null

const height = computed(() => (width.value < 520 ? 220 : 280))
const plotBottom = computed(() => height.value - 22)
const plotW = computed(() => Math.max(10, width.value - AXIS_W))
const n = computed(() => props.bars.length)
const slot = computed(() => plotW.value / Math.max(1, n.value))

function x(i) {
  return (i + 0.5) * slot.value
}

const closes = computed(() => props.bars.map(b => Number(b?.close)))
const bounds = computed(() => paddedBounds(closes.value.filter(Number.isFinite)))
const ticks = computed(() => niceTicks(bounds.value.min, bounds.value.max, 3))

function y(v) {
  const { min, max } = bounds.value
  const span = max - min || 1
  return PLOT_TOP + (1 - (v - min) / span) * (plotBottom.value - PLOT_TOP)
}

const linePath = computed(() => {
  let d = ''
  let pen = false
  closes.value.forEach((v, i) => {
    if (!Number.isFinite(v)) { pen = false; return }
    d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`
    pen = true
  })
  return d
})

function fmtAxis(v) {
  return Math.abs(v) >= 1000 ? fmtUsd(v).replace(/\.\d\d$/, '') : fmtUsd(v)
}

function fmtTick(ymd) {
  if (props.range === '6M' || props.range === '1Y') {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(ymd || ''))
    if (!m) return ''
    const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])))
    return d.toLocaleDateString('en-US', { month: 'short', year: '2-digit', timeZone: 'UTC' })
  }
  return fmtEventDate(ymd)
}

const xLabels = computed(() => {
  const count = n.value
  if (count < 2) return []
  const k = width.value < 420 ? 3 : 4
  const out = []
  for (let j = 0; j < k; j++) {
    const i = Math.round((j / (k - 1)) * (count - 1))
    out.push({ i, text: fmtTick(props.bars[i]?.date), anchor: j === 0 ? 'start' : j === k - 1 ? 'end' : 'middle' })
  }
  return out
})

// ── Markers ──────────────────────────────────────────────────────────────────
const markers = computed(() => eventMarkers(props.bars, props.events).map(m => {
  const style = markerStyle(m.event)
  return {
    ...m,
    shape: style.shape,
    paint: style.kind === 'filled'
      ? { fill: style.color, stroke: PAGE_BG, 'stroke-width': 2 }
      : { fill: PAGE_BG, stroke: style.color, 'stroke-width': style.kind === 'failed' ? 2 : 1.5 },
  }
}))

const orderedMarkers = computed(() => {
  const list = [...markers.value]
  const i = list.findIndex(m => m.event.id === props.selectedId)
  if (i >= 0) list.push(list.splice(i, 1)[0])
  return list
})

const selectedIndex = computed(() => markers.value.find(m => m.event.id === props.selectedId)?.index ?? null)

function diamond(cx, cy) {
  const r = 6
  return `M${cx},${cy - r}L${cx + r},${cy}L${cx},${cy + r}L${cx - r},${cy}Z`
}

function markerLabel(m) {
  const e = m.event
  const what = e.status === 'explained' ? (e.category_label || 'Explained') : statusText(e)
  const head = e.status === 'explained' && e.headline ? `, ${e.headline}` : ''
  return `${fmtEventDate(e.date, { long: true })}: ${fmtPct(e.ret_pct)}, ${what}${head}`
}

// ── Strip ────────────────────────────────────────────────────────────────────
const stripScale = computed(() => stripMax(props.bars))
const shownDates = computed(() => new Set(props.events.map(e => String(e?.date || ''))))

const stripBars = computed(() => {
  const mid = STRIP_H / 2
  const k = (mid - 3) / stripScale.value
  const s = slot.value
  const gap = s >= 6 ? 2 : s >= 3 ? 1 : 0.5
  const w = Math.max(1, s - gap)
  const out = []
  props.bars.forEach((b, i) => {
    const r = Number(b?.ret_pct)
    if (b?.ret_pct == null || !Number.isFinite(r)) return
    const h = Math.max(1, Math.abs(r) * k)
    const date = String(b?.date || '')
    const emphasised = shownDates.value.has(date) || (!props.filterActive && !!b?.flagged)
    out.push({
      i,
      x: x(i) - w / 2,
      y: r >= 0 ? mid - h : mid,
      w,
      h,
      rx: Math.min(2, w / 2),
      fill: r >= 0 ? GAIN : LOSS,
      opacity: emphasised ? 1 : 0.3,
    })
  })
  return out
})

// ── Hover and click ──────────────────────────────────────────────────────────
function indexAt(e) {
  const rect = e.currentTarget.getBoundingClientRect()
  const px = Math.min(Math.max(e.clientX - rect.left, 0), plotW.value - 0.01)
  return { px, i: Math.min(n.value - 1, Math.max(0, Math.floor(px / slot.value))) }
}

function onPointer(e) {
  if (n.value < 2) return
  scrub.value = indexAt(e).i
}

const eventByDate = computed(() => new Map(props.events.map(ev => [String(ev?.date || ''), ev])))

function onPriceClick(e) {
  if (n.value < 2) return
  const { px } = indexAt(e)
  let best = null
  for (const m of markers.value) {
    const d = Math.abs(x(m.index) - px)
    if (d <= 14 && (!best || d < best.d)) best = { d, id: m.event.id }
  }
  if (best) emit('select', best.id)
}

function onStripClick(e) {
  if (n.value < 2) return
  const { i } = indexAt(e)
  const ev = eventByDate.value.get(String(props.bars[i]?.date || ''))
  if (ev) emit('select', ev.id)
}

const TIP_W = 232
const tip = computed(() => {
  const i = scrub.value
  if (i == null || i < 0 || i >= n.value) return null
  const b = props.bars[i]
  const r = Number(b?.ret_pct)
  const parts = [['Market', b?.market_pct], ['Sector', b?.sector_pct], ['Stock', b?.specific_pct]]
    .filter(([, v]) => v != null && Number.isFinite(Number(v)))
  const ev = eventByDate.value.get(String(b?.date || ''))
  let headline = ''
  if (ev) headline = ev.status === 'explained' ? (ev.headline || ev.category_label || '') : statusText(ev)
  const left = Math.min(Math.max(x(i) - TIP_W / 2, 0), Math.max(0, width.value - TIP_W))
  return {
    left,
    date: fmtEventDate(b?.date, { long: true }),
    close: fmtPrice(b?.close),
    ret: b?.ret_pct == null ? '' : fmtPct(r),
    tone: !Number.isFinite(r) || Math.abs(r) < 0.005 ? 'text-slate-400' : r > 0 ? 'text-gain' : 'text-loss',
    split: parts.length === 3 ? parts.map(([k, v]) => `${k} ${fmtPct(v)}`).join(' · ') : '',
    headline,
  }
})

watch(() => [props.symbol, props.range], () => { scrub.value = null })

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
})

onUnmounted(() => ro?.disconnect())
</script>

<style scoped>
.axis {
  font-size: 11px;
  fill: #71717a;
  font-variant-numeric: tabular-nums;
}
.marker { outline: none; }
.marker:focus-visible .marker-ring { opacity: 1; }
</style>
