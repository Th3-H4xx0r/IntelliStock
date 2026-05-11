<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'

import ChatHeader from './chatbot/ChatHeader.vue'
import ChatMessage from './chatbot/ChatMessage.vue'
import ChatComposer from './chatbot/ChatComposer.vue'
import ChatToolCall from './chatbot/ChatToolCall.vue'
import ChatModelPicker from './chatbot/ChatModelPicker.vue'
import ChatSettings from './chatbot/ChatSettings.vue'

import {
  isOpen, isMinimised, showSettings,
  conversations, currentConversation, currentId, sending,
  errorMessage, toolCatalog, lastModelId,
  messages, pendingMessage, needsModel,
  openChatbot, minimiseChatbot, closeChatbot, toggleChatbot,
  refreshConversations, loadConversation, startNewConversation,
  setConversationModel, setAutoConfirmSafe, clearCurrent,
  deleteCurrent, sendMessage, approveTool, loadToolCatalog,
  clearError,
} from '../composables/useChatbot.js'
import { isAuthenticated, getToken } from '../utils/auth.js'

const router = useRouter()

const composerRef = ref(null)
const messagesEl = ref(null)
const isMobile = ref(false)
const conversationListOpen = ref(false)
// Fullscreen on desktop maximises the panel to the whole viewport instead
// of the 420×640 floating box. Mobile is already full-bleed so the toggle
// is hidden there. Persisted in sessionStorage so a refresh keeps mode.
const isFullscreen = ref(false)
try {
  isFullscreen.value = sessionStorage.getItem('intellistock_chatbot_fullscreen') === '1'
} catch { /* ignore */ }
function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value
  try { sessionStorage.setItem('intellistock_chatbot_fullscreen', isFullscreen.value ? '1' : '0') } catch { /* ignore */ }
}

// Track which navigate-blocks we've already actioned, so route hops don't repeat.
// Cleared when the active conversation changes so the Set never grows
// unboundedly across long sessions.
const handledNavigates = new Set()
watch(currentId, () => { handledNavigates.clear() })

const title = computed(() => currentConversation.value?.title || 'Assistant')
const modelName = computed(() => currentConversation.value?.model_name || '')

const ready = computed(() => !!currentConversation.value && !needsModel.value)

const expanded = computed(() => isOpen.value && !isMinimised.value)

function onResize() {
  // Match the AppShell breakpoint (`lg` = 1024px) so the dock layout flips
  // at the same width the sidebar drawer kicks in. Avoids the "tablet shows
  // a tiny floating panel while the rest of the chrome is in mobile mode"
  // mismatch.
  isMobile.value = typeof window !== 'undefined' && window.innerWidth < 1024
}

async function bootstrap() {
  if (!isAuthenticated()) return
  await refreshConversations()
  if (currentId.value) {
    try { await loadConversation(currentId.value) } catch { /* ignore */ }
  }
  loadToolCatalog()
}

async function ensureConversation() {
  if (currentConversation.value) return
  // If a recent one exists, prefer it; otherwise create new.
  if (conversations.value.length) {
    await loadConversation(conversations.value[0].id)
  } else {
    await startNewConversation({ modelId: lastModelId.value })
  }
}

async function onSend(text) {
  await ensureConversation()
  await sendMessage(text)
  await nextTick()
  scrollToBottom()
}

async function onModelPicked({ id, name }) {
  if (!currentConversation.value) {
    await startNewConversation({ modelId: id, title: null })
  }
  await setConversationModel(id, name)
}

async function onApproveTool() {
  const msg = pendingMessage.value
  if (!msg) return
  await approveTool(msg.id, true)
  await nextTick()
  scrollToBottom()
}

async function onDeclineTool() {
  const msg = pendingMessage.value
  if (!msg) return
  await approveTool(msg.id, false)
  await nextTick()
  scrollToBottom()
}

async function onClear() {
  if (!currentConversation.value) return
  if (!confirm('Clear all messages in this conversation?')) return
  await clearCurrent()
}

async function onDeleteConversation() {
  if (!currentConversation.value) return
  if (!confirm('Delete this conversation? This cannot be undone.')) return
  await deleteCurrent()
  showSettings.value = false
}

async function onNewConversation() {
  await startNewConversation({ modelId: lastModelId.value })
  showSettings.value = false
}

function scrollToBottom() {
  const el = messagesEl.value
  if (!el) return
  el.scrollTop = el.scrollHeight
}

// Catch navigate blocks emitted by the bot and apply them.
watch(messages, (list) => {
  for (const msg of list) {
    for (const b of (msg.blocks || [])) {
      if (b?.type === 'navigate' && b.route && !handledNavigates.has(msg.id + ':' + b.route)) {
        handledNavigates.add(msg.id + ':' + b.route)
        // Defer to avoid mutating router during watch flush
        Promise.resolve().then(() => {
          try { router.push(b.route) } catch { /* ignore */ }
        })
      }
    }
  }
}, { deep: true })

watch(messages, () => nextTick(scrollToBottom))
watch(expanded, (v) => {
  if (v) nextTick(scrollToBottom)
})

// Re-bootstrap whenever the JWT becomes present (login flow may not remount
// AppShell, so onMounted alone misses post-login transitions).
let lastToken = getToken()
let authPollTimer = null
function pollAuth() {
  const t = getToken()
  if (t && t !== lastToken) {
    lastToken = t
    bootstrap()
  } else if (!t && lastToken) {
    // Logout: state was already cleared by clearSession via resetChatbot().
    lastToken = null
  }
}

onMounted(() => {
  onResize()
  window.addEventListener('resize', onResize)
  bootstrap()
  authPollTimer = window.setInterval(pollAuth, 1500)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (authPollTimer) window.clearInterval(authPollTimer)
})

defineExpose({ open: openChatbot, close: closeChatbot, toggle: toggleChatbot })
</script>

<template>
  <div v-if="isAuthenticated()">
    <!-- Collapsed bubble -->
    <Transition name="dock-bubble">
      <button
        v-if="!expanded"
        @click="openChatbot"
        class="fixed z-[45] right-4 bottom-[max(1rem,env(safe-area-inset-bottom))] sm:right-6 sm:bottom-6 size-14 rounded-full bg-primary text-background-dark shadow-2xl flex items-center justify-center hover:brightness-110 transition-all chatbot-bubble"
        aria-label="Open assistant"
      >
        <span class="material-symbols-outlined text-[26px]">smart_toy</span>
      </button>
    </Transition>

    <!-- Expanded panel -->
    <Transition name="dock-panel">
      <div
        v-if="expanded"
        class="fixed z-[45] chatbot-panel"
        :class="[
          isMobile || isFullscreen
            // On lg+ the AppShell sidebar is permanently fixed at w-56
            // (14rem) along the left edge — push the panel's left side
            // past it (+1rem gap) so the sidebar stops clipping the
            // chatbot in fullscreen. Below lg the sidebar is a drawer
            // and inset-4 alone is correct.
            ? 'inset-x-0 bottom-0 top-[max(0.5rem,env(safe-area-inset-top))] sm:inset-4 sm:top-4 sm:bottom-4 lg:left-[calc(14rem+1rem)] rounded-t-2xl sm:rounded-2xl border-t border-x sm:border border-border-subtle'
            : 'right-4 sm:right-6 bottom-[max(1rem,env(safe-area-inset-bottom))] sm:bottom-6 w-[420px] max-w-[calc(100vw-2rem)] h-[640px] max-h-[calc(100vh-3rem)] rounded-2xl border border-border-subtle',
        ]"
      >
        <div class="relative h-full flex flex-col bg-[#0a0716]/95 backdrop-blur-xl rounded-[inherit] overflow-hidden shadow-2xl">

          <ChatHeader
            :title="title"
            :model-name="modelName"
            :show-settings="showSettings"
            :is-fullscreen="isFullscreen"
            @toggle-settings="showSettings = !showSettings"
            @toggle-fullscreen="toggleFullscreen"
            @clear="onClear"
            @minimise="minimiseChatbot"
            @close="closeChatbot"
          />

          <!-- Conversation history toggle (mobile-friendly drawer) -->
          <div
            v-if="conversations.length > 1"
            class="border-b border-border-subtle px-3 sm:px-4 py-1.5 flex items-center gap-2 text-xs"
          >
            <button
              @click="conversationListOpen = !conversationListOpen"
              class="inline-flex items-center gap-1 text-slate-400 hover:text-slate-200 transition"
            >
              <span class="material-symbols-outlined text-[14px]">{{ conversationListOpen ? 'expand_less' : 'history' }}</span>
              {{ conversations.length }} conversation{{ conversations.length === 1 ? '' : 's' }}
            </button>
            <button
              @click="onNewConversation"
              class="ml-auto inline-flex items-center gap-1 text-primary hover:brightness-125 transition"
            >
              <span class="material-symbols-outlined text-[14px]">add</span>
              New
            </button>
          </div>
          <div
            v-if="conversationListOpen"
            class="border-b border-border-subtle px-2 py-2 max-h-40 overflow-y-auto bg-[#0a0716]/95"
          >
            <button
              v-for="c in conversations"
              :key="c.id"
              @click="loadConversation(c.id); conversationListOpen = false"
              class="w-full text-left px-2 py-1.5 rounded-md text-xs flex items-center gap-2 transition"
              :class="c.id === currentId ? 'bg-primary/10 text-primary' : 'text-slate-300 hover:bg-surface'"
            >
              <span class="material-symbols-outlined text-[14px]">chat</span>
              <span class="truncate flex-1">{{ c.title || 'Untitled' }}</span>
              <span class="text-[10px] text-slate-500">{{ c.message_count || 0 }} msg</span>
            </button>
          </div>

          <!-- Body -->
          <div class="flex-1 overflow-hidden relative">
            <!-- First-run model picker -->
            <div
              v-if="needsModel && !showSettings"
              class="absolute inset-0 overflow-y-auto"
            >
              <ChatModelPicker
                :initial-model-id="lastModelId"
                :busy="sending"
                @confirm="onModelPicked"
              />
            </div>

            <!-- Empty conversation state -->
            <div
              v-else-if="!messages.length && !showSettings"
              class="absolute inset-0 flex flex-col items-center justify-center text-center px-6 gap-3"
            >
              <div class="size-12 rounded-2xl bg-primary/15 border border-primary/30 flex items-center justify-center">
                <span class="material-symbols-outlined text-primary text-[24px]">smart_toy</span>
              </div>
              <p class="text-sm font-semibold text-slate-200">How can I help?</p>
              <p class="text-xs text-slate-500 max-w-xs leading-relaxed">
                Ask about your portfolio, run a backtest, link a brokerage, or just say hi.
                I can show charts, tables, and run actions on your behalf with your approval.
              </p>
              <div class="flex flex-wrap gap-2 justify-center pt-1">
                <button
                  v-for="s in [
                    'List my instances',
                    'Show my portfolio over the last month',
                    'How many backtests have I run?',
                  ]"
                  :key="s"
                  @click="onSend(s)"
                  class="text-[11px] px-2.5 py-1 rounded-full border border-border-subtle text-slate-300 hover:border-primary/40 hover:text-primary transition"
                >{{ s }}</button>
              </div>
            </div>

            <!-- Message list -->
            <div
              v-else-if="!showSettings"
              ref="messagesEl"
              class="absolute inset-0 overflow-y-auto overflow-x-hidden px-3 sm:px-4 py-3 space-y-3"
            >
              <template v-for="m in messages" :key="m.id">
                <ChatToolCall
                  v-if="m.status === 'pending_confirmation'"
                  :message="m"
                  :busy="sending"
                  @approve="onApproveTool"
                  @decline="onDeclineTool"
                />
                <ChatMessage v-else :message="m" />
              </template>

              <div v-if="sending" class="flex items-center gap-1.5 text-slate-500 text-xs px-2">
                <span class="chatbot-dot"></span>
                <span class="chatbot-dot" style="animation-delay: 0.15s"></span>
                <span class="chatbot-dot" style="animation-delay: 0.3s"></span>
                <span class="ml-1">Thinking…</span>
              </div>

              <div v-if="errorMessage" class="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-red-300 text-xs flex items-start gap-2">
                <span class="material-symbols-outlined text-[14px] mt-0.5">error</span>
                <p class="flex-1 break-words">{{ errorMessage }}</p>
                <button
                  @click="clearError"
                  class="shrink-0 size-5 rounded-md flex items-center justify-center text-red-300/70 hover:text-red-200 transition"
                  aria-label="Dismiss error"
                >
                  <span class="material-symbols-outlined text-[14px]">close</span>
                </button>
              </div>
            </div>

            <!-- Settings overlay -->
            <ChatSettings
              v-if="showSettings"
              :conversation="currentConversation"
              :tool-catalog="toolCatalog"
              :busy="sending"
              @close="showSettings = false"
              @change-model="async ({ id, name }) => { await setConversationModel(id, name) }"
              @toggle-auto-confirm="(v) => setAutoConfirmSafe(v)"
              @new-conversation="onNewConversation"
              @delete-conversation="onDeleteConversation"
            />
          </div>

          <!-- Composer -->
          <ChatComposer
            v-if="!showSettings"
            ref="composerRef"
            :busy="sending"
            :disabled="needsModel"
            :placeholder="needsModel ? 'Pick a model first…' : 'Ask me anything…'"
            @send="onSend"
          />
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.chatbot-bubble {
  animation: chatbot-bubble-glow 3s ease-in-out infinite;
}
@keyframes chatbot-bubble-glow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(140, 66, 255, 0.55), 0 12px 32px rgba(0,0,0,0.45); }
  50%      { box-shadow: 0 0 0 14px rgba(140, 66, 255, 0), 0 12px 32px rgba(0,0,0,0.45); }
}
.chatbot-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: #c98fff;
  animation: chatbot-dot 1.1s ease-in-out infinite both;
}
@keyframes chatbot-dot {
  0%, 80%, 100% { transform: scale(0.55); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}

.dock-bubble-enter-active, .dock-bubble-leave-active { transition: opacity 0.25s, transform 0.3s cubic-bezier(0.34,1.56,0.64,1); }
.dock-bubble-enter-from, .dock-bubble-leave-to { opacity: 0; transform: translateY(20px) scale(0.85); }

.dock-panel-enter-active, .dock-panel-leave-active { transition: opacity 0.3s, transform 0.35s cubic-bezier(0.16,1,0.3,1); }
.dock-panel-enter-from, .dock-panel-leave-to { opacity: 0; transform: translateY(40px) scale(0.97); }
</style>
