<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getToken } from '../../utils/auth.js'

// Launch a backtest of a crypto instance's *own* configured allocation. The
// backend runs crypto instances through the same broker.py as live (v1beta3
// historical bars + taker-fee fills + synthetic strategy from crypto_config), so
// this just POSTs /backtests with the instance's slash-pairs and navigates to the
// generic result view. Mirrors the equity create-backtest form in InstancesView.
const props = defineProps({
  instance: { type: Object, required: true }, // { id, name, crypto_config, stocks }
})
const emit = defineEmits(['close'])

const router = useRouter()

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

// ── Granularity (seconds). Default derived from the instance's band. ────────────
const GRANS = [
  { value: '300', label: '5 min' },
  { value: '900', label: '15 min' },
  { value: '3600', label: '1 hour' },
  { value: '86400', label: '1 day' },
]
const BAND_GRAN = { high: '300', medium: '900', low: '3600' }
const granularity = ref('900')

// ── Dates (default: last 90 days → today) ───────────────────────────────────────
function isoDate(d) { return d.toISOString().slice(0, 10) }
const startDate = ref('')
const endDate = ref('')

const cash = ref('10000')
const creating = ref(false)
const err = ref('')

onMounted(() => {
  const band = String(props.instance?.crypto_config?.band || '').toLowerCase()
  granularity.value = BAND_GRAN[band] || '900'
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
    }
    const res = await fetch(`${API_BASE}/backtests`, {
      method: 'POST', headers: authHeaders(), body: JSON.stringify(payload),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) { err.value = data.detail || `Backtest failed (HTTP ${res.status})`; return }
    const id = data.id ?? data.backtest_id
    emit('close')
    if (id != null) router.push(`/backtests/${id}`)
    else router.push('/backtests')
  } catch (e) { err.value = String(e.message || e) } finally { creating.value = false }
}
</script>

<template>
  <div class="fixed inset-0 z-[60] flex items-center justify-center p-4">
    <div @click="emit('close')" class="absolute inset-0 bg-black/60 backdrop-blur-sm"></div>
    <div class="relative glass-card rounded-2xl w-full max-w-lg max-h-[92vh] overflow-y-auto">
      <!-- Header -->
      <div class="px-6 pt-5 pb-3 border-b border-border-subtle flex items-center justify-between sticky top-0 bg-surface/80 backdrop-blur z-10">
        <h3 class="text-base font-bold text-slate-100 flex items-center gap-2">
          <span class="material-symbols-outlined text-primary text-[20px]">analytics</span>
          Backtest crypto instance
        </h3>
        <button @click="emit('close')" class="text-slate-500 hover:text-slate-300"><span class="material-symbols-outlined">close</span></button>
      </div>

      <div class="px-6 py-5 space-y-5">
        <p class="text-[13.5px] text-slate-400 leading-relaxed">
          Simulate <b class="text-slate-200">{{ instance.name || instance.id }}</b>'s current allocation over a
          historical window. Crypto fills include the taker fee; the auto-discovered
          <b class="text-primary">Dynamic</b> portion is traded exactly as it would be live.
        </p>

        <!-- Coins summary (read-only — backtests the configured allocation) -->
        <div>
          <p class="text-xs font-medium text-slate-400 uppercase tracking-wider mb-2">Allocation under test</p>
          <div class="flex flex-wrap gap-1.5">
            <span v-for="c in coins" :key="c.ticker"
                  class="px-2 py-0.5 rounded-md bg-surface border border-border-subtle text-xs font-mono text-slate-300 tabular-nums">
              {{ c.ticker }} {{ c.pct }}%
            </span>
            <span v-if="dynamicPct > 0"
                  class="px-2 py-0.5 rounded-md bg-primary/10 border border-primary/30 text-xs font-mono text-primary tabular-nums">
              Dynamic {{ dynamicPct }}%
            </span>
          </div>
          <p v-if="!coins.length" class="text-[11px] text-slate-600 mt-1.5">100% dynamic — the backtest auto-discovers its own universe.</p>
        </div>

        <!-- Date range -->
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-xs font-medium text-slate-400 mb-1.5">Start date</label>
            <input v-model="startDate" type="date"
                   class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-400 mb-1.5">End date</label>
            <input v-model="endDate" type="date"
                   class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </div>
        </div>

        <!-- Granularity -->
        <div>
          <label class="block text-xs font-medium text-slate-400 mb-1.5">Granularity</label>
          <div class="inline-flex flex-wrap gap-1.5">
            <button v-for="g in GRANS" :key="g.value" type="button" @click="granularity = g.value"
                    class="px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors"
                    :class="granularity === g.value
                      ? 'bg-primary/[0.14] border-primary/45 text-primary'
                      : 'bg-surface border-border-subtle text-slate-400 hover:text-slate-200'">
              {{ g.label }}
            </button>
          </div>
        </div>

        <!-- Initial cash -->
        <div>
          <label class="block text-xs font-medium text-slate-400 mb-1.5">Initial cash</label>
          <div class="relative">
            <span class="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-sm">$</span>
            <input v-model="cash" type="number" min="0" step="100"
                   class="w-full bg-surface border border-border-subtle rounded-lg pl-7 pr-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </div>
        </div>

        <p v-if="err" class="text-xs text-red-400">{{ err }}</p>
      </div>

      <!-- Footer -->
      <div class="px-6 py-4 border-t border-border-subtle flex gap-3 sticky bottom-0 bg-surface/80 backdrop-blur">
        <button @click="emit('close')" class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200">Cancel</button>
        <button @click="submit" :disabled="creating"
                class="flex-1 py-2.5 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 disabled:opacity-50">
          {{ creating ? 'Queuing…' : 'Run backtest' }}
        </button>
      </div>
    </div>
  </div>
</template>
