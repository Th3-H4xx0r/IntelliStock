import { ref, computed } from 'vue'
import {
  listConversations,
  createConversation,
  getConversation,
  patchConversation,
  deleteConversation,
  clearConversation,
  turnConversation,
  confirmTool,
  fetchToolCatalog,
} from '../utils/chatbot.js'

// ── Module-level shared state (single instance per browser tab) ─────────────
// Mirrors the useFullscreen.js pattern: pages render <AppShell /> as their
// root, so the page is the parent of AppShell — provide/inject would not
// work. A module-level ref is shared across every importer.

const STORAGE_KEY = 'intellistock_chatbot_state'

function loadPersisted() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    return JSON.parse(raw) || {}
  } catch { return {} }
}

function persist(patch) {
  try {
    const cur = loadPersisted()
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...cur, ...patch }))
  } catch { /* ignore */ }
}

const persisted = loadPersisted()

export const isOpen = ref(false)
export const isMinimised = ref(false)
export const showSettings = ref(false)
export const conversations = ref([])
export const currentId = ref(persisted.currentId || null)
export const currentConversation = ref(null)
export const sending = ref(false)
export const errorMessage = ref('')
export const toolCatalog = ref([])
export const lastModelId = ref(persisted.lastModelId || null)

export const messages = computed(() => currentConversation.value?.messages || [])
export const pendingMessage = computed(() => {
  const list = messages.value
  for (let i = list.length - 1; i >= 0; i--) {
    if (list[i]?.status === 'pending_confirmation') return list[i]
  }
  return null
})
export const needsModel = computed(() => !currentConversation.value?.model_id)

// ── Public actions ──────────────────────────────────────────────────────────

export function openChatbot() {
  isOpen.value = true
  isMinimised.value = false
}

export function minimiseChatbot() {
  isMinimised.value = true
}

export function closeChatbot() {
  isOpen.value = false
  isMinimised.value = false
  showSettings.value = false
}

export function toggleChatbot() {
  if (isOpen.value && !isMinimised.value) {
    minimiseChatbot()
  } else {
    openChatbot()
  }
}

export async function refreshConversations() {
  try {
    const data = await listConversations()
    conversations.value = data.conversations || []
  } catch (e) {
    errorMessage.value = e.message || 'Failed to load conversations'
  }
}

export async function loadConversation(id) {
  try {
    errorMessage.value = ''
    const convo = await getConversation(id)
    currentConversation.value = convo
    currentId.value = convo.id
    persist({ currentId: convo.id, lastModelId: convo.model_id })
    if (convo.model_id) lastModelId.value = convo.model_id
  } catch (e) {
    errorMessage.value = e.message || 'Failed to load conversation'
  }
}

export async function startNewConversation({ modelId = null, title = null } = {}) {
  errorMessage.value = ''
  const useModel = modelId || lastModelId.value || null
  const convo = await createConversation({ model_id: useModel, title })
  currentConversation.value = convo
  currentId.value = convo.id
  persist({ currentId: convo.id, lastModelId: convo.model_id || null })
  if (convo.model_id) lastModelId.value = convo.model_id
  await refreshConversations()
  return convo
}

export async function setConversationModel(modelId, modelName = null) {
  if (!currentConversation.value) {
    return startNewConversation({ modelId })
  }
  const updated = await patchConversation(currentConversation.value.id, { model_id: modelId })
  currentConversation.value = updated
  if (updated.model_id) {
    lastModelId.value = updated.model_id
    persist({ lastModelId: updated.model_id })
  }
  return updated
}

export async function setAutoConfirmSafe(value) {
  if (!currentConversation.value) return null
  const updated = await patchConversation(currentConversation.value.id, {
    auto_confirm_safe_tools: !!value,
  })
  currentConversation.value = updated
  return updated
}

export async function clearCurrent() {
  if (!currentConversation.value) return
  const updated = await clearConversation(currentConversation.value.id)
  currentConversation.value = updated
  await refreshConversations()
}

export async function deleteCurrent() {
  if (!currentConversation.value) return
  const id = currentConversation.value.id
  await deleteConversation(id)
  currentConversation.value = null
  currentId.value = null
  persist({ currentId: null })
  await refreshConversations()
}

export async function sendMessage(text) {
  const convo = currentConversation.value
  if (!convo) throw new Error('No active conversation')
  const trimmed = (text || '').trim()
  if (!trimmed) return

  // Optimistic local append so the user sees their message in the chat the
  // instant they hit send, before the LLM round-trip starts. The temp id
  // is removed when the server's authoritative messages arrive.
  const tempId = `temp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  const optimisticMsg = {
    id: tempId,
    role: 'user',
    content: trimmed,
    created_at: new Date().toISOString(),
    status: 'sending',
  }
  currentConversation.value = {
    ...convo,
    messages: [...(convo.messages || []), optimisticMsg],
  }

  sending.value = true
  errorMessage.value = ''
  try {
    const data = await turnConversation(convo.id, trimmed)
    const appended = data.messages || []
    // Drop the optimistic temp message and use the server-echoed messages
    // (they carry the canonical id + timestamps used by the LLM context).
    const baseMsgs = (currentConversation.value?.messages || [])
      .filter(m => m.id !== tempId)
    currentConversation.value = {
      ...currentConversation.value,
      messages: [...baseMsgs, ...appended],
    }
    // Refresh the title in case orchestration auto-named the conversation.
    try {
      const fresh = await getConversation(convo.id)
      currentConversation.value = fresh
      await refreshConversations()
    } catch { /* ignore */ }
  } catch (e) {
    // Keep the optimistic message so the user can still see what they typed,
    // but mark it failed so future styling can differentiate.
    const msgs = (currentConversation.value?.messages || []).map(m =>
      m.id === tempId ? { ...m, status: 'failed' } : m,
    )
    currentConversation.value = {
      ...currentConversation.value,
      messages: msgs,
    }
    errorMessage.value = e.message || 'Send failed'
  } finally {
    sending.value = false
  }
}

export async function approveTool(messageId, approved) {
  const convo = currentConversation.value
  if (!convo) return
  sending.value = true
  errorMessage.value = ''
  try {
    const data = await confirmTool(convo.id, messageId, approved)
    const fresh = await getConversation(convo.id)
    currentConversation.value = fresh
    return data
  } catch (e) {
    errorMessage.value = e.message || 'Failed to confirm tool'
  } finally {
    sending.value = false
  }
}

export async function loadToolCatalog() {
  if (toolCatalog.value.length) return toolCatalog.value
  try {
    const data = await fetchToolCatalog()
    toolCatalog.value = data.tools || []
  } catch { /* not fatal */ }
  return toolCatalog.value
}

export function rememberLastModel(modelId) {
  lastModelId.value = modelId
  persist({ lastModelId: modelId })
}

/**
 * Forget every piece of chatbot state for the current user. Called from
 * auth.clearSession on sign-out so a follow-on sign-in (same browser tab,
 * different account) can't read the previous user's conversation id /
 * model picks. Note that the *backend* also enforces user_id scoping, so
 * even if cached state survived, conversations from another user would
 * 404 on fetch — this is defence-in-depth.
 */
export function resetChatbot() {
  isOpen.value = false
  isMinimised.value = false
  showSettings.value = false
  conversations.value = []
  currentId.value = null
  currentConversation.value = null
  sending.value = false
  errorMessage.value = ''
  lastModelId.value = null
  try { localStorage.removeItem(STORAGE_KEY) } catch { /* ignore */ }
}

export function clearError() {
  errorMessage.value = ''
}
