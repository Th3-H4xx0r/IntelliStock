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
const llmUsage   = ref(null)
const activity   = ref({ activity: [], busy: false })
const engineStatus = ref(null)

// Pagination. Every one of these lists grows without bound — 109 observed runs
// on day one — and an unpaged page is unusable on a phone.
const PAGE = 8
const page = ref({ findings: 1, funnels: 1, hypotheses: 1, intents: 1, approvals: 1 })

function paged(rows, key) {
  const list = rows || []
  const start = (page.value[key] - 1) * PAGE
  return list.slice(start, start + PAGE)
}
function pageCount(rows) {
  return Math.max(1, Math.ceil((rows || []).length / PAGE))
}
function turnPage(key, delta, rows) {
  const next = page.value[key] + delta
  if (next >= 1 && next <= pageCount(rows)) page.value[key] = next
}

/// Is a model call in flight for this finding right now?
function busyFor(findingId) {
  return (activity.value?.activity || []).some(
    (a) => a.active && a.finding_id === findingId)
}
function busyStep(findingId, step) {
  return (activity.value?.activity || []).some(
    (a) => a.active && a.finding_id === findingId && a.step === step)
}
function busyRole(findingId) {
  const hit = (activity.value?.activity || []).find(
    (a) => a.active && a.finding_id === findingId)
  return hit?.role || ''
}
const loading    = ref(true)
const loadError  = ref('')
const openThread = ref(null)      // the finding whose ladder stepper is expanded
let pollTimer = null
let activityTimer = null

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

// Severity as a dot beside the word, not a coloured pill.
function severityClasses(severity) {
  if (severity === 'high') return 'ui-dot-error'
  if (severity === 'medium') return 'ui-dot-warn'
  return 'ui-dot-off'
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
    getJson('/learning/llm-usage'),
    getJson('/learning/activity'),
    getJson('/learning/engine-status'),
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
  if (results[13].status === 'fulfilled') llmUsage.value = results[13].value || null
  if (results[14].status === 'fulfilled') activity.value = results[14].value || activity.value
  if (results[15].status === 'fulfilled') engineStatus.value = results[15].value || null

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
  if (activityTimer) {
    clearInterval(activityTimer)
    activityTimer = null
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

async function pollActivity() {
  try {
    activity.value = await getJson('/learning/activity')
  } catch (_e) { /* the main poll reports errors */ }
}

onMounted(() => {
  load()
  pollTimer = setInterval(load, 30000)
  // A spinner that updates every 30s is not a spinner.
  activityTimer = setInterval(pollActivity, 3000)
})

onUnmounted(stopPolling)
</script>

<template>
  <AppShell>
    <main class="ui-page">

      <!-- Header -->
      <header class="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <div class="min-w-0">
          <h1 class="ui-page-title">Learning</h1>
          <p class="ui-page-sub hidden sm:block">
            What the system decided, what it declined to do, and what that says about it
          </p>
          <p class="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500 tabular-nums">
            <span class="inline-flex items-center gap-1.5" :class="engineRunning ? 'text-slate-200' : 'text-slate-400'">
              <span class="ui-dot" :class="engineRunning ? 'ui-dot-live' : 'ui-dot-off'"></span>
              {{ engineRunning ? 'Engine running' : 'Engine stopped' }}
            </span>
            <span class="text-slate-600" aria-hidden="true">·</span>
            <span :class="observeOnly ? 'text-slate-400' : 'text-slate-200'">{{ modeLabel }}</span>
            <template v-if="engineStatus && engineStatus.alive">
              <span class="text-slate-600" aria-hidden="true">·</span>
              <span>
                Last turn {{ engineStatus.seconds_since_last_turn }}s ago ·
                engine build {{ engineStatus.source_fingerprint }}
              </span>
            </template>
          </p>
        </div>

        <!-- Controls -->
        <div class="flex items-center gap-2 flex-wrap">
          <button
            @click="toggleEngine" :disabled="busy === 'engine'"
            class="ui-btn"
            :class="engineRunning ? 'text-loss' : ''"
          >
            {{ busy === 'engine' ? 'Working…' : (engineRunning ? 'Stop engine' : 'Start engine') }}
          </button>

          <button
            @click="showSettings ? (showSettings = false) : openSettings()"
            class="ui-btn"
            :aria-expanded="showSettings"
          >
            <span class="material-symbols-outlined text-[16px] text-slate-400">tune</span>
            {{ showSettings ? 'Close settings' : 'Settings' }}
          </button>
        </div>
      </header>

      <div v-if="engineStatus && engineStatus.flagged_running && !engineStatus.alive"
           class="mt-6 ui-card px-5 py-4">
        <p class="text-sm text-slate-200 inline-flex items-center gap-2">
          <span class="ui-dot ui-dot-warn"></span>
          Engine is flagged running but has not taken a turn
          <span v-if="engineStatus.seconds_since_last_turn !== null" class="tabular-nums">
            in {{ Math.round(engineStatus.seconds_since_last_turn / 60) }} min
          </span>
        </p>
        <p class="text-sm text-slate-500 mt-1 pl-3.5">
          {{ engineStatus.diagnosis }}
        </p>
      </div>

      <div v-if="saveError" class="mt-6 ui-error">
        {{ saveError }}
      </div>
      <p v-if="saveOk" class="mt-6 text-sm text-gain">
        {{ saveOk }}
      </p>

      <!-- Counters -->
      <div v-if="overview" class="mt-8 ui-card px-5 py-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div>
          <p class="text-xs text-slate-500">Open findings</p>
          <p class="mt-1 text-xl font-medium text-slate-100 tabular-nums">{{ overview.open_findings }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">Runs observed</p>
          <p class="mt-1 text-xl font-medium text-slate-100 tabular-nums">{{ overview.runs_observed }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">Decisions</p>
          <p class="mt-1 text-xl font-medium text-slate-100 tabular-nums">{{ overview.decisions_observed }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">Refusals</p>
          <p class="mt-1 text-xl font-medium text-slate-100 tabular-nums">{{ overview.refusals_observed }}</p>
        </div>
      </div>

      <!-- Settings -->
      <section v-if="showSettings" class="mt-12">
        <div class="mb-3">
          <h2 class="ui-section-title">Settings</h2>
          <p class="ui-section-sub">
            Nothing is promotable until a target has a measured noise floor, whatever
            these say
          </p>
        </div>

        <div class="ui-card p-5">
          <!-- Mode + enabled -->
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <label class="block">
              <span class="ui-field-label">Mode</span>
              <select v-model="draft.mode" class="ui-input">
                <option value="observe">Observe — record and report only</option>
                <option value="propose">Propose — ask, never apply</option>
                <option value="act">Act — apply what the permissions allow</option>
              </select>
            </label>
            <label class="flex items-center gap-2 sm:mt-7">
              <input type="checkbox" v-model="draft.enabled"
                     class="size-4 rounded accent-slate-200" />
              <span class="text-sm text-slate-300">Subsystem enabled</span>
            </label>
          </div>

          <!-- Numbers -->
          <div class="mt-6 grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-5">
            <label v-for="[key, label, hint] in NUMBER_FIELDS" :key="key" class="block">
              <span class="ui-field-label">{{ label }}</span>
              <input type="number" step="any" min="0" v-model.number="draft[key]"
                     class="ui-input tabular-nums" />
              <span class="ui-help block">{{ hint }}</span>
            </label>
          </div>

          <!-- AI role models -->
          <div class="mt-8 pt-6 border-t border-white/[0.06]">
            <h3 class="text-sm font-medium text-slate-200">AI role models</h3>
            <p class="text-xs text-slate-500 mt-1">
              A role with no model is skipped — it never falls back to a default, so
              the recorded model id is always the model that actually answered.
            </p>
            <div class="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-5">
              <label v-for="[role, key, hint] in ROLE_KEYS" :key="key" class="block">
                <span class="ui-field-label capitalize">{{ role }}</span>
                <select v-model="draft[key]" class="ui-input">
                  <option value="">Not configured</option>
                  <option v-for="m in models" :key="m.id" :value="m.id">
                    {{ m.name || m.model || m.id }}<span v-if="m.provider"> ({{ m.provider }})</span>
                  </option>
                </select>
                <span class="ui-help block">{{ hint }}</span>
              </label>
            </div>
          </div>

          <!-- Document allowlist -->
          <div class="mt-8 pt-6 border-t border-white/[0.06]">
            <h3 class="text-sm font-medium text-slate-200">
              Strategy documents the subsystem may write to
            </h3>
            <p class="text-xs text-slate-500 mt-1">
              Arming a document is what lets the subsystem change it. Empty means it
              writes nowhere, whatever the permission matrix says.
            </p>
            <div v-if="!targets?.strategies?.length" class="mt-3 text-sm text-slate-500">
              No strategy documents found.
            </div>
            <div v-else class="mt-3 rounded-lg border border-white/[0.07] divide-y divide-white/[0.05] overflow-hidden">
              <button v-for="doc in targets.strategies" :key="doc.id"
                      @click="toggleDocument(doc.id)"
                      class="w-full text-left px-3 py-2.5 transition-colors duration-150 ease-out"
                      :class="draft.document_allowlist?.includes(doc.id)
                        ? 'bg-raised'
                        : 'hover:bg-white/[0.03]'"
                      :aria-pressed="!!draft.document_allowlist?.includes(doc.id)">
                <div class="flex items-center gap-2">
                  <span class="material-symbols-outlined text-[16px]"
                        :class="draft.document_allowlist?.includes(doc.id) ? 'text-slate-200' : 'text-slate-600'">
                    {{ draft.document_allowlist?.includes(doc.id) ? 'check_box' : 'check_box_outline_blank' }}
                  </span>
                  <span class="text-sm text-slate-200 truncate">{{ doc.name }}</span>
                  <span class="text-xs text-slate-600 tabular-nums">#{{ doc.id }}</span>
                  <span v-if="doc.money === 'live'"
                        class="ml-auto shrink-0 inline-flex items-center gap-1.5 text-xs text-slate-200">
                    <span class="ui-dot ui-dot-warn"></span>
                    Real money
                  </span>
                  <span v-else-if="doc.money === 'unknown'"
                        class="ml-auto shrink-0 text-xs text-slate-400">
                    Unverified
                  </span>
                  <span v-else-if="doc.money === 'paper'"
                        class="ml-auto shrink-0 text-xs text-slate-500">
                    Paper
                  </span>
                </div>
                <div class="text-xs text-slate-500 mt-0.5 pl-6">
                  {{ doc.sub_strategies }} sub-strateg{{ doc.sub_strategies === 1 ? 'y' : 'ies' }}
                  <span v-if="doc.instance_names?.length">
                    · used by {{ doc.instance_names.join(', ') }}
                  </span>
                  <span v-else> · not attached to an instance</span>
                </div>
              </button>
            </div>
          </div>

          <!-- Watched instances -->
          <div class="mt-8 pt-6 border-t border-white/[0.06]">
            <h3 class="text-sm font-medium text-slate-200">
              Instances to watch
            </h3>
            <p class="text-xs text-slate-500 mt-1">
              Which instances' runs the engine observes. Selecting none watches
              everything — observing is read-only, so narrowing is the deliberate act.
            </p>
            <div v-if="!draft.watched_instances?.length"
                 class="mt-2 text-xs text-slate-400">
              Watching every instance
            </div>
            <div v-if="!targets?.instances?.length" class="mt-3 text-sm text-slate-500">
              No instances found.
            </div>
            <div v-else class="mt-3 rounded-lg border border-white/[0.07] divide-y divide-white/[0.05] overflow-hidden">
              <button v-for="inst in targets.instances" :key="inst.id"
                      @click="toggleInstance(inst.id)"
                      class="w-full text-left px-3 py-2.5 transition-colors duration-150 ease-out"
                      :class="draft.watched_instances?.includes(inst.id)
                        ? 'bg-raised'
                        : 'hover:bg-white/[0.03]'"
                      :aria-pressed="!!draft.watched_instances?.includes(inst.id)">
                <div class="flex items-center gap-2">
                  <span class="material-symbols-outlined text-[16px]"
                        :class="draft.watched_instances?.includes(inst.id) ? 'text-slate-200' : 'text-slate-600'">
                    {{ draft.watched_instances?.includes(inst.id) ? 'check_box' : 'check_box_outline_blank' }}
                  </span>
                  <span class="text-sm text-slate-200 truncate">{{ inst.name }}</span>
                  <span class="text-xs text-slate-600">{{ inst.kind }}</span>
                  <span v-if="inst.money === 'live'"
                        class="ml-auto shrink-0 inline-flex items-center gap-1.5 text-xs text-slate-200">
                    <span class="ui-dot ui-dot-warn"></span>
                    Real money
                  </span>
                  <span v-else-if="inst.money === 'unknown'"
                        class="ml-auto shrink-0 text-xs text-slate-400">
                    Unverified
                  </span>
                  <span v-else-if="inst.money === 'paper'"
                        class="ml-auto shrink-0 text-xs text-slate-500">
                    Paper
                  </span>
                  <span v-else-if="inst.running"
                        class="ml-auto shrink-0 inline-flex items-center gap-1.5 text-xs text-slate-300">
                    <span class="ui-dot ui-dot-live"></span>
                    Running
                  </span>
                </div>
                <div class="text-xs text-slate-500 mt-0.5 pl-6 tabular-nums">
                  doc #{{ inst.strategy_id || '—' }}
                </div>
              </button>
            </div>
          </div>

          <!-- Permission matrix -->
          <div class="mt-8 pt-6 border-t border-white/[0.06]">
            <h3 class="text-sm font-medium text-slate-200">Permissions</h3>
            <p class="text-xs text-slate-500 mt-1">
              What may be applied without asking. Reverting is never gated by any of
              these — a rollback that waits for approval is one that does not happen.
            </p>
            <div class="mt-3 overflow-x-auto rounded-lg border border-white/[0.07]">
              <table class="w-full text-sm">
                <thead>
                  <tr class="h-10 text-xs text-slate-500 border-b border-white/[0.06]">
                    <th class="text-left font-normal px-3">Action</th>
                    <th v-for="rung in (permissions?.rungs || [])" :key="rung"
                        class="text-left font-normal px-3 whitespace-nowrap">{{ rung }}</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/[0.05]">
                  <tr v-for="cls in (permissions?.action_classes || [])" :key="cls">
                    <td class="px-3 py-2 text-slate-300 whitespace-nowrap">{{ cls }}</td>
                    <td v-for="rung in (permissions?.rungs || [])" :key="rung" class="px-3 py-2">
                      <select v-if="draft.permission_matrix?.[cls]"
                              v-model="draft.permission_matrix[cls][rung]"
                              class="ui-input h-8 min-w-[88px] px-2 text-xs">
                        <option value="autonomous">auto</option>
                        <option value="ask">ask</option>
                        <option value="blocked">blocked</option>
                      </select>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div class="flex gap-2 mt-6 pt-5 border-t border-white/[0.06]">
            <button @click="saveSettings" :disabled="busy === 'settings'"
                    class="ui-btn-primary">
              {{ busy === 'settings' ? 'Saving…' : 'Save settings' }}
            </button>
            <button @click="resetDraft"
                    class="ui-btn-ghost h-9">
              Revert
            </button>
          </div>
        </div>
      </section>

      <div v-if="budget || roles" class="mt-8 ui-card grid grid-cols-1 sm:grid-cols-2 divide-y sm:divide-y-0 sm:divide-x divide-white/[0.06]">
        <div v-if="budget" class="px-5 py-4">
          <p class="text-xs text-slate-500">Budget remaining</p>
          <p class="mt-1 text-xl font-medium text-slate-100 tabular-nums inline-flex items-center gap-2">
            ${{ Number(budget.remaining_usd || 0).toFixed(2) }}
            <span v-if="!(Number(budget.remaining_usd) > 0)" class="ui-dot ui-dot-warn" title="No budget left"></span>
          </p>
          <p class="mt-1 text-xs text-slate-500 tabular-nums">
            ${{ Number(budget.spent_today_usd || 0).toFixed(2) }} today ·
            ${{ Number(budget.reserved_usd || 0).toFixed(2) }} reserved
            <span v-if="!Number(budget.daily_limit_usd)"> · no ceiling set, so nothing will spend</span>
          </p>
        </div>
        <div v-if="roles" class="px-5 py-4">
          <p class="text-xs text-slate-500">AI roles configured</p>
          <p class="mt-1 text-xl font-medium text-slate-100 tabular-nums">
            {{ Object.keys(roles.roles || {}).length - (roles.unconfigured || []).length }}<span class="text-slate-500">
            / {{ Object.keys(roles.roles || {}).length }}</span>
          </p>
          <p class="mt-1 text-xs text-slate-500">
            <span v-if="(roles.unconfigured || []).length">
              Unconfigured: {{ (roles.unconfigured || []).join(', ') }}
            </span>
            <span v-else>Every role has a model</span>
          </p>
        </div>
      </div>

      <div v-if="loadError" class="mt-8 ui-error">
        {{ loadError }}
      </div>
      <div v-if="loading" class="mt-12 space-y-3" aria-hidden="true">
        <div class="ui-skel h-4 w-36"></div>
        <div class="h-28 rounded-xl bg-surface"></div>
      </div>

      <!-- 1. Pending approvals -->
      <section class="mt-12">
        <div class="mb-3 flex flex-wrap items-end justify-between gap-x-4 gap-y-1">
          <h2 class="ui-section-title">Pending approvals</h2>
          <span v-if="approvals.live_pending_count"
                class="inline-flex items-center gap-1.5 text-xs text-slate-300 tabular-nums">
            <span class="ui-dot ui-dot-warn"></span>
            {{ approvals.live_pending_count }} live — waits indefinitely
          </span>
        </div>

        <div v-if="!approvals.pending?.length" class="ui-card">
          <div class="ui-empty gap-1">
            <p class="text-slate-300">No approvals waiting</p>
            <p class="text-xs text-slate-500 max-w-md">
              {{ observeOnly
                  ? 'The subsystem is in observe mode — it records and reports, and does not propose changes.'
                  : 'Nothing is waiting on you right now.' }}
            </p>
          </div>
        </div>

        <div v-else class="ui-card overflow-hidden ui-rows">
          <div v-for="a in paged(approvals.pending, 'approvals')" :key="a.id"
               class="px-5 py-4">
            <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div class="min-w-0">
                <div class="flex items-center gap-x-3 gap-y-1 flex-wrap text-xs">
                  <span class="inline-flex items-center gap-1.5 text-slate-300">
                    <span v-if="a.holds_forever" class="ui-dot ui-dot-warn"></span>
                    {{ a.rung }}
                  </span>
                  <span class="text-slate-500">{{ a.action_class }}</span>
                  <span class="text-slate-500 tabular-nums">doc {{ a.document_id || '—' }}</span>
                </div>
                <p class="text-sm text-slate-100 mt-1.5">{{ a.summary }}</p>
                <p class="text-xs text-slate-500 mt-1">
                  {{ a.target }} · requested {{ fmtWhen(a.requested_at) }}
                  <span v-if="a.holds_forever"> · this one waits until you answer</span>
                </p>
              </div>
              <div class="flex gap-2 shrink-0">
                <button @click="decide(a, 'approved')" :disabled="deciding === a.id"
                        class="ui-btn">
                  Approve
                </button>
                <button @click="decide(a, 'rejected')" :disabled="deciding === a.id"
                        class="ui-btn">
                  Reject
                </button>
              </div>
            </div>
          </div>
        </div>

        <div v-if="pageCount(approvals.pending) > 1" class="flex items-center justify-center gap-3 mt-3">
          <button @click="turnPage('approvals', -1, approvals.pending)" :disabled="page.approvals === 1"
                  class="ui-btn">
            Prev
          </button>
          <span class="text-xs text-slate-500 tabular-nums">
            {{ page.approvals }} / {{ pageCount(approvals.pending) }} · {{ (approvals.pending || []).length }} total
          </span>
          <button @click="turnPage('approvals', 1, approvals.pending)" :disabled="page.approvals === pageCount(approvals.pending)"
                  class="ui-btn">
            Next
          </button>
        </div>
      </section>

      <!-- Noise floors: which targets can be trusted at all -->
      <section class="mt-12">
        <div class="mb-3">
          <h2 class="ui-section-title">Measured noise floors</h2>
          <p class="ui-section-sub max-w-3xl">
            A target with no measured floor cannot promote anything — two runs of one
            window have differed by ~16pp here, so an unmeasured target's results are
            not attributable.
          </p>
        </div>
        <div v-if="!floors.length" class="ui-card">
          <div class="ui-empty gap-1">
            <p class="text-slate-300 inline-flex items-center gap-2">
              <span class="ui-dot ui-dot-warn"></span>
              No floor has been measured yet
            </p>
            <p class="text-xs text-slate-500">Nothing is promotable until one is.</p>
          </div>
        </div>
        <div v-else class="ui-card overflow-x-auto">
          <table class="w-full text-sm">
            <thead><tr class="h-10 text-xs text-slate-500 border-b border-white/[0.06]">
              <th class="text-left font-normal px-5">Target</th>
              <th class="text-left font-normal px-5">Window class</th>
              <th class="text-right font-normal px-5">Floor</th>
              <th class="text-right font-normal px-5">Repeats</th>
              <th class="text-left font-normal px-5">Usable</th>
            </tr></thead>
            <tbody class="divide-y divide-white/[0.05]">
              <tr v-for="f in floors" :key="f.id" class="h-14 tabular-nums">
                <td class="px-5 text-slate-100">{{ f.target }}</td>
                <td class="px-5 text-slate-500">{{ f.window_class }}</td>
                <td class="px-5 text-right text-slate-300">{{ Number(f.floor_pp).toFixed(2) }}pp</td>
                <td class="px-5 text-right text-slate-300">{{ f.n }}</td>
                <td class="px-5" :class="f.measured ? 'text-slate-300' : 'text-amber-400'">
                  {{ f.measured ? 'yes' : (f.reason || 'not measured') }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- 2. Findings & reports -->
      <section class="mt-12">
        <div class="mb-3">
          <h2 class="ui-section-title">Findings &amp; reports</h2>
        </div>

        <div v-if="!loading && !findings.length && !loadError" class="ui-card">
          <div class="ui-empty gap-1">
            <p class="text-slate-300">Nothing raised yet</p>
            <p class="text-xs text-slate-500">
              {{ overview && !overview.engine_running
                  ? 'The self-learning engine is stopped — start it to begin observing.'
                  : 'Findings appear as completed runs are observed.' }}
            </p>
          </div>
        </div>

        <div v-if="findings.length" class="ui-card overflow-hidden ui-rows">
          <div v-for="f in paged(findings, 'findings')" :key="f.id">
            <button class="w-full text-left px-5 py-4 transition-colors duration-150 ease-out hover:bg-white/[0.03]"
                    :class="openThread === f.id ? 'bg-white/[0.02]' : ''"
                    :aria-expanded="openThread === f.id"
                    @click="toggleThread(f)">
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="flex items-center gap-x-3 gap-y-1 flex-wrap text-xs">
                    <span class="inline-flex items-center gap-1.5 text-slate-300 capitalize">
                      <span class="ui-dot" :class="severityClasses(f.severity)"></span>
                      {{ f.severity || '' }}
                    </span>
                    <span class="text-slate-500">{{ f.target }}</span>
                  </div>
                  <h3 class="text-sm text-slate-100 mt-1.5">{{ f.title }}</h3>
                  <p class="text-xs text-slate-500 mt-1">{{ f.detail }}</p>
                </div>
                <div class="flex items-center gap-3 shrink-0">
                  <span v-if="busyFor(f.id)"
                        class="inline-flex items-center gap-1.5 text-xs text-slate-300">
                    <span class="ui-dot ui-dot-live"></span>
                    {{ busyRole(f.id) }} thinking…
                  </span>
                  <span class="material-symbols-outlined text-slate-500 text-[18px]">
                    {{ openThread === f.id ? 'expand_less' : 'expand_more' }}
                  </span>
                </div>
              </div>
            </button>

            <!-- 3. Thread detail — the ladder stepper -->
            <div v-if="openThread === f.id" class="border-t border-white/[0.06] px-5 py-4">
              <p class="text-xs text-slate-500 mb-4 tabular-nums">
                Detected {{ fmtWhen(f.detected_at) }} · run {{ f.run_id || '—' }}
              </p>

              <ol class="relative border-l border-white/[0.08] ml-1">
                <li class="ml-5 pb-4">
                  <span class="absolute -left-[5px] mt-1.5 size-2.5 rounded-full bg-slate-200"></span>
                  <p class="text-sm text-slate-200">Detected</p>
                  <p class="text-xs text-slate-500 mt-0.5">{{ f.kind }} · {{ f.target }}</p>
                  <pre class="mt-2 text-xs text-slate-400 bg-background-dark border border-white/[0.06] rounded-lg p-3 overflow-x-auto tabular-nums">{{ JSON.stringify(f.evidence, null, 2) }}</pre>
                </li>
                <li v-for="rung in LADDER" :key="rung.key" class="ml-5 pb-4 last:pb-0">
                  <span class="absolute -left-[5px] mt-1.5 size-2.5 rounded-full"
                        :class="busyStep(f.id, rung.key)
                          ? 'bg-gain shadow-[0_0_0_3px_rgba(76,195,138,0.15)]'
                          : 'bg-slate-700'"></span>
                  <p class="text-sm"
                     :class="busyStep(f.id, rung.key) ? 'text-slate-200' : 'text-slate-500'">
                    {{ rung.label }}
                  </p>
                  <p class="text-xs text-slate-600 mt-0.5">{{ rung.hint }}</p>
                  <p v-if="busyStep(f.id, rung.key)"
                     class="text-xs text-slate-300 mt-1">
                    {{ busyRole(f.id) }} working on this step…
                  </p>
                  <p v-else class="text-xs text-slate-600 mt-1">Not reached yet</p>
                </li>
              </ol>
            </div>
          </div>
        </div>

        <div v-if="pageCount(findings) > 1" class="flex items-center justify-center gap-3 mt-3">
          <button @click="turnPage('findings', -1, findings)" :disabled="page.findings === 1"
                  class="ui-btn">
            Prev
          </button>
          <span class="text-xs text-slate-500 tabular-nums">
            {{ page.findings }} / {{ pageCount(findings) }} · {{ (findings || []).length }} total
          </span>
          <button @click="turnPage('findings', 1, findings)" :disabled="page.findings === pageCount(findings)"
                  class="ui-btn">
            Next
          </button>
        </div>
      </section>

      <!-- LLM usage -->
      <section v-if="llmUsage" class="mt-12">
        <div class="mb-3">
          <h2 class="ui-section-title">AI usage &amp; cost</h2>
          <p class="ui-section-sub max-w-3xl">
            The recorded cost of the subsystem's own calls, by role — not an
            estimate reconstructed from a price list.
          </p>
        </div>
        <div v-if="!llmUsage.available || !llmUsage.totals?.calls" class="ui-card">
          <div class="ui-empty gap-1">
            <p class="text-slate-300">No AI calls yet</p>
            <p class="text-xs text-slate-500">
              Usage appears once the loop asks a role for something.
            </p>
          </div>
        </div>
        <div v-else class="ui-card overflow-hidden">
          <div class="px-5 py-4 grid grid-cols-2 gap-4 sm:grid-cols-4 border-b border-white/[0.06]">
            <div>
              <p class="text-xs text-slate-500">Total cost</p>
              <p class="mt-1 text-xl font-medium text-slate-100 tabular-nums">
                ${{ Number(llmUsage.totals.cost_usd || 0).toFixed(4) }}
              </p>
            </div>
            <div>
              <p class="text-xs text-slate-500">Calls</p>
              <p class="mt-1 text-xl font-medium text-slate-100 tabular-nums">{{ llmUsage.totals.calls }}</p>
            </div>
            <div>
              <p class="text-xs text-slate-500">Input tokens</p>
              <p class="mt-1 text-xl font-medium text-slate-100 tabular-nums">
                {{ Number(llmUsage.totals.input_tokens || 0).toLocaleString() }}
              </p>
            </div>
            <div>
              <p class="text-xs text-slate-500">Output tokens</p>
              <p class="mt-1 text-xl font-medium text-slate-100 tabular-nums">
                {{ Number(llmUsage.totals.output_tokens || 0).toLocaleString() }}
              </p>
            </div>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead><tr class="h-10 text-xs text-slate-500 border-b border-white/[0.06]">
                <th class="text-left font-normal px-5">Role</th>
                <th class="text-right font-normal px-5">Calls</th>
                <th class="text-right font-normal px-5">In</th>
                <th class="text-right font-normal px-5">Out</th>
                <th class="text-right font-normal px-5">Cost</th>
                <th class="text-right font-normal px-5">Errors</th>
              </tr></thead>
              <tbody class="divide-y divide-white/[0.05]">
                <tr v-for="(u, role) in llmUsage.by_role" :key="role" class="h-14 tabular-nums">
                  <td class="px-5 text-slate-100 capitalize">{{ role }}</td>
                  <td class="px-5 text-right text-slate-300">{{ u.calls }}</td>
                  <td class="px-5 text-right text-slate-400">{{ Number(u.input_tokens).toLocaleString() }}</td>
                  <td class="px-5 text-right text-slate-400">{{ Number(u.output_tokens).toLocaleString() }}</td>
                  <td class="px-5 text-right text-slate-100">${{ Number(u.cost_usd).toFixed(4) }}</td>
                  <td class="px-5 text-right"
                      :class="u.errors ? 'text-loss' : 'text-slate-600'">{{ u.errors }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- Hypothesis ledger -->
      <section class="mt-12">
        <div class="mb-3">
          <h2 class="ui-section-title">Hypothesis ledger</h2>
          <p class="ui-section-sub max-w-3xl">
            Rejections are kept on purpose — they are what stops the generator
            re-proposing an idea that has already been disproved.
          </p>
        </div>
        <div v-if="!hypotheses.length" class="ui-card">
          <div class="ui-empty">
            <p class="text-slate-300">No hypotheses yet</p>
          </div>
        </div>
        <div v-else class="ui-card overflow-hidden ui-rows">
          <div v-for="h in paged(hypotheses, 'hypotheses')" :key="h.id"
               class="px-5 py-4">
            <div class="flex items-center gap-x-3 gap-y-1 flex-wrap text-xs">
              <span class="capitalize"
                    :class="h.status === 'confirmed'
                      ? 'text-slate-100'
                      : h.status === 'proposed'
                        ? 'text-slate-400'
                        : 'text-slate-500'">
                {{ h.status || '' }}
              </span>
              <span class="text-slate-500">{{ h.target }}</span>
              <span class="text-slate-500 tabular-nums">
                predicts {{ h.predicted_direction }} {{ h.predicted_min_pp }}–{{ h.predicted_max_pp }}pp
              </span>
            </div>
            <p class="text-sm text-slate-100 mt-1.5">{{ h.claim }}</p>
            <p class="text-xs text-slate-500 mt-1">{{ h.mechanism }}</p>
            <p v-if="h.status_reason" class="text-xs text-slate-400 mt-1">
              {{ h.status_reason }}
            </p>
          </div>
        </div>

        <div v-if="pageCount(hypotheses) > 1" class="flex items-center justify-center gap-3 mt-3">
          <button @click="turnPage('hypotheses', -1, hypotheses)" :disabled="page.hypotheses === 1"
                  class="ui-btn">
            Prev
          </button>
          <span class="text-xs text-slate-500 tabular-nums">
            {{ page.hypotheses }} / {{ pageCount(hypotheses) }} · {{ (hypotheses || []).length }} total
          </span>
          <button @click="turnPage('hypotheses', 1, hypotheses)" :disabled="page.hypotheses === pageCount(hypotheses)"
                  class="ui-btn">
            Next
          </button>
        </div>
      </section>

      <!-- What the loop decided, and why -->
      <section class="mt-12">
        <div class="mb-3">
          <h2 class="ui-section-title">Loop decisions</h2>
          <p class="ui-section-sub max-w-3xl">
            Every turn's intents, blocked ones included — a decision that leaves no
            trace is as unprovable as a lever that never announced itself.
          </p>
        </div>
        <div v-if="!intents.length" class="ui-card">
          <div class="ui-empty">
            <p class="text-slate-300">The loop has not run a turn yet</p>
          </div>
        </div>
        <div v-else class="ui-card overflow-hidden ui-rows">
          <div v-for="(i, idx) in paged(intents, 'intents')" :key="idx" class="min-h-14 px-5 py-3">
            <div class="flex items-center gap-x-3 gap-y-1 flex-wrap text-xs">
              <span class="text-slate-200">{{ i.kind }}</span>
              <span v-if="i.target" class="text-slate-500">{{ i.target }}</span>
              <span v-if="i.rung" class="text-slate-500">{{ i.rung }}</span>
              <span class="text-slate-600 ml-auto tabular-nums">{{ fmtWhen(i.at) }}</span>
            </div>
            <p class="text-sm text-slate-400 mt-1">{{ i.reason }}</p>
          </div>
        </div>

        <div v-if="pageCount(intents) > 1" class="flex items-center justify-center gap-3 mt-3">
          <button @click="turnPage('intents', -1, intents)" :disabled="page.intents === 1"
                  class="ui-btn">
            Prev
          </button>
          <span class="text-xs text-slate-500 tabular-nums">
            {{ page.intents }} / {{ pageCount(intents) }} · {{ (intents || []).length }} total
          </span>
          <button @click="turnPage('intents', 1, intents)" :disabled="page.intents === pageCount(intents)"
                  class="ui-btn">
            Next
          </button>
        </div>
      </section>

      <!-- Observed runs -->
      <section class="mt-12">
        <div class="mb-3">
          <h2 class="ui-section-title">Observed runs</h2>
        </div>
        <div v-if="!funnels.length" class="ui-card">
          <div class="ui-empty">
            <p class="text-slate-300">
              {{ loading ? 'Loading…' : loadError ? 'Could not load observed runs.' : 'No runs observed yet.' }}
            </p>
          </div>
        </div>
        <div v-else class="ui-card overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="h-10 text-xs text-slate-500 border-b border-white/[0.06]">
                <th class="text-left font-normal px-5">Run</th>
                <th class="text-left font-normal px-5">Target</th>
                <th class="text-right font-normal px-5">Decided</th>
                <th class="text-right font-normal px-5">Executed</th>
                <th class="text-right font-normal px-5">Refused</th>
                <th class="text-right font-normal px-5">Buy conv.</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-white/[0.05]">
              <tr v-for="row in paged(funnelRows, 'funnels')" :key="row.id" class="h-14 tabular-nums">
                <td class="px-5 text-slate-100 whitespace-nowrap">{{ row.run_id }}</td>
                <td class="px-5 text-slate-400">{{ row.target }}</td>
                <td class="px-5 text-right text-slate-300">{{ row.decided }}</td>
                <td class="px-5 text-right text-slate-300">{{ row.executed }}</td>
                <td class="px-5 text-right text-slate-300">{{ row.refused }}</td>
                <td class="px-5 text-right"
                    :class="row.pct !== null && row.pct < 25 ? 'text-amber-400' : 'text-slate-300'">
                  {{ row.pct === null ? '—' : row.pct.toFixed(1) + '%' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="pageCount(funnelRows) > 1" class="flex items-center justify-center gap-3 mt-3">
          <button @click="turnPage('funnels', -1, funnelRows)" :disabled="page.funnels === 1"
                  class="ui-btn">
            Prev
          </button>
          <span class="text-xs text-slate-500 tabular-nums">
            {{ page.funnels }} / {{ pageCount(funnelRows) }} · {{ (funnelRows || []).length }} total
          </span>
          <button @click="turnPage('funnels', 1, funnelRows)" :disabled="page.funnels === pageCount(funnelRows)"
                  class="ui-btn">
            Next
          </button>
        </div>
      </section>

    </main>
  </AppShell>
</template>
