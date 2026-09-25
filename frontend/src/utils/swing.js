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
 */
export function createDecisionLatch() {
  let generation = 0
  const hidden = new Map()          // id -> the generation last started when its 2xx arrived
  return {
    beginLoad() {
      generation += 1
      return generation
    },
    record(id) { hidden.set(id, generation) },
    has(id) { return hidden.has(id) },
    clear() { hidden.clear() },
    apply(loadGeneration, pendingRows, nonPendingIds = []) {
      const seen = new Set(nonPendingIds)
      for (const [id, at] of [...hidden]) {
        if (loadGeneration > at || seen.has(id)) hidden.delete(id)
      }
      return pendingRows.filter(s => !hidden.has(s.id))
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
  if (decision === 'approve') return `Approve ${sym}? The broker rebuilds the order at the live price and checks it before sending. Decisions are final.`
  if (decision === 'approve_half') return `Approve ${sym} at half size? The broker rebuilds the order at the live price and checks it before sending. Decisions are final.`
  return `Reject ${sym}? Decisions are final.`
}

export function decisionSuccessMessage(signal, decision) {
  const sym = signal?.symbol || 'the signal'
  if (decision === 'approve') return `Approved ${sym}. The broker rebuilds and checks the order at the live price; if it refuses, you'll get a notification.`
  if (decision === 'approve_half') return `Approved ${sym} at half size. The broker rebuilds and checks the order at the live price; if it refuses, you'll get a notification.`
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
export function classifyDecisionSuccess(status, body, signal, decision) {
  const uncertain = status === 202 || body?.uncertain === true
  if (!uncertain) return { uncertain: false, tone: 'ok', message: decisionSuccessMessage(signal, decision) }
  const sym = signal?.symbol || 'the signal'
  const message = detailText(body?.detail)
    || `Approval received for ${sym} — the order may be in flight; check the signal status and open orders.`
  return { uncertain: true, tone: 'warn', message }
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
