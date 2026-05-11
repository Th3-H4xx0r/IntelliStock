<script setup>
import { computed } from 'vue'
import ChatRichBlock from './ChatRichBlock.vue'

const props = defineProps({
  message: { type: Object, required: true },
})

const isUser = computed(() => props.message.role === 'user')
const isAssistant = computed(() => props.message.role === 'assistant')
const isTool = computed(() => props.message.role === 'tool')

const visibleBlocks = computed(() => {
  const blocks = props.message.blocks || []
  // Hide internal navigate blocks at the message level — the dock listens
  // for them via onMounted side effects and routes the user.
  return blocks.filter(b => b?.type !== 'navigate')
})

function fmtTime(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  } catch { return '' }
}
</script>

<template>
  <div
    class="flex w-full min-w-0"
    :class="isUser ? 'justify-end' : 'justify-start'"
  >
    <div
      class="max-w-[88%] sm:max-w-[80%] min-w-0 flex flex-col gap-2"
      :class="isUser ? 'items-end' : 'items-start'"
    >
      <!-- Tool call summary chip (assistant w/ tool_calls but no pending) -->
      <div
        v-if="isAssistant && message.tool_calls?.length"
        class="flex flex-wrap gap-1.5 max-w-full"
      >
        <span
          v-for="tc in message.tool_calls"
          :key="tc.id"
          class="inline-flex items-center gap-1 rounded-md bg-primary/10 border border-primary/25 px-2 py-0.5 text-[10px] uppercase tracking-wider text-primary max-w-full truncate"
        >
          <span class="material-symbols-outlined text-[11px]">build</span>
          {{ tc.name }}
        </span>
      </div>

      <!-- Bubble (text content) -->
      <div
        v-if="message.content || (!message.blocks?.length && !message.tool_calls?.length)"
        class="rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed shadow-sm max-w-full overflow-hidden"
        :class="[
          isUser
            ? 'bg-primary text-background-dark font-medium rounded-br-sm'
            : isTool
              ? 'bg-surface/40 border border-border-subtle text-slate-400 font-mono text-[11px] rounded-bl-sm'
              : 'bg-surface/70 border border-border-subtle text-slate-100 rounded-bl-sm'
        ]"
      >
        <template v-if="isAssistant">
          <ChatRichBlock :block="{ type: 'markdown', content: message.content }" />
        </template>
        <template v-else-if="isTool">
          <!-- Wrap aggressively so a long single-token JSON blob doesn't push
               the whole panel into a horizontal scroll. Cap to two lines via
               line-clamp; the LLM still sees the full payload, this view is
               just an at-a-glance audit trail. -->
          <p class="break-all chatbot-tool-clamp">
            <span class="text-slate-500">{{ message.name }} →</span>
            {{ message.content?.slice(0, 200) }}<span v-if="(message.content || '').length > 200">…</span>
          </p>
        </template>
        <template v-else>
          <p class="whitespace-pre-wrap break-words">{{ message.content }}</p>
        </template>
      </div>

      <!-- Rich blocks (chart / table / stat) — must clamp to bubble width and
           contain their own horizontal overflow so a wide table never escapes
           the message column. -->
      <div
        v-for="(b, idx) in visibleBlocks"
        :key="message.id + ':b' + idx"
        class="w-full max-w-full overflow-hidden"
      >
        <ChatRichBlock :block="b" />
      </div>

      <p
        v-if="message.created_at && !isTool"
        class="text-[10px] text-slate-600 px-1"
      >{{ fmtTime(message.created_at) }}</p>
    </div>
  </div>
</template>

<style scoped>
.chatbot-tool-clamp {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
