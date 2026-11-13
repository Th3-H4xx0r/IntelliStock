import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  WHEEL_SCAN_TONE_CLASSES,
  collateralWarning,
  normalizeRecentSignals,
  paddedBounds,
  priceLine,
  putPlan,
  reviewConfirmLabel,
  reviewDetails,
  reviewFigures,
  reviewSideLabel,
  seriesFromPoints,
  stockExpiryText,
  swingSignalAnchor,
  wheelLead,
  wheelScanSignal,
  wheelScanStatus,
} from '../src/utils/swingReview.js'

const wheelSignal = (over = {}) => ({
  id: 'w1', lane: 'wheel', symbol: 'XLE', session: '2026-11-10', status: 'pending',
  proposal: { strike: 88, expiry: '2026-11-20', qty: 1, premium_est: 0.95 },
  ...over,
})
const scan = (over = {}) => ({ symbol: 'XLE', session: '2026-11-10', strike: 88, status: 'pending', ...over })

// -- wheelScanSignal (SwingPresentation.swift wheelScanSignal) ------------------

test('a scan links to the wheel signal with its symbol, session and strike', () => {
  const s = wheelSignal()
  assert.equal(wheelScanSignal(scan(), [s]), s)
})

test('a strike within half a cent still matches; a different strike does not', () => {
  assert.ok(wheelScanSignal(scan({ strike: 88.004 }), [wheelSignal()]))
  assert.equal(wheelScanSignal(scan({ strike: 88.5 }), [wheelSignal()]), null)
})

test('both strikes missing match; only one missing does not', () => {
  const noStrike = wheelSignal({ proposal: {} })
  assert.equal(wheelScanSignal(scan({ strike: null }), [noStrike]), noStrike)
  assert.equal(wheelScanSignal(scan(), [noStrike]), null)
  assert.equal(wheelScanSignal(scan({ strike: null }), [wheelSignal()]), null)
})

test('a swing signal, another symbol or another session never matches', () => {
  assert.equal(wheelScanSignal(scan(), [wheelSignal({ lane: 'swing' })]), null)
  assert.equal(wheelScanSignal(scan(), [wheelSignal({ lane: '' })]), null) // empty lane reads as swing
  assert.equal(wheelScanSignal(scan(), [wheelSignal({ symbol: 'GLD' })]), null)
  assert.equal(wheelScanSignal(scan(), [wheelSignal({ session: '2026-11-03' })]), null)
})

test('a strike sent as a string is read as a number; the first match wins', () => {
  const a = wheelSignal({ id: 'a', proposal: { strike: '88.00' } })
  const b = wheelSignal({ id: 'b' })
  assert.equal(wheelScanSignal(scan(), [a, b]), a)
})

// -- wheelScanStatus (SwingPresentation.swift wheelScanStatus) -------------------

test('a signal still in the pending queue is reviewable', () => {
  assert.deepEqual(wheelScanStatus(scan(), wheelSignal(), new Set(['w1'])),
    { label: 'Pending', tone: 'waiting', reviewable: true })
  assert.deepEqual(wheelScanStatus(scan(), wheelSignal(), ['w1']),
    { label: 'Pending', tone: 'waiting', reviewable: true })
})

test('the signal status wins over the scan log status (the iOS / web mismatch)', () => {
  const st = wheelScanStatus(scan({ status: 'pending' }), wheelSignal({ status: 'rejected' }), [])
  assert.deepEqual(st, { label: 'Rejected', tone: 'bad', reviewable: false })
})

test('each signal status maps to its label and tone', () => {
  const cases = {
    pending: ['Decided', 'neutral'],
    rejected: ['Rejected', 'bad'],
    failed: ['Failed', 'bad'],
    auto_approved: ['Auto-approved', 'good'],
    approved_half: ['Approved ½', 'good'],
    submitted: ['Submitted', 'good'],
    approved: ['Approved', 'good'],
    placed: ['Placed', 'good'],
    expired: ['Expired', 'neutral'],
  }
  for (const [status, [label, tone]] of Object.entries(cases)) {
    assert.deepEqual(wheelScanStatus(scan(), wheelSignal({ status }), []), { label, tone, reviewable: false }, status)
  }
  // An empty status reads as pending -> not in the queue -> Decided.
  assert.equal(wheelScanStatus(scan(), wheelSignal({ status: '' }), []).label, 'Decided')
})

test('without a signal, the scan log status shows as before', () => {
  assert.deepEqual(wheelScanStatus(scan({ status: 'pending' }), null, []), { label: 'Pending', tone: 'waiting', reviewable: false })
  assert.deepEqual(wheelScanStatus(scan({ status: 'placed' }), null, []), { label: 'Placed', tone: 'good', reviewable: false })
  assert.deepEqual(wheelScanStatus(scan({ status: 'rejected' }), null, []), { label: 'Rejected', tone: 'bad', reviewable: false })
  assert.deepEqual(wheelScanStatus(scan({ status: '' }), null, []), { label: '—', tone: 'neutral', reviewable: false })
  assert.deepEqual(wheelScanStatus(scan({ status: 'skipped' }), null, []), { label: 'Skipped', tone: 'neutral', reviewable: false })
})

test('every tone has a dot and a word class', () => {
  for (const tone of ['good', 'waiting', 'bad', 'neutral']) {
    assert.ok(WHEEL_SCAN_TONE_CLASSES[tone].dot && WHEEL_SCAN_TONE_CLASSES[tone].word, tone)
  }
})

test('recent signals accept a bare list or {signals} and drop rows without an id', () => {
  assert.deepEqual(normalizeRecentSignals({ signals: [{ id: 'a' }, { id: '' }, null] }), [{ id: 'a' }])
  assert.deepEqual(normalizeRecentSignals([{ id: 'b' }]), [{ id: 'b' }])
  assert.deepEqual(normalizeRecentSignals(null), [])
})

test('a signal anchor is a safe DOM id', () => {
  assert.equal(swingSignalAnchor('sig-1'), 'swing-signal-sig-1')
  assert.equal(swingSignalAnchor('a b/c'), 'swing-signal-a_b_c')
})

// -- Order review (SwingOrderReviewSheet.swift) ---------------------------------

test('a put plan reads as collect or assigned (iOS demo order)', () => {
  const demo = wheelSignal({ symbol: 'QCOM', proposal: { strike: 177.5, expiry: '2026-10-09', qty: 1, premium_est: 1.31 } })
  const plan = putPlan(demo)
  assert.equal(plan.summary, 'Collect about $131.00 if QCOM stays above $177.50 by Oct 9, 2026.')
  assert.equal(plan.assignment, 'Below $177.50 you buy 100 shares at $177.50.')
  assert.equal(plan.breakeven, 177.5 - 1.31)
})

test('a put plan without a premium or expiry still reads', () => {
  const plan = putPlan(wheelSignal({ proposal: { strike: 88, qty: 2 } }))
  assert.equal(plan.summary, 'Collect the premium if XLE stays above $88.00.')
  assert.equal(plan.assignment, 'Below $88.00 you buy 200 shares at $88.00.')
  assert.equal(plan.breakeven, null)
  assert.equal(putPlan({ lane: 'swing', proposal: { strike: 10 } }), null)
  assert.equal(putPlan(wheelSignal({ proposal: { strike: 0 } })), null)
})

test('the wheel lead figures: whole-order premium and its return on collateral', () => {
  const lead = wheelLead(wheelSignal())
  assert.deepEqual(lead, {
    premium: '$95.00', premiumFootnote: '$0.95 a share',
    collateral: '$8,800.00', collateralFootnote: '1.08% return',
  })
})

test('review figures: a put leads with premium, collateral, breakeven, expiry', () => {
  assert.deepEqual(reviewFigures(wheelSignal()).map(r => [r.label, r.value]), [
    ['Premium', '$95.00'], ['Collateral', '$8,800.00'], ['Breakeven', '$87.05'], ['Expires', 'Nov 20, 2026'],
  ])
})

test('review figures: a swing order shows its proposal', () => {
  const s = { lane: 'swing', symbol: 'AMD', proposal: { entry: 148.2, stop: 139.5, target: 166, shares: 12 } }
  assert.deepEqual(reviewFigures(s).map(r => [r.label, r.value]), [
    ['Entry', '$148.20'], ['Stop', '$139.50'], ['Target', '$166.00'], ['Shares', '12'],
  ])
})

test('review details: swing cost and what is at stake at the proposal prices', () => {
  const s = { lane: 'swing', symbol: 'AMD', proposal: { entry: 148.2, stop: 139.5, target: 166, shares: 12 } }
  assert.deepEqual(reviewDetails(s).map(r => [r.label, r.value]), [
    ['Side', 'Buy'], ['Est. cost', '$1,778.40'], ['Loss at stop', '-$104.40'],
    ['Gain at target', '$213.60'], ['Reward / risk', '2.05×'],
  ])
})

test('review details: a scan-made put says what the broker sets at approval', () => {
  const s = wheelSignal({ context: { stock_price: 91.2, otm_pct: 3.5 } })
  assert.deepEqual(reviewDetails(s).map(r => [r.label, r.value]), [
    ['Side', 'Sell to open'], ['Contract', 'Picked at approval'], ['Strike', '$88.00'],
    ['Expiry', '2026-11-20'], ['Contracts', '1'], ['Limit', 'Set at approval'],
    ['Stock at scan', '$91.20'], ['Out of the money', '3.5%'],
  ])
})

test('the confirm button names the order', () => {
  const swing = { lane: 'swing', symbol: 'AMD', proposal: { shares: 12 } }
  assert.equal(reviewConfirmLabel(swing, 'approve'), 'Approve buy 12 AMD')
  assert.equal(reviewConfirmLabel(swing, 'approve_half'), 'Approve ½ · AMD')
  assert.equal(reviewConfirmLabel({ lane: 'swing', symbol: 'AMD', proposal: {} }, 'approve'), 'Approve buy AMD')
  assert.equal(reviewConfirmLabel(wheelSignal(), 'approve'), 'Approve sell 1 XLE put')
  assert.equal(reviewConfirmLabel(wheelSignal({ proposal: { qty: 3 } }), 'approve'), 'Approve sell 3 XLE puts')
  assert.equal(reviewSideLabel(wheelSignal()), 'Sell put')
  assert.equal(reviewSideLabel(swing), 'Buy')
})

test('a collateral warning only when a put needs more than the cash', () => {
  assert.equal(collateralWarning(wheelSignal(), 10000), null)
  assert.match(collateralWarning(wheelSignal(), 5000), /^Collateral \$8,800\.00 is more than the account's \$5,000\.00 cash\./)
  assert.equal(collateralWarning(wheelSignal(), null), null)
  assert.equal(collateralWarning({ lane: 'swing', proposal: { strike: 1, qty: 1 } }, 0), null)
})

test('expiry text', () => {
  assert.equal(stockExpiryText('2026-10-09'), 'Oct 9, 2026')
  assert.equal(stockExpiryText('soon'), 'soon')
  assert.equal(stockExpiryText('2026-13-01'), '2026-13-01')
})

// -- Review chart -----------------------------------------------------------------

test('1D keeps today when that leaves two points, else the whole series', () => {
  const now = new Date(2026, 10, 12, 15, 0)
  const y = new Date(2026, 10, 11, 15, 0).toISOString()
  const t1 = new Date(2026, 10, 12, 10, 0).toISOString()
  const t2 = new Date(2026, 10, 12, 11, 0).toISOString()
  assert.deepEqual(seriesFromPoints([{ ts: y, value: 1 }, { ts: t1, value: 2 }, { ts: t2, value: 3 }], '1D', now).vals, [2, 3])
  assert.deepEqual(seriesFromPoints([{ ts: y, value: 1 }, { ts: t2, value: 3 }], '1D', now).vals, [1, 3])
  assert.deepEqual(seriesFromPoints([{ ts: y, value: 1 }, { ts: t1, value: 2 }], '1M', now).vals, [1, 2])
  assert.deepEqual(seriesFromPoints([{ ts: 'bad', value: 1 }, { ts: t1, value: 2 }], '1M', now).vals, [2])
})

test('padded bounds', () => {
  assert.deepEqual(paddedBounds([10, 20]), { min: 9.4, max: 20.6 })
  assert.deepEqual(paddedBounds([100, 100]), { min: 99, max: 101 })
  assert.deepEqual(paddedBounds([]), { min: 0, max: 1 })
})

test('the price line shows the cushion over a strike, else the change', () => {
  assert.deepEqual(priceLine([90, 92.4], null, 88), { price: '$92.40', text: '5.00% above strike', tone: 'gain' })
  assert.deepEqual(priceLine([90, 85.8], null, 88), { price: '$85.80', text: '2.50% below strike', tone: 'loss' })
  assert.deepEqual(priceLine([100, 98, 105], 1, null), { price: '$98.00', text: '-$2.00 (-2.00%)', tone: 'loss' })
  assert.deepEqual(priceLine([100, 105], null, null), { price: '$105.00', text: '+$5.00 (+5.00%)', tone: 'gain' })
  assert.equal(priceLine([100], null, null), null)
})

test('axis ticks land on round numbers inside the bounds', async () => {
  const { niceTicks } = await import('../src/utils/swingReview.js')
  assert.deepEqual(niceTicks(87.3, 93.1, 3), [88, 90, 92])
  assert.deepEqual(niceTicks(0, 100), [0, 20, 40, 60, 80, 100])
  assert.deepEqual(niceTicks(5, 5), [5])
})
