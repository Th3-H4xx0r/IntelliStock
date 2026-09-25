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
  createStuckMemory,
  createInFlightGuard,
  decisionSuccessMessage,
  decisionsFor,
  detailText,
  addUncertain,
  cardSections,
  fmtAsOf,
  foldSignalLoad,
  UNCERTAIN_BADGE,
  WAITING_COPY,
  waitingCanDismiss,
  uncertainBadge,
  uncertainListsNeeded,
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

test('resendBlockedReason (round 3 minor 1): only an approval made today in New York can be re-sent', () => {
  // A wheel signal's session is its weekly scan day: only decided_at counts.
  assert.equal(resendBlockedReason({ ...WHEEL, session: '2026-09-21', decided_at: '2026-09-25T14:00:00Z' }, '2026-09-25'), null)
  assert.equal(resendBlockedReason({ ...SWING, session: '2026-09-25', decided_at: '2026-09-24T15:00:00Z' }, '2026-09-25'),
    'This approval was made on 2026-09-24; approve a fresh signal instead.')
  // 01:30 UTC on the 25th is 21:30 ET on the 24th.
  assert.equal(resendBlockedReason({ ...SWING, decided_at: '2026-09-25T01:30:00Z' }, '2026-09-24'), null)
  for (const decided_at of [undefined, null, '', 'nope']) {
    assert.equal(resendBlockedReason({ ...SWING, decided_at }, '2026-09-25'),
      'This approval was made on an unknown date; approve a fresh signal instead.')
  }
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

// -- Follow-up 2: an uncertain approval stays on its card until a poll settles it --------

test('the latch reports the newest load generation (the one a 202 must outlive)', () => {
  const latch = createDecisionLatch()
  assert.equal(latch.current(), 0)
  latch.beginLoad(); latch.beginLoad()
  assert.equal(latch.current(), 2)
})

test('addUncertain records the card with the poll generation and the time it must outlive', () => {
  const u = addUncertain({}, SWING, 3, T0)
  assert.deepEqual(u.a1, { signal: SWING, since: 3, sinceMs: T0, resolved: null })
  assert.equal(uncertainBadge(u.a1), 'uncertain — waiting for the broker')
  assert.equal(UNCERTAIN_BADGE, 'uncertain — waiting for the broker')
  assert.equal(uncertainListsNeeded(u), true)
  assert.equal(uncertainListsNeeded({}), false)
})

function fold(latch, generation, uncertain, lists, { nowMs = T0, dismissed = new Set() } = {}) {
  const results = {}
  for (const [k, v] of Object.entries(lists)) results[k] = v instanceof Error ? { status: 'rejected', reason: v } : ok(v)
  return foldSignalLoad({ generation, latch, results, previous: { signals: [], stuck: [] }, nowMs,
    resentAt: new Map(), uncertain, dismissed })
}

test('an uncertain card waits while the signal still reads approved or is nowhere', () => {
  const latch = createDecisionLatch()
  latch.beginLoad(); latch.record('a1')
  const u = addUncertain({}, SWING, 1, T0)
  const gen = latch.beginLoad()
  const out = fold(latch, gen, u, { pending: [], approved: [approvedAt('a1', '2026-09-25T13:00:00Z')],
    approved_half: [], submitted: [], failed: [] })
  assert.equal(out.uncertain.a1.resolved, null)
  // Approved 30 minutes ago, but it is uncertain, not stuck: no Re-send card.
  assert.deepEqual(out.stuck, [])
  assert.equal(fold(latch, latch.beginLoad(), u, { pending: [], approved: [], approved_half: [], submitted: [], failed: [] })
    .uncertain.a1.resolved, null)
})

test('a later poll that reports submitted or failed settles the badge', () => {
  const latch = createDecisionLatch()
  const u = addUncertain({}, SWING, 0, T0)
  const sub = fold(latch, latch.beginLoad(), u, { pending: [], approved: [], approved_half: [],
    submitted: [{ ...SWING, status: 'submitted', order_client_id: 'instance-1-abc-0' }], failed: [] })
  assert.equal(sub.uncertain.a1.resolved, 'submitted')
  assert.equal(uncertainBadge(sub.uncertain.a1), 'submitted')
  const bad = fold(latch, latch.beginLoad(), u, { pending: [], approved: [], approved_half: [],
    submitted: [], failed: [{ ...SWING, status: 'failed' }] })
  assert.deepEqual([bad.uncertain.a1.resolved, uncertainBadge(bad.uncertain.a1)], ['failed', 'failed'])
})

// Seams I-2: the broker writes submitted when it CLAIMS the approval, before it sends
// anything, and order_client_id only once the order went out. The claim can still be
// undone (back to pending) or swept (failed), so only a keyed row settles the card.
test('a submitted row without order_client_id keeps waiting, and can still return to pending', () => {
  const latch = createDecisionLatch()
  latch.beginLoad(); latch.record('a1')
  const u = addUncertain({}, SWING, 1, T0)
  const claimed = fold(latch, latch.beginLoad(), u, { pending: [], approved: [], approved_half: [],
    submitted: [{ ...SWING, status: 'submitted', order_client_id: null }], failed: [] })
  assert.equal(claimed.uncertain.a1.resolved, null)
  assert.equal(uncertainBadge(claimed.uncertain.a1), UNCERTAIN_BADGE)
  const unkeyed = fold(latch, latch.beginLoad(), claimed.uncertain, { pending: [], approved: [],
    approved_half: [], submitted: [{ ...SWING, status: 'submitted' }], failed: [] })
  assert.equal(unkeyed.uncertain.a1.resolved, null)
  // The control re-read failed and the broker put it back: the pending card is back.
  const reset = fold(latch, latch.beginLoad(), unkeyed.uncertain, { pending: [SWING], approved: [],
    approved_half: [], submitted: [], failed: [] })
  assert.deepEqual(reset.uncertain, {})
  assert.deepEqual(reset.signals.map(s => s.id), ['a1'])
})

test('a submitted row without order_client_id that is then swept failed says failed', () => {
  const latch = createDecisionLatch()
  const u = addUncertain({}, SWING, 0, T0)
  const claimed = fold(latch, latch.beginLoad(), u, { pending: [], approved: [], approved_half: [],
    submitted: [{ ...SWING, status: 'submitted', order_client_id: '' }], failed: [] })
  assert.equal(claimed.uncertain.a1.resolved, null)
  const swept = fold(latch, latch.beginLoad(), claimed.uncertain, { pending: [], approved: [],
    approved_half: [], submitted: [], failed: [{ ...SWING, status: 'failed', order_client_id: null }] })
  assert.equal(swept.uncertain.a1.resolved, 'failed')
})

test('a later poll that reports pending ends the uncertain card: the pending card is back', () => {
  const latch = createDecisionLatch()
  latch.beginLoad(); latch.record('a1')
  const u = addUncertain({}, SWING, 1, T0)
  const out = fold(latch, latch.beginLoad(), u, { pending: [SWING], approved: [], approved_half: [],
    submitted: [], failed: [] })
  assert.deepEqual(out.uncertain, {})
  assert.deepEqual(out.signals.map(s => s.id), ['a1'])
})

test('a poll that began before the 202 cannot settle it; a failed read settles nothing', () => {
  const latch = createDecisionLatch()
  const racing = latch.beginLoad()
  latch.record('a1')
  const u = addUncertain({}, SWING, racing, T0)
  const out = fold(latch, racing, u, { pending: [SWING], approved: [], approved_half: [], submitted: [], failed: [] })
  assert.equal(out.uncertain.a1.resolved, null)
  const down = fold(latch, latch.beginLoad(), u, { pending: new Error('x'), approved: [], approved_half: [],
    submitted: new Error('y'), failed: [] })
  assert.equal(down.uncertain.a1.resolved, null)
})

// -- Round 3 FU-1: a waiting card never waits forever with no action -----------------------

test('the waiting copy says what to do, and when Re-send becomes possible', () => {
  assert.equal(WAITING_COPY, 'Delivery to the broker could not be confirmed. Do NOT place this order by hand. This should show submitted or failed within a minute; if it is still waiting after 2 minutes you can re-send it here.')
})

test('still approved 2 minutes after the 202: the card leaves waiting and joins the stuck list', () => {
  const latch = createDecisionLatch()
  const u = addUncertain({}, SWING, 0, T0)
  // Decided (server clock) 30 s after the 202 (client clock): skew must not hide it.
  const row = approvedAt('a1', '2026-09-25T13:30:30Z')
  const lists = { pending: [], approved: [row], approved_half: [], submitted: [], failed: [] }
  const atTwo = fold(latch, latch.beginLoad(), u, lists, { nowMs: T0 + STUCK_AFTER_MS })
  assert.equal(atTwo.uncertain.a1.resolved, null)                 // exactly 2 min: still waiting
  assert.deepEqual(atTwo.stuck, [])
  const after = fold(latch, latch.beginLoad(), u, lists, { nowMs: T0 + STUCK_AFTER_MS + 1000 })
  assert.deepEqual(after.uncertain, {})
  assert.deepEqual(after.stuck.map(s => s.id), ['a1'])
  // A failed approved read cannot move it: it keeps waiting (with Dismiss, below).
  const down = fold(latch, latch.beginLoad(), u, { ...lists, approved: new Error('x') },
    { nowMs: T0 + STUCK_AFTER_MS + 1000 })
  assert.equal(down.uncertain.a1.resolved, null)
})

test('a waiting card offers Dismiss once 2 minutes have passed, or once it is settled', () => {
  const entry = addUncertain({}, SWING, 0, T0).a1
  assert.equal(waitingCanDismiss(entry, T0 + 60000), false)
  assert.equal(waitingCanDismiss(entry, T0 + STUCK_AFTER_MS + 1), true)
  assert.equal(waitingCanDismiss({ ...entry, resolved: 'submitted' }, T0), true)
})

test('a dismissed stuck card stays off the stuck list', () => {
  const latch = createDecisionLatch()
  const lists = { pending: [], approved: [approvedAt('a1', '2026-09-25T13:00:00Z')], approved_half: [] }
  assert.deepEqual(fold(latch, latch.beginLoad(), {}, lists).stuck.map(s => s.id), ['a1'])
  assert.deepEqual(fold(latch, latch.beginLoad(), {}, lists, { dismissed: new Set(['a1']) }).stuck, [])
})

// -- Round 3 minor 2: the stuck list renders on its own ------------------------------------

test('cardSections: the stuck list shows even when the pending read failed on first load', () => {
  const stuckRow = approvedAt('a1', '2026-09-25T13:00:00Z')
  const firstLoadFailed = cardSections({ loaded: false, loading: false, signals: [], stuck: [stuckRow], uncertain: {} })
  assert.deepEqual(firstLoadFailed, { loadingText: false, empty: false, pending: false, waiting: false, stuck: true })
  assert.equal(cardSections({ loaded: false, loading: true, signals: [], stuck: [stuckRow], uncertain: {} }).stuck, true)
  assert.deepEqual(cardSections({ loaded: true, loading: false, signals: [], stuck: [], uncertain: { a1: {} } }),
    { loadingText: false, empty: true, pending: false, waiting: true, stuck: false })
  assert.deepEqual(cardSections({ loaded: true, loading: false, signals: [SWING], stuck: [], uncertain: {} }),
    { loadingText: false, empty: false, pending: true, waiting: false, stuck: false })
  assert.equal(cardSections({ loaded: false, loading: true, signals: [], stuck: [], uncertain: {} }).loadingText, true)
})

// -- Round 4: R3-1 (a joined card survives device clock skew), R3-2 (a new decision
// -- forgets a dismissal) -----------------------------------------------------------------

function pollAll(latch, memory, { uncertain, nowMs, rows, resentAt = new Map(), previous }) {
  return foldSignalLoad({
    generation: latch.beginLoad(), latch, nowMs, resentAt, previous, uncertain,
    dismissed: memory.dismissed, joined: memory.joined,
    results: { pending: ok([]), approved: ok(rows), approved_half: ok([]), submitted: ok([]), failed: ok([]) },
  })
}

test('R3-1: a card that joined the stuck list stays there under device clock skew', () => {
  // The reviewer's probe: the device runs 90 s behind the server.
  const row = { ...SWING, status: 'approved', decided_at: '2026-09-25T13:31:30Z' }
  const latch = createDecisionLatch()
  const memory = createStuckMemory()
  let uncertain = addUncertain({}, row, 0, T0)
  let previous = { signals: [], stuck: [] }
  const seen = []
  for (const dt of [STUCK_AFTER_MS + 1000, STUCK_AFTER_MS + 31000, STUCK_AFTER_MS + 61000, STUCK_AFTER_MS + 91000]) {
    const out = pollAll(latch, memory, { uncertain, nowMs: T0 + dt, rows: [row], previous })
    memory.joined = out.joined
    seen.push(out.stuck.map(s => s.id).join(',') || '-')
    uncertain = out.uncertain
    previous = { signals: out.signals, stuck: out.stuck }
  }
  assert.deepEqual(seen, ['a1', 'a1', 'a1', 'a1'])
  // Once a poll no longer finds it approved, it is forgotten.
  const gone = pollAll(latch, memory, { uncertain, nowMs: T0 + 400000, rows: [], previous })
  assert.deepEqual([gone.stuck, [...gone.joined]], [[], []])
})

test('R3-1: a joined card still waits out a re-send (resentAt) like any stuck card', () => {
  const row = { ...SWING, status: 'approved', decided_at: '2026-09-25T13:31:30Z' }
  const latch = createDecisionLatch()
  const memory = createStuckMemory()
  memory.joined = new Set(['a1'])
  const resentAt = new Map([['a1', T0]])
  const previous = { signals: [], stuck: [] }
  assert.deepEqual(pollAll(latch, memory, { uncertain: {}, nowMs: T0 + 60000, rows: [row], resentAt, previous }).stuck, [])
  assert.deepEqual(pollAll(latch, memory, { uncertain: {}, nowMs: T0 + STUCK_AFTER_MS + 1000, rows: [row], resentAt, previous })
    .stuck.map(s => s.id), ['a1'])
})

test('R3-2: a new decision on a dismissed signal forgets the dismissal', () => {
  const row = { ...SWING, status: 'approved', decided_at: '2026-09-25T13:30:00Z' }
  const latch = createDecisionLatch()
  const memory = createStuckMemory()
  memory.dismiss('a1')
  assert.equal(memory.dismissed.has('a1'), true)
  // The broker reset it, the operator approved it again, and that got a 202.
  memory.noteDecision('a1')
  const uncertain = addUncertain({}, row, 0, T0)
  const out = pollAll(latch, memory, { uncertain, nowMs: T0 + STUCK_AFTER_MS + 1000, rows: [row],
    previous: { signals: [], stuck: [] } })
  assert.deepEqual(out.stuck.map(s => s.id), ['a1'])     // joined, not filtered out
  memory.clear()
  assert.deepEqual([memory.dismissed.size, memory.joined.size], [0, 0])
})
