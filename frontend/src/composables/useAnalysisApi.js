// Requests for the AI Analysis screen and its settings card. Every call
// returns { ok, status, body } (status 0 when the server can't be reached)
// so callers can tell an unknown symbol (404) or a missing model (400) from
// a failure, and show the server's own `detail`. Nothing here retries; the
// screen's polling is in views/AnalysisView.vue.
// Contract: docs/superpowers/specs/2026-11-14-why-it-moved-design.md (API).

import { getToken } from '../utils/auth.js'
import { API_BASE } from './useStockApi.js'

async function request(method, path, body) {
  const token = getToken()
  const headers = token ? { Authorization: `Bearer ${token}` } : {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let res
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    return { ok: false, status: 0, body: null }
  }
  let data = null
  try { data = await res.json() } catch { /* empty or not JSON */ }
  return { ok: res.ok, status: res.status, body: data }
}

/** GET /analysis/settings → { model_id, model_name, sigma, min_move_pct, models } */
export function getAnalysisSettings() {
  return request('GET', '/analysis/settings')
}

/** PUT /analysis/settings with any of model_id (string|null), sigma, min_move_pct. */
export function saveAnalysisSettings(patch) {
  return request('PUT', '/analysis/settings', patch)
}

/** GET /analysis/moves?symbol=…&range=… → bars, events, categories, pending. 404 = unknown symbol. */
export function getMoves(symbol, range) {
  return request('GET', `/analysis/moves?${new URLSearchParams({ symbol, range })}`)
}

/**
 * POST /analysis/moves/explain → { queued }. `dates` omitted queues every
 * unexplained or failed event in the range; 400 when no model is chosen.
 */
export function explainMoves({ symbol, range, dates, force = false }) {
  const body = { symbol, range, force: !!force }
  if (Array.isArray(dates) && dates.length) body.dates = dates
  return request('POST', '/analysis/moves/explain', body)
}

/** GET /analysis/portfolio?brokerage_id=… → { as_of, spy_pct, totals, holdings } */
export function getPortfolioMoves(brokerageId) {
  return request('GET', `/analysis/portfolio?${new URLSearchParams({ brokerage_id: String(brokerageId) })}`)
}

/** GET /symbols/search?q=… → { results: [{ symbol, name, type }] } */
export function searchSymbols(q) {
  return request('GET', `/symbols/search?${new URLSearchParams({ q })}`)
}
