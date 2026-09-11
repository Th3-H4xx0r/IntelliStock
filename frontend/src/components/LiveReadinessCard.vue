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
  <div class="glass-card rounded-2xl p-5" :class="cardBorder">
    <div class="flex items-center justify-between mb-4 gap-2">
      <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Live Readiness</p>
      <span
        class="text-[10px] font-bold px-2 py-0.5 rounded-full border uppercase tracking-wider shrink-0"
        :class="badgeClass"
      >{{ badgeLabel }}</span>
    </div>

    <!-- Which of the two kinds of report this is, is the thing an operator
         needs to know here: one survives a deploy, the other must not. -->
    <div
      v-if="isWaived"
      class="rounded-lg border border-red-500/25 bg-red-500/5 px-3 py-2 mb-4 flex items-start gap-2"
    >
      <span class="material-symbols-outlined text-red-300 text-[14px] mt-0.5">gpp_maybe</span>
      <p class="text-[11px] text-red-200/80 leading-relaxed">
        Standing operator waiver — carried forward automatically on every deploy.
      </p>
    </div>
    <div v-else class="rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2 mb-4 flex items-start gap-2">
      <span class="material-symbols-outlined text-amber-400 text-[14px] mt-0.5">deployed_code_alert</span>
      <p class="text-[11px] text-amber-200/80 leading-relaxed">
        An earned report is bound to the image it was gathered on, and a deploy
        invalidates it.
      </p>
    </div>

    <div class="space-y-3 text-sm">
      <!-- No readiness fields at all: an older API build. Saying "no report" -->
      <!-- here would announce a missing gate that may well be present.      -->
      <div v-if="status === 'unsupported'" class="text-xs text-slate-500 leading-relaxed">
        This API build does not report live-readiness state. Update the backend
        to see whether a report exists for this instance.
      </div>

      <template v-else-if="status === 'missing'">
        <p class="text-xs text-slate-400 leading-relaxed">
          No readiness report on this instance. A funded broker will refuse to
          start until one is earned — or waived below.
        </p>
      </template>

      <template v-else>
        <div class="flex justify-between gap-2">
          <span class="text-slate-500">State</span>
          <span class="text-slate-200 font-mono text-xs">{{ report.state || '—' }}</span>
        </div>
        <div class="flex justify-between gap-2">
          <span class="text-slate-500">Artifact</span>
          <span
            class="text-slate-200 font-mono text-xs truncate max-w-[55%]"
            :title="report.artifact_hash || ''"
          >{{ shortHash(report.artifact_hash) || '—' }}</span>
        </div>
        <div class="flex justify-between gap-2">
          <span class="text-slate-500">Checks passed</span>
          <span class="font-mono text-xs" :class="counts.passed === counts.total && counts.total > 0 ? 'text-emerald-400' : 'text-amber-400'">
            {{ counts.passed }}/{{ counts.total }}
          </span>
        </div>
        <div v-if="report.fingerprint" class="flex justify-between gap-2">
          <span class="text-slate-500">Fingerprint</span>
          <span class="text-slate-400 font-mono text-xs" :title="report.fingerprint">{{ shortHash(report.fingerprint) }}</span>
        </div>
      </template>

      <div v-if="waivedAt || waivedBy" class="pt-3 mt-1 border-t border-red-500/20 space-y-2">
        <div class="flex justify-between gap-2">
          <span class="text-red-300/80 text-xs font-semibold">Gate waived by</span>
          <span class="text-red-200 font-mono text-xs">{{ waivedBy || 'unknown' }}</span>
        </div>
        <div class="flex justify-between gap-2">
          <span class="text-red-300/80 text-xs font-semibold">Waived at</span>
          <span class="text-red-200 font-mono text-xs" :title="waivedAt || ''">{{ fmtWhen(waivedAt) }}</span>
        </div>
        <!-- The carry-forward is invisible otherwise: the operator never -->
        <!-- pressed anything, so the row has to say when it last happened. -->
        <div v-if="reboundAt" class="flex justify-between gap-2">
          <span class="text-red-300/80 text-xs font-semibold">Last carried forward</span>
          <span
            class="text-red-200 font-mono text-xs"
            :title="reboundFrom ? `re-bound from image ${reboundFrom}` : ''"
          >{{ fmtWhen(reboundAt) }}</span>
        </div>
        <p v-if="waivedIsStale" class="text-[11px] text-red-300 leading-relaxed">
          The waiver names an image other than the one this report is bound to.
          Re-waive before starting live.
        </p>
      </div>
    </div>

    <div class="mt-4 pt-4 border-t border-border-subtle space-y-2">
      <button
        @click="openWaiver"
        title="Bypass the live-readiness gate (audited)"
        class="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-bold border transition-colors
               border-red-500/30 text-red-300 hover:bg-red-500/10"
      >
        <span class="material-symbols-outlined text-[14px]">gpp_maybe</span>
        {{ isWaived ? 'Re-waive with a new reason…' : 'Waive live-readiness gate…' }}
      </button>
      <button
        v-if="isWaived"
        @click="showRevoke = true"
        title="End the standing waiver — a funded start is gated again"
        class="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-bold border transition-colors
               border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-white/5"
      >
        <span class="material-symbols-outlined text-[14px]">lock_reset</span>
        Revoke waiver
      </button>
    </div>
  </div>

  <!-- ── Revoke confirmation ─────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showRevoke"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
        @click.self="closeRevoke"
      >
        <div class="relative w-full max-w-md bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">
          <div class="px-6 py-5 border-b border-border-subtle">
            <h2 class="text-base font-bold">Revoke the standing waiver</h2>
            <p class="text-[11px] text-slate-500 truncate mt-0.5">
              Instance <span class="font-mono text-slate-300">{{ instanceId }}</span>
            </p>
          </div>
          <div class="px-6 py-5 space-y-3">
            <p class="text-xs text-slate-300 leading-relaxed">
              This removes the readiness report and the waiver stamps. A funded
              broker on this instance will refuse to start again until the gate
              is earned — or waived afresh.
            </p>
            <p v-if="revokeMsg" class="text-xs leading-relaxed"
               :class="revokeOk ? 'text-emerald-300' : 'text-red-300'">
              {{ revokeMsg }}
            </p>
          </div>
          <div class="px-6 py-4 border-t border-border-subtle flex gap-3">
            <button @click="closeRevoke" :disabled="revoking"
                    class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-50">
              {{ revokeOk ? 'Close' : 'Cancel' }}
            </button>
            <button v-if="!revokeOk" @click="submitRevoke" :disabled="revoking"
                    class="flex-1 py-2.5 rounded-lg bg-red-500 text-white text-sm font-bold hover:brightness-110 transition-all disabled:opacity-40 disabled:cursor-not-allowed">
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
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
        @click.self="closeWaiver"
      >
        <div class="relative w-full max-w-lg bg-[#0f1318] border border-red-500/40 rounded-2xl shadow-2xl overflow-hidden">
          <div class="flex items-center justify-between px-6 py-5 border-b border-red-500/25">
            <div class="flex items-center gap-3 min-w-0">
              <div class="size-9 rounded-xl bg-red-500/15 border border-red-500/30 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-red-300 text-lg">warning</span>
              </div>
              <div class="min-w-0">
                <h2 class="text-base font-bold">Waive the live-readiness gate</h2>
                <p class="text-[11px] text-slate-500 truncate">
                  Real money · instance <span class="font-mono text-slate-300">{{ instanceId }}</span>
                </p>
              </div>
            </div>
            <button @click="closeWaiver" :disabled="saving"
                    class="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>

          <div class="px-6 py-5 space-y-4 max-h-[70vh] overflow-y-auto">
            <div class="rounded-xl border border-red-500/30 bg-red-500/5 px-4 py-3 space-y-1.5">
              <p class="text-xs text-red-200 leading-relaxed">
                This writes a readiness report that a funded broker will accept,
                with every check marked passed on your say-so rather than on
                evidence. It is recorded against your username, pages the
                operator channel, and is bound to the image deployed right now.
              </p>
              <p class="text-[11px] text-red-300/80 leading-relaxed">
                It stands until you revoke it: the launcher re-binds it to each
                newly deployed image, so a deploy will not quietly put the gate
                back.
              </p>
            </div>

            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">
                Type <span class="font-mono text-red-300">{{ phrase }}</span> to confirm
              </label>
              <input
                v-model="confirmText"
                :disabled="saving"
                type="text"
                autocomplete="off"
                spellcheck="false"
                :placeholder="phrase"
                class="w-full bg-surface border rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-700 font-mono focus:outline-none transition-colors"
                :class="confirmOk ? 'border-emerald-500/40 focus:border-emerald-400' : 'border-red-500/30 focus:border-red-400'"
              />
              <p v-if="confirmText && !confirmOk" class="text-[11px] text-slate-500 mt-1">
                Must match exactly — the server compares it character for character.
              </p>
            </div>

            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">
                Reason (at least {{ WAIVER_MIN_REASON_CHARS }} characters)
              </label>
              <textarea
                v-model="reasonText"
                :disabled="saving"
                rows="3"
                placeholder="Why you are accepting the live risk on this instance."
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-700 focus:outline-none focus:border-red-400 transition-colors resize-y"
              ></textarea>
              <p class="text-[11px] mt-1" :class="reasonOk ? 'text-slate-600' : 'text-amber-400'">
                {{ reasonLen }}/{{ WAIVER_MIN_REASON_CHARS }} characters — this is the whole audit record.
              </p>
            </div>

            <div v-if="msg" class="rounded-xl px-4 py-3 text-xs leading-relaxed"
                 :class="ok
                   ? 'border border-emerald-500/30 bg-emerald-500/5 text-emerald-300'
                   : 'border border-red-500/30 bg-red-500/5 text-red-300'">
              <p class="font-semibold">{{ msg }}</p>
              <div v-if="ok && result" class="mt-2 space-y-1 font-mono text-[11px] text-emerald-200/80">
                <div :title="result.fingerprint">fingerprint {{ shortHash(result.fingerprint) }}</div>
                <div :title="result.artifact_hash">artifact&nbsp;&nbsp;&nbsp; {{ shortHash(result.artifact_hash) }}</div>
              </div>
            </div>
          </div>

          <div class="px-6 py-4 border-t border-border-subtle flex gap-3">
            <button @click="closeWaiver" :disabled="saving"
                    class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-50">
              {{ ok ? 'Close' : 'Cancel' }}
            </button>
            <button v-if="!ok"
                    @click="submitWaiver"
                    :disabled="!canSubmit || saving"
                    class="flex-1 py-2.5 rounded-lg bg-red-500 text-white text-sm font-bold hover:brightness-110 transition-all disabled:opacity-40 disabled:cursor-not-allowed">
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
const badgeClass = computed(() => {
  if (status.value === 'present' && !isWaived.value) {
    return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/25'
  }
  if (status.value === 'present') return 'bg-red-500/10 text-red-300 border-red-500/30'
  if (status.value === 'missing') return 'bg-amber-500/10 text-amber-400 border-amber-500/25'
  return 'bg-slate-500/10 text-slate-500 border-slate-700'
})
// A waived instance wears the warning border; everything else keeps
// .glass-card's own, so the card sits flush with its neighbours.
const cardBorder = computed(() => (isWaived.value ? 'border border-red-500/25' : ''))

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
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to       { opacity: 0; }
</style>
