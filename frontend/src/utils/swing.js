/**
 * Pure helpers for the swing / wheel approval UI (PendingSignalsCard and
 * WheelPanel). No Vue and no fetch, so the rules can be exercised with
 * `npm test` (node --test) without mounting a component.
 *
 * Shapes: the SwingSignals document is section 5 of
 * docs/superpowers/plans/2026-09-24-swing-port-interfaces.md; the wheel
 * payload is the Contract addendum of plan C
 * (docs/superpowers/plans/2026-09-24-swing-port-C-ui.md). If plan B ships a
 * different wheel shape, parseWheelPayload is the only reader to change.
 */

const LANE_BY_STRATEGY_ID = { strategy_swing: 'swing', strategy_wheel: 'wheel' }

function canonicalStrategyId(value) {
  // "StrategySwing" -> "strategy_swing"; "strategy_swing" is unchanged.
  return String(value ?? '').trim().replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase()
}

/** Which approval lanes the instance's strategy document carries. */
export function swingLanesOf(strategyDoc) {
  const subs = Array.isArray(strategyDoc?.strategies) ? strategyDoc.strategies : []
  const lanes = { swing: false, wheel: false }
  for (const sub of subs) {
    const lane = LANE_BY_STRATEGY_ID[canonicalStrategyId(sub?.strategy)]
    if (lane) lanes[lane] = true
  }
  return { ...lanes, any: lanes.swing || lanes.wheel }
}

/**
 * GET /instances/{id}/swing/signals?status=pending -> pending rows, newest
 * first. Accepts a bare list or {signals: [...]}; drops anything not pending
 * in case an older API build ignores the status filter.
 */
export function normalizeSignalList(payload) {
  const rows = Array.isArray(payload) ? payload : (Array.isArray(payload?.signals) ? payload.signals : [])
  return rows
    .filter(r => r && typeof r === 'object' && r.id && String(r.status ?? 'pending') === 'pending')
    .sort((a, b) => String(b.created_at ?? '').localeCompare(String(a.created_at ?? '')))
}

/**
 * FW-api-I2: the hide a decision's 2xx puts on its card. It covers only the
 * race with a poll that was already in flight when the 2xx arrived, whose
 * answer can predate the decision. The hide ends as soon as either
 *  - a poll that began after the 2xx answers (its answer is authoritative), or
 *  - a poll returns the id with a non-pending status (`nonPendingIds`).
 * After that the server's status governs, so a signal the broker puts back
 * to pending (no quote before the open, an unreadable book) shows again.
 *
 * beginLoad() is called as each load starts and returns its generation;
 * apply(generation, pendingRows, nonPendingIds) filters that load's answer.
 * A row the same load also saw with a non-pending status is dropped from the
 * pending rows: the lists come from separate requests.
 */
export function createDecisionLatch() {
  let generation = 0
  const hidden = new Map()          // id -> the generation last started when its 2xx arrived
  return {
    beginLoad() {
      generation += 1
      return generation
    },
    /** The generation of the newest load started so far. */
    current() { return generation },
    record(id) { hidden.set(id, generation) },
    has(id) { return hidden.has(id) },
    clear() { hidden.clear() },
    apply(loadGeneration, pendingRows, nonPendingIds = []) {
      const seen = new Set(nonPendingIds)
      for (const [id, at] of [...hidden]) {
        if (loadGeneration > at || seen.has(id)) hidden.delete(id)
      }
      // The lists are separate requests: a row this same load also saw with
      // another status is not shown pending (its pending read may predate it).
      return pendingRows.filter(s => !hidden.has(s.id) && !seen.has(s.id))
    },
  }
}

export function scoreTone(score) {
  if (score == null || score === '') return 'unknown'
  const n = Number(score)
  if (!Number.isFinite(n)) return 'unknown'
  if (n >= 75) return 'high'
  if (n >= 50) return 'mid'
  return 'low'
}

export function joinKeyRisks(keyRisks) {
  if (!Array.isArray(keyRisks)) return ''
  return keyRisks.map(r => String(r ?? '').trim()).filter(Boolean).join(' · ')
}

export const REASONING_PREVIEW_CHARS = 280

/** Collapsed reasoning: cut on a word boundary when one is close, else hard. */
export function reasoningPreview(text, limit = REASONING_PREVIEW_CHARS) {
  const full = String(text ?? '').trim()
  if (full.length <= limit) return { text: full, truncated: false }
  const cut = full.slice(0, limit)
  const lastSpace = cut.lastIndexOf(' ')
  const head = lastSpace > limit * 0.6 ? cut.slice(0, lastSpace) : cut
  return { text: `${head.trimEnd()}…`, truncated: true }
}

export const DECISION_LABELS = { approve: 'Approve', approve_half: 'Approve ½', reject: 'Reject' }

/** Approve half exists for swing entries only; the wheel sizes in whole contracts. */
export function decisionsFor(signal) {
  return signal?.lane === 'swing' ? ['approve', 'approve_half', 'reject'] : ['approve', 'reject']
}

export function fmtUsd(value) {
  if (value == null || value === '') return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString('en-US', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })
}

function fmtCount(value) {
  if (value == null || value === '') return '—'
  const n = Number(value)
  return Number.isFinite(n) ? String(Math.trunc(n)) : '—'
}

function finiteOrNull(value) {
  if (value == null || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

/** The proposal grid on a card: [{label, value, mono?}]. */
export function proposalRows(signal) {
  const p = signal?.proposal && typeof signal.proposal === 'object' ? signal.proposal : {}
  if (signal?.lane === 'wheel') {
    const qty = finiteOrNull(p.qty)
    const premium = finiteOrNull(p.premium_est)
    const strike = finiteOrNull(p.strike)
    const credit = qty != null && premium != null ? premium * 100 * qty : null
    const collateral = qty != null && strike != null ? strike * 100 * qty : null
    return [
      { label: 'Contract', value: p.contract ? String(p.contract) : '—', mono: true },
      { label: 'Strike', value: fmtUsd(p.strike) },
      { label: 'Expiry', value: p.expiry ? String(p.expiry) : '—' },
      { label: 'Qty', value: fmtCount(p.qty) },
      { label: 'Limit', value: fmtUsd(p.limit_price) },
      { label: 'Premium', value: credit == null ? fmtUsd(p.premium_est) : `${fmtUsd(p.premium_est)} (${fmtUsd(credit)})` },
      { label: 'Collateral', value: fmtUsd(collateral) },
    ]
  }
  return [
    { label: 'Entry', value: fmtUsd(p.entry) },
    { label: 'Stop', value: fmtUsd(p.stop) },
    { label: 'Target', value: fmtUsd(p.target) },
    { label: 'Shares', value: fmtCount(p.shares) },
  ]
}

export function confirmPrompt(signal, decision) {
  const sym = signal?.symbol || 'this signal'
  // Follow-up 5: an approval is not final. A transient refusal (no quote
  // before the open, say) puts the signal back to pending.
  const after = "Approving sends the order. If the broker can't place it yet (e.g. before the open) the signal returns here to approve again."
  if (decision === 'approve') return `Approve ${sym}? The broker rebuilds the order at the live price and checks it before sending. ${after}`
  if (decision === 'approve_half') return `Approve ${sym} at half size? The broker rebuilds the order at the live price and checks it before sending. ${after}`
  return `Reject ${sym}? Decisions are final.`
}

export function decisionSuccessMessage(signal, decision) {
  const sym = signal?.symbol || 'the signal'
  // No notification is promised: some refusals send none (FW item 4, M-1).
  if (decision === 'approve') return `Approved ${sym}. The broker rebuilds and checks the order at the live price before sending it.`
  if (decision === 'approve_half') return `Approved ${sym} at half size. The broker rebuilds and checks the order at the live price before sending it.`
  return `Rejected ${sym}.`
}

/** FastAPI `detail` may be a string or a list of {msg|message}. */
export function detailText(detail) {
  if (detail == null) return ''
  if (typeof detail === 'string') return detail.trim()
  if (Array.isArray(detail)) {
    return detail.map(d => (d && typeof d === 'object' ? (d.msg ?? d.message ?? JSON.stringify(d)) : String(d))).join('; ')
  }
  return String(detail)
}

/**
 * What a 2xx from POST .../decision means. A 202 (or a body flagged
 * `uncertain`) is FW-api-I1's accepted-but-uncertain answer: the approval is
 * recorded, but the broker command may or may not be queued, so the order may
 * be in flight. The card goes like any success; the notice is a warning that
 * carries the server's advice.
 */
/** Follow-up 1: the controller's wording for a 202, which the server sends too. */
export function uncertainMessage(what = 'Approval') {
  return `${what} received, but its delivery to the broker could not be confirmed. Do NOT place this order by hand — it may still be queued. The card will show submitted or failed shortly.`
}

/** The server's 412 detail, for a 412 that arrives without one. */
const REGRADED_MESSAGE = 'This card was regraded since you opened it; review the new terms and approve again.'

/**
 * The POST .../decision body. A wheel approval (approve or approve_half,
 * never reject) carries `expect_regrade_count`: the regrade.count (0 when
 * absent) of the row the operator was shown, so the server can refuse with a
 * 412 when a regrade changed the terms since.
 */
export function decisionBody(decision, reason, shownSignal) {
  const body = { decision }
  const text = String(reason ?? '').trim()
  if (text) body.reason = text
  if (shownSignal?.lane === 'wheel' && (decision === 'approve' || decision === 'approve_half')) {
    body.expect_regrade_count = regradeCountOf(shownSignal)
  }
  return body
}

/** A row's regrade.count (regrades done) as an integer; 0 when absent or unusable. */
export function regradeCountOf(signal) {
  const n = finiteOrNull(signal?.regrade?.count)
  return n == null ? 0 : Math.trunc(n)
}

export function classifyDecisionSuccess(status, body, signal, decision) {
  const uncertain = status === 202 || body?.uncertain === true
  if (!uncertain) return { uncertain: false, tone: 'ok', message: decisionSuccessMessage(signal, decision) }
  return { uncertain: true, tone: 'warn', message: detailText(body?.detail) || uncertainMessage('Approval') }
}

/**
 * What a failed POST .../decision means for the card.
 * 400/404/409: the signal is no longer pending (another device decided, or it
 * is gone). Remove the card and show the server's reason; if it IS still
 * pending, the next poll brings it back.
 * 503: the approval provably did not reach the broker and the signal is
 * pending, so the card stays and a retry is safe.
 */
export function classifyDecisionFailure(status, detail) {
  const text = detailText(detail)
  if (status === 401) {
    return { kind: 'unauthorized', removeCard: false, stopPolling: true, message: 'Session expired — please sign in again.' }
  }
  if (status === 403) {
    return { kind: 'forbidden', removeCard: false, stopPolling: false, message: text || 'You are not allowed to decide this signal.' }
  }
  // 412: the wheel approval version check (expect_regrade_count). The card is
  // still pending, but a regrade changed its terms since the operator opened
  // it. Keep it and re-read; every 409 stays "decided elsewhere".
  if (status === 412) {
    return { kind: 'regraded', removeCard: false, stopPolling: false, message: text || REGRADED_MESSAGE }
  }
  if (status === 400 || status === 404 || status === 409) {
    return { kind: 'stale', removeCard: true, stopPolling: false, message: text || 'This signal is no longer pending — it was decided elsewhere.' }
  }
  if (status === 503) {
    return { kind: 'unavailable', removeCard: false, stopPolling: false, message: text || 'Not queued — the signal is still pending; try again.' }
  }
  if (!status) {
    return { kind: 'failed', removeCard: false, stopPolling: false, message: text || 'Could not reach the server.' }
  }
  return { kind: 'failed', removeCard: false, stopPolling: false, message: text || `Request failed (${status})` }
}

// -- Re-send a stuck approval (fix wave item 3) -------------------------------

export const APPROVED_STATUSES = ['approved', 'approved_half']

/** An approval the broker has not claimed this long is offered a Re-send. */
export const STUCK_AFTER_MS = 2 * 60 * 1000

/**
 * GET ...?status=approved and ...?status=approved_half, merged: the rows that
 * still read approved, newest decision first. Each payload may be a bare list
 * or {signals: [...]}.
 */
export function normalizeApprovedList(...payloads) {
  const byId = new Map()
  for (const payload of payloads) {
    const rows = Array.isArray(payload) ? payload : (Array.isArray(payload?.signals) ? payload.signals : [])
    for (const r of rows) {
      if (r && typeof r === 'object' && r.id && APPROVED_STATUSES.includes(String(r.status))) byId.set(r.id, r)
    }
  }
  return [...byId.values()]
    .sort((a, b) => String(b.decided_at ?? '').localeCompare(String(a.decided_at ?? '')))
}

function approvedSinceMs(signal, resentAt) {
  const decided = Date.parse(signal?.decided_at ?? '')
  const resent = resentAt?.get?.(signal?.id)
  const times = [decided, resent].filter(Number.isFinite)
  return times.length ? Math.max(...times) : null
}

/**
 * Approved signals no broker command has claimed for more than
 * STUCK_AFTER_MS, counted from the later of the decision and this page's last
 * re-send (`resentAt`: id -> epoch ms). An undated one counts as stuck: the
 * server refuses a re-send while a command for it is still queued.
 */
export function stuckApprovals(approved, nowMs, resentAt = new Map()) {
  return approved.filter(s => {
    const since = approvedSinceMs(s, resentAt)
    return since == null || nowMs - since > STUCK_AFTER_MS
  })
}

// -- Follow-up 2: an uncertain (202) approval stays on its card ----------------

export const UNCERTAIN_BADGE = 'uncertain — waiting for the broker'

/** Round 3 FU-1: what a waiting card says. */
export const WAITING_COPY = 'Delivery to the broker could not be confirmed. Do NOT place this order by hand. This should show submitted or failed within a minute; if it is still waiting after 2 minutes you can re-send it here.'

/**
 * Put a 202'd approval or re-send on an "uncertain" card. `since` is the
 * generation of the newest load started when the 202 arrived: only a load
 * begun after it may settle the card. `sinceMs` is when the 202 arrived.
 * Returns a new map (id -> entry).
 */
export function addUncertain(uncertain, signal, since, sinceMs = Date.now()) {
  return { ...uncertain, [signal.id]: { signal, since, sinceMs, resolved: null } }
}

/** Round 3 FU-1: a waiting card is never without an action: Dismiss after 2 minutes. */
export function waitingCanDismiss(entry, nowMs) {
  return Boolean(entry?.resolved) || nowMs - (entry?.sinceMs ?? nowMs) > STUCK_AFTER_MS
}

export function uncertainBadge(entry) {
  return entry?.resolved || UNCERTAIN_BADGE
}

/** While any card waits, each poll also reads ?status=submitted and ?status=failed. */
export function uncertainListsNeeded(uncertain) {
  return Object.values(uncertain || {}).some(e => !e.resolved)
}

function idsOf(result, keep = () => true) {
  if (result?.status !== 'fulfilled') return null
  const v = result.value
  const rows = Array.isArray(v) ? v : (Array.isArray(v?.signals) ? v.signals : [])
  return new Set(rows.filter(r => r && typeof r === 'object' && r.id && keep(r)).map(r => r.id))
}

/**
 * A waiting card settles only on a load begun after its 202: pending (the
 * card goes, and the pending card is back), submitted or failed (the badge
 * says so until the operator dismisses it). Seams I-2: the broker writes
 * submitted when it CLAIMS the approval, before it sends anything, and
 * order_client_id once the order went out; a claim can still go back to
 * pending or be swept to failed. So only a submitted row carrying
 * order_client_id settles the card; one without it keeps waiting. Round 3
 * FU-1: still approved more than STUCK_AFTER_MS after the 202, it leaves the
 * waiting state and joins the stuck list (`joinedStuck`), where Re-send and
 * Dismiss are. Anything else, or a failed read, leaves it waiting.
 */
function foldUncertain(uncertain, generation, results, nowMs) {
  const pending = idsOf(results?.pending)
  const submitted = idsOf(results?.submitted, r => Boolean(r.order_client_id))
  const failed = idsOf(results?.failed)
  const approved = new Set([...(idsOf(results?.approved) || []), ...(idsOf(results?.approved_half) || [])])
  const next = {}
  const joinedStuck = new Set()
  for (const [id, entry] of Object.entries(uncertain || {})) {
    if (entry.resolved || generation <= entry.since) {
      next[id] = entry
    } else if (pending?.has(id)) {
      continue
    } else if (submitted?.has(id)) {
      next[id] = { ...entry, resolved: 'submitted' }
    } else if (failed?.has(id)) {
      next[id] = { ...entry, resolved: 'failed' }
    } else if (approved.has(id) && nowMs - entry.sinceMs > STUCK_AFTER_MS) {
      joinedStuck.add(id)
    } else {
      next[id] = entry
    }
  }
  return { next, joinedStuck }
}

/**
 * Follow-up 4: fold one poll's list reads into the next card state. Each read
 * settles on its own (`results` holds Promise.allSettled outcomes keyed by
 * status: pending, approved, approved_half, and submitted and failed while a
 * card is uncertain). A failed read keeps its part of `previous` and reports
 * its error; it never stops another list refreshing. A reason carrying
 * `unauthorized: true` is a 401. An uncertain card is never also a stuck one.
 */
export function foldSignalLoad({ generation, latch, results, previous, nowMs, resentAt, uncertain = {},
  dismissed = new Set(), joined = new Set() }) {
  const done = key => results?.[key]?.status === 'fulfilled'
  const failures = Object.values(results || {})
    .filter(r => r?.status === 'rejected').map(r => r.reason)
  const approvedRows = normalizeApprovedList(
    done('approved') ? results.approved.value : null,
    done('approved_half') ? results.approved_half.value : null)
  const signals = done('pending')
    ? latch.apply(generation, normalizeSignalList(results.pending.value), approvedRows.map(s => s.id))
    : previous.signals
  const { next: nextUncertain, joinedStuck } = foldUncertain(uncertain, generation, results, nowMs)
  const approvedRead = done('approved') && done('approved_half')
  // Round 3 R3-1: a card that left waiting for the stuck list stays joined
  // while it still reads approved, and a joined card skips the server-clock
  // age check (a device clock behind the server would otherwise hide it
  // between polls). Only this page's re-send snooze (resentAt, device clock)
  // still applies. A failed approved read keeps the set as it was.
  const approvedIds = new Set(approvedRows.map(s => s.id))
  const nextJoined = approvedRead
    ? new Set([...joined, ...joinedStuck].filter(id => approvedIds.has(id)))
    : new Set([...joined, ...joinedStuck])
  let stuck = approvedRead ? stuckApprovals(approvedRows, nowMs, resentAt) : previous.stuck
  const listed = new Set(stuck.map(s => s.id))
  const snoozed = id => resentAt?.has?.(id) && nowMs - resentAt.get(id) <= STUCK_AFTER_MS
  stuck = [...stuck, ...approvedRows.filter(s => nextJoined.has(s.id) && !listed.has(s.id) && !snoozed(s.id))]
    .filter(s => !nextUncertain[s.id] && !dismissed.has(s.id))
  const first = failures[0]
  return {
    signals,
    stuck,
    uncertain: nextUncertain,
    joined: nextJoined,
    pendingLoaded: done('pending'),
    error: first ? (first.message || String(first)) : '',
    unauthorized: failures.some(r => r?.unauthorized === true),
  }
}

/**
 * Round 4: what a page remembers about stuck cards across polls.
 * `dismissed`: ids the operator dismissed. `joined`: ids that left the
 * waiting state for the stuck list (R3-1; foldSignalLoad returns the next
 * set). A new decision or 202 on the same id forgets both (R3-2), so a
 * re-approved signal is never filtered out by an old dismissal.
 */
export function createStuckMemory() {
  return {
    dismissed: new Set(),
    joined: new Set(),
    dismiss(id) { this.dismissed.add(id) },
    noteDecision(id) {
      this.dismissed.delete(id)
      this.joined.delete(id)
    },
    clear() {
      this.dismissed.clear()
      this.joined.clear()
    },
  }
}

/**
 * Round 3 minor 2: which parts of the card render. The pending list and its
 * empty state wait for a first good pending read; the waiting and stuck
 * lists render on their own, so a failed first pending read never hides an
 * approval that needs a Re-send.
 */
export function cardSections({ loaded, loading, signals, stuck, uncertain }) {
  return {
    loadingText: Boolean(!loaded && loading),
    empty: Boolean(loaded && !signals.length),
    pending: Boolean(loaded && signals.length),
    waiting: Object.keys(uncertain || {}).length > 0,
    stuck: stuck.length > 0,
  }
}

export function stuckLabel(signal, nowMs) {
  const decided = Date.parse(signal?.decided_at ?? '')
  if (!Number.isFinite(decided)) return 'Approved; the broker has not picked it up yet.'
  const mins = Math.max(1, Math.floor((nowMs - decided) / 60000))
  return `Approved ${mins} min ago; the broker has not picked it up yet.`
}

const NY_DATE = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
})

/** The New York calendar date at epoch ms `nowMs`, "YYYY-MM-DD". */
export function nyDate(nowMs) {
  return NY_DATE.format(new Date(nowMs))
}

/**
 * Round 3 minor 1: the server re-sends only an approval made today in New
 * York (its decided_at, not its session: a wheel signal's session is its
 * weekly scan day), and it alone decides the 409. The button follows the
 * same rule. null when the card may offer Re-send, else the reason shown in
 * its place. `today` is nyDate(now).
 */
export function resendBlockedReason(signal, today) {
  const decided = Date.parse(signal?.decided_at ?? '')
  const madeOn = Number.isFinite(decided) ? nyDate(decided) : null
  if (madeOn && madeOn === today) return null
  return `This approval was made on ${madeOn || 'an unknown date'}; approve a fresh signal instead.`
}

export function resendPrompt(signal) {
  const sym = signal?.symbol || 'this signal'
  return `Re-send the approval for ${sym}? The broker rebuilds the order at the live price and checks it before sending; a copy it already picked up is ignored.`
}

/** A 2xx from POST .../resend; a 202 is the same accepted-but-uncertain answer as a decision's. */
export function resendSuccess(status, body, signal) {
  const sym = signal?.symbol || 'the signal'
  if (status === 202 || body?.uncertain === true) {
    return { uncertain: true, tone: 'warn', message: detailText(body?.detail) || uncertainMessage('Re-send') }
  }
  return { uncertain: false, tone: 'ok', message: `Re-sent ${sym} to the broker.` }
}

/**
 * A failed POST .../resend. 409 (not approved any more, or a command for it
 * is still queued) and 404 drop the stuck card and snooze it for another
 * STUCK_AFTER_MS; the polls say what happens next. 503 and the rest keep it.
 */
export function classifyResendFailure(status, detail) {
  const text = detailText(detail)
  if (status === 401) {
    return { removeCard: false, snooze: false, stopPolling: true, message: 'Session expired — please sign in again.' }
  }
  if (status === 404 || status === 409) {
    return { removeCard: true, snooze: true, stopPolling: false, message: text || 'Nothing to re-send for this signal.' }
  }
  if (status === 503) {
    return { removeCard: false, snooze: false, stopPolling: false, message: text || 'Not queued — try again.' }
  }
  if (!status) {
    return { removeCard: false, snooze: false, stopPolling: false, message: text || 'Could not reach the server.' }
  }
  return { removeCard: false, snooze: false, stopPolling: false, message: text || `Request failed (${status})` }
}

/**
 * Synchronous per-id latch. `disabled` on a button only takes effect after
 * Vue re-renders; two clicks inside one tick would both get through without
 * this.
 */
export function createInFlightGuard() {
  const ids = new Set()
  return {
    tryAcquire(id) {
      if (ids.has(id)) return false
      ids.add(id)
      return true
    },
    release(id) { ids.delete(id) },
    has(id) { return ids.has(id) },
  }
}

/** GET /instances/{id}/wheel, per the plan C Contract addendum. */
export function parseWheelPayload(payload) {
  const src = payload && typeof payload === 'object' ? payload : {}
  const openPuts = (Array.isArray(src.open_puts) ? src.open_puts : [])
    .filter(r => r && typeof r === 'object')
    .map(r => ({
      contract: String(r.contract ?? ''),
      underlying: String(r.underlying ?? ''),
      strike: finiteOrNull(r.strike),
      expiry: String(r.expiry ?? ''),
      qty: finiteOrNull(r.qty),
      avgEntryPrice: finiteOrNull(r.avg_entry_price),
      currentPrice: finiteOrNull(r.current_price),
      underlyingPrice: finiteOrNull(r.underlying_price),
      itmPct: finiteOrNull(r.itm_pct),
      dte: finiteOrNull(r.dte),
      collateral: finiteOrNull(r.collateral),
      unrealizedPl: finiteOrNull(r.unrealized_pl),
    }))
  const recentScans = (Array.isArray(src.recent_scans) ? src.recent_scans : [])
    .filter(r => r && typeof r === 'object')
    .map(r => ({
      id: String(r.id ?? ''),
      session: String(r.session ?? ''),
      createdAt: String(r.created_at ?? ''),
      symbol: String(r.symbol ?? ''),
      stockPrice: finiteOrNull(r.stock_price),
      strike: finiteOrNull(r.strike),
      expiry: String(r.expiry ?? ''),
      premiumEst: finiteOrNull(r.premium_est),
      score: finiteOrNull(r.score),
      recommendation: String(r.recommendation ?? ''),
      status: String(r.status ?? ''),
      skipReason: r.skip_reason ? String(r.skip_reason) : '',
    }))
  return {
    openPuts,
    collateralTotal: finiteOrNull(src.collateral_total),
    cash: finiteOrNull(src.cash),
    recentScans,
  }
}

/** "as of 14:02" for the last successful wheel load, local 24-hour time. */
export function fmtAsOf(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return ''
  const hh = String(date.getHours()).padStart(2, '0')
  const mm = String(date.getMinutes()).padStart(2, '0')
  return `as of ${hh}:${mm}`
}

/**
 * A failed GET /instances/{id}/wheel. FastAPI's own 404 for a route it does
 * not have reads exactly "Not Found": that API build has no wheel endpoint,
 * which is not an error. Any other 404 (an unknown instance, say) is shown
 * with its detail. Only a 401 stops polling: every other answer can change.
 */
export function wheelLoadFailure(status, detail) {
  const text = detailText(detail)
  if (status === 401) {
    return { kind: 'unauthorized', stopPolling: true, message: 'Session expired — please sign in again.' }
  }
  if (status === 404 && text === 'Not Found') {
    return { kind: 'no-endpoint', stopPolling: false, message: 'This API build has no wheel endpoint yet.' }
  }
  if (!status) return { kind: 'error', stopPolling: false, message: text || 'Could not load the wheel' }
  return { kind: 'error', stopPolling: false, message: text || `Could not load the wheel (${status})` }
}

/** 'alert' is exactly the set the 15:45 monitor buys back (spec section 5.2). */
export function itmTone(itmPct, dte) {
  if (itmPct == null) return 'unknown'
  if (itmPct >= 10 || (itmPct >= 5 && dte != null && dte <= 2) || (itmPct > 0 && dte === 0)) return 'alert'
  if (itmPct > 0) return 'itm'
  return 'otm'
}

export function fmtItm(itmPct) {
  if (itmPct == null) return '—'
  return itmPct > 0 ? `${itmPct.toFixed(1)}% ITM` : `${Math.abs(itmPct).toFixed(1)}% OTM`
}

// -- Wheel regrade (POST .../swing/signals/{id}/regrade) ------------------------
//
// A regrade re-scores one pending wheel card at today's prices on the
// instance (backend swing_trader/wheel_manual.py). The POST returns at once
// with the row's `regrade` block queued; the result lands on the signal row
// about a minute later, which the signals list returns. A done + rescored
// regrade rewrites the row's score, proposal and context in place and keeps
// what the card said before in `regrade.previous`. It never changes the
// signal's status (operator ruling 2026-11-18): a card that no longer
// qualifies stays pending with a warning, and a failed regrade keeps the old
// values.

/** A regrade still queued or running after this long is dead (wheel_manual.REGRADE_STALE_S). */
export const REGRADE_STALE_MS = 5 * 60 * 1000

function regradeOf(signal) {
  const r = signal?.regrade
  return r && typeof r === 'object' ? r : null
}

function parseMs(value) {
  if (value == null || value === '') return null
  const t = Date.parse(value)
  return Number.isFinite(t) ? t : null
}

/**
 * 'idle' | 'live' | 'stale' | 'done' | 'failed'. A queued or running
 * regrade is live while its request is at most REGRADE_STALE_MS old (the
 * server's own rule, so a click it would answer 409 stays disabled), and
 * stale after that or when undated: the server lets it be asked for again.
 */
export function regradeState(signal, nowMs = Date.now()) {
  const r = regradeOf(signal)
  const status = r?.status
  if (status === 'done' || status === 'failed') return status
  if (status === 'queued' || status === 'running') {
    const at = parseMs(r.requested_at)
    return at != null && nowMs - at <= REGRADE_STALE_MS ? 'live' : 'stale'
  }
  return 'idle'
}

/** True while any card has a live regrade: the list is then re-read every few seconds. */
export function anyRegradeLive(signals, nowMs = Date.now()) {
  return (signals || []).some(s => regradeState(s, nowMs) === 'live')
}

/** The card's Regrade button: only on a pending wheel card, disabled while one is live. */
export function regradeControl(signal, nowMs = Date.now()) {
  const show = signal?.lane === 'wheel' && signal?.status === 'pending'
  const live = show && regradeState(signal, nowMs) === 'live'
  return { show, disabled: live, label: live ? 'Regrading…' : 'Regrade' }
}

const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const NY_STAMP = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York', weekday: 'short', year: 'numeric', month: 'numeric', day: 'numeric',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})

/** "Fri 20 Nov, 10:42 ET" for epoch ms, New York time. Months are built here, never ICU's "Sept". */
function fmtNyStamp(ms) {
  const p = Object.fromEntries(NY_STAMP.formatToParts(new Date(ms)).map(x => [x.type, x.value]))
  return `${p.weekday} ${Number(p.day)} ${MONTHS_SHORT[Number(p.month) - 1]}, ${p.hour}:${p.minute} ET`
}

/**
 * When the card's current score was made: `regrade.scored_at` (set by a
 * regrade that rescored it, carried through later blocks), else
 * `created_at`. "Scored Fri 20 Nov, 10:42 ET".
 */
export function scoredAtLabel(signal) {
  const at = parseMs(regradeOf(signal)?.scored_at || signal?.created_at)
  return at == null ? 'Score time unknown' : `Scored ${fmtNyStamp(at)}`
}

/** "+8.0%" / "-3.2%" / "0.0%" from `before` to `after`, or null. */
export function pctMove(before, after) {
  const a = finiteOrNull(before)
  const b = finiteOrNull(after)
  if (a == null || b == null || a <= 0) return null
  const pct = Number(((b - a) / a * 100).toFixed(1))
  if (pct === 0) return '0.0%'
  return `${pct > 0 ? '+' : '-'}${Math.abs(pct).toFixed(1)}%`
}

function fmtScore(value) {
  const n = finiteOrNull(value)
  return n == null ? '—' : String(n)
}

/**
 * The before -> after rows of a done regrade that rescored the card:
 * [{ label, before, after, move }] for score, stock price (move = its
 * percent change), strike and premium. null otherwise: rescored false means
 * the name failed today's screen and nothing on the card changed.
 */
export function regradeChanges(signal) {
  const r = regradeOf(signal)
  if (r?.status !== 'done' || r.rescored !== true) return null
  const prev = r.previous && typeof r.previous === 'object' ? r.previous : {}
  const p = signal?.proposal && typeof signal.proposal === 'object' ? signal.proposal : {}
  const stock = signal?.context?.stock_price
  return [
    { label: 'Score', before: fmtScore(prev.score), after: fmtScore(signal?.score), move: null },
    { label: 'Stock', before: fmtUsd(prev.stock_price), after: fmtUsd(stock), move: pctMove(prev.stock_price, stock) },
    { label: 'Strike', before: fmtUsd(prev.strike), after: fmtUsd(p.strike), move: null },
    { label: 'Premium', before: fmtUsd(prev.premium_est), after: fmtUsd(p.premium_est), move: null },
  ]
}

/**
 * The card's regrade warnings, [] when none. "No longer qualifies" whenever
 * `qualifies` is false, whatever the status: the server carries it through
 * later queued and failed blocks until a done regrade overwrites it. Beside
 * it, a failed regrade (the instance's error, verbatim) or a stale one (read
 * as failed; it can be asked for again).
 */
export function regradeWarnings(signal, nowMs = Date.now()) {
  const r = regradeOf(signal)
  const out = []
  if (r?.qualifies === false) {
    const reason = String(r.reason ?? '').trim()
    out.push(reason ? `No longer qualifies: ${reason}` : 'No longer qualifies')
  }
  const state = regradeState(signal, nowMs)
  if (state === 'failed') {
    const error = String(r.error ?? '').trim()
    out.push(error ? `Regrade failed: ${error}` : 'Regrade failed')
  } else if (state === 'stale') {
    out.push('Regrade failed: no result after 5 minutes. You can regrade again.')
  }
  return out
}

/**
 * The review opened on `reviewed` (the row the operator read); `live` is
 * the card's row now. null while their regrade counts match. Otherwise a
 * regrade landed while the review was open: { from, to, rows }, the count
 * change and the terms that differ, [{ label, before, after, move }] for
 * score, stock price (move = its percent change), strike, expiry, qty and
 * premium. rows is empty when the regrade changed none of them (it did not
 * rescore); the server still refuses the old count.
 */
export function reviewDrift(reviewed, live) {
  if (!reviewed || !live) return null
  const from = regradeCountOf(reviewed)
  const to = regradeCountOf(live)
  if (from === to) return null
  const fields = [
    ['Score', s => fmtScore(s?.score)],
    ['Stock', s => fmtUsd(s?.context?.stock_price)],
    ['Strike', s => fmtUsd(s?.proposal?.strike)],
    ['Expiry', s => (s?.proposal?.expiry ? String(s.proposal.expiry) : '—')],
    ['Qty', s => fmtCount(s?.proposal?.qty)],
    ['Premium', s => fmtUsd(s?.proposal?.premium_est)],
  ]
  const rows = fields
    .map(([label, read]) => ({
      label, before: read(reviewed), after: read(live),
      move: label === 'Stock' ? pctMove(reviewed?.context?.stock_price, live?.context?.stock_price) : null,
    }))
    .filter(row => row.before !== row.after)
  return { from, to, rows }
}

function regradeKey(signal) {
  const r = regradeOf(signal)
  return r ? `${r.status ?? ''}|${r.requested_at ?? ''}` : ''
}

/**
 * The refused-regrade messages (signal id -> text) still worth showing after
 * a load: an entry goes when its card left the list, or when the card's
 * regrade status or requested_at changed since the previous rows (the
 * refusal is about a state that is gone). A card absent before keeps it.
 */
export function pruneRegradeErrors(errors, beforeRows, afterRows) {
  const before = new Map((beforeRows || []).map(s => [s?.id, s]))
  const after = new Map((afterRows || []).map(s => [s?.id, s]))
  const keep = {}
  for (const [id, text] of Object.entries(errors || {})) {
    if (!after.has(id)) continue
    if (before.has(id) && regradeKey(before.get(id)) !== regradeKey(after.get(id))) continue
    keep[id] = text
  }
  return keep
}

/**
 * The regrade block a POST just returned (`local`: signal id -> block), kept
 * on its row until a poll shows it: a poll already in flight when the POST
 * answered can carry the row from before. Each row gets whichever block was
 * requested later (both stamps are the server's); an entry is dropped once
 * the server's row has caught up, or the card is gone. Returns { rows, local }.
 */
export function applyLocalRegrades(rows, local) {
  const keep = {}
  const out = (rows || []).map(row => {
    const mine = local?.[row?.id]
    if (!mine) return row
    const theirs = parseMs(row?.regrade?.requested_at)
    const ours = parseMs(mine.requested_at)
    if (theirs != null && (ours == null || theirs >= ours)) return row
    keep[row.id] = mine
    return { ...row, regrade: mine }
  })
  return { rows: out, local: keep }
}

/** "The instance is not running", with the server's detail appended (plan, UI behaviour). */
function notRunningMessage(text) {
  return text ? `Instance is not running: ${text}` : 'Instance is not running.'
}

/**
 * A failed POST .../regrade: { message, reload, stopPolling }. 409: one is
 * already under way (`reload`: a re-read shows it). 503: the instance is not
 * running, or the request could not be queued. 400/404: no longer a pending
 * wheel card; a re-read settles it.
 */
export function classifyRegradeFailure(status, detail) {
  const text = detailText(detail)
  if (status === 401) return { message: 'Session expired — please sign in again.', reload: false, stopPolling: true }
  if (status === 409) return { message: 'Regrade already under way.', reload: true, stopPolling: false }
  if (status === 503) return { message: notRunningMessage(text), reload: false, stopPolling: false }
  if (status === 400 || status === 404) {
    return { message: text || 'This card can no longer be regraded.', reload: true, stopPolling: false }
  }
  if (!status) return { message: text || 'Could not reach the server.', reload: false, stopPolling: false }
  return { message: text || `Regrade failed (${status})`, reload: false, stopPolling: false }
}

// -- Manual wheel run (POST / GET .../wheel/scan) -------------------------------
//
// Review only: a run adds pending cards and never places an order, even at
// score 75+ (operator ruling 2026-11-18). The server's `in_progress` is the
// truth: it already reads a run whose heartbeat stopped as not in progress.

function count(value) {
  const n = finiteOrNull(value)
  return n == null ? 0 : Math.trunc(n)
}

function runProgress(run) {
  const phase = run?.phase
  if (phase === 'queued') return 'Queued: waiting for the instance to start it'
  if (phase === 'screening') return 'Screening the wheel universe'
  if (phase === 'earnings') {
    return `Checking earnings dates · ${count(run.passed_screen)} of ${count(run.scanned)} passed the screen`
  }
  if (phase === 'scoring') {
    const found = count(run.new_pending)
    return `Scoring with the AI · ${count(run.scored)} of ${count(run.to_score)} scored · ` +
      `${found} new card${found === 1 ? '' : 's'} so far`
  }
  return 'Running'
}

function runCounts(run) {
  return [
    { label: 'Scanned', value: count(run.scanned) },
    { label: 'Passed screen', value: count(run.passed_screen) },
    { label: 'Scored', value: count(run.scored) },
    { label: 'New cards', value: count(run.new_pending) },
    { label: 'Rejected', value: count(run.rejected) },
    { label: 'Already had a card', value: count(run.skipped_existing) },
    { label: 'Errors', value: count(run.errors) },
  ]
}

/**
 * What the wheel panel shows for the newest run (GET .../wheel/scan's `run`
 * and `in_progress`): { state, when, progress, counts, error }.
 * state: 'none' | 'running' | 'done' | 'failed' | 'stopped' (still marked
 * running, but the server says it is not in progress: it died). `when` is
 * the finish time, or the start while running or stopped.
 */
export function manualRunView(run, inProgress) {
  const row = run && typeof run === 'object' ? run : null
  const view = { state: 'none', when: '', progress: '', counts: [], error: '' }
  if (inProgress) {
    const at = parseMs(row?.started_at)
    return { ...view, state: 'running', when: at == null ? '' : fmtNyStamp(at), progress: runProgress(row) }
  }
  if (!row) return view
  const status = row.status
  const at = parseMs(status === 'done' || status === 'failed' ? row.finished_at : row.started_at)
  const when = at == null ? '' : fmtNyStamp(at)
  if (status === 'done') return { ...view, state: 'done', when, counts: runCounts(row) }
  if (status === 'failed') {
    const error = String(row.error ?? '').trim()
    return { ...view, state: 'failed', when, error: error ? `Run failed: ${error}` : 'Run failed' }
  }
  return {
    ...view, state: 'stopped', when,
    error: 'The last run stopped before it finished (the instance may have restarted). You can run it again.',
  }
}

/**
 * A failed POST .../wheel/scan: { message, refresh, stopPolling }. 409: a
 * run is already under way (`refresh`: read it and show its progress). 503:
 * the instance is not running, or the request could not be queued.
 */
export function classifyRunFailure(status, detail) {
  const text = detailText(detail)
  if (status === 401) return { message: 'Session expired — please sign in again.', refresh: false, stopPolling: true }
  if (status === 409) return { message: 'A wheel run is already under way.', refresh: true, stopPolling: false }
  if (status === 503) return { message: notRunningMessage(text), refresh: false, stopPolling: false }
  if (!status) return { message: text || 'Could not reach the server.', refresh: false, stopPolling: false }
  return { message: text || `Could not start the run (${status})`, refresh: false, stopPolling: false }
}
