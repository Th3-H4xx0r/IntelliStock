<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
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

// ── Row overflow menu (presentation only: one open at a time; closes on an
//    outside pointerdown or Escape). Items call the same handlers as before. ──
const menuFor = ref(null)
function toggleMenu(id) { menuFor.value = menuFor.value === id ? null : id }
function closeMenu() { menuFor.value = null }
function onDocPointerDown(e) {
  if (menuFor.value == null) return
  if (!e.target.closest?.(`[data-menu-root="${menuFor.value}"]`)) closeMenu()
}
function onDocKeydown(e) { if (e.key === 'Escape') closeMenu() }
function fixedSummary(inst) { return allocChips(inst).map((c) => `${c.ticker} ${c.pct}%`).join(', ') }

onMounted(async () => {
  document.addEventListener('pointerdown', onDocPointerDown)
  document.addEventListener('keydown', onDocKeydown)
  await Promise.all([fetchBrokerages(), fetchInstances()])
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', onDocPointerDown)
  document.removeEventListener('keydown', onDocKeydown)
})
</script>

<template>
  <AppShell>
    <main class="ui-page">

      <!-- Header -->
      <header class="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <div class="min-w-0">
          <h1 class="ui-page-title">Crypto</h1>
          <p class="ui-page-sub">Autonomous 24/7 crypto trading. Pin fixed weights, leave the rest dynamic</p>
        </div>
        <div class="flex items-center gap-2">
          <button @click="fetchInstances" class="ui-icon-btn" title="Refresh" aria-label="Refresh instances">
            <span class="material-symbols-outlined text-[18px]">refresh</span>
          </button>
          <button @click="openCreate" class="ui-btn-primary">
            <span class="material-symbols-outlined text-[16px]">add</span>
            New crypto instance
          </button>
        </div>
      </header>

      <!-- Loading -->
      <div v-if="loading" class="mt-8 ui-card overflow-hidden" aria-hidden="true">
        <div class="h-10 border-b border-white/[0.06]"></div>
        <div class="ui-rows">
          <div v-for="n in 3" :key="n" class="h-16 px-5 flex items-center gap-6">
            <div class="h-4 w-40 ui-skel"></div>
            <div class="h-3 w-16 ui-skel"></div>
            <div class="ml-auto h-4 w-20 ui-skel"></div>
          </div>
        </div>
      </div>

      <!-- Error -->
      <div v-else-if="loadError" class="mt-8 ui-error">{{ loadError }}</div>

      <!-- No brokerage -->
      <div v-else-if="!brokerages.length && !instances.length" class="mt-8 ui-card ui-empty">
        <p>No Alpaca account linked. Link an Alpaca brokerage to create a crypto trading instance.</p>
        <RouterLink to="/brokerages" class="ui-btn-primary">Link a brokerage</RouterLink>
      </div>

      <!-- Empty state -->
      <div v-else-if="instances.length === 0" class="mt-8 ui-card ui-empty">
        <p>No crypto instances yet. Create one to trade a fixed + dynamic coin allocation around the clock.</p>
        <button @click="openCreate" class="ui-btn-primary">Create your first crypto instance</button>
      </div>

      <!-- Instances: one calm row each; the row opens the instance -->
      <section v-else class="mt-8" aria-labelledby="cr-list">
        <div class="mb-3">
          <h2 id="cr-list" class="ui-section-title">Instances</h2>
          <p class="ui-section-sub tabular-nums">{{ instances.length }} instance{{ instances.length === 1 ? '' : 's' }} · open one for its allocation and backtests</p>
        </div>
        <div class="ui-card">
          <div class="cr-row hidden md:grid ui-th">
            <span class="cr-name">Instance</span>
            <span class="cr-status">Status</span>
            <span class="cr-dyn text-right">Dynamic</span>
            <span class="cr-value text-right">Account value</span>
            <span class="cr-ctrl"></span>
          </div>
          <ul class="ui-rows">
            <li v-for="inst in instances" :key="inst.id"
                class="cr-row grid items-center min-h-16 px-5 py-3 cursor-pointer transition-colors duration-150 ease-out hover:bg-white/[0.03] last:rounded-b-xl max-md:first:rounded-t-xl focus-visible:outline-none focus-visible:bg-white/[0.03]"
                tabindex="0"
                :title="`Open ${inst.name || inst.id}`"
                @click="openDetail(inst)"
                @keydown.enter.self="openDetail(inst)">
              <!-- Name + one muted subline -->
              <span class="cr-name min-w-0">
                <span class="block text-sm text-slate-100 truncate">{{ inst.name || inst.id }}</span>
                <span class="block text-xs text-slate-500 mt-0.5 truncate" :title="`${inst.id}${fixedSummary(inst) ? ' · ' + fixedSummary(inst) : ''}`">
                  <template v-if="inst.brokerage_id">{{ brokerageMap[inst.brokerage_id] || inst.brokerage_id }}</template>
                  <template v-if="inst.crypto_config?.band"> · <span class="capitalize">{{ inst.crypto_config.band }}</span> band</template>
                  <template v-if="fixedSummary(inst)"> · {{ fixedSummary(inst) }}</template>
                </span>
              </span>

              <!-- Status: a dot and a word -->
              <span class="cr-status inline-flex items-center gap-2 text-sm"
                    :class="inst.crashed ? 'text-loss' : inst.runCommand ? 'text-slate-200' : 'text-slate-500'">
                <span class="ui-dot" :class="inst.crashed ? 'ui-dot-error' : inst.runCommand ? 'ui-dot-live' : 'ui-dot-off'"></span>
                {{ inst.crashed ? 'Crashed' : inst.runCommand ? 'Running' : 'Stopped' }}
              </span>

              <!-- Key figures -->
              <span class="cr-dyn text-right text-sm text-slate-300 tabular-nums">{{ Math.round(dynamicPct(inst)) }}%</span>
              <span class="cr-value text-right text-sm text-slate-100 tabular-nums">
                {{ inst.brokerage_id && values[inst.brokerage_id] != null ? fmtUsd(values[inst.brokerage_id]) : '—' }}
              </span>

              <!-- One primary control + overflow menu -->
              <span class="cr-ctrl flex items-center justify-end gap-1" @click.stop>
                <button v-if="!inst.runCommand" @click="startInstance(inst.id)" :disabled="busy[inst.id]" class="ui-btn">
                  <span v-if="busy[inst.id]" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                  <span v-else class="material-symbols-outlined text-[16px]">play_arrow</span> Start
                </button>
                <button v-else @click="stopInstance(inst.id)" :disabled="busy[inst.id]" class="ui-btn text-loss">
                  <span v-if="busy[inst.id]" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                  <span v-else class="material-symbols-outlined text-[16px]">stop</span> Stop
                </button>
                <span class="relative" :data-menu-root="inst.id">
                  <button type="button" class="ui-icon-btn" :class="menuFor === inst.id ? 'bg-raised text-slate-200' : ''"
                          @click="toggleMenu(inst.id)" :aria-expanded="menuFor === inst.id" aria-haspopup="menu" aria-label="More actions">
                    <span class="material-symbols-outlined text-[18px]">more_horiz</span>
                  </button>
                  <div v-if="menuFor === inst.id" class="ui-menu" role="menu">
                    <button type="button" role="menuitem" class="ui-menu-item" :disabled="busy[inst.id]" @click="closeMenu(); openDetail(inst)">
                      <span class="material-symbols-outlined text-[16px] text-slate-500">visibility</span> View details
                    </button>
                    <button type="button" role="menuitem" class="ui-menu-item" :disabled="busy[inst.id]" @click="closeMenu(); openEdit(inst)">
                      <span class="material-symbols-outlined text-[16px] text-slate-500">tune</span> Edit
                    </button>
                    <button type="button" role="menuitem" class="ui-menu-item" :disabled="busy[inst.id]" @click="closeMenu(); openBacktest(inst)">
                      <span class="material-symbols-outlined text-[16px] text-slate-500">analytics</span> Backtest
                    </button>
                    <div class="ui-menu-sep"></div>
                    <button type="button" role="menuitem" class="ui-menu-item text-loss" :disabled="busy[inst.id]" @click="closeMenu(); deleteInstance(inst)">
                      <span class="material-symbols-outlined text-[16px]">delete</span> Delete
                    </button>
                  </div>
                </span>
              </span>
            </li>
          </ul>
        </div>
      </section>
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

<style scoped>
/* One calm row per instance.
   phone   name · value / status · controls
   md      name · status · value · controls
   lg      name · status · dynamic · value · controls */
.cr-row {
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas: "name value" "status ctrl";
  column-gap: 16px;
  row-gap: 8px;
}
.cr-name { grid-area: name; }
.cr-status { grid-area: status; }
.cr-dyn { grid-area: dyn; display: none; }
.cr-value { grid-area: value; }
.cr-ctrl { grid-area: ctrl; }
@media (min-width: 768px) {
  .cr-row {
    grid-template-columns: minmax(0, 1fr) 112px 120px 136px;
    grid-template-areas: "name status value ctrl";
    row-gap: 0;
  }
}
@media (min-width: 1024px) {
  .cr-row {
    grid-template-columns: minmax(0, 1fr) 112px 88px 120px 136px;
    grid-template-areas: "name status dyn value ctrl";
  }
  .cr-dyn { display: block; }
}
</style>
