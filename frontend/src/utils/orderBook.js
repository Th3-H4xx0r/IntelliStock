// Display rules for the dashboard's Orders section. Orders come from
// GET /brokerages/{id}/order-book (Alpaca's /v2/orders, normalized):
// { id, symbol, side, type, status, qty, notional, filled_qty,
//   filled_avg_price, limit_price, stop_price, submitted_at, filled_at,
//   canceled_at, asset_class }

import { fmtUsd, fmtPrice } from './dashboardAnalytics.js'

const OPEN_STATUSES = new Set([
  'new', 'accepted', 'pending_new', 'accepted_for_bidding', 'held',
  'calculated', 'pending_replace', 'partially_filled', 'pending_cancel',
])

const TYPE_LABELS = {
  market: 'Market', limit: 'Limit', stop: 'Stop', stop_limit: 'Stop limit', trailing_stop: 'Trailing stop',
}

function num(v) {
  const n = Number(v)
  return v == null || v === '' || !Number.isFinite(n) ? null : n
}

function sentence(s) {
  const t = String(s || '').replace(/_/g, ' ').trim()
  return t ? t[0].toUpperCase() + t.slice(1) : ''
}

/** `3`, `0.49`, `1.5` — trims trailing zeros. */
function qtyNumber(q) {
  const n = Number(q) || 0
  const s = Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(Math.abs(n) >= 10 ? 2 : 4)
  return s.includes('.') ? s.replace(/\.?0+$/, '') : s
}

export function isOpenOrder(o) {
  return OPEN_STATUSES.has(String(o?.status || '').toLowerCase())
}

/** The tab to show first: Open when anything is working, else Recent. */
export function defaultOrdersMode(book) {
  return book?.open?.length ? 'open' : 'recent'
}

/** `Buy · Limit`, `Sell · Stop limit` */
export function orderSideType(o) {
  const side = sentence(o?.side)
  const type = TYPE_LABELS[String(o?.type || '').toLowerCase()] || sentence(o?.type)
  return [side, type].filter(Boolean).join(' · ')
}

/** Size: `3 sh`, `2 ct` for options, `$250.00` for a dollar order, `1 of 3 sh` part-filled. */
export function orderQtyLabel(o) {
  const unit = o?.asset_class === 'us_option' ? 'ct' : 'sh'
  const qty = num(o?.qty)
  const filled = num(o?.filled_qty) || 0
  if (qty == null) {
    const notional = num(o?.notional)
    if (notional != null) return fmtUsd(notional)
    return filled > 0 ? `${qtyNumber(filled)} ${unit}` : '—'
  }
  if (filled > 0 && filled < qty) return `${qtyNumber(filled)} of ${qtyNumber(qty)} ${unit}`
  return `${qtyNumber(qty)} ${unit}`
}

/** The price that matters: the average fill once anything filled, else the order's limit or stop. */
export function orderPriceLabel(o) {
  const avg = num(o?.filled_avg_price)
  if ((num(o?.filled_qty) || 0) > 0 && avg != null) return fmtPrice(avg)
  const limit = num(o?.limit_price)
  const stop = num(o?.stop_price)
  if (limit != null && stop != null) return `${fmtPrice(stop)} stop`
  if (limit != null) return fmtPrice(limit)
  if (stop != null) return `${fmtPrice(stop)} stop`
  return 'Market'
}

/** A plain status word. Partial outcomes say how much filled. */
export function orderStatusLabel(o) {
  const status = String(o?.status || '').toLowerCase()
  const filled = num(o?.filled_qty) || 0
  switch (status) {
    case 'filled': return 'Filled'
    case 'partially_filled': return 'Partially filled'
    case 'pending_cancel': return 'Canceling'
    case 'canceled': return filled > 0 ? 'Canceled, part filled' : 'Canceled'
    case 'expired': return filled > 0 ? 'Expired, part filled' : 'Expired'
    case 'done_for_day': return 'Done for day'
    case 'rejected': return 'Rejected'
    default: return OPEN_STATUSES.has(status) ? 'Open' : sentence(status)
  }
}

/** Muted for orders that ended without trading, full strength otherwise. */
export function orderStatusClass(o) {
  const status = String(o?.status || '').toLowerCase()
  const filled = num(o?.filled_qty) || 0
  if (['canceled', 'expired', 'rejected', 'done_for_day'].includes(status) && filled === 0) return 'text-slate-500'
  return 'text-slate-200'
}

/** When it happened: fills and cancels by their own time, open orders by submission. */
export function orderTimestamp(o) {
  return o?.filled_at || o?.canceled_at || o?.submitted_at || null
}

const DAY_FMT = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' })

/** `Just now`, `12m ago`, `3h ago`, `Yesterday`, then `Nov 3`. */
export function orderTimeLabel(iso, now = Date.now()) {
  const t = Date.parse(iso || '')
  if (!Number.isFinite(t)) return ''
  const m = Math.max(0, Math.floor((now - t) / 60_000))
  if (m < 1) return 'Just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  if (h < 48) return 'Yesterday'
  return DAY_FMT.format(new Date(t))
}
