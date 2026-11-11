// Pure dashboard analytics, ported from the iOS app so both dashboards agree:
//   ios/IntelliStock/Features/Dashboard/Model/PortfolioAnalytics.swift
//   ios/IntelliStock/Features/Dashboard/Model/AccountHoldingsModel.swift
//   ios/IntelliStock/Features/Dashboard/Model/DashboardInsightsModel.swift
// Inputs are plain values; nothing here fetches.

import { parseOccSymbol } from './optionPositions.js'

/** Major index proxies, in display order. */
export const INDEX_SYMBOLS = [
  { symbol: 'SPY', label: 'S&P 500' },
  { symbol: 'QQQ', label: 'Nasdaq' },
  { symbol: 'DIA', label: 'Dow' },
  { symbol: 'IWM', label: 'Russell 2000' },
]

/** The 11 SPDR sector ETFs. */
export const SECTOR_ETFS = [
  { symbol: 'XLK', label: 'Technology' },
  { symbol: 'XLF', label: 'Financials' },
  { symbol: 'XLV', label: 'Health care' },
  { symbol: 'XLY', label: 'Consumer discretionary' },
  { symbol: 'XLC', label: 'Communication' },
  { symbol: 'XLI', label: 'Industrials' },
  { symbol: 'XLP', label: 'Consumer staples' },
  { symbol: 'XLE', label: 'Energy' },
  { symbol: 'XLU', label: 'Utilities' },
  { symbol: 'XLRE', label: 'Real estate' },
  { symbol: 'XLB', label: 'Materials' },
]

export function isOccOptionSymbol(symbol) {
  return parseOccSymbol(symbol) != null
}

/** First-to-last percent change, or null with fewer than 2 points or a zero start. */
export function pctChangeOf(values) {
  if (!values || values.length < 2 || values[0] === 0) return null
  return (values[values.length - 1] / values[0] - 1) * 100
}

/**
 * Largest holding's weight (0-100), position count, Herfindahl index (0-1)
 * and a 0-100 diversification score (100 = perfectly even).
 */
export function concentration(values) {
  const pos = (values || []).filter(v => v > 0)
  const total = pos.reduce((a, b) => a + b, 0)
  if (total <= 0 || !pos.length) return { topWeight: 0, count: 0, hhi: 0, score: 0 }
  const weights = pos.map(v => v / total)
  const hhi = weights.reduce((a, w) => a + w * w, 0)
  const top = Math.max(...weights)
  const score = Math.min(Math.max(Math.round((1 - hhi) * 100), 0), 100)
  return { topWeight: top * 100, count: pos.length, hhi, score }
}

/**
 * Volatility and max drawdown (%) and Sharpe from an equity curve.
 * Returns are period-over-period, annualized by sqrt(252), risk-free 0.
 * A single-period move above 50% is a deposit or data gap, not a return.
 */
export function riskMetrics(values) {
  if (!values || values.length < 2) return { volatility: 0, maxDrawdown: 0, sharpe: null, points: 0 }
  const returns = []
  for (let i = 1; i < values.length; i++) {
    if (values[i - 1] > 0) {
      const r = values[i] / values[i - 1] - 1
      if (Math.abs(r) <= 0.5) returns.push(r)
    }
  }
  let peak = values[0]
  let maxDd = 0
  for (const v of values) {
    if (v > peak) peak = v
    if (peak > 0) maxDd = Math.max(maxDd, (peak - v) / peak)
  }
  if (!returns.length) return { volatility: 0, maxDrawdown: maxDd * 100, sharpe: null, points: values.length }
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length
  const variance = returns.reduce((a, r) => a + (r - mean) ** 2, 0) / returns.length
  const stdev = variance <= 0 ? 0 : Math.sqrt(variance)
  const annualize = Math.sqrt(252)
  return {
    volatility: stdev * annualize * 100,
    maxDrawdown: maxDd * 100,
    sharpe: stdev === 0 ? null : (mean / stdev) * annualize,
    points: values.length,
  }
}

/**
 * Market value by sector, biggest first. Blank sectors fold into "Other".
 * Options are left out: their value is premium, not sector exposure.
 */
export function aggregateBySector(valueBySymbol, sectorBySymbol) {
  const order = []
  const bySector = new Map()
  let total = 0
  for (const { symbol, value } of valueBySymbol || []) {
    if (isOccOptionSymbol(symbol) || !(value > 0)) continue
    const raw = String(sectorBySymbol?.[symbol] ?? '').trim()
    const key = raw || 'Other'
    if (!bySector.has(key)) order.push(key)
    bySector.set(key, (bySector.get(key) || 0) + value)
    total += value
  }
  if (total <= 0) return []
  return order
    .map(sector => ({ sector, value: bySector.get(sector), pct: (bySector.get(sector) / total) * 100 }))
    .sort((a, b) => b.value - a.value)
}

/**
 * A holding's P&L for the row. Daily comes from the 1D sparkline
 * (start of day to now); Total is the lifetime unrealized P&L.
 */
export function holdingPnl(position, spark, mode) {
  if (mode !== 'daily') return { hasPnl: true, abs: position.unrealizedPnl, pct: position.unrealizedPnlPct }
  if (!spark || spark.length < 2 || spark[0] === 0) return { hasPnl: false, abs: 0, pct: 0 }
  const ratio = spark[spark.length - 1] / spark[0]
  return { hasPnl: true, abs: position.marketValue * (1 - 1 / ratio), pct: (ratio - 1) * 100 }
}

/** A /symbol-historicals range whose bars suit a holding `days` old. */
export function rangeForHoldingAge(days) {
  if (days <= 6) return '1W'
  if (days <= 31) return '1M'
  if (days <= 93) return '3M'
  if (days <= 366) return '1Y'
  return 'ALL'
}

/** Values from local midnight on; the full series if fewer than 2 bars are after midnight. */
export function sliceSinceMidnight(points, now = new Date()) {
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const all = []
  const since = []
  for (const p of points || []) {
    const v = Number(p.value)
    all.push(v)
    const t = Date.parse(p.ts)
    if (!Number.isFinite(t) || t >= midnight) since.push(v)
  }
  const vals = since.length >= 2 ? since : all
  return vals.length >= 2 ? vals : null
}

/**
 * A holding's line since it was bought, with the ends pinned to the average
 * entry and the current price so its direction matches the P&L.
 */
export function totalSpark(points, openedAt, avgEntry, lastPrice) {
  const openMs = openedAt instanceof Date ? openedAt.getTime() : NaN
  let vals = (points || [])
    .filter(p => !Number.isFinite(openMs) || Date.parse(p.ts) >= openMs)
    .map(p => Number(p.value))
  if (Number.isFinite(avgEntry) && avgEntry > 0) vals = [avgEntry, ...vals]
  if (Number.isFinite(lastPrice) && lastPrice > 0) vals = [...vals, lastPrice]
  return vals.length >= 2 ? vals : null
}

/** Holdings ranked by today's % move, biggest gainer first. */
export function todaysMovers(sparks, order) {
  const out = []
  for (const symbol of order || []) {
    const pct = pctChangeOf(sparks?.[symbol])
    if (pct != null) out.push({ symbol, pct })
  }
  return out.sort((a, b) => b.pct - a.pct)
}

// ── Formatting: one money and percent format across the dashboard ─────────────

const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 })
const MINUS = '−'

export function fmtUsd(v) {
  const n = Number(v)
  if (v == null || !Number.isFinite(n)) return '—'
  return n < 0 ? MINUS + USD.format(-n) : USD.format(n)
}

/** A share price: cents above $1, four decimals below so penny stocks don't read $0.00. */
export function fmtPrice(v) {
  const n = Number(v)
  if (v == null || !Number.isFinite(n)) return '—'
  if (Math.abs(n) >= 1 || n === 0) return fmtUsd(n)
  return `$${n.toFixed(4)}`
}

export function fmtSignedUsd(v) {
  const n = Number(v)
  if (v == null || !Number.isFinite(n)) return '—'
  return (n >= 0 ? '+' : MINUS) + USD.format(Math.abs(n))
}

export function fmtPct(v, { signed = true } = {}) {
  const n = Number(v)
  if (v == null || !Number.isFinite(n)) return '—'
  if (Math.abs(n) < 0.005) return '0.00%'
  const sign = n > 0 ? (signed ? '+' : '') : MINUS
  return `${sign}${Math.abs(n).toFixed(2)}%`
}

/** `22.4 sh`, `5.53 sh`, `5 sh` */
export function fmtQty(q) {
  const n = Number(q) || 0
  const s = Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(Math.abs(n) >= 10 ? 1 : 2)
  return `${s.includes('.') ? s.replace(/\.?0+$/, '') : s} sh`
}

/** `5m ago`, `3h ago`, `2d ago` */
export function fmtRelative(iso, now = Date.now()) {
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return ''
  const m = Math.max(0, Math.round((now - t) / 60_000))
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.round(h / 24)}d ago`
}

/** Green, red or muted text class for a signed number. */
export function signClass(v) {
  const n = Number(v)
  if (!Number.isFinite(n) || Math.abs(n) < 0.005) return 'text-slate-500'
  return n > 0 ? 'text-gain' : 'text-loss'
}

const NY_PARTS = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York', weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})

/**
 * True inside the US regular session, Mon-Fri 09:30-16:00 New York time.
 * Holidays are not modeled; this is a status hint, as on iOS.
 */
export function isMarketOpen(now = new Date()) {
  const parts = Object.fromEntries(NY_PARTS.formatToParts(now).map(p => [p.type, p.value]))
  if (parts.weekday === 'Sat' || parts.weekday === 'Sun') return false
  const minutes = Number(parts.hour) * 60 + Number(parts.minute)
  return minutes >= 9 * 60 + 30 && minutes < 16 * 60
}
