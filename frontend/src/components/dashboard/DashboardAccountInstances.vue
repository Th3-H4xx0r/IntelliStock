<script setup>
// The instances that trade the selected account: who is acting on these
// holdings right now. A running one pings like the Instances page's pinned
// cards; each row opens the instance.
import { computed, toRef } from 'vue'
import { useAccountInstances } from '../../composables/useDashboardData.js'

const props = defineProps({
  brokerageId: { type: [String, Number], default: null },
})

const { instances, error } = useAccountInstances(toRef(props, 'brokerageId'))

function state(i) {
  return i?.crashed ? 'crashed' : i?.runCommand ? 'running' : 'stopped'
}
const WORD = { running: 'Running', stopped: 'Stopped', crashed: 'Crashed' }
const ORDER = { running: 0, crashed: 1, stopped: 2 }

const rows = computed(() => (instances.value || [])
  .map(i => {
    const s = state(i)
    const n = (i?.stocks || []).length
    return {
      id: String(i.id),
      name: i.name || i.id,
      sub: [i.id, (i.created_by || 'user') === 'ai' ? 'AI created' : null, n ? `${n} ${n === 1 ? 'symbol' : 'symbols'}` : null]
        .filter(Boolean).join(' · '),
      state: s,
      word: WORD[s],
    }
  })
  .sort((a, b) => ORDER[a.state] - ORDER[b.state] || a.name.localeCompare(b.name)))

const runningCount = computed(() => rows.value.filter(r => r.state === 'running').length)
const subtitle = computed(() => {
  if (instances.value == null) return ' '
  if (!rows.value.length) return 'Nothing is linked to it'
  return runningCount.value ? `${runningCount.value} running` : 'None running'
})
</script>

<template>
  <section aria-labelledby="acct-inst-title">
    <div class="flex items-end justify-between gap-4 mb-3">
      <div class="min-w-0">
        <h2 id="acct-inst-title" class="ui-section-title">Trading this account</h2>
        <p class="ui-section-sub tabular-nums">{{ subtitle }}</p>
      </div>
      <RouterLink to="/instances" class="ui-btn-ghost -mr-3 shrink-0">Manage</RouterLink>
    </div>

    <div class="ui-card overflow-hidden">
      <ul v-if="instances == null && !error" class="ui-rows" aria-hidden="true">
        <li v-for="n in 2" :key="n" class="h-14 px-5 flex items-center justify-between gap-4">
          <span class="space-y-2">
            <span class="block h-3.5 w-28 ui-skel"></span>
            <span class="block h-3 w-20 ui-skel"></span>
          </span>
          <span class="block h-3 w-14 ui-skel"></span>
        </li>
      </ul>

      <p v-else-if="instances == null" class="px-5 py-6 text-sm text-slate-400">{{ error }}</p>

      <div v-else-if="!rows.length" class="px-5 py-6">
        <p class="text-sm text-slate-300">No instance trades this account</p>
        <RouterLink to="/instances" class="mt-1 inline-block text-xs text-slate-500 hover:text-slate-300 transition-colors duration-150 ease-out">
          Link one on Instances
        </RouterLink>
      </div>

      <ul v-else class="ui-rows">
        <li v-for="r in rows" :key="r.id">
          <RouterLink
            :to="'/instances/' + encodeURIComponent(r.id)"
            class="min-h-14 px-5 py-2.5 flex items-center justify-between gap-4 transition-colors duration-150 ease-out hover:bg-white/[0.03] focus-visible:outline-none focus-visible:bg-white/[0.04]"
          >
            <span class="min-w-0">
              <span class="block text-sm truncate" :class="r.state === 'stopped' ? 'text-slate-300' : 'text-slate-100'">{{ r.name }}</span>
              <span class="block text-xs text-slate-500 mt-0.5 truncate tabular-nums">{{ r.sub }}</span>
            </span>
            <span class="shrink-0 flex items-center gap-2 text-sm">
              <span class="ai-dot" :class="`ai-dot-${r.state}`" aria-hidden="true"></span>
              <span :class="r.state === 'running' ? 'text-gain' : r.state === 'crashed' ? 'text-loss' : 'text-slate-500'">{{ r.word }}</span>
            </span>
          </RouterLink>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
/* The Instances page's live dot: two staggered rings ping out while it runs. */
.ai-dot { position: relative; display: inline-block; width: 7px; height: 7px; border-radius: 9999px; flex-shrink: 0; }
.ai-dot-stopped { background: #52525b; }
.ai-dot-crashed { background: #e5484d; }
.ai-dot-running { background: #4cc38a; box-shadow: 0 0 8px rgba(76, 195, 138, 0.55); }
.ai-dot-running::before,
.ai-dot-running::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 9999px;
  background: rgba(76, 195, 138, 0.5);
  animation: ai-ping 2.4s cubic-bezier(0, 0, 0.2, 1) infinite;
}
.ai-dot-running::after { animation-delay: 1.2s; }
@keyframes ai-ping {
  0%        { transform: scale(1);   opacity: 0.6; }
  70%, 100% { transform: scale(3.2); opacity: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .ai-dot-running::before, .ai-dot-running::after { animation: none; opacity: 0; }
}
</style>
