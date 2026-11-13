// Pure rules for the web order ticket (stock page Buy / Sell). No Vue and no
// fetch, so `node --test` covers them.
//
// Validation mirrors the backend's build_manual_order
// (backend/api/main.py, POST /brokerages/{id}/orders) message for message, so
// the ticket refuses what the server would refuse before anything is sent.

import { fmtUsd, fmtPrice } from './dashboardAnalytics.js'

export const SYMBOL_RE = /^[A-Z][A-Z0-9.-]{0,9}$/
/** Alpaca books fractional shares to 9 decimals; "Sell all" sends them exactly. */
export const QTY_MAX_DECIMALS = 9

const UNREACHABLE = "Couldn't reach Alpaca. The order was not confirmed — check Orders before trying again"

export function normalizeSymbol(symbol) {
  return String(symbol ?? '').trim().toUpperCase()
}

/** Equities only: options and crypto symbols are not tradable from the app. */
export function isTradableSymbol(symbol) {
  return SYMBOL_RE.test(normalizeSymbol(symbol))
}

// ── Accounts ─────────────────────────────────────────────────────────────────

export function isAlpacaAccount(a) {
  return String(a?.brokerage_type || '').toLowerCase() === 'alpaca'
}

/** Said plainly on the ticket and the review. */
export function accountModeLabel(a) {
  return a?.alpaca_paper ? 'Paper' : 'Live money'
}

/** Alpaca accounts only, live money first, then paper; API order within each. */
export function tradableAccounts(accounts) {
  const list = (accounts || []).filter(isAlpacaAccount)
  return [...list.filter(a => !a.alpaca_paper), ...list.filter(a => a.alpaca_paper)]
}

/** The preferred account when it is tradable, else the first tradable one. */
export function pickAccountId(accounts, preferredId) {
  const list = tradableAccounts(accounts)
  const hit = list.find(a => String(a.id) === String(preferredId ?? ''))
  return hit ? hit.id : (list[0]?.id ?? null)
}

/** Running stock instances on this account: they may act on the position. */
export function instancesTradingAccount(instances, brokerageId) {
  if (brokerageId == null) return []
  return (instances || [])
    .filter(i => i && String(i.brokerage_id ?? '') === String(brokerageId)
      && i.runCommand && i.kind !== 'kalshi' && i.kind !== 'crypto')
    .map(i => String(i.name || i.id))
}

/** "<name> trades this account and may act on this position", or ''. */
export function instanceNotice(names) {
  const list = (names || []).filter(Boolean)
  if (!list.length) return ''
  if (list.length === 1) return `${list[0]} trades this account and may act on this position`
  const who = `${list.slice(0, -1).join(', ')} and ${list[list.length - 1]}`
  return `${who} trade this account and may act on this position`
}

// ── Numbers ──────────────────────────────────────────────────────────────────

/**
 * A typed amount: commas and a leading `$` are ignored. Returns
 * { empty, ok, value, places } — `places` counts decimals after dropping
 * trailing zeros, as the server's Decimal.normalize() does.
 */
export function parseDecimal(text) {
  const t = String(text ?? '').trim().replace(/,/g, '').replace(/^\$/, '')
  if (t === '') return { empty: true, ok: false, value: null, places: 0 }
  if (!/^\d*\.?\d*$/.test(t) || t === '.') return { empty: false, ok: false, value: null, places: 0 }
  const value = Number(t)
  if (!Number.isFinite(value)) return { empty: false, ok: false, value: null, places: 0 }
  const frac = t.includes('.') ? t.split('.')[1].replace(/0+$/, '') : ''
  return { empty: false, ok: true, value, places: frac.length }
}

/** A held quantity as exact plain text (never exponent form), for "Sell all". */
export function plainQtyText(qty) {
  const n = Number(qty)
  if (qty == null || !Number.isFinite(n)) return ''
  let s = String(Math.abs(n))
  if (/e/i.test(s)) s = Math.abs(n).toFixed(QTY_MAX_DECIMALS).replace(/\.?0+$/, '')
  return s
}

/** `$250`, `$250.50` — whole dollars without cents, for button labels. */
function dollarsShort(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return Number.isInteger(n) ? `$${n.toLocaleString('en-US')}` : fmtUsd(n)
}

// ── The ticket ───────────────────────────────────────────────────────────────

/**
 * A ticket: { symbol, side: 'buy'|'sell', sizeMode: 'shares'|'dollars',
 * amount: string, orderType: 'market'|'limit', limitPrice: string,
 * tif: 'day'|'gtc', extendedHours: boolean }
 */
export function newTicket(symbol, side = 'buy') {
  return {
    symbol: normalizeSymbol(symbol),
    side: side === 'sell' ? 'sell' : 'buy',
    sizeMode: 'shares',
    amount: '',
    orderType: 'market',
    limitPrice: '',
    tif: 'day',
    extendedHours: false,
  }
}

/** Dollar amounts and fractional shares are day orders only. */
export function isFractionalTicket(t) {
  if (t?.sizeMode === 'dollars') return true
  const a = parseDecimal(t?.amount)
  return a.ok && !Number.isInteger(a.value)
}

export function gtcAllowed(t) {
  return !isFractionalTicket(t)
}

export function extendedHoursAllowed(t) {
  return t?.orderType === 'limit' && t?.tif === 'day'
}

/**
 * The first reason the server would refuse this ticket, or ''.
 * Same rules and words as build_manual_order.
 */
export function ticketIssue(t) {
  const symbol = normalizeSymbol(t?.symbol)
  if (!symbol) return 'Enter a stock symbol'
  if (!SYMBOL_RE.test(symbol)) return "Enter a stock symbol like AAPL. Options and crypto aren't supported here"
  if (t.side !== 'buy' && t.side !== 'sell') return 'Choose buy or sell'
  if (t.orderType !== 'market' && t.orderType !== 'limit') return 'Order type must be market or limit'
  if (t.tif !== 'day' && t.tif !== 'gtc') return 'Time in force must be day or gtc'

  const dollars = t.sizeMode === 'dollars'
  const amount = parseDecimal(t.amount)
  if (amount.empty) return 'Enter a number of shares or a dollar amount'
  if (!amount.ok) return dollars ? 'Dollar amount must be a number' : 'Shares must be a number'
  if (dollars) {
    if (amount.value <= 0) return 'The dollar amount must be more than zero'
    if (amount.places > 2) return "The dollar amount can't go past cents"
    if (t.orderType !== 'market') return 'Dollar amounts work with market orders only'
    if (t.tif !== 'day') return 'Dollar amounts are day orders only'
  } else {
    if (amount.value <= 0) return 'Shares must be more than zero'
    if (amount.places > QTY_MAX_DECIMALS) return `Shares can have at most ${QTY_MAX_DECIMALS} decimal places`
    if (!Number.isInteger(amount.value) && t.tif !== 'day') return 'Fractional shares are day orders only'
  }

  if (t.orderType === 'limit') {
    const limit = parseDecimal(t.limitPrice)
    if (!limit.ok || limit.value <= 0) return 'Enter a limit price above zero'
    if (limit.value >= 1 && limit.places > 2) return 'Limit prices of $1 or more go to the cent'
    if (limit.value < 1 && limit.places > 4) return 'Limit prices under $1 go to 4 decimal places at most'
  }

  if (t.extendedHours && !extendedHoursAllowed(t)) return 'Extended hours needs a limit order for the day'
  return ''
}

/** The POST /brokerages/{id}/orders body for a valid ticket. */
export function orderRequestBody(t, clientOrderId) {
  const body = {
    symbol: normalizeSymbol(t.symbol),
    side: t.side,
    type: t.orderType,
    time_in_force: t.tif,
    client_order_id: clientOrderId,
  }
  const amount = parseDecimal(t.amount).value
  if (t.sizeMode === 'dollars') body.notional = amount
  else body.qty = amount
  if (t.orderType === 'limit') body.limit_price = parseDecimal(t.limitPrice).value
  if (t.extendedHours && extendedHoursAllowed(t)) body.extended_hours = true
  return body
}

/** One id per ticket, so a retried tap can't place the order twice. */
export function newClientOrderId(randomUUID = globalThis.crypto?.randomUUID?.bind(globalThis.crypto)) {
  let hex = ''
  if (typeof randomUUID === 'function') hex = String(randomUUID()).replace(/-/g, '')
  while (hex.length < 32) hex += Math.floor(Math.random() * 16).toString(16)
  return `web-${hex.slice(0, 32)}`
}

/** The price an estimate uses: the limit for a limit order, else the last price. */
export function estimatePrice(t, lastPrice) {
  if (t?.orderType === 'limit') {
    const limit = parseDecimal(t.limitPrice)
    if (limit.ok && limit.value > 0) return limit.value
  }
  const last = Number(lastPrice)
  return lastPrice != null && Number.isFinite(last) && last > 0 ? last : null
}

/**
 * { label: 'Estimated cost'|'Estimated proceeds', amount, shares, price }.
 * Shares × price, or the dollar amount (and the shares it buys at the price).
 */
export function ticketEstimate(t, lastPrice) {
  const label = t?.side === 'sell' ? 'Estimated proceeds' : 'Estimated cost'
  const price = estimatePrice(t, lastPrice)
  const a = parseDecimal(t?.amount)
  if (!a.ok || a.value <= 0) return { label, amount: null, shares: null, price }
  if (t.sizeMode === 'dollars') {
    return { label, amount: a.value, shares: price ? a.value / price : null, price }
  }
  return { label, amount: price ? a.value * price : null, shares: a.value, price }
}

/** `3 shares`, `1 share`, `0.5 shares`, or `$250.00`. */
export function sizeText(t) {
  const a = parseDecimal(t?.amount)
  if (!a.ok) return '—'
  if (t.sizeMode === 'dollars') return fmtUsd(a.value)
  return `${plainQtyText(a.value)} ${a.value === 1 ? 'share' : 'shares'}`
}

/** The confirm control names the action: "Buy 3 AAPL", "Sell $250 of NVDA". */
export function confirmLabel(t) {
  const verb = t?.side === 'sell' ? 'Sell' : 'Buy'
  const symbol = normalizeSymbol(t?.symbol)
  const a = parseDecimal(t?.amount)
  if (!a.ok) return `${verb} ${symbol}`
  if (t.sizeMode === 'dollars') return `${verb} ${dollarsShort(a.value)} of ${symbol}`
  return `${verb} ${plainQtyText(a.value)} ${symbol}`
}

/** "You hold 3 sh", or '' when nothing is held. */
export function heldLine(qty) {
  const n = Number(qty)
  if (qty == null || !Number.isFinite(n) || n === 0) return ''
  return `You hold ${n < 0 ? '−' : ''}${plainQtyText(n)} sh${n < 0 ? ' short' : ''}`
}

/** A sell for more shares than are held would open a short position. */
export function sellsMoreThanHeld(t, heldQty, lastPrice) {
  if (t?.side !== 'sell') return false
  const held = Math.max(0, Number(heldQty) || 0)
  const est = ticketEstimate(t, lastPrice)
  if (est.shares == null) return false
  return est.shares > held + 1e-9
}

export function typeLabel(type) {
  return type === 'limit' ? 'Limit' : 'Market'
}

export function tifLabel(tif) {
  return tif === 'gtc' ? 'Good till canceled' : 'Day'
}

/** Label/value rows for the review step, in reading order. */
export function reviewRows(t, { account = null, lastPrice = null } = {}) {
  const est = ticketEstimate(t, lastPrice)
  const rows = [
    { label: 'Account', value: account?.account_name || '—' },
    { label: 'Money', value: account ? accountModeLabel(account) : '—' },
    { label: 'Side', value: t.side === 'sell' ? 'Sell' : 'Buy' },
    { label: 'Symbol', value: normalizeSymbol(t.symbol) },
    { label: t.sizeMode === 'dollars' ? 'Amount' : 'Shares', value: sizeText(t) },
    { label: 'Order type', value: typeLabel(t.orderType) },
  ]
  if (t.orderType === 'limit') rows.push({ label: 'Limit price', value: fmtPrice(parseDecimal(t.limitPrice).value) })
  rows.push({ label: 'Time in force', value: tifLabel(t.tif) })
  if (t.extendedHours && extendedHoursAllowed(t)) rows.push({ label: 'Extended hours', value: 'Yes' })
  if (t.sizeMode === 'dollars' && est.shares != null) {
    rows.push({ label: 'Estimated shares', value: `≈ ${est.shares.toFixed(est.shares >= 10 ? 2 : 4)}` })
  }
  rows.push({ label: est.label, value: est.amount == null ? '—' : fmtUsd(est.amount) })
  return rows
}

/** Where the estimate's price came from, for the line under it. */
export function estimateBasis(t, lastPrice) {
  const price = estimatePrice(t, lastPrice)
  if (price == null) return 'No recent price to estimate from'
  return t?.orderType === 'limit' ? `At your limit of ${fmtPrice(price)}` : `At the last price of ${fmtPrice(price)}`
}

// ── Responses ────────────────────────────────────────────────────────────────

function detailOf(body) {
  const d = body?.detail
  if (typeof d === 'string' && d.trim()) return d.trim()
  if (Array.isArray(d) && d.length) {
    const msg = d[0]?.msg
    if (typeof msg === 'string' && msg.trim()) return msg.trim()
  }
  return ''
}

/** The words to show when placing failed: the server's detail when it sent one. */
export function placeOrderFailure(status, body) {
  const detail = detailOf(body)
  if (detail) return detail
  if (!status) return "Couldn't reach the server. The order was not confirmed — check Orders before trying again"
  if (status === 401) return 'Your session expired. Sign in again before placing the order'
  if (status >= 500) return UNREACHABLE
  return `Couldn't place the order (HTTP ${status})`
}

export function cancelOrderFailure(status, body) {
  const detail = detailOf(body)
  if (detail) return detail
  if (!status) return "Couldn't reach the server. The cancel was not confirmed — check Orders"
  if (status === 401) return 'Your session expired. Sign in again'
  return `Couldn't cancel the order (HTTP ${status})`
}

/** Alpaca's status as a word: `accepted` → Accepted, `pending_new` → Pending new. */
export function orderStatusWord(order) {
  const s = String(order?.status || '').replace(/_/g, ' ').trim()
  return s ? s[0].toUpperCase() + s.slice(1) : 'Sent'
}

/** "Buy 3 sh AAPL" or "Sell $250.00 of NVDA" for a returned order. */
export function placedOrderLine(order) {
  const verb = String(order?.side || '').toLowerCase() === 'sell' ? 'Sell' : 'Buy'
  const symbol = normalizeSymbol(order?.symbol)
  const qty = order?.qty == null ? null : Number(order.qty)
  if (qty != null && Number.isFinite(qty)) return `${verb} ${plainQtyText(qty)} sh ${symbol}`
  const notional = order?.notional == null ? null : Number(order.notional)
  if (notional != null && Number.isFinite(notional)) return `${verb} ${fmtUsd(notional)} of ${symbol}`
  return `${verb} ${symbol}`
}

/** The question before a cancel: "Cancel the buy order for 2 sh QQQ at $505.00?" */
export function cancelPrompt(order) {
  const side = String(order?.side || '').toLowerCase() === 'sell' ? 'sell' : 'buy'
  const symbol = normalizeSymbol(order?.symbol)
  const qty = order?.qty == null ? null : Number(order.qty)
  const notional = order?.notional == null ? null : Number(order.notional)
  const size = qty != null && Number.isFinite(qty)
    ? `${plainQtyText(qty)} sh ${symbol}`
    : (notional != null && Number.isFinite(notional) ? `${fmtUsd(notional)} of ${symbol}` : symbol)
  const limit = order?.limit_price == null ? null : Number(order.limit_price)
  const stop = order?.stop_price == null ? null : Number(order.stop_price)
  const at = limit != null && Number.isFinite(limit)
    ? ` at ${fmtPrice(limit)}`
    : (stop != null && Number.isFinite(stop) ? ` with a ${fmtPrice(stop)} stop` : '')
  return `Cancel the ${side} order for ${size}${at}?`
}
