<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import LiveReadinessCard from '../components/LiveReadinessCard.vue'
import PendingSignalsCard from '../components/swing/PendingSignalsCard.vue'
import WheelPanel from '../components/swing/WheelPanel.vue'
import { swingLanesOf } from '../utils/swing.js'
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

// Crypto and Kalshi instances are routed to their own detail views; `kind` is
// absent on equity rows created before the field existed, so anything that is
// not explicitly one of those is an equity instance.
const isStockInstance = computed(
  () => !['crypto', 'kalshi'].includes(String(inst.value?.kind ?? '').toLowerCase()))

// The swing / wheel approval cards appear only on an equity instance whose
// strategy document carries one of those lanes (spec 2026-09-24 section 10).
const swingLanes = computed(() => swingLanesOf(inst.value?.strategy))
// Shared between the two swing cards (iOS InstanceDetailView passes the same):
// the pending queue's ids tell the wheel's scan rows which can jump to a
// card; a decision makes the wheel re-read its signal links; the wheel's
// cash feeds the order review's collateral warning.
const swingPendingIds = ref([])
const swingDecisions  = ref(0)
const wheelCash       = ref(null)

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

// ── Stocks ────────────────────────────────────────────────────────────────────
const showAddStock  = ref(false)
const addStockSym   = ref('')
const addStockBusy  = ref(false)
const addStockMsg   = ref('')
const removingStock = ref('')

function openAddStock() {
  addStockSym.value  = ''
  addStockMsg.value  = ''
  addStockBusy.value = false
  showAddStock.value = true
}

function closeAddStock() {
  if (addStockBusy.value) return
  showAddStock.value = false
}

async function submitAddStock() {
  const sym = addStockSym.value.trim().toUpperCase()
  if (!sym) { addStockMsg.value = 'Symbol is required'; return }
  addStockBusy.value = true
  addStockMsg.value  = ''
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId.value)}/stocks`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ symbol: sym }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    await fetchInstance()
    showAddStock.value = false
  } catch (e) {
    addStockMsg.value = e.message || 'Failed to add stock'
  } finally {
    addStockBusy.value = false
  }
}

async function removeStock(symbol) {
  removingStock.value = symbol
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId.value)}/stocks/${encodeURIComponent(symbol)}`, {
      method: 'DELETE', headers: authHeaders(),
    })
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${res.status}`) }
    await fetchInstance()
  } catch (e) {
    alert(`Remove failed: ${e.message}`)
  } finally {
    removingStock.value = ''
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
  initial_value: '',
  clean_room_mode: false,
})
// Snapshot of what the row held when the modal opened. The clean-room fields
// are sent only when they differ from it: PATCH treats an omitted field as
// "leave it alone", so re-sending an unchanged baseline on every rename is a
// write to a real-money field that nobody asked for.
const editInfoOriginal = ref({ initial_value: '', clean_room_mode: false })

function openEditInfo() {
  if (!inst.value) return
  editInfoMsg.value = ''
  editInfoOk.value = false
  editInfoSaving.value = false
  const baseline = inst.value.initial_value != null ? String(inst.value.initial_value) : ''
  const cleanRoom = !!inst.value.clean_room_mode
  editInfoForm.value = {
    name: inst.value.name || '',
    granularity: String(inst.value.granularity_time_increment || '60'),
    max_usage: inst.value.max_usage != null ? String(inst.value.max_usage) : '',
    initial_value: baseline,
    clean_room_mode: cleanRoom,
  }
  editInfoOriginal.value = { initial_value: baseline, clean_room_mode: cleanRoom }
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

    // The clean-room baseline every drawdown is measured against. Only sent
    // when the operator actually changed it.
    const baselineText = String(editInfoForm.value.initial_value ?? '').trim()
    if (baselineText !== editInfoOriginal.value.initial_value) {
      if (baselineText === '') {
        // PATCH reads an absent field as "unchanged", so blanking the box
        // would look like a clear and do nothing. Say so instead of
        // silently discarding the edit.
        throw new Error('Initial value cannot be cleared here — set a positive baseline or leave it unchanged')
      }
      const parsedBaseline = Number(baselineText)
      if (!Number.isFinite(parsedBaseline) || parsedBaseline <= 0) {
        throw new Error('Initial value must be a number greater than 0')
      }
      payload.initial_value = parsedBaseline
    }
    if (!!editInfoForm.value.clean_room_mode !== editInfoOriginal.value.clean_room_mode) {
      payload.clean_room_mode = !!editInfoForm.value.clean_room_mode
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
  // Sign before the currency symbol: +$1,284.55 / -$412.10
  return (n >= 0 ? '+' : '-') + fmtMoney(Math.abs(n))
}

function pnlClass(v) {
  if (v == null) return 'text-slate-500'
  return Number(v) >= 0 ? 'text-gain' : 'text-loss'
}

// Backtest status as a dot plus a word (the dashboard pattern). Green only
// for a run that is actually running; paused is amber; failures red; the
// rest (queued, completed, stopped) grey.
const statusDot = {
  running:             'ui-dot-live',
  paused:              'ui-dot-warn',
  paused_llm_critical: 'ui-dot-warn',
  error:               'ui-dot-error',
  failed:              'ui-dot-error',
}

function btStatusClass(s) {
  const key = (s || '').toLowerCase()
  return statusDot[key] || 'ui-dot-off'
}

function btStatusLabel(s) {
  const t = String(s || 'queued').replace(/_/g, ' ')
  return t.charAt(0).toUpperCase() + t.slice(1)
}

// Result line inside the modals: neutral while a request is in flight,
// gain on success, loss on failure.
function msgClass(ok, working = false) {
  if (working) return 'border-white/[0.07] bg-raised text-slate-300'
  return ok ? 'border-gain/20 bg-gain/5 text-gain' : 'border-loss/20 bg-loss/5 text-loss'
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
// Preserve-history popup: shown when a save would change the Nexus model
// identity for an instance that has existing live state.
const showPreserveHistory = ref(false)
const preserveHistoryInfo = ref(null)  // { instances: [{ base_instance_id, ... }] }
const preserveHistoryAffectedNames = computed(() => {
  const list = (preserveHistoryInfo.value?.instances || [])
    .filter(i => i.would_rebuild && i.snapshot_exists)
    .map(i => i.base_instance_id)
  return list.length ? list.join(', ') : 'this instance'
})
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
  // Bedrock-specific — region + reasoning, surfaced in the Effort cell.
  bedrockRegion: '',
  bedrockReasoning: '',
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
  if (draft.provider === 'bedrock') {
    const r = String(draft.bedrockReasoning || '').trim().toLowerCase()
    if (!r || r === 'off') return 'Off'
    return r.charAt(0).toUpperCase() + r.slice(1)
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
      llmConfigDraft.value.bedrockRegion = m.bedrock_region || ''
      llmConfigDraft.value.bedrockReasoning = m.bedrock_reasoning || ''
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
    bedrockRegion: m.bedrock_region || '',
    bedrockReasoning: m.bedrock_reasoning || '',
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
  editStrategyMsg.value    = 'Checking for model changes...'
  editStrategyOk.value     = false
  // Ask the backend whether this save would rebuild Nexus history. If so, let
  // the operator choose to preserve it; otherwise save straight through.
  try {
    const pres = await fetch(`${API_BASE}/strategies/${d.id}/config-change-preview`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ strategies: d.strategies }),
    })
    const presBody = await pres.json().catch(() => ({}))
    if (pres.ok && presBody.needs_prompt) {
      preserveHistoryInfo.value = presBody
      showPreserveHistory.value = true
      editStrategySaving.value  = false
      editStrategyMsg.value     = ''
      return
    }
  } catch { /* preview is advisory — fall through to a normal save */ }
  await doSaveStrategy(false)
}

async function doSaveStrategy(preserveHistory) {
  const d = editStrategyData.value
  showPreserveHistory.value = false
  editStrategySaving.value = true
  editStrategyMsg.value    = 'Saving...'
  editStrategyOk.value     = false
  try {
    const res = await fetch(`${API_BASE}/strategies/${d.id}`, {
      method: 'PUT',
      headers: authHeaders(),
      body: JSON.stringify({ name: d.name.trim(), strategies: d.strategies, preserve_history: !!preserveHistory }),
    })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`)
    if (preserveHistory && body.restamp_error) {
      // Config saved, but the re-stamp failed — history was NOT preserved, so
      // the next boot will rebuild. Surface it instead of a false success.
      editStrategyOk.value  = false
      editStrategyMsg.value = `Saved, but preserving history failed: ${body.restamp_error}. Next start will rebuild.`
      await fetchInstance()
      return
    }
    editStrategyOk.value  = true
    editStrategyMsg.value = preserveHistory
      ? 'Saved. History preserved — takes effect on next Stop→Start.'
      : 'Strategy saved.'
    await fetchInstance()
    setTimeout(() => { showEditStrategy.value = false }, 1400)
  } catch (e) {
    editStrategyOk.value  = false
    editStrategyMsg.value = e.message || 'Something went wrong'
  } finally {
    editStrategySaving.value = false
  }
}

function cancelPreserveHistory() {
  showPreserveHistory.value = false
  editStrategyMsg.value = 'Save cancelled.'
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
    <main class="ui-page">

      <!-- Breadcrumb + back -->
      <nav class="mb-4 flex items-center gap-1.5 text-sm text-slate-500 min-w-0" aria-label="Breadcrumb">
        <button @click="router.push('/instances')" class="inline-flex items-center gap-1 hover:text-slate-200 transition-colors duration-150 ease-out">
          <span class="material-symbols-outlined text-[16px]">arrow_back</span>
          Instances
        </button>
        <span class="text-slate-600" aria-hidden="true">/</span>
        <span class="text-slate-300 truncate max-w-xs">{{ inst?.name || instanceId }}</span>
      </nav>

      <!-- Loading: static blocks shaped like the page -->
      <div v-if="loading">
        <span class="sr-only">Loading instance</span>
        <div aria-hidden="true">
          <div class="h-7 w-56 ui-skel"></div>
          <div class="h-4 w-72 ui-skel mt-2"></div>
          <div class="mt-8 grid grid-cols-1 lg:grid-cols-2 gap-x-6 gap-y-10">
            <div v-for="n in 4" :key="n">
              <div class="h-4 w-28 ui-skel mb-5"></div>
              <div class="h-56 ui-card"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- Error -->
      <div v-else-if="error" class="ui-error">
        Couldn't load this instance: {{ error }}
      </div>

      <template v-else-if="inst">

        <!-- ── Header ──────────────────────────────────────────────────────── -->
        <header class="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
          <div class="min-w-0">
            <h1 class="ui-page-title truncate">{{ inst.name || inst.id }}</h1>
            <p class="ui-page-sub flex flex-wrap items-center gap-x-2 gap-y-1">
              <span class="truncate">{{ inst.id }}</span>
              <span class="text-slate-600" aria-hidden="true">·</span>
              <span>Created by {{ (inst.created_by || 'user') === 'ai' ? 'AI' : 'user' }}</span>
              <span class="text-slate-600" aria-hidden="true">·</span>
              <span
                class="inline-flex items-center gap-1.5"
                :class="inst.crashed ? 'text-loss' : inst.runCommand ? 'text-slate-300' : ''"
              >
                <span class="ui-dot" :class="inst.crashed ? 'ui-dot-error' : inst.runCommand ? 'ui-dot-live' : 'ui-dot-off'"></span>
                {{ inst.crashed ? 'Crashed' : inst.runCommand ? 'Running' : 'Stopped' }}
              </span>
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <button
              @click="toggleRun"
              :disabled="busy"
              class="ui-btn"
              :class="inst.runCommand ? 'text-loss' : ''"
            >
              <span v-if="busy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ inst.runCommand ? 'Stop' : 'Start' }}
            </button>
            <button
              @click="router.push(`/instances/${instanceId}/live`)"
              class="ui-btn"
            >
              Live trading
            </button>
            <button
              @click="fetchInstance"
              class="ui-icon-btn"
              title="Refresh"
              aria-label="Refresh instance"
            >
              <span class="material-symbols-outlined text-[18px]">refresh</span>
            </button>
          </div>
        </header>

        <!-- ── Overview ────────────────────────────────────────────────────── -->
        <div class="mt-8 grid grid-cols-1 lg:grid-cols-2 gap-x-6 gap-y-10">

          <!-- Instance info -->
          <section class="flex flex-col min-w-0" aria-labelledby="inst-info-title">
            <div class="sec-head">
              <h2 id="inst-info-title" class="ui-section-title">Instance info</h2>
              <div class="flex items-center gap-2">
                <button
                  @click="openClearState"
                  class="ui-btn text-loss"
                  title="Clear lookback / strategy cache / full instance state"
                >Clear data</button>
                <button @click="openEditInfo" class="ui-btn">Edit</button>
              </div>
            </div>
            <div class="ui-card ui-rows flex-1">
              <div class="kv">
                <span>Granularity</span>
                <span class="text-slate-100 tabular-nums">{{ fmtGranularity(inst.granularity_time_increment) }}</span>
              </div>
              <div class="kv">
                <span>Uptime</span>
                <span class="tabular-nums" :class="inst.runCommand ? 'text-slate-100' : 'text-slate-500'">
                  {{ inst.runCommand ? fmtUptime(liveSecs) : '—' }}
                </span>
              </div>
              <div class="kv">
                <span>Created by</span>
                <span class="text-slate-100">{{ (inst.created_by || 'user') === 'ai' ? 'AI engine' : 'User' }}</span>
              </div>
              <div class="kv">
                <span>Max usage</span>
                <span class="text-slate-100 tabular-nums">{{ inst.max_usage != null ? fmtMoney(inst.max_usage) : '—' }}</span>
              </div>
              <!-- The clean-room pair the live-start preflight reads. Stored
                   values: LIVE_CLEAN_ROOM_MODE / LIVE_INITIAL_VALUE still beat
                   the row at launch. -->
              <div class="kv">
                <span>Initial value</span>
                <span class="tabular-nums" :class="inst.initial_value != null ? 'text-slate-100' : 'text-slate-500'">
                  {{ inst.initial_value != null ? fmtMoney(inst.initial_value) : '—' }}
                </span>
              </div>
              <div class="kv">
                <span>Clean-room mode</span>
                <span :class="inst.clean_room_mode ? 'text-slate-100' : 'text-slate-500'">
                  {{ inst.clean_room_mode ? 'On' : 'Off' }}
                </span>
              </div>
              <p
                v-if="inst.clean_room_mode && inst.initial_value == null"
                class="px-5 py-3 flex items-start gap-2 text-xs text-slate-400 leading-relaxed"
              >
                <span class="ui-dot ui-dot-warn mt-1.5"></span>
                <span>Clean-room mode is on with no baseline — the broker refuses to
                launch until an initial value is set.</span>
              </p>
            </div>
          </section>

          <!-- Brokerage -->
          <section class="flex flex-col min-w-0" aria-labelledby="inst-brokerage-title">
            <div class="sec-head">
              <h2 id="inst-brokerage-title" class="ui-section-title">Brokerage</h2>
              <div class="flex items-center gap-2">
                <button
                  v-if="inst.brokerage"
                  @click="openUnlinkBrokerage"
                  class="ui-btn text-loss"
                >Unlink</button>
                <button
                  v-if="inst.brokerage"
                  @click="openLinkBrokerage"
                  class="ui-btn"
                >Edit</button>
              </div>
            </div>
            <div class="ui-card flex-1 flex flex-col">
              <div v-if="inst.brokerage" class="ui-rows">
                <div class="px-5 py-3 min-w-0">
                  <p class="text-sm text-slate-100 truncate">{{ inst.brokerage.account_name }}</p>
                  <p class="text-xs text-slate-500 mt-0.5 capitalize">{{ inst.brokerage.brokerage_type }}</p>
                </div>
                <div v-if="inst.brokerage.alpaca_account_number" class="kv">
                  <span>Account #</span>
                  <span class="text-slate-100 tabular-nums">{{ inst.brokerage.alpaca_account_number }}</span>
                </div>
                <div v-if="inst.brokerage.alpaca_paper != null" class="kv">
                  <span>Mode</span>
                  <span class="text-slate-100">{{ inst.brokerage.alpaca_paper ? 'Paper' : 'Live' }}</span>
                </div>
                <div v-if="inst.max_usage != null" class="kv">
                  <span>Max usage</span>
                  <span class="text-slate-100 tabular-nums">{{ fmtMoney(inst.max_usage) }}</span>
                </div>
              </div>
              <div v-else class="px-5 py-8 flex flex-col items-center gap-3 text-center">
                <p class="text-sm text-slate-400">No brokerage linked</p>
                <button @click="openLinkBrokerage" class="ui-btn">Link brokerage</button>
              </div>

              <!-- Data-source brokerage (optional, for market data/news) -->
              <div class="mt-auto px-5 py-4 border-t border-white/[0.06]">
                <div class="flex items-start justify-between gap-3">
                  <div class="min-w-0">
                    <p class="text-sm text-slate-200">Market-data source</p>
                    <p class="text-xs text-slate-500 mt-0.5">Optional Alpaca account for bars and news (use live for paper trading)</p>
                  </div>
                  <div class="flex items-center gap-2 shrink-0">
                    <button
                      v-if="inst.alpaca_data_brokerage_id"
                      @click="submitLinkDataBrokerage(true)"
                      class="ui-btn text-loss"
                    >Clear</button>
                    <button
                      @click="openLinkDataBrokerage"
                      class="ui-btn"
                    >{{ inst.alpaca_data_brokerage_id ? 'Change' : 'Set' }}</button>
                  </div>
                </div>
                <div v-if="inst.alpaca_data_brokerage" class="mt-3 min-w-0">
                  <p class="text-sm text-slate-100 truncate">{{ inst.alpaca_data_brokerage.account_name }}</p>
                  <p class="text-xs text-slate-500 mt-0.5">{{ inst.alpaca_data_brokerage.alpaca_paper ? 'Paper' : 'Live' }} Alpaca</p>
                </div>
                <p v-else-if="inst.alpaca_data_brokerage_id" class="mt-3 text-xs text-slate-500">
                  Linked (id: {{ inst.alpaca_data_brokerage_id.slice(0, 8) }}…)
                </p>
                <p v-else class="mt-3 text-xs text-slate-500">
                  Using trading creds for data fetches
                </p>
              </div>
            </div>
          </section>

          <!-- Strategy -->
          <section class="flex flex-col min-w-0 lg:self-start" aria-labelledby="inst-strategy-title">
            <div class="sec-head">
              <h2 id="inst-strategy-title" class="ui-section-title">Strategy</h2>
              <div v-if="inst.strategy_id" class="flex items-center gap-2">
                <button @click="openUnlinkStrategy" class="ui-btn text-loss">Unlink</button>
                <button @click="openEditStrategy" class="ui-btn">Edit</button>
              </div>
            </div>
            <div class="ui-card flex-1">
              <div v-if="inst.strategy" class="ui-rows">
                <div class="px-5 py-3 min-w-0">
                  <p class="text-sm text-slate-100 truncate" :title="inst.strategy.name">{{ inst.strategy.name }}</p>
                  <p class="text-xs text-slate-500 mt-0.5 tabular-nums">ID {{ inst.strategy_id }}</p>
                </div>
                <div
                  v-for="sub in (inst.strategy.strategies || [])"
                  :key="sub.strategy"
                  class="kv"
                >
                  <span class="truncate">{{ sub.strategy }}</span>
                  <span class="text-slate-100 tabular-nums shrink-0">{{ (sub.weight * 100).toFixed(0) }}%</span>
                </div>
              </div>
              <div v-else class="px-5 py-8 flex flex-col items-center gap-3 text-center">
                <p class="text-sm text-slate-400">No strategy linked</p>
                <button @click="openLinkStrategy" class="ui-btn">Link strategy</button>
              </div>
            </div>
          </section>

          <!-- Live readiness — equity instances only. Crypto and Kalshi have
               their own detail views and no live-readiness gate. -->
          <LiveReadinessCard
            v-if="isStockInstance"
            :instance="inst"
            :api-base="API_BASE"
            @waived="fetchInstance"
            @revoked="fetchInstance"
          />
        </div>

        <!-- ── Swing / wheel lanes ───────────────────────────────────────── -->
        <div
          v-if="isStockInstance && swingLanes.any"
          class="mt-12 grid grid-cols-1 gap-y-12"
        >
          <PendingSignalsCard
            :instance-id="instanceId"
            :api-base="API_BASE"
            :cash="wheelCash"
            @pending-ids="swingPendingIds = $event"
            @decided="swingDecisions++"
          />
          <WheelPanel
            v-if="swingLanes.wheel"
            :instance-id="instanceId"
            :api-base="API_BASE"
            :pending-ids="swingPendingIds"
            :refresh-key="swingDecisions"
            @cash="wheelCash = $event"
          />
        </div>

        <!-- ── Stocks ──────────────────────────────────────────────────────── -->
        <section class="mt-12" aria-labelledby="inst-stocks-title">
          <div class="sec-head">
            <div class="min-w-0">
              <h2 id="inst-stocks-title" class="ui-section-title">Stocks</h2>
              <p class="ui-section-sub tabular-nums">
                {{ (inst.stocks || []).length }} {{ (inst.stocks || []).length === 1 ? 'symbol' : 'symbols' }}
              </p>
            </div>
            <button @click="openAddStock" class="ui-btn">Add stock</button>
          </div>
          <div class="ui-card">
            <p v-if="(inst.stocks || []).length === 0" class="px-5 py-6 text-sm text-slate-500">No stocks added</p>
            <ul v-else class="px-5 py-4 flex flex-wrap gap-2">
              <li
                v-for="sym in inst.stocks"
                :key="sym"
                class="h-7 pl-2.5 pr-1 inline-flex items-center gap-1 rounded-md bg-raised border border-white/[0.06] text-xs font-medium text-slate-200"
              >
                {{ sym }}
                <button
                  @click="removeStock(sym)"
                  :disabled="removingStock === sym"
                  class="size-5 inline-flex items-center justify-center rounded text-slate-500 hover:text-loss transition-colors duration-150 ease-out disabled:opacity-40"
                  :title="`Remove ${sym}`"
                  :aria-label="`Remove ${sym}`"
                >
                  <span
                    class="material-symbols-outlined text-[14px]"
                    :class="removingStock === sym ? 'animate-spin' : ''"
                  >{{ removingStock === sym ? 'progress_activity' : 'close' }}</span>
                </button>
              </li>
            </ul>
          </div>
        </section>

        <!-- ── Backtests ───────────────────────────────────────────────────── -->
        <section class="mt-12" aria-labelledby="inst-backtests-title">
          <div class="sec-head">
            <div class="min-w-0">
              <h2 id="inst-backtests-title" class="ui-section-title">Backtests</h2>
              <p class="ui-section-sub tabular-nums">{{ btTotal }} {{ btTotal === 1 ? 'run' : 'runs' }}</p>
            </div>
            <button @click="openCreateBacktest" class="ui-btn">New backtest</button>
          </div>
          <div class="ui-card overflow-hidden">
            <div v-if="btLoading && !allBacktests.length" class="ui-rows">
              <span class="sr-only">Loading backtests</span>
              <div v-for="n in 4" :key="n" class="h-14 px-5 flex items-center" aria-hidden="true">
                <div class="h-4 w-48 ui-skel"></div>
              </div>
            </div>
            <div v-else-if="!allBacktests.length" class="ui-empty">
              <p>No backtests run for this instance yet</p>
            </div>
            <div v-else class="relative overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="h-10 text-left text-xs text-slate-500 border-b border-white/[0.06]">
                    <th class="pl-5 pr-3 font-normal whitespace-nowrap">Backtest</th>
                    <th class="px-3 font-normal whitespace-nowrap">Period</th>
                    <th class="px-3 font-normal whitespace-nowrap">
                      <button
                        @click="toggleBtSort('completed_at')"
                        class="inline-flex items-center gap-1 hover:text-slate-200 transition-colors duration-150 ease-out"
                      >
                        Date completed
                        <span class="material-symbols-outlined text-[14px]">{{ btSortIcon('completed_at') }}</span>
                      </button>
                    </th>
                    <th class="px-3 font-normal whitespace-nowrap">Status</th>
                    <th class="px-3 font-normal whitespace-nowrap text-right">Elapsed</th>
                    <th class="px-3 font-normal whitespace-nowrap text-right">
                      <button
                        @click="toggleBtSort('pnl')"
                        class="inline-flex items-center gap-1 hover:text-slate-200 transition-colors duration-150 ease-out"
                      >
                        P&L
                        <span class="material-symbols-outlined text-[14px]">{{ btSortIcon('pnl') }}</span>
                      </button>
                    </th>
                    <th class="px-3 font-normal whitespace-nowrap text-right">
                      <button
                        @click="toggleBtSort('pnl_percent')"
                        class="inline-flex items-center gap-1 hover:text-slate-200 transition-colors duration-150 ease-out"
                      >
                        P&L %
                        <span class="material-symbols-outlined text-[14px]">{{ btSortIcon('pnl_percent') }}</span>
                      </button>
                    </th>
                    <th class="pl-3 pr-5"><span class="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/[0.05]">
                  <tr
                    v-for="bt in allBacktests"
                    :key="bt.id"
                    class="hover:bg-white/[0.03] transition-colors duration-150 ease-out"
                  >
                    <td class="h-14 pl-5 pr-3 py-2 min-w-[180px]">
                      <span class="block text-slate-100 tabular-nums">#{{ bt.id }}</span>
                      <span class="block text-xs text-slate-500 mt-0.5 whitespace-nowrap">
                        <template v-if="(bt.stocks || []).length">{{ (bt.stocks || []).slice(0, 4).join(', ') }}</template>
                        <template v-else>—</template>
                        <span v-if="(bt.stocks || []).length > 4" class="tabular-nums"> +{{ bt.stocks.length - 4 }}</span>
                      </span>
                    </td>
                    <td class="px-3 py-2 whitespace-nowrap tabular-nums">
                      <span class="block text-slate-300">{{ bt.start_date || '?' }}</span>
                      <span class="block text-xs text-slate-500 mt-0.5">→ {{ bt.end_date || '?' }}</span>
                    </td>
                    <td class="px-3 py-2 text-slate-300 whitespace-nowrap tabular-nums">
                      {{ fmtDateTime(bt.completed_at) }}
                    </td>
                    <td class="px-3 py-2">
                      <span class="inline-flex items-center gap-2 text-slate-200 whitespace-nowrap">
                        <span class="ui-dot" :class="btStatusClass(btProgress[bt.id]?.status || bt.status)"></span>
                        {{ btStatusLabel(btProgress[bt.id]?.status || bt.status || 'queued') }}
                      </span>
                      <!-- Progress bar for running backtests -->
                      <div
                        v-if="btProgress[bt.id]?.progress != null && ['running','queued','pending'].includes((btProgress[bt.id]?.status || '').toLowerCase())"
                        class="mt-1.5 flex items-center gap-2"
                      >
                        <div class="h-1 w-20 rounded-full bg-raised overflow-hidden shrink-0">
                          <div
                            class="h-full rounded-full bg-slate-300 progress-fill"
                            :style="{ transform: `scaleX(${Math.min(100, Math.max(0, btProgress[bt.id].progress)) / 100})` }"
                          ></div>
                        </div>
                        <span class="text-xs text-slate-400 tabular-nums">{{ Math.round(btProgress[bt.id].progress) }}%</span>
                      </div>
                    </td>
                    <td class="px-3 py-2 text-right text-slate-300 whitespace-nowrap tabular-nums">
                      {{ fmtElapsed(btProgress[bt.id]?.time_elapsed_seconds ?? bt.time_elapsed_seconds) }}
                    </td>
                    <td class="px-3 py-2 text-right whitespace-nowrap tabular-nums" :class="pnlClass(bt.pnl)">
                      {{ fmtPnl(bt.pnl) }}
                    </td>
                    <td class="px-3 py-2 text-right whitespace-nowrap tabular-nums" :class="pnlClass(bt.pnl_percent)">
                      {{ bt.pnl_percent != null ? (Number(bt.pnl_percent) >= 0 ? '+' : '') + Number(bt.pnl_percent).toFixed(2) + '%' : '—' }}
                    </td>
                    <td class="pl-3 pr-5 py-2">
                      <div class="flex items-center gap-1 justify-end">
                        <!-- Inline controls based on status -->
                        <template v-if="['running','queued','pending'].includes((btProgress[bt.id]?.status || bt.status || '').toLowerCase())">
                          <button @click="openBtConfirm(bt, 'pause')" title="Pause" aria-label="Pause backtest" class="ui-icon-btn">
                            <span class="material-symbols-outlined text-[18px]">pause</span>
                          </button>
                        </template>
                        <template v-if="['paused','paused_llm_critical'].includes((btProgress[bt.id]?.status || bt.status || '').toLowerCase())">
                          <button @click="openBtConfirm(bt, 'resume')" title="Resume" aria-label="Resume backtest" class="ui-icon-btn">
                            <span class="material-symbols-outlined text-[18px]">play_arrow</span>
                          </button>
                        </template>
                        <template v-if="['running','queued','pending','paused','paused_llm_critical'].includes((btProgress[bt.id]?.status || bt.status || '').toLowerCase())">
                          <button @click="openBtConfirm(bt, 'stop')" title="Stop" aria-label="Stop backtest" class="ui-icon-btn hover:text-loss">
                            <span class="material-symbols-outlined text-[18px]">stop</span>
                          </button>
                        </template>
                        <button
                          @click="router.push({ name: 'backtest-detail', params: { id: bt.id }, query: { from: `/instances/${instanceId}` } })"
                          class="ui-btn ml-1"
                        >View</button>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- Pagination -->
            <div v-if="btTotalPages > 1" class="h-14 px-5 border-t border-white/[0.06] flex items-center justify-between gap-4">
              <p class="text-xs text-slate-500 tabular-nums">
                Page {{ btPage }} of {{ btTotalPages }}
                <span class="text-slate-600" aria-hidden="true">·</span>
                {{ btTotal }} total
              </p>
              <div class="flex items-center gap-1">
                <button
                  @click="fetchBacktests(btPage - 1)"
                  :disabled="btPage <= 1 || btLoading"
                  class="ui-icon-btn disabled:opacity-30 disabled:pointer-events-none"
                  aria-label="Previous page"
                >
                  <span class="material-symbols-outlined text-[18px]">chevron_left</span>
                </button>
                <template v-for="p in btTotalPages" :key="p">
                  <button
                    v-if="p === 1 || p === btTotalPages || Math.abs(p - btPage) <= 1"
                    @click="fetchBacktests(p)"
                    :disabled="btLoading"
                    class="h-8 min-w-8 px-2 rounded-md text-xs tabular-nums transition-colors duration-150 ease-out disabled:opacity-50"
                    :class="p === btPage
                      ? 'bg-raised text-slate-100 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]'
                      : 'text-slate-500 hover:text-slate-200 hover:bg-raised'"
                    :aria-current="p === btPage ? 'page' : undefined"
                  >{{ p }}</button>
                  <span
                    v-else-if="p === btPage - 2 || p === btPage + 2"
                    class="text-slate-600 text-xs px-1"
                  >…</span>
                </template>
                <button
                  @click="fetchBacktests(btPage + 1)"
                  :disabled="btPage >= btTotalPages || btLoading"
                  class="ui-icon-btn disabled:opacity-30 disabled:pointer-events-none"
                  aria-label="Next page"
                >
                  <span class="material-symbols-outlined text-[18px]">chevron_right</span>
                </button>
              </div>
            </div>
          </div>
        </section>

      </template>

    </main>
  </AppShell>

  <!-- ── Edit Instance Info Modal ──────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showEditInfo"
        class="ui-overlay"
        @click.self="closeEditInfo"
      >
        <div class="ui-modal max-w-md">
          <div class="ui-modal-head">
            <h2 class="text-base font-medium text-slate-100">Edit instance info</h2>
            <button @click="closeEditInfo" :disabled="editInfoSaving" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="ui-modal-body space-y-5">
            <div>
              <label class="ui-field-label">Instance name</label>
              <input
                v-model="editInfoForm.name"
                type="text"
                class="ui-input"
              />
            </div>

            <div>
              <label class="ui-field-label">Granularity (seconds)</label>
              <input
                v-model="editInfoForm.granularity"
                type="number"
                min="1"
                step="1"
                class="ui-input tabular-nums"
              />
              <div class="ui-seg mt-2 flex-wrap" role="group" aria-label="Granularity presets">
                <button
                  v-for="g in GRANULARITIES"
                  :key="'instance-granularity-' + g.value"
                  type="button"
                  @click="editInfoForm.granularity = g.value"
                  class="ui-seg-btn"
                  :class="editInfoForm.granularity === g.value ? 'ui-seg-btn-active' : ''"
                  :aria-pressed="editInfoForm.granularity === g.value"
                >
                  {{ g.label }}
                </button>
              </div>
            </div>

            <div>
              <label class="ui-field-label">Max usage ($)</label>
              <input
                v-model="editInfoForm.max_usage"
                type="number"
                min="0"
                step="0.01"
                class="ui-input tabular-nums"
                placeholder="Leave blank to keep current value"
              />
            </div>

            <!-- Live-start controls. instance.py refuses to launch a broker
                 when clean-room mode is on and no baseline is stored. -->
            <div class="pt-5 border-t border-white/[0.06]">
              <label class="ui-field-label">
                Initial value <span class="text-slate-500">(clean-room P&amp;L baseline)</span>
              </label>
              <input
                v-model="editInfoForm.initial_value"
                type="number"
                min="0"
                step="0.01"
                class="ui-input tabular-nums"
                placeholder="e.g. 6000"
              />
              <p class="ui-help leading-relaxed">
                Must be greater than 0. Sent only when changed, and it cannot be
                cleared from here. <span class="font-mono text-slate-400">LIVE_INITIAL_VALUE</span> still
                overrides it at launch.
              </p>
            </div>

            <label class="flex items-start gap-3 cursor-pointer rounded-lg border border-white/[0.07] px-3 py-3 hover:bg-white/[0.02] transition-colors duration-150 ease-out">
              <input
                v-model="editInfoForm.clean_room_mode"
                type="checkbox"
                class="mt-0.5 accent-primary shrink-0"
              />
              <span class="min-w-0">
                <span class="block text-sm text-slate-200">Clean-room mode</span>
                <span class="block text-xs text-slate-500 leading-relaxed mt-0.5">
                  Measure P&amp;L and drawdown against the baseline above instead of
                  the account's own history. With this on and no baseline stored,
                  the broker refuses to start.
                </span>
              </span>
            </label>

            <Transition name="slide-up">
              <div
                v-if="editInfoMsg"
                class="rounded-lg border px-4 py-3 text-sm"
                :class="msgClass(editInfoOk, editInfoSaving)"
              >
                <span v-if="editInfoSaving" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                  {{ editInfoMsg }}
                </span>
                <span v-else>{{ editInfoMsg }}</span>
              </div>
            </Transition>
          </div>

          <div class="ui-modal-foot">
            <button @click="closeEditInfo" :disabled="editInfoSaving" class="ui-btn-ghost h-9 px-4">
              Cancel
            </button>
            <button @click="submitEditInfo" :disabled="editInfoSaving" class="ui-btn-primary">
              <span v-if="editInfoSaving" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ editInfoSaving ? 'Saving…' : 'Save changes' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Link Brokerage Modal ──────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showLinkBrokerage"
        class="ui-overlay"
        @click.self="closeLinkBrokerage"
      >
        <div class="ui-modal max-w-md">
          <div class="ui-modal-head">
            <h2 class="text-base font-medium text-slate-100">Link brokerage</h2>
            <button @click="closeLinkBrokerage" :disabled="linkBrokerageSaving" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="ui-modal-body space-y-4">
            <template v-if="brokerageOptions.length">
              <div>
                <label class="ui-field-label">Brokerage</label>
                <select
                  v-model="selectedBrokerageId"
                  class="ui-input"
                >
                  <option
                    v-for="account in brokerageOptions"
                    :key="account.id"
                    :value="String(account.id)"
                    class="bg-surface"
                  >
                    {{ account.account_name }} ({{ account.brokerage_type }})
                  </option>
                </select>
              </div>
            </template>
            <div v-else class="rounded-lg border border-white/[0.07] px-4 py-6 flex flex-col items-center gap-3 text-center">
              <p class="text-sm text-slate-400">No brokerage accounts available yet</p>
              <button
                @click="router.push('/brokerages')"
                class="ui-btn"
              >
                Go to brokerages
              </button>
            </div>

            <Transition name="slide-up">
              <div
                v-if="linkBrokerageMsg"
                class="rounded-lg border px-4 py-3 text-sm"
                :class="msgClass(linkBrokerageOk, linkBrokerageSaving)"
              >
                <span v-if="linkBrokerageSaving" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                  {{ linkBrokerageMsg }}
                </span>
                <span v-else>{{ linkBrokerageMsg }}</span>
              </div>
            </Transition>
          </div>

          <div class="ui-modal-foot">
            <button @click="closeLinkBrokerage" :disabled="linkBrokerageSaving" class="ui-btn-ghost h-9 px-4">
              Cancel
            </button>
            <button
              @click="submitLinkBrokerage"
              :disabled="linkBrokerageSaving || !brokerageOptions.length"
              class="ui-btn-primary"
            >
              <span v-if="linkBrokerageSaving" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ linkBrokerageSaving ? 'Linking…' : 'Link brokerage' }}
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
        class="ui-overlay"
        @click.self="closeLinkDataBrokerage"
      >
        <div class="ui-modal max-w-md">
          <div class="ui-modal-head">
            <div class="min-w-0">
              <h2 class="text-base font-medium text-slate-100">Market-data source</h2>
              <p class="text-sm text-slate-500 mt-1">Alpaca account for bars and news fetches</p>
            </div>
            <button @click="closeLinkDataBrokerage" :disabled="linkDataBrokerageSaving" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="ui-modal-body space-y-4">
            <template v-if="dataBrokerageOptions.length">
              <div>
                <label class="ui-field-label">Alpaca account for data</label>
                <select
                  v-model="selectedDataBrokerageId"
                  class="ui-input"
                >
                  <option
                    v-for="account in dataBrokerageOptions"
                    :key="account.id"
                    :value="String(account.id)"
                    class="bg-surface"
                  >
                    {{ account.account_name }} ({{ account.alpaca_paper ? 'Paper' : 'Live' }})
                  </option>
                </select>
                <p class="ui-help leading-relaxed">
                  If you trade paper, link a live Alpaca account here so news and bars fetches
                  don't get 401s. Leaving this unset means the strategy uses the trading creds.
                </p>
              </div>
            </template>
            <div v-else class="rounded-lg border border-white/[0.07] px-4 py-6 flex flex-col items-center gap-3 text-center">
              <p class="text-sm text-slate-400">No Alpaca accounts available</p>
              <button
                @click="router.push('/brokerages')"
                class="ui-btn"
              >
                Link an Alpaca account
              </button>
            </div>

            <Transition name="slide-up">
              <div
                v-if="linkDataBrokerageMsg"
                class="rounded-lg border px-4 py-3 text-sm"
                :class="msgClass(linkDataBrokerageOk, linkDataBrokerageSaving)"
              >
                <span v-if="linkDataBrokerageSaving" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                  {{ linkDataBrokerageMsg }}
                </span>
                <span v-else>{{ linkDataBrokerageMsg }}</span>
              </div>
            </Transition>
          </div>

          <div class="ui-modal-foot">
            <button @click="closeLinkDataBrokerage" :disabled="linkDataBrokerageSaving" class="ui-btn-ghost h-9 px-4">
              Cancel
            </button>
            <button
              @click="submitLinkDataBrokerage(false)"
              :disabled="linkDataBrokerageSaving || !dataBrokerageOptions.length"
              class="ui-btn-primary"
            >
              <span v-if="linkDataBrokerageSaving" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ linkDataBrokerageSaving ? 'Saving…' : 'Set data source' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Edit Strategy Modal ───────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showEditStrategy"
        class="ui-overlay"
        @click.self="closeEditStrategy"
      >
        <div class="ui-modal max-w-2xl flex flex-col max-h-[90vh]">

          <div class="ui-modal-head border-b border-white/[0.06] shrink-0">
            <h2 class="text-base font-medium text-slate-100 truncate">Edit strategy</h2>
            <button @click="closeEditStrategy" :disabled="editStrategySaving" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="flex-1 overflow-y-auto px-6 py-5 space-y-6">

            <div v-if="editStrategyLoading">
              <span class="sr-only">Loading strategy</span>
              <div class="space-y-3" aria-hidden="true">
                <div class="h-4 w-28 ui-skel"></div>
                <div class="h-9 ui-skel"></div>
                <div class="h-40 ui-skel"></div>
              </div>
            </div>

            <template v-else-if="editStrategyData">
              <div>
                <label class="ui-field-label">Strategy name</label>
                <input
                  v-model="editStrategyData.name"
                  type="text"
                  class="ui-input"
                />
              </div>

              <div>
                <div v-if="availableStrategyOptions.length" class="mb-6">
                  <label class="ui-field-label">Add sub-strategy</label>
                  <div class="flex gap-2">
                    <select
                      v-model="editStrategyPickName"
                      class="ui-input flex-1 min-w-0"
                    >
                      <option v-for="s in availableStrategyOptions" :key="s.value" :value="s.value">{{ s.label }} · {{ s.value }}</option>
                    </select>
                    <button
                      type="button"
                      @click="addEditSubStrategy"
                      class="ui-btn h-9 shrink-0"
                    >
                      Add
                    </button>
                  </div>
                </div>

                <p class="text-sm text-slate-300 mb-3">
                  Sub-strategies
                  <span class="text-slate-500 tabular-nums ml-1">{{ editStrategyData.strategies?.length || 0 }}</span>
                </p>

                <div v-if="!editStrategyData.strategies?.length" class="text-sm text-slate-500">
                  No sub-strategies configured
                </div>

                <div class="space-y-4">
                  <div
                    v-for="(sub, idx) in editStrategyData.strategies"
                    :key="idx"
                    class="rounded-xl border border-white/[0.07] p-4 space-y-5"
                  >
                    <div class="flex items-start justify-between gap-3">
                      <div class="min-w-0">
                        <div class="flex items-baseline gap-2 flex-wrap">
                          <p class="text-sm font-medium text-slate-100">{{ strategyLabel(sub.strategy) }}</p>
                          <span class="text-xs text-slate-500">{{ strategyCanonicalId(sub.strategy) }}</span>
                        </div>
                        <p v-if="getStrategyMeta(sub.strategy)?.description" class="text-xs text-slate-500 mt-1 leading-relaxed">
                          {{ getStrategyMeta(sub.strategy)?.description }}
                        </p>
                      </div>
                      <button @click="removeSubStrategy(idx)" class="ui-icon-btn shrink-0 hover:text-loss" title="Remove" aria-label="Remove sub-strategy">
                        <span class="material-symbols-outlined text-[18px]">delete</span>
                      </button>
                    </div>

                    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
                      <div>
                        <label class="block text-xs text-slate-500 mb-1.5">Weight</label>
                        <input v-model.number="sub.weight" type="number" min="0" max="1" step="0.05"
                          class="ui-input h-8 tabular-nums" />
                      </div>
                      <div>
                        <label class="block text-xs text-slate-500 mb-1.5">Exec position</label>
                        <input v-model.number="sub.execution_position" type="number" step="1"
                          class="ui-input h-8 tabular-nums" />
                      </div>
                      <div>
                        <label class="block text-xs text-slate-500 mb-1.5">Phase</label>
                        <div class="ui-seg w-full" role="group" aria-label="Decision phase">
                          <button v-for="p in ['pre', 'post']" :key="p" type="button" @click="sub.decision_phase = p"
                            class="ui-seg-btn flex-1 px-2 capitalize"
                            :class="sub.decision_phase === p ? 'ui-seg-btn-active' : ''"
                            :aria-pressed="sub.decision_phase === p"
                          >{{ p }}</button>
                        </div>
                      </div>
                      <div>
                        <label class="block text-xs text-slate-500 mb-1.5">Scope</label>
                        <div class="ui-seg w-full" role="group" aria-label="Execution scope">
                          <button v-for="s in ['per_symbol', 'run_once']" :key="s" type="button" @click="sub.execution_scope = s"
                            class="ui-seg-btn flex-1 px-2"
                            :class="sub.execution_scope === s ? 'ui-seg-btn-active' : ''"
                            :aria-pressed="sub.execution_scope === s"
                          >{{ s === 'per_symbol' ? 'Per symbol' : 'Once' }}</button>
                        </div>
                      </div>
                    </div>

                    <div v-if="llmConfigGroups(sub).length">
                      <p class="text-xs text-slate-500 mb-2">LLM</p>
                      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div
                          v-for="group in llmConfigGroups(sub)"
                          :key="group.label"
                          class="rounded-lg border border-white/[0.07] px-3 py-3 flex items-start justify-between gap-3"
                        >
                          <div class="min-w-0">
                            <p class="text-sm text-slate-200">{{ group.label }}</p>
                            <p class="text-xs text-slate-500 leading-relaxed mt-0.5">{{ llmConfigSummary(sub, group) }}</p>
                          </div>
                          <button
                            type="button"
                            @click="openLlmConfigModal(sub, group)"
                            class="ui-btn shrink-0"
                          >
                            Edit
                          </button>
                        </div>
                      </div>
                    </div>

                    <div v-if="lookbackLlmConfigGroups(sub).length">
                      <p class="text-xs text-slate-500">Lookback LLM</p>
                      <p class="text-xs text-slate-600 mt-1 mb-2 leading-relaxed">Override the LLM provider and model for the 90-day lookback training pass. Falls back to the main LLM config above if not set.</p>
                      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div
                          v-for="group in lookbackLlmConfigGroups(sub)"
                          :key="group.label"
                          class="rounded-lg border border-white/[0.07] px-3 py-3 flex items-start justify-between gap-3"
                        >
                          <div class="min-w-0">
                            <p class="text-sm text-slate-200">{{ group.label }}</p>
                            <p class="text-xs text-slate-500 leading-relaxed mt-0.5">{{ llmConfigSummary(sub, group) }}</p>
                          </div>
                          <button
                            type="button"
                            @click="openLlmConfigModal(sub, group)"
                            class="ui-btn shrink-0"
                          >
                            Edit
                          </button>
                        </div>
                      </div>
                    </div>

                    <div v-if="configEntries(sub).length">
                      <p class="text-xs text-slate-500 mb-2">Config</p>
                      <div class="grid grid-cols-2 sm:grid-cols-3 gap-x-3 gap-y-4">
                        <div v-for="[key, val] in configEntries(sub)" :key="key" class="min-w-0">
                          <label class="block text-xs text-slate-500 mb-1.5">{{ getStrategyConfigFieldMeta(sub.strategy, key).label }}</label>
                          <template v-if="getConfigType(getRefConfig(sub.strategy)[key] ?? val) === 'boolean'">
                            <button type="button" @click="sub.config[key] = !sub.config[key]"
                              role="switch"
                              :aria-checked="!!sub.config[key]"
                              class="relative inline-flex items-center h-5 w-9 shrink-0 rounded-full border transition-colors duration-150 ease-out"
                              :class="sub.config[key] ? 'bg-slate-200 border-slate-200' : 'bg-raised border-white/[0.1]'">
                              <span class="inline-block size-3.5 rounded-full transition-transform duration-150 ease-out"
                                :class="sub.config[key] ? 'translate-x-4 bg-background-dark' : 'translate-x-0.5 bg-slate-500'"></span>
                            </button>
                          </template>
                          <template v-else-if="getConfigType(getRefConfig(sub.strategy)[key] ?? val) === 'number'">
                            <input :value="sub.config[key]" @input="updateConfigField(sub, key, $event.target.value, 'number')" type="number"
                              class="ui-input h-8 tabular-nums" />
                          </template>
                          <template v-else-if="getConfigType(getRefConfig(sub.strategy)[key] ?? val) === 'json'">
                            <textarea
                              :value="getConfigFieldValue(sub, key, val)"
                              @input="updateConfigField(sub, key, $event.target.value, 'json')"
                              @blur="syncConfigJsonDraft(sub, key, val)"
                              rows="4"
                              spellcheck="false"
                              class="ui-input font-mono text-xs resize-y"
                              :class="getConfigFieldError(sub, key) ? 'border-loss/60 focus:border-loss' : ''"
                            ></textarea>
                          </template>
                          <template v-else>
                            <input v-model="sub.config[key]" type="text"
                              class="ui-input h-8" />
                          </template>
                          <p v-if="getConfigFieldError(sub, key)" class="mt-1.5 text-xs leading-relaxed text-loss">
                            Invalid JSON: {{ getConfigFieldError(sub, key) }}
                          </p>
                          <p v-if="getStrategyConfigFieldMeta(sub.strategy, key).description" class="mt-1.5 text-xs leading-relaxed text-slate-600">
                            {{ getStrategyConfigFieldMeta(sub.strategy, key).description }}
                          </p>
                        </div>
                      </div>
                      <!-- Benzinga API test button -->
                      <div v-if="hasBenzingaConfig(sub)" class="mt-4">
                        <button @click="testBenzingaSources(sub)" :disabled="benzingaTestLoading"
                          class="ui-btn">
                          {{ benzingaTestLoading ? 'Testing…' : 'Test Benzinga API access' }}
                        </button>
                      </div>
                    </div>

                  </div>
                </div>
              </div>
            </template>

            <div v-else-if="editStrategyMsg && !editStrategyOk" class="ui-error">
              {{ editStrategyMsg }}
            </div>

            <Transition name="slide-up">
              <div v-if="editStrategyMsg && editStrategyData" class="rounded-lg border px-4 py-3 text-sm"
                :class="msgClass(editStrategyOk, editStrategySaving)">
                {{ editStrategyMsg }}
              </div>
            </Transition>
          </div>

          <div class="ui-modal-foot shrink-0">
            <button @click="closeEditStrategy" :disabled="editStrategySaving" class="ui-btn-ghost h-9 px-4">
              Cancel
            </button>
            <button v-if="editStrategyData && !editStrategyLoading" @click="submitEditStrategy" :disabled="editStrategySaving"
              class="ui-btn-primary">
              <span v-if="editStrategySaving" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ editStrategySaving ? 'Saving…' : 'Save changes' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Preserve History confirmation (model/identity change) ────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showPreserveHistory"
        class="ui-overlay z-[60]"
        @click.self="cancelPreserveHistory"
      >
        <div class="ui-modal max-w-lg">
          <div class="ui-modal-head">
            <h2 class="text-base font-medium text-slate-100">Analysis model changed</h2>
          </div>
          <div class="ui-modal-body space-y-3 text-sm text-slate-300 leading-relaxed">
            <p>
              This change would otherwise rebuild Nexus analytical history for
              <span class="font-medium text-slate-100">{{ preserveHistoryAffectedNames }}</span>
              (a full historic lookback + cleanup on next boot).
            </p>
            <p class="text-slate-400">
              Preserve existing history and apply the new model only going forward?
              Broker positions &amp; P&amp;L are unaffected either way. Takes effect on the
              next Stop→Start. Preserved learned state was trained on the prior model and
              the new model inherits it.
            </p>
          </div>
          <div class="ui-modal-foot flex-col items-stretch">
            <button @click="doSaveStrategy(true)"
              class="ui-btn-primary w-full">
              Preserve &amp; apply forward
            </button>
            <button @click="doSaveStrategy(false)"
              class="ui-btn h-9 w-full">
              Full rebuild (re-run lookback)
            </button>
            <button @click="cancelPreserveHistory"
              class="ui-btn-ghost h-9 w-full">
              Cancel
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Clear Instance State Modal (destructive) ─────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="showClearState"
           class="ui-overlay"
           @click.self="closeClearState">
        <div class="ui-modal max-w-2xl">
          <div class="ui-modal-head border-b border-white/[0.06]">
            <div class="min-w-0">
              <h2 class="text-base font-medium text-slate-100">Clear instance data</h2>
              <p class="text-sm text-slate-500 mt-1 truncate">Destructive · scoped to instance <span class="text-slate-300">{{ route.params.id }}</span></p>
            </div>
            <button @click="closeClearState"
                    :disabled="clearApplying || clearPreviewLoading"
                    class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40"
                    aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="px-6 py-5 space-y-5 max-h-[70vh] overflow-y-auto">
            <!-- Scope picker -->
            <div>
              <p class="ui-field-label">Scope</p>
              <div class="space-y-2">
                <div
                  v-for="opt in clearScopeOptions"
                  :key="opt.value"
                  @click="!clearOk && (clearScope = opt.value)"
                  :class="[
                    'rounded-lg border px-4 py-3 cursor-pointer transition-colors duration-150 ease-out',
                    clearScope === opt.value
                      ? 'border-white/[0.16] bg-raised'
                      : 'border-white/[0.07] hover:bg-white/[0.02]',
                    clearOk ? 'opacity-50 cursor-not-allowed' : '',
                  ]"
                >
                  <div class="flex items-start gap-3">
                    <input type="radio"
                           :checked="clearScope === opt.value"
                           :value="opt.value"
                           :disabled="clearOk"
                           @change="clearScope = opt.value"
                           class="mt-1 accent-primary shrink-0"
                    />
                    <div class="min-w-0">
                      <div class="text-sm text-slate-200">{{ opt.label }}</div>
                      <p class="text-xs text-slate-500 leading-relaxed mt-1">{{ opt.blurb }}</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Preview button + counts -->
            <div class="flex flex-wrap items-center gap-3">
              <button
                @click="previewClearState"
                :disabled="clearPreviewLoading || clearApplying || clearOk"
                class="ui-btn"
              >
                {{ clearPreviewLoading ? 'Counting…' : (clearPreview ? 'Refresh preview' : 'Preview (dry run)') }}
              </button>
              <span v-if="clearPreview" class="text-xs text-slate-400 tabular-nums">
                Would delete <span class="text-slate-100 font-medium">{{ clearPreview.total_would_delete }}</span> row(s) across {{ clearPreview.tables.length }} table(s).
              </span>
            </div>

            <!-- Per-table preview -->
            <div v-if="clearPreview" class="rounded-lg border border-white/[0.07] overflow-hidden">
              <table class="w-full text-sm">
                <thead>
                  <tr class="h-9 text-xs text-slate-500 border-b border-white/[0.06]">
                    <th class="text-left px-4 font-normal">Table</th>
                    <th class="text-right px-4 font-normal">{{ clearOk ? 'Deleted' : 'Would delete' }}</th>
                    <th class="text-left px-4 font-normal">Notes</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/[0.05]">
                  <tr v-for="row in (clearResult || clearPreview).tables" :key="row.table">
                    <td class="px-4 py-2.5 text-slate-300 break-all">{{ row.table }}</td>
                    <td class="px-4 py-2.5 text-right tabular-nums"
                        :class="(clearOk ? row.deleted : row.would_delete) > 0 ? 'text-slate-100' : 'text-slate-600'">
                      {{ clearOk ? row.deleted : row.would_delete }}
                    </td>
                    <td class="px-4 py-2.5 text-xs text-slate-500">
                      <span v-if="row.skipped">{{ row.reason || 'skipped' }}</span>
                      <span v-else-if="row.would_delete === 0 && !clearOk">no matching rows</span>
                      <span v-else-if="clearOk" class="text-gain">deleted</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- Confirm typed-instance-id gate -->
            <div v-if="clearPreview && !clearOk">
              <p class="ui-field-label">
                To confirm, type the instance id
                <span class="font-mono text-slate-100">{{ route.params.id }}</span>
                below
              </p>
              <input
                :value="clearConfirm"
                @input="clearConfirm = $event.target.value"
                :disabled="clearApplying"
                :placeholder="String(route.params.id)"
                class="ui-input font-mono"
              />
            </div>

            <!-- Status / result -->
            <div v-if="clearMsg" class="rounded-lg border px-4 py-3 text-sm leading-relaxed" :class="msgClass(clearOk)">
              {{ clearMsg }}
            </div>
          </div>

          <div class="ui-modal-foot">
            <button @click="closeClearState"
                    :disabled="clearApplying"
                    class="ui-btn-ghost h-9 px-4">
              {{ clearOk ? 'Close' : 'Cancel' }}
            </button>
            <button v-if="!clearOk"
                    @click="applyClearState"
                    :disabled="!clearPreview || !clearConfirmOk || clearApplying || clearPreviewLoading"
                    class="ui-btn h-9 px-4 text-loss">
              <template v-if="clearApplying">Clearing…</template>
              <template v-else>Confirm and clear</template>
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── LLM Config Modal ──────────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showLlmConfigModal"
        class="ui-overlay z-[70]"
      >
        <div class="ui-modal max-w-lg flex flex-col max-h-[90vh]">
          <div class="ui-modal-head shrink-0">
            <div class="min-w-0">
              <h2 class="text-base font-medium text-slate-100">{{ llmConfigTarget?.group?.label || 'LLM config' }}</h2>
              <p class="text-sm text-slate-500 mt-1">Select a model from your saved configurations</p>
            </div>
            <button @click="closeLlmConfigModal" class="ui-icon-btn ml-auto shrink-0" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="flex-1 overflow-y-auto px-6 pb-5 space-y-4">
            <div>
              <label class="ui-field-label">Model</label>
              <select
                v-model="llmConfigDraft.modelId"
                @change="onModelSelect"
                class="ui-input"
              >
                <option value="" disabled>Select a model</option>
                <option v-for="m in savedModels" :key="m.id" :value="m.id">{{ m.name }} — {{ getLlmProviderLabel(m.provider) }} / {{ m.model }}</option>
              </select>
            </div>

            <div v-if="llmConfigDraft.modelId" class="rounded-lg border border-white/[0.07] px-4 py-3 space-y-2">
              <div class="flex items-center gap-3 text-sm">
                <span class="text-slate-500 w-20 shrink-0">Provider</span>
                <span class="text-slate-200">{{ getLlmProviderLabel(llmConfigDraft.provider) }}</span>
              </div>
              <div class="flex items-center gap-3 text-sm min-w-0">
                <span class="text-slate-500 w-20 shrink-0">Model</span>
                <span class="text-slate-200 truncate">{{ llmConfigDraft.model }}</span>
              </div>
              <div class="flex items-center gap-3 text-sm">
                <span class="text-slate-500 w-20 shrink-0">Effort</span>
                <span class="text-slate-200">{{ effortLabel(llmConfigDraft) }}</span>
              </div>
            </div>

            <div v-if="savedModels.length === 0" class="rounded-lg border border-white/[0.07] px-4 py-3 text-sm leading-relaxed text-slate-400">
              No models saved yet. Go to the <span class="text-slate-200">Models</span> tab to add one first.
            </div>

            <div
              v-if="llmConfigMsg"
              class="rounded-lg border px-4 py-3 text-sm leading-relaxed"
              :class="msgClass(llmConfigOk)"
            >
              {{ llmConfigMsg }}
            </div>
          </div>

          <div class="ui-modal-foot shrink-0">
            <button @click="closeLlmConfigModal"
              class="ui-btn-ghost h-9 px-4">
              Cancel
            </button>
            <button @click="saveLlmConfigModal"
              :disabled="!llmConfigDraft.modelId"
              class="ui-btn-primary">
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
        class="ui-overlay"
        @click.self="showBenzingaTestModal = false"
      >
        <div class="ui-modal max-w-md">
          <div class="ui-modal-head">
            <h3 class="text-base font-medium text-slate-100">Benzinga API access test</h3>
            <button @click="showBenzingaTestModal = false" class="ui-icon-btn ml-auto shrink-0" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>
          <div class="ui-modal-body space-y-3 max-h-[60vh] overflow-y-auto">
            <div v-if="benzingaTestLoading" class="py-8 flex flex-col items-center gap-3 text-center">
              <span class="material-symbols-outlined text-[20px] text-slate-500 animate-spin">progress_activity</span>
              <p class="text-sm text-slate-500">Testing Benzinga API sources…</p>
            </div>
            <template v-else-if="benzingaTestResults">
              <div class="rounded-lg border px-4 py-3 text-sm"
                :class="msgClass(benzingaTestResults.ok)">
                {{ benzingaTestResults.message }}
              </div>
              <div v-if="benzingaTestResults.results?.length" class="rounded-lg border border-white/[0.07] divide-y divide-white/[0.05]">
                <div v-for="r in benzingaTestResults.results" :key="r.source"
                  class="flex items-center justify-between gap-3 px-4 py-2.5">
                  <div class="min-w-0">
                    <span class="text-sm text-slate-200">{{ r.label }}</span>
                    <p v-if="r.error" class="text-xs text-slate-500 mt-0.5 break-words">{{ r.error }}</p>
                  </div>
                  <span class="text-xs tabular-nums shrink-0"
                    :class="r.ok ? 'text-gain' : 'text-loss'">
                    {{ r.ok ? 'OK' : `${r.status || 'ERR'}` }}
                  </span>
                </div>
              </div>
            </template>
          </div>
          <div class="ui-modal-foot">
            <button @click="showBenzingaTestModal = false"
              class="ui-btn-primary">
              Done
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
        v-if="showBtConfirm"
        class="ui-overlay"
        @click.self="closeBtConfirm"
      >
        <div class="ui-modal max-w-sm">
          <div class="ui-modal-head">
            <div class="min-w-0">
              <h2 class="text-base font-medium text-slate-100">{{ btConfirmMeta.label }} backtest</h2>
              <p class="text-sm text-slate-500 mt-1 tabular-nums">#{{ btConfirmId }}</p>
            </div>
            <button @click="closeBtConfirm" :disabled="btConfirmBusy" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>
          <div class="ui-modal-body space-y-4">
            <p class="text-sm text-slate-300">{{ btConfirmMeta.desc }}</p>
            <Transition name="slide-up">
              <div v-if="btConfirmMsg" class="rounded-lg border px-4 py-3 text-sm"
                :class="msgClass(btConfirmOk, btConfirmBusy)">
                <span v-if="btConfirmBusy" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                  {{ btConfirmMsg }}
                </span>
                <span v-else>{{ btConfirmMsg }}</span>
              </div>
            </Transition>
          </div>
          <div class="ui-modal-foot">
            <button @click="closeBtConfirm" :disabled="btConfirmBusy"
              class="ui-btn-ghost h-9 px-4">
              Cancel
            </button>
            <button @click="executeBtConfirm" :disabled="btConfirmBusy"
              :class="btConfirmAction === 'stop' ? 'ui-btn h-9 px-4 text-loss' : 'ui-btn-primary'">
              <span v-if="btConfirmBusy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ btConfirmBusy ? 'Working…' : btConfirmMeta.label }}
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
        class="ui-overlay"
        @click.self="closeCreateBacktest"
      >
        <div class="ui-modal max-w-md">

          <div class="ui-modal-head">
            <div class="min-w-0">
              <h2 class="text-base font-medium text-slate-100">New backtest</h2>
              <p class="text-sm text-slate-500 mt-1 truncate">{{ inst?.name || instanceId }}</p>
            </div>
            <button @click="closeCreateBacktest" :disabled="creatingBacktest" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="ui-modal-body space-y-5">
            <!-- Stocks -->
            <div>
              <label class="ui-field-label">Stocks <span class="text-slate-500">*</span></label>
              <input
                v-model="createBtForm.stocks"
                type="text"
                placeholder="e.g. AAPL, MSFT, TSLA"
                class="ui-input uppercase placeholder:normal-case"
              />
              <p class="ui-help">Comma-separated ticker symbols</p>
            </div>

            <!-- Date range -->
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="ui-field-label">Start date <span class="text-slate-500">*</span></label>
                <input
                  v-model="createBtForm.start_date"
                  type="date"
                  class="ui-input tabular-nums [color-scheme:dark]"
                />
              </div>
              <div>
                <label class="ui-field-label">End date <span class="text-slate-500">*</span></label>
                <input
                  v-model="createBtForm.end_date"
                  type="date"
                  class="ui-input tabular-nums [color-scheme:dark]"
                />
              </div>
            </div>

            <!-- Granularity -->
            <div>
              <label class="ui-field-label">Granularity</label>
              <div class="ui-seg flex-wrap" role="group" aria-label="Granularity presets">
                <button
                  v-for="g in GRANULARITIES"
                  :key="g.value"
                  type="button"
                  @click="createBtForm.granularity = g.value"
                  class="ui-seg-btn"
                  :class="createBtForm.granularity === g.value ? 'ui-seg-btn-active' : ''"
                  :aria-pressed="createBtForm.granularity === g.value"
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
              <div class="mt-3 flex items-center gap-3">
                <label class="text-xs text-slate-500 whitespace-nowrap">Custom (seconds)</label>
                <input
                  v-model="createBtForm.granularity"
                  type="number"
                  min="1"
                  step="1"
                  placeholder="3600"
                  class="ui-input h-8 flex-1 min-w-0 tabular-nums"
                />
                <span class="text-xs text-slate-500 tabular-nums w-10 text-right">{{ fmtGranularity(createBtForm.granularity) }}</span>
              </div>
            </div>

            <!-- Initial cash -->
            <div>
              <label class="ui-field-label">Initial cash <span class="text-slate-500">($)</span></label>
              <input
                v-model="createBtForm.initial_cash"
                type="number"
                min="1"
                step="1000"
                placeholder="100000"
                class="ui-input tabular-nums"
              />
            </div>

            <Transition name="slide-up">
              <div
                v-if="createBtMsg"
                class="rounded-lg border px-4 py-3 text-sm"
                :class="msgClass(createBtOk, creatingBacktest)"
              >
                <span v-if="creatingBacktest" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                  {{ createBtMsg }}
                </span>
                <span v-else>{{ createBtMsg }}</span>
              </div>
            </Transition>
          </div>

          <div class="ui-modal-foot">
            <button @click="closeCreateBacktest" :disabled="creatingBacktest"
              class="ui-btn-ghost h-9 px-4">
              Cancel
            </button>
            <button @click="submitCreateBacktest" :disabled="creatingBacktest"
              class="ui-btn-primary">
              <span v-if="creatingBacktest" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ creatingBacktest ? 'Queuing…' : 'Run backtest' }}
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
        class="ui-overlay"
        @click.self="closeLinkStrategy"
      >
        <div class="ui-modal max-w-sm">
          <div class="ui-modal-head">
            <h2 class="text-base font-medium text-slate-100">Link strategy</h2>
            <button @click="closeLinkStrategy" :disabled="linkStrategyBusy" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>
          <div class="ui-modal-body space-y-4">
            <div>
              <label class="ui-field-label">Strategy</label>
              <select
                v-model="linkStrategyId"
                class="ui-input"
              >
                <option value="">Select a strategy</option>
                <option v-for="s in availStrategies" :key="s.id" :value="String(s.id)">{{ s.name || s.id }}</option>
              </select>
            </div>
            <Transition name="slide-up">
              <div
                v-if="linkStrategyMsg"
                class="rounded-lg border px-4 py-3 text-sm break-words"
                :class="msgClass(linkStrategyOk)"
              >
                {{ linkStrategyMsg }}
              </div>
            </Transition>
          </div>
          <div class="ui-modal-foot">
            <button @click="closeLinkStrategy" :disabled="linkStrategyBusy"
              class="ui-btn-ghost h-9 px-4">
              Cancel
            </button>
            <button @click="submitLinkStrategy" :disabled="linkStrategyBusy || !linkStrategyId"
              class="ui-btn-primary">
              <span v-if="linkStrategyBusy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ linkStrategyBusy ? 'Linking…' : 'Link strategy' }}
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
        class="ui-overlay"
        @click.self="closeUnlinkStrategy"
      >
        <div class="ui-modal max-w-sm">
          <div class="ui-modal-head">
            <div class="min-w-0">
              <p class="text-base font-medium text-slate-100">Unlink strategy</p>
              <p class="text-sm text-slate-500 mt-1 truncate">{{ inst?.strategy?.name || `Strategy #${inst?.strategy_id}` }}</p>
            </div>
          </div>
          <div class="ui-modal-body">
            <p class="text-sm text-slate-400 leading-relaxed">This will remove the strategy link from this instance. The strategy itself will not be deleted. You can re-link it later.</p>
          </div>
          <div class="ui-modal-foot">
            <p v-if="unlinkStrategyMsg" class="flex-1 text-xs" :class="unlinkStrategyBusy ? 'text-slate-400' : unlinkStrategyOk ? 'text-gain' : 'text-loss'">{{ unlinkStrategyMsg }}</p>
            <button @click="closeUnlinkStrategy" :disabled="unlinkStrategyBusy" class="ui-btn-ghost h-9 px-4">Cancel</button>
            <button @click="submitUnlinkStrategy" :disabled="unlinkStrategyBusy" class="ui-btn h-9 px-4 text-loss">
              <span v-if="unlinkStrategyBusy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
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
        class="ui-overlay"
        @click.self="closeUnlinkBrokerage"
      >
        <div class="ui-modal max-w-sm">
          <div class="ui-modal-head">
            <div class="min-w-0">
              <p class="text-base font-medium text-slate-100">Unlink brokerage</p>
              <p class="text-sm text-slate-500 mt-1 truncate">{{ inst?.brokerage?.account_name || 'Current brokerage' }}</p>
            </div>
          </div>
          <div class="ui-modal-body">
            <p class="text-sm text-slate-400 leading-relaxed">This will remove the brokerage link from this instance. The brokerage account will not be deleted. You can re-link it later.</p>
          </div>
          <div class="ui-modal-foot">
            <p v-if="unlinkBrokerageMsg" class="flex-1 text-xs" :class="unlinkBrokerageBusy ? 'text-slate-400' : unlinkBrokerageOk ? 'text-gain' : 'text-loss'">{{ unlinkBrokerageMsg }}</p>
            <button @click="closeUnlinkBrokerage" :disabled="unlinkBrokerageBusy" class="ui-btn-ghost h-9 px-4">Cancel</button>
            <button @click="submitUnlinkBrokerage" :disabled="unlinkBrokerageBusy" class="ui-btn h-9 px-4 text-loss">
              <span v-if="unlinkBrokerageBusy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              Unlink
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Add Stock ────────────────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showAddStock"
        class="ui-overlay"
        @click.self="closeAddStock"
      >
        <div class="ui-modal max-w-xs">
          <div class="ui-modal-head">
            <h2 class="text-base font-medium text-slate-100">Add stock</h2>
            <button @click="closeAddStock" class="ui-icon-btn ml-auto shrink-0" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>
          <div class="ui-modal-body space-y-4">
            <div>
              <label class="ui-field-label">Ticker symbol</label>
              <input
                v-model="addStockSym"
                type="text"
                placeholder="e.g. AAPL"
                @keydown.enter="submitAddStock"
                class="ui-input uppercase placeholder:normal-case"
              />
            </div>
            <div v-if="addStockMsg" class="rounded-lg border px-4 py-3 text-sm" :class="msgClass(false)">
              {{ addStockMsg }}
            </div>
          </div>
          <div class="ui-modal-foot">
            <button @click="closeAddStock" :disabled="addStockBusy"
              class="ui-btn-ghost h-9 px-4">
              Cancel
            </button>
            <button @click="submitAddStock" :disabled="addStockBusy"
              class="ui-btn-primary">
              <span v-if="addStockBusy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ addStockBusy ? 'Adding…' : 'Add stock' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

</template>

<style scoped>
/* Section header: title (and optional subline) left, actions right. */
.sec-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 32px;
  margin-bottom: 12px;
}
/* Label / value row inside a card (the dashboard Insights rhythm). */
.kv {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 44px;
  padding: 10px 20px;
  font-size: 14px;
  line-height: 20px;
}
.kv > :first-child { color: #a1a1aa; min-width: 0; }
.kv > :last-child { text-align: right; }

.progress-fill {
  transform-origin: left center;
  transition: transform 500ms cubic-bezier(0.22, 1, 0.36, 1);
}

.fade-enter-active, .fade-leave-active { transition: opacity 150ms cubic-bezier(0.22, 1, 0.36, 1); }
.fade-enter-from, .fade-leave-to       { opacity: 0; }

.slide-up-enter-active, .slide-up-leave-active {
  transition: opacity 150ms cubic-bezier(0.22, 1, 0.36, 1), transform 150ms cubic-bezier(0.22, 1, 0.36, 1);
}
.slide-up-enter-from, .slide-up-leave-to { opacity: 0; transform: translateY(4px); }
</style>
