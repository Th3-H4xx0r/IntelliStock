<!-- frontend/src/components/swing/PendingSignalsCard.vue
     AI-scored swing and wheel candidates in the review band (from the review
     threshold up to the auto-approve threshold) wait here for a human.

     An approval makes the API enqueue a submit_order live command, and the
     broker rebuilds the order at the live price, so the numbers on a card
     are the proposal, not the fill.

     A decision cannot be undone from here (swing_trader.approvals.decide),
     though a transient broker refusal returns an approval to pending. So
     every button goes through a confirm step, is disabled while its request is in flight, and a
     synchronous guard drops a second click that lands before Vue re-renders.
     A 400/404/409 means the signal is no longer pending, usually because
     another device decided first. The card goes, the server's reason is shown,
     and the next poll brings the card back only if it really is still pending.
     A decided card is hidden only from a poll that was already in flight when
     the decision landed (FW-api-I2): after that the server's status governs,
     so a signal the broker puts back to pending shows again.

     An approval no broker command has claimed for more than 2 minutes (the
     instance stopped, or the broker's handler returned early) is listed
     under "Approved, not yet sent" with a Re-send button (fix wave item 3).
     The server refuses a re-send while a command for it is still queued, and
     the broker's approved -> submitted claim ignores a second copy.

     A 202 (the approval or re-send is recorded, but its delivery could not be
     confirmed) puts the signal on a "Waiting for the broker" card with an
     "uncertain — waiting for the broker" badge (follow-up 2). While one
     waits, each poll also reads the submitted and failed lists; a poll begun
     after the 202 that finds the signal pending brings its pending card
     back, and one that finds it submitted with an order key (the broker sent
     it; a bare submitted is only its claim, seams I-2) or failed says so on
     the card until the operator dismisses it. One that still finds it approved more
     than 2 minutes after the 202 moves it to "Approved, not yet sent", with
     Re-send and Dismiss; a waiting card offers Dismiss after 2 minutes in
     any case (round 3 FU-1). -->
<template>
  <section class="min-w-0" aria-labelledby="pending-signals-title">
    <div class="flex items-center justify-between gap-3 min-h-8 mb-3">
      <div class="min-w-0">
        <h2 id="pending-signals-title" class="text-base font-medium text-slate-100">
          Pending AI signals <span v-if="loaded" class="text-slate-500 tabular-nums font-normal ml-1">{{ signals.length }}</span>
        </h2>
        <p class="text-sm text-slate-500 mt-0.5">Scored candidates waiting for your review</p>
      </div>
      <button
        @click="load"
        :disabled="loading"
        class="ui-icon-btn shrink-0 disabled:opacity-40"
        title="Refresh"
        aria-label="Refresh"
      >
        <span class="material-symbols-outlined text-[18px]" :class="loading ? 'animate-spin' : ''">
          {{ loading ? 'progress_activity' : 'refresh' }}
        </span>
      </button>
    </div>

    <div class="ui-card overflow-hidden">
      <div v-if="notice || loadError" class="px-5 pt-4 space-y-2">
        <div v-if="notice" class="rounded-lg border px-4 py-3 text-sm" :class="noticeClass">
          {{ notice.text }}
        </div>
        <div v-if="loadError" class="rounded-lg border border-loss/20 bg-loss/5 px-4 py-3 text-sm text-loss">
          {{ loadError }}
        </div>
      </div>

      <div v-if="sections.loadingText">
        <span class="sr-only">Loading</span>
        <div class="px-5 py-4 space-y-3" aria-hidden="true">
          <div class="h-4 w-40 ui-skel"></div>
          <div class="h-4 w-64 ui-skel"></div>
        </div>
      </div>
      <div
        v-else-if="sections.empty"
        class="px-6 py-10 text-center"
      >
        <p class="text-sm text-slate-400">Nothing waiting for review</p>
        <p class="text-xs text-slate-500 mt-1">
          Scores from the review threshold up to the auto-approve threshold wait here.
        </p>
      </div>

      <div v-else-if="sections.pending" class="divide-y divide-white/[0.05]">
        <div
          v-for="s in signals"
          :id="swingSignalAnchor(s.id)"
          :key="s.id"
          class="px-5 py-4 scroll-mt-24"
        >
          <div class="flex items-baseline gap-x-3 gap-y-1 flex-wrap">
            <span class="text-sm font-medium text-slate-100">{{ s.symbol }}</span>
            <span class="text-xs text-slate-400 capitalize">{{ s.lane }}</span>
            <span
              class="text-xs tabular-nums"
              :class="scoreClass(s.score)"
              :title="s.recommendation || ''"
            >Score {{ s.score ?? '—' }}</span>
            <span class="text-xs text-slate-500 tabular-nums">
              Session {{ s.session || '—' }} · {{ fmtWhen(s.created_at) }}
            </span>
          </div>

          <div class="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-3 mt-3 max-w-3xl">
            <div v-for="row in proposalRows(s)" :key="row.label" class="min-w-0">
              <div class="text-xs text-slate-500">{{ row.label }}</div>
              <div
                class="text-sm text-slate-100 tabular-nums truncate mt-0.5"
                :class="row.mono ? 'font-mono text-[13px]' : ''"
                :title="row.value"
              >{{ row.value }}</div>
            </div>
          </div>

          <p v-if="s.reasoning" class="text-sm text-slate-300 mt-3 leading-relaxed whitespace-pre-line break-words max-w-3xl">
            {{ expanded[s.id] ? String(s.reasoning).trim() : reasoningPreview(s.reasoning).text }}
            <button
              v-if="reasoningPreview(s.reasoning).truncated"
              @click="toggleExpanded(s.id)"
              class="ml-1 text-xs text-slate-400 hover:text-slate-100 underline-offset-2 hover:underline transition-colors duration-150 ease-out"
            >{{ expanded[s.id] ? 'Show less' : 'Show more' }}</button>
          </p>
          <p v-if="joinKeyRisks(s.key_risks)" class="text-xs text-slate-400 mt-2 break-words">
            <span class="text-slate-500">Risks</span> {{ joinKeyRisks(s.key_risks) }}
          </p>

          <!-- Confirm step: nothing is sent until this second click. An
               approval's second click is in the order review modal below;
               a rejection confirms here. -->
          <div
            v-if="confirming[s.id] === 'reject'"
            class="mt-3 rounded-lg border border-white/[0.07] bg-raised px-4 py-3"
          >
            <p class="text-sm text-slate-200">{{ confirmPrompt(s, confirming[s.id]) }}</p>
            <input
              v-model="reasons[s.id]"
              maxlength="500"
              placeholder="Reason (optional)"
              :disabled="!!deciding[s.id]"
              class="ui-input h-8 mt-3"
            />
            <div class="flex gap-2 mt-3 flex-wrap">
              <button
                @click="submit(s)"
                :disabled="!!deciding[s.id]"
                :class="decisionClass(confirming[s.id])"
              >{{ deciding[s.id] ? 'Working…' : `Confirm ${DECISION_LABELS[confirming[s.id]]}` }}</button>
              <button
                @click="cancelConfirm(s.id)"
                :disabled="!!deciding[s.id]"
                class="ui-btn-ghost"
              >Cancel</button>
            </div>
          </div>
          <div v-else class="flex gap-2 mt-3 flex-wrap">
            <button
              v-for="d in decisionsFor(s)"
              :key="d"
              @click="onDecisionClick(s, d)"
              :disabled="!!deciding[s.id]"
              class="ui-btn"
            >{{ DECISION_LABELS[d] }}</button>
          </div>
        </div>
      </div>

      <div v-if="sections.waiting" class="border-t border-white/[0.06]">
        <p class="h-10 px-5 flex items-center gap-2 text-xs text-slate-400 border-b border-white/[0.06]">
          <span class="ui-dot ui-dot-warn"></span>
          Waiting for the broker <span class="text-slate-500 tabular-nums">{{ Object.keys(uncertain).length }}</span>
        </p>
        <div class="divide-y divide-white/[0.05]">
          <div
            v-for="(entry, id) in uncertain"
            :key="`uncertain-${id}`"
            class="px-5 py-4"
          >
            <div class="flex items-baseline gap-x-3 gap-y-1 flex-wrap">
              <span class="text-sm font-medium text-slate-100">{{ entry.signal.symbol }}</span>
              <span class="text-xs text-slate-400 capitalize">{{ entry.signal.lane }}</span>
              <span class="text-xs" :class="uncertainBadgeClass(entry)">
                {{ uncertainBadge(entry) }}
              </span>
              <span class="text-xs text-slate-500 tabular-nums">Session {{ entry.signal.session || '—' }}</span>
            </div>
            <p v-if="!entry.resolved" class="text-sm text-slate-200 mt-2 leading-relaxed">{{ WAITING_COPY }}</p>
            <p v-else-if="entry.resolved === 'submitted'" class="text-sm text-slate-300 mt-2">
              The broker submitted it. Check open orders for the fill.
            </p>
            <p v-else class="text-sm text-slate-300 mt-2">
              It failed at the broker; the live log says why.
            </p>
            <div v-if="waitingCanDismiss(entry, nowMs)" class="flex gap-2 mt-3">
              <button
                @click="dismissUncertain(id)"
                class="ui-btn-ghost"
              >Dismiss</button>
            </div>
          </div>
        </div>
      </div>

      <!-- Round 3 minor 2: not gated on a good pending read. -->
      <div v-if="sections.stuck" class="border-t border-white/[0.06]">
        <p class="h-10 px-5 flex items-center gap-2 text-xs text-slate-400 border-b border-white/[0.06]">
          <span class="ui-dot ui-dot-warn"></span>
          Approved, not yet sent <span class="text-slate-500 tabular-nums">{{ stuck.length }}</span>
        </p>
        <div class="divide-y divide-white/[0.05]">
          <div
            v-for="s in stuck"
            :key="`stuck-${s.id}`"
            class="px-5 py-4"
          >
            <div class="flex items-baseline gap-x-3 gap-y-1 flex-wrap">
              <span class="text-sm font-medium text-slate-100">{{ s.symbol }}</span>
              <span class="text-xs text-slate-400 capitalize">{{ s.lane }}</span>
              <span class="text-xs text-slate-400">
                {{ s.status === 'approved_half' ? 'Approved ½' : 'Approved' }}
              </span>
              <span class="text-xs text-slate-500 tabular-nums">Session {{ s.session || '—' }}</span>
            </div>
            <p class="text-sm text-slate-300 mt-2">{{ stuckLabel(s, nowMs) }}</p>

            <p v-if="resendBlockedReason(s, today)" class="text-xs text-slate-500 mt-2">
              {{ resendBlockedReason(s, today) }}
            </p>
            <div v-if="resendConfirming[s.id] && !resendBlockedReason(s, today)" class="mt-3 rounded-lg border border-white/[0.07] bg-raised px-4 py-3">
              <p class="text-sm text-slate-200">{{ resendPrompt(s) }}</p>
              <div class="flex gap-2 mt-3 flex-wrap">
                <button
                  @click="resend(s)"
                  :disabled="!!resending[s.id]"
                  class="ui-btn-primary h-8 px-3 text-[13px]"
                >{{ resending[s.id] ? 'Working…' : 'Confirm re-send' }}</button>
                <button
                  @click="cancelResend(s.id)"
                  :disabled="!!resending[s.id]"
                  class="ui-btn-ghost"
                >Cancel</button>
              </div>
            </div>
            <div v-else class="flex gap-2 mt-3 flex-wrap">
              <button
                v-if="!resendBlockedReason(s, today)"
                @click="startResend(s.id)"
                :disabled="!!resending[s.id]"
                class="ui-btn"
              >Re-send</button>
              <button
                @click="dismissStuck(s.id)"
                :disabled="!!resending[s.id]"
                class="ui-btn-ghost"
              >Dismiss</button>
            </div>
          </div>
        </div>
      </div>

      <p class="px-5 py-3 border-t border-white/[0.06] text-xs text-slate-500 leading-relaxed">
        Approval rebuilds the order at the live price: shares, stop and target for swing, strike and expiry for the wheel.
      </p>
    </div>

    <!-- Order review: Approve / Approve ½ open it; its confirm calls submit(). -->
    <SwingOrderReviewModal
      v-if="review"
      :signal="review.signal"
      :decision="review.decision"
      :api-base="apiBase"
      :cash="cash"
      :reason="reasons[review.signal.id] || ''"
      :busy="reviewBusy"
      :result="shownReviewResult"
      :error="reviewError"
      @update:reason="reasons[review.signal.id] = $event"
      @cancel="cancelReview"
      @confirm="confirmReview"
      @close="closeReview"
    />
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { getToken } from '../../utils/auth.js'
import SwingOrderReviewModal from './SwingOrderReviewModal.vue'
import { swingSignalAnchor } from '../../utils/swingReview.js'
import {
  DECISION_LABELS,
  classifyDecisionFailure,
  classifyDecisionSuccess,
  classifyResendFailure,
  confirmPrompt,
  createDecisionLatch,
  createInFlightGuard,
  decisionsFor,
  detailText,
  joinKeyRisks,
  addUncertain,
  cardSections,
  createStuckMemory,
  foldSignalLoad,
  nyDate,
  proposalRows,
  reasoningPreview,
  resendBlockedReason,
  resendPrompt,
  resendSuccess,
  scoreTone,
  stuckLabel,
  uncertainBadge,
  uncertainListsNeeded,
  WAITING_COPY,
  waitingCanDismiss,
} from '../../utils/swing.js'

const POLL_MS = 30000

const props = defineProps({
  instanceId: { type: String, required: true },
  apiBase: { type: String, required: true },
  /** The wheel account's cash (from the wheel panel), for the review's collateral warning. */
  cash: { type: Number, default: null },
})
// pending-ids: the live pending queue, for the wheel's scan rows.
// decided: a decision request settled (the wheel re-reads its signal links).
const emit = defineEmits(['pending-ids', 'decided'])

const signals = ref([])
const loading = ref(false)
const loaded = ref(false)
const loadError = ref('')
const notice = ref(null)      // { tone: 'ok' | 'warn' | 'error', text }
const confirming = ref({})    // signal id -> decision awaiting its confirm click
const deciding = ref({})      // signal id -> true while the POST is in flight
const reasons = ref({})
const expanded = ref({})
const guard = createInFlightGuard()
const latch = createDecisionLatch() // hides a decided card from a poll that raced its 2xx
const stuck = ref([])            // approved, and no broker command has claimed it for 2+ minutes
const nowMs = ref(Date.now())    // the clock stuck labels were computed at
const today = computed(() => nyDate(nowMs.value)) // the New York session a re-send needs
const resendConfirming = ref({}) // signal id -> true while its Re-send awaits the confirm click
const resending = ref({})        // signal id -> true while the POST .../resend is in flight
const resendGuard = createInFlightGuard()
const resentAt = new Map()       // signal id -> epoch ms of this page's last re-send (or refusal)
const uncertain = ref({})        // signal id -> { signal, since, sinceMs, resolved } after a 202 (follow-up 2)
const stuckMemory = createStuckMemory() // dismissed and joined stuck ids on this page (round 3 FU-1, round 4)
const sections = computed(() => cardSections({
  loaded: loaded.value, loading: loading.value, signals: signals.value,
  stuck: stuck.value, uncertain: uncertain.value,
}))
let pollTimer = null
let noticeTimer = null

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

function signalsUrl() {
  return `${props.apiBase}/instances/${encodeURIComponent(props.instanceId)}/swing/signals`
}

function fmtWhen(value) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString()
}

function omit(map, id) {
  const next = { ...map }
  delete next[id]
  return next
}

function showNotice(tone, text) {
  notice.value = { tone, text }
  clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => { notice.value = null }, 8000)
}

// Notices: gain for a success, loss for a failure, neutral for a stale card.
const noticeClass = computed(() => ({
  ok: 'border-gain/20 bg-gain/5 text-gain',
  warn: 'border-white/[0.08] bg-raised text-slate-200',
  error: 'border-loss/20 bg-loss/5 text-loss',
}[notice.value?.tone] || ''))

// The score is a figure, not a money move: brighter text for a higher
// score, no colour.
function scoreClass(score) {
  return {
    high: 'text-slate-200',
    mid: 'text-slate-400',
    low: 'text-slate-500',
    unknown: 'text-slate-500',
  }[scoreTone(score)]
}

// The confirm button: an approval sends a real order, so it is the one
// primary action on the card; a rejection stays a plain button.
function decisionClass(decision) {
  return decision === 'reject'
    ? 'ui-btn'
    : 'ui-btn-primary h-8 px-3 text-[13px]'
}

function toggleExpanded(id) {
  expanded.value = { ...expanded.value, [id]: !expanded.value[id] }
}

function removeCard(id) {
  signals.value = signals.value.filter(s => s.id !== id)
  confirming.value = omit(confirming.value, id)
  reasons.value = omit(reasons.value, id)
}

async function fetchSignals(status) {
  const res = await fetch(`${signalsUrl()}?status=${status}`, { headers: authHeaders() })
  if (res.status === 401) {
    throw Object.assign(new Error('Session expired — please sign in again.'), { unauthorized: true })
  }
  if (!res.ok) {
    let detail = ''
    try { detail = detailText((await res.json())?.detail) } catch { /* keep */ }
    throw new Error(detail || `Could not load signals (${res.status})`)
  }
  return res.json()
}

async function load() {
  if (loading.value) return
  loading.value = true
  const generation = latch.beginLoad()
  try {
    // The approved lists feed the "Approved, not yet sent" cards, and they
    // end the hide on a card this page decided (FW-api-I2). Each read settles
    // on its own (follow-up 4): one failing never stops the others.
    const statuses = ['pending', 'approved', 'approved_half']
    if (uncertainListsNeeded(uncertain.value)) statuses.push('submitted', 'failed')
    const settled = await Promise.allSettled(statuses.map(fetchSignals))
    const results = Object.fromEntries(statuses.map((status, i) => [status, settled[i]]))
    nowMs.value = Date.now()
    const out = foldSignalLoad({
      generation, latch, results, nowMs: nowMs.value, resentAt, uncertain: uncertain.value,
      dismissed: stuckMemory.dismissed, joined: stuckMemory.joined,
      previous: { signals: signals.value, stuck: stuck.value },
    })
    const live = new Set(out.signals.map(s => s.id))
    // A card that vanished server-side takes its half-finished confirm with it.
    for (const id of Object.keys(confirming.value)) {
      if (!live.has(id) && !deciding.value[id]) confirming.value = omit(confirming.value, id)
    }
    const stuckIds = new Set(out.stuck.map(s => s.id))
    for (const id of Object.keys(resendConfirming.value)) {
      if (!stuckIds.has(id) && !resending.value[id]) resendConfirming.value = omit(resendConfirming.value, id)
    }
    signals.value = out.signals
    stuck.value = out.stuck
    uncertain.value = out.uncertain
    stuckMemory.joined = out.joined
    if (out.unauthorized) stopPolling()
    loadError.value = out.error
    if (out.pendingLoaded) loaded.value = true
  } finally {
    loading.value = false
  }
}

function dismissUncertain(id) {
  uncertain.value = omit(uncertain.value, id)
}

function dismissStuck(id) {
  stuckMemory.dismiss(id)
  removeStuck(id)
}

// The badge is a word: gain once the broker submitted it, loss if it
// failed, amber while delivery is still uncertain.
function uncertainBadgeClass(entry) {
  if (entry.resolved === 'submitted') return 'text-gain'
  if (entry.resolved === 'failed') return 'text-loss'
  return 'text-amber-400'
}

function startResend(id) {
  if (resending.value[id]) return
  resendConfirming.value = { ...resendConfirming.value, [id]: true }
}

function cancelResend(id) {
  if (resending.value[id]) return
  resendConfirming.value = omit(resendConfirming.value, id)
}

function removeStuck(id) {
  stuck.value = stuck.value.filter(s => s.id !== id)
  resendConfirming.value = omit(resendConfirming.value, id)
}

async function resend(signal) {
  if (!resendConfirming.value[signal.id] || !resendGuard.tryAcquire(signal.id)) return
  resending.value = { ...resending.value, [signal.id]: true }
  try {
    const res = await fetch(`${signalsUrl()}/${encodeURIComponent(signal.id)}/resend`, {
      method: 'POST',
      headers: authHeaders(),
    })
    if (res.ok) {
      let body = null
      try { body = await res.json() } catch { /* the body is optional */ }
      const outcome = resendSuccess(res.status, body, signal)
      resentAt.set(signal.id, Date.now())
      removeStuck(signal.id)
      if (outcome.uncertain) {
        stuckMemory.noteDecision(signal.id) // R3-2
        uncertain.value = addUncertain(uncertain.value, signal, latch.current(), Date.now())
      } else {
        showNotice(outcome.tone, outcome.message)
      }
      return
    }
    let detail = ''
    try { detail = (await res.json())?.detail } catch { /* keep */ }
    const verdict = classifyResendFailure(res.status, detail)
    if (verdict.stopPolling) {
      stopPolling()
      loadError.value = verdict.message
    }
    if (verdict.snooze) resentAt.set(signal.id, Date.now())
    if (verdict.removeCard) removeStuck(signal.id)
    showNotice(verdict.removeCard ? 'warn' : 'error', verdict.message)
  } catch (e) {
    showNotice('error', classifyResendFailure(0, e?.message).message)
  } finally {
    resendGuard.release(signal.id)
    resending.value = omit(resending.value, signal.id)
  }
}

function startConfirm(id, decision) {
  if (deciding.value[id]) return
  confirming.value = { ...confirming.value, [id]: decision }
}

function cancelConfirm(id) {
  if (deciding.value[id]) return
  confirming.value = omit(confirming.value, id)
}

async function submit(signal) {
  const decision = confirming.value[signal.id]
  if (!decision || !guard.tryAcquire(signal.id)) return
  deciding.value = { ...deciding.value, [signal.id]: true }
  try {
    const body = { decision }
    const reason = String(reasons.value[signal.id] || '').trim()
    if (reason) body.reason = reason
    const res = await fetch(`${signalsUrl()}/${encodeURIComponent(signal.id)}/decision`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(body),
    })
    if (res.ok) {
      // 202: recorded, but the broker command may or may not be queued (the
      // order may be in flight). The card still goes; the notice says so.
      let body = null
      try { body = await res.json() } catch { /* the body is optional */ }
      const outcome = classifyDecisionSuccess(res.status, body, signal, decision)
      latch.record(signal.id)
      removeCard(signal.id)
      stuckMemory.noteDecision(signal.id) // R3-2: a new decision forgets an old dismissal
      if (outcome.uncertain) {
        // Follow-up 2: the advice stays on a card until a poll settles it.
        uncertain.value = addUncertain(uncertain.value, { ...signal, ...(body?.signal || {}), id: signal.id },
          latch.current(), Date.now())
      } else {
        showNotice(outcome.tone, outcome.message)
      }
      emit('decided')
      return { kind: outcome.uncertain ? 'uncertain' : 'sent', message: outcome.message }
    }
    let detail = ''
    try { detail = (await res.json())?.detail } catch { /* keep */ }
    const verdict = classifyDecisionFailure(res.status, detail)
    if (verdict.stopPolling) {
      stopPolling()
      loadError.value = verdict.message
    }
    if (verdict.removeCard) removeCard(signal.id)
    showNotice(verdict.kind === 'stale' ? 'warn' : 'error', verdict.message)
    emit('decided')
    return { kind: verdict.kind === 'stale' ? 'stale' : 'failed', message: verdict.message }
  } catch (e) {
    const message = classifyDecisionFailure(0, e?.message).message
    showNotice('error', message)
    return { kind: 'failed', message }
  } finally {
    guard.release(signal.id)
    deciding.value = omit(deciding.value, signal.id)
  }
}

// -- Order review (mirrors iOS SwingOrderReviewSheet) -------------------------
// Approve and Approve ½ open the review instead of the inline confirm. The
// review's confirm calls submit(signal) above, exactly as the inline step
// did: same decision (confirming[id]), same optional reason (reasons[id]),
// same guard against a second click. Reject keeps its inline confirm.
const review = ref(null)        // { signal, decision } while open
const reviewResult = ref(null)  // { kind, message } once the request settled
const reviewError = ref('')     // the last refusal; the confirm stays available
let reviewCloseTimer = null

const reviewBusy = computed(() => !!(review.value && deciding.value[review.value.signal.id]))
// The signal left the queue while the review was open (decided on another
// device, so a poll took its confirm with it): nothing can be sent from here.
const shownReviewResult = computed(() => {
  if (reviewResult.value) return reviewResult.value
  const r = review.value
  if (r && !reviewBusy.value && confirming.value[r.signal.id] !== r.decision) {
    return { kind: 'stale', message: 'This signal is no longer pending — it was decided elsewhere.' }
  }
  return null
})

function onDecisionClick(signal, decision) {
  if (decision === 'reject') {
    startConfirm(signal.id, decision)
    return
  }
  startConfirm(signal.id, decision)
  if (confirming.value[signal.id] !== decision) return
  clearTimeout(reviewCloseTimer)
  review.value = { signal, decision }
  reviewResult.value = null
  reviewError.value = ''
}

async function confirmReview() {
  const r = review.value
  if (!r || reviewBusy.value || shownReviewResult.value) return
  reviewError.value = ''
  const out = await submit(r.signal)
  if (!out || review.value !== r) return
  if (out.kind === 'failed') {
    reviewError.value = out.message
    return
  }
  reviewResult.value = out
  if (out.kind === 'sent' || out.kind === 'uncertain') {
    // As on iOS: the sent mark shows, then the review closes by itself.
    reviewCloseTimer = setTimeout(() => { if (review.value === r) closeReview() }, 1800)
  }
}

function cancelReview() {
  const r = review.value
  if (!r || reviewBusy.value) return
  cancelConfirm(r.signal.id)
  closeReview()
}

function closeReview() {
  if (reviewBusy.value) return
  clearTimeout(reviewCloseTimer)
  const r = review.value
  // A review closed after a refusal leaves no half-finished confirm behind.
  if (r && confirming.value[r.signal.id] === r.decision && !reviewResult.value) cancelConfirm(r.signal.id)
  review.value = null
  reviewResult.value = null
  reviewError.value = ''
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(load, POLL_MS)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(() => {
  load()
  startPolling()
})

onUnmounted(() => {
  stopPolling()
  clearTimeout(noticeTimer)
  clearTimeout(reviewCloseTimer)
})

// The wheel's scan rows read the pending queue to know which rows can jump
// to a card here.
watch(signals, list => emit('pending-ids', list.map(s => s.id)), { immediate: true })

watch(() => props.instanceId, (next, prev) => {
  if (next === prev) return
  clearTimeout(reviewCloseTimer)
  review.value = null
  reviewResult.value = null
  reviewError.value = ''
  signals.value = []
  loaded.value = false
  loadError.value = ''
  confirming.value = {}
  reasons.value = {}
  latch.clear()
  stuck.value = []
  resendConfirming.value = {}
  resentAt.clear()
  uncertain.value = {}
  stuckMemory.clear()
  load()
  startPolling()
})
</script>
