<!-- frontend/src/components/analysis/MoveEventDetail.vue
     The selected move on the AI Analysis screen: the day and its move, the
     category, the one-line cause and explanation, how much came from the
     market, the sector and the stock itself, the model's confidence, and the
     articles it cited (each opens in a new tab). Explain runs the model on an
     unexplained or failed day; Re-run replaces an explanation (force). -->
<template>
  <div class="ui-card">
    <div v-if="!event" class="ui-empty py-10">
      <p>Select a marked day on the chart or in the list to see why it moved</p>
    </div>

    <template v-else>
      <div class="px-5 pt-5">
        <div class="flex items-start justify-between gap-4">
          <div class="min-w-0">
            <p class="text-sm text-slate-300">{{ fmtEventDate(event.date, { long: true }) }}</p>
            <p class="text-xs text-slate-500 mt-1 tabular-nums">Closed at {{ fmtPrice(event.close) }}</p>
          </div>
          <p class="shrink-0 text-xl font-medium leading-none tabular-nums" :class="signClass(event.ret_pct)">{{ fmtPct(event.ret_pct) }}</p>
        </div>
        <p class="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-400">
          <span class="inline-flex items-center gap-2">
            <CategoryGlyph :category="event.category" :hollow="isHollow(event)" />
            {{ event.status === 'explained' ? (event.category_label || 'Explained') : statusText(event) }}
          </span>
          <template v-if="confidence">
            <span class="text-slate-600" aria-hidden="true">·</span>
            <span>{{ confidence }}</span>
          </template>
        </p>

        <!-- The cause -->
        <template v-if="event.status === 'explained'">
          <h3 class="mt-5 text-base font-medium text-slate-100 leading-snug">{{ event.headline || event.category_label || 'Explained' }}</h3>
          <p v-if="event.explanation" class="mt-2 text-sm text-slate-300 leading-relaxed whitespace-pre-line">{{ event.explanation }}</p>
        </template>
        <div v-else-if="event.status === 'pending'" class="mt-5 flex items-center gap-2 text-sm text-slate-300">
          <span class="material-symbols-outlined text-[16px] text-slate-500 animate-spin" aria-hidden="true">progress_activity</span>
          Reading the news for this day. This page updates when it's done
        </div>
        <div v-else-if="event.status === 'failed'" class="mt-5">
          <p class="text-sm text-slate-200">The explanation failed</p>
          <p v-if="event.error" class="mt-1 text-sm text-slate-500 break-words">{{ event.error }}</p>
        </div>
        <p v-else class="mt-5 text-sm text-slate-400">
          {{ modelConfigured === false ? 'Choose a model in Settings to find out why this day moved' : 'Not explained yet. Explain finds the cause in that day\'s news' }}
        </p>
      </div>

      <!-- Where the move came from -->
      <section class="px-5 pt-5" aria-label="Where the move came from">
        <p class="ui-label">Where the move came from</p>
        <dl class="mt-2.5 space-y-2">
          <div v-for="r in rows" :key="r.key" class="split-row">
            <dt class="text-sm text-slate-400 truncate">{{ r.label }}</dt>
            <dd class="h-1.5 rounded-full bg-white/[0.04] overflow-hidden">
              <span
                class="block h-full rounded-full"
                :class="(r.value ?? 0) >= 0 ? 'bg-gain' : 'bg-loss'"
                :style="{ width: `${Math.round(r.share * 100)}%` }"
              ></span>
            </dd>
            <dd class="text-sm text-right tabular-nums" :class="signClass(r.value)">{{ fmtPct(r.value) }}</dd>
          </div>
          <div class="split-row pt-2 border-t border-white/[0.06]">
            <dt class="text-sm text-slate-200">Total move</dt>
            <dd></dd>
            <dd class="text-sm text-right tabular-nums" :class="signClass(event.ret_pct)">{{ fmtPct(event.ret_pct) }}</dd>
          </div>
        </dl>
      </section>

      <!-- Sources -->
      <section v-if="event.status === 'explained'" class="px-5 pt-5" aria-label="Sources">
        <p class="ui-label">Sources</p>
        <p v-if="!sources.length" class="mt-2 text-sm text-slate-500">No articles cited</p>
        <ul v-else class="mt-2 space-y-2.5">
          <li v-for="(s, i) in sources" :key="i" class="min-w-0">
            <a
              v-if="s.url"
              :href="s.url"
              target="_blank"
              rel="noopener noreferrer"
              class="group inline-flex items-start gap-1.5 text-sm text-slate-200 hover:text-slate-50"
            >
              <span class="underline decoration-white/20 underline-offset-2 group-hover:decoration-white/50">{{ s.title }}</span>
              <span class="material-symbols-outlined text-[14px] text-slate-500 mt-0.5 shrink-0" aria-hidden="true">open_in_new</span>
              <span class="sr-only">(opens in a new tab)</span>
            </a>
            <span v-else class="text-sm text-slate-300">{{ s.title }}</span>
            <p v-if="s.meta" class="text-xs text-slate-500 mt-0.5 tabular-nums">{{ s.meta }}</p>
          </li>
        </ul>
      </section>

      <!-- Who explained it, and the action -->
      <div class="mt-5 px-5 py-4 border-t border-white/[0.06] flex flex-wrap items-center justify-between gap-3">
        <p class="text-xs text-slate-500 min-w-0">
          <template v-if="event.status === 'explained' && (event.model_name || event.explained_at)">
            Explained<template v-if="event.model_name"> by {{ event.model_name }}</template><template v-if="stamp"> · {{ stamp }}</template>
          </template>
          <template v-else-if="modelConfigured === false">
            No model chosen. <RouterLink to="/models#ai-analysis" class="text-slate-300 underline decoration-white/20 underline-offset-2 hover:text-slate-100">Settings</RouterLink>
          </template>
        </p>
        <button
          v-if="event.status !== 'pending'"
          type="button"
          class="ui-btn"
          :disabled="busy || modelConfigured === false"
          @click="emit('explain', event.status === 'explained')"
        >
          <span v-if="busy" class="material-symbols-outlined text-[16px] animate-spin" aria-hidden="true">progress_activity</span>
          {{ actionLabel }}
        </button>
      </div>
      <p v-if="error" class="px-5 pb-4 -mt-1 text-sm text-loss" role="alert">{{ error }}</p>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import CategoryGlyph from './CategoryGlyph.vue'
import { fmtPct, fmtPrice, signClass } from '../../utils/dashboardAnalytics.js'
import {
  confidenceText, fmtEventDate, fmtStamp, isHollow, sourceRow, splitRows, statusText,
} from '../../utils/moveAnalysis.js'

const props = defineProps({
  event: { type: Object, default: null },
  sectorEtf: { type: String, default: null },
  modelConfigured: { type: Boolean, default: null },
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
})
const emit = defineEmits(['explain'])

const rows = computed(() => splitRows(props.event, props.sectorEtf))
const sources = computed(() => (props.event?.sources || []).map(sourceRow))
const confidence = computed(() => (props.event?.status === 'explained' ? confidenceText(props.event?.confidence) : ''))
const stamp = computed(() => fmtStamp(props.event?.explained_at))
const actionLabel = computed(() => {
  if (props.busy) return 'Starting…'
  if (props.event?.status === 'explained') return 'Re-run'
  if (props.event?.status === 'failed') return 'Try again'
  return 'Explain'
})
</script>

<style scoped>
.split-row {
  display: grid;
  grid-template-columns: minmax(0, 8.5rem) minmax(0, 1fr) 4.5rem;
  align-items: center;
  column-gap: 12px;
}
</style>
