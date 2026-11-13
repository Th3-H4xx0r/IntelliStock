// Pure helpers for the web stock page and symbol search, ported from iOS:
//  - ios/IntelliStock/Features/Stock/Model/StockModel.swift
//      stockRanges, stockCompact, stockStatCells, BotTradeEvent.title/backers
//  - ios/IntelliStock/Features/SymbolSearch/Data/SymbolSearchModels.swift
//      searchQuoteFromHistory, searchSymbolsForSparklines
// No Vue and no fetch, so `node --test` covers them.

import { fmtUsd } from './dashboardAnalytics.js'
import { isOpenOrder } from './orderBook.js'

export const STOCK_RANGES = ['1D', '1W', '1M', '3M', 'YTD', '1Y', 'ALL']

export const RANGE_CAPTIONS = {
  '1D': 'Today', '1W': 'Past week', '1M': 'Past month', '3M': 'Past 3 months',
  YTD: 'Year to date', '1Y': 'Past year', ALL: 'All time',
}

function num(v) {
  const n = Number(v)
  return v == null || v === '' || typeof v === 'boolean' || !Number.isFinite(n) ? null : n
}

/** `1.23T` / `4.56B` / `7.89M` / `1.2K` / `950`. */
export function stockCompact(v) {
  const n = Number(v)
  const a = Math.abs(n)
  if (a >= 1e12) return `${(n / 1e12).toFixed(2)}T`
  if (a >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `${(n / 1e6).toFixed(2)}M`
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}K`
  return n.toFixed(0)
}

/**
 * Key statistics: label → formatted value. Zero or absent numbers are left
 * out; the range's open, high and low come from the chart's series.
 */
export function stockStatCells(info, series, range) {
  const cells = []
  const add = (label, key, format) => {
    const n = num(info?.[key])
    if (n == null || n === 0) return
    cells.push({ label, value: format(n) })
  }
  add('Prev close', 'previousClose', fmtUsd)
  const vals = series?.vals || []
  if (vals.length >= 2) {
    cells.push({ label: 'Open', value: fmtUsd(vals[0]) })
    cells.push({ label: `${range} high`, value: fmtUsd(Math.max(...vals)) })
    cells.push({ label: `${range} low`, value: fmtUsd(Math.min(...vals)) })
  }
  add('52W high', 'fiftyTwoWeekHigh', fmtUsd)
  add('52W low', 'fiftyTwoWeekLow', fmtUsd)
  add('Volume', 'volume', stockCompact)
  add('Avg volume', 'averageVolume', stockCompact)
  add('Market cap', 'marketCap', v => `$${stockCompact(v)}`)
  add('P/E', 'trailingPE', v => v.toFixed(2))
  add('Fwd P/E', 'forwardPE', v => v.toFixed(2))
  add('Beta', 'beta', v => v.toFixed(2))
  add('Analyst target', 'targetMeanPrice', fmtUsd)
  return cells
}

/** A trimmed string field of the info map, '' when absent. */
export function infoText(info, key) {
  const v = info?.[key]
  return typeof v === 'string' ? v.trim() : ''
}

/** The account's position in a symbol, or null. */
export function positionFor(positions, symbol) {
  const want = String(symbol || '').toUpperCase()
  return (positions || []).find(p => String(p?.symbol || '').toUpperCase() === want) || null
}

/** The order book narrowed to one symbol: { open, recent }. */
export function ordersForSymbol(book, symbol) {
  const want = String(symbol || '').toUpperCase()
  const mine = o => String(o?.symbol || '').toUpperCase() === want
  return {
    open: (book?.open || []).filter(mine).filter(isOpenOrder),
    recent: (book?.recent || []).filter(mine),
  }
}

/** "3 shares · avg $160.00" (short positions say so). */
export function positionLine(qty, avg) {
  const n = Number(qty) || 0
  const a = Math.abs(n)
  const count = a >= 100 ? a.toFixed(0) : String(Number(a.toFixed(a >= 10 ? 4 : 6)))
  const unit = a === 1 ? 'share' : 'shares'
  return `${count} ${unit}${n < 0 ? ' short' : ''} · avg ${fmtUsd(avg)}`
}

// ── Bot activity (GET /brokerages/{id}/bot-activity) ─────────────────────────

/** The driving strategy, else "Buy decision" / "Sell decision". */
export function botEventTitle(event) {
  const s = String(event?.strategy || '').trim()
  if (s) return s
  return String(event?.side || '').toLowerCase() === 'sell' ? 'Sell decision' : 'Buy decision'
}

/** Other strategies that voted the same way, up to 3, first-seen order. */
export function botEventBackers(event) {
  const title = botEventTitle(event)
  const seen = new Set()
  const out = []
  for (const c of event?.contributors || []) {
    const s = String(c?.strategy || '').trim()
    if (!s || s === title || seen.has(s)) continue
    seen.add(s)
    out.push(s)
    if (out.length === 3) break
  }
  return out
}

/** The event's time (ts, else created_at) as an ISO string, or null. */
export function botEventTime(event) {
  for (const v of [event?.ts, event?.created_at]) {
    if (v == null || v === '') continue
    const t = typeof v === 'number' ? (v < 1e12 ? v * 1000 : v) : Date.parse(String(v))
    if (Number.isFinite(t)) return new Date(t).toISOString()
  }
  return null
}

// ── Search ───────────────────────────────────────────────────────────────────

/** The latest price and today's change from a 1D series. */
export function searchQuote(values) {
  const vals = (values || []).map(Number).filter(Number.isFinite)
  if (!vals.length) return null
  const price = vals[vals.length - 1]
  if (vals.length < 2 || vals[0] === 0) return { price, changePct: null }
  return { price, changePct: (price - vals[0]) / vals[0] * 100 }
}

/** Unique non-empty symbols in first-seen order (one quote batch). */
export function uniqueSymbols(rows) {
  const seen = new Set()
  const out = []
  for (const r of rows || []) {
    const s = String(r?.symbol || '').trim().toUpperCase()
    if (!s || seen.has(s)) continue
    seen.add(s)
    out.push(s)
  }
  return out
}

/**
 * The user's holdings that match the query, best match first: exact symbol,
 * then symbol prefix, then symbol substring. An empty query keeps them all,
 * largest first.
 */
export function matchingHoldings(holdings, query) {
  const q = String(query || '').trim().toUpperCase()
  const list = (holdings || []).filter(h => h?.symbol)
  if (!q) return [...list].sort((a, b) => (Number(b.marketValue) || 0) - (Number(a.marketValue) || 0))
  const rank = h => {
    const s = String(h.symbol).toUpperCase()
    if (s === q) return 0
    if (s.startsWith(q)) return 1
    if (s.includes(q)) return 2
    return -1
  }
  return list
    .map(h => ({ h, r: rank(h) }))
    .filter(x => x.r >= 0)
    .sort((a, b) => a.r - b.r || (Number(b.h.marketValue) || 0) - (Number(a.h.marketValue) || 0))
    .map(x => x.h)
}

/** Search results with the symbols already listed under holdings removed. */
export function resultsWithoutHoldings(results, holdings) {
  const held = new Set((holdings || []).map(h => String(h?.symbol || '').toUpperCase()))
  return (results || []).filter(r => r?.symbol && !held.has(String(r.symbol).toUpperCase()))
}
