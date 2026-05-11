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
    <div class="size-14 rounded-2xl bg-primary/15 border border-primary/30 flex items-center justify-center">
      <span class="material-symbols-outlined text-primary text-[28px]">smart_toy</span>
    </div>
    <div>
      <p class="text-primary text-[10px] font-bold uppercase tracking-widest mb-1">Welcome</p>
      <h2 class="text-lg sm:text-xl font-bold text-slate-100 leading-tight">Pick the model that powers me</h2>
      <p class="text-xs sm:text-sm text-slate-400 mt-2 max-w-xs leading-relaxed">
        I'll use this model for every reply in this conversation. You can swap it later in settings.
      </p>
    </div>

    <div v-if="loading" class="flex items-center gap-2 text-slate-500 text-xs">
      <span class="material-symbols-outlined animate-spin text-[14px]">progress_activity</span>
      Loading models…
    </div>

    <div v-else-if="loadError" class="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-red-300 text-xs">
      {{ loadError }}
    </div>

    <div v-else-if="!hasModels" class="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-amber-200 text-xs leading-relaxed max-w-sm">
      No models configured yet. Add one on the
      <RouterLink to="/models" class="font-semibold underline">Models</RouterLink>
      page or run the
      <RouterLink to="/onboarding" class="font-semibold underline">onboarding</RouterLink>
      flow.
    </div>

    <div v-else class="w-full max-w-xs space-y-2">
      <label class="block text-[10px] font-semibold uppercase tracking-widest text-slate-500 text-left">Model</label>
      <select
        v-model="selected"
        class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary"
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
      class="inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 transition disabled:opacity-40"
    >
      <span v-if="busy" class="material-symbols-outlined text-[14px] animate-spin">progress_activity</span>
      <span v-else class="material-symbols-outlined text-[14px]">check</span>
      Start chatting
    </button>
  </div>
</template>
