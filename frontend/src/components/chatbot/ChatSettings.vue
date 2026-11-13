<script setup>
import { ref, computed, onMounted } from 'vue'
import { listAvailableModels } from '../../utils/chatbot.js'

const props = defineProps({
  conversation: { type: Object, default: null },
  toolCatalog: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits([
  'close',
  'changeModel',
  'toggleAutoConfirm',
  'newConversation',
  'deleteConversation',
])

const models = ref([])
const loading = ref(true)

const selectedModel = ref(props.conversation?.model_id || '')
const autoConfirm = computed({
  get: () => !!(props.conversation?.settings?.auto_confirm_safe_tools ?? true),
  set: (v) => emit('toggleAutoConfirm', v),
})

const safeTools = computed(() => props.toolCatalog.filter(t => t.safety === 'safe' && !t.render_only))
const writeTools = computed(() => props.toolCatalog.filter(t => t.safety === 'write'))
const destructiveTools = computed(() => props.toolCatalog.filter(t => t.safety === 'destructive'))

onMounted(async () => {
  try {
    models.value = await listAvailableModels()
  } finally {
    loading.value = false
  }
})

function applyModel() {
  if (!selectedModel.value || selectedModel.value === props.conversation?.model_id) return
  const m = models.value.find(x => x.id === selectedModel.value)
  emit('changeModel', { id: selectedModel.value, name: m?.name || '' })
}
</script>

<template>
  <div class="absolute inset-0 z-10 bg-surface flex flex-col">
    <div class="h-12 flex items-center justify-between pl-4 pr-2 border-b border-white/[0.06] shrink-0">
      <p class="text-sm font-medium text-slate-100">Chatbot settings</p>
      <button
        @click="$emit('close')"
        class="ui-icon-btn"
        aria-label="Close settings"
      >
        <span class="material-symbols-outlined text-[18px]">close</span>
      </button>
    </div>

    <div class="flex-1 overflow-y-auto px-4 py-5 space-y-8 text-sm">
      <!-- Model -->
      <section>
        <p class="text-xs text-slate-500 mb-2">Model</p>
        <div v-if="loading" class="text-sm text-slate-500">Loading…</div>
        <div v-else-if="!models.length" class="text-sm text-slate-400">
          No models configured.
          <RouterLink to="/models" class="text-slate-100 underline underline-offset-2">Add one</RouterLink>.
        </div>
        <div v-else class="space-y-2">
          <select
            v-model="selectedModel"
            class="ui-input"
          >
            <option value="">Choose…</option>
            <option v-for="m in models" :key="m.id" :value="m.id">
              {{ m.name }} ({{ m.provider }} · {{ m.model }})
            </option>
          </select>
          <button
            @click="applyModel"
            :disabled="busy || !selectedModel || selectedModel === conversation?.model_id"
            class="ui-btn"
          >
            Apply model
          </button>
        </div>
      </section>

      <!-- Tool confirmation policy -->
      <section>
        <p class="text-xs text-slate-500 mb-2">Tool execution</p>
        <label class="flex items-start gap-3 cursor-pointer">
          <button
            type="button"
            @click="autoConfirm = !autoConfirm"
            role="switch"
            :aria-checked="autoConfirm"
            aria-label="Auto-run safe tools"
            class="relative inline-flex items-center h-5 w-9 shrink-0 mt-0.5 rounded-full border transition-colors duration-150 ease-out"
            :class="autoConfirm ? 'bg-slate-200 border-slate-200' : 'bg-raised border-white/[0.08]'"
          >
            <span
              class="inline-block size-3.5 rounded-full transition-transform duration-150 ease-out"
              :class="autoConfirm ? 'translate-x-[18px] bg-background-dark' : 'translate-x-0.5 bg-slate-500'"
            ></span>
          </button>
          <div class="min-w-0">
            <p class="text-sm text-slate-200">Auto-run safe (read-only) tools</p>
            <p class="text-xs text-slate-500 mt-0.5 leading-relaxed">
              When on, the assistant can call read-only tools like <code class="font-mono text-slate-300">list_instances</code> without asking. Mutating tools always require approval.
            </p>
          </div>
        </label>
      </section>

      <!-- Conversation actions -->
      <section>
        <p class="text-xs text-slate-500 mb-2">Conversation</p>
        <div class="grid grid-cols-2 gap-2">
          <button
            @click="$emit('newConversation')"
            class="ui-btn"
          >
            New conversation
          </button>
          <button
            @click="$emit('deleteConversation')"
            class="ui-btn text-loss"
          >
            Delete
          </button>
        </div>
      </section>

      <!-- Tool catalog -->
      <section>
        <p class="text-xs text-slate-500 mb-2">Tools the assistant can use</p>
        <div class="rounded-lg border border-white/[0.07] divide-y divide-white/[0.05]">
          <details open>
            <summary class="h-10 px-3 cursor-pointer text-sm text-slate-200 flex items-center gap-2 select-none">
              <span class="ui-dot ui-dot-off"></span>
              Read-only
              <span class="text-slate-500 tabular-nums">{{ safeTools.length }}</span>
            </summary>
            <ul class="px-3 pb-3 space-y-1.5 text-xs text-slate-400">
              <li v-for="t in safeTools" :key="t.name">
                <code class="font-mono text-slate-300">{{ t.name }}</code> — {{ t.description }}
              </li>
            </ul>
          </details>
          <details>
            <summary class="h-10 px-3 cursor-pointer text-sm text-slate-200 flex items-center gap-2 select-none">
              <span class="ui-dot ui-dot-warn"></span>
              Confirm to run
              <span class="text-slate-500 tabular-nums">{{ writeTools.length }}</span>
            </summary>
            <ul class="px-3 pb-3 space-y-1.5 text-xs text-slate-400">
              <li v-for="t in writeTools" :key="t.name">
                <code class="font-mono text-slate-300">{{ t.name }}</code> — {{ t.description }}
              </li>
            </ul>
          </details>
          <details>
            <summary class="h-10 px-3 cursor-pointer text-sm text-slate-200 flex items-center gap-2 select-none">
              <span class="ui-dot ui-dot-error"></span>
              Destructive
              <span class="text-slate-500 tabular-nums">{{ destructiveTools.length }}</span>
            </summary>
            <ul class="px-3 pb-3 space-y-1.5 text-xs text-slate-400">
              <li v-for="t in destructiveTools" :key="t.name">
                <code class="font-mono text-slate-300">{{ t.name }}</code> — {{ t.description }}
              </li>
            </ul>
          </details>
        </div>
      </section>
    </div>
  </div>
</template>
