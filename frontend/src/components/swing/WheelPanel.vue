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
  <section class="glass-card rounded-2xl p-5">
    <div class="flex items-center justify-between mb-4 gap-2">
      <p class="text-xs font-bold uppercase tracking-widest text-slate-500">
        Wheel <span v-if="loaded" class="text-slate-700 ml-1">({{ wheel.openPuts.length }} open)</span>
        <span v-if="loadedAt" class="ml-2 normal-case tracking-normal font-medium text-slate-600">{{ fmtAsOf(loadedAt) }}</span>
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

    <div v-if="loadNote" class="mb-3 rounded-lg border border-slate-700 bg-slate-800/40 px-3 py-2 text-xs text-slate-400">
      {{ loadNote }}
    </div>
    <div v-if="loadError" class="mb-3 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
      {{ loadError }}
    </div>
    <div v-if="!loaded && loading" class="text-xs text-slate-500">Loading…</div>

    <template v-if="loaded">
      <div class="grid grid-cols-3 gap-2 mb-4">
        <div class="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
          <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Open puts</div>
          <div class="text-sm font-bold text-slate-100 tabular-nums">{{ wheel.openPuts.length }}</div>
        </div>
        <div class="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
          <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Collateral</div>
          <div class="text-sm font-bold text-slate-100 tabular-nums">{{ fmtUsd(wheel.collateralTotal) }}</div>
        </div>
        <div class="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
          <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Cash</div>
          <div class="text-sm font-bold text-slate-100 tabular-nums">{{ fmtUsd(wheel.cash) }}</div>
        </div>
      </div>

      <div v-if="!wheel.openPuts.length" class="text-xs text-slate-600 italic mb-4">No open puts.</div>
      <div v-else class="rounded-lg border border-slate-800 bg-slate-900/40 overflow-x-auto mb-4">
        <table class="w-full text-xs">
          <thead>
            <tr class="text-slate-500 border-b border-slate-800">
              <th class="text-left font-medium px-3 py-2">Contract</th>
              <th class="text-right font-medium px-3 py-2">Qty</th>
              <th class="text-right font-medium px-3 py-2">Entry</th>
              <th class="text-right font-medium px-3 py-2">Mark</th>
              <th class="text-right font-medium px-3 py-2">ITM</th>
              <th class="text-right font-medium px-3 py-2">DTE</th>
              <th class="text-right font-medium px-3 py-2">Collateral</th>
              <th class="text-right font-medium px-3 py-2">P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in wheel.openPuts" :key="p.contract" class="border-b border-slate-800/60 last:border-0">
              <td class="px-3 py-2">
                <div class="text-slate-200 font-semibold">{{ p.underlying }} {{ fmtUsd(p.strike) }} P</div>
                <div class="text-[10px] text-slate-600 font-mono">{{ p.contract }} · {{ p.expiry }}</div>
              </td>
              <td class="px-3 py-2 text-right text-slate-300 tabular-nums">{{ p.qty ?? '—' }}</td>
              <td class="px-3 py-2 text-right text-slate-300 tabular-nums">{{ fmtUsd(p.avgEntryPrice) }}</td>
              <td class="px-3 py-2 text-right text-slate-300 tabular-nums">{{ fmtUsd(p.currentPrice) }}</td>
              <td class="px-3 py-2 text-right tabular-nums font-semibold" :class="itmClass(p)">{{ fmtItm(p.itmPct) }}</td>
              <td class="px-3 py-2 text-right text-slate-300 tabular-nums">{{ p.dte ?? '—' }}</td>
              <td class="px-3 py-2 text-right text-slate-300 tabular-nums">{{ fmtUsd(p.collateral) }}</td>
              <td class="px-3 py-2 text-right tabular-nums" :class="pnlClass(p.unrealizedPl)">{{ fmtUsd(p.unrealizedPl) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-2">Recent scans</p>
      <div v-if="!wheel.recentScans.length" class="text-xs text-slate-600 italic">No scans recorded yet.</div>
      <div v-else class="space-y-1.5">
        <div
          v-for="scan in wheel.recentScans"
          :key="scan.id || `${scan.session}-${scan.symbol}`"
          class="flex items-start justify-between gap-3 text-xs rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2"
        >
          <div class="min-w-0">
            <span class="font-mono font-semibold text-slate-200">{{ scan.symbol }}</span>
            <span class="text-slate-500 ml-2">{{ fmtUsd(scan.strike) }} P · {{ scan.expiry || '—' }}</span>
            <p v-if="scan.skipReason" class="text-[11px] text-slate-600 mt-0.5 break-words">{{ scan.skipReason }}</p>
          </div>
          <div class="shrink-0 text-right">
            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold border uppercase" :class="scanClass(scan.status)">
              {{ scan.status || '—' }}
            </span>
            <div class="text-[10px] text-slate-600 mt-0.5">
              {{ scan.session }}<span v-if="scan.score != null"> · score {{ scan.score }}</span>
            </div>
          </div>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { getToken } from '../../utils/auth.js'
import { detailText, fmtAsOf, fmtItm, fmtUsd, itmTone, parseWheelPayload, wheelLoadFailure } from '../../utils/swing.js'

// The wheel changes a few times a day; a minute is plenty.
const POLL_MS = 60000

const props = defineProps({
  instanceId: { type: String, required: true },
  apiBase: { type: String, required: true },
})

const wheel = ref(parseWheelPayload(null))
const loading = ref(false)
const loaded = ref(false)
const loadError = ref('')
const loadNote = ref('')      // not an error: this API build has no wheel route
const loadedAt = ref(null)    // Date of the last successful load
let pollTimer = null

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

async function load() {
  if (loading.value) return
  loading.value = true
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
  } catch (e) {
    loadError.value = wheelLoadFailure(0, e?.message).message
  } finally {
    loading.value = false
  }
}

function itmClass(put) {
  return {
    alert: 'text-rose-400',
    itm: 'text-amber-400',
    otm: 'text-emerald-400',
    unknown: 'text-slate-500',
  }[itmTone(put.itmPct, put.dte)]
}

function pnlClass(value) {
  if (value == null || value === 0) return 'text-slate-400'
  return value > 0 ? 'text-emerald-400' : 'text-rose-400'
}

function scanClass(status) {
  return {
    placed: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
    pending: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
    rejected: 'text-rose-400 bg-rose-500/10 border-rose-500/20',
  }[status] || 'text-slate-400 bg-slate-500/10 border-slate-700'
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

watch(() => props.instanceId, (next, prev) => {
  if (next === prev) return
  wheel.value = parseWheelPayload(null)
  loaded.value = false
  loadError.value = ''
  loadNote.value = ''
  loadedAt.value = null
  load()
  startPolling()
})
</script>
