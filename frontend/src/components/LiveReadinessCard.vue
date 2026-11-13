<!-- frontend/src/components/LiveReadinessCard.vue
     The live-readiness gate, on the instance page.

     `instance.py:_assert_live_broker_start_allowed` refuses to spawn a funded
     broker without a fingerprinted, artifact-bound readiness report on the
     row, and the report binds to the Docker image it was written against.

     A waiver is a STANDING decision now (operator, 2026-09-11): the launcher
     re-binds it to each newly deployed image
     (`live_readiness.rebind_operator_waiver`), so a deploy no longer silently
     revokes it and nobody has to race a restart to press this button again.
     An earned report is still invalidated by a deploy — it is evidence about
     one artifact — and that is why this card distinguishes the two.

     The card reads that state; the modal waives it. The waiver is a safety
     bypass, so it is styled as one and gated exactly the way the route is:
     the exact phrase typed by hand and a reason long enough to be an audit
     record. Any signed-in user may waive (operator decision 2026-09-11) --
     the typing is the gate, not the role. There is deliberately no
     "remember this phrase" and
     no way to waive without typing it — a one-click bypass of a real-money
     gate is not a convenience, it is the failure mode.

     Because it is standing, it has to be endable: "Revoke waiver" (DELETE on
     the same route, behind its own confirmation) is the only thing that puts
     the gate back. -->
<template>
  <section class="flex flex-col min-w-0" aria-labelledby="live-readiness-title">
    <div class="flex items-center justify-between gap-3 min-h-8 mb-3">
      <h2 id="live-readiness-title" class="text-base font-medium text-slate-100">Live readiness</h2>
      <span class="inline-flex items-center gap-2 text-sm text-slate-300 shrink-0">
        <span class="ui-dot" :class="badgeClass"></span>
        {{ badgeLabel }}
      </span>
    </div>

    <div class="ui-card flex-1 flex flex-col" :class="cardBorder">
      <!-- Which of the two kinds of report this is, is the thing an operator
           needs to know here: one survives a deploy, the other must not. -->
      <p
        v-if="isWaived"
        class="px-5 py-3 flex items-start gap-2 text-xs text-slate-300 leading-relaxed border-b border-white/[0.06]"
      >
        <span class="ui-dot ui-dot-error mt-1.5"></span>
        <span>Standing operator waiver — carried forward automatically on every deploy</span>
      </p>
      <p v-else class="px-5 py-3 text-xs text-slate-500 leading-relaxed border-b border-white/[0.06]">
        An earned report is bound to the image it was gathered on, and a deploy
        invalidates it.
      </p>

      <div class="ui-rows">
        <!-- No readiness fields at all: an older API build. Saying "no report" -->
        <!-- here would announce a missing gate that may well be present.      -->
        <p v-if="status === 'unsupported'" class="px-5 py-3 text-sm text-slate-400 leading-relaxed">
          This API build does not report live-readiness state. Update the backend
          to see whether a report exists for this instance.
        </p>

        <template v-else-if="status === 'missing'">
          <p class="px-5 py-3 text-sm text-slate-400 leading-relaxed">
            No readiness report on this instance. A funded broker will refuse to
            start until one is earned — or waived below.
          </p>
        </template>

        <template v-else>
          <div class="kv">
            <span>State</span>
            <span class="text-slate-100">{{ report.state || '—' }}</span>
          </div>
          <div class="kv">
            <span>Artifact</span>
            <span
              class="text-slate-100 tabular-nums truncate max-w-[55%]"
              :title="report.artifact_hash || ''"
            >{{ shortHash(report.artifact_hash) || '—' }}</span>
          </div>
          <div class="kv">
            <span>Checks passed</span>
            <span class="tabular-nums" :class="counts.passed === counts.total && counts.total > 0 ? 'text-gain' : 'text-amber-400'">
              {{ counts.passed }}/{{ counts.total }}
            </span>
          </div>
          <div v-if="report.fingerprint" class="kv">
            <span>Fingerprint</span>
            <span class="text-slate-300 tabular-nums" :title="report.fingerprint">{{ shortHash(report.fingerprint) }}</span>
          </div>
        </template>

        <template v-if="waivedAt || waivedBy">
          <div class="kv">
            <span>Gate waived by</span>
            <span class="text-slate-100">{{ waivedBy || 'unknown' }}</span>
          </div>
          <div class="kv">
            <span>Waived at</span>
            <span class="text-slate-100 tabular-nums" :title="waivedAt || ''">{{ fmtWhen(waivedAt) }}</span>
          </div>
          <!-- The carry-forward is invisible otherwise: the operator never -->
          <!-- pressed anything, so the row has to say when it last happened. -->
          <div v-if="reboundAt" class="kv">
            <span>Last carried forward</span>
            <span
              class="text-slate-100 tabular-nums"
              :title="reboundFrom ? `re-bound from image ${reboundFrom}` : ''"
            >{{ fmtWhen(reboundAt) }}</span>
          </div>
          <p v-if="waivedIsStale" class="px-5 py-3 text-xs text-loss leading-relaxed">
            The waiver names an image other than the one this report is bound to.
            Re-waive before starting live.
          </p>
        </template>
      </div>

      <div class="mt-auto px-5 py-4 border-t border-white/[0.06] flex flex-wrap gap-2">
        <button
          @click="openWaiver"
          title="Bypass the live-readiness gate (audited)"
          class="ui-btn text-loss flex-1"
        >
          {{ isWaived ? 'Re-waive with a new reason…' : 'Waive live-readiness gate…' }}
        </button>
        <button
          v-if="isWaived"
          @click="showRevoke = true"
          title="End the standing waiver — a funded start is gated again"
          class="ui-btn flex-1"
        >
          Revoke waiver
        </button>
      </div>
    </div>
  </section>

  <!-- ── Revoke confirmation ─────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showRevoke"
        class="ui-overlay"
        @click.self="closeRevoke"
      >
        <div class="ui-modal max-w-md">
          <div class="ui-modal-head">
            <div class="min-w-0">
              <h2 class="text-base font-medium text-slate-100">Revoke the standing waiver</h2>
              <p class="text-sm text-slate-500 truncate mt-1">
                Instance <span class="text-slate-300">{{ instanceId }}</span>
              </p>
            </div>
          </div>
          <div class="ui-modal-body space-y-3">
            <p class="text-sm text-slate-300 leading-relaxed">
              This removes the readiness report and the waiver stamps. A funded
              broker on this instance will refuse to start again until the gate
              is earned — or waived afresh.
            </p>
            <p v-if="revokeMsg" class="text-sm leading-relaxed"
               :class="revokeOk ? 'text-gain' : 'text-loss'">
              {{ revokeMsg }}
            </p>
          </div>
          <div class="ui-modal-foot">
            <button @click="closeRevoke" :disabled="revoking"
                    class="ui-btn-ghost h-9 px-4">
              {{ revokeOk ? 'Close' : 'Cancel' }}
            </button>
            <button v-if="!revokeOk" @click="submitRevoke" :disabled="revoking"
                    class="ui-btn h-9 px-4 text-loss">
              {{ revoking ? 'Revoking…' : 'Revoke waiver' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Waiver modal (safety bypass — typed confirmation, no shortcuts) ──── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showWaiver"
        class="ui-overlay"
        @click.self="closeWaiver"
      >
        <div class="ui-modal max-w-lg">
          <div class="ui-modal-head border-b border-white/[0.06]">
            <div class="min-w-0">
              <h2 class="text-base font-medium text-slate-100">Waive the live-readiness gate</h2>
              <p class="text-sm text-slate-500 truncate mt-1">
                Real money · instance <span class="text-slate-300">{{ instanceId }}</span>
              </p>
            </div>
            <button @click="closeWaiver" :disabled="saving"
                    class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40"
                    aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="px-6 py-5 space-y-5 max-h-[70vh] overflow-y-auto">
            <div class="rounded-xl border border-loss/20 bg-loss/5 px-4 py-3 space-y-2">
              <p class="text-sm text-slate-200 leading-relaxed">
                This writes a readiness report that a funded broker will accept,
                with every check marked passed on your say-so rather than on
                evidence. It is recorded against your username, pages the
                operator channel, and is bound to the image deployed right now.
              </p>
              <p class="text-xs text-slate-400 leading-relaxed">
                It stands until you revoke it: the launcher re-binds it to each
                newly deployed image, so a deploy will not quietly put the gate
                back.
              </p>
            </div>

            <div>
              <label class="ui-field-label">
                Type <span class="font-mono text-slate-100">{{ phrase }}</span> to confirm
              </label>
              <input
                v-model="confirmText"
                :disabled="saving"
                type="text"
                autocomplete="off"
                spellcheck="false"
                :placeholder="phrase"
                class="ui-input font-mono"
                :class="confirmOk ? 'border-gain/40 focus:border-gain/60' : ''"
              />
              <p v-if="confirmText && !confirmOk" class="ui-help">
                Must match exactly — the server compares it character for character.
              </p>
            </div>

            <div>
              <label class="ui-field-label">
                Reason <span class="text-slate-500">(at least {{ WAIVER_MIN_REASON_CHARS }} characters)</span>
              </label>
              <textarea
                v-model="reasonText"
                :disabled="saving"
                rows="3"
                placeholder="Why you are accepting the live risk on this instance"
                class="ui-input resize-y"
              ></textarea>
              <p class="ui-help tabular-nums" :class="reasonOk ? '' : 'text-amber-400'">
                {{ reasonLen }}/{{ WAIVER_MIN_REASON_CHARS }} characters — this is the whole audit record.
              </p>
            </div>

            <div v-if="msg" class="rounded-lg border px-4 py-3 text-sm leading-relaxed"
                 :class="ok
                   ? 'border-gain/20 bg-gain/5 text-gain'
                   : 'border-loss/20 bg-loss/5 text-loss'">
              <p class="font-medium">{{ msg }}</p>
              <div v-if="ok && result" class="mt-2 space-y-1 text-xs text-slate-300 tabular-nums">
                <div :title="result.fingerprint"><span class="text-slate-500">Fingerprint</span> {{ shortHash(result.fingerprint) }}</div>
                <div :title="result.artifact_hash"><span class="text-slate-500">Artifact</span> {{ shortHash(result.artifact_hash) }}</div>
              </div>
            </div>
          </div>

          <div class="ui-modal-foot">
            <button @click="closeWaiver" :disabled="saving"
                    class="ui-btn-ghost h-9 px-4">
              {{ ok ? 'Close' : 'Cancel' }}
            </button>
            <button v-if="!ok"
                    @click="submitWaiver"
                    :disabled="!canSubmit || saving"
                    class="ui-btn h-9 px-4 text-loss">
              {{ saving ? 'Waiving…' : 'Waive the gate' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, ref } from 'vue'
import { getToken } from '../utils/auth.js'
import {
  WAIVER_MIN_REASON_CHARS,
  canSubmitWaiver,
  isWaivedReport,
  readinessCheckCounts,
  readinessStatus,
  shortArtifactHash,
  waiverConfirmMatches,
  waiverConfirmPhrase,
  waiverReasonLength,
  waiverReasonOk,
} from '../utils/liveReadiness.js'

const props = defineProps({
  instance: { type: Object, default: () => ({}) },
  apiBase: { type: String, required: true },
})
const emit = defineEmits(['waived', 'revoked'])

const instanceId = computed(() => String(props.instance?.id ?? ''))
const status = computed(() => readinessStatus(props.instance))
const report = computed(() => props.instance?.live_readiness_report || {})
const counts = computed(() => readinessCheckCounts(report.value))
const waivedAt = computed(() => props.instance?.live_readiness_waived_at || '')
const waivedBy = computed(() => props.instance?.live_readiness_waived_by || '')
const reboundAt = computed(() => props.instance?.live_readiness_rebound_at || '')
const reboundFrom = computed(() => props.instance?.live_readiness_rebound_from || '')
const isWaived = computed(
  () => status.value === 'present' && isWaivedReport(report.value))

// A waiver stamp with no report left on the row means the report was replaced
// by something the waiver did not write — worth saying out loud rather than
// showing a stale "waived by" line under an unrelated report.
const waivedIsStale = computed(
  () => !!waivedAt.value && status.value === 'present' && !isWaivedReport(report.value))

const badgeLabel = computed(() => {
  if (status.value === 'unsupported') return 'Unknown'
  if (status.value === 'missing') return 'No report'
  return isWaived.value ? 'Waived' : 'Reported'
})
// The status dot beside the title. An earned report is a pass (gain, no live
// ring); a waiver is a bypass (red); no report is a warning (amber).
const badgeClass = computed(() => {
  if (status.value === 'present' && !isWaived.value) return 'bg-gain'
  if (status.value === 'present') return 'ui-dot-error'
  if (status.value === 'missing') return 'ui-dot-warn'
  return 'ui-dot-off'
})
// A waived instance wears a faint loss hairline; everything else keeps
// .ui-card's own, so the card sits flush with its neighbours.
const cardBorder = computed(() => (isWaived.value ? 'border-loss/25' : ''))

function shortHash(v) { return shortArtifactHash(v) }

function fmtWhen(v) {
  if (!v) return '—'
  const d = new Date(v)
  return isNaN(d.getTime())
    ? String(v)
    : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

// ── Waiver modal ────────────────────────────────────────────────────────────
const showWaiver  = ref(false)
const confirmText = ref('')
const reasonText  = ref('')
const saving      = ref(false)
const msg         = ref('')
const ok          = ref(false)
const result      = ref(null)

const phrase     = computed(() => waiverConfirmPhrase(instanceId.value))
const confirmOk  = computed(() => waiverConfirmMatches(instanceId.value, confirmText.value))
const reasonLen  = computed(() => waiverReasonLength(reasonText.value))
const reasonOk   = computed(() => waiverReasonOk(reasonText.value))
const canSubmit  = computed(
  () => canSubmitWaiver(instanceId.value, confirmText.value, reasonText.value))

function openWaiver() {
  // Never carry a phrase across openings: the typing IS the confirmation.
  confirmText.value = ''
  reasonText.value  = ''
  msg.value = ''
  ok.value = false
  result.value = null
  saving.value = false
  showWaiver.value = true
}

function closeWaiver() {
  if (saving.value) return
  showWaiver.value = false
  confirmText.value = ''
  reasonText.value = ''
}

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

async function submitWaiver() {
  if (!canSubmit.value || saving.value) return
  saving.value = true
  msg.value = ''
  ok.value = false
  try {
    const res = await fetch(
      `${props.apiBase}/instances/${encodeURIComponent(instanceId.value)}/readiness-waiver`,
      {
        method: 'POST',
        headers: authHeaders(),
        // Sent verbatim. Trimming the phrase client-side would hide a typo the
        // server is right to reject.
        body: JSON.stringify({ confirm: confirmText.value, reason: reasonText.value }),
      })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data?.detail || data?.error || `HTTP ${res.status}`)
    result.value = data
    ok.value = true
    msg.value = `Gate waived for ${instanceId.value}.`
    emit('waived', data)
  } catch (e) {
    ok.value = false
    msg.value = e.message || 'Waiver failed'
  } finally {
    saving.value = false
  }
}

// ── Revoke ──────────────────────────────────────────────────────────────────
// The waiver is standing, so ending it is a deliberate act of its own rather
// than something a deploy does by accident. One confirmation, no typed phrase:
// revoking re-arms a safety gate, it does not bypass one.
const showRevoke = ref(false)
const revoking   = ref(false)
const revokeMsg  = ref('')
const revokeOk   = ref(false)

function closeRevoke() {
  if (revoking.value) return
  showRevoke.value = false
  revokeMsg.value = ''
  revokeOk.value = false
}

async function submitRevoke() {
  if (revoking.value) return
  revoking.value = true
  revokeMsg.value = ''
  try {
    const res = await fetch(
      `${props.apiBase}/instances/${encodeURIComponent(instanceId.value)}/readiness-waiver`,
      { method: 'DELETE', headers: authHeaders() })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data?.detail || data?.error || `HTTP ${res.status}`)
    revokeOk.value = true
    revokeMsg.value = `Waiver revoked. A funded start on ${instanceId.value} is gated again.`
    emit('revoked', data)
  } catch (e) {
    revokeOk.value = false
    revokeMsg.value = e.message || 'Revocation failed'
  } finally {
    revoking.value = false
  }
}
</script>

<style scoped>
/* Matches the instance page's own modal transition (scoped there, so it does
   not reach a teleported child). */
.fade-enter-active, .fade-leave-active { transition: opacity 150ms cubic-bezier(0.22, 1, 0.36, 1); }
.fade-enter-from, .fade-leave-to       { opacity: 0; }

/* Label / value row — same rhythm as the instance page's own cards. */
.kv {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 44px;
  padding: 10px 20px;
  font-size: 14px;
  line-height: 20px;
}
.kv > :first-child { color: #a1a1aa; min-width: 0; }
.kv > :last-child { text-align: right; }
</style>
