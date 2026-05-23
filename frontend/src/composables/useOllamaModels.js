// Cached loader for the Ollama model list. 30-second in-memory cache;
// pass force=true to bypass (used by the form's Refresh button).
//
// Returns ``{ models, error, source }`` where:
//   models is an array of { name, model, size_bytes, parameter_size,
//                           quantization_level, context_length }
//   error  is null on success, or a short user-facing string on failure.
//   source is 'backend' or 'browser' depending on which path succeeded.
//
// Two-stage resolution: try our backend's /api/ollama/list-models first
// (works for localhost or any backend-reachable host). If that fails
// AND the operator's base_url isn't localhost, retry directly from the
// browser against ``<base_url>/api/tags``. The browser path requires
// the operator to set ``OLLAMA_ORIGINS`` on their Ollama server to
// allow our origin (or "*" while testing). That works for any host the
// browser itself can reach — Tailscale IPs, LAN hostnames, etc — where
// our backend can't.

import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

const CACHE = new Map()          // key: `${baseUrl}|${apiKey || ''}` → {ts, models, source}
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

function _isLocalhost(urlString) {
  try {
    const host = new URL(urlString).hostname.toLowerCase()
    return host === 'localhost' || host === '127.0.0.1' || host === '::1'
  } catch (_) {
    return false
  }
}

// Stage 1: ask our backend. Returns { models, error } shaped object;
// throws only on programmer errors.
async function _fetchViaBackend({ baseUrl, apiKey }) {
  try {
    const resp = await fetch(`${API_BASE}/ollama/list-models`, {
      method: 'POST',
      headers: _authHeaders(),
      credentials: 'include',
      body: JSON.stringify({
        base_url: baseUrl,
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
    return { models, error: null }
  } catch (err) {
    return { models: [], error: String(err?.message || err) }
  }
}

// Stage 2: hit Ollama directly from the browser. Only works if the
// operator's Ollama allows our origin via OLLAMA_ORIGINS.
async function _fetchViaBrowser({ baseUrl, apiKey }) {
  const trimmed = String(baseUrl || '').replace(/\/+$/, '')
  // Ollama Cloud uses /v1 OpenAI-compat; the native /api/tags lives at
  // root. Strip a trailing /v1 if present so /api/tags resolves.
  const root = trimmed.endsWith('/v1') ? trimmed.slice(0, -3) : trimmed
  const url = `${root}/api/tags`
  const headers = { 'Content-Type': 'application/json' }
  if (apiKey) headers.Authorization = `Bearer ${apiKey}`
  try {
    const resp = await fetch(url, { method: 'GET', headers })
    if (!resp.ok) {
      return { models: [], error: `HTTP ${resp.status} from ${url}` }
    }
    const data = await resp.json()
    const raw = Array.isArray(data?.models) ? data.models : []
    // Project to the same shape the backend endpoint returns. We don't
    // call /api/show from the browser path — context_length is left null
    // to avoid 5+ extra round-trips per refresh.
    const models = raw.map((m) => ({
      name: m.name || m.model,
      model: m.model || m.name,
      size_bytes: m.size,
      parameter_size: m.details?.parameter_size || null,
      quantization_level: m.details?.quantization_level || null,
      context_length: null,
    }))
    return { models, error: null }
  } catch (err) {
    return { models: [], error: String(err?.message || err) }
  }
}

export async function loadOllamaModels({ baseUrl, apiKey, force = false } = {}) {
  const url = String(baseUrl || '').trim()
  if (!url) {
    return { models: [], error: 'Base URL is required', source: null }
  }
  const key = _cacheKey(url, apiKey)
  if (!force) {
    const hit = CACHE.get(key)
    if (hit && (Date.now() - hit.ts) < TTL_MS) {
      return { models: hit.models, error: null, source: hit.source }
    }
  }

  const backend = await _fetchViaBackend({ baseUrl: url, apiKey })
  if (!backend.error && backend.models.length) {
    CACHE.set(key, { ts: Date.now(), models: backend.models, source: 'backend' })
    return { models: backend.models, error: null, source: 'backend' }
  }

  // Fall back to direct browser fetch when the backend couldn't reach
  // the host. We skip the fallback for localhost / 127.0.0.1 since a
  // backend in a container usually can't reach the operator's loopback
  // either, but the browser's loopback IS the operator's loopback —
  // worth trying.
  const browser = await _fetchViaBrowser({ baseUrl: url, apiKey })
  if (!browser.error) {
    CACHE.set(key, { ts: Date.now(), models: browser.models, source: 'browser' })
    return { models: browser.models, error: null, source: 'browser' }
  }

  // Both paths failed. Surface both errors so the operator sees what
  // happened on each side.
  const combined = (
    `backend: ${backend.error}`
    + ` · browser: ${browser.error}`
    + (_isLocalhost(url)
        ? ''
        : ' · If the host is only reachable from your machine, set'
          + ' OLLAMA_ORIGINS="*" on Ollama to allow browser fetches.')
  )
  return { models: [], error: combined, source: null }
}

export function clearOllamaModelsCache() {
  CACHE.clear()
}
