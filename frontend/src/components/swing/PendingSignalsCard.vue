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
     back, and one that finds it submitted or failed says so on the card
     until the operator dismisses it. One that still finds it approved more
     than 2 minutes after the 202 moves it to "Approved, not yet sent", with
     Re-send and Dismiss; a waiting card offers Dismiss after 2 minutes in
     any case (round 3 FU-1). -->
<template>
  <section class="glass-card rounded-2xl p-5">
    <div class="flex items-center justify-between mb-4 gap-2">
      <p class="text-xs font-bold uppercase tracking-widest text-slate-500">
        Pending AI Signals <span v-if="loaded" class="text-slate-700 ml-1">({{ signals.length }})</span>
      </p>
      <button
        @click="load"
        :disabled="loading"
        class="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-400 hover:bg-slate-800 px-2 py-1 rounded-lg border border-slate-700 transition-colors disabled:opacity-40"
      >
        <span class="material-symbols-outlined text-[13px]" :class="loading ? 'animate-spin' : ''">
          {{ loading ? 'progress_activity' : 'refresh' }}
        </span>
        Refresh
      </button>
    </div>

    <div v-if="notice" class="mb-3 rounded-lg border px-3 py-2 text-xs" :class="noticeClass">
      {{ notice.text }}
    </div>
    <div v-if="loadError" class="mb-3 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
      {{ loadError }}
    </div>

    <div v-if="sections.loadingText" class="text-xs text-slate-500">Loading…</div>
    <div
      v-else-if="sections.empty"
      class="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-6 text-center"
    >
      <p class="text-sm text-slate-400">Nothing waiting for review.</p>
      <p class="text-xs text-slate-600 mt-1">
        Scores from the review threshold up to the auto-approve threshold wait here.
      </p>
    </div>

    <div v-else-if="sections.pending" class="space-y-2">
      <div
        v-for="s in signals"
        :key="s.id"
        class="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3"
      >
        <div class="flex items-center gap-2 flex-wrap">
          <span class="text-base font-black text-slate-100 tracking-wide font-mono">{{ s.symbol }}</span>
          <span class="px-2 py-0.5 rounded-full text-[10px] font-bold border uppercase" :class="laneClass(s.lane)">
            {{ s.lane }}
          </span>
          <span
            class="px-2 py-0.5 rounded-full text-[10px] font-bold border tabular-nums"
            :class="scoreClass(s.score)"
            :title="s.recommendation || ''"
          >{{ s.score ?? '—' }}</span>
          <span class="text-[11px] text-slate-600">
            session {{ s.session || '—' }} · {{ fmtWhen(s.created_at) }}
          </span>
        </div>

        <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-3">
          <div v-for="row in proposalRows(s)" :key="row.label" class="min-w-0">
            <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">{{ row.label }}</div>
            <div
              class="text-xs font-bold text-slate-200 tabular-nums truncate"
              :class="row.mono ? 'font-mono' : ''"
              :title="row.value"
            >{{ row.value }}</div>
          </div>
        </div>

        <p v-if="s.reasoning" class="text-xs text-slate-300 mt-3 leading-relaxed whitespace-pre-line break-words">
          {{ expanded[s.id] ? String(s.reasoning).trim() : reasoningPreview(s.reasoning).text }}
          <button
            v-if="reasoningPreview(s.reasoning).truncated"
            @click="toggleExpanded(s.id)"
            class="ml-1 text-[11px] font-semibold text-sky-400 hover:underline"
          >{{ expanded[s.id] ? 'Show less' : 'Show more' }}</button>
        </p>
        <p v-if="joinKeyRisks(s.key_risks)" class="text-[11px] text-amber-300/80 mt-2 break-words">
          Risks: {{ joinKeyRisks(s.key_risks) }}
        </p>

        <!-- Confirm step: nothing is sent until this second click. -->
        <div
          v-if="confirming[s.id]"
          class="mt-3 rounded-lg border px-3 py-2"
          :class="confirming[s.id] === 'reject'
            ? 'border-slate-700 bg-slate-800/40'
            : 'border-emerald-500/25 bg-emerald-500/5'"
        >
          <p class="text-xs text-slate-200">{{ confirmPrompt(s, confirming[s.id]) }}</p>
          <input
            v-model="reasons[s.id]"
            maxlength="500"
            placeholder="Reason (optional)"
            :disabled="!!deciding[s.id]"
            class="mt-2 w-full rounded-lg bg-slate-900 border border-slate-700 px-2 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-slate-500"
          />
          <div class="flex gap-2 mt-2 flex-wrap">
            <button
              @click="submit(s)"
              :disabled="!!deciding[s.id]"
              class="px-3 py-1.5 rounded-lg text-xs font-semibold border disabled:opacity-50"
              :class="decisionClass(confirming[s.id])"
            >{{ deciding[s.id] ? 'Working…' : `Confirm ${DECISION_LABELS[confirming[s.id]]}` }}</button>
            <button
              @click="cancelConfirm(s.id)"
              :disabled="!!deciding[s.id]"
              class="px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-700 bg-slate-800/60 text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >Cancel</button>
          </div>
        </div>
        <div v-else class="flex gap-2 mt-3 flex-wrap">
          <button
            v-for="d in decisionsFor(s)"
            :key="d"
            @click="startConfirm(s.id, d)"
            :disabled="!!deciding[s.id]"
            class="px-3 py-1.5 rounded-lg text-xs font-semibold border disabled:opacity-50"
            :class="decisionClass(d)"
          >{{ DECISION_LABELS[d] }}</button>
        </div>
      </div>
    </div>

    <div v-if="sections.waiting" class="mt-4">
      <p class="text-[11px] font-bold uppercase tracking-widest text-amber-400/80 mb-2">
        Waiting for the broker ({{ Object.keys(uncertain).length }})
      </p>
      <div class="space-y-2">
        <div
          v-for="(entry, id) in uncertain"
          :key="`uncertain-${id}`"
          class="rounded-lg border px-4 py-3"
          :class="uncertainCardClass(entry)"
        >
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-base font-black text-slate-100 tracking-wide font-mono">{{ entry.signal.symbol }}</span>
            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold border uppercase" :class="laneClass(entry.signal.lane)">
              {{ entry.signal.lane }}
            </span>
            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold border" :class="uncertainBadgeClass(entry)">
              {{ uncertainBadge(entry) }}
            </span>
            <span class="text-[11px] text-slate-600">session {{ entry.signal.session || '—' }}</span>
          </div>
          <p v-if="!entry.resolved" class="text-xs text-amber-200/90 mt-2 leading-relaxed">{{ WAITING_COPY }}</p>
          <p v-else-if="entry.resolved === 'submitted'" class="text-xs text-slate-300 mt-2">
            The broker submitted it. Check open orders for the fill.
          </p>
          <p v-else class="text-xs text-slate-300 mt-2">
            It failed at the broker; the live log says why.
          </p>
          <div v-if="waitingCanDismiss(entry, nowMs)" class="flex gap-2 mt-3">
            <button
              @click="dismissUncertain(id)"
              class="px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-700 bg-slate-800/60 text-slate-300 hover:bg-slate-800"
            >Dismiss</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Round 3 minor 2: not gated on a good pending read. -->
    <div v-if="sections.stuck" class="mt-4">
      <p class="text-[11px] font-bold uppercase tracking-widest text-amber-400/80 mb-2">
        Approved, not yet sent ({{ stuck.length }})
      </p>
      <div class="space-y-2">
        <div
          v-for="s in stuck"
          :key="`stuck-${s.id}`"
          class="rounded-lg border border-amber-500/20 bg-amber-500/5 px-4 py-3"
        >
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-base font-black text-slate-100 tracking-wide font-mono">{{ s.symbol }}</span>
            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold border uppercase" :class="laneClass(s.lane)">
              {{ s.lane }}
            </span>
            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold border uppercase text-amber-300 bg-amber-500/10 border-amber-500/20">
              {{ s.status === 'approved_half' ? 'approved ½' : 'approved' }}
            </span>
            <span class="text-[11px] text-slate-600">session {{ s.session || '—' }}</span>
          </div>
          <p class="text-xs text-slate-300 mt-2">{{ stuckLabel(s, nowMs) }}</p>

          <p v-if="resendBlockedReason(s, today)" class="text-[11px] text-slate-500 mt-2">
            {{ resendBlockedReason(s, today) }}
          </p>
          <div v-if="resendConfirming[s.id] && !resendBlockedReason(s, today)" class="mt-3 rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2">
            <p class="text-xs text-slate-200">{{ resendPrompt(s) }}</p>
            <div class="flex gap-2 mt-2 flex-wrap">
              <button
                @click="resend(s)"
                :disabled="!!resending[s.id]"
                class="px-3 py-1.5 rounded-lg text-xs font-semibold border border-amber-500/30 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20 disabled:opacity-50"
              >{{ resending[s.id] ? 'Working…' : 'Confirm Re-send' }}</button>
              <button
                @click="cancelResend(s.id)"
                :disabled="!!resending[s.id]"
                class="px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-700 bg-slate-800/60 text-slate-300 hover:bg-slate-800 disabled:opacity-50"
              >Cancel</button>
            </div>
          </div>
          <div v-else class="flex gap-2 mt-3 flex-wrap">
            <button
              v-if="!resendBlockedReason(s, today)"
              @click="startResend(s.id)"
              :disabled="!!resending[s.id]"
              class="px-3 py-1.5 rounded-lg text-xs font-semibold border border-amber-500/30 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20 disabled:opacity-50"
            >Re-send</button>
            <button
              @click="dismissStuck(s.id)"
              :disabled="!!resending[s.id]"
              class="px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-700 bg-slate-800/60 text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >Dismiss</button>
          </div>
        </div>
      </div>
    </div>

    <p class="text-[10px] text-slate-600 mt-3 leading-relaxed">
      Approval rebuilds the order at the live price: shares, stop and target for swing, strike and expiry for the wheel.
    </p>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { getToken } from '../../utils/auth.js'
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
})

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
const dismissedStuck = new Set() // stuck ids the operator dismissed on this page (round 3 FU-1)
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

const noticeClass = computed(() => ({
  ok: 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300',
  warn: 'border-amber-500/20 bg-amber-500/10 text-amber-300',
  error: 'border-rose-500/20 bg-rose-500/10 text-rose-300',
}[notice.value?.tone] || ''))

function laneClass(lane) {
  return lane === 'wheel'
    ? 'text-violet-300 bg-violet-500/10 border-violet-500/20'
    : 'text-sky-400 bg-sky-500/10 border-sky-500/20'
}

function scoreClass(score) {
  return {
    high: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
    mid: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
    low: 'text-rose-400 bg-rose-500/10 border-rose-500/20',
    unknown: 'text-slate-400 bg-slate-500/10 border-slate-700',
  }[scoreTone(score)]
}

function decisionClass(decision) {
  return decision === 'reject'
    ? 'border-slate-700 bg-slate-800/60 text-slate-300 hover:bg-slate-800'
    : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20'
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
      dismissed: dismissedStuck,
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
  dismissedStuck.add(id)
  removeStuck(id)
}

function uncertainCardClass(entry) {
  if (entry.resolved === 'submitted') return 'border-emerald-500/20 bg-emerald-500/5'
  if (entry.resolved === 'failed') return 'border-rose-500/20 bg-rose-500/5'
  return 'border-amber-500/25 bg-amber-500/5'
}

function uncertainBadgeClass(entry) {
  if (entry.resolved === 'submitted') return 'text-emerald-300 bg-emerald-500/10 border-emerald-500/20'
  if (entry.resolved === 'failed') return 'text-rose-300 bg-rose-500/10 border-rose-500/20'
  return 'text-amber-300 bg-amber-500/10 border-amber-500/20'
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
      if (outcome.uncertain) {
        // Follow-up 2: the advice stays on a card until a poll settles it.
        uncertain.value = addUncertain(uncertain.value, { ...signal, ...(body?.signal || {}), id: signal.id },
          latch.current(), Date.now())
      } else {
        showNotice(outcome.tone, outcome.message)
      }
      return
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
  } catch (e) {
    showNotice('error', classifyDecisionFailure(0, e?.message).message)
  } finally {
    guard.release(signal.id)
    deciding.value = omit(deciding.value, signal.id)
  }
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
})

watch(() => props.instanceId, (next, prev) => {
  if (next === prev) return
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
  dismissedStuck.clear()
  load()
  startPolling()
})
</script>
