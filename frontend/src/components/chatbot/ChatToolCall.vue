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
  destructive: 'DESTRUCTIVE — confirm carefully',
}
const colorByTier = {
  safe:        'border-primary/30 bg-primary/5 text-primary',
  write:       'border-amber-500/30 bg-amber-500/5 text-amber-300',
  destructive: 'border-red-500/40 bg-red-500/10 text-red-300',
}
</script>

<template>
  <div
    class="rounded-2xl border px-3.5 py-3 sm:px-4 sm:py-3.5 max-w-[88%] sm:max-w-[80%] flex flex-col gap-3 shadow-sm"
    :class="colorByTier[safety]"
  >
    <div class="flex items-start gap-2">
      <span class="material-symbols-outlined text-[18px] mt-0.5">
        {{ safety === 'destructive' ? 'warning' : 'play_circle' }}
      </span>
      <div class="min-w-0">
        <p class="text-[10px] uppercase tracking-widest font-semibold opacity-70">{{ labelByTier[safety] }}</p>
        <p class="text-sm font-semibold leading-snug">{{ pending.name }}</p>
        <p v-if="pending.description" class="text-xs opacity-80 mt-1 leading-relaxed">{{ pending.description }}</p>
      </div>
    </div>

    <details v-if="argsPreview && argsPreview !== '{}'" class="text-[11px]">
      <summary class="cursor-pointer opacity-70 hover:opacity-100 transition">Arguments</summary>
      <pre class="mt-1 bg-black/30 rounded-md p-2 font-mono text-[10px] leading-snug overflow-x-auto whitespace-pre-wrap break-words text-slate-300">{{ argsPreview }}</pre>
    </details>

    <div v-if="requiresTyped" class="space-y-1">
      <label class="block text-[11px] font-semibold uppercase tracking-widest opacity-80">
        Type <span class="font-mono">CONFIRM</span> to proceed
      </label>
      <input
        v-model="typedConfirmation"
        type="text"
        autocomplete="off"
        class="w-full bg-black/40 border border-current/30 rounded-md px-2.5 py-1.5 text-xs font-mono text-slate-100 focus:outline-none focus:border-current"
      />
    </div>

    <div class="flex flex-col-reverse sm:flex-row sm:justify-end gap-2 pt-1">
      <button
        @click="emit('decline')"
        :disabled="busy"
        class="px-3 py-1.5 rounded-md text-xs font-semibold border border-current/30 hover:bg-current/10 transition disabled:opacity-40"
      >Decline</button>
      <button
        @click="emit('approve')"
        :disabled="busy || !typedOk"
        class="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-bold bg-current/15 border border-current/40 hover:bg-current/25 transition disabled:opacity-40"
      >
        <span v-if="busy" class="material-symbols-outlined text-[14px] animate-spin">progress_activity</span>
        <span v-else class="material-symbols-outlined text-[14px]">check</span>
        Approve &amp; run
      </button>
    </div>
  </div>
</template>
