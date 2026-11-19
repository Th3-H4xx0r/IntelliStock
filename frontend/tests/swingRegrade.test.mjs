import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  REGRADE_STALE_MS,
  anyRegradeLive,
  applyLocalRegrades,
  classifyDecisionFailure,
  classifyRegradeFailure,
  classifyRunFailure,
  decisionBody,
  pruneRegradeErrors,
  manualRunView,
  pctMove,
  regradeChanges,
  regradeCountOf,
  regradeControl,
  regradeState,
  regradeWarnings,
  reviewDrift,
  scoredAtLabel,
} from '../src/utils/swing.js'

// Fri 20 Nov 2026, 10:42 New York (EST, UTC-5).
const NOW = Date.parse('2026-11-20T15:42:00Z')
const iso = ms => new Date(ms).toISOString()

const WHEEL = {
  id: 'w1', lane: 'wheel', status: 'pending', symbol: 'ACME', session: '2026-11-16',
  created_at: '2026-11-16T14:05:00+00:00', score: 71, recommendation: 'REVIEW',
  proposal: { strike: 102.5, expiry: '2026-11-27', qty: 1, premium_est: 1.45 },
  context: { stock_price: 108 },
}

function withRegrade(regrade, extra = {}) {
  return { ...WHEEL, ...extra, regrade }
}

const PREVIOUS = {
  score: 62, recommendation: 'REVIEW', stock_price: 100, strike: 95, premium_est: 1.2,
  expiry: '2026-11-20', at: '2026-11-16T14:05:00+00:00',
}

const DONE = {
  status: 'done', requested_at: iso(NOW - 70000), requested_by: 'op', finished_at: iso(NOW - 10000),
  count: 1, error: null, qualifies: true, rescored: true, reason: null, previous: PREVIOUS,
}

// -- regradeState ------------------------------------------------------------

test('regradeState: no regrade block, or an unknown status, is idle', () => {
  assert.equal(regradeState(WHEEL, NOW), 'idle')
  assert.equal(regradeState(withRegrade(null), NOW), 'idle')
  assert.equal(regradeState(withRegrade('queued'), NOW), 'idle')
  assert.equal(regradeState(withRegrade({ status: 'weird' }), NOW), 'idle')
  assert.equal(regradeState(null, NOW), 'idle')
})

test('regradeState: queued or running under 5 minutes old is live', () => {
  for (const status of ['queued', 'running']) {
    assert.equal(regradeState(withRegrade({ status, requested_at: iso(NOW - 1000) }), NOW), 'live')
    assert.equal(regradeState(withRegrade({ status, requested_at: iso(NOW - REGRADE_STALE_MS) }), NOW), 'live')
  }
  // A device clock behind the server's: a request "from the future" is live.
  assert.equal(regradeState(withRegrade({ status: 'queued', requested_at: iso(NOW + 30000) }), NOW), 'live')
})

test('regradeState: queued or running older than 5 minutes, or undated, is stale', () => {
  assert.equal(REGRADE_STALE_MS, 300000)
  for (const status of ['queued', 'running']) {
    assert.equal(regradeState(withRegrade({ status, requested_at: iso(NOW - REGRADE_STALE_MS - 1) }), NOW), 'stale')
    assert.equal(regradeState(withRegrade({ status }), NOW), 'stale')
    assert.equal(regradeState(withRegrade({ status, requested_at: 'not a date' }), NOW), 'stale')
  }
})

test('regradeState: done and failed read as themselves', () => {
  assert.equal(regradeState(withRegrade(DONE), NOW), 'done')
  assert.equal(regradeState(withRegrade({ status: 'failed', error: 'x' }), NOW), 'failed')
})

test('anyRegradeLive: true only while some card has a live regrade', () => {
  const live = withRegrade({ status: 'running', requested_at: iso(NOW - 5000) }, { id: 'w2' })
  const stale = withRegrade({ status: 'queued', requested_at: iso(NOW - REGRADE_STALE_MS - 1000) }, { id: 'w3' })
  assert.equal(anyRegradeLive([WHEEL, withRegrade(DONE), live], NOW), true)
  assert.equal(anyRegradeLive([WHEEL, withRegrade(DONE), stale], NOW), false)
  assert.equal(anyRegradeLive([], NOW), false)
  assert.equal(anyRegradeLive(null, NOW), false)
})

// -- regradeControl ----------------------------------------------------------

test('regradeControl: shown only on a pending wheel card', () => {
  assert.deepEqual(regradeControl(WHEEL, NOW), { show: true, disabled: false, label: 'Regrade' })
  assert.equal(regradeControl({ ...WHEEL, lane: 'swing' }, NOW).show, false)
  assert.equal(regradeControl({ ...WHEEL, status: 'approved' }, NOW).show, false)
  assert.equal(regradeControl({ ...WHEEL, status: undefined }, NOW).show, false)
  assert.equal(regradeControl(null, NOW).show, false)
})

test('regradeControl: disabled while live; a stale, failed or done regrade can be asked again', () => {
  assert.deepEqual(regradeControl(withRegrade({ status: 'queued', requested_at: iso(NOW - 1000) }), NOW),
    { show: true, disabled: true, label: 'Regrading…' })
  assert.deepEqual(regradeControl(withRegrade({ status: 'running', requested_at: iso(NOW - REGRADE_STALE_MS - 1) }), NOW),
    { show: true, disabled: false, label: 'Regrade' })
  assert.equal(regradeControl(withRegrade({ status: 'failed', error: 'x' }), NOW).disabled, false)
  assert.equal(regradeControl(withRegrade(DONE), NOW).disabled, false)
})

// -- scoredAtLabel -----------------------------------------------------------

test('scoredAtLabel: never regraded, the score is from created_at (New York time)', () => {
  assert.equal(scoredAtLabel(WHEEL), 'Scored Mon 16 Nov, 09:05 ET')
})

test('scoredAtLabel: regrade.scored_at dates the score, whatever the regrade status', () => {
  for (const status of ['done', 'queued', 'running', 'failed']) {
    const s = withRegrade({ ...DONE, status, scored_at: '2026-11-18T15:42:00Z', finished_at: '2026-11-20T15:42:00Z' })
    assert.equal(scoredAtLabel(s), 'Scored Wed 18 Nov, 10:42 ET', status)
  }
})

test('scoredAtLabel: no scored_at means created_at; finished_at is never used', () => {
  for (const status of ['done', 'queued', 'running', 'failed']) {
    const s = withRegrade({ ...DONE, status, finished_at: '2026-11-20T15:42:00Z' })
    assert.equal(scoredAtLabel(s), 'Scored Mon 16 Nov, 09:05 ET', status)
  }
  assert.equal(scoredAtLabel(withRegrade({ ...DONE, scored_at: null })), 'Scored Mon 16 Nov, 09:05 ET')
  assert.equal(scoredAtLabel(withRegrade({ ...DONE, scored_at: '' })), 'Scored Mon 16 Nov, 09:05 ET')
})

test('scoredAtLabel: an unreadable time says so; midnight is 00:00, months are never "Sept"', () => {
  assert.equal(scoredAtLabel({ ...WHEEL, created_at: null }), 'Score time unknown')
  assert.equal(scoredAtLabel(withRegrade({ ...DONE, scored_at: 'not a date' })), 'Score time unknown')
  assert.equal(scoredAtLabel({ ...WHEEL, created_at: '2026-09-15T04:00:00Z' }), 'Scored Tue 15 Sep, 00:00 ET')
})

// -- pctMove -----------------------------------------------------------------

test('pctMove: signed one-decimal percent, null when it cannot be computed', () => {
  assert.equal(pctMove(100, 108), '+8.0%')
  assert.equal(pctMove(100, 96.8), '-3.2%')
  assert.equal(pctMove(100, 100), '0.0%')
  assert.equal(pctMove(100, 100.04), '0.0%')
  assert.equal(pctMove('100', '110'), '+10.0%')
  assert.equal(pctMove(null, 100), null)
  assert.equal(pctMove(100, null), null)
  assert.equal(pctMove(0, 100), null)
  assert.equal(pctMove(-5, 100), null)
  assert.equal(pctMove('x', 100), null)
})

// -- regradeChanges ----------------------------------------------------------

test('regradeChanges: done + rescored gives score, stock (with move), strike and premium before -> after', () => {
  assert.deepEqual(regradeChanges(withRegrade(DONE)), [
    { label: 'Score', before: '62', after: '71', move: null },
    { label: 'Stock', before: '$100.00', after: '$108.00', move: '+8.0%' },
    { label: 'Strike', before: '$95.00', after: '$102.50', move: null },
    { label: 'Premium', before: '$1.20', after: '$1.45', move: null },
  ])
})

test('regradeChanges: missing values read as a dash', () => {
  const s = withRegrade({ ...DONE, previous: null }, { score: null, proposal: null, context: null })
  assert.deepEqual(regradeChanges(s), [
    { label: 'Score', before: '—', after: '—', move: null },
    { label: 'Stock', before: '—', after: '—', move: null },
    { label: 'Strike', before: '—', after: '—', move: null },
    { label: 'Premium', before: '—', after: '—', move: null },
  ])
})

test('regradeChanges: null unless the regrade is done AND rescored', () => {
  assert.equal(regradeChanges(WHEEL), null)
  assert.equal(regradeChanges(withRegrade({ ...DONE, rescored: false, qualifies: false })), null)
  assert.equal(regradeChanges(withRegrade({ ...DONE, rescored: undefined })), null)
  for (const status of ['queued', 'running', 'failed']) {
    assert.equal(regradeChanges(withRegrade({ ...DONE, status })), null, status)
  }
})

// -- regradeWarnings ---------------------------------------------------------

const WARM = 'the wheel lane has not completed a tick since this instance started; try again after its next tick (up to 20 minutes)'

test('regradeWarnings: no longer qualifies, from a done regrade', () => {
  assert.deepEqual(regradeWarnings(withRegrade({ ...DONE, qualifies: false, reason: 'score 41 is below the review threshold' }), NOW),
    ['No longer qualifies: score 41 is below the review threshold'])
  assert.deepEqual(regradeWarnings(withRegrade({ ...DONE, qualifies: false, rescored: false, reason: 'RSI 81 above 70' }), NOW),
    ['No longer qualifies: RSI 81 above 70'])
  assert.deepEqual(regradeWarnings(withRegrade({ ...DONE, qualifies: false, reason: null }), NOW), ['No longer qualifies'])
})

test('regradeWarnings: qualifies false carried through a later queued, running or stale regrade still warns', () => {
  const carried = { ...DONE, qualifies: false, reason: 'RSI 81 above 70' }
  for (const status of ['queued', 'running']) {
    assert.deepEqual(regradeWarnings(withRegrade({ ...carried, status, requested_at: iso(NOW - 1000) }), NOW),
      ['No longer qualifies: RSI 81 above 70'], status)
  }
  assert.deepEqual(regradeWarnings(withRegrade({ ...carried, status: 'queued', requested_at: iso(NOW - REGRADE_STALE_MS - 1) }), NOW),
    ['No longer qualifies: RSI 81 above 70', 'Regrade failed: no result after 5 minutes. You can regrade again.'])
})

test('regradeWarnings: failed shows the server error verbatim, beside a carried no-longer-qualifies', () => {
  assert.deepEqual(regradeWarnings(withRegrade({ ...DONE, status: 'failed', error: WARM }), NOW), [`Regrade failed: ${WARM}`])
  assert.deepEqual(regradeWarnings(withRegrade({ status: 'failed', error: null }), NOW), ['Regrade failed'])
  assert.deepEqual(regradeWarnings(withRegrade({ ...DONE, status: 'failed', error: 'boom', qualifies: false, reason: 'RSI 81 above 70' }), NOW),
    ['No longer qualifies: RSI 81 above 70', 'Regrade failed: boom'])
})

test('regradeWarnings: a stale regrade reads as failed and offers a retry', () => {
  const stale = withRegrade({ status: 'running', requested_at: iso(NOW - REGRADE_STALE_MS - 1) })
  assert.deepEqual(regradeWarnings(stale, NOW), ['Regrade failed: no result after 5 minutes. You can regrade again.'])
})

test('regradeWarnings: none for idle, live or a qualifying regrade', () => {
  assert.deepEqual(regradeWarnings(WHEEL, NOW), [])
  assert.deepEqual(regradeWarnings(null, NOW), [])
  assert.deepEqual(regradeWarnings(withRegrade({ status: 'queued', requested_at: iso(NOW - 1000) }), NOW), [])
  assert.deepEqual(regradeWarnings(withRegrade(DONE), NOW), [])
  assert.deepEqual(regradeWarnings(withRegrade({ ...DONE, qualifies: undefined }), NOW), [])
})

// -- decisionBody / approval version check ------------------------------------

test('decisionBody: a wheel approval carries the shown card\'s regrade.count', () => {
  assert.deepEqual(decisionBody('approve', '', withRegrade({ ...DONE, count: 2 })), { decision: 'approve', expect_regrade_count: 2 })
  assert.deepEqual(decisionBody('approve_half', 'ok', withRegrade({ ...DONE, count: 1 })),
    { decision: 'approve_half', reason: 'ok', expect_regrade_count: 1 })
})

test('decisionBody: no regrade block, or no usable count, sends 0', () => {
  assert.deepEqual(decisionBody('approve', '', WHEEL), { decision: 'approve', expect_regrade_count: 0 })
  assert.deepEqual(decisionBody('approve', '', withRegrade({ status: 'queued' })), { decision: 'approve', expect_regrade_count: 0 })
  assert.deepEqual(decisionBody('approve', '', withRegrade({ ...DONE, count: 'x' })), { decision: 'approve', expect_regrade_count: 0 })
  assert.deepEqual(decisionBody('approve', '', withRegrade({ ...DONE, count: '3' })), { decision: 'approve', expect_regrade_count: 3 })
})

test('decisionBody: never on a rejection or a swing card; the reason is trimmed and optional', () => {
  assert.deepEqual(decisionBody('reject', '  too risky  ', withRegrade({ ...DONE, count: 2 })), { decision: 'reject', reason: 'too risky' })
  assert.deepEqual(decisionBody('approve', '', { id: 's1', lane: 'swing', status: 'pending' }), { decision: 'approve' })
  assert.deepEqual(decisionBody('approve', '   ', null), { decision: 'approve' })
})

test('classifyDecisionFailure: 412 (the card was regraded since the review opened) keeps the card and asks for a re-read', () => {
  const detail = 'This card was regraded since you opened it; review the new terms and approve again.'
  assert.deepEqual(classifyDecisionFailure(412, detail),
    { kind: 'regraded', removeCard: false, stopPolling: false, message: detail })
  // The status decides, not the wording.
  assert.equal(classifyDecisionFailure(412, 'something else').kind, 'regraded')
  assert.equal(classifyDecisionFailure(412, '').message,
    'This card was regraded since you opened it; review the new terms and approve again.')
  // Every 409 stays "decided elsewhere", even with the regrade wording.
  const v = classifyDecisionFailure(409, detail)
  assert.deepEqual([v.kind, v.removeCard, v.message], ['stale', true, detail])
  assert.equal(classifyDecisionFailure(409, 'signal w1 is approved, not pending').kind, 'stale')
})

// -- applyLocalRegrades ------------------------------------------------------

test('applyLocalRegrades: a poll that predates the POST keeps the queued block', () => {
  const mine = { status: 'queued', requested_at: '2026-11-20T15:41:00+00:00' }
  const older = withRegrade({ ...DONE, requested_at: '2026-11-19T15:00:00+00:00' })
  const out = applyLocalRegrades([WHEEL, older, { ...WHEEL, id: 'other' }], { w1: mine })
  assert.deepEqual(out.rows.map(r => r.regrade ?? null), [mine, mine, null])
  assert.deepEqual(out.local, { w1: mine })
  assert.equal(out.rows[0].score, 71) // the server's other fields stay
})

test('applyLocalRegrades: the entry goes once the server row caught up, or the card is gone', () => {
  const mine = { status: 'queued', requested_at: '2026-11-20T15:41:00+00:00' }
  const same = withRegrade({ status: 'running', requested_at: '2026-11-20T15:41:00+00:00' })
  let out = applyLocalRegrades([same], { w1: mine })
  assert.equal(out.rows[0], same)
  assert.deepEqual(out.local, {})
  const later = withRegrade({ ...DONE, requested_at: '2026-11-20T15:41:00.5+00:00' })
  out = applyLocalRegrades([later], { w1: mine })
  assert.equal(out.rows[0], later)
  assert.deepEqual(out.local, {})
  out = applyLocalRegrades([{ ...WHEEL, id: 'other' }], { w1: mine })
  assert.deepEqual(out.local, {})
})

test('applyLocalRegrades: no entries, or no rows, change nothing', () => {
  const rows = [WHEEL]
  assert.deepEqual(applyLocalRegrades(rows, {}), { rows, local: {} })
  assert.deepEqual(applyLocalRegrades(rows, null), { rows, local: {} })
  assert.deepEqual(applyLocalRegrades(null, { w1: { status: 'queued' } }), { rows: [], local: {} })
})

// -- classifyRegradeFailure --------------------------------------------------

test('classifyRegradeFailure: 409 is "already under way" and re-reads the list', () => {
  assert.deepEqual(classifyRegradeFailure(409, 'signal w1 is already being regraded'),
    { message: 'Regrade already under way.', reload: true, stopPolling: false })
})

test('classifyRegradeFailure: 503 says the instance is not running, with the server detail', () => {
  assert.deepEqual(classifyRegradeFailure(503, 'Instance x is not running, so the regrade could not reach it'),
    { message: 'Instance is not running: Instance x is not running, so the regrade could not reach it', reload: false, stopPolling: false })
  assert.equal(classifyRegradeFailure(503, '').message, 'Instance is not running.')
})

test('classifyRegradeFailure: 400/404 show the detail and re-read; 401 stops polling', () => {
  assert.deepEqual(classifyRegradeFailure(400, 'signal w1 is approved, not pending'),
    { message: 'signal w1 is approved, not pending', reload: true, stopPolling: false })
  assert.deepEqual(classifyRegradeFailure(404, [{ msg: 'Signal not found: w1' }]),
    { message: 'Signal not found: w1', reload: true, stopPolling: false })
  assert.equal(classifyRegradeFailure(404, null).message, 'This card can no longer be regraded.')
  assert.deepEqual(classifyRegradeFailure(401, 'x'),
    { message: 'Session expired — please sign in again.', reload: false, stopPolling: true })
})

test('classifyRegradeFailure: other statuses and network errors show the detail', () => {
  assert.deepEqual(classifyRegradeFailure(500, 'boom'), { message: 'boom', reload: false, stopPolling: false })
  assert.equal(classifyRegradeFailure(500, '').message, 'Regrade failed (500)')
  assert.equal(classifyRegradeFailure(0, 'Failed to fetch').message, 'Failed to fetch')
  assert.equal(classifyRegradeFailure(0, '').message, 'Could not reach the server.')
})

// -- manualRunView -----------------------------------------------------------

const RUN = {
  id: 'r1', status: 'done', phase: 'done', requested_by: 'op',
  started_at: '2026-11-20T15:30:00+00:00', heartbeat_at: '2026-11-20T15:41:00+00:00',
  finished_at: '2026-11-20T15:42:00+00:00', session: '2026-11-20',
  scanned: 503, passed_screen: 31, to_score: 12, scored: 12, new_pending: 3,
  rejected: 7, skipped_existing: 4, errors: 2, error: null,
}

test('manualRunView: no run yet', () => {
  assert.deepEqual(manualRunView(null, false), { state: 'none', when: '', progress: '', counts: [], error: '' })
})

test('manualRunView: a finished run lists every count', () => {
  assert.deepEqual(manualRunView(RUN, false), {
    state: 'done',
    when: 'Fri 20 Nov, 10:42 ET',
    progress: '',
    counts: [
      { label: 'Scanned', value: 503 },
      { label: 'Passed screen', value: 31 },
      { label: 'Scored', value: 12 },
      { label: 'New cards', value: 3 },
      { label: 'Rejected', value: 7 },
      { label: 'Already had a card', value: 4 },
      { label: 'Errors', value: 2 },
    ],
    error: '',
  })
})

test('manualRunView: missing counts read as 0', () => {
  const v = manualRunView({ id: 'r1', status: 'done', finished_at: null, started_at: null }, false)
  assert.equal(v.when, '')
  assert.deepEqual(v.counts.map(c => c.value), [0, 0, 0, 0, 0, 0, 0])
})

test('manualRunView: in progress shows the phase and its counts (the server decides in_progress)', () => {
  const base = { ...RUN, status: 'running', finished_at: null }
  const at = { ...base, phase: 'queued' }
  assert.deepEqual(manualRunView(at, true), {
    state: 'running', when: 'Fri 20 Nov, 10:30 ET',
    progress: 'Queued: waiting for the instance to start it', counts: [], error: '',
  })
  assert.equal(manualRunView({ ...base, phase: 'screening' }, true).progress, 'Screening the wheel universe')
  assert.equal(manualRunView({ ...base, phase: 'earnings' }, true).progress,
    'Checking earnings dates · 31 of 503 passed the screen')
  assert.equal(manualRunView({ ...base, phase: 'scoring', scored: 4, new_pending: 1 }, true).progress,
    'Scoring with the AI · 4 of 12 scored · 1 new card so far')
  assert.equal(manualRunView({ ...base, phase: 'scoring', scored: 5, new_pending: 2 }, true).progress,
    'Scoring with the AI · 5 of 12 scored · 2 new cards so far')
  assert.equal(manualRunView({ ...base, phase: 'mystery' }, true).progress, 'Running')
  assert.equal(manualRunView(null, true).progress, 'Running')
})

test('manualRunView: a failed run shows its error verbatim', () => {
  const error = 'the wheel lane has not completed a tick since this instance started; try again after its next tick (up to 20 minutes)'
  const v = manualRunView({ ...RUN, status: 'failed', phase: 'failed', error }, false)
  assert.deepEqual([v.state, v.error, v.counts], ['failed', `Run failed: ${error}`, []])
  assert.equal(manualRunView({ ...RUN, status: 'failed', error: null }, false).error, 'Run failed')
})

test('manualRunView: still "running" but the server says not in progress = it stopped', () => {
  const v = manualRunView({ ...RUN, status: 'running', phase: 'scoring', finished_at: null }, false)
  assert.equal(v.state, 'stopped')
  assert.equal(v.when, 'Fri 20 Nov, 10:30 ET')
  assert.equal(v.error, 'The last run stopped before it finished (the instance may have restarted). You can run it again.')
})

// -- classifyRunFailure ------------------------------------------------------

test('classifyRunFailure: 409 already under way re-reads the run; 503 not running + detail', () => {
  assert.deepEqual(classifyRunFailure(409, 'a wheel run is already under way for x'),
    { message: 'A wheel run is already under way.', refresh: true, stopPolling: false })
  assert.deepEqual(classifyRunFailure(503, 'the wheel run could not be queued: boom'),
    { message: 'Instance is not running: the wheel run could not be queued: boom', refresh: false, stopPolling: false })
  assert.equal(classifyRunFailure(503, null).message, 'Instance is not running.')
})

test('classifyRunFailure: 401 stops polling; others show the detail', () => {
  assert.deepEqual(classifyRunFailure(401, ''), { message: 'Session expired — please sign in again.', refresh: false, stopPolling: true })
  assert.deepEqual(classifyRunFailure(400, 'Instance not found: x'), { message: 'Instance not found: x', refresh: false, stopPolling: false })
  assert.equal(classifyRunFailure(500, '').message, 'Could not start the run (500)')
  assert.equal(classifyRunFailure(0, '').message, 'Could not reach the server.')
})

// -- Fix round 3: the reviewed count, regraded-while-open, stale regrade errors --

test('regradeCountOf: the row\'s regrade.count as an integer, 0 when absent or unusable', () => {
  assert.equal(regradeCountOf(withRegrade({ ...DONE, count: 2 })), 2)
  assert.equal(regradeCountOf(withRegrade({ ...DONE, count: '3' })), 3)
  assert.equal(regradeCountOf(withRegrade({ ...DONE, count: 1.9 })), 1)
  assert.equal(regradeCountOf(withRegrade({ ...DONE, count: 'x' })), 0)
  assert.equal(regradeCountOf(withRegrade({ status: 'queued' })), 0)
  assert.equal(regradeCountOf(WHEEL), 0)
  assert.equal(regradeCountOf(null), 0)
})

test('decisionBody: sends the count of the row the review opened on, not the live row', () => {
  const opened = withRegrade({ ...DONE, count: 1 })
  assert.deepEqual(decisionBody('approve', '', opened), { decision: 'approve', expect_regrade_count: 1 })
})

test('reviewDrift: null while the live row has the count the review opened on', () => {
  const opened = withRegrade({ ...DONE, count: 1 })
  assert.equal(reviewDrift(opened, { ...opened, score: 99 }), null)
  assert.equal(reviewDrift(WHEEL, withRegrade({ status: 'queued', requested_at: iso(NOW) })), null)
  assert.equal(reviewDrift(null, opened), null)
  assert.equal(reviewDrift(opened, null), null)
})

test('reviewDrift: a regrade landed while open -> the count change and the terms that differ', () => {
  const opened = WHEEL // count 0: strike 102.5, qty 1, premium 1.45, stock 108, score 71
  const live = withRegrade({ ...DONE, count: 1 }, {
    score: 78, context: { stock_price: 99.36 },
    proposal: { strike: 92, expiry: '2026-11-27', qty: 2, premium_est: 1.05 },
  })
  assert.deepEqual(reviewDrift(opened, live), {
    from: 0, to: 1,
    rows: [
      { label: 'Score', before: '71', after: '78', move: null },
      { label: 'Stock', before: '$108.00', after: '$99.36', move: '-8.0%' },
      { label: 'Strike', before: '$102.50', after: '$92.00', move: null },
      { label: 'Qty', before: '1', after: '2', move: null },
      { label: 'Premium', before: '$1.45', after: '$1.05', move: null },
    ],
  })
})

test('reviewDrift: a regrade that changed no terms (rescored false) still drifts, with no rows', () => {
  const opened = withRegrade({ ...DONE, count: 1 })
  const live = withRegrade({ ...DONE, count: 2, rescored: false, qualifies: false, reason: 'RSI 81 above 70' })
  assert.deepEqual(reviewDrift(opened, live), { from: 1, to: 2, rows: [] })
  const expiry = { ...live, proposal: { ...live.proposal, expiry: '2026-12-04' } }
  assert.deepEqual(reviewDrift(opened, expiry).rows, [{ label: 'Expiry', before: '2026-11-27', after: '2026-12-04', move: null }])
})

test('pruneRegradeErrors: keeps an error while the card and its regrade block are unchanged', () => {
  const row = withRegrade({ ...DONE })
  const errors = { w1: 'Instance is not running.' }
  assert.deepEqual(pruneRegradeErrors(errors, [row], [{ ...row, score: 50 }]), errors)
  assert.deepEqual(pruneRegradeErrors(errors, [WHEEL], [{ ...WHEEL }]), errors)
})

test('pruneRegradeErrors: drops it when the card left, or its regrade status or requested_at changed', () => {
  const errors = { w1: 'Regrade already under way.' }
  const queued = withRegrade({ status: 'queued', requested_at: iso(NOW - 1000) })
  assert.deepEqual(pruneRegradeErrors(errors, [WHEEL], []), {})
  assert.deepEqual(pruneRegradeErrors(errors, [WHEEL], [queued]), {})                        // a regrade appeared
  assert.deepEqual(pruneRegradeErrors(errors, [queued], [{ ...queued, regrade: { ...queued.regrade, status: 'done' } }]), {})
  assert.deepEqual(pruneRegradeErrors(errors, [queued], [{ ...queued, regrade: { ...queued.regrade, requested_at: iso(NOW) } }]), {})
})

test('pruneRegradeErrors: a card absent before keeps its error; no errors in, none out', () => {
  assert.deepEqual(pruneRegradeErrors({ w1: 'x' }, [], [WHEEL]), { w1: 'x' })
  assert.deepEqual(pruneRegradeErrors({}, [WHEEL], [WHEEL]), {})
  assert.deepEqual(pruneRegradeErrors(null, [WHEEL], [WHEEL]), {})
})
