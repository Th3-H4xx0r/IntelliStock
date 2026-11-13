<script setup>
// A running instance's uptime that ticks every second: `up 3h 12m 08s`.
// It keeps its own one-second timer, so only this text re-renders, not the
// page around it. `seconds` was measured at `at` (epoch ms).
import { ref, computed, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  seconds: { type: Number, required: true },
  at: { type: Number, required: true },
})

const now = ref(Date.now())
let timer = null
onMounted(() => { timer = setInterval(() => { now.value = Date.now() }, 1000) })
onUnmounted(() => clearInterval(timer))

const pad = n => String(n).padStart(2, '0')

const label = computed(() => {
  const s = Math.max(0, Math.floor(props.seconds + (now.value - props.at) / 1000))
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (d > 0) return `up ${d}d ${h}h ${pad(m)}m ${pad(sec)}s`
  if (h > 0) return `up ${h}h ${pad(m)}m ${pad(sec)}s`
  if (m > 0) return `up ${m}m ${pad(sec)}s`
  return `up ${sec}s`
})
</script>

<template>
  <span class="tabular-nums">{{ label }}</span>
</template>
