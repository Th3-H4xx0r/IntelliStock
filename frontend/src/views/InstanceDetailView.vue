<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import { getToken } from '../utils/auth.js'
import {
  applyStrategyLlmDraft,
  buildStrategyLlmTestPayload,
  buildStrategyLlmDraft,
  describeStrategyLlmGroup,
  getLlmProviderLabel,
  getStrategyConfigFieldMeta,
  getStrategyLlmConfigGroups,
  getStrategyLookbackLlmConfigGroups,
  LLM_PROVIDER_OPTIONS,
  LLM_REASONING_EFFORT_OPTIONS,
  NVIDIA_REASONING_EFFORT_OPTIONS,
  shouldHideStrategyConfigField,
  isLlmManagedConfigField,
} from '../utils/strategyConfig.js'

const route  = useRoute()
const router = useRouter()
const instanceId = computed(() => route.params.id)

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

// ── State ─────────────────────────────────────────────────────────────────────
const inst    = ref(null)
const loading = ref(true)
const error   = ref('')
const busy    = ref(false)

// Backtest history with pagination
const allBacktests  = ref([])
const btPage        = ref(1)
const btPerPage     = 15
const btTotal       = ref(0)
const btTotalPages  = ref(0)
const btLoading     = ref(false)
const btSortBy      = ref('completed_at')
const btSortOrder   = ref('desc')

// Live uptime ticker
let uptimeTimer = null
const liveSecs  = ref(0)

function authHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

// ── Data ──────────────────────────────────────────────────────────────────────
async function fetchInstance() {
  loading.value = true
  error.value   = ''
  try {
    const res  = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId.value)}`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    inst.value = await res.json()
    liveSecs.value = inst.value.uptime_seconds || 0
    startUptimeTicker()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function fetchBacktests(page = btPage.value) {
  btLoading.value = true
  try {
    const params = new URLSearchParams({
      page: String(page),
      per_page: String(btPerPage),
      sort_by: btSortBy.value,
      sort_order: btSortOrder.value,
    })
    const res = await fetch(
      `${API_BASE}/instances/${encodeURIComponent(instanceId.value)}/backtests?${params.toString()}`,
      { headers: authHeaders() }
    )
    if (!res.ok) return
    const data = await res.json()
    allBacktests.value = data.backtests || []
    btTotal.value      = data.total ?? 0
    btTotalPages.value = data.total_pages ?? 1
    btPage.value       = data.page ?? page
  } catch { /* non-critical */ } finally {
    btLoading.value = false
  }
}

function startUptimeTicker() {
  clearInterval(uptimeTimer)
  if (inst.value?.runCommand) {
    uptimeTimer = setInterval(() => { liveSecs.value++ }, 1000)
  }
}

onMounted(async () => {
  await fetchInstance()
  await fetchBacktests(1)
  startBtPolling()
})
onUnmounted(() => {
  clearInterval(uptimeTimer)
  stopBtPolling()
})

// ── Actions ───────────────────────────────────────────────────────────────────
async function toggleRun() {
  if (!inst.value) return
  busy.value = true
  const action = inst.value.runCommand ? 'stop' : 'start'
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId.value)}/${action}`, {
      method: 'POST', headers: authHeaders(),
    })
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${res.status}`) }
    await fetchInstance()
  } catch (e) {
    alert(`${action} failed: ${e.message}`)
  } finally {
    busy.value = false
  }
}

// ── Formatting ────────────────────────────────────────────────────────────────
const showEditInfo   = ref(false)
const editInfoSaving = ref(false)
const editInfoMsg    = ref('')
const editInfoOk     = ref(false)
const editInfoForm   = ref({
  name: '',
  granularity: '60',
  max_usage: '',
})

function openEditInfo() {
  if (!inst.value) return
  editInfoMsg.value = ''
  editInfoOk.value = false
  editInfoSaving.value = false
  editInfoForm.value = {
    name: inst.value.name || '',
    granularity: String(inst.value.granularity_time_increment || '60'),
    max_usage: inst.value.max_usage != null ? String(inst.value.max_usage) : '',
  }
  showEditInfo.value = true
}

function closeEditInfo() {
  if (editInfoSaving.value) return
  showEditInfo.value = false
}

// ── Clear instance state (destructive — modal-gated) ────────────────────────
// One modal handles three scopes (lookback_only / strategy_cache_only /
// full_instance). Workflow: pick scope → click Preview → review per-table
// counts from the dry-run endpoint → type the instance id to unlock
// "Confirm and clear" → second POST with apply=true.
const showClearState = ref(false)
const clearScope     = ref('lookback_only')
const clearPreview   = ref(null)       // dry-run response from the API
const clearPreviewLoading = ref(false)
const clearApplying  = ref(false)
const clearConfirm   = ref('')         // operator types instance_id here
const clearMsg       = ref('')
const clearOk        = ref(false)
const clearResult    = ref(null)       // apply response

function openClearState() {
  clearScope.value = 'lookback_only'
  clearPreview.value = null
  clearConfirm.value = ''
  clearMsg.value = ''
  clearOk.value = false
  clearResult.value = null
  clearPreviewLoading.value = false
  clearApplying.value = false
  showClearState.value = true
}

function closeClearState() {
  if (clearApplying.value || clearPreviewLoading.value) return
  showClearState.value = false
}

const clearScopeOptions = [
  {
    value: 'lookback_only',
    label: 'Lookback only',
    blurb: 'GraphNexusTradeContexts + GraphNexusOutcomes. Forces lookback re-build on next run; other instance state untouched.',
  },
  {
    value: 'strategy_cache_only',
    label: 'DB strategy cache only',
    blurb: 'NexusStrategyCache rows for this instance (non-backtest origin). Backtest snapshots preserved.',
  },
  {
    value: 'full_instance',
    label: 'Full instance reset',
    blurb: 'Every per-instance table — lookback, runtime state, discovery, market trends, rotation cooldown, learning cache, trade outcomes, analyst panel, strategy cache (non-backtest). Shared caches (article/sentiment/finbert/Benzinga/Neo4j) and backtest snapshots preserved.',
  },
]

const clearConfirmOk = computed(() => {
  return (clearConfirm.value || '').trim() === String(route?.params?.id || '').trim()
})

async function previewClearState() {
  if (!route?.params?.id) return
  clearPreviewLoading.value = true
  clearMsg.value = ''
  clearOk.value = false
  clearPreview.value = null
  clearResult.value = null
  try {
    const res = await fetch(`${API_BASE}/instances/${route.params.id}/clear-state`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ scope: clearScope.value, apply: false }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data?.detail || data?.error || `HTTP ${res.status}`)
    clearPreview.value = data
  } catch (e) {
    clearMsg.value = `Preview failed: ${e.message}`
    clearOk.value = false
  } finally {
    clearPreviewLoading.value = false
  }
}

async function applyClearState() {
  if (!route?.params?.id) return
  if (!clearConfirmOk.value) {
    clearMsg.value = `Type "${route.params.id}" to confirm.`
    clearOk.value = false
    return
  }
  clearApplying.value = true
  clearMsg.value = ''
  clearOk.value = false
  try {
    const res = await fetch(`${API_BASE}/instances/${route.params.id}/clear-state`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({
        scope: clearScope.value,
        apply: true,
        confirm: String(route.params.id),
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data?.detail || data?.error || `HTTP ${res.status}`)
    clearResult.value = data
    clearOk.value = true
    clearMsg.value = `Deleted ${data.total_deleted} row(s) across ${data.tables.length} table(s).`
  } catch (e) {
    clearMsg.value = `Clear failed: ${e.message}`
    clearOk.value = false
  } finally {
    clearApplying.value = false
  }
}

async function submitEditInfo() {
  if (!inst.value) return
  editInfoSaving.value = true
  editInfoOk.value = false
  editInfoMsg.value = 'Saving...'
  try {
    const payload = {
      name: editInfoForm.value.name,
      // v-model on <input type="number"> coerces typed input to Number;
      // API expects str. Stringify here so a typed custom value (e.g.
      // 1200) doesn't 422 on Optional[str] validation.
      granularity: String(editInfoForm.value.granularity ?? '60'),
    }
    const rawMaxUsage = editInfoForm.value.max_usage
    const maxUsageText = rawMaxUsage == null ? '' : String(rawMaxUsage).trim()
    if (maxUsageText !== '') {
      const parsedMaxUsage = Number(maxUsageText)
      if (!Number.isFinite(parsedMaxUsage)) throw new Error('Max usage must be a valid number')
      payload.max_usage = parsedMaxUsage
    }
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId.value)}`, {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify(payload),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    editInfoOk.value = true
    editInfoMsg.value = 'Instance info updated.'
    await fetchInstance()
    setTimeout(() => { showEditInfo.value = false }, 1000)
  } catch (e) {
    editInfoOk.value = false
    editInfoMsg.value = e.message || 'Something went wrong'
  } finally {
    editInfoSaving.value = false
  }
}

const showLinkBrokerage   = ref(false)
const linkBrokerageSaving = ref(false)
const linkBrokerageMsg    = ref('')
const linkBrokerageOk     = ref(false)
const brokerageOptions    = ref([])
const selectedBrokerageId = ref('')

async function openLinkBrokerage() {
  linkBrokerageSaving.value = false
  linkBrokerageMsg.value = ''
  linkBrokerageOk.value = false
  selectedBrokerageId.value = ''
  brokerageOptions.value = []
  showLinkBrokerage.value = true
  try {
    const res = await fetch(`${API_BASE}/brokerages`, { headers: authHeaders() })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    brokerageOptions.value = data.accounts || []
    if (brokerageOptions.value.length > 0) {
      const currentId = String(inst.value?.brokerage_id || '')
      const hasCurrent = currentId && brokerageOptions.value.some(a => String(a.id) === currentId)
      selectedBrokerageId.value = hasCurrent
        ? currentId
        : String(brokerageOptions.value[0].id || '')
    }
  } catch (e) {
    linkBrokerageMsg.value = e.message || 'Failed to load brokerages'
  }
}

function closeLinkBrokerage() {
  if (linkBrokerageSaving.value) return
  showLinkBrokerage.value = false
}

async function submitLinkBrokerage() {
  if (!selectedBrokerageId.value) {
    linkBrokerageOk.value = false
    linkBrokerageMsg.value = 'Select a brokerage account first.'
    return
  }
  linkBrokerageSaving.value = true
  linkBrokerageOk.value = false
  linkBrokerageMsg.value = 'Linking...'
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId.value)}`, {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify({ brokerage_id: selectedBrokerageId.value }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    linkBrokerageOk.value = true
    linkBrokerageMsg.value = 'Brokerage linked.'
    await fetchInstance()
    setTimeout(() => { showLinkBrokerage.value = false }, 900)
  } catch (e) {
    linkBrokerageOk.value = false
    linkBrokerageMsg.value = e.message || 'Failed to link brokerage'
  } finally {
    linkBrokerageSaving.value = false
  }
}

// V32 Phase 5: separate Alpaca brokerage for market-data API calls
// (bars, news). Useful when the trading brokerage is paper (no data sub).
const showLinkDataBrokerage   = ref(false)
const linkDataBrokerageSaving = ref(false)
const linkDataBrokerageMsg    = ref('')
const linkDataBrokerageOk     = ref(false)
const dataBrokerageOptions    = ref([])
const selectedDataBrokerageId = ref('')

async function openLinkDataBrokerage() {
  linkDataBrokerageSaving.value = false
  linkDataBrokerageMsg.value = ''
  linkDataBrokerageOk.value = false
  selectedDataBrokerageId.value = ''
  dataBrokerageOptions.value = []
  showLinkDataBrokerage.value = true
  try {
    const res = await fetch(`${API_BASE}/brokerages`, { headers: authHeaders() })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    // Only Alpaca accounts are valid data sources.
    dataBrokerageOptions.value = (data.accounts || []).filter(a => (a.brokerage_type || '').toLowerCase() === 'alpaca')
    if (dataBrokerageOptions.value.length > 0) {
      const currentId = String(inst.value?.alpaca_data_brokerage_id || '')
      const hasCurrent = currentId && dataBrokerageOptions.value.some(a => String(a.id) === currentId)
      selectedDataBrokerageId.value = hasCurrent
        ? currentId
        : String(dataBrokerageOptions.value[0].id || '')
    }
  } catch (e) {
    linkDataBrokerageMsg.value = e.message || 'Failed to load Alpaca accounts'
  }
}

function closeLinkDataBrokerage() {
  if (linkDataBrokerageSaving.value) return
  showLinkDataBrokerage.value = false
}

async function submitLinkDataBrokerage(clear = false) {
  if (!clear && !selectedDataBrokerageId.value) {
    linkDataBrokerageOk.value = false
    linkDataBrokerageMsg.value = 'Select an Alpaca account first.'
    return
  }
  linkDataBrokerageSaving.value = true
  linkDataBrokerageOk.value = false
  linkDataBrokerageMsg.value = clear ? 'Unlinking...' : 'Linking...'
  try {
    const body = clear ? { brokerage_id: null } : { brokerage_id: selectedDataBrokerageId.value }
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId.value)}/link-data-brokerage`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(body),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    linkDataBrokerageOk.value = true
    linkDataBrokerageMsg.value = clear ? 'Data source cleared.' : 'Data source linked.'
    await fetchInstance()
    setTimeout(() => { showLinkDataBrokerage.value = false }, 900)
  } catch (e) {
    linkDataBrokerageOk.value = false
    linkDataBrokerageMsg.value = e.message || 'Failed to update data source'
  } finally {
    linkDataBrokerageSaving.value = false
  }
}
// ── Link strategy ─────────────────────────────────────────────────────────────
const showLinkStrategy    = ref(false)
const linkStrategyId      = ref('')
const linkStrategyBusy    = ref(false)
const linkStrategyMsg     = ref('')
const linkStrategyOk      = ref(false)
const availStrategies     = ref([])

async function openLinkStrategy() {
  linkStrategyId.value  = ''
  linkStrategyMsg.value = ''
  linkStrategyOk.value  = false
  linkStrategyBusy.value = false
  showLinkStrategy.value = true
  if (!availStrategies.value.length) {
    try {
      const res = await fetch(`${API_BASE}/strategies`, { headers: authHeaders() })
      if (res.ok) { const d = await res.json(); availStrategies.value = d.strategies || [] }
    } catch { /* non-critical */ }
  }
}

function closeLinkStrategy() {
  if (linkStrategyBusy.value) return
  showLinkStrategy.value = false
}

async function submitLinkStrategy() {
  if (!linkStrategyId.value) { linkStrategyMsg.value = 'Select a strategy'; return }
  linkStrategyBusy.value = true
  linkStrategyMsg.value  = ''
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId.value)}/link-strategy`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ strategy_id: Number(linkStrategyId.value) }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    linkStrategyOk.value  = true
    linkStrategyMsg.value = 'Strategy linked.'
    await fetchInstance()
    setTimeout(() => { showLinkStrategy.value = false }, 900)
  } catch (e) {
    linkStrategyMsg.value = e.message || 'Failed to link strategy'
  } finally {
    linkStrategyBusy.value = false
  }
}

// ── Unlink strategy ───────────────────────────────────────────────────────────
const showUnlinkStrategy  = ref(false)
const unlinkStrategyBusy  = ref(false)
const unlinkStrategyMsg   = ref('')
const unlinkStrategyOk    = ref(false)

function openUnlinkStrategy() {
  unlinkStrategyMsg.value = ''
  unlinkStrategyOk.value  = false
  unlinkStrategyBusy.value = false
  showUnlinkStrategy.value = true
}
function closeUnlinkStrategy() {
  if (unlinkStrategyBusy.value) return
  showUnlinkStrategy.value = false
}
async function submitUnlinkStrategy() {
  unlinkStrategyBusy.value = true
  unlinkStrategyOk.value   = false
  unlinkStrategyMsg.value  = 'Unlinking...'
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId.value)}/unlink-strategy`, {
      method: 'POST', headers: authHeaders(),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    unlinkStrategyOk.value  = true
    unlinkStrategyMsg.value = 'Strategy unlinked.'
    await fetchInstance()
    setTimeout(() => { showUnlinkStrategy.value = false }, 900)
  } catch (e) {
    unlinkStrategyOk.value  = false
    unlinkStrategyMsg.value = e.message || 'Failed to unlink strategy'
  } finally {
    unlinkStrategyBusy.value = false
  }
}

// ── Unlink brokerage ──────────────────────────────────────────────────────────
const showUnlinkBrokerage  = ref(false)
const unlinkBrokerageBusy  = ref(false)
const unlinkBrokerageMsg   = ref('')
const unlinkBrokerageOk    = ref(false)

function openUnlinkBrokerage() {
  unlinkBrokerageMsg.value  = ''
  unlinkBrokerageOk.value   = false
  unlinkBrokerageBusy.value = false
  showUnlinkBrokerage.value = true
}
function closeUnlinkBrokerage() {
  if (unlinkBrokerageBusy.value) return
  showUnlinkBrokerage.value = false
}
async function submitUnlinkBrokerage() {
  unlinkBrokerageBusy.value = true
  unlinkBrokerageOk.value   = false
  unlinkBrokerageMsg.value  = 'Unlinking...'
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId.value)}`, {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify({ brokerage_id: '' }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    unlinkBrokerageOk.value  = true
    unlinkBrokerageMsg.value = 'Brokerage unlinked.'
    await fetchInstance()
    setTimeout(() => { showUnlinkBrokerage.value = false }, 900)
  } catch (e) {
    unlinkBrokerageOk.value  = false
    unlinkBrokerageMsg.value = e.message || 'Failed to unlink brokerage'
  } finally {
    unlinkBrokerageBusy.value = false
  }
}

function fmtUptime(secs) {
  if (!secs || secs < 0) return '—'
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = secs % 60
  if (h > 0) return `${h}h ${m}m ${s}s`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

function fmtGranularity(sec) {
  const n = parseInt(sec) || 60
  if (n < 60)    return `${n}s`
  if (n < 3600)  return `${n / 60}m`
  if (n < 86400) return `${n / 3600}h`
  return `${n / 86400}d`
}

function fmtElapsed(seconds) {
  const n = Number(seconds)
  if (!Number.isFinite(n) || n < 0) return '—'
  const total = Math.floor(n)
  const d = Math.floor(total / 86400)
  const h = Math.floor((total % 86400) / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m ${s}s`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

function fmtMoney(v) {
  if (v == null) return '—'
  return '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPnl(v) {
  if (v == null) return '—'
  const n = Number(v)
  return (n >= 0 ? '+' : '') + fmtMoney(n)
}

function pnlClass(v) {
  if (v == null) return 'text-slate-500'
  return Number(v) >= 0 ? 'text-emerald-400' : 'text-red-400'
}

const statusColor = {
  completed: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  finished:  'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  running:   'text-sky-400 bg-sky-500/10 border-sky-500/20',
  queued:    'text-amber-400 bg-amber-500/10 border-amber-500/20',
  stopped:   'text-red-400 bg-red-500/10 border-red-500/20',
  cancelled: 'text-red-400 bg-red-500/10 border-red-500/20',
  error:     'text-red-400 bg-red-500/10 border-red-500/20',
  failed:    'text-red-400 bg-red-500/10 border-red-500/20',
}

function btStatusClass(s) {
  const key = (s || '').toLowerCase()
  return statusColor[key] || 'text-slate-500 bg-slate-500/10 border-slate-700'
}

function btSortIcon(field) {
  if (btSortBy.value !== field) return 'unfold_more'
  return btSortOrder.value === 'asc' ? 'arrow_upward' : 'arrow_downward'
}

async function toggleBtSort(field) {
  if (btSortBy.value === field) {
    btSortOrder.value = btSortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    btSortBy.value = field
    btSortOrder.value = 'desc'
  }
  await fetchBacktests(1)
}

function fmtDateTime(v) {
  if (!v) return '—'
  const n = Number(v)
  const d = Number.isFinite(n) ? new Date(n < 1e12 ? n * 1000 : n) : new Date(v)
  if (isNaN(d.getTime())) return String(v)
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

// ── Backtest progress polling ──────────────────────────────────────────────────
const btProgress = ref({})   // { [id]: { status, progress } }
let btPollTimer = null

async function pollRunningBacktests() {
  const running = allBacktests.value.filter(b => {
    const s = (btProgress.value[b.id]?.status || b.status || '').toLowerCase()
    return s === 'running' || s === 'queued' || s === 'pending'
  })
  if (!running.length) { stopBtPolling(); return }
  await Promise.all(running.map(async b => {
    try {
      const res = await fetch(`${API_BASE}/backtests/${b.id}/status`, { headers: authHeaders() })
      if (!res.ok) return
      const d = await res.json()
      btProgress.value = { ...btProgress.value, [b.id]: d }
      if (['completed', 'finished', 'stopped', 'failed', 'error', 'cancelled'].includes((d.status || '').toLowerCase())) {
        await fetchBacktests()
      }
    } catch { /* non-critical */ }
  }))
}

function startBtPolling() {
  if (!btPollTimer) btPollTimer = setInterval(pollRunningBacktests, 3000)
}

function stopBtPolling() {
  if (btPollTimer) { clearInterval(btPollTimer); btPollTimer = null }
}

// ── Inline backtest controls ──────────────────────────────────────────────────
const showBtConfirm  = ref(false)
const btConfirmId    = ref(null)
const btConfirmAction = ref('')    // 'stop' | 'pause' | 'resume'
const btConfirmBusy  = ref(false)
const btConfirmMsg   = ref('')
const btConfirmOk    = ref(false)

const btConfirmMeta = computed(() => {
  switch (btConfirmAction.value) {
    case 'stop':   return { label: 'Stop',   color: 'red',    icon: 'stop_circle',  desc: 'This will permanently stop the backtest.' }
    case 'pause':  return { label: 'Pause',  color: 'violet', icon: 'pause_circle', desc: 'The backtest will be paused and can be resumed later.' }
    case 'resume': return { label: 'Resume', color: 'sky',    icon: 'play_circle',  desc: 'The backtest will continue from where it was paused.' }
    default:       return { label: '', color: 'slate', icon: 'help', desc: '' }
  }
})

function openBtConfirm(bt, action) {
  btConfirmId.value     = bt.id
  btConfirmAction.value = action
  btConfirmMsg.value    = ''
  btConfirmOk.value     = false
  btConfirmBusy.value   = false
  showBtConfirm.value   = true
}

function closeBtConfirm() {
  if (btConfirmBusy.value) return
  showBtConfirm.value = false
}

async function executeBtConfirm() {
  btConfirmBusy.value = true
  btConfirmMsg.value  = 'Working...'
  btConfirmOk.value   = false
  try {
    const res = await fetch(`${API_BASE}/backtests/${btConfirmId.value}/${btConfirmAction.value}`, {
      method: 'POST', headers: authHeaders(),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    btConfirmOk.value  = true
    btConfirmMsg.value = `${btConfirmMeta.value.label} successful.`
    // refresh status immediately
    const statusRes = await fetch(`${API_BASE}/backtests/${btConfirmId.value}/status`, { headers: authHeaders() })
    if (statusRes.ok) {
      const d = await statusRes.json()
      btProgress.value = { ...btProgress.value, [btConfirmId.value]: d }
    }
    await fetchBacktests()
    if (btConfirmAction.value === 'resume') startBtPolling()
    setTimeout(() => { showBtConfirm.value = false }, 1000)
  } catch (e) {
    btConfirmOk.value  = false
    btConfirmMsg.value = e.message || 'Something went wrong'
  } finally {
    btConfirmBusy.value = false
  }
}

// ── Edit Strategy modal ───────────────────────────────────────────────────────
const showEditStrategy    = ref(false)
const editStrategyLoading = ref(false)
const editStrategySaving  = ref(false)
const editStrategyMsg     = ref('')
const editStrategyOk      = ref(false)
const editStrategyData    = ref(null)
const editStrategyPickName = ref('')
const availableStrategies = ref([])
const configJsonDrafts = ref({})
const configJsonErrors = ref({})

const schemaMap = computed(() => {
  const m = {}
  for (const s of availableStrategies.value) {
    const keys = [s?.id, s?.name, s?.schema?.strategy].filter(Boolean)
    for (const key of keys) m[key] = s
  }
  return m
})

const availableStrategyOptions = computed(() =>
  availableStrategies.value
    .map(s => ({
      value: s?.schema?.strategy || s?.id || s?.name || '',
      label: s?.name || s?.schema?.strategy || s?.id || '',
      description: s?.description || '',
    }))
    .filter(s => s.value)
)

async function fetchAvailableStrategies() {
  if (availableStrategies.value.length) return
  try {
    const res = await fetch(`${API_BASE}/strategies/available`, { headers: authHeaders() })
    if (!res.ok) return
    const data = await res.json()
    availableStrategies.value = data.strategies || []
  } catch { /* non-critical */ }
}

function getStrategyMeta(strategyName) {
  return schemaMap.value[strategyName] || null
}

function strategyLabel(strategyName) {
  return getStrategyMeta(strategyName)?.name || strategyName
}

function strategyCanonicalId(strategyName) {
  const meta = getStrategyMeta(strategyName)
  return meta?.schema?.strategy || meta?.id || strategyName
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value ?? {}))
}

function mergeStrategyFields(config, conditions) {
  return { ...cloneJson(conditions || {}), ...cloneJson(config || {}) }
}

function resetConfigEditorState() {
  configJsonDrafts.value = {}
  configJsonErrors.value = {}
}

function buildSubStrategyDefaults(strategyName, executionPosition) {
  const meta = getStrategyMeta(strategyName)
  const schema = meta?.schema || {}
  const defaultWeight = Number(schema.weight)
  return {
    strategy: schema.strategy || meta?.id || meta?.name || strategyName,
    weight: Number.isFinite(defaultWeight) ? defaultWeight : 0.5,
    execution_position: executionPosition,
    decision_phase: schema.decision_phase || 'pre',
    execution_scope: schema.execution_scope || 'per_symbol',
    config: mergeStrategyFields(schema.config || {}, schema.conditions || {}),
    conditions: {},
  }
}

function hydrateSubStrategy(sub, index) {
  const base = buildSubStrategyDefaults(sub?.strategy, sub?.execution_position ?? index)
  const mergedConfig = mergeStrategyFields(sub?.config || {}, sub?.conditions || {})
  return {
    ...base,
    ...cloneJson(sub || {}),
    strategy: strategyCanonicalId(sub?.strategy || base.strategy),
    config: { ...(base.config || {}), ...mergedConfig },
    conditions: {},
  }
}

async function openEditStrategy() {
  if (!inst.value?.strategy_id) return
  resetConfigEditorState()
  editStrategyMsg.value     = ''
  editStrategyOk.value      = false
  editStrategyData.value    = null
  editStrategyLoading.value = true
  showEditStrategy.value    = true
  await fetchAvailableStrategies()
  editStrategyPickName.value = availableStrategyOptions.value[0]?.value || ''
  try {
    const res = await fetch(`${API_BASE}/strategies/${inst.value.strategy_id}`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    const normalized = JSON.parse(JSON.stringify(data.strategy || data))
    normalized.strategies = (normalized.strategies || []).map((sub, idx) => hydrateSubStrategy(sub, idx))
    editStrategyData.value = normalized
  } catch (e) {
    editStrategyMsg.value = `Failed to load strategy: ${e.message}`
  } finally {
    editStrategyLoading.value = false
  }
}

function closeEditStrategy() {
  if (editStrategySaving.value) return
  resetConfigEditorState()
  showEditStrategy.value = false
}

function getConfigType(refValue) {
  if (typeof refValue === 'boolean') return 'boolean'
  if (typeof refValue === 'number')  return 'number'
  if (refValue !== null && typeof refValue === 'object') return 'json'
  return 'string'
}

function getRefConfig(strategyName) {
  const schema = getStrategyMeta(strategyName)?.schema || {}
  return mergeStrategyFields(schema.config || {}, schema.conditions || {})
}

function updateConfigField(subStrat, key, val, type) {
  if (type === 'boolean') subStrat.config[key] = val
  else if (type === 'number') subStrat.config[key] = val === '' ? null : Number(val)
  else if (type === 'json') {
    const draftKey = getConfigDraftKey(subStrat, key)
    configJsonDrafts.value[draftKey] = val
    try {
      const parsed = String(val || '').trim() === '' ? {} : JSON.parse(val)
      subStrat.config[key] = parsed
      delete configJsonErrors.value[draftKey]
    } catch (err) {
      configJsonErrors.value[draftKey] = err?.message || 'Invalid JSON'
    }
  }
  else subStrat.config[key] = val
}

function getConfigDraftKey(subStrat, key) {
  return `${String(subStrat?.strategy || '')}::${String(subStrat?.execution_position ?? '')}::${String(key || '')}`
}

function getConfigFieldValue(subStrat, key, val) {
  const fieldType = getConfigType(getRefConfig(subStrat.strategy)[key] ?? val)
  if (fieldType !== 'json') return subStrat.config[key]
  const draftKey = getConfigDraftKey(subStrat, key)
  if (Object.prototype.hasOwnProperty.call(configJsonDrafts.value, draftKey)) {
    return configJsonDrafts.value[draftKey]
  }
  try {
    return JSON.stringify(subStrat.config[key] ?? val ?? {}, null, 2)
  } catch {
    return '{}'
  }
}

function syncConfigJsonDraft(subStrat, key, val) {
  const fieldType = getConfigType(getRefConfig(subStrat.strategy)[key] ?? val)
  if (fieldType !== 'json') return
  const draftKey = getConfigDraftKey(subStrat, key)
  if (configJsonErrors.value[draftKey]) return
  try {
    configJsonDrafts.value[draftKey] = JSON.stringify(subStrat.config[key] ?? val ?? {}, null, 2)
  } catch {
    configJsonDrafts.value[draftKey] = '{}'
  }
}

function getConfigFieldError(subStrat, key) {
  return configJsonErrors.value[getConfigDraftKey(subStrat, key)] || ''
}

function hasConfigEditorErrors() {
  return Object.keys(configJsonErrors.value).length > 0
}

function configEntries(sub) {
  return Object.entries(sub.config || {}).filter(([key]) =>
    !shouldHideStrategyConfigField(sub.strategy, key, sub.config || {}) &&
    !isLlmManagedConfigField(sub.strategy, key, sub.config || {}, getRefConfig(sub.strategy))
  )
}

function llmConfigGroups(sub) {
  return getStrategyLlmConfigGroups(sub.strategy, sub.config || {}, getRefConfig(sub.strategy))
}

function lookbackLlmConfigGroups(sub) {
  return getStrategyLookbackLlmConfigGroups(sub.strategy)
}

function llmConfigSummary(sub, group) {
  return describeStrategyLlmGroup(sub.config || {}, group)
}

function removeSubStrategy(idx) {
  editStrategyData.value.strategies.splice(idx, 1)
}

function addEditSubStrategy() {
  if (!editStrategyData.value || !editStrategyPickName.value) return
  editStrategyData.value.strategies.push(
    buildSubStrategyDefaults(editStrategyPickName.value, editStrategyData.value.strategies.length)
  )
}

const showLlmConfigModal = ref(false)
const llmConfigTarget = ref(null)
const llmConfigDraft = ref({
  modelId: '',
  provider: 'gemini',
  model: '',
  apiKey: '',
  openaiBaseUrl: '',
  azureEndpoint: '',
  azureApiVersion: '2024-10-21',
  reasoningEffort: '',
  // Ollama-specific — used by the Effort cell (Ollama uses ``think``,
  // not ``reasoning_effort``) and by the runtime resolver to know the
  // Ollama host the operator picked.
  ollamaBaseUrl: '',
  ollamaKeepAlive: '',
  ollamaThink: '',
})

// Friendly label for the "Effort" line in the picker — works across
// reasoning_effort (Azure/OpenAI/NVIDIA) and ollama_think (Ollama).
function effortLabel(draft) {
  if (!draft) return 'Default'
  if (draft.provider === 'ollama') {
    const v = String(draft.ollamaThink || '').trim().toLowerCase()
    if (!v) return 'Default'
    if (v === 'true' || v === 'on') return 'On'
    if (v === 'false' || v === 'off') return 'Off'
    return v.charAt(0).toUpperCase() + v.slice(1)
  }
  const eff = String(draft.reasoningEffort || '').trim()
  if (!eff) return 'Default'
  return eff.charAt(0).toUpperCase() + eff.slice(1)
}
const llmConfigTesting = ref(false)
const llmConfigMsg = ref('')
const llmConfigOk = ref(false)
const savedModels = ref([])

async function fetchSavedModels() {
  try {
    const res = await fetch(`${API_BASE}/models`, { headers: authHeaders() })
    if (!res.ok) return
    const data = await res.json()
    savedModels.value = data.models || []
  } catch { /* ignore */ }
}

async function openLlmConfigModal(sub, group) {
  llmConfigTarget.value = { sub, group }
  const draft = buildStrategyLlmDraft(sub.config || {}, group)
  llmConfigDraft.value = { modelId: draft.modelId || '', ...draft }
  llmConfigTesting.value = false
  llmConfigMsg.value = ''
  llmConfigOk.value = false
  await fetchSavedModels()
  // Populate fields from saved model if one is referenced
  if (llmConfigDraft.value.modelId) {
    const m = savedModels.value.find(x => x.id === llmConfigDraft.value.modelId)
    if (m) {
      llmConfigDraft.value.provider = m.provider || 'gemini'
      llmConfigDraft.value.model = m.model || ''
      llmConfigDraft.value.reasoningEffort = m.reasoning_effort || ''
      llmConfigDraft.value.ollamaBaseUrl = m.ollama_base_url || ''
      llmConfigDraft.value.ollamaKeepAlive = m.ollama_keep_alive || ''
      llmConfigDraft.value.ollamaThink = m.ollama_think || ''
    }
  }
  showLlmConfigModal.value = true
}

function closeLlmConfigModal(force = false) {
  if (llmConfigTesting.value && !force) return
  showLlmConfigModal.value = false
  llmConfigTarget.value = null
  llmConfigMsg.value = ''
  llmConfigOk.value = false
}

function onModelSelect() {
  const id = llmConfigDraft.value.modelId
  if (!id) return
  const m = savedModels.value.find(x => x.id === id)
  if (!m) return
  llmConfigDraft.value = {
    ...llmConfigDraft.value,
    modelId: id,
    provider: m.provider || 'gemini',
    model: m.model || '',
    reasoningEffort: m.reasoning_effort || '',
    ollamaBaseUrl: m.ollama_base_url || '',
    ollamaKeepAlive: m.ollama_keep_alive || '',
    ollamaThink: m.ollama_think || '',
  }
}

function saveLlmConfigModal() {
  if (!llmConfigTarget.value?.sub || !llmConfigTarget.value?.group) return
  if (!llmConfigDraft.value.modelId) {
    llmConfigMsg.value = 'Please select a saved model.'
    llmConfigOk.value = false
    return
  }
  llmConfigTarget.value.sub.config = applyStrategyLlmDraft(
    llmConfigTarget.value.sub.config || {},
    llmConfigTarget.value.group,
    llmConfigDraft.value,
  )
  closeLlmConfigModal(true)
}

// ── Benzinga API test ──────────────────────────────────────────────────────
const showBenzingaTestModal = ref(false)
const benzingaTestLoading = ref(false)
const benzingaTestResults = ref(null)

function hasBenzingaConfig(sub) {
  return Object.keys(sub.config || {}).some(k => k.startsWith('benzinga_'))
}

function getBenzingaApiKey(sub) {
  return (sub.config || {}).benzinga_api_key || ''
}

async function testBenzingaSources(sub) {
  benzingaTestLoading.value = true
  benzingaTestResults.value = null
  showBenzingaTestModal.value = true
  try {
    const enabledSources = []
    const sourceMap = {
      benzinga_ratings_enabled: 'ratings',
      benzinga_insights_enabled: 'insights',
      benzinga_insider_trades_enabled: 'insider_trades',
      benzinga_gov_trades_enabled: 'gov_trades',
      benzinga_ma_enabled: 'ma',
      benzinga_ipo_enabled: 'ipos',
      benzinga_splits_enabled: 'splits',
      benzinga_earnings_calendar_enabled: 'earnings',
      benzinga_company_actions_enabled: 'company_actions',
      benzinga_prediction_markets_enabled: 'prediction_markets',
    }
    for (const [cfgKey, srcKey] of Object.entries(sourceMap)) {
      if (sub.config[cfgKey] !== false) enabledSources.push(srcKey)
    }
    const res = await fetch(`${API_BASE}/benzinga/test`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({
        api_key: getBenzingaApiKey(sub),
        sources: enabledSources.length ? enabledSources : null,
      }),
    })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`)
    benzingaTestResults.value = body
  } catch (e) {
    benzingaTestResults.value = { ok: false, accessible: 0, total: 0, results: [], message: e.message || 'Test failed.' }
  } finally {
    benzingaTestLoading.value = false
  }
}

async function submitEditStrategy() {
  const d = editStrategyData.value
  if (!d.name?.trim()) { editStrategyMsg.value = 'Strategy name is required'; editStrategyOk.value = false; return }
  if (hasConfigEditorErrors()) { editStrategyMsg.value = 'Fix invalid JSON config fields before saving'; editStrategyOk.value = false; return }
  editStrategySaving.value = true
  editStrategyMsg.value    = 'Saving...'
  editStrategyOk.value     = false
  try {
    const res = await fetch(`${API_BASE}/strategies/${d.id}`, {
      method: 'PUT',
      headers: authHeaders(),
      body: JSON.stringify({ name: d.name.trim(), strategies: d.strategies }),
    })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`)
    editStrategyOk.value  = true
    editStrategyMsg.value = 'Strategy saved.'
    await fetchInstance()
    setTimeout(() => { showEditStrategy.value = false }, 1200)
  } catch (e) {
    editStrategyOk.value  = false
    editStrategyMsg.value = e.message || 'Something went wrong'
  } finally {
    editStrategySaving.value = false
  }
}

// ── Create Backtest modal ─────────────────────────────────────────────────────
const showCreateBacktest  = ref(false)
const creatingBacktest    = ref(false)
const createBtMsg         = ref('')
const createBtOk          = ref(false)
const createBtForm        = ref({
  stocks: '',
  start_date: '',
  end_date: '',
  granularity: '60',
  initial_cash: '100000',
})

const GRANULARITIES = [
  { label: '1 min',  value: '60' },
  { label: '5 min',  value: '300' },
  { label: '15 min', value: '900' },
  { label: '1 hour', value: '3600' },
  { label: '1 day',  value: '86400' },
]

function openCreateBacktest() {
  createBtForm.value = { stocks: '', start_date: '', end_date: '', granularity: '60', initial_cash: '100000' }
  createBtMsg.value      = ''
  createBtOk.value       = false
  creatingBacktest.value = false
  showCreateBacktest.value = true
}

function closeCreateBacktest() {
  if (creatingBacktest.value) return
  showCreateBacktest.value = false
}

async function submitCreateBacktest() {
  const f = createBtForm.value
  const stocks = f.stocks.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
  // V7.3: Allow empty stocks for pure discovery mode (Nexus strategy discovers its own tickers)
  if (!f.start_date)     { createBtMsg.value = 'Start date is required';         createBtOk.value = false; return }
  if (!f.end_date)       { createBtMsg.value = 'End date is required';           createBtOk.value = false; return }
  if (f.start_date >= f.end_date) { createBtMsg.value = 'End date must be after start date'; createBtOk.value = false; return }

  creatingBacktest.value = true
  createBtMsg.value = 'Creating...'
  createBtOk.value  = false
  try {
    const res = await fetch(`${API_BASE}/backtests`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({
        instance_id:  instanceId.value,
        stocks,
        start_date:   f.start_date,
        end_date:     f.end_date,
        granularity:  String(f.granularity ?? '60'),
        initial_cash: parseFloat(f.initial_cash) || 100000,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    createBtOk.value  = true
    createBtMsg.value = `Backtest queued (ID: ${data.id ?? data.backtest_id ?? '?'}).`
    await fetchBacktests(1)
    setTimeout(() => { showCreateBacktest.value = false }, 1500)
  } catch (e) {
    createBtOk.value  = false
    createBtMsg.value = e.message || 'Something went wrong'
  } finally {
    creatingBacktest.value = false
  }
}
</script>

<template>
  <AppShell>
    <main class="flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-10 max-w-6xl">

      <!-- Breadcrumb + back -->
      <div class="flex items-center gap-2 text-sm text-slate-500 mb-6">
        <button @click="router.push('/instances')" class="hover:text-primary transition-colors flex items-center gap-1">
          <span class="material-symbols-outlined text-[16px]">arrow_back</span>
          Instances
        </button>
        <span>/</span>
        <span class="text-slate-300 truncate max-w-xs">{{ inst?.name || instanceId }}</span>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="flex items-center gap-3 text-slate-400 text-sm">
        <span class="material-symbols-outlined animate-spin text-xl">progress_activity</span>
        Loading instance...
      </div>

      <!-- Error -->
      <div v-else-if="error" class="rounded-xl bg-red-500/10 border border-red-500/20 px-5 py-4 text-red-400 text-sm">
        {{ error }}
      </div>

      <template v-else-if="inst">

        <!-- ── Header ──────────────────────────────────────────────────────── -->
        <div class="flex items-start justify-between gap-4 mb-8 flex-wrap">
          <div class="flex items-center gap-4 min-w-0">
            <div class="size-12 rounded-2xl bg-surface border border-border-subtle flex items-center justify-center shrink-0">
              <span class="material-symbols-outlined text-primary text-2xl">memory</span>
            </div>
            <div class="min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <h1 class="text-2xl font-bold truncate">{{ inst.name || inst.id }}</h1>
                <span
                  class="text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider"
                  :class="(inst.created_by || 'user') === 'ai'
                    ? 'bg-violet-500/15 text-violet-400 border border-violet-500/20'
                    : 'bg-slate-500/10 text-slate-500 border border-slate-700'"
                >{{ (inst.created_by || 'user') === 'ai' ? 'AI' : 'User' }}</span>
              </div>
              <p class="text-xs text-slate-500 font-mono mt-0.5">{{ inst.id }}</p>
            </div>
          </div>
          <div class="flex items-center gap-3 shrink-0">
            <div
              class="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-full border"
              :class="inst.runCommand
                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                : 'bg-slate-500/10 text-slate-500 border-slate-700'"
            >
              <div class="size-1.5 rounded-full" :class="inst.runCommand ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'"></div>
              {{ inst.runCommand ? 'Running' : 'Stopped' }}
            </div>
            <button
              @click="toggleRun"
              :disabled="busy"
              class="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold border transition-all disabled:opacity-40"
              :class="inst.runCommand
                ? 'text-amber-400 hover:bg-amber-500/10 border-amber-500/20'
                : 'text-emerald-400 hover:bg-emerald-500/10 border-emerald-500/20'"
            >
              <span v-if="busy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              <span v-else class="material-symbols-outlined text-[16px]">{{ inst.runCommand ? 'stop' : 'play_arrow' }}</span>
              {{ inst.runCommand ? 'Stop' : 'Start' }}
            </button>
            <button
              @click="router.push(`/instances/${instanceId}/live`)"
              class="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold border transition-all text-sky-400 hover:bg-sky-500/10 border-sky-500/20"
            >
              <span class="material-symbols-outlined text-[16px]">monitoring</span>
              Live Trading
            </button>
            <button
              @click="fetchInstance"
              class="p-2 rounded-lg border border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface transition-all"
              title="Refresh"
            >
              <span class="material-symbols-outlined text-[18px]">refresh</span>
            </button>
          </div>
        </div>

        <!-- ── Info grid ───────────────────────────────────────────────────── -->
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5 mb-8">

          <!-- Instance info -->
          <div class="glass-card rounded-2xl p-5">
            <div class="flex items-center justify-between mb-4">
              <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Instance Info</p>
              <div class="flex items-center gap-2">
                <button
                  @click="openClearState"
                  class="inline-flex items-center gap-1 text-[11px] font-semibold text-red-300 hover:bg-red-500/10 px-2 py-1 rounded-lg border border-red-500/20 transition-colors"
                  title="Clear lookback / strategy cache / full instance state"
                >
                  <span class="material-symbols-outlined text-[13px]">delete_sweep</span>
                  Clear data
                </button>
                <button
                  @click="openEditInfo"
                  class="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-400 hover:bg-amber-500/10 px-2 py-1 rounded-lg border border-amber-500/20 transition-colors"
                >
                  <span class="material-symbols-outlined text-[13px]">edit</span>
                  Edit
                </button>
              </div>
            </div>
            <div class="space-y-3 text-sm">
              <div class="flex justify-between gap-2">
                <span class="text-slate-500">Granularity</span>
                <span class="text-slate-200 font-mono">{{ fmtGranularity(inst.granularity_time_increment) }}</span>
              </div>
              <div class="flex justify-between gap-2">
                <span class="text-slate-500">Uptime</span>
                <span class="font-mono" :class="inst.runCommand ? 'text-emerald-400' : 'text-slate-500'">
                  {{ inst.runCommand ? fmtUptime(liveSecs) : '—' }}
                </span>
              </div>
              <div class="flex justify-between gap-2">
                <span class="text-slate-500">Created by</span>
                <span class="text-slate-200">{{ (inst.created_by || 'user') === 'ai' ? 'AI Engine' : 'User' }}</span>
              </div>
              <div class="flex justify-between gap-2">
                <span class="text-slate-500">Max usage</span>
                <span class="text-slate-200 font-mono">{{ inst.max_usage != null ? fmtMoney(inst.max_usage) : '—' }}</span>
              </div>
            </div>
          </div>

          <!-- Brokerage -->
          <div class="glass-card rounded-2xl p-5">
            <div class="flex items-center justify-between mb-4">
              <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Brokerage</p>
              <div class="flex items-center gap-1.5">
                <button
                  v-if="inst.brokerage"
                  @click="openUnlinkBrokerage"
                  class="inline-flex items-center gap-1 text-[11px] font-semibold text-red-400 hover:bg-red-500/10 px-2 py-1 rounded-lg border border-red-500/20 transition-colors"
                >
                  <span class="material-symbols-outlined text-[13px]">link_off</span>
                  Unlink
                </button>
                <button
                  v-if="inst.brokerage"
                  @click="openLinkBrokerage"
                  class="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-400 hover:bg-amber-500/10 px-2 py-1 rounded-lg border border-amber-500/20 transition-colors"
                >
                  <span class="material-symbols-outlined text-[13px]">edit</span>
                  Edit
                </button>
              </div>
            </div>
            <div v-if="inst.brokerage" class="space-y-3 text-sm">
              <div class="flex items-center gap-2 mb-3">
                <div class="size-8 rounded-lg bg-surface border border-border-subtle flex items-center justify-center shrink-0">
                  <span class="material-symbols-outlined text-slate-400 text-base">account_balance</span>
                </div>
                <div class="min-w-0">
                  <p class="font-semibold text-slate-200 truncate">{{ inst.brokerage.account_name }}</p>
                  <p class="text-[11px] text-slate-500 capitalize">{{ inst.brokerage.brokerage_type }}</p>
                </div>
              </div>
              <div v-if="inst.brokerage.alpaca_account_number" class="flex justify-between gap-2">
                <span class="text-slate-500">Account #</span>
                <span class="text-slate-300 font-mono text-xs">{{ inst.brokerage.alpaca_account_number }}</span>
              </div>
              <div v-if="inst.brokerage.alpaca_paper != null" class="flex justify-between gap-2">
                <span class="text-slate-500">Mode</span>
                <span class="text-slate-300">{{ inst.brokerage.alpaca_paper ? 'Paper' : 'Live' }}</span>
              </div>
              <div v-if="inst.max_usage != null" class="flex justify-between gap-2">
                <span class="text-slate-500">Max usage</span>
                <span class="text-slate-200 font-mono font-semibold">{{ fmtMoney(inst.max_usage) }}</span>
              </div>
            </div>
            <div v-else class="flex flex-col items-center gap-2 py-4 text-center">
              <span class="material-symbols-outlined text-3xl text-slate-700">account_balance</span>
              <p class="text-xs text-slate-600">No brokerage linked</p>
              <button
                @click="openLinkBrokerage"
                class="mt-2 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-primary hover:bg-primary/10 border border-primary/20 transition-colors"
              >
                <span class="material-symbols-outlined text-[14px]">add</span>
                Link Brokerage
              </button>
            </div>

            <!-- Data-source brokerage (optional, for market data/news) -->
            <div class="mt-4 pt-4 border-t border-border-subtle">
              <div class="flex items-center justify-between mb-2">
                <div>
                  <p class="text-[11px] font-bold uppercase tracking-widest text-slate-500">Market-Data Source</p>
                  <p class="text-[10px] text-slate-600 mt-0.5">Optional Alpaca account for bars/news (use live for paper trading)</p>
                </div>
                <div class="flex items-center gap-1.5">
                  <button
                    v-if="inst.alpaca_data_brokerage_id"
                    @click="submitLinkDataBrokerage(true)"
                    class="inline-flex items-center gap-1 text-[11px] font-semibold text-red-400 hover:bg-red-500/10 px-2 py-1 rounded-lg border border-red-500/20 transition-colors"
                  >
                    <span class="material-symbols-outlined text-[13px]">link_off</span>
                    Clear
                  </button>
                  <button
                    @click="openLinkDataBrokerage"
                    class="inline-flex items-center gap-1 text-[11px] font-semibold text-primary hover:bg-primary/10 px-2 py-1 rounded-lg border border-primary/20 transition-colors"
                  >
                    <span class="material-symbols-outlined text-[13px]">{{ inst.alpaca_data_brokerage_id ? 'edit' : 'add' }}</span>
                    {{ inst.alpaca_data_brokerage_id ? 'Change' : 'Set' }}
                  </button>
                </div>
              </div>
              <div v-if="inst.alpaca_data_brokerage" class="flex items-center gap-2 text-sm">
                <div class="size-7 rounded-lg bg-surface border border-border-subtle flex items-center justify-center shrink-0">
                  <span class="material-symbols-outlined text-slate-400 text-sm">database</span>
                </div>
                <div class="min-w-0 flex-1">
                  <p class="font-semibold text-slate-300 truncate">{{ inst.alpaca_data_brokerage.account_name }}</p>
                  <p class="text-[10px] text-slate-600">{{ inst.alpaca_data_brokerage.alpaca_paper ? 'Paper' : 'Live' }} Alpaca</p>
                </div>
              </div>
              <div v-else-if="inst.alpaca_data_brokerage_id" class="text-[11px] text-slate-600 italic">
                Linked (id: {{ inst.alpaca_data_brokerage_id.slice(0, 8) }}...)
              </div>
              <div v-else class="text-[11px] text-slate-600 italic">
                Using trading creds for data fetches.
              </div>
            </div>
          </div>

          <!-- Strategy -->
          <div class="glass-card rounded-2xl p-5">
            <div class="flex items-center justify-between mb-4">
              <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Strategy</p>
              <div v-if="inst.strategy_id" class="flex items-center gap-1.5">
                <button
                  @click="openUnlinkStrategy"
                  class="inline-flex items-center gap-1 text-[11px] font-semibold text-red-400 hover:bg-red-500/10 px-2 py-1 rounded-lg border border-red-500/20 transition-colors"
                >
                  <span class="material-symbols-outlined text-[13px]">link_off</span>
                  Unlink
                </button>
                <button
                  @click="openEditStrategy"
                  class="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-400 hover:bg-amber-500/10 px-2 py-1 rounded-lg border border-amber-500/20 transition-colors"
                >
                  <span class="material-symbols-outlined text-[13px]">tune</span>
                  Edit
                </button>
              </div>
            </div>
            <div v-if="inst.strategy">
              <p class="font-semibold text-slate-200 mb-1 truncate" :title="inst.strategy.name">{{ inst.strategy.name }}</p>
              <p class="text-[11px] text-slate-600 font-mono mb-3">ID: {{ inst.strategy_id }}</p>
              <div class="space-y-1.5">
                <div
                  v-for="sub in (inst.strategy.strategies || [])"
                  :key="sub.strategy"
                  class="flex items-center justify-between gap-2 text-xs"
                >
                  <span class="text-slate-400 truncate">{{ sub.strategy }}</span>
                  <span class="text-slate-500 shrink-0">{{ (sub.weight * 100).toFixed(0) }}%</span>
                </div>
              </div>
            </div>
            <div v-else class="flex flex-col items-center gap-2 py-4 text-center">
              <span class="material-symbols-outlined text-3xl text-slate-700">schema</span>
              <p class="text-xs text-slate-600">No strategy linked</p>
              <button
                @click="openLinkStrategy"
                class="mt-2 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-primary hover:bg-primary/10 border border-primary/20 transition-colors"
              >
                <span class="material-symbols-outlined text-[14px]">add</span>
                Link Strategy
              </button>
            </div>
          </div>
        </div>

        <!-- ── Stocks ──────────────────────────────────────────────────────── -->
        <section class="glass-card rounded-2xl p-5 mb-6">
          <div class="flex items-center justify-between mb-4">
            <p class="text-xs font-bold uppercase tracking-widest text-slate-500">
              Stocks <span class="text-slate-700 ml-1">({{ (inst.stocks || []).length }})</span>
            </p>
          </div>
          <div v-if="(inst.stocks || []).length === 0" class="text-xs text-slate-600 italic">No stocks added</div>
          <div v-else class="flex flex-wrap gap-2">
            <span
              v-for="sym in inst.stocks"
              :key="sym"
              class="px-2.5 py-1 rounded-lg bg-surface border border-border-subtle text-xs font-mono text-slate-300"
            >{{ sym }}</span>
          </div>
        </section>

        <!-- ── Backtests ───────────────────────────────────────────────────── -->
        <section class="glass-card rounded-2xl overflow-hidden">
          <div class="px-5 py-4 border-b border-border-subtle flex items-center justify-between">
            <p class="text-xs font-bold uppercase tracking-widest text-slate-500">
              Backtests <span class="text-slate-700 ml-1">({{ btTotal }})</span>
            </p>
            <button
              @click="openCreateBacktest"
              class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-sky-400 hover:bg-sky-500/10 border border-sky-500/20 transition-colors"
            >
              <span class="material-symbols-outlined text-[14px]">add</span>
              New Backtest
            </button>
          </div>
          <div v-if="btLoading && !allBacktests.length" class="px-5 py-10 flex items-center justify-center gap-2 text-slate-500 text-sm">
            <span class="material-symbols-outlined animate-spin text-lg">progress_activity</span>
            Loading backtests...
          </div>
          <div v-else-if="!allBacktests.length" class="px-5 py-10 flex flex-col items-center gap-2 text-center">
            <span class="material-symbols-outlined text-4xl text-slate-700">analytics</span>
            <p class="text-xs text-slate-600">No backtests run for this instance yet</p>
          </div>
          <div v-else class="overflow-x-auto">
            <table class="w-full text-xs">
              <thead>
                <tr class="border-b border-border-subtle text-left">
                  <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider">ID</th>
                  <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider">Stocks</th>
                  <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider">Period</th>
                  <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider">
                    <button
                      @click="toggleBtSort('completed_at')"
                      class="inline-flex items-center gap-1 hover:text-slate-300 transition-colors"
                    >
                      Date Completed
                      <span class="material-symbols-outlined text-[14px]">{{ btSortIcon('completed_at') }}</span>
                    </button>
                  </th>
                  <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider">Status</th>
                  <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider">Elapsed</th>
                  <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider text-right">
                    <button
                      @click="toggleBtSort('pnl')"
                      class="inline-flex items-center gap-1 hover:text-slate-300 transition-colors"
                    >
                      P&L
                      <span class="material-symbols-outlined text-[14px]">{{ btSortIcon('pnl') }}</span>
                    </button>
                  </th>
                  <th class="px-4 py-3 text-slate-500 font-semibold uppercase tracking-wider text-right">
                    <button
                      @click="toggleBtSort('pnl_percent')"
                      class="inline-flex items-center gap-1 hover:text-slate-300 transition-colors"
                    >
                      P&L %
                      <span class="material-symbols-outlined text-[14px]">{{ btSortIcon('pnl_percent') }}</span>
                    </button>
                  </th>
                  <th class="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="bt in allBacktests"
                  :key="bt.id"
                  class="border-b border-border-subtle/50 hover:bg-surface/30 transition-colors"
                >
                  <td class="px-4 py-3 font-mono text-slate-400">{{ bt.id }}</td>
                  <td class="px-4 py-3">
                    <div class="flex flex-wrap gap-1">
                      <span
                        v-for="s in (bt.stocks || []).slice(0, 4)"
                        :key="s"
                        class="font-mono text-slate-300 bg-surface px-1.5 py-0.5 rounded"
                      >{{ s }}</span>
                      <span v-if="(bt.stocks || []).length > 4" class="text-slate-600">+{{ bt.stocks.length - 4 }}</span>
                    </div>
                  </td>
                  <td class="px-4 py-3 text-slate-400 whitespace-nowrap">
                    {{ bt.start_date || '?' }} → {{ bt.end_date || '?' }}
                  </td>
                  <td class="px-4 py-3 text-slate-300 whitespace-nowrap font-mono">
                    {{ fmtDateTime(bt.completed_at) }}
                  </td>
                  <td class="px-4 py-3">
                    <div class="space-y-1.5">
                      <span
                        class="px-2 py-0.5 rounded border font-semibold uppercase"
                        :class="btStatusClass(btProgress[bt.id]?.status || bt.status)"
                      >{{ btProgress[bt.id]?.status || bt.status || 'queued' }}</span>
                      <!-- Progress bar for running backtests -->
                      <div
                        v-if="btProgress[bt.id]?.progress != null && ['running','queued','pending'].includes((btProgress[bt.id]?.status || '').toLowerCase())"
                        class="w-24"
                      >
                        <div class="h-1.5 bg-surface rounded-full overflow-hidden">
                          <div
                            class="h-full bg-sky-400 rounded-full transition-all duration-500"
                            :style="{ width: Math.min(100, Math.max(0, btProgress[bt.id].progress)) + '%' }"
                          ></div>
                        </div>
                        <p class="text-[10px] text-slate-500 mt-0.5">{{ Math.round(btProgress[bt.id].progress) }}%</p>
                      </div>
                    </div>
                  </td>
                  <td class="px-4 py-3 text-slate-300 whitespace-nowrap font-mono">
                    {{ fmtElapsed(btProgress[bt.id]?.time_elapsed_seconds ?? bt.time_elapsed_seconds) }}
                  </td>
                  <td class="px-4 py-3 text-right font-mono font-semibold" :class="pnlClass(bt.pnl)">
                    {{ fmtPnl(bt.pnl) }}
                  </td>
                  <td class="px-4 py-3 text-right font-mono" :class="pnlClass(bt.pnl_percent)">
                    {{ bt.pnl_percent != null ? (Number(bt.pnl_percent) >= 0 ? '+' : '') + Number(bt.pnl_percent).toFixed(2) + '%' : '—' }}
                  </td>
                  <td class="px-4 py-3">
                    <div class="flex items-center gap-1.5 justify-end">
                      <!-- Inline controls based on status -->
                      <template v-if="['running','queued','pending'].includes((btProgress[bt.id]?.status || bt.status || '').toLowerCase())">
                        <button @click="openBtConfirm(bt, 'pause')" title="Pause"
                          class="p-1 rounded-md text-violet-400 hover:bg-violet-500/10 transition-colors">
                          <span class="material-symbols-outlined text-[16px]">pause_circle</span>
                        </button>
                      </template>
                      <template v-if="['paused','paused_llm_critical'].includes((btProgress[bt.id]?.status || bt.status || '').toLowerCase())">
                        <button @click="openBtConfirm(bt, 'resume')" title="Resume"
                          class="p-1 rounded-md text-sky-400 hover:bg-sky-500/10 transition-colors">
                          <span class="material-symbols-outlined text-[16px]">play_circle</span>
                        </button>
                      </template>
                      <template v-if="['running','queued','pending','paused','paused_llm_critical'].includes((btProgress[bt.id]?.status || bt.status || '').toLowerCase())">
                        <button @click="openBtConfirm(bt, 'stop')" title="Stop"
                          class="p-1 rounded-md text-red-400 hover:bg-red-500/10 transition-colors">
                          <span class="material-symbols-outlined text-[16px]">stop_circle</span>
                        </button>
                      </template>
                      <button
                        @click="router.push({ name: 'backtest-detail', params: { id: bt.id }, query: { from: `/instances/${instanceId}` } })"
                        class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold text-primary hover:bg-primary/10 border border-primary/20 transition-colors whitespace-nowrap"
                      >
                        <span class="material-symbols-outlined text-[12px]">open_in_new</span>
                        View
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Pagination -->
          <div v-if="btTotalPages > 1" class="px-5 py-3 border-t border-border-subtle flex items-center justify-between gap-4">
            <p class="text-xs text-slate-500">
              Page {{ btPage }} of {{ btTotalPages }}
              <span class="ml-1 text-slate-600">({{ btTotal }} total)</span>
            </p>
            <div class="flex items-center gap-1.5">
              <button
                @click="fetchBacktests(btPage - 1)"
                :disabled="btPage <= 1 || btLoading"
                class="p-1.5 rounded-lg border border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface transition-all disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <span class="material-symbols-outlined text-[16px]">chevron_left</span>
              </button>
              <template v-for="p in btTotalPages" :key="p">
                <button
                  v-if="p === 1 || p === btTotalPages || Math.abs(p - btPage) <= 1"
                  @click="fetchBacktests(p)"
                  :disabled="btLoading"
                  class="min-w-[28px] h-7 px-1.5 rounded-lg text-xs font-medium transition-all disabled:opacity-50"
                  :class="p === btPage
                    ? 'bg-primary/20 text-primary border border-primary/30'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-surface border border-transparent'"
                >{{ p }}</button>
                <span
                  v-else-if="p === btPage - 2 || p === btPage + 2"
                  class="text-slate-600 text-xs px-0.5"
                >…</span>
              </template>
              <button
                @click="fetchBacktests(btPage + 1)"
                :disabled="btPage >= btTotalPages || btLoading"
                class="p-1.5 rounded-lg border border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface transition-all disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <span class="material-symbols-outlined text-[16px]">chevron_right</span>
              </button>
            </div>
          </div>
        </section>

      </template>

    </main>
  </AppShell>

  <!-- ── Edit Strategy Modal ───────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showEditInfo"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeEditInfo"
      >
        <div class="relative w-full max-w-md bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">
          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle">
            <div class="flex items-center gap-3">
              <div class="size-9 rounded-xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-amber-400 text-lg">edit</span>
              </div>
              <h2 class="text-base font-bold">Edit Instance Info</h2>
            </div>
            <button @click="closeEditInfo" :disabled="editInfoSaving" class="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>

          <div class="px-6 py-5 space-y-4">
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Instance Name</label>
              <input
                v-model="editInfoForm.name"
                type="text"
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors"
              />
            </div>

            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Granularity (seconds)</label>
              <input
                v-model="editInfoForm.granularity"
                type="number"
                min="1"
                step="1"
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors font-mono"
              />
              <div class="flex gap-1.5 flex-wrap mt-2">
                <button
                  v-for="g in GRANULARITIES"
                  :key="'instance-granularity-' + g.value"
                  @click="editInfoForm.granularity = g.value"
                  class="px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all"
                  :class="editInfoForm.granularity === g.value
                    ? 'bg-primary/15 text-primary border-primary/40'
                    : 'border-border-subtle text-slate-400 hover:text-slate-200'"
                >
                  {{ g.label }}
                </button>
              </div>
            </div>

            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Max Usage ($)</label>
              <input
                v-model="editInfoForm.max_usage"
                type="number"
                min="0"
                step="0.01"
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors font-mono"
                placeholder="Leave blank to keep current value"
              />
            </div>

            <Transition name="slide-up">
              <div
                v-if="editInfoMsg"
                class="rounded-lg px-4 py-3 text-sm font-medium"
                :class="editInfoOk
                  ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                  : 'bg-red-500/10 border border-red-500/20 text-red-400'"
              >
                <span v-if="editInfoSaving" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-base animate-spin">progress_activity</span>
                  {{ editInfoMsg }}
                </span>
                <span v-else>{{ editInfoMsg }}</span>
              </div>
            </Transition>
          </div>

          <div class="px-6 pb-6 flex gap-3">
            <button @click="closeEditInfo" :disabled="editInfoSaving"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40">
              Cancel
            </button>
            <button @click="submitEditInfo" :disabled="editInfoSaving"
              class="flex-1 py-2.5 rounded-lg bg-amber-500 text-black text-sm font-bold hover:bg-amber-400 transition-all disabled:opacity-50 flex items-center justify-center gap-2">
              <span v-if="editInfoSaving" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              <span v-else class="material-symbols-outlined text-base">save</span>
              {{ editInfoSaving ? 'Saving...' : 'Save Changes' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showLinkBrokerage"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeLinkBrokerage"
      >
        <div class="relative w-full max-w-md bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">
          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle">
            <div class="flex items-center gap-3">
              <div class="size-9 rounded-xl bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-primary text-lg">account_balance</span>
              </div>
              <h2 class="text-base font-bold">Link Brokerage</h2>
            </div>
            <button @click="closeLinkBrokerage" :disabled="linkBrokerageSaving" class="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>

          <div class="px-6 py-5 space-y-4">
            <template v-if="brokerageOptions.length">
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1.5">Select brokerage</label>
                <select
                  v-model="selectedBrokerageId"
                  class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors"
                >
                  <option
                    v-for="account in brokerageOptions"
                    :key="account.id"
                    :value="String(account.id)"
                    class="bg-[#0f1318]"
                  >
                    {{ account.account_name }} ({{ account.brokerage_type }})
                  </option>
                </select>
              </div>
            </template>
            <div v-else class="rounded-lg border border-border-subtle bg-surface/40 px-4 py-4 text-center">
              <p class="text-sm text-slate-300 mb-2">No brokerage accounts available yet.</p>
              <button
                @click="router.push('/brokerages')"
                class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-primary hover:bg-primary/10 border border-primary/20 transition-colors"
              >
                <span class="material-symbols-outlined text-[14px]">open_in_new</span>
                Go To Brokerages
              </button>
            </div>

            <Transition name="slide-up">
              <div
                v-if="linkBrokerageMsg"
                class="rounded-lg px-4 py-3 text-sm font-medium"
                :class="linkBrokerageOk
                  ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                  : 'bg-red-500/10 border border-red-500/20 text-red-400'"
              >
                <span v-if="linkBrokerageSaving" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-base animate-spin">progress_activity</span>
                  {{ linkBrokerageMsg }}
                </span>
                <span v-else>{{ linkBrokerageMsg }}</span>
              </div>
            </Transition>
          </div>

          <div class="px-6 pb-6 flex gap-3">
            <button @click="closeLinkBrokerage" :disabled="linkBrokerageSaving"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40">
              Cancel
            </button>
            <button
              @click="submitLinkBrokerage"
              :disabled="linkBrokerageSaving || !brokerageOptions.length"
              class="flex-1 py-2.5 rounded-lg bg-primary text-black text-sm font-bold hover:brightness-110 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
            >
              <span v-if="linkBrokerageSaving" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              <span v-else class="material-symbols-outlined text-base">link</span>
              {{ linkBrokerageSaving ? 'Linking...' : 'Link Brokerage' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- Data-source brokerage modal (separate Alpaca account for bars/news) -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showLinkDataBrokerage"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeLinkDataBrokerage"
      >
        <div class="relative w-full max-w-md bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">
          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle">
            <div class="flex items-center gap-3">
              <div class="size-9 rounded-xl bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-primary text-lg">database</span>
              </div>
              <div>
                <h2 class="text-base font-bold">Market-Data Source</h2>
                <p class="text-[10px] text-slate-500">Alpaca account for bars / news fetches</p>
              </div>
            </div>
            <button @click="closeLinkDataBrokerage" :disabled="linkDataBrokerageSaving" class="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>

          <div class="px-6 py-5 space-y-4">
            <template v-if="dataBrokerageOptions.length">
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1.5">Select Alpaca account for data</label>
                <select
                  v-model="selectedDataBrokerageId"
                  class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors"
                >
                  <option
                    v-for="account in dataBrokerageOptions"
                    :key="account.id"
                    :value="String(account.id)"
                    class="bg-[#0f1318]"
                  >
                    {{ account.account_name }} ({{ account.alpaca_paper ? 'Paper' : 'Live' }})
                  </option>
                </select>
                <p class="mt-2 text-[10px] text-slate-500">
                  Tip: if you trade paper, link a LIVE Alpaca account here so news/bars fetches
                  don't get 401s. Leaving this unset means the strategy uses the trading creds.
                </p>
              </div>
            </template>
            <div v-else class="rounded-lg border border-border-subtle bg-surface/40 px-4 py-4 text-center">
              <p class="text-sm text-slate-300 mb-2">No Alpaca accounts available.</p>
              <button
                @click="router.push('/brokerages')"
                class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-primary hover:bg-primary/10 border border-primary/20 transition-colors"
              >
                <span class="material-symbols-outlined text-[14px]">open_in_new</span>
                Link an Alpaca account
              </button>
            </div>

            <Transition name="slide-up">
              <div
                v-if="linkDataBrokerageMsg"
                class="rounded-lg px-4 py-3 text-sm font-medium"
                :class="linkDataBrokerageOk
                  ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                  : 'bg-red-500/10 border border-red-500/20 text-red-400'"
              >
                <span v-if="linkDataBrokerageSaving" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-base animate-spin">progress_activity</span>
                  {{ linkDataBrokerageMsg }}
                </span>
                <span v-else>{{ linkDataBrokerageMsg }}</span>
              </div>
            </Transition>
          </div>

          <div class="px-6 pb-6 flex gap-3">
            <button @click="closeLinkDataBrokerage" :disabled="linkDataBrokerageSaving"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40">
              Cancel
            </button>
            <button
              @click="submitLinkDataBrokerage(false)"
              :disabled="linkDataBrokerageSaving || !dataBrokerageOptions.length"
              class="flex-1 py-2.5 rounded-lg bg-primary text-black text-sm font-bold hover:brightness-110 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
            >
              <span v-if="linkDataBrokerageSaving" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              <span v-else class="material-symbols-outlined text-base">link</span>
              {{ linkDataBrokerageSaving ? 'Saving...' : 'Set Data Source' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showEditStrategy"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeEditStrategy"
      >
        <div class="relative w-full max-w-2xl bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl flex flex-col max-h-[90vh]">

          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle shrink-0">
            <div class="flex items-center gap-3 min-w-0">
              <div class="size-9 rounded-xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-amber-400 text-lg">tune</span>
              </div>
              <h2 class="text-base font-bold truncate">Edit Strategy</h2>
            </div>
            <button @click="closeEditStrategy" :disabled="editStrategySaving" class="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40 ml-4 shrink-0">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>

          <div class="flex-1 overflow-y-auto px-6 py-5 space-y-5">

            <div v-if="editStrategyLoading" class="flex items-center gap-3 text-slate-400 text-sm py-8 justify-center">
              <span class="material-symbols-outlined animate-spin text-xl">progress_activity</span>
              Loading strategy...
            </div>

            <template v-else-if="editStrategyData">
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1.5">Strategy Name</label>
                <input
                  v-model="editStrategyData.name"
                  type="text"
                  class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors"
                />
              </div>

              <div>
                <div v-if="availableStrategyOptions.length" class="mb-4">
                  <label class="block text-xs font-medium text-slate-400 mb-1.5">Add Sub-strategy</label>
                  <div class="flex gap-2">
                    <select
                      v-model="editStrategyPickName"
                      class="flex-1 bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors"
                    >
                      <option v-for="s in availableStrategyOptions" :key="s.value" :value="s.value">{{ s.label }} · {{ s.value }}</option>
                    </select>
                    <button
                      type="button"
                      @click="addEditSubStrategy"
                      class="shrink-0 flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold text-primary hover:bg-primary/10 border border-primary/20 transition-colors"
                    >
                      <span class="material-symbols-outlined text-[14px]">add</span>
                      Add
                    </button>
                  </div>
                </div>

                <p class="text-xs font-medium text-slate-400 uppercase tracking-wider mb-3">
                  Sub-strategies
                  <span class="text-slate-600 ml-1">({{ editStrategyData.strategies?.length || 0 }})</span>
                </p>

                <div v-if="!editStrategyData.strategies?.length" class="text-xs text-slate-600 italic">
                  No sub-strategies configured.
                </div>

                <div class="space-y-4">
                  <div
                    v-for="(sub, idx) in editStrategyData.strategies"
                    :key="idx"
                    class="rounded-xl border border-border-subtle bg-surface/40 p-4 space-y-4"
                  >
                    <div class="flex items-start justify-between gap-3">
                      <div class="min-w-0">
                        <div class="flex items-center gap-2 flex-wrap">
                          <p class="text-sm font-bold text-slate-100">{{ strategyLabel(sub.strategy) }}</p>
                          <span class="text-[10px] text-slate-600 font-mono">{{ strategyCanonicalId(sub.strategy) }}</span>
                        </div>
                        <p v-if="getStrategyMeta(sub.strategy)?.description" class="text-[11px] text-slate-500 mt-0.5 leading-relaxed">
                          {{ getStrategyMeta(sub.strategy)?.description }}
                        </p>
                      </div>
                      <button @click="removeSubStrategy(idx)" class="shrink-0 text-slate-600 hover:text-red-400 transition-colors" title="Remove">
                        <span class="material-symbols-outlined text-[18px]">delete</span>
                      </button>
                    </div>

                    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
                      <div>
                        <label class="block text-[11px] font-medium text-slate-500 mb-1">Weight</label>
                        <input v-model.number="sub.weight" type="number" min="0" max="1" step="0.05"
                          class="w-full bg-[#0f1318] border border-border-subtle rounded-lg px-2.5 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary transition-colors font-mono" />
                      </div>
                      <div>
                        <label class="block text-[11px] font-medium text-slate-500 mb-1">Exec Position</label>
                        <input v-model.number="sub.execution_position" type="number" step="1"
                          class="w-full bg-[#0f1318] border border-border-subtle rounded-lg px-2.5 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary transition-colors font-mono" />
                      </div>
                      <div>
                        <label class="block text-[11px] font-medium text-slate-500 mb-1">Phase</label>
                        <div class="flex gap-1">
                          <button v-for="p in ['pre', 'post']" :key="p" @click="sub.decision_phase = p"
                            class="flex-1 py-1.5 rounded-md text-[11px] font-semibold border transition-all"
                            :class="sub.decision_phase === p ? 'bg-primary/15 text-primary border-primary/40' : 'border-border-subtle text-slate-500 hover:text-slate-300'"
                          >{{ p }}</button>
                        </div>
                      </div>
                      <div>
                        <label class="block text-[11px] font-medium text-slate-500 mb-1">Scope</label>
                        <div class="flex gap-1">
                          <button v-for="s in ['per_symbol', 'run_once']" :key="s" @click="sub.execution_scope = s"
                            class="flex-1 py-1.5 rounded-md text-[10px] font-semibold border transition-all"
                            :class="sub.execution_scope === s ? 'bg-primary/15 text-primary border-primary/40' : 'border-border-subtle text-slate-500 hover:text-slate-300'"
                          >{{ s === 'per_symbol' ? 'per sym' : 'once' }}</button>
                        </div>
                      </div>
                    </div>

                    <div v-if="llmConfigGroups(sub).length">
                      <p class="text-[11px] font-medium text-slate-500 uppercase tracking-wider mb-2">LLM</p>
                      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div
                          v-for="group in llmConfigGroups(sub)"
                          :key="group.label"
                          class="rounded-lg border border-border-subtle bg-[#0f1318] px-3 py-3 space-y-2"
                        >
                          <div class="flex items-start justify-between gap-3">
                            <div class="min-w-0">
                              <p class="text-xs font-semibold text-slate-200">{{ group.label }}</p>
                              <p class="text-[11px] text-slate-500 leading-relaxed">{{ llmConfigSummary(sub, group) }}</p>
                            </div>
                            <button
                              type="button"
                              @click="openLlmConfigModal(sub, group)"
                              class="shrink-0 rounded-md border border-primary/20 px-2.5 py-1 text-[11px] font-semibold text-primary hover:bg-primary/10 transition-colors"
                            >
                              Edit
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>

                    <div v-if="lookbackLlmConfigGroups(sub).length">
                      <p class="text-[11px] font-medium text-slate-500 uppercase tracking-wider mb-2">Lookback LLM</p>
                      <p class="text-[10px] text-slate-600 mb-2 leading-relaxed">Override LLM provider/model for the 90-day lookback training pass. Falls back to the main LLM config above if not set.</p>
                      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div
                          v-for="group in lookbackLlmConfigGroups(sub)"
                          :key="group.label"
                          class="rounded-lg border border-violet-500/20 bg-[#0f1318] px-3 py-3 space-y-2"
                        >
                          <div class="flex items-start justify-between gap-3">
                            <div class="min-w-0">
                              <p class="text-xs font-semibold text-slate-200">{{ group.label }}</p>
                              <p class="text-[11px] text-slate-500 leading-relaxed">{{ llmConfigSummary(sub, group) }}</p>
                            </div>
                            <button
                              type="button"
                              @click="openLlmConfigModal(sub, group)"
                              class="shrink-0 rounded-md border border-violet-500/20 px-2.5 py-1 text-[11px] font-semibold text-violet-400 hover:bg-violet-500/10 transition-colors"
                            >
                              Edit
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>

                    <div v-if="configEntries(sub).length">
                      <p class="text-[11px] font-medium text-slate-500 uppercase tracking-wider mb-2">Config</p>
                      <div class="grid grid-cols-2 sm:grid-cols-3 gap-3">
                        <div v-for="[key, val] in configEntries(sub)" :key="key">
                          <label class="block text-[11px] font-medium text-slate-500 mb-1">{{ getStrategyConfigFieldMeta(sub.strategy, key).label }}</label>
                          <template v-if="getConfigType(getRefConfig(sub.strategy)[key] ?? val) === 'boolean'">
                            <button type="button" @click="sub.config[key] = !sub.config[key]"
                              class="relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 transition-colors"
                              :class="sub.config[key] ? 'bg-primary border-primary' : 'bg-[#0f1318] border-border-subtle'">
                              <span class="inline-block size-3.5 rounded-full bg-white shadow transition-transform"
                                :class="sub.config[key] ? 'translate-x-4' : 'translate-x-0.5'"></span>
                            </button>
                          </template>
                          <template v-else-if="getConfigType(getRefConfig(sub.strategy)[key] ?? val) === 'number'">
                            <input :value="sub.config[key]" @input="updateConfigField(sub, key, $event.target.value, 'number')" type="number"
                              class="w-full bg-[#0f1318] border border-border-subtle rounded-lg px-2.5 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary transition-colors font-mono" />
                          </template>
                          <template v-else-if="getConfigType(getRefConfig(sub.strategy)[key] ?? val) === 'json'">
                            <textarea
                              :value="getConfigFieldValue(sub, key, val)"
                              @input="updateConfigField(sub, key, $event.target.value, 'json')"
                              @blur="syncConfigJsonDraft(sub, key, val)"
                              rows="4"
                              spellcheck="false"
                              class="w-full bg-[#0f1318] border border-border-subtle rounded-lg px-2.5 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary transition-colors font-mono resize-y"
                              :class="getConfigFieldError(sub, key) ? 'border-red-500/60 focus:border-red-400' : ''"
                            ></textarea>
                          </template>
                          <template v-else>
                            <input v-model="sub.config[key]" type="text"
                              class="w-full bg-[#0f1318] border border-border-subtle rounded-lg px-2.5 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary transition-colors font-mono" />
                          </template>
                          <p v-if="getConfigFieldError(sub, key)" class="mt-1 text-[10px] leading-relaxed text-red-400">
                            Invalid JSON: {{ getConfigFieldError(sub, key) }}
                          </p>
                          <p v-if="getStrategyConfigFieldMeta(sub.strategy, key).description" class="mt-1 text-[10px] leading-relaxed text-slate-600">
                            {{ getStrategyConfigFieldMeta(sub.strategy, key).description }}
                          </p>
                        </div>
                      </div>
                      <!-- Benzinga API test button -->
                      <div v-if="hasBenzingaConfig(sub)" class="col-span-2 sm:col-span-3 mt-2">
                        <button @click="testBenzingaSources(sub)" :disabled="benzingaTestLoading"
                          class="rounded-md border border-amber-500/30 px-3 py-1.5 text-[11px] font-semibold text-amber-400 hover:bg-amber-500/10 transition-colors disabled:opacity-50">
                          {{ benzingaTestLoading ? 'Testing...' : 'Test Benzinga API Access' }}
                        </button>
                      </div>
                    </div>

                  </div>
                </div>
              </div>
            </template>

            <div v-else-if="editStrategyMsg && !editStrategyOk" class="rounded-lg px-4 py-3 text-sm bg-red-500/10 border border-red-500/20 text-red-400">
              {{ editStrategyMsg }}
            </div>

            <Transition name="slide-up">
              <div v-if="editStrategyMsg && editStrategyData" class="rounded-lg px-4 py-3 text-sm font-medium"
                :class="editStrategyOk ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400' : 'bg-red-500/10 border border-red-500/20 text-red-400'">
                {{ editStrategyMsg }}
              </div>
            </Transition>
          </div>

          <div class="px-6 pb-6 pt-4 border-t border-border-subtle flex gap-3 shrink-0">
            <button @click="closeEditStrategy" :disabled="editStrategySaving"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40">
              Cancel
            </button>
            <button v-if="editStrategyData && !editStrategyLoading" @click="submitEditStrategy" :disabled="editStrategySaving"
              class="flex-1 py-2.5 rounded-lg bg-amber-500 text-black text-sm font-bold hover:bg-amber-400 transition-all disabled:opacity-50 flex items-center justify-center gap-2">
              <span v-if="editStrategySaving" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              {{ editStrategySaving ? 'Saving...' : 'Save Changes' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Clear Instance State Modal (destructive) ─────────────────────────── -->
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="showClearState"
           class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
           @click.self="closeClearState">
        <div class="relative w-full max-w-2xl bg-[#0f1318] border border-red-500/30 rounded-2xl shadow-2xl overflow-hidden">
          <div class="flex items-center justify-between px-6 py-5 border-b border-red-500/20">
            <div class="flex items-center gap-3">
              <div class="size-9 rounded-xl bg-red-500/15 border border-red-500/30 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-red-300 text-lg">warning</span>
              </div>
              <div>
                <h2 class="text-base font-bold">Clear instance data</h2>
                <p class="text-[11px] text-slate-500">Destructive · scoped to instance <span class="font-mono text-slate-300">{{ route.params.id }}</span></p>
              </div>
            </div>
            <button @click="closeClearState"
                    :disabled="clearApplying || clearPreviewLoading"
                    class="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>

          <div class="px-6 py-5 space-y-5 max-h-[70vh] overflow-y-auto">
            <!-- Scope picker -->
            <div class="space-y-2">
              <p class="text-xs font-semibold uppercase tracking-widest text-slate-500">Scope</p>
              <div
                v-for="opt in clearScopeOptions"
                :key="opt.value"
                @click="!clearOk && (clearScope = opt.value)"
                :class="[
                  'rounded-xl border px-4 py-3 cursor-pointer transition-colors',
                  clearScope === opt.value
                    ? 'border-red-500/40 bg-red-500/5'
                    : 'border-border-subtle hover:border-slate-600',
                  clearOk ? 'opacity-50 cursor-not-allowed' : '',
                ]"
              >
                <div class="flex items-start gap-3">
                  <input type="radio"
                         :checked="clearScope === opt.value"
                         :value="opt.value"
                         :disabled="clearOk"
                         @change="clearScope = opt.value"
                         class="mt-1 accent-red-400 shrink-0"
                  />
                  <div>
                    <div class="text-sm font-semibold text-slate-200">{{ opt.label }}</div>
                    <p class="text-[11px] text-slate-500 leading-relaxed mt-0.5">{{ opt.blurb }}</p>
                  </div>
                </div>
              </div>
            </div>

            <!-- Preview button + counts -->
            <div class="flex items-center gap-3">
              <button
                @click="previewClearState"
                :disabled="clearPreviewLoading || clearApplying || clearOk"
                class="inline-flex items-center gap-2 text-xs font-semibold px-3 py-2 rounded-lg border border-amber-500/30 text-amber-200 hover:bg-amber-500/10 transition-colors disabled:opacity-50"
              >
                <span class="material-symbols-outlined text-[14px]">visibility</span>
                {{ clearPreviewLoading ? 'Counting…' : (clearPreview ? 'Refresh preview' : 'Preview (dry run)') }}
              </button>
              <span v-if="clearPreview" class="text-[11px] text-slate-500">
                Would delete <span class="text-red-300 font-bold">{{ clearPreview.total_would_delete }}</span> row(s) across {{ clearPreview.tables.length }} table(s).
              </span>
            </div>

            <!-- Per-table preview -->
            <div v-if="clearPreview" class="rounded-xl border border-border-subtle overflow-hidden">
              <table class="w-full text-xs">
                <thead class="bg-white/[0.02] text-slate-500">
                  <tr>
                    <th class="text-left px-4 py-2 font-semibold">Table</th>
                    <th class="text-right px-4 py-2 font-semibold">{{ clearOk ? 'Deleted' : 'Would delete' }}</th>
                    <th class="text-left px-4 py-2 font-semibold">Notes</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in (clearResult || clearPreview).tables" :key="row.table"
                      class="border-t border-border-subtle/60">
                    <td class="px-4 py-2 font-mono text-slate-300">{{ row.table }}</td>
                    <td class="px-4 py-2 text-right font-mono"
                        :class="(clearOk ? row.deleted : row.would_delete) > 0 ? 'text-red-300' : 'text-slate-600'">
                      {{ clearOk ? row.deleted : row.would_delete }}
                    </td>
                    <td class="px-4 py-2 text-slate-500">
                      <span v-if="row.skipped">{{ row.reason || 'skipped' }}</span>
                      <span v-else-if="row.would_delete === 0 && !clearOk">no matching rows</span>
                      <span v-else-if="clearOk" class="text-emerald-400">deleted</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- Confirm typed-instance-id gate -->
            <div v-if="clearPreview && !clearOk" class="space-y-2">
              <p class="text-xs text-slate-400">
                To confirm, type the instance id
                <span class="font-mono text-slate-200">{{ route.params.id }}</span>
                below.
              </p>
              <input
                :value="clearConfirm"
                @input="clearConfirm = $event.target.value"
                :disabled="clearApplying"
                :placeholder="String(route.params.id)"
                class="w-full bg-surface border border-red-500/30 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-red-400 font-mono"
              />
            </div>

            <!-- Status / result -->
            <div v-if="clearMsg" :class="[
                   'rounded-xl px-3 py-2 text-xs leading-relaxed',
                   clearOk
                     ? 'border border-emerald-500/30 bg-emerald-500/5 text-emerald-300'
                     : 'border border-red-500/30 bg-red-500/5 text-red-300',
                 ]">
              {{ clearMsg }}
            </div>
          </div>

          <div class="px-6 py-4 border-t border-border-subtle flex gap-3 shrink-0">
            <button @click="closeClearState"
                    :disabled="clearApplying"
                    class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-50">
              {{ clearOk ? 'Close' : 'Cancel' }}
            </button>
            <button v-if="!clearOk"
                    @click="applyClearState"
                    :disabled="!clearPreview || !clearConfirmOk || clearApplying || clearPreviewLoading"
                    class="flex-1 py-2.5 rounded-lg bg-red-500 text-white text-sm font-bold hover:brightness-110 transition-all disabled:opacity-40 disabled:cursor-not-allowed">
              <template v-if="clearApplying">Clearing…</template>
              <template v-else>Confirm and clear</template>
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Backtest Action Confirmation Modal ───────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showLlmConfigModal"
        class="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
      >
        <div class="relative w-full max-w-lg bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl flex flex-col max-h-[90vh]">
          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle shrink-0">
            <div>
              <h2 class="text-base font-bold">{{ llmConfigTarget?.group?.label || 'LLM Config' }}</h2>
              <p class="text-[11px] text-slate-500 mt-0.5">Select a model from your saved configurations.</p>
            </div>
            <button @click="closeLlmConfigModal" class="text-slate-500 hover:text-slate-300 transition-colors">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>

          <div class="flex-1 overflow-y-auto px-6 py-5 space-y-4">
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Model</label>
              <select
                v-model="llmConfigDraft.modelId"
                @change="onModelSelect"
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors"
              >
                <option value="" disabled>Select a model...</option>
                <option v-for="m in savedModels" :key="m.id" :value="m.id">{{ m.name }} — {{ getLlmProviderLabel(m.provider) }} / {{ m.model }}</option>
              </select>
            </div>

            <div v-if="llmConfigDraft.modelId" class="rounded-lg border border-border-subtle bg-surface px-4 py-3 space-y-1.5">
              <div class="flex items-center gap-2 text-xs">
                <span class="text-slate-500 w-20 shrink-0">Provider</span>
                <span class="text-slate-200">{{ getLlmProviderLabel(llmConfigDraft.provider) }}</span>
              </div>
              <div class="flex items-center gap-2 text-xs">
                <span class="text-slate-500 w-20 shrink-0">Model</span>
                <span class="text-slate-200 font-mono">{{ llmConfigDraft.model }}</span>
              </div>
              <div class="flex items-center gap-2 text-xs">
                <span class="text-slate-500 w-20 shrink-0">Effort</span>
                <span class="text-slate-200">{{ effortLabel(llmConfigDraft) }}</span>
              </div>
            </div>

            <div v-if="savedModels.length === 0" class="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-3 text-[11px] leading-relaxed text-amber-300/80">
              No models saved yet. Go to the <strong>Models</strong> tab to add one first.
            </div>

            <div
              v-if="llmConfigMsg"
              class="rounded-lg px-3 py-3 text-[11px] leading-relaxed border border-red-500/20 bg-red-500/10 text-red-300"
            >
              {{ llmConfigMsg }}
            </div>
          </div>

          <div class="px-6 pb-6 pt-4 border-t border-border-subtle flex gap-3 shrink-0">
            <button @click="closeLlmConfigModal"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors">
              Cancel
            </button>
            <button @click="saveLlmConfigModal"
              :disabled="!llmConfigDraft.modelId"
              class="flex-1 py-2.5 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 transition-all disabled:opacity-60 disabled:cursor-not-allowed">
              Save
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- Benzinga API Test Modal -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showBenzingaTestModal"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="showBenzingaTestModal = false"
      >
        <div class="relative w-full max-w-md bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">
          <div class="flex items-center justify-between px-6 py-4 border-b border-border-subtle">
            <h3 class="text-sm font-semibold text-slate-100">Benzinga API Access Test</h3>
            <button @click="showBenzingaTestModal = false" class="text-slate-500 hover:text-slate-300 transition-colors text-lg">&times;</button>
          </div>
          <div class="px-6 py-4 space-y-3 max-h-[60vh] overflow-y-auto">
            <div v-if="benzingaTestLoading" class="text-center py-8">
              <div class="inline-block animate-spin rounded-full h-6 w-6 border-2 border-amber-400 border-t-transparent"></div>
              <p class="text-xs text-slate-500 mt-3">Testing Benzinga API sources...</p>
            </div>
            <template v-else-if="benzingaTestResults">
              <div class="rounded-lg px-3 py-2 text-xs font-medium"
                :class="benzingaTestResults.ok ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400' : 'bg-red-500/10 border border-red-500/20 text-red-400'">
                {{ benzingaTestResults.message }}
              </div>
              <div v-for="r in benzingaTestResults.results" :key="r.source"
                class="flex items-center justify-between rounded-lg border px-3 py-2.5"
                :class="r.ok ? 'border-emerald-500/20 bg-emerald-500/5' : 'border-red-500/20 bg-red-500/5'">
                <div>
                  <span class="text-xs font-medium" :class="r.ok ? 'text-emerald-400' : 'text-red-400'">{{ r.label }}</span>
                  <p v-if="r.error" class="text-[10px] text-slate-500 mt-0.5">{{ r.error }}</p>
                </div>
                <span class="text-[10px] font-mono px-2 py-0.5 rounded-full"
                  :class="r.ok ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'">
                  {{ r.ok ? 'OK' : `${r.status || 'ERR'}` }}
                </span>
              </div>
            </template>
          </div>
          <div class="px-6 py-4 border-t border-border-subtle flex justify-end">
            <button @click="showBenzingaTestModal = false"
              class="rounded-lg px-4 py-2 text-xs font-semibold text-slate-100 bg-primary hover:bg-primary/80 transition-colors">
              Acknowledge
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showBtConfirm"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeBtConfirm"
      >
        <div class="relative w-full max-w-sm bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">
          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle">
            <div class="flex items-center gap-3">
              <div class="size-9 rounded-xl flex items-center justify-center shrink-0"
                :class="{
                  'bg-red-500/10 border border-red-500/20':       btConfirmAction === 'stop',
                  'bg-violet-500/10 border border-violet-500/20': btConfirmAction === 'pause',
                  'bg-sky-500/10 border border-sky-500/20':       btConfirmAction === 'resume',
                }">
                <span class="material-symbols-outlined text-lg"
                  :class="{
                    'text-red-400':    btConfirmAction === 'stop',
                    'text-violet-400': btConfirmAction === 'pause',
                    'text-sky-400':    btConfirmAction === 'resume',
                  }">{{ btConfirmMeta.icon }}</span>
              </div>
              <div>
                <h2 class="text-base font-bold">{{ btConfirmMeta.label }} Backtest</h2>
                <p class="text-[11px] text-slate-500 font-mono">#{{ btConfirmId }}</p>
              </div>
            </div>
            <button @click="closeBtConfirm" :disabled="btConfirmBusy" class="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>
          <div class="px-6 py-5 space-y-4">
            <p class="text-sm text-slate-300">{{ btConfirmMeta.desc }}</p>
            <Transition name="slide-up">
              <div v-if="btConfirmMsg" class="rounded-lg px-4 py-3 text-sm font-medium"
                :class="btConfirmOk
                  ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                  : 'bg-red-500/10 border border-red-500/20 text-red-400'">
                <span v-if="btConfirmBusy" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-base animate-spin">progress_activity</span>
                  {{ btConfirmMsg }}
                </span>
                <span v-else>{{ btConfirmMsg }}</span>
              </div>
            </Transition>
          </div>
          <div class="px-6 pb-6 flex gap-3">
            <button @click="closeBtConfirm" :disabled="btConfirmBusy"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40">
              Cancel
            </button>
            <button @click="executeBtConfirm" :disabled="btConfirmBusy"
              class="flex-1 py-2.5 rounded-lg text-sm font-bold transition-all disabled:opacity-50 flex items-center justify-center gap-2"
              :class="{
                'bg-red-500/80 hover:bg-red-500 text-white':    btConfirmAction === 'stop',
                'bg-violet-600 hover:bg-violet-500 text-white': btConfirmAction === 'pause',
                'bg-primary hover:brightness-110 text-white':   btConfirmAction === 'resume',
              }">
              <span v-if="btConfirmBusy" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              <span v-else class="material-symbols-outlined text-base">{{ btConfirmMeta.icon }}</span>
              {{ btConfirmBusy ? 'Working...' : btConfirmMeta.label }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Create Backtest Modal ─────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showCreateBacktest"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeCreateBacktest"
      >
        <div class="relative w-full max-w-md bg-[#0f0a1c] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">

          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle">
            <div class="flex items-center gap-3">
              <div class="size-9 rounded-xl bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-primary text-lg">play_circle</span>
              </div>
              <div>
                <h2 class="text-base font-bold">New Backtest</h2>
                <p class="text-[11px] text-slate-500 font-mono mt-0.5">{{ inst?.name || instanceId }}</p>
              </div>
            </div>
            <button @click="closeCreateBacktest" :disabled="creatingBacktest" class="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>

          <div class="px-6 py-5 space-y-4">
            <!-- Stocks -->
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Stocks <span class="text-red-400">*</span></label>
              <input
                v-model="createBtForm.stocks"
                type="text"
                placeholder="e.g. AAPL, MSFT, TSLA"
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono uppercase"
              />
              <p class="text-[11px] text-slate-600 mt-1">Comma-separated ticker symbols</p>
            </div>

            <!-- Date range -->
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1.5">Start Date <span class="text-red-400">*</span></label>
                <input
                  v-model="createBtForm.start_date"
                  type="date"
                  class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors"
                />
              </div>
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1.5">End Date <span class="text-red-400">*</span></label>
                <input
                  v-model="createBtForm.end_date"
                  type="date"
                  class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors"
                />
              </div>
            </div>

            <!-- Granularity -->
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Granularity</label>
              <div class="flex gap-1.5 flex-wrap mb-2">
                <button
                  v-for="g in GRANULARITIES"
                  :key="g.value"
                  @click="createBtForm.granularity = g.value"
                  class="px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all"
                  :class="createBtForm.granularity === g.value
                    ? 'bg-primary/15 text-primary border-primary/40'
                    : 'border-border-subtle text-slate-400 hover:text-slate-200'"
                >
                  {{ g.label }}
                </button>
              </div>
              <!-- Custom seconds input — typing any positive integer overrides
                   the preset selection. The submit handler always sends this
                   value (as a string) to the API. Range hint matches the
                   dual-cadence backtest harness preflight: [30, 3600] for
                   harness runs; non-harness backtests can use any positive
                   integer seconds. -->
              <div class="flex items-center gap-2">
                <label class="text-[11px] text-slate-500 whitespace-nowrap">Custom (seconds)</label>
                <input
                  v-model="createBtForm.granularity"
                  type="number"
                  min="1"
                  step="1"
                  placeholder="3600"
                  class="flex-1 bg-surface border border-border-subtle rounded-lg px-3 py-1.5 text-xs text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono"
                />
                <span class="text-[10px] text-slate-600 font-mono">{{ fmtGranularity(createBtForm.granularity) }}</span>
              </div>
            </div>

            <!-- Initial cash -->
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Initial Cash <span class="text-slate-600">($)</span></label>
              <input
                v-model="createBtForm.initial_cash"
                type="number"
                min="1"
                step="1000"
                placeholder="100000"
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono"
              />
            </div>

            <Transition name="slide-up">
              <div
                v-if="createBtMsg"
                class="rounded-lg px-4 py-3 text-sm font-medium"
                :class="createBtOk
                  ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                  : 'bg-red-500/10 border border-red-500/20 text-red-400'"
              >
                <span v-if="creatingBacktest" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-base animate-spin">progress_activity</span>
                  {{ createBtMsg }}
                </span>
                <span v-else>{{ createBtMsg }}</span>
              </div>
            </Transition>
          </div>

          <div class="px-6 pb-6 flex gap-3">
            <button @click="closeCreateBacktest" :disabled="creatingBacktest"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40">
              Cancel
            </button>
            <button @click="submitCreateBacktest" :disabled="creatingBacktest"
              class="flex-1 py-2.5 rounded-lg bg-primary text-white text-sm font-bold hover:brightness-110 transition-all disabled:opacity-50 flex items-center justify-center gap-2">
              <span v-if="creatingBacktest" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              <span v-else class="material-symbols-outlined text-base">play_circle</span>
              {{ creatingBacktest ? 'Queuing...' : 'Run Backtest' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Link Strategy Modal ───────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showLinkStrategy"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeLinkStrategy"
      >
        <div class="w-full max-w-sm bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">
          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle">
            <div class="flex items-center gap-3">
              <div class="size-9 rounded-xl bg-violet-500/10 border border-violet-500/20 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-violet-400 text-lg">link</span>
              </div>
              <h2 class="text-base font-bold">Link Strategy</h2>
            </div>
            <button @click="closeLinkStrategy" :disabled="linkStrategyBusy" class="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>
          <div class="px-6 py-5 space-y-4">
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Strategy</label>
              <select
                v-model="linkStrategyId"
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors"
              >
                <option value="">Select a strategy...</option>
                <option v-for="s in availStrategies" :key="s.id" :value="String(s.id)">{{ s.name || s.id }}</option>
              </select>
            </div>
            <Transition name="slide-up">
              <div
                v-if="linkStrategyMsg"
                class="rounded-lg px-4 py-3 text-xs font-mono break-words"
                :class="linkStrategyOk ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400' : 'bg-red-500/10 border border-red-500/20 text-red-400'"
              >
                {{ linkStrategyMsg }}
              </div>
            </Transition>
          </div>
          <div class="px-6 pb-6 flex gap-3">
            <button @click="closeLinkStrategy" :disabled="linkStrategyBusy"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40">
              Cancel
            </button>
            <button @click="submitLinkStrategy" :disabled="linkStrategyBusy || !linkStrategyId"
              class="flex-1 py-2.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-bold transition-all disabled:opacity-50 flex items-center justify-center gap-2">
              <span v-if="linkStrategyBusy" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              {{ linkStrategyBusy ? 'Linking...' : 'Link Strategy' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Unlink Strategy Confirmation ─────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showUnlinkStrategy"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeUnlinkStrategy"
      >
        <div class="w-full max-w-sm bg-[#0d1117] border border-red-500/30 rounded-2xl shadow-2xl overflow-hidden">
          <div class="flex items-center gap-3 px-6 py-4 border-b border-red-500/20">
            <div class="size-9 rounded-xl bg-red-500/10 border border-red-500/20 flex items-center justify-center shrink-0">
              <span class="material-symbols-outlined text-red-400 text-base">link_off</span>
            </div>
            <div>
              <p class="text-sm font-semibold text-slate-100">Unlink Strategy</p>
              <p class="text-[11px] text-red-400/80 mt-0.5">{{ inst?.strategy?.name || `Strategy #${inst?.strategy_id}` }}</p>
            </div>
          </div>
          <div class="px-6 py-5">
            <p class="text-sm text-slate-400 leading-relaxed">This will remove the strategy link from this instance. The strategy itself will not be deleted. You can re-link it later.</p>
          </div>
          <div class="flex items-center justify-end gap-2 px-6 py-4 border-t border-border-subtle">
            <p v-if="unlinkStrategyMsg" class="flex-1 text-xs" :class="unlinkStrategyOk ? 'text-emerald-400' : 'text-red-400'">{{ unlinkStrategyMsg }}</p>
            <button @click="closeUnlinkStrategy" :disabled="unlinkStrategyBusy" class="px-4 py-2 rounded-lg text-sm font-medium text-slate-400 hover:text-slate-200 hover:bg-surface border border-transparent hover:border-border-subtle transition-colors disabled:opacity-40">Cancel</button>
            <button @click="submitUnlinkStrategy" :disabled="unlinkStrategyBusy" class="px-4 py-2 rounded-lg text-sm font-semibold text-white bg-red-600 hover:bg-red-500 disabled:opacity-40 transition-colors">
              <span v-if="unlinkStrategyBusy" class="material-symbols-outlined text-[14px] animate-spin align-middle mr-1">progress_activity</span>
              Unlink
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Unlink Brokerage Confirmation ────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showUnlinkBrokerage"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeUnlinkBrokerage"
      >
        <div class="w-full max-w-sm bg-[#0d1117] border border-red-500/30 rounded-2xl shadow-2xl overflow-hidden">
          <div class="flex items-center gap-3 px-6 py-4 border-b border-red-500/20">
            <div class="size-9 rounded-xl bg-red-500/10 border border-red-500/20 flex items-center justify-center shrink-0">
              <span class="material-symbols-outlined text-red-400 text-base">link_off</span>
            </div>
            <div>
              <p class="text-sm font-semibold text-slate-100">Unlink Brokerage</p>
              <p class="text-[11px] text-red-400/80 mt-0.5">{{ inst?.brokerage?.account_name || 'Current brokerage' }}</p>
            </div>
          </div>
          <div class="px-6 py-5">
            <p class="text-sm text-slate-400 leading-relaxed">This will remove the brokerage link from this instance. The brokerage account will not be deleted. You can re-link it later.</p>
          </div>
          <div class="flex items-center justify-end gap-2 px-6 py-4 border-t border-border-subtle">
            <p v-if="unlinkBrokerageMsg" class="flex-1 text-xs" :class="unlinkBrokerageOk ? 'text-emerald-400' : 'text-red-400'">{{ unlinkBrokerageMsg }}</p>
            <button @click="closeUnlinkBrokerage" :disabled="unlinkBrokerageBusy" class="px-4 py-2 rounded-lg text-sm font-medium text-slate-400 hover:text-slate-200 hover:bg-surface border border-transparent hover:border-border-subtle transition-colors disabled:opacity-40">Cancel</button>
            <button @click="submitUnlinkBrokerage" :disabled="unlinkBrokerageBusy" class="px-4 py-2 rounded-lg text-sm font-semibold text-white bg-red-600 hover:bg-red-500 disabled:opacity-40 transition-colors">
              <span v-if="unlinkBrokerageBusy" class="material-symbols-outlined text-[14px] animate-spin align-middle mr-1">progress_activity</span>
              Unlink
            </button>
          </div>
        </div>
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
