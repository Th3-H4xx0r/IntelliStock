// Cached loader for POST /ollama/list-models. 30-second in-memory cache;
// pass force=true to bypass it (used by the form's Refresh button).
//
// Returns { models, error } where:
//   models is an array of { name, model, size_bytes, parameter_size,
//                           quantization_level, context_length }
//   error is null on success, or a short user-facing string on failure.
//
// Aligns with the existing API_BASE + getToken() pattern used by
// ModelsView.vue so it works the same in dev (Vite proxy → /api),
// production (nginx → /api), and the upcoming /llm/test smoke flow.

import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

const CACHE = new Map()          // key: `${baseUrl}|${apiKey || ''}` → {ts, models}
const TTL_MS = 30_000

function _cacheKey(baseUrl, apiKey) {
  return `${baseUrl}|${apiKey || ''}`
}

function _authHeaders() {
  const token = getToken()
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

export async function loadOllamaModels({ baseUrl, apiKey, force = false } = {}) {
  const url = String(baseUrl || '').trim()
  if (!url) {
    return { models: [], error: 'Base URL is required' }
  }
  const key = _cacheKey(url, apiKey)
  if (!force) {
    const hit = CACHE.get(key)
    if (hit && (Date.now() - hit.ts) < TTL_MS) {
      return { models: hit.models, error: null }
    }
  }
  try {
    const resp = await fetch(`${API_BASE}/ollama/list-models`, {
      method: 'POST',
      headers: _authHeaders(),
      credentials: 'include',
      body: JSON.stringify({
        base_url: url,
        api_key: apiKey || undefined,
      }),
    })
    if (!resp.ok) {
      let body = {}
      try { body = await resp.json() } catch (_) {}
      const msg = body.error || body.detail || `HTTP ${resp.status}`
      return { models: [], error: String(msg) }
    }
    const data = await resp.json()
    const models = Array.isArray(data?.models) ? data.models : []
    CACHE.set(key, { ts: Date.now(), models })
    return { models, error: null }
  } catch (err) {
    return { models: [], error: String(err?.message || err) }
  }
}

export function clearOllamaModelsCache() {
  CACHE.clear()
}
