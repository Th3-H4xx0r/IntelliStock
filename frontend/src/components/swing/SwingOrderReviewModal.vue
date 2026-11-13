<!-- frontend/src/components/swing/SwingOrderReviewModal.vue
     The order review shown before an approval is sent, mirroring the iOS
     sheet (ios/IntelliStock/Features/Swing/Views/SwingOrderReviewSheet.swift):
     the symbol and what the order does, the underlying's chart (a put's
     strike drawn on it), the figures that matter, the collateral warning, the
     broker's caveat, then the confirm. The web adds the rest of the signal
     (the order detail, score, session, the AI's reasoning and key risks) so
     everything about the trade is on one screen.

     The modal sends nothing itself: Confirm emits `confirm`, and the parent
     calls its existing submit handler with the same arguments, so the
     request is byte-for-byte what the inline confirm step sent. While that
     request is in flight both buttons are disabled; a refusal shows here and
     the confirm can be pressed again; a recorded approval shows the sent
     mark and closes, as on iOS. -->
<template>
  <Teleport to="body">
    <div
      class="ui-overlay p-0 items-stretch sm:items-center sm:p-4"
      @click.self="onDismiss"
    >
      <div
        ref="panelEl"
        class="ui-modal max-w-3xl h-full sm:h-auto sm:max-h-[90vh] rounded-none sm:rounded-2xl border-0 sm:border flex flex-col outline-none"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        tabindex="-1"
      >
        <!-- Outcome: sent, recorded-but-uncertain, or no longer pending -->
        <template v-if="result">
          <div class="flex-1 flex flex-col items-center justify-center gap-3 px-6 py-16 text-center" role="status">
            <span
              class="material-symbols-outlined text-[36px]"
              :class="resultTone === 'gain' ? 'text-gain' : 'text-slate-400'"
              aria-hidden="true"
            >{{ resultTone === 'gain' ? 'check_circle' : 'info' }}</span>
            <h2 :id="titleId" class="text-lg font-medium text-slate-100">{{ resultTitle }}</h2>
            <p class="text-sm text-slate-400 max-w-md leading-relaxed">{{ result.message }}</p>
          </div>
          <div class="ui-modal-foot shrink-0">
            <button type="button" class="ui-btn-primary" @click="emit('close')">Done</button>
          </div>
        </template>

        <template v-else>
          <div class="ui-modal-head border-b border-white/[0.06] shrink-0">
            <div class="min-w-0">
              <div class="flex items-baseline gap-x-3 gap-y-1 flex-wrap">
                <h2 :id="titleId" class="text-2xl font-medium text-slate-100 leading-tight">{{ signal.symbol }}</h2>
                <span class="text-sm text-slate-300">{{ sideLabel }}</span>
                <span v-if="decision === 'approve_half'" class="text-sm text-slate-400">½ size</span>
              </div>
              <p class="text-sm text-slate-500 mt-1">Review the order before it goes to the broker</p>
            </div>
            <button
              type="button"
              class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40"
              :disabled="busy"
              aria-label="Close"
              @click="emit('cancel')"
            >
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="flex-1 min-h-0 overflow-y-auto px-6 py-5 space-y-6">
            <p v-if="plan" class="text-sm text-slate-300 leading-relaxed">{{ plan.summary }} {{ plan.assignment }}</p>

            <SwingOrderChart
              :symbol="signal.symbol"
              :strike="plan ? plan.strike : null"
              :api-base="apiBase"
            />

            <!-- The figures that matter -->
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-4 pt-1">
              <div v-for="f in figures" :key="f.label" class="min-w-0">
                <p class="text-xs text-slate-500">{{ f.label }}</p>
                <p class="text-lg font-medium text-slate-100 tabular-nums mt-1 truncate">{{ f.value }}</p>
                <p v-if="f.foot" class="text-xs text-slate-500 mt-0.5 tabular-nums">{{ f.foot }}</p>
              </div>
            </div>

            <p v-if="warning" class="flex items-start gap-2 text-sm text-slate-300 leading-relaxed">
              <span class="ui-dot ui-dot-warn mt-2"></span>
              <span>{{ warning }}</span>
            </p>

            <!-- The order and the signal behind it -->
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-6">
              <section>
                <h3 class="text-xs text-slate-500 mb-2">Order</h3>
                <dl class="rounded-lg border border-white/[0.07] divide-y divide-white/[0.05]">
                  <div v-for="d in details" :key="d.label" class="row">
                    <dt>{{ d.label }}</dt>
                    <dd class="tabular-nums" :class="detailTone(d.tone)">{{ d.value }}</dd>
                  </div>
                </dl>
              </section>
              <section>
                <h3 class="text-xs text-slate-500 mb-2">Signal</h3>
                <dl class="rounded-lg border border-white/[0.07] divide-y divide-white/[0.05]">
                  <div class="row">
                    <dt>Lane</dt>
                    <dd class="text-slate-100">{{ laneLabel }}</dd>
                  </div>
                  <div class="row">
                    <dt>Score</dt>
                    <dd class="text-slate-100 tabular-nums">{{ signal.score ?? '—' }}</dd>
                  </div>
                  <div v-if="signal.recommendation" class="row">
                    <dt>Recommendation</dt>
                    <dd class="text-slate-100">{{ signal.recommendation }}</dd>
                  </div>
                  <div class="row">
                    <dt>Session</dt>
                    <dd class="text-slate-100 tabular-nums">{{ signal.session || '—' }}</dd>
                  </div>
                  <div class="row">
                    <dt>Created</dt>
                    <dd class="text-slate-100 tabular-nums">{{ fmtWhen(signal.created_at) }}</dd>
                  </div>
                </dl>
              </section>
            </div>

            <section v-if="reasoning">
              <h3 class="text-xs text-slate-500 mb-2">Reasoning</h3>
              <p class="text-sm text-slate-300 leading-relaxed whitespace-pre-line break-words">{{ reasoning }}</p>
            </section>

            <section v-if="risks.length">
              <h3 class="text-xs text-slate-500 mb-2">Key risks</h3>
              <ul class="space-y-1.5">
                <li v-for="r in risks" :key="r" class="flex items-start gap-2 text-sm text-slate-300">
                  <span class="ui-dot ui-dot-off mt-2"></span>
                  <span class="break-words">{{ r }}</span>
                </li>
              </ul>
            </section>

            <div>
              <label :for="reasonId" class="ui-field-label">Reason <span class="text-slate-500">(optional)</span></label>
              <input
                :id="reasonId"
                :value="reason"
                maxlength="500"
                placeholder="Recorded with the decision"
                :disabled="busy"
                class="ui-input"
                @input="emit('update:reason', $event.target.value)"
              />
            </div>

            <p class="text-xs text-slate-500 leading-relaxed">{{ caveat }}</p>
          </div>

          <div v-if="error" class="px-6 py-3 shrink-0">
            <div class="ui-error py-3" role="alert">{{ error }}</div>
          </div>

          <div class="ui-modal-foot shrink-0">
            <button type="button" class="ui-btn-ghost h-9 px-4" :disabled="busy" @click="emit('cancel')">Cancel</button>
            <button type="button" class="ui-btn-primary" :disabled="busy" @click="emit('confirm')">
              <span v-if="busy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ busy ? 'Sending…' : confirmLabel }}
            </button>
          </div>
        </template>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import SwingOrderChart from './SwingOrderChart.vue'
import { confirmPrompt } from '../../utils/swing.js'
import {
  collateralWarning, isWheel, putPlan, reviewConfirmLabel, reviewDetails, reviewFigures, reviewSideLabel,
} from '../../utils/swingReview.js'

const props = defineProps({
  signal: { type: Object, required: true },
  /** "approve" | "approve_half" */
  decision: { type: String, required: true },
  apiBase: { type: String, required: true },
  /** The wheel account's cash, for the collateral warning; null if unknown. */
  cash: { type: Number, default: null },
  reason: { type: String, default: '' },
  /** The decision request is in flight. */
  busy: { type: Boolean, default: false },
  /** { kind: 'sent'|'uncertain'|'stale', message } once the request settled. */
  result: { type: Object, default: null },
  /** The last refusal, shown above the footer; the confirm stays available. */
  error: { type: String, default: '' },
})
const emit = defineEmits(['cancel', 'confirm', 'close', 'update:reason'])

const uid = Math.random().toString(36).slice(2, 8)
const titleId = `swing-review-title-${uid}`
const reasonId = `swing-review-reason-${uid}`
const panelEl = ref(null)

const plan = computed(() => putPlan(props.signal))
const figures = computed(() => reviewFigures(props.signal))
const details = computed(() => reviewDetails(props.signal))
const sideLabel = computed(() => reviewSideLabel(props.signal))
const laneLabel = computed(() => (isWheel(props.signal) ? 'Wheel' : 'Swing'))
const confirmLabel = computed(() => reviewConfirmLabel(props.signal, props.decision))
const caveat = computed(() => confirmPrompt(props.signal, props.decision))
const warning = computed(() => collateralWarning(props.signal, props.cash))
const reasoning = computed(() => String(props.signal?.reasoning || '').trim())
const risks = computed(() => (Array.isArray(props.signal?.key_risks) ? props.signal.key_risks : [])
  .map(r => String(r ?? '').trim()).filter(Boolean))

const resultTone = computed(() => (props.result?.kind === 'sent' || props.result?.kind === 'uncertain' ? 'gain' : 'neutral'))
const resultTitle = computed(() => ({
  sent: 'Sent to the broker',
  uncertain: 'Approved',
  stale: 'No longer pending',
}[props.result?.kind] || 'Done'))

function detailTone(tone) {
  return tone === 'gain' ? 'text-gain' : tone === 'loss' ? 'text-loss' : 'text-slate-100'
}

function fmtWhen(value) {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString()
}

function onDismiss() {
  if (props.busy) return
  emit(props.result ? 'close' : 'cancel')
}

function onKey(e) {
  if (e.key === 'Escape') onDismiss()
}

onMounted(() => {
  document.addEventListener('keydown', onKey)
  nextTick(() => panelEl.value?.focus({ preventScroll: true }))
})
onUnmounted(() => document.removeEventListener('keydown', onKey))
</script>

<style scoped>
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 40px;
  padding: 8px 14px;
  font-size: 14px;
  line-height: 20px;
}
.row > dt { color: #a1a1aa; min-width: 0; }
.row > dd { text-align: right; min-width: 0; overflow-wrap: anywhere; }
</style>
