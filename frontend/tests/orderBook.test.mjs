import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  isOpenOrder, defaultOrdersMode, orderSideType, orderQtyLabel, orderPriceLabel,
  orderStatusLabel, orderStatusClass, orderTimestamp, orderTimeLabel,
} from '../src/utils/orderBook.js'

const base = {
  id: 'o1', symbol: 'QQQ', side: 'buy', type: 'limit', status: 'new',
  qty: 3, notional: null, filled_qty: 0, filled_avg_price: null,
  limit_price: 510.5, stop_price: null, submitted_at: '2026-11-12T14:31:05.123Z',
  filled_at: null, canceled_at: null, asset_class: 'us_equity',
}
const o = (over) => ({ ...base, ...over })

test('open statuses are working orders; finished ones are not', () => {
  for (const s of ['new', 'accepted', 'pending_new', 'partially_filled', 'held']) assert.equal(isOpenOrder(o({ status: s })), true, s)
  for (const s of ['filled', 'canceled', 'expired', 'rejected', 'replaced']) assert.equal(isOpenOrder(o({ status: s })), false, s)
})

test('the first tab is Open only when something is working', () => {
  assert.equal(defaultOrdersMode({ open: [base], recent: [] }), 'open')
  assert.equal(defaultOrdersMode({ open: [], recent: [base] }), 'recent')
  assert.equal(defaultOrdersMode(null), 'recent')
})

test('side and type read as sentence case', () => {
  assert.equal(orderSideType(base), 'Buy · Limit')
  assert.equal(orderSideType(o({ side: 'sell', type: 'stop_limit' })), 'Sell · Stop limit')
  assert.equal(orderSideType(o({ type: 'trailing_stop' })), 'Buy · Trailing stop')
})

test('size covers shares, contracts, dollar orders and partial fills', () => {
  assert.equal(orderQtyLabel(base), '3 sh')
  assert.equal(orderQtyLabel(o({ asset_class: 'us_option', qty: 2 })), '2 ct')
  assert.equal(orderQtyLabel(o({ qty: null, notional: 250 })), '$250.00')
  assert.equal(orderQtyLabel(o({ status: 'partially_filled', filled_qty: 1 })), '1 of 3 sh')
  assert.equal(orderQtyLabel(o({ qty: 0.4925 })), '0.4925 sh')
  assert.equal(orderQtyLabel(o({ status: 'filled', filled_qty: 3 })), '3 sh')
})

test('price is the average fill once filled, else the limit or stop, else Market', () => {
  assert.equal(orderPriceLabel(o({ status: 'filled', filled_qty: 3, filled_avg_price: 509.9 })), '$509.90')
  assert.equal(orderPriceLabel(base), '$510.50')
  assert.equal(orderPriceLabel(o({ type: 'stop', limit_price: null, stop_price: 480 })), '$480.00 stop')
  assert.equal(orderPriceLabel(o({ type: 'stop_limit', limit_price: 479, stop_price: 480 })), '$480.00 stop')
  assert.equal(orderPriceLabel(o({ type: 'market', limit_price: null })), 'Market')
})

test('status words, and partial outcomes say they part filled', () => {
  assert.equal(orderStatusLabel(base), 'Open')
  assert.equal(orderStatusLabel(o({ status: 'partially_filled', filled_qty: 1 })), 'Partially filled')
  assert.equal(orderStatusLabel(o({ status: 'filled' })), 'Filled')
  assert.equal(orderStatusLabel(o({ status: 'canceled' })), 'Canceled')
  assert.equal(orderStatusLabel(o({ status: 'canceled', filled_qty: 1 })), 'Canceled, part filled')
  assert.equal(orderStatusLabel(o({ status: 'done_for_day' })), 'Done for day')
  assert.equal(orderStatusLabel(o({ status: 'some_new_status' })), 'Some new status')
})

test('orders that ended without trading are muted', () => {
  assert.equal(orderStatusClass(o({ status: 'canceled' })), 'text-slate-500')
  assert.equal(orderStatusClass(o({ status: 'canceled', filled_qty: 1 })), 'text-slate-200')
  assert.equal(orderStatusClass(o({ status: 'filled', filled_qty: 3 })), 'text-slate-200')
})

test('time uses the fill or cancel time when there is one', () => {
  assert.equal(orderTimestamp(base), base.submitted_at)
  assert.equal(orderTimestamp(o({ filled_at: '2026-11-12T15:00:00Z' })), '2026-11-12T15:00:00Z')
  assert.equal(orderTimestamp(o({ canceled_at: '2026-11-12T16:00:00Z' })), '2026-11-12T16:00:00Z')
})

test('time labels go from relative to a date after two days', () => {
  const now = Date.parse('2026-11-12T18:00:00Z')
  assert.equal(orderTimeLabel('2026-11-12T17:59:40Z', now), 'Just now')
  assert.equal(orderTimeLabel('2026-11-12T17:48:00Z', now), '12m ago')
  assert.equal(orderTimeLabel('2026-11-12T15:00:00Z', now), '3h ago')
  assert.equal(orderTimeLabel('2026-11-11T12:00:00Z', now), 'Yesterday')
  assert.equal(orderTimeLabel('2026-11-03T15:00:00Z', now), 'Nov 3')
  assert.equal(orderTimeLabel(null, now), '')
})
