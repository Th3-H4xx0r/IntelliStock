<script setup>
// A small line for a price series: green if it ended up, red if down.
// Pure SVG, no chart library, so a table of them stays cheap.
import { computed } from 'vue'

const props = defineProps({
  values: { type: Array, default: () => [] },
  width: { type: Number, default: 80 },
  height: { type: Number, default: 24 },
})

const up = computed(() => {
  const v = props.values
  return v.length < 2 || v[v.length - 1] >= v[0]
})

const path = computed(() => {
  const v = (props.values || []).filter(Number.isFinite)
  if (v.length < 2) return ''
  // Never stretch less than 0.5% of the price to full height, so a near-flat
  // holding (a T-bill ETF) draws near-flat instead of as noise.
  const lo = Math.min(...v)
  const hi = Math.max(...v)
  const mid = (lo + hi) / 2
  const span = Math.max(hi - lo, Math.abs(mid) * 0.005) || 1
  const min = mid - span / 2
  const pad = 1.5
  const h = props.height - pad * 2
  const step = props.width / (v.length - 1)
  return v
    .map((y, i) => `${i ? 'L' : 'M'}${(i * step).toFixed(1)},${(pad + h - ((y - min) / span) * h).toFixed(1)}`)
    .join('')
})
</script>

<template>
  <svg
    :width="width"
    :height="height"
    :viewBox="`0 0 ${width} ${height}`"
    preserveAspectRatio="none"
    class="block overflow-visible"
    aria-hidden="true"
  >
    <path
      v-if="path"
      :d="path"
      fill="none"
      :stroke="up ? '#4cc38a' : '#e5484d'"
      stroke-width="1.5"
      stroke-linejoin="round"
      stroke-linecap="round"
      vector-effect="non-scaling-stroke"
    />
  </svg>
</template>
