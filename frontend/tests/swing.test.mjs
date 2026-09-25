import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  DECISION_LABELS,
  REASONING_PREVIEW_CHARS,
  classifyDecisionFailure,
  classifyDecisionSuccess,
  classifyResendFailure,
  confirmPrompt,
  createDecisionLatch,
  createInFlightGuard,
  decisionSuccessMessage,
  decisionsFor,
  detailText,
  fmtAsOf,
  foldSignalLoad,
  fmtItm,
  itmTone,
  joinKeyRisks,
  normalizeApprovedList,
  nyDate,
  normalizeSignalList,
  parseWheelPayload,
  proposalRows,
  reasoningPreview,
  resendBlockedReason,
  resendPrompt,
  resendSuccess,
  STUCK_AFTER_MS,
  stuckApprovals,
  stuckLabel,
  scoreTone,
  swingLanesOf,
  wheelLoadFailure,
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
    // Follow-up 5: an approval is not final; a transient refusal returns it here.
    assert.equal(
      confirm,
      `Approve AAPL${decision === 'approve_half' ? ' at half size' : ''}? The broker rebuilds the order at the live price and checks it before sending. Approving sends the order. If the broker can't place it yet (e.g. before the open) the signal returns here to approve again.`,
    )
    assert.doesNotMatch(confirm, /final/)
    // FW item 4 (M-1): some refusals send no notification, so none is promised.
    assert.equal(
      success,
      `Approved AAPL${decision === 'approve_half' ? ' at half size' : ''}. The broker rebuilds and checks the order at the live price before sending it.`,
    )
    for (const text of [confirm, success]) {
      assert.doesNotMatch(text, /placed|goes out|within seconds|command poll|notif/)
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

const UNCERTAIN_APPROVAL = 'Approval received, but its delivery to the broker could not be confirmed. Do NOT place this order by hand — it may still be queued. The card will show submitted or failed shortly.'
const UNCERTAIN_RESEND = 'Re-send received, but its delivery to the broker could not be confirmed. Do NOT place this order by hand — it may still be queued. The card will show submitted or failed shortly.'

test('classifyDecisionSuccess: a 202 shows the server detail as a warning, never an invitation to place it by hand', () => {
  const v = classifyDecisionSuccess(202, { uncertain: true, detail: UNCERTAIN_APPROVAL, command_id: null }, SWING, 'approve')
  assert.deepEqual([v.uncertain, v.tone, v.message], [true, 'warn', UNCERTAIN_APPROVAL])
  // The flag alone (a proxy that rewrote the status) is enough, and no detail
  // falls back to the same ruled text (follow-up 1).
  const bare = classifyDecisionSuccess(200, { uncertain: true }, SWING, 'approve_half')
  assert.deepEqual([bare.uncertain, bare.message], [true, UNCERTAIN_APPROVAL])
  const noBody = classifyDecisionSuccess(202, null, WHEEL, 'approve')
  assert.deepEqual([noBody.uncertain, noBody.message], [true, UNCERTAIN_APPROVAL])
})

test('classifyDecisionFailure: a 503 keeps the card and says it was not queued', () => {
  const v = classifyDecisionFailure(503, '')
  assert.deepEqual([v.kind, v.removeCard, v.stopPolling], ['unavailable', false, false])
  assert.equal(v.message, 'Not queued — the signal is still pending; try again.')
  const server = classifyDecisionFailure(503, 'the approval was not queued for the broker (x); the signal is pending again — not queued, try again')
  assert.match(server.message, /not queued, try again$/)
  assert.doesNotMatch(v.message, /manually/)
})

// -- FW-api-I2: the local hide covers only the race with an in-flight poll -------------

test('a poll already in flight when the decision landed cannot resurrect the card', () => {
  const latch = createDecisionLatch()
  const racing = latch.beginLoad()          // the poll starts...
  latch.record('a1')                        // ...the approval's 2xx arrives...
  // ...and the poll answers with the server's pre-decision view.
  assert.deepEqual(latch.apply(racing, [SWING, WHEEL]).map(s => s.id), ['w1'])
  assert.equal(latch.has('a1'), true)
})

test('a poll that began after the decision governs: a broker reset to pending shows again', () => {
  const latch = createDecisionLatch()
  latch.beginLoad()
  latch.record('a1')
  // The broker had no quote pre-open and put a1 back to pending; the next poll
  // started after the 2xx, so the server's answer stands.
  const next = latch.beginLoad()
  assert.deepEqual(latch.apply(next, [SWING, WHEEL]).map(s => s.id), ['a1', 'w1'])
  assert.equal(latch.has('a1'), false)
})

test('a poll that returns the id with a non-pending status ends the hide at once', () => {
  const latch = createDecisionLatch()
  const racing = latch.beginLoad()
  latch.record('a1')
  // The racing poll saw a1 approved (the approved lists), so the hide is done ...
  assert.deepEqual(latch.apply(racing, [WHEEL], ['a1']).map(s => s.id), ['w1'])
  assert.equal(latch.has('a1'), false)
  // ... and from then on the server's pending rows show, whichever poll brings them.
  assert.deepEqual(latch.apply(racing, [SWING]).map(s => s.id), ['a1'])
})

test('a row one poll saw both pending and approved is not shown pending (separate requests)', () => {
  const latch = createDecisionLatch()
  const racing = latch.beginLoad()
  latch.record('a1')
  // ?status=pending was served before the decision, ?status=approved after it.
  assert.deepEqual(latch.apply(racing, [SWING, WHEEL], ['a1']).map(s => s.id), ['w1'])
  assert.equal(latch.has('a1'), false)
  // Without any decision on this page the same rule holds.
  assert.deepEqual(createDecisionLatch().apply(1, [SWING], ['a1']), [])
})

test('a decision recorded before any load began is hidden only from no load at all', () => {
  const latch = createDecisionLatch()
  latch.record('a1')                        // generation 0: nothing is in flight
  const first = latch.beginLoad()
  assert.deepEqual(latch.apply(first, [SWING]).map(s => s.id), ['a1'])
})

test('clear() drops every hide (instance switch)', () => {
  const latch = createDecisionLatch()
  const gen = latch.beginLoad()
  latch.record('a1')
  latch.clear()
  assert.deepEqual(latch.apply(gen, [SWING]).map(s => s.id), ['a1'])
})

// -- FW item 3: re-send a stuck approval ------------------------------------------------

const T0 = Date.parse('2026-09-25T13:30:00Z')
const approvedAt = (id, decidedAt, status = 'approved') =>
  ({ ...SWING, id, status, decided_at: decidedAt, decided_by: 'pranav' })

test('normalizeApprovedList merges the approved and approved_half lists, approved only', () => {
  const rows = normalizeApprovedList(
    { signals: [approvedAt('a1', '2026-09-25T13:20:00Z'), { ...SWING, id: 'p1' }] },
    [approvedAt('h1', '2026-09-25T13:25:00Z', 'approved_half'), null, 'junk'],
  )
  assert.deepEqual(rows.map(s => s.id), ['h1', 'a1'])
  assert.deepEqual(normalizeApprovedList(undefined, { detail: 'x' }), [])
})

test('stuckApprovals: approved for more than 2 minutes, counted from the latest of decision and re-send', () => {
  assert.equal(STUCK_AFTER_MS, 120000)
  const rows = [
    approvedAt('old', '2026-09-25T13:27:59Z'),      // 2m01s ago
    approvedAt('edge', '2026-09-25T13:28:00Z'),     // exactly 2m: not yet
    approvedAt('new', '2026-09-25T13:29:30Z'),
    approvedAt('resent', '2026-09-25T13:00:00Z'),
    approvedAt('undated', null),
  ]
  const resentAt = new Map([['resent', T0 - 60000]])
  assert.deepEqual(stuckApprovals(rows, T0, resentAt).map(s => s.id), ['old', 'undated'])
  assert.deepEqual(stuckApprovals(rows, T0 + 61000, resentAt).map(s => s.id),
    ['old', 'edge', 'resent', 'undated'])
})

test('stuckLabel says how long it has waited and that nothing was sent', () => {
  assert.equal(stuckLabel(approvedAt('a1', '2026-09-25T13:25:00Z'), T0),
    'Approved 5 min ago; the broker has not picked it up yet.')
  assert.equal(stuckLabel(approvedAt('a1', null), T0),
    'Approved; the broker has not picked it up yet.')
})

test('resend copy never promises a placed order or a notification', () => {
  assert.equal(resendPrompt(SWING),
    'Re-send the approval for AAPL? The broker rebuilds the order at the live price and checks it before sending; a copy it already picked up is ignored.')
  const ok = resendSuccess(200, { command_id: 'c1' }, SWING)
  assert.deepEqual([ok.uncertain, ok.tone, ok.message], [false, 'ok', 'Re-sent AAPL to the broker.'])
  const unsure = resendSuccess(202, { uncertain: true, detail: UNCERTAIN_RESEND }, SWING)
  assert.deepEqual([unsure.uncertain, unsure.tone, unsure.message], [true, 'warn', UNCERTAIN_RESEND])
  assert.equal(resendSuccess(202, null, SWING).message, UNCERTAIN_RESEND)
  for (const m of [resendPrompt(SWING), ok.message, unsure.message]) {
    assert.doesNotMatch(m, /placed|notif|manually/)
  }
})

test('classifyResendFailure: 409/404 drop the stuck card and snooze it; 503 keeps it', () => {
  const busy = classifyResendFailure(409, 'command c1 for signal a1 is still pending; ...')
  assert.deepEqual([busy.removeCard, busy.snooze, busy.stopPolling], [true, true, false])
  assert.match(busy.message, /still pending/)
  assert.equal(classifyResendFailure(404, '').removeCard, true)
  const down = classifyResendFailure(503, '')
  assert.deepEqual([down.removeCard, down.snooze], [false, false])
  assert.equal(down.message, 'Not queued — try again.')
  assert.equal(classifyResendFailure(401, '').stopPolling, true)
  assert.equal(classifyResendFailure(0, '').message, 'Could not reach the server.')
})

// -- FW item 4: the wheel card's "as of" stamp and its 404s ------------------------------

test('fmtAsOf stamps the last successful load in local 24-hour time', () => {
  assert.equal(fmtAsOf(new Date(2026, 8, 25, 14, 2, 59)), 'as of 14:02')
  assert.equal(fmtAsOf(new Date(2026, 8, 25, 9, 5)), 'as of 09:05')
  assert.equal(fmtAsOf(null), '')
  assert.equal(fmtAsOf(new Date('nope')), '')
})

test('wheelLoadFailure: a route-less API build is not an error, and only 401 stops polling', () => {
  const absent = wheelLoadFailure(404, 'Not Found')
  assert.deepEqual([absent.kind, absent.stopPolling, absent.message],
    ['no-endpoint', false, 'This API build has no wheel endpoint yet.'])
  const unknown = wheelLoadFailure(404, 'Instance not found: swing-paper')
  assert.deepEqual([unknown.kind, unknown.stopPolling, unknown.message],
    ['error', false, 'Instance not found: swing-paper'])
  assert.equal(wheelLoadFailure(404, '').message, 'Could not load the wheel (404)')
  const expired = wheelLoadFailure(401, '')
  assert.deepEqual([expired.stopPolling, expired.message], [true, 'Session expired — please sign in again.'])
  const down = wheelLoadFailure(503, 'broker unavailable: timeout')
  assert.deepEqual([down.kind, down.stopPolling, down.message], ['error', false, 'broker unavailable: timeout'])
  assert.equal(wheelLoadFailure(0, '').message, 'Could not load the wheel')
})

// -- Follow-up 3: re-send is for today's session only ------------------------------------

test('nyDate is the New York calendar date, across the DST switches', () => {
  assert.equal(nyDate(Date.parse('2026-09-25T01:30:00Z')), '2026-09-24')   // 21:30 EDT
  assert.equal(nyDate(Date.parse('2026-09-25T04:00:00Z')), '2026-09-25')   // 00:00 EDT
  assert.equal(nyDate(Date.parse('2026-03-08T04:59:00Z')), '2026-03-07')   // 23:59 EST
  assert.equal(nyDate(Date.parse('2026-11-01T04:30:00Z')), '2026-11-01')   // 00:30 EDT
  assert.equal(nyDate(Date.parse('2026-11-02T04:30:00Z')), '2026-11-01')   // 23:30 EST
})

test('resendBlockedReason: only an approval from today\'s session can be re-sent', () => {
  assert.equal(resendBlockedReason({ ...SWING, session: '2026-09-25' }, '2026-09-25'), null)
  assert.equal(resendBlockedReason({ ...SWING, session: '2026-09-24' }, '2026-09-25'),
    'This approval is from 2026-09-24; approve a fresh signal instead.')
  assert.equal(resendBlockedReason({ ...SWING, session: '' }, '2026-09-25'),
    'This approval is from an unknown session; approve a fresh signal instead.')
})

// -- Follow-up 4: the list reads settle independently -------------------------------------

const ok = value => ({ status: 'fulfilled', value })
const no = message => ({ status: 'rejected', reason: Object.assign(new Error(message), {}) })

test('foldSignalLoad: a failed approved read never stops the pending list refreshing', () => {
  const latch = createDecisionLatch()
  const previous = { signals: [WHEEL], stuck: [approvedAt('kept', '2026-09-25T13:00:00Z')] }
  const out = foldSignalLoad({
    generation: latch.beginLoad(), latch, nowMs: T0, resentAt: new Map(), previous,
    results: { pending: ok({ signals: [SWING] }), approved: no('Could not load signals (502)'), approved_half: ok([]) },
  })
  assert.deepEqual(out.signals.map(s => s.id), ['a1'])            // refreshed
  assert.deepEqual(out.stuck.map(s => s.id), ['kept'])             // last good
  assert.deepEqual([out.pendingLoaded, out.error, out.unauthorized], [true, 'Could not load signals (502)', false])
})

test('foldSignalLoad: a failed pending read keeps the last good list; the approved lists still refresh', () => {
  const latch = createDecisionLatch()
  const previous = { signals: [WHEEL], stuck: [] }
  const out = foldSignalLoad({
    generation: latch.beginLoad(), latch, nowMs: T0, resentAt: new Map(), previous,
    results: { pending: no('boom'), approved: ok([approvedAt('old', '2026-09-25T13:20:00Z')]), approved_half: ok([]) },
  })
  assert.deepEqual(out.signals.map(s => s.id), ['w1'])
  assert.deepEqual(out.stuck.map(s => s.id), ['old'])
  assert.deepEqual([out.pendingLoaded, out.error], [false, 'boom'])
})

test('foldSignalLoad: all settled clears the error; a 401 anywhere says so', () => {
  const latch = createDecisionLatch()
  const clean = foldSignalLoad({
    generation: latch.beginLoad(), latch, nowMs: T0, resentAt: new Map(), previous: { signals: [], stuck: [] },
    results: { pending: ok([SWING]), approved: ok([]), approved_half: ok([]) },
  })
  assert.deepEqual([clean.error, clean.unauthorized, clean.signals.length], ['', false, 1])
  const expired = Object.assign(new Error('Session expired — please sign in again.'), { unauthorized: true })
  const out = foldSignalLoad({
    generation: latch.beginLoad(), latch, nowMs: T0, resentAt: new Map(), previous: { signals: [], stuck: [] },
    results: { pending: ok([SWING]), approved: { status: 'rejected', reason: expired }, approved_half: ok([]) },
  })
  assert.deepEqual([out.unauthorized, out.error], [true, 'Session expired — please sign in again.'])
})
