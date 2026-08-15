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
  if (!res.ok) throw new Error(`${path} → ${res.status}`)
  return res.json()
}

async function load() {
  try {
    const [ov, fi, fu] = await Promise.all([
      getJson('/learning/overview'),
      getJson('/learning/findings?limit=100'),
      getJson('/learning/funnels?limit=100'),
    ])
    overview.value = ov
    findings.value = fi?.findings || []
    funnels.value = fu?.funnels || []
    loadError.value = ''
  } catch (err) {
    loadError.value = err?.message || 'Failed to load learning data'
  } finally {
    loading.value = false
  }
}

function toggleThread(finding) {
  openThread.value = openThread.value === finding.id ? null : finding.id
}

onMounted(() => {
  load()
  pollTimer = setInterval(load, 30000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
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

      <div v-if="loadError" class="mb-6 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
        {{ loadError }}
      </div>
      <div v-if="loading" class="text-slate-500 text-sm">Loading…</div>

      <!-- 1. Pending approvals -->
      <section class="mb-8">
        <h2 class="text-sm font-semibold text-slate-300 mb-2">Pending approvals</h2>
        <div class="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-6 text-center">
          <p class="text-sm text-slate-400">No approvals waiting.</p>
          <p class="text-xs text-slate-600 mt-1">
            The subsystem is observe-only — it records and reports, and does not yet propose changes.
          </p>
        </div>
      </section>

      <!-- 2. Findings & reports -->
      <section class="mb-8">
        <h2 class="text-sm font-semibold text-slate-300 mb-2">Findings &amp; reports</h2>

        <div v-if="!loading && !findings.length"
             class="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-6 text-center">
          <p class="text-sm text-slate-400">Nothing raised yet.</p>
          <p class="text-xs text-slate-600 mt-1">Findings appear as completed runs are observed.</p>
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

      <!-- Observed runs -->
      <section>
        <h2 class="text-sm font-semibold text-slate-300 mb-2">Observed runs</h2>
        <div v-if="!loading && !funnels.length"
             class="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-6 text-center">
          <p class="text-sm text-slate-400">No runs observed yet.</p>
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
              <tr v-for="row in funnels" :key="row.id" class="border-b border-slate-800/60 last:border-0">
                <td class="px-3 py-2 font-mono text-slate-300">{{ row.run_id }}</td>
                <td class="px-3 py-2 text-slate-400">{{ row.target }}</td>
                <td class="px-3 py-2 text-right text-slate-300">{{ row.decided }}</td>
                <td class="px-3 py-2 text-right text-slate-300">{{ row.executed }}</td>
                <td class="px-3 py-2 text-right text-slate-300">{{ row.refused }}</td>
                <td class="px-3 py-2 text-right"
                    :class="conversionPct(row) !== null && conversionPct(row) < 25 ? 'text-rose-400' : 'text-slate-300'">
                  {{ conversionPct(row) === null ? '—' : conversionPct(row).toFixed(1) + '%' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

    </main>
  </AppShell>
</template>
