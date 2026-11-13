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
  assert.equal(orderQtyLabel(o({ qty: null, notional: 250, filled_qty: 0.4236 })), '0.4236 sh')
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

import {
  orderStatusDot, orderFillFraction, orderValue, orderClock, orderDayLabel, orderWhen,
  groupOrdersByDay, orderDetails,
} from '../src/utils/orderBook.js'

// Local-time instants so the day logic holds in any time zone.
const at = (y, mo, d, h = 10, mi = 0) => new Date(y, mo, d, h, mi).toISOString()
const NOW = new Date(2026, 10, 12, 15, 0).getTime()

test('status dots: working is live, fills neutral, dead ends muted, rejects red', () => {
  assert.equal(orderStatusDot(o({ status: 'new' })), 'ui-dot-live')
  assert.equal(orderStatusDot(o({ status: 'partially_filled', filled_qty: 1 })), 'ui-dot-live')
  assert.equal(orderStatusDot(o({ status: 'filled', filled_qty: 3 })), 'bg-slate-300')
  assert.equal(orderStatusDot(o({ status: 'canceled' })), 'ui-dot-off')
  assert.equal(orderStatusDot(o({ status: 'canceled', filled_qty: 1 })), 'bg-slate-300')
  assert.equal(orderStatusDot(o({ status: 'rejected' })), 'ui-dot-error')
  assert.equal(orderStatusDot(o({ status: 'pending_cancel' })), 'ui-dot-warn')
})

test('fill fraction only for partial fills', () => {
  assert.equal(orderFillFraction(o({ filled_qty: 1 })), 1 / 3)
  assert.equal(orderFillFraction(o({ filled_qty: 0 })), null)
  assert.equal(orderFillFraction(o({ filled_qty: 3 })), null)
  assert.equal(orderFillFraction(o({ qty: null, notional: 250, filled_qty: 0.4 })), null)
})

test('value is what filled, options ×100, else the dollar amount', () => {
  assert.equal(orderValue(o({ filled_qty: 2, filled_avg_price: 100 })), 200)
  assert.equal(orderValue(o({ asset_class: 'us_option', filled_qty: 2, filled_avg_price: 1.5 })), 300)
  assert.equal(orderValue(o({ qty: null, notional: 250 })), 250)
  assert.equal(orderValue(base), null)
})

test('day labels and grouping follow local days, newest first', () => {
  assert.equal(orderDayLabel(at(2026, 10, 12), NOW), 'Today')
  assert.equal(orderDayLabel(at(2026, 10, 11), NOW), 'Yesterday')
  assert.equal(orderDayLabel(at(2026, 10, 9), NOW), 'Mon, Nov 9')
  assert.equal(orderDayLabel(at(2025, 11, 30), NOW), 'Tue, Dec 30, 2025')
  const groups = groupOrdersByDay([
    o({ id: 'a', filled_at: at(2026, 10, 12, 14) }),
    o({ id: 'b', filled_at: at(2026, 10, 12, 9) }),
    o({ id: 'c', canceled_at: at(2026, 10, 11, 11) }),
    o({ id: 'd', submitted_at: at(2026, 10, 9, 10) }),
  ], NOW)
  assert.deepEqual(groups.map(g => [g.label, g.orders.map(x => x.id)]), [
    ['Today', ['a', 'b']], ['Yesterday', ['c']], ['Mon, Nov 9', ['d']],
  ])
})

test('clock and open-order time read as times, with a date when not today', () => {
  assert.match(orderClock(at(2026, 10, 12, 9, 5)), /^9:05\s?AM$/)
  assert.match(orderWhen(at(2026, 10, 12, 14, 30), NOW), /^2:30\s?PM$/)
  assert.match(orderWhen(at(2026, 10, 10, 9, 5), NOW), /^Nov 10, 9:05\s?AM$/)
  assert.equal(orderClock(null), '')
})

test('details list the full record and skip what is empty', () => {
  const rows = orderDetails(o({ status: 'filled', time_in_force: 'day', filled_qty: 3, filled_avg_price: 509.9, filled_at: at(2026, 10, 12, 14) }))
  const labels = rows.map(r => r.label)
  assert.deepEqual(labels, ['Order type', 'Side', 'Time in force', 'Ordered', 'Filled', 'Average fill', 'Limit price', 'Submitted', 'Filled at', 'Order ID'])
  assert.equal(rows.find(r => r.label === 'Time in force').value, 'Day')
  assert.equal(rows.find(r => r.label === 'Average fill').value, '$509.90')
  assert.equal(rows.find(r => r.label === 'Order ID').value, 'o1')
})
