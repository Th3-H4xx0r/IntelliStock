/**
 * The notification taxonomy NotificationSettingsView falls back to when the
 * API sends no `types` (an older backend). The swing and wheel entries match
 * backend/notification_types.py in order and wording, as the mobile fallback
 * list (notification_prefs.dart) does; backend/tests/
 * test_web_notification_fallback.py checks them against the backend.
 */
export const FALLBACK_TYPES = [
  { key: 'order_fill',     group: 'Notifications', label: 'Order filled',    desc: 'An order was filled' },
  { key: 'order_reject',   group: 'Notifications', label: 'Order rejected',  desc: 'The broker rejected an order' },
  { key: 'halt',           group: 'Notifications', label: 'Halt',            desc: 'Live trading was halted' },
  { key: 'crash_loop',     group: 'Notifications', label: 'Crash loop',      desc: 'The broker subprocess entered a crash loop' },
  { key: 'instance_crash', group: 'Notifications', label: 'Instance crashed', desc: 'An instance process died (not a Stop) and was held open for log capture' },
  // Swing & Wheel (swing-trader port, spec §10)
  { key: 'swing_entry', group: 'Swing & Wheel', label: 'Swing entry', desc: 'The swing lane sent a bracket buy' },
  { key: 'swing_pending_review', group: 'Swing & Wheel', label: 'Swing review needed', desc: 'A swing candidate scored 50-74 and waits for your approval' },
  { key: 'swing_exit', group: 'Swing & Wheel', label: 'Swing exit', desc: 'The swing lane sold a position; or an exit was not placed or its outcome is unknown, so the position may be unprotected' },
  { key: 'swing_run_summary', group: 'Swing & Wheel', label: 'Swing & wheel run summary', desc: 'A swing or wheel scan finished; AI rejects and bear-mode notes' },
  { key: 'wheel_put_placed', group: 'Swing & Wheel', label: 'Wheel put sent', desc: 'The wheel lane sent a cash-secured put' },
  { key: 'wheel_pending_review', group: 'Swing & Wheel', label: 'Wheel review needed', desc: 'A wheel candidate scored 50-74 and waits for your approval' },
  {
    key: 'wheel_position_alert',
    group: 'Swing & Wheel',
    label: 'Wheel position alert',
    desc: 'A short put is in the money, near expiry, has no price, or is being bought back; or assigned shares are a covered-call candidate (dry run)',
  },
  { key: 'wheel_assignment', group: 'Swing & Wheel', label: 'Wheel assignment', desc: 'A put was assigned and its shares are now held' },
  { key: 'swing_approval_failed', group: 'Swing & Wheel', label: 'Approved order refused or unconfirmed', desc: 'A swing or wheel order you approved was not sent, may not have been placed, or WAS placed though its signal reads failed' },
]
