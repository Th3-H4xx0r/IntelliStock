/**
 * Live-readiness helpers for the instance page.
 *
 * `instance.py:_assert_live_broker_start_allowed` will not spawn a funded
 * broker without an artifact-bound readiness report on the instance row. The
 * operator can override that with POST /instances/{id}/readiness-waiver — an
 * audited bypass, open to any signed-in user — and these are the rules that
 * route enforces,
 * restated on the client so the UI can refuse a body the server would refuse
 * anyway, and for exactly the same reasons.
 *
 * Everything here is pure so the gate can be reasoned about (and exercised)
 * without mounting a component.
 */

/** api/main.py: READINESS_WAIVER_MIN_REASON. */
export const WAIVER_MIN_REASON_CHARS = 20

/** The phrase the operator must type. Names the instance so a phrase copied
 *  between two tabs cannot waive the gate on the wrong one. */
export function waiverConfirmPhrase(instanceId) {
  return `WAIVE LIVE GATE ${String(instanceId ?? '')}`
}

/**
 * Exact, byte-for-byte — deliberately NOT trimmed.
 *
 * The server compares `body.confirm != f"WAIVE LIVE GATE {instance_id}"`, so
 * a trailing space is a 400 there. Trimming here would let the button enable
 * on a phrase the server then rejects, and the operator would read the
 * refusal as a bug rather than as a typo.
 */
export function waiverConfirmMatches(instanceId, typed) {
  return typeof typed === 'string' && typed === waiverConfirmPhrase(instanceId)
}

/** The server strips the reason before measuring it; so do we. */
export function waiverReasonLength(reason) {
  return String(reason ?? '').trim().length
}

export function waiverReasonOk(reason) {
  return waiverReasonLength(reason) >= WAIVER_MIN_REASON_CHARS
}

/** Both gates, never one: the phrase proves the target, the reason is the
 *  entire audit record. */
export function canSubmitWaiver(instanceId, typed, reason) {
  return waiverConfirmMatches(instanceId, typed) && waiverReasonOk(reason)
}

/** A 64-hex digest is unreadable in a card; a prefix is enough to compare
 *  against `docker images --no-trunc` by eye. The full value stays in the
 *  title attribute. */
export function shortArtifactHash(hash, length = 12) {
  const text = String(hash ?? '').trim()
  if (!text) return ''
  return text.length <= length ? text : text.slice(0, length)
}

/**
 * What the card should say about this instance, as one of three states.
 *
 * `unsupported` matters: an API build that predates the readiness fields
 * omits the key entirely, and rendering that as "no readiness report" would
 * tell an operator their gate is missing when the truth is that the page
 * cannot see it.
 */
export function readinessStatus(instance) {
  if (!instance || typeof instance !== 'object') return 'unknown'
  if (!('live_readiness_report' in instance)) return 'unsupported'
  const report = instance.live_readiness_report
  return report && typeof report === 'object' ? 'present' : 'missing'
}

/** The report's own count of what passed — a waiver writes all six as passed
 *  with an "OPERATOR WAIVED" reason, so a count alone never proves evidence. */
export function readinessCheckCounts(report) {
  const checks = Array.isArray(report?.checks) ? report.checks : []
  return { total: checks.length, passed: checks.filter(c => c && c.passed).length }
}

/** True when every check's reason is an operator waiver rather than earned
 *  evidence. The route stamps that prefix on purpose: nothing reading the
 *  report later should mistake a waiver for a passing run. */
export function isWaivedReport(report) {
  const checks = Array.isArray(report?.checks) ? report.checks : []
  return checks.length > 0
    && checks.every(c => String(c?.reason ?? '').startsWith('OPERATOR WAIVED'))
}
