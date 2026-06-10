// Cached loader for the AWS Bedrock model list (foundation models +
// cross-region inference profiles). 30-second in-memory cache; pass
// force=true to bypass (the form's Refresh button).
//
// Returns ``{ models, error }`` where models is an array of
//   { id, name, provider_name, kind, supports_tools, modalities }.
//
// Backend-only: unlike Ollama there is NO browser-direct fallback. Bedrock
// requires SigV4 / bearer auth + CORS that a browser fetch can't satisfy, so
// discovery always goes through our backend's POST /api/bedrock/list-models.
// On any error (including a narrowly-scoped key lacking
// bedrock:ListFoundationModels, which returns 401) the caller falls back to
// manual model-id entry.

import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

const CACHE = new Map()          // key: `${region}|${hasKey}` → {ts, models}
const TTL_MS = 30_000

function _cacheKey(region, apiKey) {
  return `${region}|${apiKey ? '1' : '0'}`
}

function _authHeaders() {
  const token = getToken()
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

export async function loadBedrockModels({ apiKey, region, force = false } = {}) {
  const reg = String(region || '').trim()
  const key = String(apiKey || '').trim()
  if (!reg) {
    return { models: [], error: 'Region is required (e.g. us-east-1)' }
  }
  if (!key) {
    return { models: [], error: 'Bedrock API key is required to list models' }
  }
  const ck = _cacheKey(reg, key)
  if (!force) {
    const hit = CACHE.get(ck)
    if (hit && (Date.now() - hit.ts) < TTL_MS) {
      return { models: hit.models, error: null }
    }
  }
  try {
    const resp = await fetch(`${API_BASE}/bedrock/list-models`, {
      method: 'POST',
      headers: _authHeaders(),
      credentials: 'include',
      body: JSON.stringify({ api_key: key, region: reg }),
    })
    if (!resp.ok) {
      let body = {}
      try { body = await resp.json() } catch (_) {}
      const msg = body.error || body.detail || `HTTP ${resp.status}`
      // 401 here usually means the key can call Converse but lacks
      // bedrock:ListFoundationModels — discovery is simply unavailable;
      // the form falls back to a manual model-id input.
      return { models: [], error: String(msg) }
    }
    const data = await resp.json()
    const models = Array.isArray(data?.models) ? data.models : []
    CACHE.set(ck, { ts: Date.now(), models })
    return { models, error: null }
  } catch (err) {
    return { models: [], error: String(err?.message || err) }
  }
}

export function clearBedrockModelsCache() {
  CACHE.clear()
}
