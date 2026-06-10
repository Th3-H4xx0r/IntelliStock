<!-- frontend/src/components/BacktestLLMPauseBanner.vue
     Yellow diagnostic banner shown when a backtest has been auto-paused
     due to a critical LLM failure (status='paused_llm_critical'). Reads
     the diagnostic fields written by backtest_critical_abort.handle().
     -->
<template>
  <div
    v-if="visible"
    class="border border-amber-500/30 bg-amber-500/5 rounded-lg p-4 mb-4"
  >
    <div class="flex items-start gap-3">
      <span class="material-symbols-outlined text-amber-300 text-xl mt-0.5">pause_circle</span>
      <div class="flex-1 min-w-0">
        <div class="text-amber-300 font-semibold mb-2">
          Backtest paused: LLM critical failure
        </div>
        <div class="text-amber-100/80 text-sm space-y-1">
          <div><span class="text-amber-300/70 w-24 inline-block">Bar:</span> {{ formatBar(barTime) }}</div>
          <div><span class="text-amber-300/70 w-24 inline-block">Reason:</span> {{ reasonTag }} <span class="text-amber-100/50">({{ attempts }} attempts)</span></div>
          <div><span class="text-amber-300/70 w-24 inline-block">Provider:</span> {{ provider }} <span class="text-amber-100/50">&bull;  Model: {{ model }}</span></div>
          <div><span class="text-amber-300/70 w-24 inline-block">Call site:</span> {{ callSite }}</div>
          <div><span class="text-amber-300/70 w-24 inline-block">Paused at:</span> {{ formatTs(pausedAt) }}</div>
        </div>
        <div v-if="sample" class="mt-3 bg-black/20 border border-amber-500/20 rounded p-2 font-mono text-xs text-amber-100/70 break-all">
          {{ sample }}
        </div>
        <div class="text-amber-100/60 text-xs mt-3">
          Click <span class="font-semibold">"Resume Backtest"</span> above when the provider is healthy again. The same bar will retry from the snapshot.
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  summary: { type: Object, default: () => ({}) },
})

const visible = computed(() =>
  (props.summary?.status || '').toLowerCase() === 'paused_llm_critical'
)
const reasonTag = computed(() => props.summary?.pause_reason_tag || 'unknown')
const provider = computed(() => props.summary?.pause_provider || '?')
const model = computed(() => props.summary?.pause_model || '?')
const callSite = computed(() => props.summary?.pause_call_site || 'unknown')
const attempts = computed(() => props.summary?.pause_attempts ?? '?')
const barTime = computed(() => props.summary?.pause_bar_time || '')
const pausedAt = computed(() => props.summary?.paused_at || '')
const sample = computed(() => props.summary?.pause_sample || '')

function formatBar(s) {
  if (!s) return 'unknown'
  return String(s).slice(0, 10) // YYYY-MM-DD
}
function formatTs(s) {
  if (!s) return 'unknown'
  try {
    return new Date(s).toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
  } catch { return String(s) }
}
</script>
