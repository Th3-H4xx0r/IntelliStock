/**
 * Option-aware display rules for the Live Trading terminal.
 *
 * Positions carry asset_class, side, multiplier, underlying, strike and expiry
 * from live_broker_fetch (spec 2026-09-24 section 6.1). recent_trades rows may
 * carry asset_class too, but broker.py-written rows carry none, so a row
 * without asset_class falls back to the OCC symbol shape. That is a display
 * fallback only; the engine identifies contracts by Alpaca's contract fields
 * (spec section 9, fix 10).
 *
 * Money the broker reports (market_value, unrealized_pnl) is already in
 * dollars, contract multiplier included. Nothing here multiplies it. Only
 * client-side qty x price totals are multiplied.
 */

export const OPTION_MULTIPLIER = 100

// Root (1-6), YYMMDD, C|P, strike x 1000 in 8 digits.
const OCC_SYMBOL_RE = /^([A-Z][A-Z0-9]{0,5})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/

export function parseOccSymbol(symbol) {
  const m = OCC_SYMBOL_RE.exec(String(symbol ?? '').trim().toUpperCase())
  if (!m) return null
  return {
    underlying: m[1],
    expiry: `20${m[2]}-${m[3]}-${m[4]}`,
    optionType: m[5] === 'P' ? 'put' : 'call',
    strike: Number(m[6]) / 1000,
  }
}

export function isOptionRow(row) {
  const assetClass = String(row?.asset_class ?? '').trim().toLowerCase()
  if (assetClass) return assetClass === 'us_option'
  return parseOccSymbol(row?.symbol) !== null
}

export function contractMultiplier(row) {
  if (!isOptionRow(row)) return 1
  const m = Number(row?.multiplier)
  return row?.multiplier != null && Number.isFinite(m) && m > 0 ? m : OPTION_MULTIPLIER
}

export function isShortPosition(row) {
  const side = String(row?.side ?? '').trim().toLowerCase()
  if (side) return side === 'short'
  return Number(row?.qty) < 0
}

export function quantityLabel(row) {
  return isOptionRow(row) ? 'Contracts' : 'Shares'
}

/** Options: unsigned whole contracts (the SHORT badge carries the sign). */
export function displayQuantity(row, maxFractionDigits = 8) {
  if (row?.qty == null || row.qty === '') return '—'
  const n = Number(row.qty)
  if (!Number.isFinite(n)) return '—'
  if (isOptionRow(row)) return String(Math.abs(Math.trunc(n)))
  return n.toLocaleString(undefined, { maximumFractionDigits: maxFractionDigits })
}

/** Fill total. Identical to the old qty x price for stock; x100 for options. */
export function tradeTotal(trade) {
  return (Number(trade?.qty) || 0) * (Number(trade?.price) || 0) * contractMultiplier(trade)
}

/**
 * The close_position command is for the equity book only: it refuses option
 * contracts, which are closed by a buy-to-close order instead. A Close on any
 * option, long or short, would be a dead button. The wheel lane manages its
 * own buy-backs; the operator's lever is Halt.
 */
export function canClosePosition(row) {
  return !isOptionRow(row)
}

/** Symbols worth a /symbol-historicals call: stock only. */
export function historicalsSymbols(positions) {
  return (Array.isArray(positions) ? positions : [])
    .filter(p => p?.symbol && !isOptionRow(p))
    .map(p => p.symbol)
}

function fmtStrike(strike) {
  return Number.isInteger(strike) ? `$${strike}` : `$${strike.toFixed(2)}`
}

/** "APH $130 Put · 2026-10-02" for an option row, '' otherwise. */
export function describeOption(row) {
  if (!isOptionRow(row)) return ''
  const occ = parseOccSymbol(row?.symbol) || {}
  const underlying = String(row?.underlying || occ.underlying || '')
  const rawStrike = row?.strike != null && Number.isFinite(Number(row.strike)) ? Number(row.strike) : occ.strike
  const type = String(row?.option_type || occ.optionType || '').toLowerCase()
  const expiry = String(row?.expiry || occ.expiry || '')
  const parts = []
  if (underlying) parts.push(underlying)
  if (rawStrike != null && Number.isFinite(rawStrike)) parts.push(fmtStrike(rawStrike))
  if (type) parts.push(type === 'put' ? 'Put' : 'Call')
  return `${parts.join(' ')}${expiry ? ` · ${expiry}` : ''}`
}
