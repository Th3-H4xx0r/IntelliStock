import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  ALL_KEY, ANALYSIS_RANGES, FAILED_STROKE, HOLLOW_STROKE, MARKER_LEGEND, NO_ANALYSIS_TEXT, UNEXPLAINED_KEY,
  analyzableResults, apiFailure, categoryChips, categoryStyle, confidenceText, eventMarkers, explainBody,
  explainableCount, familyOf, filterEvents, filterKeyOf, fmtEventDate, isAnalyzableSymbol, isCryptoSymbol,
  isHollow, isMissingRoute, markerStyle, pendingCount, quickPicks, rangeFrom, reconcileSelection, retryable,
  sameSymbol, settingsChanges, settingsDirty, settingsDraft, settingsIssues, settingsPayload, sortNewestFirst,
  sourceRow, splitRows, statusText, stripMax, symbolKey, symbolProfile, withLoadedSymbol,
} from '../src/utils/moveAnalysis.js'

// Shapes from docs/superpowers/specs/2026-11-14-why-it-moved-design.md (API).
const ev = (date, status, category = null, extra = {}) => ({
  id: `NVDA|${date}`, symbol: 'NVDA', date, close: 100, ret_pct: -5, market_pct: -1, sector_pct: -0.5,
  specific_pct: -3.5, status, category, category_label: category ? `${category} label` : null,
  headline: status === 'explained' ? 'Headline' : null, sources: [], ...extra,
})

const CATEGORIES = [
  { key: 'earnings', label: 'Earnings' }, { key: 'analyst', label: 'Analysts' },
  { key: 'macro', label: 'Macro' }, { key: 'market', label: 'Market' }, { key: 'no_catalyst', label: 'No catalyst' },
]

test('ranges and range from the URL', () => {
  assert.deepEqual(ANALYSIS_RANGES, ['1M', '3M', '6M', '1Y'])
  assert.equal(rangeFrom('6m'), '6M')
  assert.equal(rangeFrom('5Y'), '3M')
  assert.equal(rangeFrom(undefined), '3M')
})

test('filter buckets: explained events by category, everything else unexplained', () => {
  assert.equal(filterKeyOf(ev('2026-10-02', 'explained', 'analyst')), 'analyst')
  assert.equal(filterKeyOf(ev('2026-10-02', 'pending')), UNEXPLAINED_KEY)
  assert.equal(filterKeyOf(ev('2026-10-02', 'failed')), UNEXPLAINED_KEY)
  assert.equal(filterKeyOf(ev('2026-10-02', 'explained', null)), UNEXPLAINED_KEY)
  assert.equal(isHollow(ev('2026-10-02', 'unexplained')), true)
  assert.equal(isHollow(ev('2026-10-02', 'pending')), true)
  assert.equal(isHollow(ev('2026-10-02', 'failed')), true)
  assert.equal(isHollow(ev('2026-10-02', 'explained', 'macro')), false)
})

test('category chips: counts, API order, missing categories, then not explained', () => {
  const events = [
    ev('2026-10-02', 'explained', 'analyst'),
    ev('2026-10-09', 'explained', 'earnings'),
    ev('2026-10-12', 'explained', 'analyst'),
    ev('2026-10-20', 'explained', 'sector_peer', { category_label: 'Sector peers' }),
    ev('2026-10-25', 'pending'),
    ev('2026-11-01', 'unexplained'),
  ]
  assert.deepEqual(categoryChips(events, CATEGORIES), [
    { key: ALL_KEY, label: 'All', count: 6 },
    { key: 'earnings', label: 'Earnings', count: 1 },
    { key: 'analyst', label: 'Analysts', count: 2 },
    { key: 'sector_peer', label: 'Sector peers', count: 1 },
    { key: UNEXPLAINED_KEY, label: 'Not explained', count: 2 },
  ])
  assert.deepEqual(categoryChips([], CATEGORIES), [{ key: ALL_KEY, label: 'All', count: 0 }])
})

test('filtering keeps newest first', () => {
  const events = [
    ev('2026-09-01', 'explained', 'analyst'),
    ev('2026-11-01', 'unexplained'),
    ev('2026-10-02', 'explained', 'analyst'),
  ]
  assert.deepEqual(sortNewestFirst(events).map(e => e.date), ['2026-11-01', '2026-10-02', '2026-09-01'])
  assert.deepEqual(filterEvents(events, ALL_KEY).map(e => e.date), ['2026-11-01', '2026-10-02', '2026-09-01'])
  assert.deepEqual(filterEvents(events, 'analyst').map(e => e.date), ['2026-10-02', '2026-09-01'])
  assert.deepEqual(filterEvents(events, UNEXPLAINED_KEY).map(e => e.date), ['2026-11-01'])
  assert.deepEqual(filterEvents(events, 'earnings'), [])
})

test('markers land on their bar; off-series dates take the next bar; outside the range is dropped', () => {
  const bars = [
    { date: '2026-10-01', close: 190 },
    { date: '2026-10-02', close: 188.1 },
    { date: '2026-10-05', close: 185 },
  ]
  const events = [
    ev('2026-10-02', 'explained', 'analyst', { close: 188.1 }),
    ev('2026-10-03', 'unexplained', null, { close: null }),   // a Saturday: lands on Monday's bar
    ev('2026-09-01', 'unexplained'),                          // before the series
  ]
  const markers = eventMarkers(bars, events)
  assert.deepEqual(markers.map(m => [m.event.date, m.index, m.close]), [
    ['2026-10-02', 1, 188.1],
    ['2026-10-03', 2, 185],
  ])
  assert.deepEqual(eventMarkers([], events), [])
})

test('category families match iOS: company news, analysts and peers, macro and policy, two greys', () => {
  const fam = k => familyOf(k)
  for (const k of ['earnings', 'guidance', 'deal_product', 'management', 'capital_insider']) assert.equal(fam(k), 'company', k)
  for (const k of ['analyst', 'sector_peer']) assert.equal(fam(k), 'street', k)
  for (const k of ['macro', 'regulatory_legal']) assert.equal(fam(k), 'macro', k)
  assert.equal(fam('market'), 'market')
  assert.equal(fam('no_catalyst'), 'none')
  assert.equal(fam('something_new'), 'none')
  assert.equal(categoryStyle('earnings').label, 'Company news')
  assert.equal(categoryStyle('analyst').label, 'Analysts and peers')
  assert.equal(categoryStyle('regulatory_legal').label, 'Macro and policy')
  // Three hues, three shapes; the greys differ from each other.
  const shapes = new Set(['earnings', 'analyst', 'macro'].map(k => categoryStyle(k).shape))
  const colors = new Set(['earnings', 'analyst', 'macro'].map(k => categoryStyle(k).color))
  assert.equal(shapes.size, 3)
  assert.equal(colors.size, 3)
  assert.notEqual(categoryStyle('market').color, categoryStyle('no_catalyst').color)
  // Never the app's gain or loss colour.
  for (const k of ['earnings', 'guidance', 'analyst', 'deal_product', 'macro', 'sector_peer',
    'regulatory_legal', 'management', 'capital_insider', 'market', 'no_catalyst']) {
    assert.ok(!['#4cc38a', '#e5484d'].includes(categoryStyle(k).color), k)
  }
  assert.deepEqual(MARKER_LEGEND.slice(0, 3).map(l => l.label), ['Company news', 'Analysts and peers', 'Macro and policy'])
})

test('markers: filled when explained, red outline when failed, grey outline otherwise', () => {
  assert.deepEqual(markerStyle(ev('d', 'explained', 'analyst')), { kind: 'filled', color: categoryStyle('analyst').color, shape: 'diamond' })
  assert.deepEqual(markerStyle(ev('d', 'failed')), { kind: 'failed', color: FAILED_STROKE, shape: 'circle' })
  assert.equal(FAILED_STROKE, '#e5484d')
  assert.deepEqual(markerStyle(ev('d', 'pending')), { kind: 'hollow', color: HOLLOW_STROKE, shape: 'circle' })
  assert.deepEqual(markerStyle(ev('d', 'unexplained')), { kind: 'hollow', color: HOLLOW_STROKE, shape: 'circle' })
})

test('selection survives a re-read: the selected day stays, the filter goes to All', () => {
  const before = [ev('2026-10-02', 'pending'), ev('2026-10-09', 'unexplained'), ev('2026-09-01', 'explained', 'macro')]
  const after = [ev('2026-10-02', 'explained', 'analyst'), ev('2026-10-09', 'unexplained'), ev('2026-09-01', 'explained', 'macro')]
  // Watching "Not explained" with Oct 2 selected; it finishes explaining.
  assert.deepEqual(reconcileSelection(before, CATEGORIES, UNEXPLAINED_KEY, 'NVDA|2026-10-02'),
    { filter: UNEXPLAINED_KEY, selectedId: 'NVDA|2026-10-02' })
  assert.deepEqual(reconcileSelection(after, CATEGORIES, UNEXPLAINED_KEY, 'NVDA|2026-10-02'),
    { filter: ALL_KEY, selectedId: 'NVDA|2026-10-02' })
  // Still inside the filter: nothing changes.
  assert.deepEqual(reconcileSelection(after, CATEGORIES, UNEXPLAINED_KEY, 'NVDA|2026-10-09'),
    { filter: UNEXPLAINED_KEY, selectedId: 'NVDA|2026-10-09' })
  // The selected day is gone: the newest shown day.
  assert.deepEqual(reconcileSelection(after, CATEGORIES, 'macro', 'NVDA|2026-12-01'),
    { filter: 'macro', selectedId: 'NVDA|2026-09-01' })
  // The filter's chip is gone: All.
  assert.deepEqual(reconcileSelection(after, CATEGORIES, 'earnings', null),
    { filter: ALL_KEY, selectedId: 'NVDA|2026-10-09' })
  assert.deepEqual(reconcileSelection([], CATEGORIES, ALL_KEY, 'x'), { filter: ALL_KEY, selectedId: null })
})

test('symbols: dash spelling, crypto, what can be analyzed', () => {
  assert.equal(symbolKey(' brk.b '), 'BRK-B')
  assert.ok(sameSymbol('BRK.B', 'brk-b'))
  assert.ok(!sameSymbol('BRK.B', 'BRK.A'))
  assert.ok(!sameSymbol('', ''))
  for (const s of ['BTCUSD', 'BTC/USD', 'ethusdt', 'SOLUSDC', 'DOGEUSD']) assert.ok(isCryptoSymbol(s), s)
  for (const s of ['NVDA', 'TQQQ', 'BRK.B', 'USD', 'SUSD']) assert.ok(!isCryptoSymbol(s), s)
  for (const s of ['NVDA', 'BRK.B', 'BRK-B', 'GOOGL']) assert.ok(isAnalyzableSymbol(s), s)
  for (const s of ['BTCUSD', 'BTC/USD', 'SPY261218C00600000', '', '1ABC']) assert.ok(!isAnalyzableSymbol(s), s)
  assert.deepEqual(analyzableResults([
    { symbol: 'BTC-USD', type: 'Crypto' }, { symbol: 'COIN', type: 'Stock' }, { symbol: 'ETHUSD', type: 'Stock' },
    { symbol: 'IBIT', type: 'ETF' }, { name: 'no symbol' },
  ]).map(r => r.symbol), ['COIN', 'IBIT'])
  assert.deepEqual(withLoadedSymbol(['TQQQ', 'BRK.B'], 'BRK-B'), ['TQQQ', 'BRK-B'])
  assert.deepEqual(withLoadedSymbol(['TQQQ'], null), ['TQQQ'])
})

test('profile line: beta left out when the server could not estimate it', () => {
  assert.equal(symbolProfile({ sector: 'technology', sector_etf: 'XLK', beta: 1.62 }), 'Technology · XLK · beta 1.62')
  assert.equal(symbolProfile({ sector: 'consumer_discretionary', sector_etf: 'XLY', beta: null }), 'Consumer discretionary · XLY')
  assert.equal(symbolProfile({ sector: null, sector_etf: null, beta: null }), '')
})

test('explain body names the account when there is one', () => {
  assert.deepEqual(explainBody({ symbol: 'NVDA', range: '3M', brokerageId: 'b1' }),
    { symbol: 'NVDA', range: '3M', force: false, brokerage_id: 'b1' })
  assert.deepEqual(explainBody({ symbol: 'NVDA', range: '3M', dates: ['2026-10-02'], force: true, brokerageId: null }),
    { symbol: 'NVDA', range: '3M', force: true, dates: ['2026-10-02'] })
  assert.deepEqual(explainBody({ symbol: 'NVDA', range: '1M', dates: [] }), { symbol: 'NVDA', range: '1M', force: false })
})

test('an old server: a bare "Not Found" 404 is a missing route, not an unknown symbol', () => {
  assert.ok(isMissingRoute(404, { detail: 'Not Found' }))
  assert.ok(!isMissingRoute(404, { detail: 'No price history for ZZZZ.' }))
  assert.ok(!isMissingRoute(500, { detail: 'Not Found' }))
  assert.equal(apiFailure(404, { detail: 'Not Found' }, 'x'), NO_ANALYSIS_TEXT)
  assert.equal(NO_ANALYSIS_TEXT, 'This server has no AI analysis yet')
  assert.equal(retryable(404, { detail: 'Not Found' }), false)
  assert.equal(retryable(400, { detail: 'Enter a stock or ETF symbol, for example NVDA.' }), false)
  assert.equal(retryable(500, null), true)
  assert.equal(retryable(0, null), true)
})

test('split rows: labels, values, shares against the largest part', () => {
  const rows = splitRows({ market_pct: -0.8, sector_pct: -1.1, specific_pct: -6.0 }, 'XLK')
  assert.deepEqual(rows.map(r => [r.key, r.label, r.value]), [
    ['market', 'Market', -0.8], ['sector', 'Sector (XLK)', -1.1], ['specific', 'Stock-specific', -6],
  ])
  assert.equal(rows[2].share, 1)
  assert.ok(Math.abs(rows[0].share - 0.8 / 6) < 1e-9)
  const blank = splitRows({ market_pct: null, sector_pct: null, specific_pct: null })
  assert.equal(blank[1].label, 'Sector')
  assert.deepEqual(blank.map(r => r.share), [0, 0, 0])
})

test('strip scale, counts and words', () => {
  assert.equal(stripMax([{ ret_pct: null }, { ret_pct: -7.9 }, { ret_pct: 2 }]), 7.9)
  assert.equal(stripMax([{ ret_pct: 0.2 }]), 1)
  const events = [ev('a', 'unexplained'), ev('b', 'failed'), ev('c', 'pending'), ev('d', 'explained', 'macro')]
  assert.equal(explainableCount(events), 2)
  assert.equal(pendingCount({ pending: 3, events }), 3)
  assert.equal(pendingCount({ events }), 1)
  assert.equal(statusText({ status: 'pending' }), 'Explaining…')
  assert.equal(statusText({ status: 'failed' }), "Couldn't explain this move")
  assert.equal(statusText({ status: 'explained' }), '')
  assert.equal(confidenceText('medium'), 'Medium confidence')
  assert.equal(confidenceText(null), '')
  assert.equal(fmtEventDate('2026-10-02'), 'Oct 2')
  assert.equal(fmtEventDate('2026-10-02', { long: true }), 'Fri, Oct 2, 2026')
})

test('sources: only http(s) links become hrefs', () => {
  const row = sourceRow({ title: 'Downgrade', url: 'https://www.benzinga.com/x', source: 'Benzinga', published_at: null })
  assert.deepEqual(row, { title: 'Downgrade', url: 'https://www.benzinga.com/x', meta: 'Benzinga' })
  assert.equal(sourceRow({ title: 'Bad', url: 'javascript:alert(1)' }).url, '')
  assert.deepEqual(sourceRow({ url: 'https://news.example.com/a' }), { title: 'news.example.com', url: 'https://news.example.com/a', meta: 'news.example.com' })
})

test('api failures read the server detail', () => {
  assert.match(apiFailure(0, null, 'x'), /reach the server/)
  assert.equal(apiFailure(400, { detail: 'Choose a model in Settings first' }, 'x'), 'Choose a model in Settings first')
  assert.equal(apiFailure(422, { detail: [{ msg: 'sigma too large' }] }, 'x'), 'sigma too large')
  assert.equal(apiFailure(500, null, "Couldn't save"), "Couldn't save (HTTP 500)")
})

test('quick picks: equities only (no options, no crypto), largest first, once each', () => {
  const positions = [
    { symbol: 'QQQ', marketValue: 1500 }, { symbol: 'tqqq', marketValue: 2500 },
    { symbol: 'SPY261218C00600000', marketValue: 9000 }, { symbol: 'QQQ', marketValue: 10 },
    { symbol: 'BTCUSD', marketValue: 8000 }, { symbol: 'BTC/USD', marketValue: 7000 },
    { symbol: 'BRK.B', marketValue: 900 }, { symbol: 'BRK-B', marketValue: 100 },
  ]
  // The largest holding here is crypto, so the default symbol is TQQQ.
  assert.deepEqual(quickPicks(positions), ['TQQQ', 'QQQ', 'BRK.B'])
  assert.deepEqual(quickPicks(null), [])
})

test('settings: limits, payload, draft and dirty', () => {
  assert.deepEqual(settingsIssues({ sigma: 2.5, min_move_pct: 3 }), { sigma: '', min_move_pct: '' })
  assert.deepEqual(settingsIssues({ sigma: 0.5, min_move_pct: 30 }), {
    sigma: 'Between 1.0 and 6.0', min_move_pct: 'Between 0.5 and 25.0',
  })
  assert.deepEqual(settingsIssues({ sigma: '', min_move_pct: 'x' }), { sigma: 'Enter a number', min_move_pct: 'Enter a number' })
  assert.deepEqual(settingsIssues({ sigma: 1, min_move_pct: 25 }), { sigma: '', min_move_pct: '' })

  const draft = settingsDraft({ model_id: 'abc', model_name: 'Opus', sigma: 2.5, min_move_pct: 3, models: [] })
  assert.deepEqual(draft, { model_id: 'abc', sigma: 2.5, min_move_pct: 3 })
  assert.deepEqual(settingsDraft({ model_id: null }), { model_id: '', sigma: 2.5, min_move_pct: 3 })
  assert.deepEqual(settingsPayload({ model_id: '', sigma: '2', min_move_pct: 4 }), { model_id: null, sigma: 2, min_move_pct: 4 })
  assert.equal(settingsDirty({ ...draft }, draft), false)
  assert.equal(settingsDirty({ ...draft, model_id: '' }, draft), true)
  assert.equal(settingsDirty({ ...draft, sigma: 3 }, draft), true)
})

test('settings save sends only what changed; "None" is an explicit null', () => {
  const saved = { model_id: 'abc', sigma: 2.5, min_move_pct: 3 }
  assert.deepEqual(settingsChanges({ ...saved, sigma: 3 }, saved), { sigma: 3 })
  assert.deepEqual(settingsChanges({ ...saved, model_id: '' }, saved), { model_id: null })
  assert.deepEqual(settingsChanges({ ...saved, model_id: 'def', min_move_pct: '4.5' }, saved), { model_id: 'def', min_move_pct: 4.5 })
  assert.deepEqual(settingsChanges({ ...saved }, saved), {})
})
