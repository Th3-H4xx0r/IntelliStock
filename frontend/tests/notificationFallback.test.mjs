import { test } from 'node:test'
import assert from 'node:assert/strict'
import { FALLBACK_TYPES } from '../src/utils/notificationFallback.js'

// backend/tests/test_web_notification_fallback.py checks these against
// backend/notification_types.py itself; this pins them for `npm test`.
const SWING_AND_WHEEL = [
  ['swing_entry', 'Swing entry', 'The swing lane sent a bracket buy'],
  ['swing_pending_review', 'Swing review needed', 'A swing candidate scored 50-74 and waits for your approval'],
  ['swing_exit', 'Swing exit', 'The swing lane sold a position'],
  ['swing_run_summary', 'Swing & wheel run summary', 'A swing or wheel scan finished; AI rejects and bear-mode notes'],
  ['wheel_put_placed', 'Wheel put sent', 'The wheel lane sent a cash-secured put'],
  ['wheel_pending_review', 'Wheel review needed', 'A wheel candidate scored 50-74 and waits for your approval'],
  ['wheel_position_alert', 'Wheel position alert', 'A short put is in the money, near expiry, has no price, or is being bought back; or assigned shares are a covered-call candidate (dry run)'],
  ['wheel_assignment', 'Wheel assignment', 'A put was assigned and its shares are now held'],
  ['swing_approval_failed', 'Approved order refused', 'A swing or wheel order you approved could not be sent'],
]

test('the web fallback keeps its five original types first, unchanged', () => {
  assert.deepEqual(FALLBACK_TYPES.slice(0, 5).map(t => [t.key, t.group]), [
    ['order_fill', 'Notifications'], ['order_reject', 'Notifications'], ['halt', 'Notifications'],
    ['crash_loop', 'Notifications'], ['instance_crash', 'Notifications'],
  ])
})

test('the web fallback ends with the nine swing and wheel types, in backend order and wording', () => {
  assert.equal(FALLBACK_TYPES.length, 14)
  assert.deepEqual(FALLBACK_TYPES.slice(5).map(t => [t.key, t.label, t.desc]), SWING_AND_WHEEL)
  assert.ok(FALLBACK_TYPES.slice(5).every(t => t.group === 'Swing & Wheel'))
})
