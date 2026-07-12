<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import CryptoCreateInstanceModal from '../components/crypto/CryptoCreateInstanceModal.vue'
import CryptoBacktestModal from '../components/crypto/CryptoBacktestModal.vue'
import { getToken } from '../utils/auth.js'

const router = useRouter()

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')
function authHeaders() {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

const instances = ref([])
const brokerages = ref([])
const values = ref({}) // { [brokerageId]: current_value }
const loading = ref(true)
const loadError = ref('')
const busy = ref({}) // { [id]: bool }

const brokerageMap = computed(() => {
  const m = {}
  for (const b of brokerages.value) m[b.id] = b.account_name
  return m
})

function setBusy(id, v) { busy.value = { ...busy.value, [id]: v } }
function fmtUsd(n) { return `$${Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}` }

// Fixed allocations → display chips (pct stored as a fraction 0-1).
function allocChips(inst) {
  const allocs = inst?.crypto_config?.allocations || []
  return allocs.map((a) => ({
    ticker: String(a.symbol || '').split('/')[0],
    pct: Math.round((Number(a.pct) || 0) * 100),
  }))
}
function fixedPct(inst) {
  const allocs = inst?.crypto_config?.allocations || []
  return allocs.reduce((s, a) => s + (Number(a.pct) || 0), 0) * 100
}
function dynamicPct(inst) { return Math.max(0, 100 - fixedPct(inst)) }

async function fetchBrokerages() {
  try {
    const res = await fetch(`${API_BASE}/brokerages`, { headers: authHeaders() })
    if (!res.ok) return
    const d = await res.json()
    brokerages.value = (d.accounts || []).filter((a) => a.brokerage_type === 'alpaca')
  } catch { /* non-critical */ }
}

async function fetchValue(brokerageId) {
  if (!brokerageId || values.value[brokerageId] != null) return
  try {
    const res = await fetch(`${API_BASE}/brokerages/${brokerageId}/portfolio-history?range=1D`, { headers: authHeaders() })
    if (!res.ok) return
    const d = await res.json()
    const v = Number(d?.current_value)
    if (Number.isFinite(v)) values.value = { ...values.value, [brokerageId]: v }
  } catch { /* best-effort */ }
}

async function fetchInstances() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await fetch(`${API_BASE}/instances`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const d = await res.json()
    instances.value = (d.instances || []).filter((i) => i.kind === 'crypto')
    // Best-effort per-brokerage account value for the card headline.
    for (const inst of instances.value) if (inst.brokerage_id) fetchValue(inst.brokerage_id)
  } catch (e) {
    loadError.value = `Failed to load crypto instances: ${e?.message || 'request failed'}`
  } finally {
    loading.value = false
  }
}

async function startInstance(id) {
  setBusy(id, true)
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(id)}/start`, { method: 'POST', headers: authHeaders() })
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${res.status}`) }
    await fetchInstances()
  } catch (e) { alert(`Start failed: ${e.message}`) } finally { setBusy(id, false) }
}

async function stopInstance(id) {
  setBusy(id, true)
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(id)}/stop`, { method: 'POST', headers: authHeaders() })
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${res.status}`) }
    await fetchInstances()
  } catch (e) { alert(`Stop failed: ${e.message}`) } finally { setBusy(id, false) }
}

async function deleteInstance(inst) {
  if (!confirm(`Delete crypto instance "${inst.name || inst.id}"? This removes it permanently.`)) return
  setBusy(inst.id, true)
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(inst.id)}?force=true`, { method: 'DELETE', headers: authHeaders() })
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${res.status}`) }
    await fetchInstances()
  } catch (e) { alert(`Delete failed: ${e.message}`) } finally { setBusy(inst.id, false) }
}

// ── Create / edit modal ───────────────────────────────────────────────────────
const showModal = ref(false)
const editTarget = ref(null)

function openCreate() { editTarget.value = null; showModal.value = true }
function openEdit(inst) { editTarget.value = inst; showModal.value = true }
function closeModal() { showModal.value = false; editTarget.value = null }
function onSaved() { closeModal(); fetchInstances() }

// ── Backtest modal ─────────────────────────────────────────────────────────────
const showBacktest = ref(false)
const backtestTarget = ref(null)
function openBacktest(inst) { backtestTarget.value = inst; showBacktest.value = true }
function closeBacktest() { showBacktest.value = false; backtestTarget.value = null }
function onBacktestCreated(id) { if (id) router.push(`/backtests/${id}`) }

// ── Detail view ────────────────────────────────────────────────────────────────
function openDetail(inst) { router.push(`/crypto/instances/${encodeURIComponent(inst.id)}`) }

onMounted(async () => { await Promise.all([fetchBrokerages(), fetchInstances()]) })
</script>

<template>
  <AppShell>
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10">

      <!-- Header -->
      <div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between mb-6">
        <div>
          <p class="text-primary text-xs font-bold uppercase tracking-widest mb-1 flex items-center gap-1.5">
            <span class="material-symbols-outlined text-[16px]">currency_bitcoin</span> Trading
          </p>
          <h1 class="text-2xl sm:text-3xl font-bold leading-tight">Crypto</h1>
          <p class="text-slate-400 text-sm mt-1 max-w-md">Autonomous 24/7 crypto trading — pin fixed weights, leave the rest dynamic.</p>
        </div>
        <div class="flex items-center gap-3 w-full sm:w-auto">
          <button @click="fetchInstances"
                  class="inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border border-border-subtle text-sm text-slate-400 hover:text-slate-200 hover:bg-surface transition-all shrink-0" title="Refresh">
            <span class="material-symbols-outlined text-[18px]">refresh</span>
          </button>
          <button @click="openCreate"
                  class="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary text-background-dark font-semibold text-sm hover:brightness-110 transition-all flex-1 sm:flex-none">
            <span class="material-symbols-outlined text-[18px]">add</span>
            New crypto instance
          </button>
        </div>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="flex items-center gap-3 text-slate-400 text-sm">
        <span class="material-symbols-outlined animate-spin text-xl">progress_activity</span>
        Loading crypto instances...
      </div>

      <!-- Error -->
      <div v-else-if="loadError" class="rounded-xl bg-red-500/10 border border-red-500/20 px-5 py-4 text-red-400 text-sm">{{ loadError }}</div>

      <!-- No brokerage -->
      <div v-else-if="!brokerages.length && !instances.length"
           class="rounded-2xl border border-border-subtle bg-surface/20 px-8 py-12 flex flex-col items-center gap-3 text-center">
        <span class="material-symbols-outlined text-4xl text-slate-700">currency_bitcoin</span>
        <p class="text-slate-400 text-sm font-medium">No Alpaca account linked.</p>
        <p class="text-slate-600 text-xs max-w-sm">Link an Alpaca brokerage to create a crypto trading instance.</p>
        <RouterLink to="/brokerages" class="mt-1 px-4 py-2 rounded-lg bg-primary text-background-dark text-xs font-bold hover:brightness-110 transition-all">Link a brokerage</RouterLink>
      </div>

      <!-- Empty state -->
      <div v-else-if="instances.length === 0"
           class="rounded-2xl border border-border-subtle bg-surface/30 px-8 py-16 flex flex-col items-center gap-4 text-center">
        <span class="material-symbols-outlined text-5xl text-primary/70">currency_bitcoin</span>
        <p class="text-slate-300 font-semibold">No crypto instances yet</p>
        <p class="text-slate-500 text-sm max-w-xs">Create a crypto instance to trade a fixed + dynamic coin allocation around the clock.</p>
        <button @click="openCreate" class="mt-2 px-5 py-2 rounded-lg bg-primary text-background-dark font-semibold text-sm hover:brightness-110 transition-all">
          Create your first crypto instance
        </button>
      </div>

      <!-- Instance cards -->
      <div v-else class="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-5">
        <div v-for="inst in instances" :key="inst.id" class="glass-card rounded-2xl p-5 sm:p-6 flex flex-col gap-4">
          <!-- Header -->
          <div class="flex items-start justify-between gap-3 min-w-0">
            <div class="flex items-center gap-3 min-w-0 cursor-pointer group" @click="openDetail(inst)" title="View details">
              <div class="size-10 rounded-xl bg-surface border border-border-subtle flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-primary text-xl">currency_bitcoin</span>
              </div>
              <div class="min-w-0">
                <p class="font-semibold text-slate-100 leading-tight truncate group-hover:text-primary transition-colors" :title="inst.name || inst.id">{{ inst.name || inst.id }}</p>
                <p class="text-xs text-slate-500 font-mono mt-0.5 truncate">{{ inst.id }}</p>
              </div>
            </div>
            <div class="flex flex-col items-end gap-1.5 shrink-0">
              <span class="text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-primary/15 text-primary border border-primary/20">24/7 · crypto</span>
              <div class="flex items-center gap-1.5 text-xs font-semibold px-2 py-1 rounded-full"
                   :class="inst.crashed ? 'bg-red-500/10 text-red-400 border border-red-500/20'
                     : inst.runCommand ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                     : 'bg-slate-500/10 text-slate-500 border border-slate-700'">
                <div class="size-1.5 rounded-full" :class="inst.crashed ? 'bg-red-400' : inst.runCommand ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'"></div>
                {{ inst.crashed ? 'Crashed' : inst.runCommand ? 'Running' : 'Stopped' }}
              </div>
            </div>
          </div>

          <!-- Value -->
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">Account value</p>
            <p class="text-xl font-bold text-slate-100 tabular-nums mt-0.5">
              {{ inst.brokerage_id && values[inst.brokerage_id] != null ? fmtUsd(values[inst.brokerage_id]) : '—' }}
            </p>
          </div>

          <!-- Meta -->
          <div class="space-y-1.5 text-xs text-slate-500">
            <div v-if="inst.brokerage_id" class="flex items-center gap-1.5 min-w-0">
              <span class="text-slate-400 shrink-0">Brokerage:</span>
              <span class="text-slate-300 truncate">{{ brokerageMap[inst.brokerage_id] || inst.brokerage_id }}</span>
            </div>
            <div v-if="inst.crypto_config?.band" class="flex items-center gap-1.5">
              <span class="text-slate-400">Band:</span>
              <span class="text-slate-300 capitalize">{{ inst.crypto_config.band }}</span>
            </div>
          </div>

          <!-- Allocation chips -->
          <div>
            <p class="text-xs font-medium text-slate-400 uppercase tracking-wider mb-2">Allocation</p>
            <div class="flex flex-wrap gap-1.5">
              <span v-for="c in allocChips(inst)" :key="c.ticker"
                    class="px-2 py-0.5 rounded-md bg-surface border border-border-subtle text-xs font-mono text-slate-300 tabular-nums">
                {{ c.ticker }} {{ c.pct }}%
              </span>
              <span class="px-2 py-0.5 rounded-md bg-primary/10 border border-primary/30 text-xs font-mono text-primary tabular-nums">
                Dynamic {{ Math.round(dynamicPct(inst)) }}%
              </span>
            </div>
          </div>

          <!-- Actions -->
          <div class="flex flex-wrap items-center gap-2 pt-1 mt-auto">
            <button @click="openDetail(inst)" :disabled="busy[inst.id]"
                    class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-slate-300 hover:bg-surface border border-border-subtle transition-colors disabled:opacity-40">
              <span class="material-symbols-outlined text-[14px]">visibility</span> View
            </button>
            <button @click="openEdit(inst)" :disabled="busy[inst.id]"
                    class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-amber-400 hover:bg-amber-500/10 border border-amber-500/20 transition-colors disabled:opacity-40">
              <span class="material-symbols-outlined text-[14px]">tune</span> Edit
            </button>
            <button @click="openBacktest(inst)" :disabled="busy[inst.id]"
                    class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-sky-400 hover:bg-sky-500/10 border border-sky-500/20 transition-colors disabled:opacity-40">
              <span class="material-symbols-outlined text-[14px]">analytics</span> Backtest
            </button>
            <button v-if="!inst.runCommand" @click="startInstance(inst.id)" :disabled="busy[inst.id]"
                    class="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-emerald-400 hover:bg-emerald-500/10 border border-emerald-500/20 transition-colors disabled:opacity-40">
              <span v-if="busy[inst.id]" class="material-symbols-outlined text-[14px] animate-spin">progress_activity</span>
              <span v-else class="material-symbols-outlined text-[14px]">play_arrow</span> Start
            </button>
            <button v-else @click="stopInstance(inst.id)" :disabled="busy[inst.id]"
                    class="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-amber-400 hover:bg-amber-500/10 border border-amber-500/20 transition-colors disabled:opacity-40">
              <span v-if="busy[inst.id]" class="material-symbols-outlined text-[14px] animate-spin">progress_activity</span>
              <span v-else class="material-symbols-outlined text-[14px]">stop</span> Stop
            </button>
            <button @click="deleteInstance(inst)" :disabled="busy[inst.id]"
                    class="flex items-center px-2 py-1.5 rounded-lg border border-red-500/30 text-red-400 text-xs font-semibold hover:bg-red-500/10 transition-all disabled:opacity-40" title="Delete instance">
              <span class="material-symbols-outlined text-[16px]">delete</span>
            </button>
          </div>
        </div>
      </div>
    </main>

    <CryptoCreateInstanceModal
      v-if="showModal"
      :edit-instance="editTarget"
      :initial-brokerage-id="brokerages[0]?.id || ''"
      @close="closeModal"
      @created="onSaved"
      @saved="onSaved"
    />

    <CryptoBacktestModal
      v-if="showBacktest"
      :instance="backtestTarget"
      @close="closeBacktest"
      @created="onBacktestCreated"
    />
  </AppShell>
</template>
