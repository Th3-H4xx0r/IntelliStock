<script setup>
import { ref, onMounted } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

// ── State ─────────────────────────────────────────────────────────────────────
const accounts    = ref([])
const loading     = ref(true)
const loadError   = ref('')

// Modal state
const showModal   = ref(false)
const activeTab   = ref('alpaca')   // 'alpaca' | 'robinhood'
const submitting  = ref(false)
const submitMsg   = ref('')
const submitOk    = ref(false)

// Edit mode
const editMode    = ref(false)
const editId      = ref(null)

// Alpaca form
const alpacaForm  = ref({ account_name: '', key: '', secret: '', paper: true, data_feed: 'iex' })

// R16: Alpaca diagnostic test-suite popup state. Catches credential
// mistakes at config time instead of surfacing as 401s mid-run.
const testRunning = ref(false)
const testResult  = ref(null)
const showTestPopup = ref(false)

// Pydantic v2 returns 422 errors as {detail: [{loc,msg,...}]}.
// `new Error([...])` would stringify to garbage; normalize so the red banner
// shows the actual reason ("field X invalid" etc.).
function _normalizeErrorDetail(data, fallbackStatus) {
  const d = data?.detail
  if (Array.isArray(d)) return d.map(x => (x && (x.msg || x.message)) || JSON.stringify(x)).join('; ')
  if (typeof d === 'string') return d
  if (d && typeof d === 'object') return d.msg || d.message || JSON.stringify(d)
  return `HTTP ${fallbackStatus}`
}

// Robinhood multi-step
// step 1: enter tokens  step 2: pick account
const rhStep         = ref(1)
const rhForm         = ref({ account_name: '', access_token: '', refresh_token: '', device_token: '' })
const rhAccounts     = ref([])   // fetched accounts from preview endpoint
const rhSelected     = ref('')   // chosen account_number

// ── Helpers ───────────────────────────────────────────────────────────────────
function authHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

const fmt = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 })

function fmtEquity(v) {
  return v != null ? fmt.format(v) : '—'
}

function fmtType(t) {
  if (!t) return ''
  return t.charAt(0).toUpperCase() + t.slice(1)
}

async function fetchAccounts() {
  loading.value  = true
  loadError.value = ''
  try {
    const res = await fetch(`${API_BASE}/brokerages`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    accounts.value = data.accounts || []
  } catch (e) {
    loadError.value = `Failed to load accounts: ${e.message}`
  } finally {
    loading.value = false
  }
}

async function deleteAccount(id, name) {
  if (!confirm(`Remove "${name}"? This cannot be undone.`)) return
  try {
    const res = await fetch(`${API_BASE}/brokerages/${id}`, {
      method: 'DELETE',
      headers: authHeaders(),
    })
    if (!res.ok) {
      const d = await res.json().catch(() => ({}))
      throw new Error(d.detail || `HTTP ${res.status}`)
    }
    await fetchAccounts()
  } catch (e) {
    alert(`Failed to remove: ${e.message}`)
  }
}

async function refreshRobinhood(id, name) {
  if (!confirm(`Refresh tokens for "${name}"?`)) return
  try {
    const res = await fetch(`${API_BASE}/brokerages/${id}/refresh`, {
      method: 'POST',
      headers: authHeaders(),
    })
    if (!res.ok) {
      const d = await res.json().catch(() => ({}))
      throw new Error(d.detail || `HTTP ${res.status}`)
    }
    await fetchAccounts()
  } catch (e) {
    alert(`Refresh failed: ${e.message}`)
  }
}

// ── Modal ─────────────────────────────────────────────────────────────────────
function openModal() {
  editMode.value    = false
  editId.value      = null
  activeTab.value   = 'alpaca'
  submitMsg.value   = ''
  submitOk.value    = false
  submitting.value  = false
  alpacaForm.value  = { account_name: '', key: '', secret: '', paper: true, data_feed: 'iex' }
  rhForm.value      = { account_name: '', access_token: '', refresh_token: '', device_token: '' }
  rhStep.value      = 1
  rhAccounts.value  = []
  rhSelected.value  = ''
  showModal.value   = true
}

function openEditModal(acct) {
  editMode.value    = true
  editId.value      = acct.id
  activeTab.value   = acct.brokerage_type
  submitMsg.value   = ''
  submitOk.value    = false
  submitting.value  = false
  rhStep.value      = 1
  rhAccounts.value  = []
  rhSelected.value  = ''
  if (acct.brokerage_type === 'alpaca') {
    alpacaForm.value = { account_name: acct.account_name, key: acct.alpaca_key || '', secret: '', paper: acct.alpaca_paper ?? true, data_feed: acct.alpaca_data_feed || 'iex' }
  } else {
    rhForm.value = { account_name: acct.account_name, access_token: '', refresh_token: '', device_token: '' }
  }
  showModal.value = true
}

function closeModal() {
  if (submitting.value) return
  showModal.value = false
  // R16: clear test-suite state so stale results from a previous account
  // don't bleed into the next add/edit session.
  testResult.value = null
  showTestPopup.value = false
  testRunning.value = false
}

function setTab(tab) {
  activeTab.value  = tab
  submitMsg.value  = ''
  submitOk.value   = false
  rhStep.value     = 1
  rhAccounts.value = []
  rhSelected.value = ''
}

// Step 1 → Step 2: fetch Robinhood accounts for selection
async function rhFetchAccounts() {
  const f = rhForm.value
  if (!f.account_name.trim()) { submitMsg.value = 'Account name is required'; submitOk.value = false; return }
  // In edit mode with no tokens provided, save name-only without going to step 2
  if (editMode.value && !f.access_token.trim()) { return submitLink() }
  if (!f.access_token.trim()) { submitMsg.value = 'Access token is required'; submitOk.value = false; return }
  if (!f.refresh_token.trim()) { submitMsg.value = 'Refresh token is required'; submitOk.value = false; return }

  submitting.value = true
  submitMsg.value  = 'Fetching accounts...'
  submitOk.value   = false
  try {
    const res = await fetch(`${API_BASE}/brokerages/robinhood/accounts`, {
      method:  'POST',
      headers: authHeaders(),
      body:    JSON.stringify({
        access_token:  f.access_token.trim(),
        refresh_token: f.refresh_token.trim(),
        device_token:  f.device_token.trim() || undefined,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(_normalizeErrorDetail(data, res.status))

    rhAccounts.value = data.accounts || []
    // Auto-select the first selectable (non-managed) account
    const selectable = rhAccounts.value.filter(a => a.management_type !== 'managed')
    rhSelected.value = selectable.length === 1 ? selectable[0].account_number : ''
    rhStep.value     = 2
    submitMsg.value  = ''
  } catch (e) {
    submitOk.value  = false
    submitMsg.value = e.message || 'Failed to fetch accounts'
  } finally {
    submitting.value = false
  }
}

async function submitLink(opts = {}) {
  // R16 phase-2 (2026-04-25): auto-run the Alpaca diagnostic suite before
  // saving. If any probe fails we open the test popup with results and a
  // "Save Anyway" button — no creds get persisted until either all probes
  // pass or the user explicitly overrides. Bypassed via {bypassTest: true}
  // for the override path. Non-Alpaca tabs and edit-mode-without-new-secret
  // skip the gate (no creds to test against).
  const bypassTest = !!opts.bypassTest
  if (
    !bypassTest
    && activeTab.value === 'alpaca'
    && alpacaForm.value.key.trim()
    && alpacaForm.value.secret.trim()
  ) {
    submitting.value = true
    submitMsg.value = 'Validating credentials...'
    submitOk.value = false
    try {
      const f = alpacaForm.value
      const r = await fetch(`${API_BASE}/brokerages/test-alpaca`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          key: f.key.trim(),
          secret: f.secret.trim(),
          paper: f.paper,
          alpaca_data_feed: f.data_feed || 'iex',
        }),
      })
      const data = await r.json().catch(() => ({}))
      if (r.ok && data && data.ok) {
        // All 5 probes passed — fall through to the save path below.
        testResult.value = data
      } else {
        // Surface the failures and let the user fix or override.
        testResult.value = r.ok ? data : {
          ok: false,
          summary: { passed: 0, failed: 0, total: 0 },
          tests: [],
          hints: [_normalizeErrorDetail(data, r.status)],
        }
        showTestPopup.value = true
        submitMsg.value = 'Credential test failed — review the popup. Click "Save Anyway" to bypass.'
        submitOk.value = false
        submitting.value = false
        return
      }
    } catch (e) {
      // Network error during pre-save test — show inline and let user retry.
      // Don't block save permanently; user can hit save again to bypass once
      // they've decided.
      submitMsg.value = `Pre-save credential test errored (${e.message || e}); click Save again to bypass.`
      submitOk.value = false
      submitting.value = false
      return
    }
  }

  submitting.value = true
  submitMsg.value  = ''
  submitOk.value   = false

  try {
    let body
    if (activeTab.value === 'alpaca') {
      const f = alpacaForm.value
      if (!f.account_name.trim()) throw new Error('Account name is required')
      if (!editMode.value && !f.key.trim())    throw new Error('API Key ID is required')
      if (!editMode.value && !f.secret.trim()) throw new Error('Secret Key is required')
      body = editMode.value
        ? {
            account_name: f.account_name.trim() || undefined,
            key:          f.key.trim()    || undefined,
            secret:       f.secret.trim() || undefined,
            paper:        f.paper,
            alpaca_data_feed: f.data_feed || undefined,
          }
        : {
            brokerage_type: 'alpaca',
            account_name:   f.account_name.trim(),
            key:            f.key.trim(),
            secret:         f.secret.trim(),
            paper:          f.paper,
            alpaca_data_feed: f.data_feed || 'iex',
          }
    } else {
      const f = rhForm.value
      if (!editMode.value && !rhSelected.value) throw new Error('Please select an account')
      body = editMode.value
        ? {
            account_name:   f.account_name.trim() || undefined,
            access_token:   f.access_token.trim()  || undefined,
            refresh_token:  f.refresh_token.trim() || undefined,
            device_token:   f.device_token.trim()  || undefined,
            account_number: rhSelected.value || undefined,
          }
        : {
            brokerage_type: 'robinhood',
            account_name:   f.account_name.trim(),
            access_token:   f.access_token.trim(),
            refresh_token:  f.refresh_token.trim(),
            device_token:   f.device_token.trim() || undefined,
            account_number: rhSelected.value,
          }
    }

    if (editMode.value) {
      submitMsg.value = 'Saving changes...'
      const res = await fetch(`${API_BASE}/brokerages/${editId.value}`, {
        method:  'PUT',
        headers: authHeaders(),
        body:    JSON.stringify(body),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(_normalizeErrorDetail(data, res.status))
      // 2026-04-23: surface data-access test result on success
      const _da = data?.data_access
      if (_da?.ok) {
        submitMsg.value = `Account updated! Data access verified (feed=${_da.feed}, ${_da.bars ?? 0} bar(s) fetched).`
        submitOk.value = true
      } else if (_da?.warning) {
        submitMsg.value = `Account updated. Data access could not be verified (${_da.warning}); will retry at broker boot.`
        submitOk.value = true
      }
      submitOk.value  = true
      submitMsg.value = `Account "${data.account?.account_name}" updated successfully!`
      await fetchAccounts()
      setTimeout(() => { showModal.value = false }, 1500)
      return
    }

    submitMsg.value = 'Linking account...'
    const res = await fetch(`${API_BASE}/brokerages`, {
      method:  'POST',
      headers: authHeaders(),
      body:    JSON.stringify(body),
    })

    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(_normalizeErrorDetail(data, res.status))

    submitOk.value  = true
    // 2026-04-23: include data-access test result inline on the success banner
    const _da = data?.data_access
    let _suffix = ''
    if (_da?.ok) _suffix = ` Data access verified (feed=${_da.feed}, ${_da.bars ?? 0} bar(s) fetched).`
    else if (_da?.warning) _suffix = ` Data access deferred: ${_da.warning}.`
    submitMsg.value = `Account "${data.account?.account_name}" linked successfully!${_suffix}`
    await fetchAccounts()
    setTimeout(() => { showModal.value = false }, 1500)

  } catch (e) {
    submitOk.value  = false
    submitMsg.value = e.message || 'Something went wrong'
  } finally {
    submitting.value = false
  }
}

// R16 (2026-04-25): Alpaca diagnostic test suite. Calls the backend's
// /brokerages/test-alpaca probe endpoint with the form's current key/secret/
// feed/paper combo. The endpoint runs 5 read-only Alpaca calls (account,
// clock, bars, latest_quote, news) and returns a structured per-test result
// the popup renders as PASS/FAIL chips. Probe-only — does not save.
async function runAlpacaTestSuite() {
  const f = alpacaForm.value
  // R16 phase-3: in edit mode without a fresh pasted secret, send
  // {brokerage_id} so the backend probes the STORED creds from DB.
  // Otherwise (add mode, or edit mode with a re-pasted secret) send raw
  // key+secret. If neither is satisfied, surface a hint without firing
  // the network call.
  const hasFormCreds = !!(f.key.trim() && f.secret.trim())
  const canTestStored = editMode.value && !!editId.value
  if (!hasFormCreds && !canTestStored) {
    testResult.value = {
      ok: false,
      summary: { passed: 0, failed: 0, total: 0 },
      tests: [],
      hints: ['Fill in both API Key ID and Secret Key first to run the probe.'],
    }
    showTestPopup.value = true
    return
  }

  testRunning.value = true
  testResult.value = null
  showTestPopup.value = true
  try {
    const reqBody = hasFormCreds
      ? {
          key: f.key.trim(),
          secret: f.secret.trim(),
          paper: f.paper,
          alpaca_data_feed: f.data_feed || 'iex',
        }
      : {
          // Edit-mode probe of stored creds. Override paper/feed only when
          // user changed them in the form vs. the stored value — backend
          // falls back to the row's stored values when these are absent.
          brokerage_id: editId.value,
          paper: f.paper,
          alpaca_data_feed: f.data_feed || undefined,
        }
    const res = await fetch(`${API_BASE}/brokerages/test-alpaca`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(reqBody),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      testResult.value = {
        ok: false,
        summary: { passed: 0, failed: 0, total: 0 },
        tests: [],
        hints: [_normalizeErrorDetail(data, res.status)],
      }
    } else {
      testResult.value = data
    }
  } catch (e) {
    testResult.value = {
      ok: false,
      summary: { passed: 0, failed: 0, total: 0 },
      tests: [],
      hints: [`Network error: ${e.message || e}`],
    }
  } finally {
    testRunning.value = false
  }
}

// ── Status helpers ────────────────────────────────────────────────────────────
function statusColor(status) {
  if (status === 'active')  return 'text-emerald-400'
  if (status === 'expired') return 'text-red-400'
  return 'text-yellow-400'
}

function statusDot(status) {
  if (status === 'active')  return 'bg-emerald-400'
  if (status === 'expired') return 'bg-red-400'
  return 'bg-yellow-400'
}

onMounted(fetchAccounts)
</script>

<template>
  <AppShell>
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10">

      <!-- Header -->
      <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between mb-6 sm:mb-8">
        <div>
          <p class="text-primary text-xs font-bold uppercase tracking-widest mb-1">Brokerages</p>
          <h1 class="text-2xl sm:text-3xl font-bold leading-tight">Linked Accounts</h1>
          <p class="text-slate-400 text-sm mt-1 max-w-md">Manage your Alpaca and Robinhood brokerage connections.</p>
        </div>
        <button
          @click="openModal"
          class="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary text-background-dark font-semibold text-sm hover:brightness-110 transition-all shrink-0"
        >
          <span class="material-symbols-outlined text-[18px]">add</span>
          Link Brokerage
        </button>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="flex items-center gap-3 text-slate-400 text-sm">
        <span class="material-symbols-outlined animate-spin text-xl">progress_activity</span>
        Loading accounts...
      </div>

      <!-- Error -->
      <div v-else-if="loadError" class="rounded-xl bg-red-500/10 border border-red-500/20 px-5 py-4 text-red-400 text-sm">
        {{ loadError }}
      </div>

      <!-- Empty state -->
      <div
        v-else-if="accounts.length === 0"
        class="rounded-2xl border border-border-subtle bg-surface/30 px-8 py-16 flex flex-col items-center gap-4 text-center"
      >
        <span class="material-symbols-outlined text-5xl text-slate-600">account_balance</span>
        <p class="text-slate-300 font-semibold">No brokerages linked yet</p>
        <p class="text-slate-500 text-sm max-w-xs">Link an Alpaca or Robinhood account to start trading.</p>
        <button
          @click="openModal"
          class="mt-2 px-5 py-2 rounded-lg bg-primary text-background-dark font-semibold text-sm hover:brightness-110 transition-all"
        >
          Link your first brokerage
        </button>
      </div>

      <!-- Account cards -->
      <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
        <div
          v-for="acct in accounts"
          :key="acct.id"
          class="glass-card rounded-2xl p-5 sm:p-6 flex flex-col gap-4"
        >
          <!-- Card header -->
          <div class="flex items-start justify-between gap-3 min-w-0">
            <div class="flex items-center gap-3 min-w-0">
              <div class="size-10 rounded-xl bg-surface border border-border-subtle flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-primary text-xl">
                  {{ acct.brokerage_type === 'alpaca' ? 'show_chart' : 'savings' }}
                </span>
              </div>
              <div class="min-w-0">
                <p class="font-semibold text-slate-100 leading-tight truncate" :title="acct.account_name">{{ acct.account_name }}</p>
                <p class="text-xs text-slate-500 uppercase tracking-widest mt-0.5">
                  {{ acct.brokerage_type }}
                  <span v-if="acct.brokerage_type === 'alpaca' && acct.alpaca_paper" class="text-primary">&nbsp;· Paper</span>
                  <span v-if="acct.brokerage_type === 'alpaca' && !acct.alpaca_paper" class="text-yellow-400">&nbsp;· Live</span>
                </p>
              </div>
            </div>
            <div class="flex items-center gap-1.5 shrink-0">
              <div class="size-2 rounded-full" :class="statusDot(acct.status)"></div>
              <span class="text-xs font-medium" :class="statusColor(acct.status)">{{ acct.status }}</span>
            </div>
          </div>

          <!-- Details -->
          <div class="space-y-1.5 text-xs text-slate-500">
            <div v-if="acct.brokerage_type === 'alpaca'">
              <span class="text-slate-400">Key: </span>{{ acct.alpaca_key }}
            </div>
            <div v-if="acct.brokerage_type === 'alpaca' && acct.alpaca_account_number">
              <span class="text-slate-400">Account #: </span>{{ acct.alpaca_account_number }}
            </div>
            <div v-if="acct.brokerage_type === 'robinhood' && acct.robinhood_account_number">
              <span class="text-slate-400">Account #: </span>{{ acct.robinhood_account_number }}
            </div>
            <div v-if="acct.last_refresh_at">
              <span class="text-slate-400">Last refreshed: </span>{{ new Date(acct.last_refresh_at).toLocaleString() }}
            </div>
            <div v-if="acct.last_error" class="text-red-400 break-words">
              <span class="text-red-400 font-medium">Error: </span>{{ acct.last_error }}
            </div>
          </div>

          <!-- Actions -->
          <div class="flex flex-wrap items-center gap-2 pt-1 mt-auto">
            <button
              v-if="acct.brokerage_type === 'robinhood'"
              @click="refreshRobinhood(acct.id, acct.account_name)"
              class="w-full sm:w-auto inline-flex items-center justify-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-primary hover:bg-surface border border-border-subtle transition-colors"
            >
              <span class="material-symbols-outlined text-[14px]">refresh</span>
              Refresh
            </button>
            <button
              @click="openEditModal(acct)"
              class="w-full sm:w-auto inline-flex items-center justify-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-primary hover:bg-surface border border-border-subtle transition-colors"
            >
              <span class="material-symbols-outlined text-[14px]">edit</span>
              Edit
            </button>
            <button
              @click="deleteAccount(acct.id, acct.account_name)"
              class="w-full sm:w-auto inline-flex items-center justify-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-500 hover:text-red-400 hover:bg-red-500/5 border border-border-subtle transition-colors sm:ml-auto"
            >
              <span class="material-symbols-outlined text-[14px]">delete</span>
              Remove
            </button>
          </div>
        </div>
      </div>

    </main>
  </AppShell>

  <!-- ── Link Brokerage Modal ──────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showModal"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeModal"
      >
        <div class="relative w-full max-w-md bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">

          <!-- Modal header -->
          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle">
            <div>
              <h2 class="text-base font-bold">{{ editMode ? 'Edit Brokerage Account' : 'Link Brokerage Account' }}</h2>
              <!-- Robinhood breadcrumb (only in link mode) -->
              <p v-if="!editMode && activeTab === 'robinhood'" class="text-xs text-slate-500 mt-0.5">
                <span :class="rhStep >= 1 ? 'text-primary' : ''">1. Credentials</span>
                <span class="mx-1.5">›</span>
                <span :class="rhStep >= 2 ? 'text-primary' : ''">2. Select Account</span>
              </p>
            </div>
            <button @click="closeModal" class="text-slate-500 hover:text-slate-300 transition-colors">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>

          <!-- Tab slider (only show on step 1 and not in edit mode) -->
          <div v-if="!editMode && rhStep === 1" class="px-6 pt-5">
            <div class="flex rounded-lg bg-surface border border-border-subtle p-1 gap-1">
              <button
                @click="setTab('alpaca')"
                class="flex-1 py-2 rounded-md text-sm font-semibold transition-all"
                :class="activeTab === 'alpaca'
                  ? 'bg-primary text-background-dark'
                  : 'text-slate-400 hover:text-slate-200'"
              >
                Alpaca
              </button>
              <button
                @click="setTab('robinhood')"
                class="flex-1 py-2 rounded-md text-sm font-semibold transition-all"
                :class="activeTab === 'robinhood'
                  ? 'bg-primary text-background-dark'
                  : 'text-slate-400 hover:text-slate-200'"
              >
                Robinhood
              </button>
            </div>
          </div>

          <!-- Form body -->
          <div class="px-6 py-5 space-y-4">

            <!-- ── Alpaca fields ── -->
            <template v-if="activeTab === 'alpaca'">
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1.5">Account Name</label>
                <input v-model="alpacaForm.account_name" type="text" placeholder="e.g. My Paper Trading"
                  class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors" />
              </div>
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1.5">API Key ID</label>
                <input v-model="alpacaForm.key" type="text" placeholder="PKXXXXXXXXXXXXXXXXXXXXXXXX"
                  class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono" />
              </div>
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1.5">Secret Key<span v-if="editMode" class="text-slate-600 ml-1">(leave blank to keep existing)</span></label>
                <input v-model="alpacaForm.secret" type="password"
                  :placeholder="editMode ? 'Leave blank to keep existing secret' : '••••••••••••••••••••••••••••••••••••••••'"
                  class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono" />
              </div>
              <div class="flex items-center gap-3">
                <button type="button" @click="alpacaForm.paper = !alpacaForm.paper"
                  class="relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 transition-colors"
                  :class="alpacaForm.paper ? 'bg-primary border-primary' : 'bg-surface border-border-subtle'">
                  <span class="inline-block size-3.5 rounded-full bg-white shadow transition-transform"
                    :class="alpacaForm.paper ? 'translate-x-4' : 'translate-x-0.5'"></span>
                </button>
                <span class="text-sm text-slate-300">
                  Paper trading
                  <span class="text-slate-500 text-xs ml-1">({{ alpacaForm.paper ? 'paper-api.alpaca.markets' : 'api.alpaca.markets' }})</span>
                </span>
              </div>
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1.5">
                  Market Data Feed
                  <span class="text-slate-600 ml-1">(validated on save via data.alpaca.markets)</span>
                </label>
                <select v-model="alpacaForm.data_feed"
                  class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors">
                  <option value="iex">IEX (free — Basic Market Data)</option>
                  <option value="sip">SIP (paid — Algo Trader Plus)</option>
                </select>
                <p class="text-[11px] text-slate-500 mt-1.5 leading-relaxed">
                  Paper accounts have free IEX access. Live (funded) accounts must explicitly
                  subscribe to "Basic Market Data" on the Alpaca dashboard for IEX, or
                  "Algo Trader Plus" ($99/mo) for SIP. Save fails with a clear error if the
                  chosen feed isn't authorized.
                </p>
              </div>
            </template>

            <!-- ── Robinhood Step 1: credentials ── -->
            <template v-else-if="activeTab === 'robinhood' && rhStep === 1">
              <div v-if="editMode" class="rounded-lg bg-blue-500/10 border border-blue-500/20 px-4 py-3 text-xs text-blue-300 leading-relaxed">
                Update account name and/or paste new tokens. Leave tokens blank to only update the name.
              </div>
              <div v-else class="rounded-lg bg-amber-500/10 border border-amber-500/20 px-4 py-3 text-xs text-amber-300 leading-relaxed">
                Paste your Robinhood tokens. Obtain them by running
                <code class="font-mono bg-black/30 px-1 rounded">python robinhood_cli.py --paste-tokens</code>
                and copying the tokens shown.
              </div>
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1.5">Account Name</label>
                <input v-model="rhForm.account_name" type="text" placeholder="e.g. My Robinhood Account"
                  class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors" />
              </div>
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1.5">Access Token</label>
                <textarea v-model="rhForm.access_token" rows="2" placeholder="Paste access token (Bearer prefix will be stripped)"
                  class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono resize-none"></textarea>
              </div>
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1.5">Refresh Token</label>
                <textarea v-model="rhForm.refresh_token" rows="2" placeholder="Paste refresh token"
                  class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono resize-none"></textarea>
              </div>
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1.5">Device Token <span class="text-slate-600">(optional)</span></label>
                <input v-model="rhForm.device_token" type="text" placeholder="Leave blank to auto-generate"
                  class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono" />
              </div>
            </template>

            <!-- ── Robinhood Step 2: account picker ── -->
            <template v-else-if="activeTab === 'robinhood' && rhStep === 2">
              <p class="text-xs text-slate-400">
                Select the Robinhood account you want to link as
                <span class="text-slate-200 font-semibold">{{ rhForm.account_name }}</span>.
              </p>

              <div class="space-y-2">
                <button
                  v-for="a in rhAccounts"
                  :key="a.account_number"
                  @click="a.management_type !== 'managed' && (rhSelected = a.account_number)"
                  class="w-full text-left rounded-xl border px-4 py-3.5 transition-all"
                  :disabled="a.management_type === 'managed'"
                  :class="a.management_type === 'managed'
                    ? 'border-border-subtle bg-surface/20 opacity-40 cursor-not-allowed'
                    : rhSelected === a.account_number
                      ? 'border-primary bg-primary/8'
                      : 'border-border-subtle bg-surface/50 hover:border-slate-600'"
                >
                  <div class="flex items-center justify-between gap-3">
                    <!-- Left: radio + account info -->
                    <div class="flex items-center gap-3">
                      <div class="size-4 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors"
                        :class="a.management_type === 'managed'
                          ? 'border-slate-700'
                          : rhSelected === a.account_number ? 'border-primary' : 'border-slate-600'">
                        <div v-if="rhSelected === a.account_number && a.management_type !== 'managed'" class="size-2 rounded-full bg-primary"></div>
                      </div>
                      <div>
                        <div class="flex items-center gap-2">
                          <p class="text-sm font-semibold text-slate-100">{{ a.display_name || a.account_number }}</p>
                          <span v-if="a.management_type === 'managed'" class="text-[10px] px-1.5 py-0.5 rounded bg-slate-700/60 text-slate-500 font-medium uppercase tracking-wide">Managed</span>
                        </div>
                        <p class="text-xs text-slate-500 mt-0.5 font-mono">{{ a.account_number }}
                          <span v-if="a.account_type" class="ml-1 capitalize">· {{ fmtType(a.account_type) }}</span>
                        </p>
                      </div>
                    </div>
                    <!-- Right: equity + buying power -->
                    <div class="text-right shrink-0">
                      <p class="text-sm font-bold text-slate-100 tabular-nums">{{ fmtEquity(a.equity) }}</p>
                      <p class="text-xs text-slate-500 mt-0.5">
                        <span class="text-slate-400">BP:</span> {{ fmtEquity(a.buying_power) }}
                      </p>
                    </div>
                  </div>
                </button>
              </div>
            </template>

            <!-- Status message -->
            <Transition name="slide-up">
              <div
                v-if="submitMsg"
                class="rounded-lg px-4 py-3 text-sm font-medium"
                :class="submitOk
                  ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                  : 'bg-red-500/10 border border-red-500/20 text-red-400'"
              >
                <span v-if="submitting && !submitOk" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-base animate-spin">progress_activity</span>
                  {{ submitMsg }}
                </span>
                <span v-else>{{ submitMsg }}</span>
              </div>
            </Transition>
          </div>

          <!-- Modal footer -->
          <div class="px-6 pb-6 flex gap-3">
            <!-- Alpaca or Robinhood step 1 cancel -->
            <button
              v-if="activeTab === 'alpaca' || rhStep === 1"
              @click="closeModal"
              :disabled="submitting"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40"
            >
              Cancel
            </button>

            <!-- Robinhood step 2 back button -->
            <button
              v-if="activeTab === 'robinhood' && rhStep === 2"
              @click="rhStep = 1; submitMsg = ''"
              :disabled="submitting"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40"
            >
              Back
            </button>

            <!-- Alpaca: Test Connection (R16) -->
            <button
              v-if="activeTab === 'alpaca'"
              @click="runAlpacaTestSuite"
              :disabled="submitting || testRunning"
              class="py-2.5 px-4 rounded-lg border border-primary/60 text-primary text-sm font-medium hover:bg-primary/10 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
              title="Run a 5-endpoint probe (account, clock, bars, latest quote, news) against Alpaca with these credentials. Read-only; does not save."
            >
              <span v-if="testRunning" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              <span v-else class="material-symbols-outlined text-base">network_check</span>
              {{ testRunning ? 'Testing...' : 'Test' }}
            </button>

            <!-- Alpaca: Link / Save Account -->
            <button
              v-if="activeTab === 'alpaca'"
              @click="submitLink"
              :disabled="submitting"
              class="flex-1 py-2.5 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
            >
              <span v-if="submitting" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              {{ submitting ? (editMode ? 'Saving...' : 'Linking...') : (editMode ? 'Save Changes' : 'Link Account') }}
            </button>

            <!-- Robinhood step 1: Fetch Accounts / Save Changes -->
            <button
              v-if="activeTab === 'robinhood' && rhStep === 1"
              @click="rhFetchAccounts"
              :disabled="submitting"
              class="flex-1 py-2.5 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
            >
              <span v-if="submitting" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              {{ submitting
                ? (editMode ? 'Saving...' : 'Fetching...')
                : (editMode ? (rhForm.access_token.trim() ? 'Next →' : 'Save Changes') : 'Fetch Accounts') }}
            </button>

            <!-- Robinhood step 2: Link / Save Account -->
            <button
              v-if="activeTab === 'robinhood' && rhStep === 2"
              @click="submitLink"
              :disabled="submitting || !rhSelected"
              class="flex-1 py-2.5 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
            >
              <span v-if="submitting" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              {{ submitting ? (editMode ? 'Saving...' : 'Linking...') : (editMode ? 'Save Changes' : 'Link Account') }}
            </button>
          </div>

        </div>
      </div>
    </Transition>

    <!-- R16: Alpaca diagnostic test-suite popup. Layered above the
         brokerage add/edit modal so the user can run the probe without
         losing their form state. -->
    <Transition name="fade">
      <div
        v-if="showTestPopup"
        class="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm"
        @click.self="showTestPopup = false"
      >
        <Transition name="slide-up" appear>
          <div
            v-if="showTestPopup"
            class="relative w-full max-w-lg max-h-[85vh] overflow-y-auto bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl"
          >
            <div class="px-6 py-4 border-b border-border-subtle flex items-center justify-between">
              <div class="flex items-center gap-2">
                <span class="material-symbols-outlined text-primary">network_check</span>
                <h3 class="text-base font-semibold text-slate-100">Alpaca Connection Test</h3>
              </div>
              <button
                @click="showTestPopup = false"
                class="text-slate-500 hover:text-slate-300 transition-colors"
                aria-label="Close test results"
              >
                <span class="material-symbols-outlined">close</span>
              </button>
            </div>

            <div class="px-6 py-4">
              <!-- Loading -->
              <div v-if="testRunning" class="flex items-center gap-3 py-6 text-slate-400 text-sm">
                <span class="material-symbols-outlined animate-spin">progress_activity</span>
                Running 5-endpoint probe against Alpaca...
              </div>

              <!-- Results -->
              <div v-else-if="testResult">
                <!-- Summary banner — three states: passed / failed / not-run.
                     The not-run state (total=0) is what shows when the user
                     hits Test in edit mode without re-pasting the secret;
                     showing "0 of 0 tests failed" was misleading, so route
                     that case to a neutral amber "Probe could not run" banner
                     and rely on the Hints panel below to guide them. -->
                <div
                  v-if="(testResult.summary?.total ?? 0) === 0"
                  class="mb-4 px-4 py-3 rounded-lg border text-sm flex items-center gap-2 border-amber-500/40 bg-amber-500/10 text-amber-300"
                >
                  <span class="material-symbols-outlined">info</span>
                  <span><strong>Probe could not run</strong> — see hints below.</span>
                </div>
                <div
                  v-else
                  class="mb-4 px-4 py-3 rounded-lg border text-sm flex items-center gap-2"
                  :class="testResult.ok
                    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                    : 'border-red-500/40 bg-red-500/10 text-red-300'"
                >
                  <span class="material-symbols-outlined">{{ testResult.ok ? 'check_circle' : 'error' }}</span>
                  <span>
                    <strong>{{ testResult.ok
                      ? `All ${testResult.summary.total} tests passed`
                      : `${testResult.summary.failed} of ${testResult.summary.total} tests failed` }}</strong>
                    <span v-if="testResult.feed" class="text-slate-400 ml-2">feed={{ testResult.feed }}, paper={{ testResult.paper }}</span>
                  </span>
                </div>

                <!-- Per-test rows -->
                <ul v-if="testResult.tests?.length" class="space-y-2 mb-4">
                  <li
                    v-for="t in testResult.tests"
                    :key="t.name"
                    class="px-3 py-2 rounded-lg border border-border-subtle bg-black/30 text-sm flex items-start gap-3"
                  >
                    <span
                      class="material-symbols-outlined mt-0.5 shrink-0"
                      :class="t.ok ? 'text-emerald-400' : 'text-red-400'"
                    >{{ t.ok ? 'check_circle' : 'cancel' }}</span>
                    <div class="flex-1 min-w-0">
                      <div class="flex items-baseline justify-between gap-2">
                        <code class="text-slate-200 font-mono text-xs">{{ t.name }}</code>
                        <span
                          class="text-[11px] tabular-nums"
                          :class="t.ok ? 'text-emerald-400' : 'text-red-400'"
                        >HTTP {{ t.status }}</span>
                      </div>
                      <p
                        class="text-xs mt-1 break-words"
                        :class="t.ok ? 'text-slate-400' : 'text-red-300/80'"
                      >{{ t.message }}</p>
                    </div>
                  </li>
                </ul>

                <!-- Hints -->
                <div v-if="testResult.hints?.length" class="space-y-1 px-3 py-2 rounded-lg border border-amber-500/30 bg-amber-500/5">
                  <div class="text-amber-300 text-xs font-medium flex items-center gap-1.5 mb-1">
                    <span class="material-symbols-outlined text-sm">lightbulb</span>
                    Hints
                  </div>
                  <p
                    v-for="(h, i) in testResult.hints"
                    :key="i"
                    class="text-xs text-amber-200/80 leading-relaxed"
                  >{{ h }}</p>
                </div>
              </div>

              <!-- Initial / no result -->
              <div v-else class="text-slate-400 text-sm py-4">
                Click "Test" to run the probe.
              </div>
            </div>

            <div class="px-6 py-3 border-t border-border-subtle flex justify-end gap-2">
              <button
                @click="showTestPopup = false"
                class="py-2 px-4 rounded-lg border border-border-subtle text-sm text-slate-300 hover:bg-slate-700/30 transition-colors"
              >
                Close
              </button>
              <!-- R16 phase-2: when the popup was triggered by a failed
                   pre-save test, offer a "Save Anyway" path that calls
                   submitLink with bypassTest=true so the user can override
                   the gate (e.g. transient Alpaca outage, or knowingly
                   saving creds for a feed they'll subscribe to later). -->
              <button
                v-if="activeTab === 'alpaca' && testResult && !testResult.ok && !testRunning"
                @click="showTestPopup = false; submitLink({ bypassTest: true })"
                :disabled="submitting"
                class="py-2 px-4 rounded-lg border border-amber-500/40 bg-amber-500/10 text-sm text-amber-300 hover:bg-amber-500/20 transition-colors disabled:opacity-50"
              >
                Save Anyway
              </button>
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to       { opacity: 0; }

.slide-up-enter-active, .slide-up-leave-active { transition: all 0.2s ease; }
.slide-up-enter-from, .slide-up-leave-to       { opacity: 0; transform: translateY(6px); }
</style>
