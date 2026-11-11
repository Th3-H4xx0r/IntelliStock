import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  concentration,
  riskMetrics,
  aggregateBySector,
  pctChangeOf,
  holdingPnl,
  rangeForHoldingAge,
  sliceSinceMidnight,
  totalSpark,
  isMarketOpen,
  todaysMovers,
  fmtQty,
  fmtPct,
  fmtSignedUsd,
  fmtPrice,
} from '../src/utils/dashboardAnalytics.js'

test('formatting: compact quantities, true minus signs, flat moves read 0.00%', () => {
  assert.deepEqual([5.52733135, 14.83668904, 100, 1, 22.4, 120.5].map(fmtQty),
    ['5.53 sh', '14.8 sh', '100 sh', '1 sh', '22.4 sh', '121 sh'])
  assert.equal(fmtPct(-6.701), '−6.70%')
  assert.equal(fmtPct(0.001), '0.00%')
  assert.equal(fmtSignedUsd(-145.05), '−$145.05')
  assert.equal(fmtSignedUsd(12), '+$12.00')
  assert.equal(fmtPrice(0.0042), '$0.0042')
  assert.equal(fmtPrice(365.4), '$365.40')
})

// These mirror ios/IntelliStock/Features/Dashboard/Model/PortfolioAnalytics.swift
// and AccountHoldingsModel.swift, so the web and iOS dashboards agree.

test('concentration: weights, HHI and a 0-100 diversification score', () => {
  const s = concentration([50, 30, 20, 0, -5])
  assert.equal(s.count, 3)
  assert.equal(s.topWeight, 50)
  assert.ok(Math.abs(s.hhi - 0.38) < 1e-9)
  assert.equal(s.score, 62)
  assert.deepEqual(concentration([]), { topWeight: 0, count: 0, hhi: 0, score: 0 })
})

test('riskMetrics: drawdown, volatility, Sharpe; skips >50% jumps as deposits', () => {
  const r = riskMetrics([100, 110, 99, 120])
  assert.equal(r.points, 4)
  assert.ok(Math.abs(r.maxDrawdown - 10) < 1e-9)
  assert.ok(r.volatility > 0)
  assert.ok(r.sharpe != null)
  // A deposit (100 -> 300) is not a return; drawdown still sees the curve.
  const d = riskMetrics([100, 300, 300])
  assert.equal(d.volatility, 0)
  assert.equal(d.sharpe, null)
  assert.equal(riskMetrics([5]).points, 0)
})

test('aggregateBySector: groups by sector, folds blanks into Other, drops options and non-positive', () => {
  const slices = aggregateBySector(
    [
      { symbol: 'GLD', value: 200 },
      { symbol: 'XLE', value: 100 },
      { symbol: 'XOM', value: 100 },
      { symbol: 'BIL', value: 100 },
      { symbol: 'SPY260116C00600000', value: 999 },
      { symbol: 'NEG', value: -10 },
    ],
    { GLD: '', XLE: 'Energy', XOM: 'Energy', BIL: null },
  )
  assert.deepEqual(slices.map(s => [s.sector, s.value]), [['Other', 300], ['Energy', 200]])
  assert.ok(Math.abs(slices[0].pct - 60) < 1e-9)
  assert.deepEqual(aggregateBySector([], {}), [])
})

test('pctChangeOf: first to last, null when not computable', () => {
  assert.ok(Math.abs(pctChangeOf([100, 110]) - 10) < 1e-9)
  assert.equal(pctChangeOf([0, 5]), null)
  assert.equal(pctChangeOf([5]), null)
})

test('holdingPnl: daily from the 1D spark, total from unrealized P&L', () => {
  const p = { marketValue: 110, unrealizedPnl: -4, unrealizedPnlPct: -3.5 }
  const daily = holdingPnl(p, [100, 110], 'daily')
  assert.equal(daily.hasPnl, true)
  assert.ok(Math.abs(daily.abs - 10) < 1e-9)
  assert.ok(Math.abs(daily.pct - 10) < 1e-9)
  assert.equal(holdingPnl(p, null, 'daily').hasPnl, false)
  assert.deepEqual(holdingPnl(p, null, 'total'), { hasPnl: true, abs: -4, pct: -3.5 })
})

test('rangeForHoldingAge matches the backend bar resolutions', () => {
  assert.deepEqual([3, 20, 60, 200, 900].map(rangeForHoldingAge), ['1W', '1M', '3M', '1Y', 'ALL'])
})

test('sliceSinceMidnight keeps post-midnight bars, falls back when fewer than two', () => {
  const now = new Date(2026, 10, 11, 15, 0)
  const pts = [
    { ts: new Date(2026, 10, 10, 15, 0).toISOString(), value: 1 },
    { ts: new Date(2026, 10, 11, 9, 30).toISOString(), value: 2 },
    { ts: new Date(2026, 10, 11, 10, 0).toISOString(), value: 3 },
  ]
  assert.deepEqual(sliceSinceMidnight(pts, now), [2, 3])
  assert.deepEqual(sliceSinceMidnight(pts.slice(0, 2), now), [1, 2])
  assert.equal(sliceSinceMidnight(pts.slice(0, 1), now), null)
})

test('totalSpark starts at the open, anchored to entry and last price', () => {
  const pts = [
    { ts: '2026-09-01T14:00:00Z', value: 90 },
    { ts: '2026-09-20T14:00:00Z', value: 95 },
    { ts: '2026-10-01T14:00:00Z', value: 97 },
  ]
  assert.deepEqual(totalSpark(pts, new Date('2026-09-16T13:48:00Z'), 100, 92), [100, 95, 97, 92])
  // No open date: the full series, still anchored.
  assert.deepEqual(totalSpark(pts, null, 100, 92), [100, 90, 95, 97, 92])
})

test('isMarketOpen reads New York wall-clock time, weekdays 09:30-16:00', () => {
  assert.equal(isMarketOpen(new Date('2026-11-11T15:00:00Z')), true)   // Wed 10:00 EST
  assert.equal(isMarketOpen(new Date('2026-11-11T14:29:00Z')), false)  // Wed 09:29 EST
  assert.equal(isMarketOpen(new Date('2026-11-11T21:00:00Z')), false)  // Wed 16:00 EST
  assert.equal(isMarketOpen(new Date('2026-07-15T13:30:00Z')), true)   // Wed 09:30 EDT
  assert.equal(isMarketOpen(new Date('2026-11-14T16:00:00Z')), false)  // Saturday
})

test('todaysMovers ranks holdings by intraday move, skipping short series', () => {
  const movers = todaysMovers({ A: [100, 101], B: [100, 95], C: [50], D: [100, 110] }, ['A', 'B', 'C', 'D'])
  assert.deepEqual(movers.map(m => m.symbol), ['D', 'A', 'B'])
})
