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
const activeTab   = ref('alpaca')
const submitting  = ref(false)
const submitMsg   = ref('')
const submitOk    = ref(false)

// Edit mode
const editMode    = ref(false)
const editId      = ref(null)

// Alpaca form
const alpacaForm  = ref({ account_name: '', key: '', secret: '', paper: true, data_feed: 'iex' })

// Kalshi form (RSA-PSS v2): key id + PEM private key + demo/live environment.
const kalshiForm  = ref({ account_name: '', key_id: '', private_key: '', environment: 'demo' })

// Binance.US form (spot 0.00% maker / 0.02% taker). Simpler than Alpaca — no
// data feed. `paper` = platform-simulated fills vs. live price (Binance.US has
// no native sandbox); un-checked = live signed MARKET orders (real money).
const binanceusForm = ref({ account_name: '', key: '', secret: '', paper: true })

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

// ── Modal ─────────────────────────────────────────────────────────────────────
function openModal() {
  editMode.value    = false
  editId.value      = null
  activeTab.value   = 'alpaca'
  submitMsg.value   = ''
  submitOk.value    = false
  submitting.value  = false
  alpacaForm.value  = { account_name: '', key: '', secret: '', paper: true, data_feed: 'iex' }
  kalshiForm.value  = { account_name: '', key_id: '', private_key: '', environment: 'demo' }
  binanceusForm.value = { account_name: '', key: '', secret: '', paper: true }
  showModal.value   = true
}

function openEditModal(acct) {
  editMode.value    = true
  editId.value      = acct.id
  activeTab.value   = acct.brokerage_type
  submitMsg.value   = ''
  submitOk.value    = false
  submitting.value  = false
  if (acct.brokerage_type === 'alpaca') {
    alpacaForm.value = { account_name: acct.account_name, key: acct.alpaca_key || '', secret: '', paper: acct.alpaca_paper ?? true, data_feed: acct.alpaca_data_feed || 'iex' }
  } else if (acct.brokerage_type === 'binanceus') {
    binanceusForm.value = { account_name: acct.account_name, key: acct.binanceus_key || '', secret: '', paper: acct.alpaca_paper ?? true }
  } else {
    submitMsg.value = `Unsupported brokerage type: ${acct.brokerage_type}`
    submitOk.value = false
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
        submitMsg.value = 'Credential test failed — review the results. Click "Save anyway" to bypass.'
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
    } else if (activeTab.value === 'kalshi') {
      const f = kalshiForm.value
      if (!f.account_name.trim()) throw new Error('Account name is required')
      if (!editMode.value && !f.key_id.trim()) throw new Error('Kalshi API key ID is required')
      if (!editMode.value && !f.private_key.trim()) throw new Error('Kalshi RSA private key (PEM) is required')
      body = {
        brokerage_type: 'kalshi',
        account_name: f.account_name.trim(),
        kalshi_key_id: f.key_id.trim(),
        kalshi_private_key: f.private_key,
        kalshi_environment: f.environment || 'demo',
      }
    } else if (activeTab.value === 'binanceus') {
      const f = binanceusForm.value
      if (!f.account_name.trim()) throw new Error('Account name is required')
      if (!editMode.value && !f.key.trim())    throw new Error('API Key is required')
      if (!editMode.value && !f.secret.trim()) throw new Error('Secret Key is required')
      body = editMode.value
        ? {
            account_name: f.account_name.trim() || undefined,
            key:          f.key.trim()    || undefined,
            secret:       f.secret.trim() || undefined,
            paper:        f.paper,
          }
        : {
            brokerage_type: 'binanceus',
            account_name:   f.account_name.trim(),
            key:            f.key.trim(),
            secret:         f.secret.trim(),
            paper:          f.paper,
          }
    } else {
      throw new Error(`Unsupported brokerage type: ${activeTab.value}`)
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
        submitMsg.value = `Account updated. Data access verified (feed=${_da.feed}, ${_da.bars ?? 0} bar(s) fetched).`
        submitOk.value = true
      } else if (_da?.warning) {
        submitMsg.value = `Account updated. Data access could not be verified (${_da.warning}); will retry at broker boot.`
        submitOk.value = true
      }
      submitOk.value  = true
      submitMsg.value = `Account "${data.account?.account_name}" updated.`
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
    submitMsg.value = `Account "${data.account?.account_name}" linked.${_suffix}`
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
// Status is a dot plus a word: green for a live connection, red for expired
// credentials, amber for anything still settling.
function statusColor(status) {
  if (status === 'active')  return 'text-slate-200'
  if (status === 'expired') return 'text-slate-200'
  return 'text-slate-300'
}

function statusDot(status) {
  if (status === 'active')  return 'ui-dot-live'
  if (status === 'expired') return 'ui-dot-error'
  return 'ui-dot-warn'
}

onMounted(fetchAccounts)
</script>

<template>
  <AppShell>
    <main class="ui-page">

      <!-- Header -->
      <header class="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <div class="min-w-0">
          <h1 class="ui-page-title">Brokerages</h1>
          <p class="ui-page-sub">Linked accounts the instances trade through</p>
        </div>
        <button @click="openModal" class="ui-btn-primary">Link brokerage</button>
      </header>

      <section class="mt-8">
        <!-- Loading -->
        <div v-if="loading" class="ui-card overflow-hidden" aria-label="Loading accounts">
          <div class="hidden xl:block h-10 border-b border-white/[0.06]"></div>
          <div class="ui-rows">
            <div v-for="n in 3" :key="n" class="h-16 px-5 flex flex-col justify-center gap-2">
              <div class="ui-skel h-4 w-40"></div>
              <div class="ui-skel h-3 w-24"></div>
            </div>
          </div>
        </div>

        <!-- Error -->
        <div v-else-if="loadError" class="ui-error">
          {{ loadError }}
        </div>

        <!-- Empty state -->
        <div v-else-if="accounts.length === 0" class="ui-card">
          <div class="ui-empty">
            <p>No brokerage linked yet. Link an Alpaca account to start stock trading.</p>
            <button @click="openModal" class="ui-btn-primary">Link brokerage</button>
          </div>
        </div>

        <!-- Account list -->
        <div v-else class="ui-card overflow-hidden">
          <div class="bk-head hidden xl:grid ui-th">
            <span>Account</span>
            <span>Status</span>
            <span>Key</span>
            <span>Account number</span>
            <span>Last refreshed</span>
            <span></span>
          </div>

          <ul class="ui-rows">
            <li
              v-for="acct in accounts"
              :key="acct.id"
              class="bk-row px-5 py-3 min-h-16 transition-colors duration-150 ease-out hover:bg-white/[0.02]"
            >
              <!-- Name and kind -->
              <div class="bk-name min-w-0">
                <p class="text-sm text-slate-100 truncate" :title="acct.account_name">{{ acct.account_name }}</p>
                <p class="text-xs text-slate-500 mt-0.5">
                  {{ acct.brokerage_type === 'binanceus' ? 'Binance.US' : fmtType(acct.brokerage_type) }}
                  <span v-if="(acct.brokerage_type === 'alpaca' || acct.brokerage_type === 'binanceus') && acct.alpaca_paper"> · Paper</span>
                  <span v-if="(acct.brokerage_type === 'alpaca' || acct.brokerage_type === 'binanceus') && !acct.alpaca_paper"> · Live</span>
                </p>
              </div>

              <!-- Status -->
              <span class="bk-status inline-flex items-center gap-2 text-sm" :class="statusColor(acct.status)">
                <span class="ui-dot" :class="statusDot(acct.status)"></span>
                {{ fmtType(acct.status) }}
              </span>

              <!-- Details: a line under the name on small screens, columns on wide ones -->
              <div class="bk-details">
                <span class="bk-key" :class="(acct.brokerage_type === 'alpaca' || (acct.brokerage_type === 'binanceus' && acct.binanceus_key)) ? '' : 'bk-empty'">
                  <span class="xl:hidden text-slate-500">Key </span>
                  <span class="font-mono text-slate-400">
                    <template v-if="acct.brokerage_type === 'alpaca'">{{ acct.alpaca_key }}</template>
                    <template v-else-if="acct.brokerage_type === 'binanceus' && acct.binanceus_key">{{ acct.binanceus_key }}</template>
                    <template v-else>—</template>
                  </span>
                </span>
                <span class="bk-acct" :class="acct.brokerage_type === 'alpaca' && acct.alpaca_account_number ? '' : 'bk-empty'">
                  <span class="xl:hidden text-slate-500">Account </span>
                  <span class="text-slate-400 tabular-nums">{{ (acct.brokerage_type === 'alpaca' && acct.alpaca_account_number) || '—' }}</span>
                </span>
                <span class="bk-refresh" :class="acct.last_refresh_at ? '' : 'bk-empty'">
                  <span class="xl:hidden text-slate-500">Refreshed </span>
                  <span class="text-slate-500 tabular-nums">{{ acct.last_refresh_at ? new Date(acct.last_refresh_at).toLocaleString() : '—' }}</span>
                </span>
              </div>

              <!-- Actions -->
              <div class="bk-actions flex items-center gap-1">
                <button @click="openEditModal(acct)" class="ui-btn">Edit</button>
                <button
                  @click="deleteAccount(acct.id, acct.account_name)"
                  class="ui-btn-ghost hover:text-loss"
                >
                  Remove
                </button>
              </div>

              <p v-if="acct.last_error" class="bk-error text-xs text-loss break-words">
                Error: {{ acct.last_error }}
              </p>
            </li>
          </ul>
        </div>
      </section>

    </main>
  </AppShell>

  <!-- ── Link Brokerage Modal ──────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showModal"
        class="ui-overlay"
        @click.self="closeModal"
      >
        <div class="modal-panel ui-modal max-w-md flex flex-col max-h-[90vh]">

          <!-- Modal header -->
          <div class="ui-modal-head shrink-0">
            <h2 class="text-base font-medium text-slate-100">{{ editMode ? 'Edit brokerage account' : 'Link brokerage account' }}</h2>
            <button @click="closeModal" class="ui-icon-btn ml-auto shrink-0" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="ui-modal-body flex-1 overflow-y-auto space-y-5">

            <!-- Brokerage selector -->
            <div v-if="!editMode" class="ui-seg w-full" role="group" aria-label="Brokerage">
              <button
                @click="setTab('alpaca')"
                class="ui-seg-btn flex-1 h-8 text-sm"
                :class="activeTab === 'alpaca' ? 'ui-seg-btn-active' : ''"
              >
                Alpaca
              </button>
              <button
                @click="setTab('kalshi')"
                class="ui-seg-btn flex-1 h-8 text-sm"
                :class="activeTab === 'kalshi' ? 'ui-seg-btn-active' : ''"
              >
                Kalshi
              </button>
              <button
                @click="setTab('binanceus')"
                class="ui-seg-btn flex-1 h-8 text-sm"
                :class="activeTab === 'binanceus' ? 'ui-seg-btn-active' : ''"
              >
                Binance.US
              </button>
            </div>

            <!-- ── Alpaca fields ── -->
            <template v-if="activeTab === 'alpaca'">
              <div>
                <label class="ui-field-label">Account name</label>
                <input v-model="alpacaForm.account_name" type="text" placeholder="e.g. My paper trading"
                  class="ui-input" />
              </div>
              <div>
                <label class="ui-field-label">API key ID</label>
                <input v-model="alpacaForm.key" type="text" placeholder="PKXXXXXXXXXXXXXXXXXXXXXXXX"
                  class="ui-input font-mono" />
              </div>
              <div>
                <label class="ui-field-label">Secret key<span v-if="editMode" class="text-slate-500 ml-1">(leave blank to keep existing)</span></label>
                <input v-model="alpacaForm.secret" type="password"
                  :placeholder="editMode ? 'Leave blank to keep existing secret' : '••••••••••••••••••••••••••••••••••••••••'"
                  class="ui-input font-mono" />
              </div>
              <div class="flex items-center gap-3">
                <button type="button" @click="alpacaForm.paper = !alpacaForm.paper"
                  class="switch"
                  :class="alpacaForm.paper ? 'switch-on' : ''"
                  role="switch"
                  :aria-checked="alpacaForm.paper"
                  aria-label="Paper trading">
                  <span class="switch-knob"></span>
                </button>
                <span class="text-sm text-slate-300">
                  Paper trading
                  <span class="text-slate-500 text-xs ml-1">({{ alpacaForm.paper ? 'paper-api.alpaca.markets' : 'api.alpaca.markets' }})</span>
                </span>
              </div>
              <div>
                <label class="ui-field-label">
                  Market data feed
                  <span class="text-slate-500 ml-1">(validated on save via data.alpaca.markets)</span>
                </label>
                <select v-model="alpacaForm.data_feed" class="ui-input">
                  <option value="iex">IEX (free — Basic Market Data)</option>
                  <option value="sip">SIP (paid — Algo Trader Plus)</option>
                </select>
                <p class="ui-help leading-relaxed">
                  Paper accounts have free IEX access. Live (funded) accounts must explicitly
                  subscribe to "Basic Market Data" on the Alpaca dashboard for IEX, or
                  "Algo Trader Plus" ($99/mo) for SIP. Save fails with a clear error if the
                  chosen feed isn't authorized.
                </p>
              </div>
            </template>

            <!-- ── Kalshi fields ── -->
            <template v-else-if="activeTab === 'kalshi'">
              <div>
                <label class="ui-field-label">Account name</label>
                <input v-model="kalshiForm.account_name" type="text" placeholder="e.g. Kalshi live"
                  class="ui-input" />
              </div>
              <div>
                <label class="ui-field-label">API key ID</label>
                <input v-model="kalshiForm.key_id" type="text" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                  class="ui-input font-mono" />
              </div>
              <div>
                <label class="ui-field-label">RSA private key (PEM)</label>
                <textarea v-model="kalshiForm.private_key" rows="5" placeholder="-----BEGIN PRIVATE KEY-----&#10;...&#10;-----END PRIVATE KEY-----"
                  class="ui-input text-xs font-mono"></textarea>
                <p class="ui-help">Stored encrypted at rest. Used only for RSA-PSS request signing.</p>
              </div>
              <div>
                <label class="ui-field-label">Environment</label>
                <select v-model="kalshiForm.environment" class="ui-input">
                  <option value="demo">Demo (paper · demo-api.kalshi.co)</option>
                  <option value="live">Live (real money)</option>
                </select>
                <p v-if="kalshiForm.environment === 'live'" class="mt-2 flex items-start gap-2 text-xs text-slate-300">
                  <span class="ui-dot ui-dot-warn mt-1.5"></span>
                  <span>Live account — instances bound here trade real money.</span>
                </p>
              </div>
            </template>

            <!-- ── Binance.US fields ── -->
            <template v-else-if="activeTab === 'binanceus'">
              <div class="rounded-lg border border-white/[0.07] px-4 py-3 text-xs text-slate-400 leading-relaxed">
                Spot fees: <span class="text-slate-200 tabular-nums">0.00% maker / 0.02% taker</span> — ~12× cheaper than
                Alpaca crypto (0.25%), which is what makes high-frequency strategies viable.
                Create a read+trade API key at binance.us (no withdrawal permission needed).
              </div>
              <div>
                <label class="ui-field-label">Account name</label>
                <input v-model="binanceusForm.account_name" type="text" placeholder="e.g. Binance.US paper"
                  class="ui-input" />
              </div>
              <div>
                <label class="ui-field-label">API key</label>
                <input v-model="binanceusForm.key" type="text" placeholder="Binance.US API key"
                  class="ui-input font-mono" />
              </div>
              <div>
                <label class="ui-field-label">Secret key<span v-if="editMode" class="text-slate-500 ml-1">(leave blank to keep existing)</span></label>
                <input v-model="binanceusForm.secret" type="password"
                  :placeholder="editMode ? 'Leave blank to keep existing secret' : '••••••••••••••••••••••••••••••••••••••••'"
                  class="ui-input font-mono" />
              </div>
              <div class="flex items-center gap-3">
                <button type="button" @click="binanceusForm.paper = !binanceusForm.paper"
                  class="switch"
                  :class="binanceusForm.paper ? 'switch-on' : ''"
                  role="switch"
                  :aria-checked="binanceusForm.paper"
                  aria-label="Paper trading">
                  <span class="switch-knob"></span>
                </button>
                <span class="text-sm text-slate-300">
                  Paper trading
                  <span class="text-slate-500 text-xs ml-1">({{ binanceusForm.paper ? 'simulated fills vs. live price' : 'live signed orders · real money' }})</span>
                </span>
              </div>
              <p v-if="!binanceusForm.paper" class="flex items-start gap-2 text-xs text-slate-300 leading-relaxed">
                <span class="ui-dot ui-dot-warn mt-1.5"></span>
                <span>Live account — instances bound here place real Binance.US MARKET orders with real funds.</span>
              </p>
            </template>

            <!-- Status message -->
            <Transition name="slide-up">
              <div
                v-if="submitMsg"
                class="msg"
                :class="submitting && !submitOk ? 'msg-busy' : submitOk ? 'msg-ok' : 'msg-err'"
              >
                <span v-if="submitting && !submitOk" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                  {{ submitMsg }}
                </span>
                <span v-else>{{ submitMsg }}</span>
              </div>
            </Transition>
          </div>

          <!-- Modal footer -->
          <div class="ui-modal-foot shrink-0">
            <button
              @click="closeModal"
              :disabled="submitting"
              class="ui-btn-ghost h-9 px-4 rounded-lg"
            >
              Cancel
            </button>

            <!-- Alpaca: Test Connection (R16) -->
            <button
              v-if="activeTab === 'alpaca'"
              @click="runAlpacaTestSuite"
              :disabled="submitting || testRunning"
              class="ui-btn h-9 px-4"
              title="Run a 5-endpoint probe (account, clock, bars, latest quote, news) against Alpaca with these credentials. Read-only; does not save."
            >
              <span v-if="testRunning" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ testRunning ? 'Testing...' : 'Test' }}
            </button>

            <!-- Alpaca / Kalshi / Binance.US: Link / Save Account -->
            <button
              v-if="activeTab === 'alpaca' || activeTab === 'kalshi' || activeTab === 'binanceus'"
              @click="submitLink"
              :disabled="submitting"
              class="ui-btn-primary"
            >
              <span v-if="submitting" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ submitting ? (editMode ? 'Saving...' : 'Linking...') : (editMode ? 'Save changes' : 'Link account') }}
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
        class="ui-overlay z-[60]"
        @click.self="showTestPopup = false"
      >
        <Transition name="slide-up" appear>
          <div
            v-if="showTestPopup"
            class="ui-modal max-w-lg flex flex-col max-h-[85vh]"
          >
            <div class="ui-modal-head shrink-0">
              <h3 class="text-base font-medium text-slate-100">Alpaca connection test</h3>
              <button
                @click="showTestPopup = false"
                class="ui-icon-btn ml-auto shrink-0"
                aria-label="Close test results"
              >
                <span class="material-symbols-outlined text-[18px]">close</span>
              </button>
            </div>

            <div class="ui-modal-body flex-1 overflow-y-auto">
              <!-- Loading -->
              <div v-if="testRunning" class="flex items-center gap-3 py-6 text-slate-400 text-sm">
                <span class="material-symbols-outlined text-[18px] animate-spin">progress_activity</span>
                Running 5-endpoint probe against Alpaca...
              </div>

              <!-- Results -->
              <div v-else-if="testResult">
                <!-- Summary banner — three states: passed / failed / not-run.
                     The not-run state (total=0) is what shows when the user
                     hits Test in edit mode without re-pasting the secret;
                     showing "0 of 0 tests failed" was misleading, so route
                     that case to a neutral "Probe could not run" line with
                     a warning dot and rely on the Hints panel below. -->
                <div
                  v-if="(testResult.summary?.total ?? 0) === 0"
                  class="mb-4 flex items-center gap-2 text-sm text-slate-200"
                >
                  <span class="ui-dot ui-dot-warn"></span>
                  <span>Probe could not run <span class="text-slate-500">— see hints below</span></span>
                </div>
                <div
                  v-else
                  class="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm"
                >
                  <span class="ui-dot" :class="testResult.ok ? 'bg-gain' : 'ui-dot-error'"></span>
                  <span class="tabular-nums" :class="testResult.ok ? 'text-gain' : 'text-loss'">
                    {{ testResult.ok
                      ? `All ${testResult.summary.total} tests passed`
                      : `${testResult.summary.failed} of ${testResult.summary.total} tests failed` }}
                  </span>
                  <span v-if="testResult.feed" class="text-xs text-slate-500">feed={{ testResult.feed }}, paper={{ testResult.paper }}</span>
                </div>

                <!-- Per-test rows -->
                <ul v-if="testResult.tests?.length" class="mb-4 rounded-lg border border-white/[0.07] divide-y divide-white/[0.05]">
                  <li
                    v-for="t in testResult.tests"
                    :key="t.name"
                    class="px-4 py-3 flex items-start gap-3"
                  >
                    <span class="ui-dot mt-1.5" :class="t.ok ? 'bg-gain' : 'ui-dot-error'"></span>
                    <div class="flex-1 min-w-0">
                      <div class="flex items-baseline justify-between gap-2">
                        <code class="text-slate-200 font-mono text-xs">{{ t.name }}</code>
                        <span
                          class="text-xs tabular-nums"
                          :class="t.ok ? 'text-slate-500' : 'text-loss'"
                        >HTTP {{ t.status }}</span>
                      </div>
                      <p
                        class="text-xs mt-1 break-words"
                        :class="t.ok ? 'text-slate-400' : 'text-loss'"
                      >{{ t.message }}</p>
                    </div>
                  </li>
                </ul>

                <!-- Hints -->
                <div v-if="testResult.hints?.length" class="rounded-lg border border-white/[0.07] px-4 py-3 space-y-1">
                  <div class="text-xs text-slate-500 mb-1">Hints</div>
                  <p
                    v-for="(h, i) in testResult.hints"
                    :key="i"
                    class="text-xs text-slate-300 leading-relaxed"
                  >{{ h }}</p>
                </div>
              </div>

              <!-- Initial / no result -->
              <div v-else class="text-slate-400 text-sm py-4">
                Click Test to run the probe.
              </div>
            </div>

            <div class="ui-modal-foot shrink-0">
              <button
                @click="showTestPopup = false"
                class="ui-btn-ghost h-9 px-4 rounded-lg"
              >
                Close
              </button>
              <!-- R16 phase-2: when the popup was triggered by a failed
                   pre-save test, offer a "Save anyway" path that calls
                   submitLink with bypassTest=true so the user can override
                   the gate (e.g. transient Alpaca outage, or knowingly
                   saving creds for a feed they'll subscribe to later). -->
              <button
                v-if="activeTab === 'alpaca' && testResult && !testResult.ok && !testRunning"
                @click="showTestPopup = false; submitLink({ bypassTest: true })"
                :disabled="submitting"
                class="ui-btn h-9 px-4"
              >
                Save anyway
              </button>
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* Account rows. Small screens: name · status (· actions from sm), then the
   details as one wrapping line. Wide screens: a column per field. */
.bk-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas:
    "name status"
    "details details"
    "error error"
    "actions actions";
  column-gap: 16px;
  align-items: center;
}
.bk-name { grid-area: name; }
.bk-status { grid-area: status; }
.bk-details {
  grid-area: details;
  display: flex;
  flex-wrap: wrap;
  column-gap: 16px;
  row-gap: 4px;
  margin-top: 8px;
  font-size: 12px;
  line-height: 16px;
}
.bk-empty { display: none; }
.bk-error { grid-area: error; margin-top: 8px; }
.bk-actions { grid-area: actions; margin-top: 12px; }

@media (min-width: 640px) {
  .bk-row {
    grid-template-columns: minmax(0, 1fr) auto auto;
    grid-template-areas:
      "name status actions"
      "details details details"
      "error error error";
  }
  .bk-actions { margin-top: 0; justify-content: flex-end; }
}

@media (min-width: 1280px) {
  .bk-row,
  .bk-head {
    grid-template-columns: minmax(0, 1.3fr) 104px minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1.2fr) 148px;
    column-gap: 16px;
  }
  .bk-row {
    grid-template-areas:
      "name status key acct refresh actions"
      "error error error error error error";
  }
  .bk-details { display: contents; }
  .bk-key { grid-area: key; }
  .bk-acct { grid-area: acct; }
  .bk-refresh { grid-area: refresh; }
  .bk-empty { display: inline; }
  .bk-error { margin-top: 4px; }
}

/* Modal status line: busy is neutral, success gain, failure loss. */
.msg { @apply rounded-lg border px-4 py-3 text-sm break-words; }
.msg-busy { @apply border-white/[0.07] bg-raised text-slate-300; }
.msg-ok { @apply border-gain/20 bg-gain/5 text-gain; }
.msg-err { @apply border-loss/20 bg-loss/5 text-loss; }

/* On/off switch: off is a raised track, on is the off-white primary. */
.switch {
  @apply relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-white/[0.08] bg-raised
    transition-colors duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/25;
}
.switch-on { @apply bg-slate-200 border-slate-200; }
.switch-knob { @apply inline-block size-3.5 translate-x-0.5 rounded-full bg-slate-500 transition-transform duration-150 ease-out; }
.switch-on .switch-knob { @apply translate-x-[18px] bg-background-dark; }

.fade-enter-active, .fade-leave-active { transition: opacity 180ms cubic-bezier(0.22, 1, 0.36, 1); }
.fade-enter-from, .fade-leave-to       { opacity: 0; }
.fade-enter-active .modal-panel, .fade-leave-active .modal-panel { transition: transform 220ms cubic-bezier(0.22, 1, 0.36, 1); }
.fade-enter-from .modal-panel, .fade-leave-to .modal-panel { transform: translateY(6px); }

.slide-up-enter-active, .slide-up-leave-active { transition: opacity 150ms cubic-bezier(0.22, 1, 0.36, 1), transform 150ms cubic-bezier(0.22, 1, 0.36, 1); }
.slide-up-enter-from, .slide-up-leave-to       { opacity: 0; transform: translateY(6px); }

@media (prefers-reduced-motion: reduce) {
  .fade-enter-active, .fade-leave-active,
  .fade-enter-active .modal-panel, .fade-leave-active .modal-panel,
  .slide-up-enter-active, .slide-up-leave-active { transition: none; }
}
</style>
