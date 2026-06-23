<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import KalshiPortfolioChart from '../components/kalshi/KalshiPortfolioChart.vue'
import KalshiCreateInstanceModal from '../components/kalshi/KalshiCreateInstanceModal.vue'
import InstanceLiveLogs from '../components/InstanceLiveLogs.vue'
import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')
function authHeaders() {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

const route = useRoute()
const router = useRouter()
const id = route.params.id

const detail = ref(null)
const decisions = ref([])
const summary = ref({ total: 0, placed: 0, skipped: 0, queued: 0, blocked: 0 })
const loading = ref(true)
const busy = ref(false)
const expanded = ref(new Set())

async function getJson(path) {
  try {
    const res = await fetch(`${API_BASE}${path}`, { headers: authHeaders() })
    if (!res.ok) return null
    return await res.json()
  } catch { return null }
}

async function load() {
  loading.value = true
  const [d, dec] = await Promise.all([
    getJson(`/instances/${id}/kalshi/detail`),
    getJson(`/instances/${id}/kalshi/decisions?limit=200`),
  ])
  detail.value = d
  if (dec) { decisions.value = dec.decisions || []; summary.value = dec.summary || summary.value }
  loading.value = false
}

async function startStop(start) {
  if (busy.value) return
  busy.value = true
  try {
    await fetch(`${API_BASE}/instances/${id}/${start ? 'start' : 'stop'}`, { method: 'POST', headers: authHeaders() })
    await load()
  } finally { busy.value = false }
}
async function kill() {
  if (busy.value || !detail.value) return
  if (!confirm('Stop this instance and cancel all resting orders?')) return
  busy.value = true
  try {
    await fetch(`${API_BASE}/brokerages/${detail.value.brokerage_id}/kalshi/kill`, { method: 'POST', headers: authHeaders() })
    await load()
  } finally { busy.value = false }
}

function toggle(i) {
  const s = new Set(expanded.value)
  s.has(i) ? s.delete(i) : s.add(i)
  expanded.value = s
}
const running = computed(() => !!detail.value?.running)

// Edit / delete
const showEdit = ref(false)
const deleting = ref(false)
const editBrokerages = computed(() => detail.value
  ? [{ id: detail.value.brokerage_id, account_name: detail.value.name, kalshi_environment: detail.value.environment }]
  : [])
function onSaved() { showEdit.value = false; load() }
async function del() {
  if (deleting.value) return
  if (!confirm('Delete this Kalshi instance? This cannot be undone.')) return
  deleting.value = true
  try {
    await fetch(`${API_BASE}/instances/${id}?force=true`, { method: 'DELETE', headers: authHeaders() })
    router.push('/kalshi')
  } finally { deleting.value = false }
}

function pct(v) { return v == null ? '—' : `${(v * 100).toFixed(1)}%` }
function decColor(d) {
  return { placed: 'text-emerald-400', skipped: 'text-slate-500', queued: 'text-amber-400', blocked: 'text-red-400' }[d] || 'text-slate-400'
}

onMounted(load)
</script>

<template>
  <AppShell>
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10 space-y-6">
      <RouterLink to="/kalshi" class="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200">
        <span class="material-symbols-outlined text-[16px]">arrow_back</span> Kalshi
      </RouterLink>

      <div v-if="loading" class="glass-card rounded-2xl h-[160px] animate-pulse"></div>

      <template v-else-if="detail">
        <!-- Header -->
        <div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p class="text-primary text-xs font-bold uppercase tracking-widest mb-2 flex items-center gap-1.5">
              <span class="material-symbols-outlined text-[16px]">smart_toy</span> Kalshi instance
            </p>
            <h1 class="text-2xl sm:text-3xl font-bold text-slate-100">{{ detail.name }}</h1>
            <div class="flex items-center gap-2 mt-2 text-xs">
              <span class="px-2 py-0.5 rounded-md font-medium" :class="running ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-500/15 text-slate-400'">{{ running ? 'Running' : 'Stopped' }}</span>
              <span class="px-2 py-0.5 rounded-md font-medium" :class="detail.environment === 'live' ? 'bg-red-500/15 text-red-400' : 'bg-primary/15 text-primary'">{{ detail.environment === 'live' ? 'Live · real money' : 'Paper' }}</span>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <button @click="startStop(!running)" :disabled="busy"
                    class="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold transition-all disabled:opacity-50"
                    :class="running ? 'border border-amber-500/40 text-amber-400 hover:bg-amber-500/10' : 'bg-primary text-background-dark hover:brightness-110'">
              <span class="material-symbols-outlined text-[18px]">{{ running ? 'pause' : 'play_arrow' }}</span>{{ running ? 'Stop' : 'Start' }}
            </button>
            <button v-if="running" @click="kill" :disabled="busy"
                    class="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-red-500/40 text-red-400 text-sm font-semibold hover:bg-red-500/10 transition-all disabled:opacity-50">
              <span class="material-symbols-outlined text-[18px]">stop_circle</span> Kill
            </button>
            <button @click="showEdit = true" :disabled="busy"
                    class="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border-subtle text-slate-300 text-sm font-semibold hover:text-slate-100 hover:border-primary/50 transition-all disabled:opacity-50">
              <span class="material-symbols-outlined text-[18px]">tune</span> Edit
            </button>
            <button @click="del" :disabled="deleting"
                    class="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-red-500/40 text-red-400 text-sm font-semibold hover:bg-red-500/10 transition-all disabled:opacity-50">
              <span class="material-symbols-outlined text-[18px]">delete</span> Delete
            </button>
          </div>
        </div>

        <!-- Equity + decision summary -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <KalshiPortfolioChart :brokerage-id="detail.brokerage_id" />
          <div class="glass-card rounded-2xl p-4 sm:p-5">
            <div class="flex items-center gap-2 mb-4"><span class="material-symbols-outlined text-primary text-[18px]">insights</span><span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Decision summary</span></div>
            <div class="grid grid-cols-2 gap-3">
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3"><div class="text-lg font-bold text-emerald-400 tabular-nums">{{ summary.placed }}</div><div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Placed</div></div>
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3"><div class="text-lg font-bold text-slate-100 tabular-nums">{{ summary.skipped }}</div><div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Skipped</div></div>
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3"><div class="text-lg font-bold text-amber-400 tabular-nums">{{ summary.queued }}</div><div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Queued</div></div>
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3"><div class="text-lg font-bold text-red-400 tabular-nums">{{ summary.blocked }}</div><div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Blocked</div></div>
            </div>
          </div>
        </div>

        <!-- Decision log -->
        <div class="glass-card rounded-2xl p-4 sm:p-5">
          <div class="flex items-center gap-2 mb-3"><span class="material-symbols-outlined text-primary text-[18px]">history_edu</span><span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Decision log</span></div>
          <p v-if="!decisions.length" class="text-sm text-slate-500">No decisions logged yet. The engine writes a row for every bet placed and every candidate it considered.</p>
          <div v-for="(d, i) in decisions" :key="d.id || i" class="border-b border-border-subtle/60 last:border-0">
            <button @click="toggle(i)" class="w-full flex items-center justify-between gap-2 py-2.5 text-left">
              <span class="flex items-center gap-2 min-w-0">
                <span class="material-symbols-outlined text-slate-500 text-[16px] transition-transform" :class="{ 'rotate-90': expanded.has(i) }">chevron_right</span>
                <span class="text-sm text-slate-300 truncate">{{ d.market_ticker }} <span class="text-slate-500">· {{ d.side }}</span></span>
              </span>
              <span class="flex items-center gap-3 shrink-0 text-xs">
                <span class="tabular-nums" :class="(d.edge || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'">{{ d.edge == null ? '' : (d.edge >= 0 ? '+' : '') + pct(d.edge) }}</span>
                <span class="font-semibold uppercase" :class="decColor(d.decision)">{{ d.decision }}</span>
              </span>
            </button>
            <div v-if="expanded.has(i)" class="pb-3 pl-6 text-xs text-slate-400 space-y-1">
              <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <div><span class="text-slate-600">Model</span> {{ pct(d.model_prob) }}</div>
                <div><span class="text-slate-600">Sharp</span> {{ pct(d.sharp_prob) }}</div>
                <div><span class="text-slate-600">LLM adj</span> {{ d.llm_adjustment == null ? '—' : (d.llm_adjustment >= 0 ? '+' : '') + pct(d.llm_adjustment) }}</div>
                <div><span class="text-slate-600">Fair</span> {{ pct(d.fused_fair) }}</div>
                <div><span class="text-slate-600">Size</span> {{ d.size || 0 }}</div>
                <div><span class="text-slate-600">Opp score</span> {{ d.opportunity_score == null ? '—' : d.opportunity_score.toFixed(2) }}</div>
                <div v-if="d.outcome"><span class="text-slate-600">Outcome</span> {{ d.outcome }}</div>
                <div v-if="d.clv != null"><span class="text-slate-600">CLV</span> {{ pct(d.clv) }}</div>
              </div>
              <p v-if="d.llm_rationale" class="text-slate-300 bg-surface/40 border border-border-subtle rounded-lg px-3 py-2 mt-1">
                <span class="material-symbols-outlined text-primary text-[14px] align-middle mr-1">psychology</span>{{ d.llm_rationale }}
              </p>
              <p v-if="d.block_reason" class="text-red-400/80">Blocked: {{ d.block_reason }}</p>
            </div>
          </div>
        </div>

        <!-- Live logs -->
        <div class="glass-card rounded-2xl p-4 sm:p-5">
          <div class="flex items-center gap-2 mb-3"><span class="material-symbols-outlined text-primary text-[18px]">terminal</span><span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Live logs</span></div>
          <InstanceLiveLogs :key="id" :instance-id="id" />
        </div>
      </template>

      <div v-else class="rounded-2xl border border-border-subtle bg-surface/20 px-8 py-12 text-center text-slate-400">
        Instance not found. <button @click="router.push('/kalshi')" class="text-primary">Back to Kalshi</button>
      </div>
    </main>

    <KalshiCreateInstanceModal
      v-if="showEdit && detail"
      :brokerages="editBrokerages"
      :initial-brokerage-id="detail.brokerage_id"
      :edit-instance="{ id, name: detail.name, config: detail.config }"
      @close="showEdit = false"
      @saved="onSaved"
    />
  </AppShell>
</template>
