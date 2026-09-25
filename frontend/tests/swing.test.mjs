import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  DECISION_LABELS,
  REASONING_PREVIEW_CHARS,
  classifyDecisionFailure,
  classifyDecisionSuccess,
  confirmPrompt,
  createInFlightGuard,
  decisionSuccessMessage,
  decisionsFor,
  detailText,
  fmtItm,
  itmTone,
  joinKeyRisks,
  normalizeSignalList,
  parseWheelPayload,
  proposalRows,
  reasoningPreview,
  scoreTone,
  swingLanesOf,
  withoutDecided,
} from '../src/utils/swing.js'

const SWING = {
  id: 'a1', instance_id: 'swing-paper', lane: 'swing', symbol: 'AAPL',
  session: '2026-09-24', created_at: '2026-09-24T13:15:02Z', score: 62,
  recommendation: 'REVIEW', reasoning: 'Pullback to the 50-day in an uptrend.',
  key_risks: ['earnings in 9 days', '', '  sector rotation  '], size_adjustment: 1.0,
  proposal: { entry: 200.0, stop: 188.0, target: 218.0, shares: 6 }, status: 'pending',
}
const WHEEL = {
  id: 'w1', instance_id: 'swing-paper', lane: 'wheel', symbol: 'APH',
  session: '2026-09-21', created_at: '2026-09-21T14:30:00Z', score: 55,
  recommendation: 'REVIEW', reasoning: 'IV rank is high.', key_risks: [],
  size_adjustment: 1.0, status: 'pending',
  proposal: { contract: 'APH261002P00130000', strike: 130, expiry: '2026-10-02',
    qty: 1, limit_price: 1.23, premium_est: 1.3, delta: -0.24 },
}

test('swingLanesOf reads lowercase ids and CamelCase class names', () => {
  assert.deepEqual(swingLanesOf({ strategies: [{ strategy: 'strategy_swing' }] }),
    { swing: true, wheel: false, any: true })
  assert.deepEqual(swingLanesOf({ strategies: [{ strategy: 'StrategyWheel' }, { strategy: 'strategy_eb' }] }),
    { swing: false, wheel: true, any: true })
  assert.deepEqual(swingLanesOf({ strategies: [{ strategy: 'strategy_eb' }] }),
    { swing: false, wheel: false, any: false })
  assert.deepEqual(swingLanesOf(null), { swing: false, wheel: false, any: false })
  assert.deepEqual(swingLanesOf({ strategies: 'oops' }), { swing: false, wheel: false, any: false })
})

test('normalizeSignalList accepts a bare list or {signals}, keeps pending only, newest first', () => {
  const decided = { ...SWING, id: 'old', status: 'approved' }
  assert.deepEqual(normalizeSignalList([WHEEL, SWING, decided, null, { status: 'pending' }]).map(s => s.id), ['a1', 'w1'])
  assert.deepEqual(normalizeSignalList({ signals: [SWING] }).map(s => s.id), ['a1'])
  assert.deepEqual(normalizeSignalList({ detail: 'nope' }), [])
})

test('withoutDecided drops ids this page already decided, so a poll cannot resurrect a card', () => {
  assert.deepEqual(withoutDecided([SWING, WHEEL], new Set(['a1'])).map(s => s.id), ['w1'])
})

test('scoreTone bands', () => {
  assert.equal(scoreTone(80), 'high')
  assert.equal(scoreTone(75), 'high')
  assert.equal(scoreTone(74), 'mid')
  assert.equal(scoreTone(50), 'mid')
  assert.equal(scoreTone(49), 'low')
  assert.equal(scoreTone(null), 'unknown')
  assert.equal(scoreTone('x'), 'unknown')
})

test('joinKeyRisks trims, drops blanks and joins with a middle dot', () => {
  assert.equal(joinKeyRisks(SWING.key_risks), 'earnings in 9 days · sector rotation')
  assert.equal(joinKeyRisks(undefined), '')
})

test('reasoningPreview leaves short text alone and cuts long text on a word', () => {
  assert.deepEqual(reasoningPreview('short'), { text: 'short', truncated: false })
  const long = 'word '.repeat(400)
  const out = reasoningPreview(long)
  assert.equal(out.truncated, true)
  assert.ok(out.text.length <= REASONING_PREVIEW_CHARS + 1)
  assert.ok(out.text.endsWith('…'))
  const unbroken = 'x'.repeat(5000)
  assert.equal(reasoningPreview(unbroken).text.length, REASONING_PREVIEW_CHARS + 1)
})

test('decisionsFor offers Approve half to swing only', () => {
  assert.deepEqual(decisionsFor(SWING), ['approve', 'approve_half', 'reject'])
  assert.deepEqual(decisionsFor(WHEEL), ['approve', 'reject'])
  assert.deepEqual(decisionsFor({}), ['approve', 'reject'])
  assert.equal(DECISION_LABELS.approve_half, 'Approve ½')
})

test('proposalRows: swing shows entry, stop, target, shares', () => {
  assert.deepEqual(proposalRows(SWING).map(r => [r.label, r.value]), [
    ['Entry', '$200.00'], ['Stop', '$188.00'], ['Target', '$218.00'], ['Shares', '6'],
  ])
})

test('proposalRows: wheel shows contract, strike, expiry, qty, limit, premium and collateral', () => {
  assert.deepEqual(proposalRows(WHEEL).map(r => [r.label, r.value]), [
    ['Contract', 'APH261002P00130000'], ['Strike', '$130.00'], ['Expiry', '2026-10-02'],
    ['Qty', '1'], ['Limit', '$1.23'], ['Premium', '$1.30 ($130.00)'], ['Collateral', '$13,000.00'],
  ])
})

test('proposalRows tolerates a missing proposal', () => {
  assert.deepEqual(proposalRows({ lane: 'swing' }).map(r => r.value), ['—', '—', '—', '—'])
})

test('confirmPrompt and decisionSuccessMessage name the symbol and the decision', () => {
  assert.match(confirmPrompt(SWING, 'approve'), /AAPL/)
  assert.match(confirmPrompt(SWING, 'approve_half'), /half/)
  assert.match(confirmPrompt(WHEEL, 'reject'), /final/)
  assert.match(decisionSuccessMessage(SWING, 'approve'), /^Approved AAPL/)
  assert.match(decisionSuccessMessage(WHEEL, 'reject'), /^Rejected APH/)
})

test('approval copy says the broker rebuilds and checks the order, never that it was placed', () => {
  for (const decision of ['approve', 'approve_half']) {
    const confirm = confirmPrompt(SWING, decision)
    const success = decisionSuccessMessage(SWING, decision)
    assert.equal(
      confirm,
      `Approve AAPL${decision === 'approve_half' ? ' at half size' : ''}? The broker rebuilds the order at the live price and checks it before sending. Decisions are final.`,
    )
    assert.equal(
      success,
      `Approved AAPL${decision === 'approve_half' ? ' at half size' : ''}. The broker rebuilds and checks the order at the live price; if it refuses, you'll get a notification.`,
    )
    for (const text of [confirm, success]) {
      assert.doesNotMatch(text, /placed|goes out|within seconds|command poll/)
    }
  }
  assert.equal(confirmPrompt(WHEEL, 'reject'), 'Reject APH? Decisions are final.')
  assert.equal(decisionSuccessMessage(WHEEL, 'reject'), 'Rejected APH.')
})

test('detailText flattens FastAPI detail shapes', () => {
  assert.equal(detailText('signal is not pending'), 'signal is not pending')
  assert.equal(detailText([{ msg: 'bad decision' }, { message: 'x' }]), 'bad decision; x')
  assert.equal(detailText(null), '')
})

test('classifyDecisionFailure: another device decided first -> remove card, show why', () => {
  for (const status of [400, 404, 409]) {
    const v = classifyDecisionFailure(status, 'signal a1 is approved, not pending')
    assert.equal(v.kind, 'stale')
    assert.equal(v.removeCard, true)
    assert.equal(v.stopPolling, false)
    assert.equal(v.message, 'signal a1 is approved, not pending')
  }
  assert.match(classifyDecisionFailure(400, '').message, /no longer pending/)
})

test('classifyDecisionFailure: 401 stops polling, 403 keeps the card, 5xx keeps the card', () => {
  assert.deepEqual(
    [classifyDecisionFailure(401, '').kind, classifyDecisionFailure(401, '').stopPolling, classifyDecisionFailure(401, '').removeCard],
    ['unauthorized', true, false])
  const forbidden = classifyDecisionFailure(403, 'not your instance')
  assert.deepEqual([forbidden.kind, forbidden.removeCard, forbidden.message], ['forbidden', false, 'not your instance'])
  const boom = classifyDecisionFailure(502, '')
  assert.deepEqual([boom.kind, boom.removeCard, boom.message], ['failed', false, 'Request failed (502)'])
  assert.equal(classifyDecisionFailure(0, '').message, 'Could not reach the server.')
})

test('createInFlightGuard refuses a second acquire until release (double click)', () => {
  const guard = createInFlightGuard()
  assert.equal(guard.tryAcquire('a1'), true)
  assert.equal(guard.tryAcquire('a1'), false)
  assert.equal(guard.has('a1'), true)
  assert.equal(guard.tryAcquire('w1'), true)
  guard.release('a1')
  assert.equal(guard.tryAcquire('a1'), true)
})

test('parseWheelPayload maps the addendum shape and tolerates junk', () => {
  const w = parseWheelPayload({
    open_puts: [{ contract: 'APH261002P00130000', underlying: 'APH', strike: 130, expiry: '2026-10-02',
      qty: 1, avg_entry_price: 1.23, current_price: null, underlying_price: 127.4,
      itm_pct: 2.0, dte: 8, collateral: 13000, unrealized_pl: null }, 'junk'],
    collateral_total: 13000, cash: 25000,
    recent_scans: [{ id: 's1', session: '2026-09-21', created_at: '2026-09-21T14:30:00Z', symbol: 'APH',
      stock_price: 134.2, strike: 130, expiry: '2026-10-02', premium_est: 1.3, score: 55,
      recommendation: 'REVIEW', status: 'pending', skip_reason: null }],
  })
  assert.equal(w.openPuts.length, 1)
  assert.equal(w.openPuts[0].currentPrice, null)
  assert.equal(w.openPuts[0].itmPct, 2)
  assert.equal(w.collateralTotal, 13000)
  assert.equal(w.recentScans[0].skipReason, '')
  assert.deepEqual(parseWheelPayload(null), { openPuts: [], collateralTotal: null, cash: null, recentScans: [] })
})

test('itmTone mirrors the 15:45 monitor buy-back rules', () => {
  assert.equal(itmTone(10, 20), 'alert')
  assert.equal(itmTone(5, 2), 'alert')
  assert.equal(itmTone(0.4, 0), 'alert')
  assert.equal(itmTone(5, 3), 'itm')
  assert.equal(itmTone(-3, 1), 'otm')
  assert.equal(itmTone(null, 1), 'unknown')
})

test('fmtItm says which side of the strike the stock is', () => {
  assert.equal(fmtItm(2), '2.0% ITM')
  assert.equal(fmtItm(-3.25), '3.3% OTM')
  assert.equal(fmtItm(null), '—')
})

// -- FW-api-I1: a 202 is accepted-but-uncertain, a 503 is "not queued" ---------------

test('classifyDecisionSuccess: a 200 is the plain success copy', () => {
  const v = classifyDecisionSuccess(200, { signal: SWING, command_id: 'c1' }, SWING, 'approve')
  assert.deepEqual([v.uncertain, v.tone], [false, 'ok'])
  assert.equal(v.message, decisionSuccessMessage(SWING, 'approve'))
})

test('classifyDecisionSuccess: a 202 shows the server detail as a warning, never "place manually"', () => {
  const detail = 'approval received — the order may be in flight; check the signal status and open orders before placing anything by hand (x)'
  const v = classifyDecisionSuccess(202, { uncertain: true, detail, command_id: null }, SWING, 'approve')
  assert.deepEqual([v.uncertain, v.tone, v.message], [true, 'warn', detail])
  // The flag alone (a proxy that rewrote the status) is enough, and no detail still warns.
  const bare = classifyDecisionSuccess(200, { uncertain: true }, SWING, 'approve_half')
  assert.equal(bare.uncertain, true)
  assert.match(bare.message, /^Approval received for AAPL — the order may be in flight; check the signal status and open orders/)
  const noBody = classifyDecisionSuccess(202, null, WHEEL, 'approve')
  assert.equal(noBody.uncertain, true)
  for (const m of [v.message, bare.message, noBody.message]) {
    assert.doesNotMatch(m, /place the order manually|no broker command was queued/)
  }
})

test('classifyDecisionFailure: a 503 keeps the card and says it was not queued', () => {
  const v = classifyDecisionFailure(503, '')
  assert.deepEqual([v.kind, v.removeCard, v.stopPolling], ['unavailable', false, false])
  assert.equal(v.message, 'Not queued — the signal is still pending; try again.')
  const server = classifyDecisionFailure(503, 'the approval was not queued for the broker (x); the signal is pending again — not queued, try again')
  assert.match(server.message, /not queued, try again$/)
  assert.doesNotMatch(v.message, /manually/)
})
