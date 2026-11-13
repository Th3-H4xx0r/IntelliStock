<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  message: { type: Object, required: true },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['approve', 'decline'])

const pending = computed(() => props.message?.pending_tool || {})
const safety = computed(() => pending.value.safety || 'write')

const requiresTyped = computed(() => safety.value === 'destructive')
const typedConfirmation = ref('')
const expectedToken = computed(() => 'CONFIRM')
// Case-sensitive on purpose — destructive tools shouldn't accept "confirm"
// or pasted whitespace; deliberate typing is the whole point.
const typedOk = computed(() => !requiresTyped.value || typedConfirmation.value === expectedToken.value)

const argsPreview = computed(() => {
  try {
    return JSON.stringify(pending.value.arguments || {}, null, 2)
  } catch { return '' }
})

const labelByTier = {
  safe: 'Run tool',
  write: 'This will change your workspace',
  destructive: 'Destructive — confirm carefully',
}
// The tier shows as a dot beside its label: grey, amber, red.
const colorByTier = {
  safe:        'ui-dot-off',
  write:       'ui-dot-warn',
  destructive: 'ui-dot-error',
}
</script>

<template>
  <div
    class="rounded-xl border border-white/[0.08] bg-raised px-4 py-3.5 max-w-[88%] sm:max-w-[80%] flex flex-col gap-3"
  >
    <div class="min-w-0">
      <p class="text-xs text-slate-400 inline-flex items-center gap-1.5">
        <span class="ui-dot" :class="colorByTier[safety]"></span>
        {{ labelByTier[safety] }}
      </p>
      <p class="text-sm font-medium text-slate-100 leading-snug mt-1.5">{{ pending.name }}</p>
      <p v-if="pending.description" class="text-xs text-slate-400 mt-1 leading-relaxed">{{ pending.description }}</p>
    </div>

    <details v-if="argsPreview && argsPreview !== '{}'" class="text-xs">
      <summary class="cursor-pointer text-slate-500 hover:text-slate-300 transition-colors duration-150 ease-out">Arguments</summary>
      <pre class="mt-1.5 bg-background-dark border border-white/[0.06] rounded-md p-2 font-mono text-xs leading-snug overflow-x-auto whitespace-pre-wrap break-words text-slate-300">{{ argsPreview }}</pre>
    </details>

    <div v-if="requiresTyped">
      <label class="block text-xs text-slate-400 mb-1.5">
        Type <span class="text-slate-100">CONFIRM</span> to proceed
      </label>
      <input
        v-model="typedConfirmation"
        type="text"
        autocomplete="off"
        class="ui-input h-8 px-2.5 text-xs"
      />
    </div>

    <div class="flex flex-col-reverse sm:flex-row sm:justify-end gap-2">
      <button
        @click="emit('decline')"
        :disabled="busy"
        class="ui-btn-ghost"
      >Decline</button>
      <button
        @click="emit('approve')"
        :disabled="busy || !typedOk"
        :class="safety === 'destructive' ? 'ui-btn text-loss' : 'ui-btn-primary h-8 px-3 text-[13px]'"
      >
        <span v-if="busy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
        Approve &amp; run
      </button>
    </div>
  </div>
</template>
