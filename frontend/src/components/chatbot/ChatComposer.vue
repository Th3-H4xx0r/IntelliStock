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
  <div class="border-t border-border-subtle bg-[#0a0f13]/85 px-3 sm:px-4 pt-2.5 pb-[max(0.625rem,env(safe-area-inset-bottom))]">
    <div
      class="flex items-end gap-2 rounded-xl border border-border-subtle bg-surface/70 px-2.5 py-2 focus-within:border-primary/60 transition-colors"
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
        class="flex-1 bg-transparent resize-none outline-none border-0 text-sm text-slate-100 placeholder-slate-600 leading-snug min-h-[24px] max-h-[200px] disabled:opacity-50"
      ></textarea>
      <button
        @click="send"
        :disabled="busy || disabled || !text.trim()"
        class="shrink-0 size-9 rounded-lg bg-primary text-background-dark font-bold flex items-center justify-center hover:brightness-110 transition disabled:opacity-40 disabled:bg-surface disabled:text-slate-600 disabled:cursor-not-allowed"
        aria-label="Send"
      >
        <span v-if="busy" class="material-symbols-outlined text-[18px] animate-spin">progress_activity</span>
        <span v-else class="material-symbols-outlined text-[18px]">send</span>
      </button>
    </div>
    <p class="text-[10px] text-slate-600 mt-1 px-1.5 hidden sm:block">
      <kbd class="font-mono">Enter</kbd> to send · <kbd class="font-mono">Shift+Enter</kbd> for newline
    </p>
  </div>
</template>
