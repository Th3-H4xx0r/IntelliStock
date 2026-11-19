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
     any case (round 3 FU-1).

     A pending wheel card has a Regrade button: POST .../regrade re-scores it
     at today's prices on the instance, and the result lands on the row about
     a minute later. While any card's regrade is live (queued or running, not
     past the 5-minute stale mark) the list is re-read every 4 s. A regrade
     never changes the card's status: one that no longer qualifies, or failed,
     shows a warning and Approve / Reject stay. Each wheel card says when its
     score was made, and a done + rescored regrade shows before -> after.
     A wheel approval sends the regrade.count of the terms the operator
     reviewed (the row shown when the review opened). A regrade that lands
     while the review is open shows a "Regraded while open" notice with the
     before -> after and a "Use new terms" button; confirming without it gets
     the server's 412, which keeps the card and the review open with the
     server's message, re-reads, and makes the new terms the reviewed ones. -->
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
              v-if="s.source === 'manual_scan'"
              class="text-xs text-slate-400 rounded border border-white/[0.08] px-1.5"
              title="Made by a manual wheel run"
            >Manual run</span>
            <span
              class="text-xs tabular-nums"
              :class="scoreClass(s.score)"
              :title="s.recommendation || ''"
            >Score {{ s.score ?? '—' }}</span>
            <span class="text-xs text-slate-500 tabular-nums">
              Session {{ s.session || '—' }} · {{ s.lane === 'wheel' ? regradeById[s.id].scored : fmtWhen(s.created_at) }}
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

          <p v-if="regradeById[s.id].changes" class="text-xs text-slate-400 mt-3 tabular-nums max-w-3xl">
            <span class="text-slate-500">Regraded:</span>
            <template v-for="(c, i) in regradeById[s.id].changes" :key="c.label">
              <span v-if="i" class="text-slate-600" aria-hidden="true"> · </span>
              <span class="whitespace-nowrap">
                {{ c.label }} {{ c.before }} → <span class="text-slate-200">{{ c.after }}</span><span v-if="c.move" class="text-slate-500"> ({{ c.move }})</span>
              </span>
            </template>
          </p>
          <p
            v-for="w in regradeById[s.id].warnings"
            :key="w"
            class="flex items-start gap-2 text-sm text-slate-300 mt-3 max-w-3xl"
          >
            <span class="ui-dot ui-dot-warn mt-2"></span>
            <span class="break-words">{{ w }}</span>
          </p>
          <p v-if="regradeErrors[s.id]" class="text-sm text-loss mt-2 break-words max-w-3xl" role="alert">
            {{ regradeErrors[s.id] }}
          </p>

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
            <button
              v-if="regradeById[s.id].control.show"
              @click="regrade(s)"
              :disabled="regradeById[s.id].control.disabled || !!regrading[s.id]"
              class="ui-btn-ghost inline-flex items-center gap-1.5 disabled:opacity-60"
              title="Re-score this put at today's prices (about a minute). The card stays pending."
            >
              <span
                v-if="regradeById[s.id].control.disabled || regrading[s.id]"
                class="material-symbols-outlined text-[16px] animate-spin"
                aria-hidden="true"
              >progress_activity</span>
              {{ regrading[s.id] ? 'Regrading…' : regradeById[s.id].control.label }}
            </button>
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
      :signal="reviewSignal"
      :decision="review.decision"
      :api-base="apiBase"
      :cash="cash"
      :reason="reasons[review.signal.id] || ''"
      :busy="reviewBusy"
      :result="shownReviewResult"
      :error="reviewError"
      :drift="reviewDriftView"
      @accept-terms="acceptReviewTerms"
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
  decisionBody,
  decisionsFor,
  detailText,
  joinKeyRisks,
  addUncertain,
  anyRegradeLive,
  applyLocalRegrades,
  cardSections,
  classifyRegradeFailure,
  createStuckMemory,
  foldSignalLoad,
  nyDate,
  proposalRows,
  pruneRegradeErrors,
  reasoningPreview,
  regradeChanges,
  regradeControl,
  regradeWarnings,
  resendBlockedReason,
  reviewDrift,
  resendPrompt,
  resendSuccess,
  scoredAtLabel,
  scoreTone,
  stuckLabel,
  uncertainBadge,
  uncertainListsNeeded,
  WAITING_COPY,
  waitingCanDismiss,
} from '../../utils/swing.js'

const POLL_MS = 30000
// While a card's regrade is live (it takes about a minute).
const REGRADE_POLL_MS = 4000

const props = defineProps({
  instanceId: { type: String, required: true },
  apiBase: { type: String, required: true },
  /** The wheel account's cash (from the wheel panel), for the review's collateral warning. */
  cash: { type: Number, default: null },
  /** Bumped when a manual wheel run finishes: re-read the list for its new cards. */
  refreshKey: { type: Number, default: 0 },
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

// -- Regrade (wheel cards) -----------------------------------------------------
const regrading = ref({})        // signal id -> true while the POST .../regrade is in flight
const regradeErrors = ref({})    // signal id -> why the last regrade request was refused
const regradeGuard = createInFlightGuard()
let localRegrades = {}           // signal id -> the queued block a POST returned (applyLocalRegrades)
let regradeTimer = null
let reloadQueued = false
let disposed = false
// Each card's regrade bits, from the clock the list was last read at.
const regradeById = computed(() => Object.fromEntries(signals.value.map(s => [s.id, {
  control: regradeControl(s, nowMs.value),
  scored: scoredAtLabel(s),
  changes: regradeChanges(s),
  warnings: regradeWarnings(s, nowMs.value),
}])))

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
  regradeErrors.value = omit(regradeErrors.value, id)
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
  const instanceAtStart = props.instanceId
  try {
    // The approved lists feed the "Approved, not yet sent" cards, and they
    // end the hide on a card this page decided (FW-api-I2). Each read settles
    // on its own (follow-up 4): one failing never stops the others.
    const statuses = ['pending', 'approved', 'approved_half']
    if (uncertainListsNeeded(uncertain.value)) statuses.push('submitted', 'failed')
    const settled = await Promise.allSettled(statuses.map(fetchSignals))
    // The instance changed while this read was out: its rows are another
    // instance's. The watcher queued a fresh load (refresh), run in finally.
    if (disposed || props.instanceId !== instanceAtStart) return
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
    // A poll that was in flight when a regrade POST answered can predate it.
    const merged = applyLocalRegrades(out.signals, localRegrades)
    localRegrades = merged.local
    // A refused-regrade message goes once its card left, or the card's
    // regrade moved on (another device's regrade started or finished).
    const keptErrors = pruneRegradeErrors(regradeErrors.value, signals.value, merged.rows)
    if (Object.keys(keptErrors).length !== Object.keys(regradeErrors.value).length) regradeErrors.value = keptErrors
    const stuckIds = new Set(out.stuck.map(s => s.id))
    for (const id of Object.keys(resendConfirming.value)) {
      if (!stuckIds.has(id) && !resending.value[id]) resendConfirming.value = omit(resendConfirming.value, id)
    }
    signals.value = merged.rows
    adoptReviewedAfter412(generation)
    stuck.value = out.stuck
    uncertain.value = out.uncertain
    stuckMemory.joined = out.joined
    if (out.unauthorized) stopPolling()
    loadError.value = out.error
    if (out.pendingLoaded) loaded.value = true
  } finally {
    loading.value = false
    syncRegradePoll()
    if (reloadQueued && !disposed) {
      reloadQueued = false
      load()
    }
  }
}

/** load(), or once more after the load in flight (whose answer may predate the reason). */
function refresh() {
  if (disposed) return
  if (loading.value) {
    reloadQueued = true
    return
  }
  load()
}

// The 4 s re-read runs only beside the regular poll (so a 401 or leaving the
// page stops it) and only while some card's regrade is live.
function syncRegradePoll() {
  const want = pollTimer !== null && anyRegradeLive(signals.value, Date.now())
  if (want && !regradeTimer) {
    regradeTimer = setInterval(load, REGRADE_POLL_MS)
  } else if (!want && regradeTimer) {
    clearInterval(regradeTimer)
    regradeTimer = null
  }
}

async function regrade(signal) {
  if (!regradeControl(signal, Date.now()).show || !regradeGuard.tryAcquire(signal.id)) return
  const instanceAtStart = props.instanceId
  regrading.value = { ...regrading.value, [signal.id]: true }
  regradeErrors.value = omit(regradeErrors.value, signal.id)
  try {
    const res = await fetch(`${signalsUrl()}/${encodeURIComponent(signal.id)}/regrade`, {
      method: 'POST',
      headers: authHeaders(),
    })
    if (disposed || props.instanceId !== instanceAtStart) return
    if (res.ok) {
      let body = null
      try { body = await res.json() } catch { /* read the row from the list instead */ }
      const row = body?.signal
      if (row && typeof row === 'object' && row.regrade && typeof row.regrade === 'object') {
        // Optimistic: the returned row (regrade queued) shows at once.
        localRegrades = { ...localRegrades, [signal.id]: row.regrade }
        signals.value = signals.value.map(s => (s.id === signal.id ? { ...s, ...row, id: s.id } : s))
        nowMs.value = Date.now()
        syncRegradePoll()
      } else {
        refresh()
      }
      return
    }
    let detail = ''
    try { detail = (await res.json())?.detail } catch { /* keep */ }
    const verdict = classifyRegradeFailure(res.status, detail)
    if (verdict.stopPolling) {
      stopPolling()
      loadError.value = verdict.message
      return
    }
    regradeErrors.value = { ...regradeErrors.value, [signal.id]: verdict.message }
    if (verdict.reload) refresh()
  } catch (e) {
    if (disposed || props.instanceId !== instanceAtStart) return
    regradeErrors.value = { ...regradeErrors.value, [signal.id]: classifyRegradeFailure(0, e?.message).message }
  } finally {
    regradeGuard.release(signal.id)
    regrading.value = omit(regrading.value, signal.id)
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

// `shown`: the row whose terms the operator confirmed (the review's live
// row); a wheel approval sends its regrade.count for the server's version check.
async function submit(signal, shown = signal) {
  const decision = confirming.value[signal.id]
  if (!decision || !guard.tryAcquire(signal.id)) return
  deciding.value = { ...deciding.value, [signal.id]: true }
  try {
    const body = decisionBody(decision, reasons.value[signal.id], shown)
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
    // Regraded since the review opened: the card stays pending and the
    // re-read brings its new terms into the review, to approve again.
    const since = latch.current()
    if (verdict.kind === 'regraded') refresh()
    showNotice(verdict.kind === 'stale' || verdict.kind === 'regraded' ? 'warn' : 'error', verdict.message)
    emit('decided')
    const kind = verdict.kind === 'stale' || verdict.kind === 'regraded' ? verdict.kind : 'failed'
    return { kind, message: verdict.message, since }
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
// { signal, decision, reviewed, adoptAfter } while open. `reviewed` is the row
// whose terms the operator accepted: the row shown when the review opened,
// or the live row after "Use new terms" or a 412. Confirm sends ITS
// regrade.count, never the live row's, so a regrade that lands while the
// review is open is refused (412) until the operator has seen it.
const review = ref(null)
const reviewResult = ref(null)  // { kind, message } once the request settled
const reviewError = ref('')     // the last refusal; the confirm stays available
let reviewCloseTimer = null

const reviewBusy = computed(() => !!(review.value && deciding.value[review.value.signal.id]))
// The review shows the card's latest row, so a regrade that lands while it
// is open updates it; the snapshot stands in once the card has left the list.
const reviewSignal = computed(() => {
  const r = review.value
  return r ? (signals.value.find(s => s.id === r.signal.id) || r.signal) : null
})
// A regrade landed while the review was open: the modal says so, with the
// before -> after of the terms, until the operator accepts the new terms.
const reviewDriftView = computed(() => {
  const r = review.value
  return r ? reviewDrift(r.reviewed, reviewSignal.value) : null
})

function acceptReviewTerms() {
  const r = review.value
  if (!r || reviewBusy.value || !reviewSignal.value) return
  r.reviewed = reviewSignal.value
}

// After a 412 the re-read brings the new terms into the review; the first
// load begun after the 412 makes them the reviewed terms (the 412 message
// already told the operator to review them).
function adoptReviewedAfter412(generation) {
  const r = review.value
  if (!r || r.adoptAfter == null || generation <= r.adoptAfter) return
  const row = signals.value.find(s => s.id === r.signal.id)
  if (row) r.reviewed = row
  r.adoptAfter = null
}

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
  review.value = { signal, decision, reviewed: signal, adoptAfter: null }
  reviewResult.value = null
  reviewError.value = ''
}

async function confirmReview() {
  const r = review.value
  if (!r || reviewBusy.value || shownReviewResult.value) return
  reviewError.value = ''
  const out = await submit(r.signal, r.reviewed)
  if (!out || review.value !== r) return
  if (out.kind === 'regraded') r.adoptAfter = out.since
  if (out.kind === 'failed' || out.kind === 'regraded') {
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
  if (regradeTimer) {
    clearInterval(regradeTimer)
    regradeTimer = null
  }
}

onMounted(() => {
  load()
  startPolling()
})

onUnmounted(() => {
  disposed = true
  stopPolling()
  clearTimeout(noticeTimer)
  clearTimeout(reviewCloseTimer)
})

// The wheel's scan rows read the pending queue to know which rows can jump
// to a card here.
watch(signals, list => emit('pending-ids', list.map(s => s.id)), { immediate: true })

watch(() => props.refreshKey, (next, prev) => {
  if (next !== prev) refresh()
})

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
  localRegrades = {}
  regradeErrors.value = {}
  // A load still out for the old instance drops its answer; refresh() runs
  // this instance's read after it rather than skipping it.
  refresh()
  startPolling()
})
</script>
