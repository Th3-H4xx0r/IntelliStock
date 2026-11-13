<!-- frontend/src/components/BacktestLLMPauseBanner.vue
     Diagnostic panel shown when a backtest has been auto-paused due to a
     critical LLM failure (status='paused_llm_critical'). Reads the
     diagnostic fields written by backtest_critical_abort.handle().
     Paused = amber dot and a word, like every other status in the app.
     -->
<template>
  <section
    v-if="visible"
    class="ui-card px-5 py-4"
    aria-labelledby="llm-pause-title"
  >
    <div class="flex items-center gap-2">
      <span class="ui-dot ui-dot-warn" aria-hidden="true"></span>
      <h2 id="llm-pause-title" class="text-sm font-medium text-slate-100">
        Backtest paused: LLM critical failure
      </h2>
    </div>
    <dl class="mt-3 grid grid-cols-[88px_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
      <dt class="text-slate-500">Bar</dt>
      <dd class="text-slate-200 tabular-nums">{{ formatBar(barTime) }}</dd>
      <dt class="text-slate-500">Reason</dt>
      <dd class="text-slate-200">{{ reasonTag }} <span class="text-slate-500 tabular-nums">· {{ attempts }} attempts</span></dd>
      <dt class="text-slate-500">Provider</dt>
      <dd class="text-slate-200">{{ provider }} <span class="text-slate-500">· model {{ model }}</span></dd>
      <dt class="text-slate-500">Call site</dt>
      <dd class="text-slate-200 break-all">{{ callSite }}</dd>
      <dt class="text-slate-500">Paused at</dt>
      <dd class="text-slate-200 tabular-nums">{{ formatTs(pausedAt) }}</dd>
    </dl>
    <p v-if="sample" class="mt-3 rounded-lg border border-white/[0.06] bg-background-dark px-3 py-2 font-mono text-xs leading-5 text-slate-400 break-all">
      {{ sample }}
    </p>
    <p class="mt-3 text-xs text-slate-500">
      Resume the backtest when the provider is healthy again. The same bar will retry from the snapshot.
    </p>
  </section>
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
