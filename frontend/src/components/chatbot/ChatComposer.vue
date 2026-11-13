<script setup>
import { ref, nextTick, watch } from 'vue'

const props = defineProps({
  busy: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: 'Ask me anything…' },
})
const emit = defineEmits(['send'])

const text = ref('')
const textareaEl = ref(null)

function autosize() {
  const el = textareaEl.value
  if (!el) return
  el.style.height = 'auto'
  // Cap at ~7 rows; scroll after.
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

watch(text, () => nextTick(autosize))

function send() {
  const t = text.value.trim()
  if (!t || props.busy || props.disabled) return
  emit('send', t)
  text.value = ''
  nextTick(autosize)
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    send()
  }
}

defineExpose({ focus: () => textareaEl.value?.focus() })
</script>

<template>
  <div class="border-t border-white/[0.06] bg-surface px-3 sm:px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
    <div
      class="flex items-end gap-2 rounded-xl border border-white/[0.08] bg-background-dark pl-3 pr-1.5 py-1.5 focus-within:border-white/25 transition-colors duration-150 ease-out"
    >
      <textarea
        ref="textareaEl"
        v-model="text"
        @keydown="onKeydown"
        @input="autosize"
        :placeholder="placeholder"
        :disabled="disabled || busy"
        rows="1"
        aria-label="Message"
        class="flex-1 self-center bg-transparent resize-none outline-none border-0 py-1 text-sm text-slate-100 placeholder-slate-600 leading-snug min-h-[24px] max-h-[200px] disabled:opacity-50"
      ></textarea>
      <button
        @click="send"
        :disabled="busy || disabled || !text.trim()"
        class="shrink-0 size-8 rounded-lg bg-primary text-background-dark flex items-center justify-center hover:bg-white transition-colors duration-150 ease-out disabled:bg-raised disabled:text-slate-600 disabled:cursor-default"
        aria-label="Send"
      >
        <span v-if="busy" class="material-symbols-outlined text-[18px] animate-spin">progress_activity</span>
        <span v-else class="material-symbols-outlined text-[18px]">arrow_upward</span>
      </button>
    </div>
    <p class="text-xs text-slate-600 mt-1.5 px-1 hidden sm:block">
      <kbd class="font-sans">Enter</kbd> to send · <kbd class="font-sans">Shift+Enter</kbd> for a new line
    </p>
  </div>
</template>
