// Cached loader for the OpenRouter model catalog (GET /api/v1/models, proxied
// through our backend's POST /openrouter/list-models). 30-second in-memory
// cache; pass force=true to bypass (the form's Refresh button).
//
// Returns ``{ models, error }`` where models is an array of
//   { id, name, context_length, pricing }.
// ``pricing`` is OpenRouter's raw object (USD **per token**, string values) so
// the caller can auto-fill the per-row cost overrides (× 1e6 → USD per 1M).
//
// The catalog is public — no API key is required. On any error the caller
// falls back to manual model-id entry (same contract as useBedrockModels).

import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

const DEFAULT_BASE_URL = 'https://openrouter.ai/api/v1'
const CACHE = new Map()          // key: baseUrl → {ts, models}
const TTL_MS = 30_000

function _authHeaders() {
  const token = getToken()
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

export async function loadOpenRouterModels({ baseUrl, force = false } = {}) {
  const base = String(baseUrl || DEFAULT_BASE_URL).trim() || DEFAULT_BASE_URL
  if (!force) {
    const hit = CACHE.get(base)
    if (hit && (Date.now() - hit.ts) < TTL_MS) {
      return { models: hit.models, error: null }
    }
  }
  try {
    const resp = await fetch(`${API_BASE}/openrouter/list-models`, {
      method: 'POST',
      headers: _authHeaders(),
      credentials: 'include',
      body: JSON.stringify({ base_url: base }),
    })
    if (!resp.ok) {
      let body = {}
      try { body = await resp.json() } catch (_) {}
      const msg = body.error || body.detail || `HTTP ${resp.status}`
      return { models: [], error: String(msg) }
    }
    const data = await resp.json()
    const models = Array.isArray(data?.models) ? data.models : []
    if (models.length) CACHE.set(base, { ts: Date.now(), models })
    return { models, error: data?.error || (models.length ? null : 'No models returned') }
  } catch (err) {
    return { models: [], error: String(err?.message || err) }
  }
}

export function clearOpenRouterModelsCache() {
  CACHE.clear()
}

// Convert an OpenRouter pricing object (USD per token, string values) into the
// per-row cost-override fields IntelliStock stores (USD per 1M tokens). Returns
// only the keys that were present + numeric; callers spread into the draft.
export function openRouterPricingToPer1m(pricing) {
  const p = pricing || {}
  const per1m = (v) => {
    const n = Number(v)
    return Number.isFinite(n) ? +(n * 1_000_000).toFixed(6) : null
  }
  const out = {}
  const inp = per1m(p.prompt)
  const out_ = per1m(p.completion)
  const cr = per1m(p.input_cache_read)
  const cw = per1m(p.input_cache_write)
  if (inp != null) out.inputCostPer1m = inp
  if (out_ != null) out.outputCostPer1m = out_
  if (cr != null) out.cacheReadCostPer1m = cr
  if (cw != null) out.cacheCreationCostPer1m = cw
  return out
}
