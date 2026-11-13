import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  STOCK_RANGES, stockCompact, stockStatCells, infoText, positionFor, ordersForSymbol, positionLine,
  botEventTitle, botEventBackers, botEventTime, searchQuote, uniqueSymbols, matchingHoldings,
  resultsWithoutHoldings,
} from '../src/utils/stock.js'

test('ranges match iOS', () => {
  assert.deepEqual(STOCK_RANGES, ['1D', '1W', '1M', '3M', 'YTD', '1Y', 'ALL'])
})

test('stockCompact', () => {
  assert.equal(stockCompact(2.345e12), '2.35T')
  assert.equal(stockCompact(4.56e9), '4.56B')
  assert.equal(stockCompact(7.891e6), '7.89M')
  assert.equal(stockCompact(1234), '1.2K')
  assert.equal(stockCompact(950), '950')
})

test('stat cells: zero/absent skipped, range open/high/low from the series', () => {
  const info = { previousClose: 188.5, marketCap: 2.9e12, trailingPE: 31.234, beta: 0, volume: null, fiftyTwoWeekHigh: 199.62 }
  const cells = stockStatCells(info, { vals: [189, 192, 187.5, 190] }, '1D')
  assert.deepEqual(cells, [
    { label: 'Prev close', value: '$188.50' },
    { label: 'Open', value: '$189.00' },
    { label: '1D high', value: '$192.00' },
    { label: '1D low', value: '$187.50' },
    { label: '52W high', value: '$199.62' },
    { label: 'Market cap', value: '$2.90T' },
    { label: 'P/E', value: '31.23' },
  ])
  assert.deepEqual(stockStatCells({}, null, '1D'), [])
})

test('infoText trims and ignores non-strings', () => {
  assert.equal(infoText({ name: '  Apple Inc. ' }, 'name'), 'Apple Inc.')
  assert.equal(infoText({ name: 3 }, 'name'), '')
  assert.equal(infoText(null, 'name'), '')
})

test('position and orders for one symbol', () => {
  const positions = [{ symbol: 'QQQ', qty: 3 }, { symbol: 'aapl', qty: 2 }]
  assert.equal(positionFor(positions, 'AAPL').qty, 2)
  assert.equal(positionFor(positions, 'MSFT'), null)
  const book = {
    open: [{ id: 'o1', symbol: 'AAPL', status: 'new' }, { id: 'o2', symbol: 'QQQ', status: 'new' },
      { id: 'o3', symbol: 'AAPL', status: 'filled' }],
    recent: [{ id: 'c1', symbol: 'AAPL', status: 'filled' }, { id: 'c2', symbol: 'GLD', status: 'filled' }],
  }
  const mine = ordersForSymbol(book, 'aapl')
  assert.deepEqual(mine.open.map(o => o.id), ['o1'])
  assert.deepEqual(mine.recent.map(o => o.id), ['c1'])
  assert.deepEqual(ordersForSymbol(null, 'AAPL'), { open: [], recent: [] })
})

test('positionLine', () => {
  assert.equal(positionLine(82, 160), '82 shares · avg $160.00')
  assert.equal(positionLine(1, 12.5), '1 share · avg $12.50')
  assert.equal(positionLine(-2, 10), '2 shares short · avg $10.00')
  assert.equal(positionLine(1.358695652, 184), '1.358696 shares · avg $184.00')
})

test('bot events: title, backers, time', () => {
  const e = {
    side: 'sell', strategy: '', ts: null, created_at: '2026-11-12T15:00:00Z',
    contributors: [{ strategy: 'Momentum' }, { strategy: 'Momentum' }, { strategy: 'Mean reversion' },
      { strategy: 'Nexus' }, { strategy: 'Breakout' }, { strategy: '' }],
  }
  assert.equal(botEventTitle(e), 'Sell decision')
  assert.equal(botEventTitle({ side: 'buy', strategy: ' Momentum ' }), 'Momentum')
  assert.deepEqual(botEventBackers(e), ['Momentum', 'Mean reversion', 'Nexus'])
  assert.deepEqual(botEventBackers({ strategy: 'Momentum', contributors: [{ strategy: 'Momentum' }, { strategy: 'Nexus' }] }), ['Nexus'])
  assert.equal(botEventTime(e), '2026-11-12T15:00:00.000Z')
  assert.equal(botEventTime({ ts: 'garbage' }), null)
})

test('search: quotes, unique symbols, holdings first', () => {
  assert.deepEqual(searchQuote([100, 102, 105]), { price: 105, changePct: 5 })
  assert.deepEqual(searchQuote([7]), { price: 7, changePct: null })
  assert.equal(searchQuote([]), null)
  assert.deepEqual(uniqueSymbols([{ symbol: 'aapl' }, { symbol: 'AAPL' }, { symbol: '' }, { symbol: 'MSFT' }]), ['AAPL', 'MSFT'])

  const holdings = [
    { symbol: 'QQQ', marketValue: 1500 }, { symbol: 'TQQQ', marketValue: 900 },
    { symbol: 'GLD', marketValue: 1400 }, { symbol: 'AAPL', marketValue: 300 },
  ]
  assert.deepEqual(matchingHoldings(holdings, '').map(h => h.symbol), ['QQQ', 'GLD', 'TQQQ', 'AAPL'])
  assert.deepEqual(matchingHoldings(holdings, 'qqq').map(h => h.symbol), ['QQQ', 'TQQQ'])
  assert.deepEqual(matchingHoldings(holdings, 'tq').map(h => h.symbol), ['TQQQ'])
  assert.deepEqual(matchingHoldings(holdings, 'zzz'), [])

  const results = [{ symbol: 'QQQ', name: 'Invesco QQQ' }, { symbol: 'QQQM', name: 'Invesco QQQ M' }]
  assert.deepEqual(resultsWithoutHoldings(results, [{ symbol: 'QQQ' }]).map(r => r.symbol), ['QQQM'])
})
