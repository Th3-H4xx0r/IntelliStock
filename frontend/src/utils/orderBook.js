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

/** Size: `3 sh`, `2 ct` for options, `$250.00` for an unfilled dollar order (its shares once filled), `1 of 3 sh` part-filled. */
export function orderQtyLabel(o) {
  const unit = o?.asset_class === 'us_option' ? 'ct' : 'sh'
  const qty = num(o?.qty)
  const filled = num(o?.filled_qty) || 0
  if (qty == null) {
    // A dollar order: once it fills, the shares it bought; until then, the amount.
    if (filled > 0) return `${qtyNumber(filled)} ${unit}`
    const notional = num(o?.notional)
    return notional != null ? fmtUsd(notional) : '—'
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

// ── Blotter helpers ─────────────────────────────────────────────────────────

/** The app's status dot: a working order is live (green), a fill is neutral,
 *  a dead end is muted, a reject is red. */
export function orderStatusDot(o) {
  const status = String(o?.status || '').toLowerCase()
  const filled = num(o?.filled_qty) || 0
  if (status === 'rejected') return 'ui-dot-error'
  if (status === 'pending_cancel') return 'ui-dot-warn'
  if (OPEN_STATUSES.has(status)) return 'ui-dot-live'
  if (status === 'filled' || filled > 0) return 'bg-slate-300'
  return 'ui-dot-off'
}

/** Share filled (0–1) for a partial fill, else null — drives the thin fill bar. */
export function orderFillFraction(o) {
  const qty = num(o?.qty)
  const filled = num(o?.filled_qty) || 0
  return qty && filled > 0 && filled < qty ? filled / qty : null
}

/** Dollar value of what filled (×100 per option contract), else a dollar order's amount, else null. */
export function orderValue(o) {
  const filled = num(o?.filled_qty) || 0
  const avg = num(o?.filled_avg_price)
  if (filled > 0 && avg != null) return filled * avg * (o?.asset_class === 'us_option' ? 100 : 1)
  return num(o?.notional)
}

const CLOCK_FMT = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' })
const WEEKDAY_FMT = new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
const WEEKDAY_YEAR_FMT = new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
const STAMP_FMT = new Intl.DateTimeFormat('en-US', {
  month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit',
})

function localDayKey(d) {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}

/** `10:42 AM` in the viewer's time zone. */
export function orderClock(iso) {
  const t = Date.parse(iso || '')
  return Number.isFinite(t) ? CLOCK_FMT.format(new Date(t)) : ''
}

/** `Today`, `Yesterday`, `Mon, Nov 10` (with the year when it isn't this year). */
export function orderDayLabel(iso, now = Date.now()) {
  const t = Date.parse(iso || '')
  if (!Number.isFinite(t)) return 'Undated'
  const d = new Date(t)
  const today = new Date(now)
  const yesterday = new Date(now)
  yesterday.setDate(today.getDate() - 1)
  if (localDayKey(d) === localDayKey(today)) return 'Today'
  if (localDayKey(d) === localDayKey(yesterday)) return 'Yesterday'
  return (d.getFullYear() === today.getFullYear() ? WEEKDAY_FMT : WEEKDAY_YEAR_FMT).format(d)
}

/** Open orders: `10:42 AM` today, else `Nov 10, 10:42 AM`. */
export function orderWhen(iso, now = Date.now()) {
  const t = Date.parse(iso || '')
  if (!Number.isFinite(t)) return ''
  const d = new Date(t)
  if (localDayKey(d) === localDayKey(new Date(now))) return CLOCK_FMT.format(d)
  return `${DAY_FMT.format(d)}, ${CLOCK_FMT.format(d)}`
}

/** History split into local days, keeping the incoming (newest-first) order. */
export function groupOrdersByDay(orders, now = Date.now()) {
  const groups = []
  for (const o of orders || []) {
    const iso = orderTimestamp(o)
    const t = Date.parse(iso || '')
    const key = Number.isFinite(t) ? localDayKey(new Date(t)) : 'undated'
    let g = groups[groups.length - 1]
    if (!g || g.key !== key) {
      g = { key, label: orderDayLabel(iso, now), orders: [] }
      groups.push(g)
    }
    g.orders.push(o)
  }
  return groups
}

const TIF_LABELS = {
  day: 'Day', gtc: 'Good till canceled', ioc: 'Immediate or cancel', fok: 'Fill or kill', opg: 'Market open', cls: 'Market close',
}

/** Label/value pairs for the expanded row; empty fields are left out. */
export function orderDetails(o) {
  const unit = o?.asset_class === 'us_option' ? 'ct' : 'sh'
  const qty = num(o?.qty)
  const filled = num(o?.filled_qty) || 0
  const stamp = iso => {
    const t = Date.parse(iso || '')
    return Number.isFinite(t) ? STAMP_FMT.format(new Date(t)) : null
  }
  const rows = [
    ['Order type', TYPE_LABELS[String(o?.type || '').toLowerCase()] || sentence(o?.type) || null],
    ['Side', sentence(o?.side) || null],
    ['Time in force', TIF_LABELS[String(o?.time_in_force || '').toLowerCase()] || (o?.time_in_force ? String(o.time_in_force).toUpperCase() : null)],
    ['Ordered', qty != null ? `${qtyNumber(qty)} ${unit}` : (num(o?.notional) != null ? fmtUsd(num(o.notional)) : null)],
    ['Filled', filled > 0 ? `${qtyNumber(filled)} ${unit}` : null],
    ['Average fill', filled > 0 && num(o?.filled_avg_price) != null ? fmtPrice(num(o.filled_avg_price)) : null],
    ['Limit price', num(o?.limit_price) != null ? fmtPrice(num(o.limit_price)) : null],
    ['Stop price', num(o?.stop_price) != null ? fmtPrice(num(o.stop_price)) : null],
    ['Submitted', stamp(o?.submitted_at)],
    ['Filled at', stamp(o?.filled_at)],
    ['Canceled at', stamp(o?.canceled_at)],
    ['Order ID', o?.id ? String(o.id) : null],
  ]
  return rows.filter(([, v]) => v != null && v !== '').map(([label, value]) => ({ label, value }))
}
