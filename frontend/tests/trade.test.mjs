import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  normalizeSymbol, isTradableSymbol, accountModeLabel, tradableAccounts, pickAccountId,
  instancesTradingAccount, instanceNotice, parseDecimal, plainQtyText, newTicket,
  isFractionalTicket, gtcAllowed, extendedHoursAllowed, ticketIssue, orderRequestBody,
  newClientOrderId, estimatePrice, ticketEstimate, sizeText, confirmLabel, heldLine,
  sellsMoreThanHeld, reviewRows, estimateBasis, placeOrderFailure, cancelOrderFailure,
  orderStatusWord, placedOrderLine, cancelPrompt,
} from '../src/utils/trade.js'

const t = (over) => ({ ...newTicket('AAPL', 'buy'), amount: '3', ...over })

const LIVE = { id: 'a1', account_name: 'Alpaca Live', brokerage_type: 'alpaca', alpaca_paper: false }
const PAPER = { id: 'a2', account_name: 'Paper Test', brokerage_type: 'alpaca', alpaca_paper: true }
const KALSHI = { id: 'k1', account_name: 'Kalshi', brokerage_type: 'kalshi' }

test('symbols: trimmed and upper-cased; equities only', () => {
  assert.equal(normalizeSymbol('  brk.b '), 'BRK.B')
  for (const s of ['AAPL', 'brk.b', 'BF-B', 'X']) assert.equal(isTradableSymbol(s), true, s)
  for (const s of ['', 'AAPL261218C00200000', 'BTC/USD', '1ABC', 'TOOLONGSYMBOL']) assert.equal(isTradableSymbol(s), false, s)
})

test('accounts: Alpaca only, live money first; mode said plainly', () => {
  assert.deepEqual(tradableAccounts([PAPER, KALSHI, LIVE]).map(a => a.id), ['a1', 'a2'])
  assert.equal(accountModeLabel(LIVE), 'Live money')
  assert.equal(accountModeLabel(PAPER), 'Paper')
  assert.equal(pickAccountId([PAPER, LIVE, KALSHI], 'a2'), 'a2')
  assert.equal(pickAccountId([PAPER, LIVE, KALSHI], 'k1'), 'a1')  // Kalshi can't trade here
  assert.equal(pickAccountId([KALSHI], 'k1'), null)
})

test('instances on the account: running stock instances only, one quiet line', () => {
  const instances = [
    { id: 'i1', name: 'Strategy EB', brokerage_id: 'a1', runCommand: true },
    { id: 'i2', name: 'Stopped one', brokerage_id: 'a1', runCommand: false },
    { id: 'i3', name: 'Other account', brokerage_id: 'a2', runCommand: true },
    { id: 'i4', name: 'Crypto bot', brokerage_id: 'a1', runCommand: true, kind: 'crypto' },
  ]
  assert.deepEqual(instancesTradingAccount(instances, 'a1'), ['Strategy EB'])
  assert.equal(instanceNotice(['Strategy EB']), 'Strategy EB trades this account and may act on this position')
  assert.equal(instanceNotice(['A', 'B', 'C']), 'A, B and C trade this account and may act on this position')
  assert.equal(instanceNotice([]), '')
})

test('parseDecimal: commas and $ ignored; trailing zeros are not decimals', () => {
  assert.deepEqual(parseDecimal(' $1,250.50 '), { empty: false, ok: true, value: 1250.5, places: 1 })
  assert.deepEqual(parseDecimal('1.358695652'), { empty: false, ok: true, value: 1.358695652, places: 9 })
  assert.equal(parseDecimal('').empty, true)
  for (const bad of ['abc', '1.2.3', '-1', '.', '1e5']) assert.equal(parseDecimal(bad).ok, false, bad)
})

test('plainQtyText: exact, never exponent form', () => {
  assert.equal(plainQtyText(1.358695652), '1.358695652')
  assert.equal(plainQtyText(3), '3')
  assert.equal(plainQtyText(1e-7), '0.0000001')
  assert.equal(plainQtyText(null), '')
})

test('fractional tickets are day only; extended hours needs limit + day', () => {
  assert.equal(isFractionalTicket(t({ amount: '1.5' })), true)
  assert.equal(isFractionalTicket(t({ sizeMode: 'dollars', amount: '250' })), true)
  assert.equal(gtcAllowed(t({ amount: '2' })), true)
  assert.equal(gtcAllowed(t({ amount: '0.5' })), false)
  assert.equal(extendedHoursAllowed(t({ orderType: 'limit', tif: 'day' })), true)
  assert.equal(extendedHoursAllowed(t({ orderType: 'market' })), false)
})

test('ticketIssue mirrors the server, message for message', () => {
  const cases = [
    [t({}), ''],
    [t({ symbol: '' }), 'Enter a stock symbol'],
    [t({ symbol: 'AAPL261218C00200000' }), "Enter a stock symbol like AAPL. Options and crypto aren't supported here"],
    [t({ amount: '' }), 'Enter a number of shares or a dollar amount'],
    [t({ amount: 'x' }), 'Shares must be a number'],
    [t({ amount: '0' }), 'Shares must be more than zero'],
    [t({ amount: '0.0000000001' }), 'Shares can have at most 9 decimal places'],
    [t({ amount: '1.5', tif: 'gtc' }), 'Fractional shares are day orders only'],
    [t({ sizeMode: 'dollars', amount: 'x' }), 'Dollar amount must be a number'],
    [t({ sizeMode: 'dollars', amount: '0' }), 'The dollar amount must be more than zero'],
    [t({ sizeMode: 'dollars', amount: '10.005' }), "The dollar amount can't go past cents"],
    [t({ sizeMode: 'dollars', amount: '100', orderType: 'limit', limitPrice: '10' }), 'Dollar amounts work with market orders only'],
    [t({ sizeMode: 'dollars', amount: '100', tif: 'gtc' }), 'Dollar amounts are day orders only'],
    [t({ orderType: 'limit', limitPrice: '' }), 'Enter a limit price above zero'],
    [t({ orderType: 'limit', limitPrice: '0' }), 'Enter a limit price above zero'],
    [t({ orderType: 'limit', limitPrice: '190.123' }), 'Limit prices of $1 or more go to the cent'],
    [t({ orderType: 'limit', limitPrice: '0.12345' }), 'Limit prices under $1 go to 4 decimal places at most'],
    [t({ extendedHours: true }), 'Extended hours needs a limit order for the day'],
    [t({ orderType: 'limit', limitPrice: '190', tif: 'gtc', extendedHours: true }), 'Extended hours needs a limit order for the day'],
    [t({ orderType: 'limit', limitPrice: '190.12', tif: 'gtc' }), ''],
    [t({ amount: '1.358695652' }), ''],
  ]
  for (const [ticket, want] of cases) assert.equal(ticketIssue(ticket), want, JSON.stringify(ticket))
})

test('orderRequestBody: shares or dollars, limit only for limit orders', () => {
  assert.deepEqual(orderRequestBody(t({}), 'web-1'), {
    symbol: 'AAPL', side: 'buy', type: 'market', time_in_force: 'day', client_order_id: 'web-1', qty: 3,
  })
  assert.deepEqual(orderRequestBody(t({ side: 'sell', sizeMode: 'dollars', amount: '250' }), 'web-2'), {
    symbol: 'AAPL', side: 'sell', type: 'market', time_in_force: 'day', client_order_id: 'web-2', notional: 250,
  })
  const lim = orderRequestBody(t({ orderType: 'limit', limitPrice: '189.5', extendedHours: true }), 'web-3')
  assert.equal(lim.limit_price, 189.5)
  assert.equal(lim.extended_hours, true)
  assert.equal('notional' in lim, false)
  // A stale extended-hours flag never rides along on an order that can't use it.
  assert.equal('extended_hours' in orderRequestBody(t({ extendedHours: true }), 'web-4'), false)
  // The exact held quantity goes out unchanged.
  assert.equal(orderRequestBody(t({ side: 'sell', amount: plainQtyText(1.358695652) }), 'w').qty, 1.358695652)
})

test('client order ids: web-<32 hex>, unique, valid for the server', () => {
  const a = newClientOrderId()
  const b = newClientOrderId()
  assert.match(a, /^web-[0-9a-f]{32}$/)
  assert.notEqual(a, b)
  assert.ok(a.length <= 48)
  assert.match(newClientOrderId(null), /^web-[0-9a-f]{32}$/)  // no crypto.randomUUID
})

test('estimates: shares × price (limit for a limit order), or the dollar amount', () => {
  assert.equal(estimatePrice(t({}), 190), 190)
  assert.equal(estimatePrice(t({ orderType: 'limit', limitPrice: '185' }), 190), 185)
  assert.equal(estimatePrice(t({}), null), null)
  assert.deepEqual(ticketEstimate(t({}), 190), { label: 'Estimated cost', amount: 570, shares: 3, price: 190 })
  const sell = ticketEstimate(t({ side: 'sell', sizeMode: 'dollars', amount: '250' }), 125)
  assert.deepEqual(sell, { label: 'Estimated proceeds', amount: 250, shares: 2, price: 125 })
  assert.equal(ticketEstimate(t({ amount: '' }), 190).amount, null)
  assert.equal(estimateBasis(t({}), 190), 'At the last price of $190.00')
  assert.equal(estimateBasis(t({ orderType: 'limit', limitPrice: '185' }), 190), 'At your limit of $185.00')
  assert.equal(estimateBasis(t({ sizeMode: 'dollars', amount: '250' }), 190), 'At the last price of $190.00')
  assert.equal(estimateBasis(t({}), null), 'No recent price to estimate from')
})

test('labels: the confirm names the action', () => {
  assert.equal(confirmLabel(t({})), 'Buy 3 AAPL')
  assert.equal(confirmLabel(t({ side: 'sell', sizeMode: 'dollars', amount: '250', symbol: 'nvda' })), 'Sell $250 of NVDA')
  assert.equal(confirmLabel(t({ sizeMode: 'dollars', amount: '250.5' })), 'Buy $250.50 of AAPL')
  assert.equal(confirmLabel(t({ side: 'sell', amount: '1.358695652' })), 'Sell 1.358695652 AAPL')
  assert.equal(sizeText(t({ amount: '1' })), '1 share')
  assert.equal(sizeText(t({ amount: '0.5' })), '0.5 shares')
  assert.equal(sizeText(t({ sizeMode: 'dollars', amount: '250' })), '$250.00')
})

test('held quantity and overselling', () => {
  assert.equal(heldLine(3), 'You hold 3 sh')
  assert.equal(heldLine(1.358695652), 'You hold 1.358695652 sh')
  assert.equal(heldLine(0), '')
  assert.equal(heldLine(null), '')
  assert.equal(sellsMoreThanHeld(t({ side: 'sell', amount: '4' }), 3, 100), true)
  assert.equal(sellsMoreThanHeld(t({ side: 'sell', amount: '3' }), 3, 100), false)
  assert.equal(sellsMoreThanHeld(t({ side: 'sell', sizeMode: 'dollars', amount: '500' }), 3, 100), true)
  assert.equal(sellsMoreThanHeld(t({ side: 'buy', amount: '40' }), 3, 100), false)
})

test('review rows: account and money first, the estimate last', () => {
  const rows = reviewRows(t({ orderType: 'limit', limitPrice: '185', tif: 'gtc' }), { account: LIVE, lastPrice: 190 })
  assert.deepEqual(rows.map(r => r.label), [
    'Account', 'Money', 'Side', 'Symbol', 'Shares', 'Order type', 'Limit price', 'Time in force', 'Estimated cost',
  ])
  assert.equal(rows[1].value, 'Live money')
  assert.equal(rows[rows.length - 1].value, '$555.00')
  const dollars = reviewRows(t({ side: 'sell', sizeMode: 'dollars', amount: '250' }), { account: PAPER, lastPrice: 125 })
  assert.equal(dollars.find(r => r.label === 'Money').value, 'Paper')
  assert.equal(dollars.find(r => r.label === 'Amount').value, '$250.00')
  assert.equal(dollars.find(r => r.label === 'Estimated shares').value, '≈ 2.0000')
  assert.equal(dollars[dollars.length - 1].label, 'Estimated proceeds')
})

test('failures show the server detail; fallbacks say the order was not confirmed', () => {
  assert.equal(placeOrderFailure(400, { detail: 'Alpaca rejected the order: insufficient buying power' }),
    'Alpaca rejected the order: insufficient buying power')
  assert.equal(placeOrderFailure(422, { detail: [{ msg: 'Input should be a valid dictionary' }] }), 'Input should be a valid dictionary')
  assert.match(placeOrderFailure(502, null), /not confirmed — check Orders/)
  assert.match(placeOrderFailure(0, null), /Couldn't reach the server/)
  assert.equal(placeOrderFailure(418, {}), "Couldn't place the order (HTTP 418)")
  assert.equal(cancelOrderFailure(400, { detail: "Alpaca couldn't cancel this order: filled" }), "Alpaca couldn't cancel this order: filled")
  assert.match(cancelOrderFailure(0, null), /not confirmed/)
})

test('returned orders: a status word and a plain line', () => {
  assert.equal(orderStatusWord({ status: 'pending_new' }), 'Pending new')
  assert.equal(orderStatusWord({ status: 'filled' }), 'Filled')
  assert.equal(orderStatusWord({}), 'Sent')
  assert.equal(placedOrderLine({ side: 'buy', symbol: 'aapl', qty: 3 }), 'Buy 3 sh AAPL')
  assert.equal(placedOrderLine({ side: 'sell', symbol: 'NVDA', qty: null, notional: 250 }), 'Sell $250.00 of NVDA')
})

test('cancel prompt names the order', () => {
  assert.equal(cancelPrompt({ side: 'buy', symbol: 'QQQ', qty: 2, limit_price: 505 }), 'Cancel the buy order for 2 sh QQQ at $505.00?')
  assert.equal(cancelPrompt({ side: 'sell', symbol: 'GLD', qty: 6, stop_price: 232.5 }), 'Cancel the sell order for 6 sh GLD with a $232.50 stop?')
  assert.equal(cancelPrompt({ side: 'buy', symbol: 'SPY', qty: null, notional: 250 }), 'Cancel the buy order for $250.00 of SPY?')
})
