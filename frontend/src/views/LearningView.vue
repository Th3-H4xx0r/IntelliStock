<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

// ── State ─────────────────────────────────────────────────────────────────────
const overview   = ref(null)
const findings   = ref([])
const funnels    = ref([])
const approvals  = ref({ pending: [], pending_count: 0, live_pending_count: 0 })
const hypotheses = ref([])
const floors     = ref([])
const intents    = ref([])
const budget     = ref(null)
const roles      = ref(null)
const deciding   = ref('')
const control    = ref(null)      // { running, config }
const permissions= ref(null)
const models     = ref([])
const showSettings = ref(false)
const busy       = ref('')
const saveError  = ref('')
const saveOk     = ref('')
const draft      = ref({})
const targets    = ref(null)
const loading    = ref(true)
const loadError  = ref('')
const openThread = ref(null)      // the finding whose ladder stepper is expanded
let pollTimer = null

// The six rungs of the promotion ladder. Phase 1 populates only the detection
// step; the rest render locked so the UI never implies an autonomy that is not
// wired yet.
const LADDER = [
  { key: 'PROPOSED',    label: 'Proposed',    hint: 'hypothesis pre-registered with a predicted direction' },
  { key: 'BACKTEST',    label: 'Backtest',    hint: 'paired A/B across windows, must clear the measured noise floor' },
  { key: 'SHADOW',      label: 'Shadow',      hint: 'virtual portfolio on live quotes, no broker surface' },
  { key: 'PAPER',       label: 'Paper',       hint: 'a real paper instance, control-relative' },
  { key: 'LIVE_CAPPED', label: 'Live (capped)', hint: 'real money on a bounded book' },
  { key: 'LIVE_FULL',   label: 'Live (full)', hint: 'applied to the primary live document' },
]

const funnelRows = computed(() =>
  funnels.value.map((row) => ({ ...row, pct: conversionPct(row) })))

const observeOnly = computed(() => !(overview.value?.acts_autonomously))
const modeLabel = computed(() => {
  if (!overview.value) return '—'
  return observeOnly.value ? 'Observe only' : String(overview.value.mode || '')
})

function severityClasses(severity) {
  if (severity === 'high') return 'text-rose-400 bg-rose-500/10 border-rose-500/20'
  if (severity === 'medium') return 'text-amber-400 bg-amber-500/10 border-amber-500/20'
  return 'text-slate-400 bg-slate-500/10 border-slate-700'
}

function conversionPct(row) {
  const decided = Number(row?.buy_decided || 0)
  if (!decided) return null
  return (Number(row?.buy_executed || 0) / decided) * 100
}

function fmtWhen(value) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString()
}

async function getJson(path) {
  const res = await fetch(`${API_BASE}${path}`, { headers: authHeaders() })
  if (res.status === 401) {
    const err = new Error('Session expired — please sign in again.')
    err.unauthorized = true
    throw err
  }
  if (!res.ok) throw new Error(`Request failed (${res.status})`)
  return res.json()
}

// Settled individually, not Promise.all: one missing endpoint used to blank the
// whole tab, including the counters that had loaded fine. These are three
// brand-new endpoints and the backend can lag a redeploy.
async function load() {
  const results = await Promise.allSettled([
    getJson('/learning/overview'),
    getJson('/learning/findings?limit=100'),
    getJson('/learning/funnels?limit=100'),
    getJson('/learning/approvals?limit=100'),
    getJson('/learning/hypotheses?limit=100'),
    getJson('/learning/noise-floors'),
    getJson('/learning/intents?limit=60'),
    getJson('/learning/budget'),
    getJson('/learning/roles'),
    getJson('/learning/control'),
    getJson('/learning/permissions'),
    getJson('/models'),
    getJson('/learning/targets'),
  ])
  const failures = results.filter((r) => r.status === 'rejected')

  if (failures.some((r) => r.reason?.unauthorized)) {
    // Stop polling rather than firing three 401s every 30 seconds forever.
    stopPolling()
    loadError.value = 'Session expired — please sign in again.'
    loading.value = false
    return
  }

  if (results[0].status === 'fulfilled') overview.value = results[0].value
  if (results[1].status === 'fulfilled') findings.value = results[1].value?.findings || []
  if (results[2].status === 'fulfilled') funnels.value = results[2].value?.funnels || []
  if (results[3].status === 'fulfilled') approvals.value = results[3].value || approvals.value
  if (results[4].status === 'fulfilled') hypotheses.value = results[4].value?.hypotheses || []
  if (results[5].status === 'fulfilled') floors.value = results[5].value?.floors || []
  if (results[6].status === 'fulfilled') intents.value = results[6].value?.intents || []
  if (results[7].status === 'fulfilled') budget.value = results[7].value || null
  if (results[8].status === 'fulfilled') roles.value = results[8].value || null
  if (results[9].status === 'fulfilled') {
    control.value = results[9].value || null
    if (!showSettings.value) resetDraft()
  }
  if (results[10].status === 'fulfilled') permissions.value = results[10].value || null
  if (results[11].status === 'fulfilled') models.value = results[11].value?.models || []
  if (results[12].status === 'fulfilled') targets.value = results[12].value || null

  loadError.value = failures.length
    ? failures.map((r) => r.reason?.message || 'request failed').join('; ')
    : ''
  loading.value = false
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const ROLE_KEYS = [
  ['analyst',   'learning_analyst_llm_model_id',   'Narrates what happened. Runs often — a cheap model is right.'],
  ['generator', 'learning_generator_llm_model_id', 'Invents hypotheses. Worth your strongest model.'],
  ['coder',     'learning_coder_llm_model_id',     'Writes patches for confirmed hypotheses.'],
  ['judge',     'learning_judge_llm_model_id',     'Confirms or vetoes. Cannot promote past the statistics.'],
]

const NUMBER_FIELDS = [
  ['daily_budget_usd',      'Daily spend ceiling ($)',   '0 means the loop will not spend at all.'],
  ['monthly_budget_usd',    'Monthly spend ceiling ($)', '0 means the loop will not spend at all.'],
  ['breaker_limit_pct',     'Breaker limit (%)',         'Attributable drawdown that unwinds the whole live tier. 0 means it never fires.'],
  ['approval_timeout_hours','Approval timeout (hours)',  'Sub-live proposals auto-proceed after this. Live rungs ignore it and wait for you.'],
  ['demote_after',          'Demote after N failures',   'Consecutive sub-bar evaluations before a change is reverted.'],
  ['retain_days',           'Keep raw observations (days)', 'Rolled-up aggregates are kept regardless.'],
  ['variance_threshold',    'Variance guard threshold',  'A field where this share of samples take one value raises a defect finding.'],
  ['variance_min_n',        'Variance guard minimum n',  'Below this many samples, saturation is a small sample rather than a constant.'],
]

function resetDraft() {
  const cfg = control.value?.config || {}
  draft.value = {
    mode: cfg.mode || 'observe',
    enabled: cfg.enabled !== false,
    document_allowlist: [...(cfg.document_allowlist || [])],
    permission_matrix: JSON.parse(JSON.stringify(
      permissions.value?.matrix || cfg.permission_matrix || {})),
    ...Object.fromEntries(NUMBER_FIELDS.map(([k]) => [k, cfg[k] ?? 0])),
    watched_instances: [...(cfg.watched_instances || [])],
    ...Object.fromEntries(ROLE_KEYS.map(([, k]) => [k, cfg[k] || ''])),
  }
}

function toggleDocument(id) {
  const list = draft.value.document_allowlist || []
  draft.value.document_allowlist = list.includes(id)
    ? list.filter((d) => d !== id)
    : [...list, id]
}

function toggleInstance(id) {
  const list = draft.value.watched_instances || []
  draft.value.watched_instances = list.includes(id)
    ? list.filter((d) => d !== id)
    : [...list, id]
}

async function postControl(body, label) {
  busy.value = label
  saveError.value = ''
  saveOk.value = ''
  try {
    const res = await fetch(`${API_BASE}/learning/control`, {
      method: 'POST', headers: authHeaders(), body: JSON.stringify(body),
    })
    if (!res.ok) {
      let detail = `Request failed (${res.status})`
      try { detail = (await res.json())?.detail || detail } catch (_e) { /* keep */ }
      throw new Error(detail)
    }
    control.value = await res.json()
    saveOk.value = label === 'settings' ? 'Settings saved.' : 'Engine updated.'
    await load()
  } catch (err) {
    saveError.value = err?.message || 'Could not apply that change'
  } finally {
    busy.value = ''
  }
}

const engineRunning = computed(() => !!control.value?.running)

function toggleEngine() {
  postControl({ running: !engineRunning.value }, 'engine')
}

function saveSettings() {
  postControl({ config: { ...draft.value } }, 'settings')
}

function openSettings() {
  resetDraft()
  showSettings.value = true
}

async function decide(approval, decision) {
  deciding.value = approval.id
  try {
    const res = await fetch(`${API_BASE}/learning/approvals/${approval.id}`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ decision }),
    })
    if (!res.ok) throw new Error(`Request failed (${res.status})`)
    await load()
  } catch (err) {
    loadError.value = err?.message || 'Could not record that decision'
  } finally {
    deciding.value = ''
  }
}

function toggleThread(finding) {
  openThread.value = openThread.value === finding.id ? null : finding.id
}

onMounted(() => {
  load()
  pollTimer = setInterval(load, 30000)
})

onUnmounted(stopPolling)
</script>

<template>
  <AppShell>
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10">

      <!-- Header -->
      <div class="mb-6">
        <div class="flex items-start justify-between gap-3 mb-3">
          <div class="min-w-0">
            <h1 class="text-xl sm:text-2xl font-bold text-slate-100">Learning</h1>
            <p class="text-slate-500 text-xs sm:text-sm mt-1 hidden sm:block">
              What the system decided, what it declined to do, and what that says about it.
            </p>
          </div>
          <span
            class="shrink-0 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border"
            :class="observeOnly
              ? 'text-sky-400 bg-sky-500/10 border-sky-500/20'
              : 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'"
          >
            <span class="size-1.5 rounded-full" :class="observeOnly ? 'bg-sky-400' : 'bg-emerald-400 animate-pulse'"></span>
            {{ modeLabel }}
          </span>
        </div>

        <!-- Controls -->
        <div class="mb-3 flex items-center gap-2 flex-wrap">
          <span
            class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border"
            :class="engineRunning
              ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
              : 'text-slate-500 bg-slate-500/10 border-slate-700'"
          >
            <span class="size-1.5 rounded-full"
                  :class="engineRunning ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'"></span>
            {{ engineRunning ? 'Engine running' : 'Engine stopped' }}
          </span>

          <button
            @click="toggleEngine" :disabled="busy === 'engine'"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors disabled:opacity-50"
            :class="engineRunning
              ? 'border-rose-500/30 bg-rose-500/10 text-rose-400 hover:bg-rose-500/20'
              : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20'"
          >
            <span class="material-symbols-outlined text-[14px]">
              {{ engineRunning ? 'stop' : 'play_arrow' }}
            </span>
            <span>{{ busy === 'engine' ? 'Working…' : (engineRunning ? 'Stop engine' : 'Start engine') }}</span>
          </button>

          <button
            @click="showSettings ? (showSettings = false) : openSettings()"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-700 bg-slate-800/60 text-slate-300 hover:bg-slate-800 transition-colors"
          >
            <span class="material-symbols-outlined text-[14px]">tune</span>
            <span>{{ showSettings ? 'Close settings' : 'Settings' }}</span>
          </button>
        </div>

        <div v-if="saveError" class="mb-3 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {{ saveError }}
        </div>
        <div v-if="saveOk" class="mb-3 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300">
          {{ saveOk }}
        </div>

        <!-- Counters -->
        <div v-if="overview" class="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <div class="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
            <div class="text-[11px] text-slate-500">Open findings</div>
            <div class="text-lg font-bold text-slate-100">{{ overview.open_findings }}</div>
          </div>
          <div class="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
            <div class="text-[11px] text-slate-500">Runs observed</div>
            <div class="text-lg font-bold text-slate-100">{{ overview.runs_observed }}</div>
          </div>
          <div class="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
            <div class="text-[11px] text-slate-500">Decisions</div>
            <div class="text-lg font-bold text-slate-100">{{ overview.decisions_observed }}</div>
          </div>
          <div class="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
            <div class="text-[11px] text-slate-500">Refusals</div>
            <div class="text-lg font-bold text-slate-100">{{ overview.refusals_observed }}</div>
          </div>
        </div>
      </div>

      <!-- Settings -->
      <section v-if="showSettings" class="mb-8 rounded-lg border border-slate-800 bg-slate-900/40 p-4">
        <h2 class="text-sm font-semibold text-slate-300 mb-1">Settings</h2>
        <p class="text-xs text-slate-600 mb-4">
          Nothing is promotable until a target has a measured noise floor, whatever
          these say.
        </p>

        <!-- Mode + enabled -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
          <label class="block">
            <span class="text-[11px] text-slate-500">Mode</span>
            <select v-model="draft.mode"
                    class="mt-1 w-full rounded-lg bg-slate-950/60 border border-slate-700 px-3 py-2 text-sm text-slate-200">
              <option value="observe">observe — record and report only</option>
              <option value="propose">propose — ask, never apply</option>
              <option value="act">act — apply what the permissions allow</option>
            </select>
          </label>
          <label class="flex items-center gap-2 mt-1 sm:mt-6">
            <input type="checkbox" v-model="draft.enabled"
                   class="size-4 rounded border-slate-600 bg-slate-950" />
            <span class="text-sm text-slate-300">Subsystem enabled</span>
          </label>
        </div>

        <!-- Numbers -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
          <label v-for="[key, label, hint] in NUMBER_FIELDS" :key="key" class="block">
            <span class="text-[11px] text-slate-500">{{ label }}</span>
            <input type="number" step="any" min="0" v-model.number="draft[key]"
                   class="mt-1 w-full rounded-lg bg-slate-950/60 border border-slate-700 px-3 py-2 text-sm text-slate-200" />
            <span class="text-[10px] text-slate-600 block mt-0.5">{{ hint }}</span>
          </label>
        </div>

        <!-- AI role models -->
        <h3 class="text-xs font-semibold text-slate-400 mt-5 mb-2">AI role models</h3>
        <p class="text-[11px] text-slate-600 mb-2">
          A role with no model is skipped — it never falls back to a default, so
          the recorded model id is always the model that actually answered.
        </p>
        <div class="space-y-3 mb-4">
          <label v-for="[role, key, hint] in ROLE_KEYS" :key="key" class="block">
            <span class="text-[11px] text-slate-500 capitalize">{{ role }}</span>
            <select v-model="draft[key]"
                    class="mt-1 w-full rounded-lg bg-slate-950/60 border border-slate-700 px-3 py-2 text-sm text-slate-200">
              <option value="">— not configured —</option>
              <option v-for="m in models" :key="m.id" :value="m.id">
                {{ m.name || m.model || m.id }}<span v-if="m.provider"> ({{ m.provider }})</span>
              </option>
            </select>
            <span class="text-[10px] text-slate-600 block mt-0.5">{{ hint }}</span>
          </label>
        </div>

        <!-- Document allowlist -->
        <h3 class="text-xs font-semibold text-slate-400 mt-5 mb-2">
          Strategy documents the subsystem may write to
        </h3>
        <p class="text-[11px] text-slate-600 mb-2">
          Arming a document is what lets the subsystem change it. Empty means it
          writes nowhere, whatever the permission matrix says.
        </p>
        <div v-if="!targets?.strategies?.length" class="text-xs text-slate-600 mb-4">
          No strategy documents found.
        </div>
        <div v-else class="space-y-1.5 mb-4">
          <button v-for="doc in targets.strategies" :key="doc.id"
                  @click="toggleDocument(doc.id)"
                  class="w-full text-left rounded-lg border px-3 py-2 transition-colors"
                  :class="draft.document_allowlist?.includes(doc.id)
                    ? (doc.is_live
                        ? 'border-rose-500/40 bg-rose-500/10'
                        : 'border-emerald-500/30 bg-emerald-500/10')
                    : 'border-slate-800 bg-slate-950/40 hover:bg-slate-900/60'">
            <div class="flex items-center gap-2">
              <span class="material-symbols-outlined text-[16px]"
                    :class="draft.document_allowlist?.includes(doc.id)
                      ? (doc.is_live ? 'text-rose-400' : 'text-emerald-400')
                      : 'text-slate-600'">
                {{ draft.document_allowlist?.includes(doc.id) ? 'check_box' : 'check_box_outline_blank' }}
              </span>
              <span class="text-sm text-slate-200 truncate">{{ doc.name }}</span>
              <span class="text-[10px] text-slate-600 font-mono">#{{ doc.id }}</span>
              <span v-if="doc.is_live"
                    class="ml-auto px-1.5 py-0.5 rounded text-[10px] font-bold border text-rose-400 bg-rose-500/10 border-rose-500/30">
                REAL MONEY
              </span>
            </div>
            <div class="text-[10px] text-slate-600 mt-0.5 pl-6">
              {{ doc.sub_strategies }} sub-strateg{{ doc.sub_strategies === 1 ? 'y' : 'ies' }}
              <span v-if="doc.instance_names?.length">
                · used by {{ doc.instance_names.join(', ') }}
              </span>
              <span v-else> · not attached to an instance</span>
            </div>
          </button>
        </div>

        <!-- Watched instances -->
        <h3 class="text-xs font-semibold text-slate-400 mt-5 mb-2">
          Instances to watch
        </h3>
        <p class="text-[11px] text-slate-600 mb-2">
          Which instances' runs the engine observes. Selecting none watches
          everything — observing is read-only, so narrowing is the deliberate act.
        </p>
        <div v-if="!draft.watched_instances?.length"
             class="text-[11px] text-sky-400 mb-2">
          Watching every instance.
        </div>
        <div v-if="!targets?.instances?.length" class="text-xs text-slate-600 mb-4">
          No instances found.
        </div>
        <div v-else class="space-y-1.5 mb-4">
          <button v-for="inst in targets.instances" :key="inst.id"
                  @click="toggleInstance(inst.id)"
                  class="w-full text-left rounded-lg border px-3 py-2 transition-colors"
                  :class="draft.watched_instances?.includes(inst.id)
                    ? 'border-sky-500/30 bg-sky-500/10'
                    : 'border-slate-800 bg-slate-950/40 hover:bg-slate-900/60'">
            <div class="flex items-center gap-2">
              <span class="material-symbols-outlined text-[16px]"
                    :class="draft.watched_instances?.includes(inst.id) ? 'text-sky-400' : 'text-slate-600'">
                {{ draft.watched_instances?.includes(inst.id) ? 'check_box' : 'check_box_outline_blank' }}
              </span>
              <span class="text-sm text-slate-200 truncate">{{ inst.name }}</span>
              <span class="text-[10px] text-slate-600 font-mono">{{ inst.kind }}</span>
              <span v-if="inst.is_live"
                    class="ml-auto px-1.5 py-0.5 rounded text-[10px] font-bold border text-rose-400 bg-rose-500/10 border-rose-500/30">
                LIVE
              </span>
              <span v-else-if="inst.running"
                    class="ml-auto px-1.5 py-0.5 rounded text-[10px] font-semibold border text-emerald-400 bg-emerald-500/10 border-emerald-500/20">
                running
              </span>
            </div>
            <div class="text-[10px] text-slate-600 mt-0.5 pl-6">
              doc #{{ inst.strategy_id || '—' }}
            </div>
          </button>
        </div>

        <!-- Permission matrix -->
        <h3 class="text-xs font-semibold text-slate-400 mt-5 mb-2">Permissions</h3>
        <p class="text-[11px] text-slate-600 mb-2">
          What may be applied without asking. Reverting is never gated by any of
          these — a rollback that waits for approval is one that does not happen.
        </p>
        <div class="overflow-x-auto rounded-lg border border-slate-800">
          <table class="w-full text-xs">
            <thead>
              <tr class="text-slate-500 border-b border-slate-800">
                <th class="text-left font-medium px-2 py-2">Action</th>
                <th v-for="rung in (permissions?.rungs || [])" :key="rung"
                    class="text-left font-medium px-2 py-2 whitespace-nowrap">{{ rung }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="cls in (permissions?.action_classes || [])" :key="cls"
                  class="border-b border-slate-800/60 last:border-0">
                <td class="px-2 py-2 text-slate-300 whitespace-nowrap">{{ cls }}</td>
                <td v-for="rung in (permissions?.rungs || [])" :key="rung" class="px-2 py-1.5">
                  <select v-if="draft.permission_matrix?.[cls]"
                          v-model="draft.permission_matrix[cls][rung]"
                          class="w-full rounded bg-slate-950/60 border border-slate-700 px-1.5 py-1 text-[11px] text-slate-200">
                    <option value="autonomous">auto</option>
                    <option value="ask">ask</option>
                    <option value="blocked">blocked</option>
                  </select>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="flex gap-2 mt-5">
          <button @click="saveSettings" :disabled="busy === 'settings'"
                  class="px-4 py-2 rounded-lg text-xs font-semibold border border-emerald-500/30 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 disabled:opacity-50">
            {{ busy === 'settings' ? 'Saving…' : 'Save settings' }}
          </button>
          <button @click="resetDraft"
                  class="px-4 py-2 rounded-lg text-xs font-semibold border border-slate-700 bg-slate-800/60 text-slate-300 hover:bg-slate-800">
            Revert
          </button>
        </div>
      </section>

      <div v-if="budget || roles" class="mb-6 grid grid-cols-1 sm:grid-cols-2 gap-2">
        <div v-if="budget" class="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
          <div class="text-[11px] text-slate-500">Budget remaining</div>
          <div class="text-lg font-bold"
               :class="Number(budget.remaining_usd) > 0 ? 'text-slate-100' : 'text-amber-400'">
            ${{ Number(budget.remaining_usd || 0).toFixed(2) }}
          </div>
          <div class="text-[11px] text-slate-600">
            ${{ Number(budget.spent_today_usd || 0).toFixed(2) }} today ·
            ${{ Number(budget.reserved_usd || 0).toFixed(2) }} reserved
            <span v-if="!Number(budget.daily_limit_usd)"> · no ceiling set, so nothing will spend</span>
          </div>
        </div>
        <div v-if="roles" class="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
          <div class="text-[11px] text-slate-500">AI roles configured</div>
          <div class="text-lg font-bold text-slate-100">
            {{ Object.keys(roles.roles || {}).length - (roles.unconfigured || []).length }}
            / {{ Object.keys(roles.roles || {}).length }}
          </div>
          <div class="text-[11px] text-slate-600">
            <span v-if="(roles.unconfigured || []).length">
              unconfigured: {{ (roles.unconfigured || []).join(', ') }}
            </span>
            <span v-else>every role has a model</span>
          </div>
        </div>
      </div>

      <div v-if="loadError" class="mb-6 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
        {{ loadError }}
      </div>
      <div v-if="loading" class="text-slate-500 text-sm">Loading…</div>

      <!-- 1. Pending approvals -->
      <section class="mb-8">
        <div class="flex items-center gap-2 mb-2">
          <h2 class="text-sm font-semibold text-slate-300">Pending approvals</h2>
          <span v-if="approvals.live_pending_count"
                class="px-2 py-0.5 rounded-full text-[10px] font-bold border text-rose-400 bg-rose-500/10 border-rose-500/20">
            {{ approvals.live_pending_count }} LIVE — waits indefinitely
          </span>
        </div>

        <div v-if="!approvals.pending?.length"
             class="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-6 text-center">
          <p class="text-sm text-slate-400">No approvals waiting.</p>
          <p class="text-xs text-slate-600 mt-1">
            {{ observeOnly
                ? 'The subsystem is in observe mode — it records and reports, and does not propose changes.'
                : 'Nothing is waiting on you right now.' }}
          </p>
        </div>

        <div class="space-y-2">
          <div v-for="a in approvals.pending" :key="a.id"
               class="rounded-lg border px-4 py-3"
               :class="a.holds_forever
                 ? 'border-rose-500/30 bg-rose-500/5'
                 : 'border-slate-800 bg-slate-900/40'">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="px-2 py-0.5 rounded-full text-[10px] font-bold border"
                        :class="a.holds_forever
                          ? 'text-rose-400 bg-rose-500/10 border-rose-500/20'
                          : 'text-sky-400 bg-sky-500/10 border-sky-500/20'">{{ a.rung }}</span>
                  <span class="text-[11px] text-slate-500 font-mono">{{ a.action_class }}</span>
                  <span class="text-[11px] text-slate-500 font-mono">doc {{ a.document_id || '—' }}</span>
                </div>
                <p class="text-sm text-slate-200 mt-1">{{ a.summary }}</p>
                <p class="text-[11px] text-slate-600 mt-1">
                  {{ a.target }} · requested {{ fmtWhen(a.requested_at) }}
                  <span v-if="a.holds_forever"> · this one waits until you answer</span>
                </p>
              </div>
              <div class="flex gap-2 shrink-0">
                <button @click="decide(a, 'approved')" :disabled="deciding === a.id"
                        class="px-3 py-1.5 rounded-lg text-xs font-semibold border border-emerald-500/30 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 disabled:opacity-50">
                  Approve
                </button>
                <button @click="decide(a, 'rejected')" :disabled="deciding === a.id"
                        class="px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-700 bg-slate-800/60 text-slate-300 hover:bg-slate-800 disabled:opacity-50">
                  Reject
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- Noise floors: which targets can be trusted at all -->
      <section class="mb-8">
        <h2 class="text-sm font-semibold text-slate-300 mb-2">Measured noise floors</h2>
        <p class="text-xs text-slate-600 mb-2">
          A target with no measured floor cannot promote anything — two runs of one
          window have differed by ~16pp here, so an unmeasured target's results are
          not attributable.
        </p>
        <div v-if="!floors.length"
             class="rounded-lg border border-amber-500/20 bg-amber-500/5 px-4 py-4">
          <p class="text-sm text-amber-300">No floor has been measured yet.</p>
          <p class="text-xs text-slate-500 mt-1">Nothing is promotable until one is.</p>
        </div>
        <div v-else class="rounded-lg border border-slate-800 bg-slate-900/40 overflow-x-auto">
          <table class="w-full text-xs">
            <thead><tr class="text-slate-500 border-b border-slate-800">
              <th class="text-left font-medium px-3 py-2">Target</th>
              <th class="text-left font-medium px-3 py-2">Window class</th>
              <th class="text-right font-medium px-3 py-2">Floor</th>
              <th class="text-right font-medium px-3 py-2">Repeats</th>
              <th class="text-left font-medium px-3 py-2">Usable</th>
            </tr></thead>
            <tbody>
              <tr v-for="f in floors" :key="f.id" class="border-b border-slate-800/60 last:border-0">
                <td class="px-3 py-2 text-slate-300">{{ f.target }}</td>
                <td class="px-3 py-2 text-slate-500 font-mono">{{ f.window_class }}</td>
                <td class="px-3 py-2 text-right text-slate-300">{{ Number(f.floor_pp).toFixed(2) }}pp</td>
                <td class="px-3 py-2 text-right text-slate-300">{{ f.n }}</td>
                <td class="px-3 py-2" :class="f.measured ? 'text-emerald-400' : 'text-amber-400'">
                  {{ f.measured ? 'yes' : (f.reason || 'not measured') }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- 2. Findings & reports -->
      <section class="mb-8">
        <h2 class="text-sm font-semibold text-slate-300 mb-2">Findings &amp; reports</h2>

        <div v-if="!loading && !findings.length && !loadError"
             class="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-6 text-center">
          <p class="text-sm text-slate-400">Nothing raised yet.</p>
          <p class="text-xs text-slate-600 mt-1">
            {{ overview && !overview.engine_running
                ? 'The self-learning engine is stopped — start it to begin observing.'
                : 'Findings appear as completed runs are observed.' }}
          </p>
        </div>

        <div class="space-y-2">
          <div v-for="f in findings" :key="f.id"
               class="rounded-lg border border-slate-800 bg-slate-900/40 overflow-hidden">
            <button class="w-full text-left px-4 py-3 hover:bg-slate-900/70 transition-colors"
                    @click="toggleThread(f)">
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold border"
                          :class="severityClasses(f.severity)">{{ (f.severity || '').toUpperCase() }}</span>
                    <span class="text-[11px] text-slate-500 font-mono">{{ f.target }}</span>
                  </div>
                  <h3 class="text-sm font-semibold text-slate-100 mt-1">{{ f.title }}</h3>
                  <p class="text-xs text-slate-400 mt-1">{{ f.detail }}</p>
                </div>
                <span class="material-symbols-outlined text-slate-600 text-[18px] shrink-0">
                  {{ openThread === f.id ? 'expand_less' : 'expand_more' }}
                </span>
              </div>
            </button>

            <!-- 3. Thread detail — the ladder stepper -->
            <div v-if="openThread === f.id" class="border-t border-slate-800 px-4 py-4">
              <div class="text-[11px] text-slate-500 mb-3">
                Detected {{ fmtWhen(f.detected_at) }} · run {{ f.run_id || '—' }}
              </div>

              <ol class="relative border-l border-slate-800 ml-2">
                <li class="ml-4 pb-4">
                  <span class="absolute -left-[5px] mt-1 size-2.5 rounded-full bg-rose-400"></span>
                  <div class="text-xs font-semibold text-slate-200">Detected</div>
                  <div class="text-[11px] text-slate-500">{{ f.kind }} · {{ f.target }}</div>
                  <pre class="mt-2 text-[10px] text-slate-400 bg-slate-950/60 rounded p-2 overflow-x-auto">{{ JSON.stringify(f.evidence, null, 2) }}</pre>
                </li>
                <li v-for="rung in LADDER" :key="rung.key" class="ml-4 pb-4">
                  <span class="absolute -left-[5px] mt-1 size-2.5 rounded-full bg-slate-700"></span>
                  <div class="text-xs font-semibold text-slate-500">{{ rung.label }}</div>
                  <div class="text-[11px] text-slate-600">{{ rung.hint }}</div>
                  <div class="text-[10px] text-slate-700 mt-0.5">not reached — the subsystem observes only</div>
                </li>
              </ol>
            </div>
          </div>
        </div>
      </section>

      <!-- Hypothesis ledger -->
      <section class="mb-8">
        <h2 class="text-sm font-semibold text-slate-300 mb-2">Hypothesis ledger</h2>
        <p class="text-xs text-slate-600 mb-2">
          Rejections are kept on purpose — they are what stops the generator
          re-proposing an idea that has already been disproved.
        </p>
        <div v-if="!hypotheses.length"
             class="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-6 text-center">
          <p class="text-sm text-slate-400">No hypotheses yet.</p>
        </div>
        <div v-else class="space-y-2">
          <div v-for="h in hypotheses" :key="h.id"
               class="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="px-2 py-0.5 rounded-full text-[10px] font-bold border"
                    :class="h.status === 'confirmed'
                      ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
                      : h.status === 'proposed'
                        ? 'text-sky-400 bg-sky-500/10 border-sky-500/20'
                        : 'text-slate-400 bg-slate-500/10 border-slate-700'">
                {{ (h.status || '').toUpperCase() }}
              </span>
              <span class="text-[11px] text-slate-500 font-mono">{{ h.target }}</span>
              <span class="text-[11px] text-slate-600">
                predicts {{ h.predicted_direction }} {{ h.predicted_min_pp }}–{{ h.predicted_max_pp }}pp
              </span>
            </div>
            <p class="text-sm text-slate-200 mt-1">{{ h.claim }}</p>
            <p class="text-xs text-slate-500 mt-1">{{ h.mechanism }}</p>
            <p v-if="h.status_reason" class="text-[11px] text-slate-600 mt-1">
              {{ h.status_reason }}
            </p>
          </div>
        </div>
      </section>

      <!-- What the loop decided, and why -->
      <section class="mb-8">
        <h2 class="text-sm font-semibold text-slate-300 mb-2">Loop decisions</h2>
        <p class="text-xs text-slate-600 mb-2">
          Every turn's intents, blocked ones included — a decision that leaves no
          trace is as unprovable as a lever that never announced itself.
        </p>
        <div v-if="!intents.length"
             class="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-6 text-center">
          <p class="text-sm text-slate-400">The loop has not run a turn yet.</p>
        </div>
        <div v-else class="rounded-lg border border-slate-800 bg-slate-900/40 divide-y divide-slate-800/60">
          <div v-for="(i, idx) in intents" :key="idx" class="px-4 py-2">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="px-2 py-0.5 rounded text-[10px] font-mono border border-slate-700 text-slate-300">
                {{ i.kind }}
              </span>
              <span v-if="i.target" class="text-[11px] text-slate-500">{{ i.target }}</span>
              <span v-if="i.rung" class="text-[11px] text-slate-500">{{ i.rung }}</span>
              <span class="text-[11px] text-slate-700 ml-auto">{{ fmtWhen(i.at) }}</span>
            </div>
            <p class="text-xs text-slate-400 mt-1">{{ i.reason }}</p>
          </div>
        </div>
      </section>

      <!-- Observed runs -->
      <section>
        <h2 class="text-sm font-semibold text-slate-300 mb-2">Observed runs</h2>
        <div v-if="!funnels.length"
             class="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-6 text-center">
          <p class="text-sm text-slate-400">
            {{ loading ? 'Loading…' : loadError ? 'Could not load observed runs.' : 'No runs observed yet.' }}
          </p>
        </div>
        <div v-else class="rounded-lg border border-slate-800 bg-slate-900/40 overflow-x-auto">
          <table class="w-full text-xs">
            <thead>
              <tr class="text-slate-500 border-b border-slate-800">
                <th class="text-left font-medium px-3 py-2">Run</th>
                <th class="text-left font-medium px-3 py-2">Target</th>
                <th class="text-right font-medium px-3 py-2">Decided</th>
                <th class="text-right font-medium px-3 py-2">Executed</th>
                <th class="text-right font-medium px-3 py-2">Refused</th>
                <th class="text-right font-medium px-3 py-2">Buy conv.</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in funnelRows" :key="row.id" class="border-b border-slate-800/60 last:border-0">
                <td class="px-3 py-2 font-mono text-slate-300">{{ row.run_id }}</td>
                <td class="px-3 py-2 text-slate-400">{{ row.target }}</td>
                <td class="px-3 py-2 text-right text-slate-300">{{ row.decided }}</td>
                <td class="px-3 py-2 text-right text-slate-300">{{ row.executed }}</td>
                <td class="px-3 py-2 text-right text-slate-300">{{ row.refused }}</td>
                <td class="px-3 py-2 text-right"
                    :class="row.pct !== null && row.pct < 25 ? 'text-rose-400' : 'text-slate-300'">
                  {{ row.pct === null ? '—' : row.pct.toFixed(1) + '%' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

    </main>
  </AppShell>
</template>
