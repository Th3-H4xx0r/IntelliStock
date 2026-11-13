/**
 * Pure helpers for the wheel's scan rows and the order review shown before
 * an approval is sent. No Vue and no fetch, so `node --test` covers them.
 *
 * Ported from the iOS app:
 *  - ios/IntelliStock/Features/Swing/Model/SwingPresentation.swift
 *      wheelScanSignal(_:in:), wheelScanStatus(_:signal:pendingIds:),
 *      swingWheelLead, swingWheelDetails, swingProposalFields
 *  - ios/IntelliStock/Features/Swing/Views/SwingOrderReviewSheet.swift
 *      SwingPutPlan, SwingOrderCard.figures, SwingOrderPriceLine
 *  - ios/IntelliStock/Features/Stock/Model/StockModel.swift
 *      StockModel.series(from:range:now:), stockExpiryText
 *  - ios/IntelliStock/Core/Charts/ChartGeometry.swift  paddedBounds
 */

import { fmtUsd } from './swing.js'

// -- Field readers (SwingRepository.swift swingNum / swingInt / swingStr) -----

/** A number, or a numeric string; null for anything else. */
export function num(value) {
  if (value == null || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

function int(value) {
  const n = num(value)
  return n == null ? null : Math.trunc(n)
}

function str(value) {
  return value == null ? '' : String(value)
}

function proposalOf(signal) {
  return signal?.proposal && typeof signal.proposal === 'object' ? signal.proposal : {}
}

/** "swing" | "wheel"; an empty lane reads as swing (SwingSignal.init). */
export function laneOf(signal) {
  return str(signal?.lane) || 'swing'
}

/** An empty status reads as pending (SwingSignal.init). */
export function statusOf(signal) {
  return str(signal?.status) || 'pending'
}

export function isWheel(signal) {
  return laneOf(signal) === 'wheel'
}

/** `String.dsSentenceCased`: first character upper-cased, the rest as is. */
export function sentenceCased(text) {
  const s = str(text)
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s
}

// -- Recent signals (SwingRepository.recentSignals) ---------------------------

/**
 * GET /instances/{id}/swing/signals?limit=200 (every status): a bare list or
 * {signals: [...]}; rows without an id are dropped.
 */
export function normalizeRecentSignals(payload) {
  const rows = Array.isArray(payload) ? payload : (Array.isArray(payload?.signals) ? payload.signals : [])
  return rows.filter(r => r && typeof r === 'object' && str(r.id) !== '')
}

// -- Wheel scan rows ----------------------------------------------------------

/** The signal a scan row produced: the same wheel symbol, session and strike. */
export function wheelScanSignal(scan, signals) {
  const scanStrike = num(scan?.strike)
  for (const s of signals || []) {
    if (!isWheel(s) || str(s.symbol) !== str(scan?.symbol) || str(s.session) !== str(scan?.session)) continue
    const a = num(proposalOf(s).strike)
    if (a == null && scanStrike == null) return s
    if (a != null && scanStrike != null && Math.abs(a - scanStrike) < 0.005) return s
  }
  return null
}

/**
 * A scan row's badge: its signal's live status when it has one, else the
 * scan log's own (which is written at scan time and never updated).
 * Returns { label, tone: 'good'|'waiting'|'bad'|'neutral', reviewable }.
 * `reviewable` means the signal is pending in the live queue: the row jumps
 * to its signal card.
 */
export function wheelScanStatus(scan, signal, pendingIds) {
  const pending = pendingIds instanceof Set ? pendingIds : new Set(pendingIds || [])
  if (signal) {
    if (pending.has(signal.id)) return { label: 'Pending', tone: 'waiting', reviewable: true }
    const status = statusOf(signal)
    switch (status) {
      case 'pending': return { label: 'Decided', tone: 'neutral', reviewable: false }
      case 'rejected': return { label: 'Rejected', tone: 'bad', reviewable: false }
      case 'failed': return { label: 'Failed', tone: 'bad', reviewable: false }
      case 'auto_approved': return { label: 'Auto-approved', tone: 'good', reviewable: false }
      case 'approved_half': return { label: 'Approved ½', tone: 'good', reviewable: false }
      case 'submitted':
      case 'approved':
      case 'placed':
        return { label: sentenceCased(status), tone: 'good', reviewable: false }
      default: return { label: sentenceCased(status), tone: 'neutral', reviewable: false }
    }
  }
  const status = str(scan?.status)
  switch (status) {
    case 'pending': return { label: 'Pending', tone: 'waiting', reviewable: false }
    case 'placed': return { label: 'Placed', tone: 'good', reviewable: false }
    case 'rejected': return { label: 'Rejected', tone: 'bad', reviewable: false }
    case '': return { label: '—', tone: 'neutral', reviewable: false }
    default: return { label: sentenceCased(status), tone: 'neutral', reviewable: false }
  }
}

/** The dot and word classes for a scan tone (a dot and a word, no pills). */
export const WHEEL_SCAN_TONE_CLASSES = {
  good: { dot: 'bg-slate-300', word: 'text-slate-200' },
  waiting: { dot: 'ui-dot-warn', word: 'text-slate-200' },
  bad: { dot: 'ui-dot-error', word: 'text-loss' },
  neutral: { dot: 'ui-dot-off', word: 'text-slate-400' },
}

/** DOM id of a pending signal's card, so a scan row can jump to it. */
export function swingSignalAnchor(id) {
  return `swing-signal-${str(id).replace(/[^A-Za-z0-9_-]/g, '_')}`
}

// -- Order review --------------------------------------------------------------

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "2026-10-09" -> "Oct 9, 2026"; anything else unchanged. */
export function stockExpiryText(ymd) {
  const parts = str(ymd).split('-').map(p => Number.parseInt(p, 10))
  if (parts.length !== 3 || parts.some(n => !Number.isFinite(n)) || parts[1] < 1 || parts[1] > 12) return str(ymd)
  return `${MONTHS[parts[1] - 1]} ${parts[2]}, ${parts[0]}`
}

/** Premium for the whole order: per-share premium x 100 x contracts. */
export function creditEst(signal) {
  const p = proposalOf(signal)
  const premium = num(p.premium_est)
  const qty = int(p.qty)
  return premium == null || qty == null ? null : premium * 100 * qty
}

/** Cash a sold put ties up: strike x 100 x contracts. */
export function collateralOf(signal) {
  const p = proposalOf(signal)
  const strike = num(p.strike)
  const qty = int(p.qty)
  return strike == null || qty == null ? null : strike * 100 * qty
}

/**
 * What selling the put means (SwingPutPlan): strike, premium, breakeven
 * (strike - premium a share), and the two sentences the review leads with.
 * null for a swing signal or a put without a strike.
 */
export function putPlan(signal) {
  if (!isWheel(signal)) return null
  const p = proposalOf(signal)
  const strike = num(p.strike)
  if (strike == null || strike <= 0) return null
  const symbol = str(signal.symbol)
  const premium = num(p.premium_est)
  const contracts = Math.max(1, int(p.qty) ?? 1)
  const expiry = str(p.expiry) || null
  const breakeven = premium == null ? null : strike - premium
  const collected = premium == null ? null : premium * 100 * contracts
  const by = expiry ? ` by ${stockExpiryText(expiry)}` : ''
  const collect = collected == null ? 'Collect the premium' : `Collect about ${fmtUsd(collected)}`
  return {
    symbol,
    strike,
    premium,
    contracts,
    expiry,
    breakeven,
    collected,
    summary: `${collect} if ${symbol} stays above ${fmtUsd(strike)}${by}.`,
    assignment: `Below ${fmtUsd(strike)} you buy ${contracts * 100} shares at ${fmtUsd(strike)}.`,
  }
}

/** The two figures a put seller weighs first (swingWheelLead). */
export function wheelLead(signal) {
  const credit = creditEst(signal)
  const collateral = collateralOf(signal)
  const premiumEst = num(proposalOf(signal).premium_est)
  return {
    premium: fmtUsd(credit ?? premiumEst),
    premiumFootnote: premiumEst == null ? null : `${fmtUsd(premiumEst)} a share`,
    collateral: fmtUsd(collateral),
    collateralFootnote: credit != null && collateral != null && collateral > 0
      ? `${(credit / collateral * 100).toFixed(2)}% return`
      : null,
  }
}

/**
 * The lead figures on the review (SwingOrderCard.figures): a put shows
 * premium, collateral, breakeven and expiry; a swing order its proposal.
 * [{ label, value, foot? }]
 */
export function reviewFigures(signal) {
  const plan = putPlan(signal)
  if (plan) {
    const lead = wheelLead(signal)
    const rows = [
      { label: 'Premium', value: lead.premium, foot: lead.premiumFootnote },
      { label: 'Collateral', value: lead.collateral, foot: lead.collateralFootnote },
    ]
    if (plan.breakeven != null) rows.push({ label: 'Breakeven', value: fmtUsd(plan.breakeven) })
    if (plan.expiry) rows.push({ label: 'Expires', value: stockExpiryText(plan.expiry) })
    return rows
  }
  const p = proposalOf(signal)
  const shares = int(p.shares)
  return [
    { label: 'Entry', value: fmtUsd(p.entry) },
    { label: 'Stop', value: fmtUsd(p.stop) },
    { label: 'Target', value: fmtUsd(p.target) },
    { label: 'Shares', value: shares == null ? '—' : String(shares) },
  ]
}

/**
 * The order's remaining detail. A put: the contract, strike, expiry,
 * contracts and limit (the broker resolves the contract and the limit when
 * you approve, so a scan-made signal has neither yet), plus the scan's stock
 * price and distance out of the money. A swing order: what it costs and
 * what is at stake between the stop and the target, at the proposal's own
 * prices (the broker re-prices at approval).
 * [{ label, value, tone? }]  tone: 'gain' | 'loss'
 */
export function reviewDetails(signal) {
  const p = proposalOf(signal)
  if (isWheel(signal)) {
    const qty = int(p.qty)
    const contract = str(p.contract)
    const ctx = signal?.context && typeof signal.context === 'object' ? signal.context : {}
    const rows = [
      { label: 'Side', value: 'Sell to open' },
      { label: 'Contract', value: contract || 'Picked at approval' },
      { label: 'Strike', value: fmtUsd(p.strike) },
      { label: 'Expiry', value: str(p.expiry) || '—' },
      { label: 'Contracts', value: qty == null ? '—' : String(qty) },
      { label: 'Limit', value: num(p.limit_price) == null ? 'Set at approval' : fmtUsd(p.limit_price) },
    ]
    const stockPrice = num(ctx.stock_price)
    const otm = num(ctx.otm_pct)
    if (stockPrice != null) rows.push({ label: 'Stock at scan', value: fmtUsd(stockPrice) })
    if (otm != null) rows.push({ label: 'Out of the money', value: `${otm.toFixed(1)}%` })
    return rows
  }
  const entry = num(p.entry)
  const stop = num(p.stop)
  const target = num(p.target)
  const shares = int(p.shares)
  const rows = [{ label: 'Side', value: 'Buy' }]
  if (entry != null && shares != null) rows.push({ label: 'Est. cost', value: fmtUsd(entry * shares) })
  if (entry != null && stop != null && shares != null) {
    rows.push({ label: 'Loss at stop', value: fmtUsd(-(entry - stop) * shares), tone: 'loss' })
  }
  if (entry != null && target != null && shares != null) {
    rows.push({ label: 'Gain at target', value: fmtUsd((target - entry) * shares), tone: 'gain' })
  }
  if (entry != null && stop != null && target != null && entry - stop > 0) {
    rows.push({ label: 'Reward / risk', value: `${((target - entry) / (entry - stop)).toFixed(2)}×` })
  }
  return rows
}

/** "Sell put" for the wheel, "Buy" for a swing entry. */
export function reviewSideLabel(signal) {
  return isWheel(signal) ? 'Sell put' : 'Buy'
}

/**
 * The review's confirm button, naming the order:
 * "Approve buy 12 AMD", "Approve ½ · AMD", "Approve sell 1 XLE put".
 */
export function reviewConfirmLabel(signal, decision) {
  const sym = str(signal?.symbol)
  if (decision === 'approve_half') return `Approve ½ · ${sym}`
  const p = proposalOf(signal)
  if (isWheel(signal)) {
    const n = Math.max(1, int(p.qty) ?? 1)
    return `Approve sell ${n} ${sym} put${n === 1 ? '' : 's'}`
  }
  const shares = int(p.shares)
  return shares == null ? `Approve buy ${sym}` : `Approve buy ${shares} ${sym}`
}

/** A put whose collateral is above the account's cash (swingCollateralWarning). */
export function collateralWarning(signal, cash) {
  const collateral = collateralOf(signal)
  const c = num(cash)
  if (!isWheel(signal) || collateral == null || c == null || !(collateral > c)) return null
  return `Collateral ${fmtUsd(collateral)} is more than the account's ${fmtUsd(c)} cash. The broker picks the strike again when you approve and refuses the order if the cash can't secure it.`
}

// -- Review chart --------------------------------------------------------------

export const REVIEW_CHART_RANGES = ['1D', '1W', '1M', '3M', '1Y']

/** Poll cadence while the review is open: 10 s on 1D, 30 s otherwise. */
export function chartPollMs(range) {
  return range === '1D' ? 10000 : 30000
}

function parseTs(ts) {
  if (typeof ts === 'number') return Number.isFinite(ts) ? (ts < 1e12 ? ts * 1000 : ts) : null
  const t = Date.parse(str(ts))
  return Number.isFinite(t) ? t : null
}

/**
 * /symbol-historicals points -> { ts: ms[], vals: number[] }. Unparseable
 * timestamps are skipped; 1D is trimmed to local midnight when that leaves
 * at least two points, else the full series (StockModel.series).
 */
export function seriesFromPoints(points, range, now = new Date()) {
  const midnight = range === '1D' ? new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() : null
  const allTs = []
  const allVals = []
  const dayTs = []
  const dayVals = []
  for (const p of points || []) {
    const t = parseTs(p?.ts)
    const v = num(p?.value)
    if (t == null || v == null) continue
    allTs.push(t)
    allVals.push(v)
    if (midnight != null && t >= midnight) {
      dayTs.push(t)
      dayVals.push(v)
    }
  }
  if (midnight != null && dayVals.length >= 2) return { ts: dayTs, vals: dayVals }
  return { ts: allTs, vals: allVals }
}

/** Min/max with 6% headroom; a flat series gets 1% (or 1) each side. */
export function paddedBounds(values, padFraction = 0.06) {
  let lo = Infinity
  let hi = -Infinity
  for (const v of values || []) {
    if (!Number.isFinite(v)) continue
    if (v < lo) lo = v
    if (v > hi) hi = v
  }
  if (lo === Infinity) return { min: 0, max: 1 }
  if (lo === hi) {
    const pad = Math.abs(lo) < 1 ? 1 : Math.abs(lo) * 0.01
    return { min: lo - pad, max: hi + pad }
  }
  const pad = (hi - lo) * padFraction
  return { min: lo - pad, max: hi + pad }
}

function fmtPctAbs(v) {
  return `${Math.abs(v).toFixed(2)}%`
}

/**
 * The line over the chart, following the scrub (SwingOrderPriceLine):
 * with a strike, the cushion above or below it; otherwise the change since
 * the range began. { price, text, tone: 'gain'|'loss'|'flat' } or null.
 */
export function priceLine(vals, index, strike) {
  if (!Array.isArray(vals) || vals.length < 2) return null
  const i = Number.isInteger(index) && index >= 0 && index < vals.length ? index : vals.length - 1
  const shown = vals[i]
  const k = num(strike)
  if (k != null && k > 0) {
    const cushion = (shown - k) / k * 100
    return {
      price: fmtUsd(shown),
      text: `${fmtPctAbs(cushion)} ${cushion >= 0 ? 'above' : 'below'} strike`,
      tone: cushion >= 0 ? 'gain' : 'loss',
    }
  }
  const change = shown - vals[0]
  const pct = vals[0] !== 0 ? change / vals[0] * 100 : 0
  const sign = change < 0 ? '-' : '+'
  return {
    price: fmtUsd(shown),
    text: `${sign}${fmtUsd(Math.abs(change))} (${pct < 0 ? '-' : '+'}${fmtPctAbs(pct)})`,
    tone: change > 0 ? 'gain' : change < 0 ? 'loss' : 'flat',
  }
}

/** Round-number axis ticks inside [min, max], about `count` of them. */
export function niceTicks(min, max, count = 4) {
  const span = max - min
  if (!Number.isFinite(span) || span <= 0) return Number.isFinite(min) ? [min] : []
  const raw = span / count
  const mag = 10 ** Math.floor(Math.log10(raw))
  const norm = raw / mag
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag
  const ticks = []
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) {
    ticks.push(Number(v.toFixed(10)))
  }
  return ticks
}
