// Requests for the stock page, the order ticket and symbol search.
// Reads go through the dashboard's apiGet; the two writes (place an order,
// cancel one) return the status and body so the caller can show the
// server's own `detail`. Nothing here retries.

import { getToken } from '../utils/auth.js'
import { apiGet } from './useDashboardData.js'

export const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')

export { apiGet }

async function send(method, path, body) {
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

/** POST /brokerages/{id}/orders — sent once; never retried. */
export function placeOrder(brokerageId, order) {
  return send('POST', `/brokerages/${encodeURIComponent(brokerageId)}/orders`, order)
}

/** DELETE /brokerages/{id}/orders/{orderId} */
export function cancelOrder(brokerageId, orderId) {
  return send('DELETE', `/brokerages/${encodeURIComponent(brokerageId)}/orders/${encodeURIComponent(orderId)}`)
}

/** GET /brokerages/{id}/positions → { cash, positions } with numbers. */
export async function loadPositions(brokerageId) {
  const d = await apiGet(`/brokerages/${encodeURIComponent(brokerageId)}/positions`)
  const n = v => (v == null || !Number.isFinite(Number(v)) ? null : Number(v))
  return {
    cash: n(d?.cash),
    positions: (d?.positions || []).filter(p => p?.symbol).map(p => ({
      symbol: String(p.symbol).toUpperCase(),
      qty: Number(p.qty) || 0,
      marketValue: Number(p.marketValue) || 0,
      unrealizedPnl: Number(p.unrealizedPnl) || 0,
      unrealizedPnlPct: Number(p.unrealizedPnlPct) || 0,
      lastPrice: n(p.lastPrice),
      avgEntryPrice: n(p.avgEntryPrice),
    })),
  }
}

/** GET /symbol-historicals → { SYMBOL: number[] } of 1D values. */
export async function loadDayValues(symbols) {
  if (!symbols.length) return {}
  const q = new URLSearchParams({ symbols: symbols.join(','), range: '1D' })
  const d = await apiGet(`/symbol-historicals?${q}`)
  const out = {}
  for (const [sym, pts] of Object.entries(d?.results || {})) {
    out[sym] = (pts || []).map(p => Number(p?.value)).filter(Number.isFinite)
  }
  return out
}

// The dashboard's remembered account, shared so the stock page opens on the
// same one.
export const SELECTED_ACCOUNT_KEY = 'intellistock_dashboard_account'

export function savedAccountId() {
  try { return localStorage.getItem(SELECTED_ACCOUNT_KEY) } catch { return null }
}

export function saveAccountId(id) {
  try { localStorage.setItem(SELECTED_ACCOUNT_KEY, String(id)) } catch { /* storage blocked */ }
}
