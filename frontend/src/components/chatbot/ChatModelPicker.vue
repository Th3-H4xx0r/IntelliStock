<script setup>
import { ref, computed, onMounted } from 'vue'
import { listAvailableModels } from '../../utils/chatbot.js'

const props = defineProps({
  initialModelId: { type: String, default: null },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['confirm'])

const models = ref([])
const loading = ref(true)
const loadError = ref('')
const selected = ref(props.initialModelId || '')

const hasModels = computed(() => models.value.length > 0)

onMounted(async () => {
  try {
    models.value = await listAvailableModels()
    if (!selected.value && models.value.length === 1) {
      selected.value = models.value[0].id
    }
  } catch (e) {
    loadError.value = e.message || 'Failed to load models'
  } finally {
    loading.value = false
  }
})

function pick() {
  if (!selected.value) return
  const m = models.value.find(x => x.id === selected.value)
  emit('confirm', { id: selected.value, name: m?.name || '' })
}
</script>

<template>
  <div class="flex flex-col items-center text-center gap-5 p-5 sm:p-6">
    <div>
      <h2 class="text-base font-medium text-slate-100 leading-tight">Pick a model for this conversation</h2>
      <p class="text-sm text-slate-500 mt-2 max-w-xs leading-relaxed">
        I'll use this model for every reply in this conversation. You can swap it later in settings.
      </p>
    </div>

    <div v-if="loading" class="flex items-center gap-2 text-slate-500 text-sm">
      <span class="material-symbols-outlined animate-spin text-[16px]">progress_activity</span>
      Loading models…
    </div>

    <div v-else-if="loadError" class="rounded-lg border border-loss/20 bg-loss/5 px-3 py-2 text-loss text-sm">
      {{ loadError }}
    </div>

    <div v-else-if="!hasModels" class="rounded-xl border border-white/[0.07] bg-background-dark/60 px-4 py-3 text-slate-400 text-sm leading-relaxed max-w-sm">
      No models configured yet. Add one on the
      <RouterLink to="/models" class="text-slate-100 underline underline-offset-2">Models</RouterLink>
      page or run the
      <RouterLink to="/onboarding" class="text-slate-100 underline underline-offset-2">onboarding</RouterLink>
      flow.
    </div>

    <div v-else class="w-full max-w-xs text-left">
      <label class="ui-field-label">Model</label>
      <select
        v-model="selected"
        class="ui-input"
      >
        <option value="">Choose…</option>
        <option v-for="m in models" :key="m.id" :value="m.id">
          {{ m.name }} ({{ m.provider }} · {{ m.model }})
        </option>
      </select>
    </div>

    <button
      v-if="hasModels"
      @click="pick"
      :disabled="!selected || busy"
      class="ui-btn-primary"
    >
      <span v-if="busy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
      Start chatting
    </button>
  </div>
</template>
