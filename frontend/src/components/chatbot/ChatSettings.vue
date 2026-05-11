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
  <div class="absolute inset-0 z-10 bg-[#0a0f13]/95 backdrop-blur-md flex flex-col">
    <div class="flex items-center justify-between px-4 py-3 border-b border-border-subtle">
      <p class="text-sm font-semibold text-slate-100">Chatbot settings</p>
      <button
        @click="$emit('close')"
        class="size-8 rounded-md flex items-center justify-center text-slate-400 hover:text-slate-100"
        aria-label="Close settings"
      >
        <span class="material-symbols-outlined text-[18px]">close</span>
      </button>
    </div>

    <div class="flex-1 overflow-y-auto px-4 py-4 space-y-6 text-sm">
      <!-- Model -->
      <section class="space-y-2">
        <p class="text-[10px] font-semibold uppercase tracking-widest text-slate-500">Model</p>
        <div v-if="loading" class="text-xs text-slate-500">Loading…</div>
        <div v-else-if="!models.length" class="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-amber-200 text-xs">
          No models configured.
          <RouterLink to="/models" class="underline font-semibold">Add one</RouterLink>.
        </div>
        <div v-else class="space-y-2">
          <select
            v-model="selectedModel"
            class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-primary"
          >
            <option value="">Choose…</option>
            <option v-for="m in models" :key="m.id" :value="m.id">
              {{ m.name }} ({{ m.provider }} · {{ m.model }})
            </option>
          </select>
          <button
            @click="applyModel"
            :disabled="busy || !selectedModel || selectedModel === conversation?.model_id"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary/90 text-background-dark text-xs font-bold hover:brightness-110 transition disabled:opacity-40"
          >
            <span class="material-symbols-outlined text-[14px]">save</span>
            Apply model
          </button>
        </div>
      </section>

      <!-- Tool confirmation policy -->
      <section class="space-y-2">
        <p class="text-[10px] font-semibold uppercase tracking-widest text-slate-500">Tool execution</p>
        <label class="flex items-start gap-3 cursor-pointer">
          <button
            type="button"
            @click="autoConfirm = !autoConfirm"
            class="relative inline-flex h-5 w-9 shrink-0 mt-0.5 rounded-full border-2 transition-colors"
            :class="autoConfirm ? 'bg-primary border-primary' : 'bg-surface border-border-subtle'"
          >
            <span
              class="inline-block size-3.5 rounded-full bg-white shadow transition-transform"
              :class="autoConfirm ? 'translate-x-4' : 'translate-x-0.5'"
            ></span>
          </button>
          <div class="min-w-0">
            <p class="text-sm text-slate-200">Auto-run safe (read-only) tools</p>
            <p class="text-[11px] text-slate-500 mt-0.5 leading-relaxed">
              When ON, the assistant can call read-only tools like <code class="font-mono">list_instances</code> without asking. Mutating tools always require approval.
            </p>
          </div>
        </label>
      </section>

      <!-- Conversation actions -->
      <section class="space-y-2">
        <p class="text-[10px] font-semibold uppercase tracking-widest text-slate-500">Conversation</p>
        <div class="grid grid-cols-2 gap-2">
          <button
            @click="$emit('newConversation')"
            class="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border border-border-subtle text-slate-300 hover:text-slate-100 hover:bg-surface text-xs transition"
          >
            <span class="material-symbols-outlined text-[14px]">add</span>
            New conversation
          </button>
          <button
            @click="$emit('deleteConversation')"
            class="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border border-red-500/30 text-red-300 hover:bg-red-500/10 text-xs transition"
          >
            <span class="material-symbols-outlined text-[14px]">delete</span>
            Delete
          </button>
        </div>
      </section>

      <!-- Tool catalog -->
      <section class="space-y-3">
        <p class="text-[10px] font-semibold uppercase tracking-widest text-slate-500">Tools the assistant can use</p>
        <details open>
          <summary class="cursor-pointer text-xs text-emerald-400 font-semibold flex items-center gap-1.5">
            <span class="material-symbols-outlined text-[14px]">visibility</span>
            Read-only ({{ safeTools.length }})
          </summary>
          <ul class="mt-1.5 space-y-1 pl-5 text-[11px] text-slate-400">
            <li v-for="t in safeTools" :key="t.name">
              <code class="font-mono text-slate-300">{{ t.name }}</code> — {{ t.description }}
            </li>
          </ul>
        </details>
        <details>
          <summary class="cursor-pointer text-xs text-amber-400 font-semibold flex items-center gap-1.5">
            <span class="material-symbols-outlined text-[14px]">edit</span>
            Confirm to run ({{ writeTools.length }})
          </summary>
          <ul class="mt-1.5 space-y-1 pl-5 text-[11px] text-slate-400">
            <li v-for="t in writeTools" :key="t.name">
              <code class="font-mono text-slate-300">{{ t.name }}</code> — {{ t.description }}
            </li>
          </ul>
        </details>
        <details>
          <summary class="cursor-pointer text-xs text-red-400 font-semibold flex items-center gap-1.5">
            <span class="material-symbols-outlined text-[14px]">warning</span>
            Destructive ({{ destructiveTools.length }})
          </summary>
          <ul class="mt-1.5 space-y-1 pl-5 text-[11px] text-slate-400">
            <li v-for="t in destructiveTools" :key="t.name">
              <code class="font-mono text-slate-300">{{ t.name }}</code> — {{ t.description }}
            </li>
          </ul>
        </details>
      </section>
    </div>
  </div>
</template>
