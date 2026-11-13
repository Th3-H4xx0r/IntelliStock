<!-- frontend/src/components/swing/WheelPanel.vue
     The wheel lane's book: open cash-secured puts, the collateral they tie
     up, how far each is from being assigned, and the latest weekly scans.

     Read-only. The lane buys puts back by itself (the 15:45 ET monitor and the
     Monday 2x-premium check), so the ITM column is coloured by exactly those
     rules: red means the monitor will buy this put back on its next pass.
     GET /instances/{id}/wheel; the shape is the plan C Contract addendum.
     The header stamps the last successful load ("as of 14:02"): after a failed
     poll the book on screen is that one. Only a 401 stops polling. -->
<template>
  <section class="min-w-0" aria-labelledby="wheel-title">
    <div class="flex items-center justify-between gap-3 min-h-8 mb-3">
      <div class="min-w-0">
        <h2 id="wheel-title" class="text-base font-medium text-slate-100">Wheel</h2>
        <p class="text-sm text-slate-500 mt-0.5 tabular-nums">
          <span v-if="loaded">{{ wheel.openPuts.length }} open</span>
          <span v-if="loaded && loadedAt" class="text-slate-600" aria-hidden="true"> · </span>
          <span v-if="loadedAt">{{ fmtAsOf(loadedAt) }}</span>
          <span v-if="!loaded && !loadedAt">Cash-secured puts</span>
        </p>
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
      <div v-if="loadNote || loadError" class="px-5 pt-4 space-y-2" :class="loaded ? '' : 'pb-4'">
        <div v-if="loadNote" class="rounded-lg border border-white/[0.07] bg-raised px-4 py-3 text-sm text-slate-400">
          {{ loadNote }}
        </div>
        <div v-if="loadError" class="rounded-lg border border-loss/20 bg-loss/5 px-4 py-3 text-sm text-loss">
          {{ loadError }}
        </div>
      </div>
      <div v-if="!loaded && loading">
        <span class="sr-only">Loading</span>
        <div class="px-5 py-4 grid grid-cols-3 gap-4" aria-hidden="true">
          <div v-for="n in 3" :key="n" class="space-y-2">
            <div class="h-3 w-16 ui-skel"></div>
            <div class="h-5 w-20 ui-skel"></div>
          </div>
        </div>
      </div>

      <template v-if="loaded">
        <div class="px-5 py-4 grid grid-cols-3 gap-4">
          <div class="min-w-0">
            <div class="text-xs text-slate-500">Open puts</div>
            <div class="text-base sm:text-lg font-medium text-slate-100 tabular-nums mt-1">{{ wheel.openPuts.length }}</div>
          </div>
          <div class="min-w-0">
            <div class="text-xs text-slate-500">Collateral</div>
            <div class="text-base sm:text-lg font-medium text-slate-100 tabular-nums mt-1 truncate">{{ fmtUsd(wheel.collateralTotal) }}</div>
          </div>
          <div class="min-w-0">
            <div class="text-xs text-slate-500">Cash</div>
            <div class="text-base sm:text-lg font-medium text-slate-100 tabular-nums mt-1 truncate">{{ fmtUsd(wheel.cash) }}</div>
          </div>
        </div>

        <div v-if="!wheel.openPuts.length" class="px-5 py-4 border-t border-white/[0.06] text-sm text-slate-500">No open puts</div>
        <div v-else class="border-t border-white/[0.06] overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="h-10 text-xs text-slate-500 border-b border-white/[0.06]">
                <th class="text-left font-normal pl-5 pr-3 whitespace-nowrap">Contract</th>
                <th class="text-right font-normal px-3 whitespace-nowrap">Qty</th>
                <th class="text-right font-normal px-3 whitespace-nowrap">Entry</th>
                <th class="text-right font-normal px-3 whitespace-nowrap">Mark</th>
                <th class="text-right font-normal px-3 whitespace-nowrap">ITM</th>
                <th class="text-right font-normal px-3 whitespace-nowrap">DTE</th>
                <th class="text-right font-normal px-3 whitespace-nowrap">Collateral</th>
                <th class="text-right font-normal pl-3 pr-5 whitespace-nowrap">P&amp;L</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-white/[0.05]">
              <tr v-for="p in wheel.openPuts" :key="p.contract">
                <td class="pl-5 pr-3 py-2.5">
                  <div class="text-slate-100 whitespace-nowrap">{{ p.underlying }} {{ fmtUsd(p.strike) }} P</div>
                  <div class="text-xs text-slate-500 mt-0.5 whitespace-nowrap tabular-nums">{{ p.contract }} · {{ p.expiry }}</div>
                </td>
                <td class="px-3 py-2.5 text-right text-slate-300 tabular-nums">{{ p.qty ?? '—' }}</td>
                <td class="px-3 py-2.5 text-right text-slate-300 tabular-nums">{{ fmtUsd(p.avgEntryPrice) }}</td>
                <td class="px-3 py-2.5 text-right text-slate-300 tabular-nums">{{ fmtUsd(p.currentPrice) }}</td>
                <td class="px-3 py-2.5 text-right tabular-nums whitespace-nowrap" :class="itmClass(p)">{{ fmtItm(p.itmPct) }}</td>
                <td class="px-3 py-2.5 text-right text-slate-300 tabular-nums">{{ p.dte ?? '—' }}</td>
                <td class="px-3 py-2.5 text-right text-slate-300 tabular-nums">{{ fmtUsd(p.collateral) }}</td>
                <td class="pl-3 pr-5 py-2.5 text-right tabular-nums" :class="pnlClass(p.unrealizedPl)">{{ fmtUsd(p.unrealizedPl) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <p class="h-10 px-5 flex items-center text-xs text-slate-500 border-y border-white/[0.06]">Recent scans</p>
        <div v-if="!wheel.recentScans.length" class="px-5 py-4 text-sm text-slate-500">No scans recorded yet</div>
        <div v-else class="divide-y divide-white/[0.05]">
          <component
            :is="row.status.reviewable ? 'button' : 'div'"
            v-for="row in scanRows"
            :key="row.scan.id || `${row.scan.session}-${row.scan.symbol}`"
            :type="row.status.reviewable ? 'button' : undefined"
            :aria-label="row.status.reviewable ? `${row.scan.symbol}: pending, show its signal` : undefined"
            class="w-full min-h-14 px-5 py-2.5 flex items-center justify-between gap-3 text-left"
            :class="row.status.reviewable ? 'hover:bg-white/[0.03] transition-colors duration-150 ease-out' : ''"
            @click="row.status.reviewable && jumpToSignal(row.signal.id)"
          >
            <div class="min-w-0">
              <span class="text-sm font-medium text-slate-100">{{ row.scan.symbol }}</span>
              <span class="text-sm text-slate-400 ml-2 tabular-nums"><span class="whitespace-nowrap">{{ fmtUsd(row.scan.strike) }} P</span> · <span class="whitespace-nowrap">{{ row.scan.expiry || '—' }}</span></span>
              <p v-if="row.scan.skipReason" class="text-xs text-slate-500 mt-0.5 break-words">{{ row.scan.skipReason }}</p>
            </div>
            <div class="shrink-0 flex items-center gap-3">
              <div class="text-right">
                <span class="inline-flex items-center gap-2 text-sm">
                  <span class="ui-dot" :class="toneClasses(row.status.tone).dot"></span>
                  <span :class="toneClasses(row.status.tone).word">{{ row.status.label }}</span>
                </span>
                <div class="text-xs text-slate-500 mt-0.5 tabular-nums">
                  {{ row.scan.session }}<span v-if="row.scan.score != null"> · score {{ row.scan.score }}</span>
                </div>
              </div>
              <span
                v-if="row.status.reviewable"
                class="material-symbols-outlined text-[18px] text-slate-500"
                aria-hidden="true"
              >arrow_upward</span>
            </div>
          </component>
        </div>
        <p v-if="wheel.recentScans.length" class="px-5 py-3 border-t border-white/[0.06] text-xs text-slate-500 leading-relaxed">
          A scan is a log entry. Approve or reject a put on its pending signal above; select a pending scan to jump to it.
        </p>
      </template>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { getToken } from '../../utils/auth.js'
import { detailText, fmtAsOf, fmtItm, fmtUsd, itmTone, parseWheelPayload, wheelLoadFailure } from '../../utils/swing.js'
import {
  WHEEL_SCAN_TONE_CLASSES, normalizeRecentSignals, swingSignalAnchor, wheelScanSignal, wheelScanStatus,
} from '../../utils/swingReview.js'

// The wheel changes a few times a day; a minute is plenty.
const POLL_MS = 60000

const props = defineProps({
  instanceId: { type: String, required: true },
  apiBase: { type: String, required: true },
  /** Ids in the live pending queue (from PendingSignalsCard). */
  pendingIds: { type: Array, default: () => [] },
  /** Bumped after a decision lands: re-read the signals the scan rows link to. */
  refreshKey: { type: Number, default: 0 },
})
// cash: the account's cash, for the order review's collateral warning.
const emit = defineEmits(['cash'])

const wheel = ref(parseWheelPayload(null))
const loading = ref(false)
const loaded = ref(false)
const loadError = ref('')
const loadNote = ref('')      // not an error: this API build has no wheel route
const loadedAt = ref(null)    // Date of the last successful load
// Recent signals of every status, which the scan rows link to: the scan
// log's own status is written at scan time and never updated (iOS WheelModel).
const recentSignals = ref([])
let pollTimer = null

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

async function loadSignals() {
  try {
    const res = await fetch(
      `${props.apiBase}/instances/${encodeURIComponent(props.instanceId)}/swing/signals?limit=200`,
      { headers: authHeaders() },
    )
    if (!res.ok) return
    recentSignals.value = normalizeRecentSignals(await res.json())
  } catch { /* the scan rows fall back to the scan log's own status */ }
}

async function load() {
  if (loading.value) return
  loading.value = true
  // On the same poll as the book, in parallel (iOS WheelModel.load).
  const recent = loadSignals()
  try {
    const res = await fetch(
      `${props.apiBase}/instances/${encodeURIComponent(props.instanceId)}/wheel`,
      { headers: authHeaders() },
    )
    if (!res.ok) {
      // FW item 4 (M-2): a 404 is not always "no wheel route"; only a 401
      // stops polling, since every other answer can change.
      let detail = ''
      try { detail = detailText((await res.json())?.detail) } catch { /* keep */ }
      const failure = wheelLoadFailure(res.status, detail)
      if (failure.stopPolling) stopPolling()
      loadNote.value = failure.kind === 'no-endpoint' ? failure.message : ''
      loadError.value = failure.kind === 'no-endpoint' ? '' : failure.message
      return
    }
    wheel.value = parseWheelPayload(await res.json())
    loadError.value = ''
    loadNote.value = ''
    loadedAt.value = new Date()
    loaded.value = true
    emit('cash', wheel.value.cash)
  } catch (e) {
    loadError.value = wheelLoadFailure(0, e?.message).message
  } finally {
    await recent
    loading.value = false
  }
}

// Each scan with the signal it produced and that signal's live status.
const scanRows = computed(() => wheel.value.recentScans.map(scan => {
  const signal = wheelScanSignal(scan, recentSignals.value)
  return { scan, signal, status: wheelScanStatus(scan, signal, props.pendingIds) }
}))

function toneClasses(tone) {
  return WHEEL_SCAN_TONE_CLASSES[tone] || WHEEL_SCAN_TONE_CLASSES.neutral
}

// A pending scan jumps to its signal card in the pending list above.
function jumpToSignal(id) {
  const el = document.getElementById(swingSignalAnchor(id))
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// ITM keeps the monitor's rules: loss = it will buy this put back on its
// next pass, amber = in the money, out of the money stays neutral.
function itmClass(put) {
  return {
    alert: 'text-loss',
    itm: 'text-amber-400',
    otm: 'text-slate-300',
    unknown: 'text-slate-500',
  }[itmTone(put.itmPct, put.dte)]
}

function pnlClass(value) {
  if (value == null || value === 0) return 'text-slate-400'
  return value > 0 ? 'text-gain' : 'text-loss'
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

onUnmounted(stopPolling)

watch(() => props.refreshKey, (next, prev) => {
  if (next !== prev) loadSignals()
})

watch(() => props.instanceId, (next, prev) => {
  if (next === prev) return
  wheel.value = parseWheelPayload(null)
  recentSignals.value = []
  loaded.value = false
  loadError.value = ''
  loadNote.value = ''
  loadedAt.value = null
  load()
  startPolling()
})
</script>
