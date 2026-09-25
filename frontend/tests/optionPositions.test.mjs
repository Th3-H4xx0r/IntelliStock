import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  OPTION_MULTIPLIER,
  canClosePosition,
  contractMultiplier,
  describeOption,
  displayQuantity,
  historicalsSymbols,
  isOptionRow,
  isShortPosition,
  parseOccSymbol,
  quantityLabel,
  tradeTotal,
} from '../src/utils/optionPositions.js'

// live-state position after plan A-live: Alpaca's own current_price may be null.
const SHORT_PUT = {
  symbol: 'APH261002P00130000', qty: -1, avg_entry_price: 1.23,
  last_price: null, market_value: null, unrealized_pnl: null, unrealized_pnl_pct: null,
  asset_class: 'us_option', side: 'short', multiplier: 100,
  underlying: 'APH', strike: 130, expiry: '2026-10-02',
}
const STOCK = { symbol: 'AAPL', qty: 12, avg_entry_price: 190, last_price: 200, market_value: 2400 }
// broker.py-written recent_trades rows carry no asset_class: the OCC shape is
// the only signal there.
const OPTION_TRADE = { symbol: 'APH261002P00130000', side: 'sell', qty: 1, price: 1.25 }
const STOCK_TRADE = { symbol: 'AAPL', side: 'buy', qty: 12, price: 200 }

test('parseOccSymbol reads root, expiry, type and strike', () => {
  assert.deepEqual(parseOccSymbol('APH261002P00130000'),
    { underlying: 'APH', expiry: '2026-10-02', optionType: 'put', strike: 130 })
  assert.deepEqual(parseOccSymbol('spy261218c00612500'),
    { underlying: 'SPY', expiry: '2026-12-18', optionType: 'call', strike: 612.5 })
  for (const s of ['AAPL', 'BRK.B', '', null, 'APH261002X00130000', 'TOOLONGROOT261002P00130000']) {
    assert.equal(parseOccSymbol(s), null, String(s))
  }
})

test('isOptionRow trusts asset_class first, then the OCC shape', () => {
  assert.equal(isOptionRow(SHORT_PUT), true)
  assert.equal(isOptionRow(STOCK), false)
  assert.equal(isOptionRow(OPTION_TRADE), true)
  assert.equal(isOptionRow(STOCK_TRADE), false)
  assert.equal(isOptionRow({ symbol: 'APH261002P00130000', asset_class: 'us_equity' }), false)
})

test('contractMultiplier: 100 for options unless the row says otherwise, 1 for stock', () => {
  assert.equal(contractMultiplier(SHORT_PUT), 100)
  assert.equal(contractMultiplier({ ...SHORT_PUT, multiplier: 10 }), 10)
  assert.equal(contractMultiplier({ ...SHORT_PUT, multiplier: null }), OPTION_MULTIPLIER)
  assert.equal(contractMultiplier(STOCK), 1)
})

test('isShortPosition reads side, then the sign of qty', () => {
  assert.equal(isShortPosition(SHORT_PUT), true)
  assert.equal(isShortPosition({ qty: -2 }), true)
  assert.equal(isShortPosition(STOCK), false)
  assert.equal(isShortPosition({ side: 'long', qty: -1 }), false)
})

test('labels and quantities: Contracts, unsigned whole numbers for options', () => {
  assert.equal(quantityLabel(SHORT_PUT), 'Contracts')
  assert.equal(quantityLabel(STOCK), 'Shares')
  assert.equal(displayQuantity(SHORT_PUT), '1')
  assert.equal(displayQuantity(STOCK), '12')
  assert.equal(displayQuantity({ qty: null, symbol: 'AAPL' }), '—')
})

test('tradeTotal multiplies option fills by 100 and leaves stock unchanged', () => {
  assert.equal(tradeTotal(OPTION_TRADE), 125)
  assert.equal(tradeTotal(STOCK_TRADE), 2400)
  assert.equal(tradeTotal({ symbol: 'AAPL', qty: null, price: 5 }), 0)
})

test('Close is offered for stock only: close_position reads the equity book', () => {
  assert.equal(canClosePosition(SHORT_PUT), false)
  assert.equal(canClosePosition({ ...SHORT_PUT, side: 'long', qty: 1 }), false)
  assert.equal(canClosePosition(STOCK), true)
})

test('historicalsSymbols never asks /symbol-historicals for an OCC contract', () => {
  assert.deepEqual(historicalsSymbols([SHORT_PUT, STOCK, { symbol: '' }, null]), ['AAPL'])
  assert.deepEqual(historicalsSymbols(undefined), [])
})

test('describeOption uses the position fields, falling back to the symbol', () => {
  assert.equal(describeOption(SHORT_PUT), 'APH $130 Put · 2026-10-02')
  assert.equal(describeOption(OPTION_TRADE), 'APH $130 Put · 2026-10-02')
  assert.equal(describeOption({ symbol: 'SPY261218C00612500' }), 'SPY $612.50 Call · 2026-12-18')
  assert.equal(describeOption(STOCK), '')
})

test('a short put with no quote yields no numbers to invent', () => {
  // The view renders these through fmtMoney / fmtPct, which print an em dash
  // for null. None of the helpers may turn a missing quote into 0.
  assert.equal(SHORT_PUT.last_price, null)
  assert.equal(displayQuantity(SHORT_PUT), '1')
  assert.equal(describeOption(SHORT_PUT), 'APH $130 Put · 2026-10-02')
  assert.equal(canClosePosition(SHORT_PUT), false)
})
