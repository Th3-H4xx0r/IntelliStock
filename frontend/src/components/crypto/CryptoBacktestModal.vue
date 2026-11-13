<script setup>
import { ref, computed, onMounted } from 'vue'
import { getToken } from '../../utils/auth.js'

// Launch a backtest of a crypto instance's *own* configured allocation. The
// backend runs crypto instances through the same broker.py as live (v1beta3
// historical bars + taker-fee fills + synthetic strategy from crypto_config), so
// this just POSTs /backtests with the instance's slash-pairs. Emits `created`
// with the new backtest id; the parent decides whether to navigate to the result
// view or refresh in place. Mirrors the equity create-backtest form.
const props = defineProps({
  instance: { type: Object, required: true }, // { id, name, crypto_config, stocks }
})
const emit = defineEmits(['close', 'created'])

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')
function authHeaders() {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

// ── Coins backtested = the instance's fixed allocations (empty ⇒ 100% dynamic) ──
const allocations = computed(() => {
  const a = props.instance?.crypto_config?.allocations
  return Array.isArray(a) ? a : []
})
const coins = computed(() =>
  allocations.value.map((a) => ({
    ticker: String(a.symbol || '').split('/')[0],
    pct: Math.round((Number(a.pct) || 0) * 100),
  })),
)
const fixedPct = computed(() => allocations.value.reduce((s, a) => s + (Number(a.pct) || 0), 0) * 100)
const dynamicPct = computed(() => Math.max(0, Math.round(100 - fixedPct.value)))
// Slash-pairs feed the backtest symbol universe; empty ⇒ pure auto-discovery.
const stocks = computed(() => allocations.value.map((a) => a.symbol).filter(Boolean))

// ── Bar cadence is DERIVED from the instance's Band (the single source of truth,
//    set in create/edit) — not a separate control. High/Medium/Low → 5/15/60 min. ─
const BAND_GRAN = { high: '300', medium: '900', low: '3600' }
const CADENCE_LABEL = {
  high: 'High · ~5-minute bars',
  medium: 'Medium · ~15-minute bars',
  low: 'Low · ~60-minute bars',
}
const band = computed(() => String(props.instance?.crypto_config?.band || 'medium').toLowerCase())
const granularity = computed(() => BAND_GRAN[band.value] || '900')
const cadenceLabel = computed(() => CADENCE_LABEL[band.value] || CADENCE_LABEL.medium)

// ── Dates (default: last 90 days → today) ───────────────────────────────────────
function isoDate(d) { return d.toISOString().slice(0, 10) }
const startDate = ref('')
const endDate = ref('')

const cash = ref('10000')
const creating = ref(false)
const err = ref('')

// ── Emulate fees: value the strategy at a chosen venue's taker fee instead of
//    the instance's own brokerage. 'default' = use the instance's brokerage. ──
const FEE_VENUES = [
  { id: 'default', label: "Default — instance's brokerage" },
  { id: 'binanceus', label: 'Binance.US — 0.02% taker' },
  { id: 'alpaca', label: 'Alpaca — 0.25% taker' },
  { id: 'kraken', label: 'Kraken — 0.26% taker' },
  { id: 'coinbase', label: 'Coinbase Advanced — 0.60% taker' },
]
const feeVenue = ref('default')

onMounted(() => {
  const today = new Date()
  const ago = new Date()
  ago.setDate(ago.getDate() - 90)
  endDate.value = isoDate(today)
  startDate.value = isoDate(ago)
})

async function submit() {
  if (creating.value) return
  err.value = ''
  if (!startDate.value) { err.value = 'Start date is required'; return }
  if (!endDate.value) { err.value = 'End date is required'; return }
  if (startDate.value >= endDate.value) { err.value = 'End date must be after start date'; return }

  creating.value = true
  try {
    const payload = {
      instance_id: props.instance.id,
      stocks: stocks.value,
      start_date: startDate.value,
      end_date: endDate.value,
      granularity: String(granularity.value || '900'),
      initial_cash: parseFloat(cash.value) || 10000,
      emulate_fee_venue: feeVenue.value,
    }
    const res = await fetch(`${API_BASE}/backtests`, {
      method: 'POST', headers: authHeaders(), body: JSON.stringify(payload),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) { err.value = data.detail || `Backtest failed (HTTP ${res.status})`; return }
    const id = data.id ?? data.backtest_id
    emit('created', id != null ? String(id) : null)
    emit('close')
  } catch (e) { err.value = String(e.message || e) } finally { creating.value = false }
}
</script>

<template>
  <div class="fixed inset-0 z-[60] flex items-center justify-center p-4">
    <div @click="emit('close')" class="absolute inset-0 bg-black/60"></div>
    <div class="relative ui-modal max-w-lg max-h-[92vh] overflow-y-auto">
      <!-- Header -->
      <div class="ui-modal-head sticky top-0 z-10 bg-surface border-b border-white/[0.06]">
        <div class="min-w-0">
          <h2 class="text-base font-medium text-slate-100">Backtest crypto instance</h2>
          <p class="text-sm text-slate-500 mt-1">Uses the instance's configured allocation</p>
        </div>
        <button @click="emit('close')" class="ui-icon-btn ml-auto shrink-0" aria-label="Close"><span class="material-symbols-outlined text-[18px]">close</span></button>
      </div>

      <div class="px-6 py-5 space-y-5">
        <p class="text-sm text-slate-400 leading-relaxed">
          Simulate <span class="text-slate-200">{{ instance.name || instance.id }}</span>'s current allocation over a
          historical window. Crypto fills include the taker fee; the auto-discovered
          <span class="text-slate-200">Dynamic</span> portion is traded exactly as it would be live.
        </p>

        <!-- Coins summary (read-only — backtests the configured allocation) -->
        <div>
          <p class="ui-field-label">Allocation under test</p>
          <p class="text-sm text-slate-200 tabular-nums">
            <span v-for="(c, ci) in coins" :key="c.ticker"><span v-if="ci" class="text-slate-600"> · </span>{{ c.ticker }} <span class="text-slate-400">{{ c.pct }}%</span></span>
            <span v-if="dynamicPct > 0"><span v-if="coins.length" class="text-slate-600"> · </span>Dynamic <span class="text-slate-400">{{ dynamicPct }}%</span></span>
          </p>
          <p v-if="!coins.length" class="ui-help">100% dynamic — the backtest auto-discovers its own universe.</p>
        </div>

        <!-- Date range -->
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="ui-field-label">Start date</label>
            <input v-model="startDate" type="date" class="ui-input [color-scheme:dark]" />
          </div>
          <div>
            <label class="ui-field-label">End date</label>
            <input v-model="endDate" type="date" class="ui-input [color-scheme:dark]" />
          </div>
        </div>

        <!-- Cadence (read-only — derived from the instance's Band) -->
        <div>
          <label class="ui-field-label">Cadence</label>
          <div class="h-9 flex items-center gap-2 rounded-lg border border-white/[0.06] px-3">
            <span class="material-symbols-outlined text-slate-500 text-[16px]">schedule</span>
            <span class="text-sm text-slate-200">{{ cadenceLabel }}</span>
            <span class="ml-auto text-xs text-slate-500">From Band. Change it in Edit</span>
          </div>
        </div>

        <!-- Emulate fees (crypto): value the strategy at another venue's taker fee -->
        <div>
          <label class="ui-field-label">
            Emulate fees <span class="text-slate-500">· taker rate applied to fills</span>
          </label>
          <div class="relative">
            <select v-model="feeVenue" class="ui-input appearance-none pr-9">
              <option v-for="v in FEE_VENUES" :key="v.id" :value="v.id">{{ v.label }}</option>
            </select>
            <span class="material-symbols-outlined text-slate-500 text-[18px] absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none">expand_more</span>
          </div>
          <p class="ui-help leading-snug">
            <template v-if="feeVenue === 'default'">Uses this instance's own brokerage fee.</template>
            <template v-else>Emulated — fills charged at the selected venue's fee, not your instance's. Handy to compare a strategy across venues (fees make or break high-frequency crypto).</template>
          </p>
        </div>

        <!-- Initial cash -->
        <div>
          <label class="ui-field-label">Initial cash</label>
          <div class="relative">
            <span class="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-sm">$</span>
            <input v-model="cash" type="number" min="0" step="100" class="ui-input pl-7 tabular-nums" />
          </div>
        </div>

        <p v-if="err" class="text-sm text-loss">{{ err }}</p>
      </div>

      <!-- Footer -->
      <div class="ui-modal-foot sticky bottom-0 bg-surface">
        <button @click="emit('close')" class="ui-btn-ghost h-9 px-4">Cancel</button>
        <button @click="submit" :disabled="creating" class="ui-btn-primary">
          {{ creating ? 'Queuing…' : 'Run backtest' }}
        </button>
      </div>
    </div>
  </div>
</template>
