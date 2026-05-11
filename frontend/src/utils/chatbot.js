/**
 * Chatbot HTTP wrappers. Mirrors the auth.js API_BASE pattern; every call
 * is JWT-authenticated via the shared Bearer header.
 */
import { authHeaders, ONBOARDING_API_BASE as API_BASE } from './auth.js'

async function _json(res) {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const data = await res.json()
      const d = data?.detail
      if (Array.isArray(d)) detail = d.map(x => x?.msg || x?.message || JSON.stringify(x)).join('; ')
      else if (typeof d === 'string') detail = d
      else if (d && typeof d === 'object') detail = d.msg || d.message || JSON.stringify(d)
    } catch { /* ignore */ }
    throw new Error(detail)
  }
  return res.json()
}

function _headers() {
  return { ...authHeaders(), 'Content-Type': 'application/json' }
}

export async function listConversations() {
  const res = await fetch(`${API_BASE}/chatbot/conversations`, { headers: _headers() })
  return _json(res)
}

export async function createConversation(body = {}) {
  const res = await fetch(`${API_BASE}/chatbot/conversations`, {
    method: 'POST',
    headers: _headers(),
    body: JSON.stringify(body),
  })
  return _json(res)
}

export async function getConversation(id) {
  const res = await fetch(`${API_BASE}/chatbot/conversations/${encodeURIComponent(id)}`, {
    headers: _headers(),
  })
  return _json(res)
}

export async function patchConversation(id, body) {
  const res = await fetch(`${API_BASE}/chatbot/conversations/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: _headers(),
    body: JSON.stringify(body),
  })
  return _json(res)
}

export async function deleteConversation(id) {
  const res = await fetch(`${API_BASE}/chatbot/conversations/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: _headers(),
  })
  return _json(res)
}

export async function clearConversation(id) {
  const res = await fetch(`${API_BASE}/chatbot/conversations/${encodeURIComponent(id)}/clear`, {
    method: 'POST',
    headers: _headers(),
  })
  return _json(res)
}

export async function turnConversation(id, content) {
  const res = await fetch(`${API_BASE}/chatbot/conversations/${encodeURIComponent(id)}/turn`, {
    method: 'POST',
    headers: _headers(),
    body: JSON.stringify({ content }),
  })
  return _json(res)
}

export async function confirmTool(id, messageId, approved) {
  const res = await fetch(`${API_BASE}/chatbot/conversations/${encodeURIComponent(id)}/confirm-tool`, {
    method: 'POST',
    headers: _headers(),
    body: JSON.stringify({ message_id: messageId, approved }),
  })
  return _json(res)
}

export async function fetchToolCatalog() {
  const res = await fetch(`${API_BASE}/chatbot/tools`, { headers: _headers() })
  return _json(res)
}

export async function listAvailableModels() {
  const res = await fetch(`${API_BASE}/models`, { headers: _headers() })
  const data = await _json(res)
  return data.models || data || []
}
