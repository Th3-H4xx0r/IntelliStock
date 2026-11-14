<!-- frontend/src/components/analysis/AnalysisSettingsCard.vue
     The "AI Analysis" settings section (on Settings → Models): which saved
     model explains moves, and the two thresholds that decide which days are
     flagged. GET /analysis/settings fills it, PUT /analysis/settings saves
     it. The model list comes from the same payload; it is re-read when the
     Models table changes (`modelsVersion`). Linked as /models#ai-analysis. -->
<template>
  <section id="ai-analysis" ref="sectionEl" class="scroll-mt-20" aria-labelledby="ai-analysis-title">
    <div class="mb-3">
      <h2 id="ai-analysis-title" class="ui-section-title">AI Analysis</h2>
      <p class="ui-section-sub">The model that explains why a stock moved, and when a day counts as unusual</p>
    </div>

    <div class="ui-card">
      <div v-if="loading" class="p-5 grid grid-cols-1 sm:grid-cols-3 gap-5" aria-hidden="true">
        <div v-for="k in 3" :key="k" class="space-y-2">
          <div class="ui-skel h-3.5 w-24"></div>
          <div class="ui-skel h-9 w-full rounded-lg"></div>
        </div>
      </div>

      <div v-else-if="loadError" class="px-5 py-4 flex items-center justify-between gap-4">
        <p class="text-sm text-slate-400 min-w-0">{{ loadError }}</p>
        <button v-if="loadRetry" type="button" class="ui-btn shrink-0" @click="load()">Retry</button>
      </div>

      <form v-else @submit.prevent="save">
        <div class="p-5 grid grid-cols-1 sm:grid-cols-3 gap-x-5 gap-y-5">
          <div class="min-w-0">
            <label :for="`${uid}-model`" class="ui-field-label">Model</label>
            <select :id="`${uid}-model`" v-model="draft.model_id" class="ui-input" :disabled="saving">
              <option value="">None</option>
              <option v-for="m in modelOptions" :key="m.id" :value="m.id">{{ m.label }}</option>
            </select>
            <p class="ui-help">
              <template v-if="!models.length">No saved models yet. Add one above</template>
              <template v-else>It reads only the articles it is given and must cite them</template>
            </p>
          </div>

          <div class="min-w-0">
            <label :for="`${uid}-sigma`" class="ui-field-label">Unusual move (standard deviations)</label>
            <input
              :id="`${uid}-sigma`"
              v-model.number="draft.sigma"
              type="number"
              inputmode="decimal"
              step="0.1"
              :min="SIGMA_LIMITS.min"
              :max="SIGMA_LIMITS.max"
              class="ui-input tabular-nums"
              :class="issues.sigma ? 'border-loss/50' : ''"
              :aria-invalid="!!issues.sigma"
              :disabled="saving"
            />
            <p v-if="issues.sigma" class="ui-help text-loss">{{ issues.sigma }}</p>
            <p v-else class="ui-help">Flag a day when the stock's own move is this far from normal. 1.0 to 6.0</p>
          </div>

          <div class="min-w-0">
            <label :for="`${uid}-min`" class="ui-field-label">Smallest move to flag (%)</label>
            <input
              :id="`${uid}-min`"
              v-model.number="draft.min_move_pct"
              type="number"
              inputmode="decimal"
              step="0.5"
              :min="MIN_MOVE_LIMITS.min"
              :max="MIN_MOVE_LIMITS.max"
              class="ui-input tabular-nums"
              :class="issues.min_move_pct ? 'border-loss/50' : ''"
              :aria-invalid="!!issues.min_move_pct"
              :disabled="saving"
            />
            <p v-if="issues.min_move_pct" class="ui-help text-loss">{{ issues.min_move_pct }}</p>
            <p v-else class="ui-help">A move of twice this is always flagged. 0.5 to 25</p>
          </div>
        </div>

        <div class="px-5 py-4 border-t border-white/[0.06] flex flex-wrap items-center justify-end gap-3">
          <p v-if="message" class="mr-auto text-sm" :class="messageOk ? 'text-gain' : 'text-loss'" role="status">{{ message }}</p>
          <button type="button" class="ui-btn-ghost" :disabled="saving || !dirty" @click="reset">Reset</button>
          <button type="submit" class="ui-btn-primary" :disabled="saving || !dirty || invalid">
            <span v-if="saving" class="material-symbols-outlined text-[16px] animate-spin" aria-hidden="true">progress_activity</span>
            {{ saving ? 'Saving…' : 'Save' }}
          </button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getAnalysisSettings, saveAnalysisSettings } from '../../composables/useAnalysisApi.js'
import { getLlmProviderLabel } from '../../utils/strategyConfig.js'
import {
  MIN_MOVE_LIMITS, SIGMA_LIMITS, apiFailure, retryable, settingsChanges, settingsDirty, settingsDraft, settingsIssues,
} from '../../utils/moveAnalysis.js'

const props = defineProps({
  // Bumped by the Models page after its list changes, so the dropdown follows.
  modelsVersion: { type: Number, default: 0 },
})

const uid = `ai-analysis-${Math.random().toString(36).slice(2, 8)}`
const loading = ref(true)
const loadError = ref('')
const loadRetry = ref(true)
const models = ref([])
const savedModelName = ref('')
const saved = ref(null)            // the last saved draft
const draft = ref(settingsDraft(null))
const saving = ref(false)
const message = ref('')
const messageOk = ref(false)

const issues = computed(() => settingsIssues(draft.value))
const invalid = computed(() => !!(issues.value.sigma || issues.value.min_move_pct))
const dirty = computed(() => settingsDirty(draft.value, saved.value))

const modelOptions = computed(() => {
  const list = models.value.map(m => ({
    id: String(m.id),
    label: [m.name || m.model || String(m.id), m.provider ? getLlmProviderLabel(m.provider) : ''].filter(Boolean).join(' · '),
  }))
  // A saved choice that is no longer in the registry still shows, so the
  // select never silently reads "None".
  const current = saved.value?.model_id
  if (current && !list.some(o => o.id === current)) {
    list.push({ id: current, label: `${savedModelName.value || current} (no longer saved)` })
  }
  return list
})

function apply(body, { keepDraft = false } = {}) {
  models.value = Array.isArray(body?.models) ? body.models.filter(m => m?.id != null) : []
  savedModelName.value = String(body?.model_name || '')
  const next = settingsDraft(body)
  const wasDirty = settingsDirty(draft.value, saved.value)
  saved.value = next
  if (!keepDraft || !wasDirty) draft.value = { ...next }
}

async function load({ quiet = false } = {}) {
  if (!quiet) {
    loading.value = true
    loadError.value = ''
  }
  const res = await getAnalysisSettings()
  if (res.ok) {
    apply(res.body, { keepDraft: quiet })
    loadError.value = ''
  } else if (!quiet) {
    loadError.value = apiFailure(res.status, res.body, "Couldn't load AI Analysis settings")
    loadRetry.value = retryable(res.status, res.body)
  }
  loading.value = false
}

function reset() {
  if (saved.value) draft.value = { ...saved.value }
  message.value = ''
}

async function save() {
  if (saving.value || invalid.value || !dirty.value) return
  saving.value = true
  message.value = ''
  // Only what changed: an absent field is left alone on the server, so this
  // save can't undo a model or threshold changed on the phone meanwhile.
  const res = await saveAnalysisSettings(settingsChanges(draft.value, saved.value))
  saving.value = false
  if (res.ok) {
    apply(res.body)
    messageOk.value = true
    message.value = 'Saved'
  } else {
    messageOk.value = false
    message.value = apiFailure(res.status, res.body, "Couldn't save")
  }
}

// "Saved" stays until the next edit.
watch(dirty, d => { if (d && messageOk.value) message.value = '' })
watch(() => props.modelsVersion, () => { if (!loading.value) load({ quiet: true }) })

// The router has no scrollBehavior, so honour /models#ai-analysis here.
const route = useRoute()
const sectionEl = ref(null)
onMounted(async () => {
  await load()
  if (route.hash === '#ai-analysis') {
    await nextTick()
    sectionEl.value?.scrollIntoView({ block: 'start' })
  }
})
</script>
