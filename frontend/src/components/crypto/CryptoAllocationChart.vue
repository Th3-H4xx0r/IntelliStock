<script setup>
import { ref, computed } from 'vue'
import VueApexCharts from 'vue3-apexcharts'

// Live-reactive allocation donut. `allocations` are the FIXED coins as
// [{ symbol, pct, color }] with pct in PERCENT (0-100); `dynamicPct` is the
// auto-discovered remainder (0-100). The Dynamic slice is rendered muted and
// visually distinct from the metallic-violet fixed coins. The center label
// tracks the hovered slice (defaults to the Dynamic total).
const props = defineProps({
  allocations: { type: Array, default: () => [] }, // [{ symbol, pct, color }]
  dynamicPct: { type: Number, default: 100 },
  size: { type: Number, default: 240 }, // outer diameter in px (chart scales to fit)
})

const DYNAMIC_COLOR = '#6b6488'
const hovered = ref(null) // slice index, or null (→ show Dynamic in center)

// ── Slice model (fixed coins with pct>0, then the Dynamic remainder) ──────────
const slices = computed(() => {
  const out = props.allocations
    .filter((a) => Number(a.pct) > 0)
    .map((a) => ({ label: a.symbol, value: Number(a.pct), color: a.color || '#a78bfa', dynamic: false }))
  const dyn = Number(props.dynamicPct) || 0
  if (dyn > 0) out.push({ label: 'Dynamic', value: dyn, color: DYNAMIC_COLOR, dynamic: true })
  return out
})

const hasData = computed(() => slices.value.length > 0)
const series = computed(() => slices.value.map((s) => Math.max(0, s.value)))

// ── Metallic-violet fill: each slice fades from its hue toward a darker shade ──
function hexToRgb(h) {
  const s = String(h || '').replace('#', '')
  return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)]
}
function mix(a, b, t) { return a.map((v, i) => Math.round(v + (b[i] - v) * t)) }
function toHex(c) { return '#' + c.map((v) => Math.max(0, Math.min(255, v)).toString(16).padStart(2, '0')).join('') }
function darken(hex, t) { return toHex(mix(hexToRgb(hex), [18, 12, 34], t)) }

const colors = computed(() => slices.value.map((s) => s.color))
const gradientTo = computed(() => slices.value.map((s) => (s.dynamic ? darken(s.color, 0.35) : darken(s.color, 0.55))))

// Hover handlers are stable so recomputing options isn't needed on hover — the
// center overlay reads `hovered` directly.
const onEnter = (_e, _ctx, cfg) => { hovered.value = cfg?.dataPointIndex ?? null }
const onLeave = () => { hovered.value = null }

const chartOptions = computed(() => ({
  chart: {
    type: 'donut',
    background: 'transparent',
    fontFamily: 'Inter, sans-serif',
    toolbar: { show: false },
    animations: { enabled: true, speed: 320, animateGradually: { enabled: false } },
    events: { dataPointMouseEnter: onEnter, dataPointMouseLeave: onLeave },
  },
  theme: { mode: 'dark' },
  labels: slices.value.map((s) => s.label),
  colors: colors.value,
  stroke: { width: 2, colors: ['#0f0a1c'] },
  dataLabels: { enabled: false },
  legend: { show: false },
  fill: {
    type: 'gradient',
    gradient: {
      type: 'diagonal2',
      shadeIntensity: 1,
      gradientToColors: gradientTo.value,
      inverseColors: false,
      opacityFrom: 1,
      opacityTo: 1,
      stops: [0, 100],
    },
  },
  plotOptions: {
    pie: {
      expandOnClick: false,
      donut: { size: '72%', labels: { show: false } },
    },
  },
  states: {
    hover: { filter: { type: 'lighten', value: 0.06 } },
    active: { filter: { type: 'none' } },
  },
  tooltip: {
    theme: 'dark',
    y: { formatter: (v) => `${Number(v).toFixed(0)}%` },
  },
}))

// ── Center overlay (follows the hovered slice, defaults to Dynamic) ───────────
const centerBig = computed(() => {
  const i = hovered.value
  if (i != null && slices.value[i]) return `${Math.round(slices.value[i].value)}%`
  return `${Math.round(Number(props.dynamicPct) || 0)}%`
})
const centerLabel = computed(() => {
  const i = hovered.value
  if (i != null && slices.value[i]) return slices.value[i].label
  return 'Dynamic'
})
const centerSub = computed(() => {
  const i = hovered.value
  if (i != null && slices.value[i]) return slices.value[i].dynamic ? 'auto-discovered' : 'fixed weight'
  return 'auto-discovered'
})
const centerColor = computed(() => {
  const i = hovered.value
  if (i != null && slices.value[i]) return slices.value[i].color
  return '#a78bfa'
})
</script>

<template>
  <div class="flex flex-col items-center">
    <div class="relative" :style="{ width: size + 'px', height: size + 'px' }">
      <VueApexCharts
        v-if="hasData"
        type="donut"
        :width="size"
        :height="size"
        :options="chartOptions"
        :series="series"
      />
      <div v-else class="w-full h-full rounded-full border border-dashed border-border-subtle flex items-center justify-center"></div>

      <!-- Center label -->
      <div class="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
        <div class="font-extrabold leading-none tabular-nums tracking-tight" :style="{ color: centerColor, fontSize: Math.round(size * 0.142) + 'px' }">
          {{ centerBig }}
        </div>
        <div class="font-semibold uppercase tracking-[0.14em] text-slate-400 mt-1.5" :style="{ fontSize: Math.max(9, Math.round(size * 0.044)) + 'px' }">{{ centerLabel }}</div>
        <div class="text-slate-500 mt-0.5" :style="{ fontSize: Math.max(9, Math.round(size * 0.046)) + 'px' }">{{ centerSub }}</div>
      </div>
    </div>

    <!-- Legend -->
    <div class="flex flex-wrap gap-x-3 gap-y-1.5 justify-center mt-3">
      <span v-for="s in slices" :key="s.label" class="inline-flex items-center gap-1.5 text-[11.5px] text-slate-300">
        <span class="w-2.5 h-2.5 rounded-[3px] ring-1 ring-white/5" :style="{ background: s.color }"></span>
        {{ s.label }} {{ Math.round(s.value) }}%
      </span>
    </div>
  </div>
</template>
