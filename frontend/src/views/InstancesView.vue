<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import { getToken } from '../utils/auth.js'
import LlmConfigForm from '../components/LlmConfigForm.vue'
import LiveUptime from '../components/LiveUptime.vue'
import { fmtUsd, fmtSignedUsd, fmtPct, signClass } from '../utils/dashboardAnalytics.js'
import {
  applyStrategyLlmDraft,
  buildStrategyLlmTestPayload,
  buildStrategyLlmDraft,
  describeStrategyLlmGroup,
  getLlmProviderLabel,
  getStrategyConfigFieldMeta,
  getStrategyLlmConfigGroups,
  LLM_PROVIDER_OPTIONS,
  LLM_REASONING_EFFORT_OPTIONS,
  NVIDIA_REASONING_EFFORT_OPTIONS,
  shouldHideStrategyConfigField,
  isLlmManagedConfigField,
} from '../utils/strategyConfig.js'

const router = useRouter()
const route = useRoute()

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

const REQUEST_TIMEOUT_MS = 15000

// ── State ─────────────────────────────────────────────────────────────────────
const instances  = ref([])
const loading    = ref(true)
const loadError  = ref('')

const strategies = ref([])
const strategyMap = computed(() => {
  const m = {}
  for (const s of strategies.value) m[s.id] = s.name || s.id
  return m
})

const brokerageMap = computed(() => {
  const m = {}
  for (const b of brokerages.value) m[b.id] = `${b.account_name} (${b.brokerage_type})`
  return m
})

// Filter
const filter = ref('all')  // 'all' | 'user' | 'ai'
const filteredInstances = computed(() => {
  if (filter.value === 'all') return instances.value
  return instances.value.filter(i => (i.created_by || 'user') === filter.value)
})

// Action loading per instance id
const busy       = ref({})  // { [id]: true/false }

// Delete modal
const showDelete    = ref(false)
const deleteTarget  = ref(null)   // { id, name }
const deleteError   = ref('')
const deleteForce   = ref(false)  // true after first attempt fails with force hint
const deleting      = ref(false)

// Brokerages for create form
const brokerages = ref([])

// Create modal
const showCreate   = ref(false)
const creating     = ref(false)
const createMsg    = ref('')
const createOk     = ref(false)
const createForm   = ref({ id: '', name: '', granularity: '60', run_command: false, brokerage_id: '', max_usage: '', strategy_id: '' })

// Add stock modal
const showAddStock  = ref(false)
const addStockId    = ref('')
const addStockSym   = ref('')
const addStockBusy  = ref(false)
const addStockMsg   = ref('')
const addStockOk    = ref('')     // "Added AAPL" after a successful add
const addStockInput = ref(null)

// ── Helpers ───────────────────────────────────────────────────────────────────
function authHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

async function fetchWithTimeout(url, options = {}, timeoutMs = REQUEST_TIMEOUT_MS) {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { ...options, signal: controller.signal })
  } finally {
    clearTimeout(timeoutId)
  }
}

function setBusy(id, val) { busy.value = { ...busy.value, [id]: val } }

// ── Data fetching ─────────────────────────────────────────────────────────────
async function fetchBrokerages() {
  try {
    const res = await fetchWithTimeout(`${API_BASE}/brokerages`, { headers: authHeaders() })
    if (!res.ok) return
    const data = await res.json()
    brokerages.value = data.accounts || []
  } catch { /* non-critical */ }
}

async function fetchStrategies() {
  try {
    const res = await fetchWithTimeout(`${API_BASE}/strategies`, { headers: authHeaders() })
    if (!res.ok) return
    const data = await res.json()
    strategies.value = data.strategies || []
  } catch { /* non-critical */ }
}

async function fetchInstances() {
  loading.value   = true
  loadError.value = ''
  try {
    const res = await fetchWithTimeout(`${API_BASE}/instances`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    // Kalshi bots and crypto instances have their own dedicated tabs; keep them
    // out of the equities instances list.
    instances.value = (data.instances || []).filter((i) => i.kind !== 'kalshi' && i.kind !== 'crypto')
  } catch (e) {
    loadError.value = e?.name === 'AbortError'
      ? 'Instances took too long to load. Try again after the server catches up.'
      : `Failed to load instances: ${e?.message || 'request failed'}`
  } finally {
    loading.value = false
  }
}

// ── Instance actions ──────────────────────────────────────────────────────────
async function startInstance(id) {
  setBusy(id, true)
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(id)}/start`, {
      method: 'POST', headers: authHeaders(),
    })
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${res.status}`) }
    await fetchInstances()
  } catch (e) {
    alert(`Start failed: ${e.message}`)
  } finally {
    setBusy(id, false)
  }
}

async function stopInstance(id) {
  setBusy(id, true)
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(id)}/stop`, {
      method: 'POST', headers: authHeaders(),
    })
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${res.status}`) }
    await fetchInstances()
  } catch (e) {
    alert(`Stop failed: ${e.message}`)
  } finally {
    setBusy(id, false)
  }
}

function openDelete(id, name) {
  deleteTarget.value = { id, name }
  deleteError.value  = ''
  deleteForce.value  = false
  deleting.value     = false
  showDelete.value   = true
}

function closeDelete() {
  if (deleting.value) return
  showDelete.value = false
}

async function confirmDelete(force = false) {
  const { id, name } = deleteTarget.value
  deleting.value = true
  deleteError.value = ''
  setBusy(id, true)
  try {
    const url = `${API_BASE}/instances/${encodeURIComponent(id)}${force ? '?force=true' : ''}`
    const res = await fetch(url, { method: 'DELETE', headers: authHeaders() })
    if (!res.ok) {
      const d = await res.json().catch(() => ({}))
      const msg = d.detail || `HTTP ${res.status}`
      // If the API hints about force=true, expose the force option
      if (msg.toLowerCase().includes('force')) {
        deleteForce.value = true
      }
      throw new Error(msg)
    }
    showDelete.value = false
    await fetchInstances()
  } catch (e) {
    deleteError.value = e.message
  } finally {
    deleting.value = false
    setBusy(id, false)
  }
}

async function removeStock(instanceId, symbol) {
  setBusy(instanceId, true)
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId)}/stocks/${encodeURIComponent(symbol)}`, {
      method: 'DELETE', headers: authHeaders(),
    })
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${res.status}`) }
    await fetchInstances()
  } catch (e) {
    alert(`Remove failed: ${e.message}`)
  } finally {
    setBusy(instanceId, false)
  }
}

// ── Create modal ──────────────────────────────────────────────────────────────
function openCreate() {
  createForm.value = { id: '', name: '', granularity: '60', run_command: false, brokerage_id: '', max_usage: '', strategy_id: '' }
  createMsg.value  = ''
  createOk.value   = false
  creating.value   = false
  showCreate.value = true
  fetchAvailableStrategies()
}

function closeCreate() {
  if (creating.value) return
  showCreate.value = false
}

async function submitCreate() {
  const f = createForm.value
  if (!f.id.trim()) { createMsg.value = 'Instance ID is required'; createOk.value = false; return }
  creating.value  = true
  createMsg.value = 'Creating...'
  createOk.value  = false
  try {
    const payload = {
      id:          f.id.trim(),
      name:        f.name.trim() || undefined,
      // Stringify in case the field was typed into a number-input
      // somewhere; API expects str on Optional[str].
      granularity: String(f.granularity || '60'),
      run_command: f.run_command,
    }
    if (f.brokerage_id) payload.brokerage_id = f.brokerage_id
    if (f.max_usage)    payload.max_usage = parseFloat(f.max_usage) || undefined
    if (f.strategy_id)  payload.strategy_id = f.strategy_id
    const res = await fetch(`${API_BASE}/instances`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(payload),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    createOk.value  = true
    createMsg.value = `Instance "${f.id.trim()}" created.`
    await fetchInstances()
    setTimeout(() => { showCreate.value = false }, 1200)
  } catch (e) {
    createOk.value  = false
    createMsg.value = e.message || 'Something went wrong'
  } finally {
    creating.value = false
  }
}

// ── Create Backtest modal ─────────────────────────────────────────────────────
const showCreateBacktest  = ref(false)
const creatingBacktest    = ref(false)
const createBtMsg         = ref('')
const createBtOk          = ref(false)
const createBtForm        = ref({
  instance_id: '',
  stocks: '',
  start_date: '',
  end_date: '',
  granularity: '60',
  initial_cash: '100000',
})

function openCreateBacktest(instanceId) {
  createBtForm.value = {
    instance_id: instanceId,
    stocks: '',
    start_date: '',
    end_date: '',
    granularity: '60',
    initial_cash: '100000',
  }
  createBtMsg.value     = ''
  createBtOk.value      = false
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
    const payload = {
      instance_id:  f.instance_id,
      stocks,
      start_date:   f.start_date,
      end_date:     f.end_date,
      granularity:  String(f.granularity ?? '60'),
      initial_cash: parseFloat(f.initial_cash) || 100000,
    }
    const res = await fetch(`${API_BASE}/backtests`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(payload),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    createBtOk.value  = true
    createBtMsg.value = `Backtest queued (ID: ${data.id ?? data.backtest_id ?? '?'}).`
    setTimeout(() => { showCreateBacktest.value = false }, 1500)
  } catch (e) {
    createBtOk.value  = false
    createBtMsg.value = e.message || 'Something went wrong'
  } finally {
    creatingBacktest.value = false
  }
}

// ── Add stock modal ───────────────────────────────────────────────────────────
function openAddStock(id) {
  addStockId.value  = id
  addStockSym.value = ''
  addStockMsg.value = ''
  addStockBusy.value = false
  addStockOk.value = ''
  showAddStock.value = true
  nextTick(() => addStockInput.value?.focus())
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
  addStockOk.value   = ''
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(addStockId.value)}/stocks`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ symbol: sym }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    await fetchInstances()
    // Stay open for the next symbol: clear the field and keep focus in it.
    addStockSym.value = ''
    addStockOk.value = `Added ${sym}`
  } catch (e) {
    addStockMsg.value = e.message || 'Failed to add stock'
  } finally {
    addStockBusy.value = false
    nextTick(() => addStockInput.value?.focus())
  }
}

// ── Edit Strategy modal ───────────────────────────────────────────────────────
const showEditStrategy    = ref(false)
const editStrategyLoading = ref(false)
const editStrategySaving  = ref(false)
const editStrategyMsg     = ref('')
const editStrategyOk      = ref(false)
const editStrategyData    = ref(null)   // { id, name, strategies: [...] }
const editStrategyPickName = ref('')
const availableStrategies = ref([])     // from GET /strategies/available
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

async function openEditStrategy(strategyId) {
  resetConfigEditorState()
  editStrategyMsg.value     = ''
  editStrategyOk.value      = false
  editStrategyData.value    = null
  editStrategyLoading.value = true
  showEditStrategy.value    = true
  await fetchAvailableStrategies()
  editStrategyPickName.value = availableStrategyOptions.value[0]?.value || ''
  try {
    const res = await fetch(`${API_BASE}/strategies/${strategyId}`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    // Deep-clone so edits don't mutate original
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

function llmConfigSummary(sub, group) {
  return describeStrategyLlmGroup(sub.config || {}, group, savedModels.value)
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
    await fetchStrategies()
    setTimeout(() => { showEditStrategy.value = false }, 1200)
  } catch (e) {
    editStrategyOk.value  = false
    editStrategyMsg.value = e.message || 'Something went wrong'
  } finally {
    editStrategySaving.value = false
  }
}

// ── Create Strategy (nested inside Create Instance) ───────────────────────────
const showCreateStrategy    = ref(false)
const creatingStrategy      = ref(false)
const createStrategyMsg     = ref('')
const createStrategyOk      = ref(false)
const newStrategyForm       = ref({ name: '', strategies: [] })
const newStrategyPickName   = ref('')  // sub-strategy selector

function openCreateStrategy() {
  resetConfigEditorState()
  newStrategyForm.value    = { name: '', strategies: [] }
  newStrategyPickName.value = availableStrategyOptions.value[0]?.value || ''
  createStrategyMsg.value  = ''
  createStrategyOk.value   = false
  creatingStrategy.value   = false
  showCreateStrategy.value = true
}

function closeCreateStrategy() {
  if (creatingStrategy.value) return
  resetConfigEditorState()
  showCreateStrategy.value = false
}

function addNewSubStrategy() {
  const name = newStrategyPickName.value
  if (!name) return
  newStrategyForm.value.strategies.push(
    buildSubStrategyDefaults(name, newStrategyForm.value.strategies.length)
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
  // Ollama-specific — see InstanceDetailView for context.
  ollamaBaseUrl: '',
  ollamaKeepAlive: '',
  ollamaThink: '',
  // Bedrock-specific — region + reasoning.
  bedrockRegion: '',
  bedrockReasoning: '',
})

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

function removeNewSubStrategy(idx) {
  newStrategyForm.value.strategies.splice(idx, 1)
}

async function submitCreateStrategy() {
  const f = newStrategyForm.value
  if (!f.name.trim()) { createStrategyMsg.value = 'Strategy name is required'; createStrategyOk.value = false; return }
  if (hasConfigEditorErrors()) { createStrategyMsg.value = 'Fix invalid JSON config fields before saving'; createStrategyOk.value = false; return }
  creatingStrategy.value  = true
  createStrategyMsg.value = 'Creating...'
  createStrategyOk.value  = false
  try {
    const res = await fetch(`${API_BASE}/strategies`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ name: f.name.trim(), strategies: f.strategies }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    createStrategyOk.value  = true
    createStrategyMsg.value = `Strategy "${f.name.trim()}" created.`
    await fetchStrategies()
    // Auto-select the new strategy in the create instance form
    const newId = data.id ?? data.strategy?.id
    if (newId) createForm.value.strategy_id = String(newId)
    setTimeout(() => { showCreateStrategy.value = false }, 1000)
  } catch (e) {
    createStrategyOk.value  = false
    createStrategyMsg.value = e.message || 'Something went wrong'
  } finally {
    creatingStrategy.value = false
  }
}

const GRANULARITIES = [
  { label: '1 min',  value: '60' },
  { label: '5 min',  value: '300' },
  { label: '15 min', value: '900' },
  { label: '1 hour', value: '3600' },
  { label: '1 day',  value: '86400' },
]

function granLabel(sec) {
  const n = parseInt(sec)
  if (n < 60)   return `${n}s`
  if (n < 3600) return `${n / 60}m`
  if (n < 86400) return `${n / 3600}h`
  return `${n / 86400}d`
}

// ── Change Brokerage modal ────────────────────────────────────────────────────
const showChangeBrokerage    = ref(false)
const changeBrokerageInstId  = ref(null)
const changeBrokerageId      = ref('')
const changeBrokerageBusy    = ref(false)
const changeBrokerageMsg     = ref('')
const changeBrokerageOk      = ref(false)

function openChangeBrokerage(inst) {
  changeBrokerageInstId.value = inst.id
  changeBrokerageId.value     = inst.brokerage_id ?? ''
  changeBrokerageMsg.value    = ''
  changeBrokerageOk.value     = false
  changeBrokerageBusy.value   = false
  showChangeBrokerage.value   = true
}

function closeChangeBrokerage() {
  if (changeBrokerageBusy.value) return
  showChangeBrokerage.value = false
}

async function submitChangeBrokerage() {
  changeBrokerageBusy.value = true
  changeBrokerageMsg.value  = ''
  try {
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(changeBrokerageInstId.value)}/link-brokerage`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ brokerage_id: changeBrokerageId.value || null }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    changeBrokerageOk.value  = true
    changeBrokerageMsg.value = changeBrokerageId.value ? 'Brokerage updated.' : 'Brokerage unlinked.'
    await fetchInstances()
    setTimeout(() => { showChangeBrokerage.value = false }, 1000)
  } catch (e) {
    changeBrokerageMsg.value = e.message || 'Failed to update brokerage'
  } finally {
    changeBrokerageBusy.value = false
  }
}

// ── Link Strategy modal ───────────────────────────────────────────────────────
const showLinkStrategy    = ref(false)
const linkStrategyInstId  = ref(null)
const linkStrategyId      = ref('')
const linkStrategyBusy    = ref(false)
const linkStrategyMsg     = ref('')
const linkStrategyOk      = ref(false)

function openLinkStrategy(instId) {
  linkStrategyInstId.value = instId
  linkStrategyId.value     = strategies.value[0]?.id ?? ''
  linkStrategyMsg.value    = ''
  linkStrategyOk.value     = false
  linkStrategyBusy.value   = false
  showLinkStrategy.value   = true
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
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(linkStrategyInstId.value)}/link-strategy`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ strategy_id: linkStrategyId.value }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    linkStrategyOk.value  = true
    linkStrategyMsg.value = 'Strategy linked.'
    await fetchInstances()
    setTimeout(() => { showLinkStrategy.value = false }, 1000)
  } catch (e) {
    linkStrategyMsg.value = e.message || 'Failed to link strategy'
  } finally {
    linkStrategyBusy.value = false
  }
}

// ── Row overflow menu (presentation only) ─────────────────────────────────────
// One menu open at a time. Opening focuses the first item; arrows move between
// items; Escape closes and returns focus to the ⋯ button; a pointerdown
// anywhere outside a menu closes it.
const openMenuId = ref(null)

function menuRoot(id) {
  return document.querySelector(`[data-row-menu="${CSS.escape(String(id))}"]`)
}
function menuItems(id) {
  return [...(menuRoot(id)?.querySelectorAll('[role="menuitem"]:not(:disabled)') || [])]
}
function toggleMenu(id) {
  openMenuId.value = openMenuId.value === id ? null : id
  if (openMenuId.value != null) nextTick(() => menuItems(id)[0]?.focus())
}
function closeMenu(returnFocus = false) {
  const id = openMenuId.value
  openMenuId.value = null
  if (returnFocus && id != null) menuRoot(id)?.querySelector('[aria-haspopup]')?.focus()
}
function onMenuKey(e, id) {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
  e.preventDefault()
  const items = menuItems(id)
  if (!items.length) return
  const i = items.indexOf(document.activeElement)
  const next = e.key === 'ArrowDown' ? (i + 1) % items.length : (i - 1 + items.length) % items.length
  items[next].focus()
}
function onDocPointer(e) {
  if (openMenuId.value != null && !e.target.closest?.('[data-row-menu]')) closeMenu()
}
function onDocKey(e) {
  if (e.key === 'Escape' && openMenuId.value != null) closeMenu(true)
}
onMounted(() => {
  document.addEventListener('pointerdown', onDocPointer)
  document.addEventListener('keydown', onDocKey)
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', onDocPointer)
  document.removeEventListener('keydown', onDocKey)
})

// ── Pinned instances ──────────────────────────────────────────────────────────
// Pins live in this browser, as the iOS app keeps its own on-device
// (UserDefaults `pinned_instances`). Insertion order, best-effort: the page
// works the same when storage is blocked.
const PIN_KEY = 'intellistock_pinned_instances'
function readPins() {
  try {
    const v = JSON.parse(localStorage.getItem(PIN_KEY) || '[]')
    return Array.isArray(v) ? v.map(String) : []
  } catch { return [] }
}
const pinnedIds = ref(readPins())
function isPinned(id) { return pinnedIds.value.includes(String(id)) }
function togglePin(id) {
  const key = String(id)
  pinnedIds.value = isPinned(key) ? pinnedIds.value.filter(x => x !== key) : [...pinnedIds.value, key]
  try { localStorage.setItem(PIN_KEY, JSON.stringify(pinnedIds.value)) } catch { /* storage blocked */ }
}
// Pinned cards follow the same User/AI filter as the list; the list shows the rest.
const pinnedInstances = computed(() =>
  pinnedIds.value.map(id => filteredInstances.value.find(i => i.id === id)).filter(Boolean))
const listInstances = computed(() => filteredInstances.value.filter(i => !isPinned(i.id)))

// What the cards show beyond the list, as on iOS: each pinned instance's
// detail (GET /instances/{id}: uptime, strategy doc, brokerage) and its
// account's day (GET /brokerages/{id}/portfolio-history?range=1D). Fetched
// for pinned instances only; a failure keeps the last good answer.
const pinDetails  = ref({})   // instance id → { inst, at }
const pinAccounts = ref({})   // brokerage id → { value, change_abs, change_pct, values }

async function fetchPinDetail(id) {
  try {
    const res = await fetchWithTimeout(`${API_BASE}/instances/${encodeURIComponent(id)}`, { headers: authHeaders() })
    if (!res.ok) return
    const inst = await res.json()
    pinDetails.value = { ...pinDetails.value, [id]: { inst, at: Date.now() } }
  } catch { /* keep the last detail */ }
}
async function fetchPinAccount(bid) {
  try {
    const res = await fetchWithTimeout(`${API_BASE}/brokerages/${encodeURIComponent(bid)}/portfolio-history?range=1D`, { headers: authHeaders() })
    if (!res.ok) return
    const d = await res.json()
    const values = (Array.isArray(d?.values) ? d.values : []).map(Number).filter(Number.isFinite)
    pinAccounts.value = {
      ...pinAccounts.value,
      [bid]: { value: d?.current_value, change_abs: d?.change_abs, change_pct: d?.change_pct, values },
    }
  } catch { /* keep the last figures */ }
}
function refreshPinned() {
  const list = pinnedInstances.value
  list.forEach(i => fetchPinDetail(i.id))
  // Portfolio history serves stock accounts; Kalshi has its own tab.
  const bids = [...new Set(list.map(i => i.brokerage_id).filter(Boolean))]
    .filter(bid => String(brokerages.value.find(b => b.id === bid)?.brokerage_type || '').toLowerCase() !== 'kalshi')
  bids.forEach(fetchPinAccount)
}
// Refetch when the pinned set, its links or a run state changes…
watch(
  () => pinnedInstances.value.map(i => `${i.id}|${i.strategy_id ?? ''}|${i.brokerage_id ?? ''}|${i.runCommand}`).join(','),
  (key) => { if (key) refreshPinned() },
)
// …and gently while the page is visible.
const PIN_POLL_MS = 60_000
let pinPollTimer = null
onMounted(() => {
  pinPollTimer = setInterval(() => {
    if (document.visibilityState === 'visible' && pinnedInstances.value.length) refreshPinned()
  }, PIN_POLL_MS)
})
onUnmounted(() => {
  clearInterval(pinPollTimer)
})

// Card text (presentation only), mirroring the iOS instance rows.
function runState(inst) {
  return inst.crashed ? 'crashed' : inst.runCommand ? 'running' : 'stopped'
}
// Uptime as the detail reported it and when; LiveUptime ticks it from there.
function pinUptime(inst) {
  const d = pinDetails.value[inst.id]
  if (runState(inst) !== 'running' || !d) return null
  const up = Number(d.inst?.uptime_seconds)
  if (!(d.inst?.run_command ?? d.inst?.runCommand) || !Number.isFinite(up)) return null
  return { seconds: up, at: d.at }
}
// "Swing" when the strategy runs a swing or wheel lane, else "Equity";
// nothing until a linked strategy's doc has loaded.
function pinKind(inst) {
  const doc = pinDetails.value[inst.id]?.inst?.strategy
  if (inst.strategy_id && !doc) return null
  const subs = Array.isArray(doc?.strategies) ? doc.strategies : []
  const lanes = subs.map(s => String(s?.strategy || '').replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase())
  return lanes.some(l => l === 'strategy_swing' || l === 'strategy_wheel') ? 'Swing' : 'Equity'
}
function pinStateWord(inst) {
  return { running: 'Running', stopped: 'Stopped', crashed: 'Crashed' }[runState(inst)]
}

// The account's 1D line for a pinned card, scaled like the dashboard
// Sparkline (never stretching less than 0.5% of the value to full height),
// with room on the right for the live "now" dot. Returns the line, the area
// under it, and where the last point sits (as % of the box) for the dot.
const pinGradId = id => `pin-area-${String(id).replace(/[^A-Za-z0-9_-]/g, '_')}`
const PIN_SPARK_W = 160
const PIN_SPARK_H = 40
function pinSpark(values) {
  const v = (values || []).filter(Number.isFinite)
  if (v.length < 2) return null
  const lo = Math.min(...v)
  const hi = Math.max(...v)
  const mid = (lo + hi) / 2
  const span = Math.max(hi - lo, Math.abs(mid) * 0.005) || 1
  const min = mid - span / 2
  const padY = 5
  const usableW = PIN_SPARK_W - 6
  const usableH = PIN_SPARK_H - padY * 2
  const step = usableW / (v.length - 1)
  const pts = v.map((y, i) => [i * step, padY + usableH - ((y - min) / span) * usableH])
  const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join('')
  const [lx, ly] = pts[pts.length - 1]
  return {
    line,
    area: `${line}L${lx.toFixed(1)},${PIN_SPARK_H}L0,${PIN_SPARK_H}Z`,
    up: v[v.length - 1] >= v[0],
    left: `${(lx / PIN_SPARK_W) * 100}%`,
    top: `${(ly / PIN_SPARK_H) * 100}%`,
  }
}

// A card that just started plays one "ignition" flash around its border.
// Only a stopped → running change counts, never the first load.
const pinIgnite = ref({})   // instance id → true while the flash plays
const pinWasRunning = new Map()
watch(
  () => pinnedInstances.value.map(i => `${i.id}\u0000${runState(i) === 'running' ? 1 : 0}`).join('\u0001'),
  (key) => {
    for (const pair of key ? key.split('\u0001') : []) {
      const [id, on] = pair.split('\u0000')
      const running = on === '1'
      if (running && pinWasRunning.get(id) === false) {
        pinIgnite.value = { ...pinIgnite.value, [id]: true }
        setTimeout(() => {
          const next = { ...pinIgnite.value }
          delete next[id]
          pinIgnite.value = next
        }, 1600)
      }
      pinWasRunning.set(id, running)
    }
  },
  { immediate: true },
)
// Paper or Live money, from the detail's brokerage or the loaded account.
function pinMoneyKind(inst) {
  if (!inst.brokerage_id) return null
  const nested = pinDetails.value[inst.id]?.inst?.brokerage
  if (typeof nested?.alpaca_paper === 'boolean') return nested.alpaca_paper ? 'Paper' : 'Live'
  const acct = brokerages.value.find(b => b.id === inst.brokerage_id)
  const type = String(acct?.brokerage_type || '').toLowerCase()
  if (type === 'alpaca' || type === 'binanceus') return acct.alpaca_paper ? 'Paper' : 'Live'
  if (type === 'kalshi' && acct.kalshi_environment) return String(acct.kalshi_environment).toLowerCase() === 'live' ? 'Live' : 'Paper'
  return null
}
function pinStrategyName(inst) {
  if (!inst.strategy_id) return null
  return strategyMap.value[inst.strategy_id] || pinDetails.value[inst.id]?.inst?.strategy?.name || `Strategy ${inst.strategy_id}`
}
function pinAccountName(inst) {
  if (!inst.brokerage_id) return null
  return brokerages.value.find(b => b.id === inst.brokerage_id)?.account_name
    || pinDetails.value[inst.id]?.inst?.brokerage?.account_name
    || inst.brokerage_id
}

onMounted(async () => {
  await Promise.all([fetchBrokerages(), fetchStrategies(), fetchInstances()])
  if (String(route.query.createStrategy || '') === '1') {
    await fetchAvailableStrategies()
    openCreateStrategy()
    router.replace({ path: route.path, query: { ...route.query, createStrategy: undefined } })
  }
})
</script>

<template>
  <AppShell>
    <main class="ui-page">

      <!-- Header -->
      <header class="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <div class="min-w-0">
          <h1 class="ui-page-title">Instances</h1>
          <p class="ui-page-sub">Live trading and backtesting bots</p>
        </div>
        <div class="flex items-center gap-2">
          <button
            @click="fetchInstances"
            class="ui-icon-btn"
            title="Refresh"
            aria-label="Refresh instances"
          >
            <span class="material-symbols-outlined text-[18px]">refresh</span>
          </button>
          <button @click="openCreate" class="ui-btn-primary">New instance</button>
        </div>
      </header>

      <!-- Filter -->
      <div class="mt-8 mb-3 flex items-center justify-between gap-4">
        <div class="ui-seg" role="group" aria-label="Filter instances">
          <button
            v-for="f in [{ label: 'All', value: 'all' }, { label: 'User created', value: 'user' }, { label: 'AI created', value: 'ai' }]"
            :key="f.value"
            type="button"
            @click="filter = f.value"
            class="ui-seg-btn inline-flex items-center gap-1.5"
            :class="filter === f.value ? 'ui-seg-btn-active' : ''"
            :aria-pressed="filter === f.value"
          >
            {{ f.label }}
            <span v-if="f.value !== 'all'" class="tabular-nums text-slate-500">
              {{ instances.filter(i => (i.created_by || 'user') === f.value).length }}
            </span>
            <span v-else class="tabular-nums text-slate-500">{{ instances.length }}</span>
          </button>
        </div>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="ui-card overflow-hidden" aria-label="Loading instances">
        <div class="hidden md:block h-10 border-b border-white/[0.06]"></div>
        <div class="ui-rows">
          <div v-for="n in 6" :key="n" class="h-16 px-5 flex flex-col justify-center gap-2">
            <div class="ui-skel h-4 w-44"></div>
            <div class="ui-skel h-3 w-28"></div>
          </div>
        </div>
      </div>

      <!-- Error -->
      <div v-else-if="loadError" class="ui-error">
        {{ loadError }}
      </div>

      <!-- Empty state (no instances at all) -->
      <div v-else-if="instances.length === 0" class="ui-card">
        <div class="ui-empty">
          <p>No instances yet. Create one to run a strategy live or in a backtest.</p>
          <button @click="openCreate" class="ui-btn-primary">New instance</button>
        </div>
      </div>

      <!-- Empty state (filter has no results) -->
      <div v-else-if="filteredInstances.length === 0" class="ui-card">
        <p class="ui-empty">No {{ filter === 'ai' ? 'AI-created' : 'user-created' }} instances</p>
      </div>

      <!-- Instance list: one line per instance. The whole row opens it;
           Start/Stop and the ⋯ menu sit above the row link. -->
      <template v-else>
      <!-- ── Pinned: a card per pinned instance ─────────────────────────── -->
      <section v-if="pinnedInstances.length" class="mt-6" aria-labelledby="pinned-title">
        <div class="mb-3">
          <h2 id="pinned-title" class="ui-section-title">Pinned</h2>
          <p class="ui-section-sub">Saved in this browser</p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          <article
            v-for="inst in pinnedInstances"
            :key="inst.id"
            class="pin-card ui-card p-5 relative flex flex-col"
            :class="[`pin-${runState(inst)}`, pinIgnite[inst.id] ? 'pin-ignite' : '']"
          >
            <!-- Running: a light orbits the border, with a soft glow behind it.
                 It fades in on start and out on stop; starting also flashes
                 the whole border once. -->
            <span class="pin-fx" aria-hidden="true">
              <span class="pin-glow"><span class="pin-ring pin-ring-wide"><span class="pin-orbit"></span></span></span>
              <span class="pin-ring"><span class="pin-orbit"></span></span>
              <span class="pin-flash"></span>
            </span>

            <!-- Head: name (the card link), id · paper/live · origin, actions -->
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <RouterLink
                  :to="'/instances/' + encodeURIComponent(inst.id)"
                  class="block text-base font-medium truncate focus-visible:outline-none after:absolute after:inset-0 after:rounded-xl after:content-[''] focus-visible:after:ring-2 focus-visible:after:ring-white/25"
                  :class="runState(inst) === 'stopped' ? 'text-slate-300' : 'text-slate-100'"
                  :title="inst.name || inst.id"
                >{{ inst.name || inst.id }}</RouterLink>
                <p class="text-xs text-slate-500 mt-1 truncate tabular-nums">
                  {{ inst.id }}
                  <template v-if="pinMoneyKind(inst)">
                    <span class="text-slate-600" aria-hidden="true">·</span> {{ pinMoneyKind(inst) }}
                  </template>
                  <template v-if="(inst.created_by || 'user') === 'ai'">
                    <span class="text-slate-600" aria-hidden="true">·</span> AI created
                  </template>
                </p>
              </div>

              <div class="relative flex items-center gap-1 shrink-0 -mr-1" :class="openMenuId === inst.id ? 'z-30' : 'z-10'">
                <button
                  type="button"
                  @click.stop="togglePin(inst.id)"
                  class="ui-icon-btn"
                  title="Unpin"
                  :aria-label="`Unpin ${inst.name || inst.id}`"
                >
                  <span class="material-symbols-outlined text-[18px] pin-filled">push_pin</span>
                </button>
                <button
                  v-if="!inst.runCommand"
                  @click.stop="startInstance(inst.id)"
                  :disabled="busy[inst.id]"
                  class="ui-btn w-16 ml-1"
                >
                  <span v-if="busy[inst.id]" class="material-symbols-outlined text-[16px] animate-spin" aria-label="Working">progress_activity</span>
                  <template v-else>Start</template>
                </button>
                <button
                  v-else
                  @click.stop="stopInstance(inst.id)"
                  :disabled="busy[inst.id]"
                  class="ui-btn w-16 ml-1 text-loss"
                >
                  <span v-if="busy[inst.id]" class="material-symbols-outlined text-[16px] animate-spin" aria-label="Working">progress_activity</span>
                  <template v-else>Stop</template>
                </button>

                <div class="relative" :data-row-menu="inst.id">
                  <button
                    type="button"
                    @click.stop="toggleMenu(inst.id)"
                    class="ui-icon-btn"
                    :class="openMenuId === inst.id ? 'bg-raised text-slate-200' : ''"
                    aria-haspopup="menu"
                    :aria-expanded="openMenuId === inst.id"
                    :aria-label="`More actions for ${inst.name || inst.id}`"
                  >
                    <span class="material-symbols-outlined text-[18px]">more_horiz</span>
                  </button>
                  <div
                    v-if="openMenuId === inst.id"
                    class="ui-menu"
                    role="menu"
                    @keydown="onMenuKey($event, inst.id)"
                  >
                    <button type="button" role="menuitem" class="ui-menu-item menu-focus"
                      @click="closeMenu(); router.push('/instances/' + encodeURIComponent(inst.id))">View</button>
                    <button type="button" role="menuitem" class="ui-menu-item menu-focus"
                      :disabled="busy[inst.id]"
                      @click="closeMenu(); openCreateBacktest(inst.id)">Run backtest</button>
                    <button v-if="!inst.strategy_id" type="button" role="menuitem" class="ui-menu-item menu-focus"
                      :disabled="busy[inst.id]"
                      @click="closeMenu(); openLinkStrategy(inst.id)">Link strategy</button>
                    <button v-if="inst.strategy_id" type="button" role="menuitem" class="ui-menu-item menu-focus"
                      :disabled="busy[inst.id]"
                      @click="closeMenu(); openEditStrategy(inst.strategy_id)">
                      Edit strategy
                      <span class="ml-auto pl-4 text-xs text-slate-500 tabular-nums">ID {{ inst.strategy_id }}</span>
                    </button>
                    <button v-if="inst.brokerage_id" type="button" role="menuitem" class="ui-menu-item menu-focus"
                      :disabled="busy[inst.id]"
                      @click="closeMenu(); openChangeBrokerage(inst)">Change brokerage</button>
                    <button type="button" role="menuitem" class="ui-menu-item menu-focus"
                      @click="closeMenu(); openAddStock(inst.id)">
                      Edit stocks
                      <span class="ml-auto pl-4 text-xs text-slate-500 tabular-nums">{{ (inst.stocks || []).length }}</span>
                    </button>
                    <button type="button" role="menuitem" class="ui-menu-item menu-focus"
                      @click="closeMenu(); togglePin(inst.id)">Unpin</button>
                    <div class="ui-menu-sep" role="separator"></div>
                    <button type="button" role="menuitem" class="ui-menu-item menu-focus text-loss"
                      :disabled="busy[inst.id]"
                      @click="closeMenu(); openDelete(inst.id, inst.name)">Delete</button>
                  </div>
                </div>
              </div>
            </div>

            <!-- Status: dot (pinging while it runs) · live uptime · kind -->
            <p class="mt-4 flex items-center gap-2.5 text-sm" :class="runState(inst) === 'stopped' ? 'text-slate-500' : 'text-slate-200'">
              <span
                class="pin-dot"
                :class="runState(inst) === 'running' ? 'pin-dot-live' : runState(inst) === 'crashed' ? 'bg-loss' : 'bg-slate-600'"
                aria-hidden="true"
              ></span>
              <span class="truncate tabular-nums">
                <span :class="runState(inst) === 'running' ? 'text-gain' : runState(inst) === 'crashed' ? 'text-loss' : ''">{{ pinStateWord(inst) }}</span>
                <template v-if="pinUptime(inst)">
                  <span class="text-slate-600" aria-hidden="true"> · </span>
                  <LiveUptime :seconds="pinUptime(inst).seconds" :at="pinUptime(inst).at" class="text-slate-400" />
                </template>
                <template v-if="pinKind(inst)">
                  <span class="text-slate-600" aria-hidden="true"> · </span>
                  <span class="text-slate-400">{{ pinKind(inst) }}</span>
                </template>
              </span>
            </p>

            <!-- The linked account's day: equity, today's change and its 1D line -->
            <div class="mt-5 flex items-end justify-between gap-4 min-h-16">
              <template v-if="inst.brokerage_id">
                <div class="min-w-0">
                  <p class="text-xs text-slate-500 truncate">{{ pinAccountName(inst) }} · today</p>
                  <p
                    class="mt-1 text-2xl font-medium tabular-nums leading-tight"
                    :class="runState(inst) === 'stopped' ? 'text-slate-300' : 'text-slate-100'"
                  >{{ pinAccounts[inst.brokerage_id] ? fmtUsd(pinAccounts[inst.brokerage_id].value) : '—' }}</p>
                  <p
                    v-if="pinAccounts[inst.brokerage_id] && pinAccounts[inst.brokerage_id].change_abs != null"
                    class="mt-1 text-xs tabular-nums"
                    :class="signClass(pinAccounts[inst.brokerage_id].change_abs)"
                  >
                    {{ fmtSignedUsd(pinAccounts[inst.brokerage_id].change_abs) }} · {{ fmtPct(pinAccounts[inst.brokerage_id].change_pct) }}
                  </p>
                  <p v-else class="mt-1 text-xs text-slate-600">&nbsp;</p>
                </div>
                <div
                  v-if="pinSpark(pinAccounts[inst.brokerage_id]?.values)"
                  class="pin-spark relative w-2/5 max-w-40 h-10 shrink-0"
                  :class="[runState(inst) === 'running' ? 'pin-spark-live' : '', pinSpark(pinAccounts[inst.brokerage_id].values).up ? 'pin-up' : 'pin-down']"
                  aria-hidden="true"
                >
                  <svg :viewBox="`0 0 ${PIN_SPARK_W} ${PIN_SPARK_H}`" preserveAspectRatio="none" class="pin-spark-svg block w-full h-10 overflow-visible">
                    <defs>
                      <linearGradient :id="pinGradId(inst.id)" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0" stop-color="currentColor" stop-opacity="0.18" />
                        <stop offset="1" stop-color="currentColor" stop-opacity="0" />
                      </linearGradient>
                    </defs>
                    <path :d="pinSpark(pinAccounts[inst.brokerage_id].values).area" :fill="`url(#${pinGradId(inst.id)})`" />
                    <path
                      :d="pinSpark(pinAccounts[inst.brokerage_id].values).line"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="1.5"
                      stroke-linejoin="round"
                      stroke-linecap="round"
                      vector-effect="non-scaling-stroke"
                    />
                  </svg>
                  <!-- "Now": the line's last point, pinging while it runs -->
                  <span
                    class="pin-now"
                    :style="{ left: pinSpark(pinAccounts[inst.brokerage_id].values).left, top: pinSpark(pinAccounts[inst.brokerage_id].values).top }"
                  ></span>
                </div>
              </template>
              <p v-else class="text-sm text-slate-600 self-center">No brokerage linked</p>
            </div>

            <!-- Strategy and symbols -->
            <dl class="mt-5 pt-4 border-t border-white/[0.06] grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
              <dt class="text-slate-500">Strategy</dt>
              <dd class="truncate" :class="inst.strategy_id ? 'text-slate-300' : 'text-slate-600'" :title="pinStrategyName(inst) || ''">
                {{ pinStrategyName(inst) || 'No strategy linked' }}
              </dd>
              <dt class="text-slate-500">Symbols</dt>
              <dd class="truncate tabular-nums" :class="(inst.stocks || []).length ? 'text-slate-300' : 'text-slate-600'" :title="(inst.stocks || []).join(', ')">
                {{ (inst.stocks || []).length ? inst.stocks.join(', ') : 'None' }}
              </dd>
            </dl>
          </article>
        </div>
      </section>

      <!-- ── Everything else: one line per instance ──────────────────────── -->
      <section v-if="listInstances.length" :class="pinnedInstances.length ? 'mt-12' : ''" :aria-label="pinnedInstances.length ? 'Other instances' : 'Instances'">
        <div v-if="pinnedInstances.length" class="mb-3">
          <h2 class="ui-section-title">Other instances</h2>
        </div>
      <div class="ui-card">
        <div class="inst-row hidden md:grid ui-th">
          <span>Instance</span>
          <span>Status</span>
          <span class="hidden lg:block">Strategy</span>
          <span class="hidden lg:block">Brokerage</span>
          <span></span>
        </div>

        <ul class="ui-rows">
          <li
            v-for="inst in listInstances"
            :key="inst.id"
            class="inst-row ui-row relative grid items-center transition-colors duration-150 ease-out hover:bg-white/[0.03] last:rounded-b-xl max-md:first:rounded-t-xl"
          >
            <!-- Instance: name (the row link) and id · origin · symbols -->
            <div class="min-w-0">
              <RouterLink
                :to="'/instances/' + encodeURIComponent(inst.id)"
                class="flex items-center gap-2 min-w-0 focus-visible:outline-none after:absolute after:inset-0 after:content-[''] focus-visible:after:ring-2 focus-visible:after:ring-inset focus-visible:after:ring-white/25"
                :title="inst.name || inst.id"
              >
                <span class="md:hidden ui-dot" :class="inst.crashed ? 'ui-dot-error' : inst.runCommand ? 'ui-dot-live' : 'ui-dot-off'"></span>
                <span class="text-sm text-slate-100 truncate">{{ inst.name || inst.id }}</span>
                <span class="sr-only md:hidden">{{ inst.crashed ? 'Crashed' : inst.runCommand ? 'Running' : 'Stopped' }}</span>
              </RouterLink>
              <p class="text-xs text-slate-500 mt-0.5 truncate tabular-nums pl-3.5 md:pl-0">
                {{ inst.id }}
                <span class="hidden sm:inline">
                  <span class="text-slate-600" aria-hidden="true">·</span>
                  {{ (inst.created_by || 'user') === 'ai' ? 'AI created' : 'User created' }}
                </span>
                <template v-if="(inst.stocks || []).length">
                  {{ ' ' }}<span class="text-slate-600" aria-hidden="true">·</span>
                  {{ inst.stocks.length }} symbol{{ inst.stocks.length === 1 ? '' : 's' }}
                </template>
              </p>
            </div>

            <!-- Running status (crashed = held open for log capture) -->
            <span
              class="hidden md:inline-flex items-center gap-2 text-sm"
              :class="inst.crashed || inst.runCommand ? 'text-slate-200' : 'text-slate-500'"
            >
              <span class="ui-dot" :class="inst.crashed ? 'ui-dot-error' : inst.runCommand ? 'ui-dot-live' : 'ui-dot-off'"></span>
              {{ inst.crashed ? 'Crashed' : inst.runCommand ? 'Running' : 'Stopped' }}
            </span>

            <!-- Strategy -->
            <span class="hidden lg:block min-w-0 truncate text-sm" :class="inst.strategy_id ? 'text-slate-300' : 'text-slate-600'">
              {{ inst.strategy_id ? (strategyMap[inst.strategy_id] || inst.strategy_id) : 'No strategy' }}
            </span>

            <!-- Brokerage -->
            <span class="hidden lg:block min-w-0 truncate text-sm" :class="inst.brokerage_id ? 'text-slate-300' : 'text-slate-600'">
              {{ inst.brokerage_id ? (brokerageMap[inst.brokerage_id] || inst.brokerage_id) : '—' }}
            </span>

            <!-- Start / Stop and the overflow menu -->
            <div class="relative flex items-center justify-end gap-2" :class="openMenuId === inst.id ? 'z-30' : 'z-10'">
              <button
                v-if="!inst.runCommand"
                @click.stop="startInstance(inst.id)"
                :disabled="busy[inst.id]"
                class="ui-btn w-16"
              >
                <span v-if="busy[inst.id]" class="material-symbols-outlined text-[16px] animate-spin" aria-label="Working">progress_activity</span>
                <template v-else>Start</template>
              </button>
              <button
                v-else
                @click.stop="stopInstance(inst.id)"
                :disabled="busy[inst.id]"
                class="ui-btn w-16 text-loss"
              >
                <span v-if="busy[inst.id]" class="material-symbols-outlined text-[16px] animate-spin" aria-label="Working">progress_activity</span>
                <template v-else>Stop</template>
              </button>

              <div class="relative" :data-row-menu="inst.id">
                <button
                  type="button"
                  @click.stop="toggleMenu(inst.id)"
                  class="ui-icon-btn"
                  :class="openMenuId === inst.id ? 'bg-raised text-slate-200' : ''"
                  aria-haspopup="menu"
                  :aria-expanded="openMenuId === inst.id"
                  :aria-label="`More actions for ${inst.name || inst.id}`"
                >
                  <span class="material-symbols-outlined text-[18px]">more_horiz</span>
                </button>

                <div
                  v-if="openMenuId === inst.id"
                  class="ui-menu"
                  role="menu"
                  @keydown="onMenuKey($event, inst.id)"
                >
                  <button
                    type="button" role="menuitem" class="ui-menu-item menu-focus"
                    @click="closeMenu(); router.push('/instances/' + encodeURIComponent(inst.id))"
                  >View</button>
                  <button
                    type="button" role="menuitem" class="ui-menu-item menu-focus"
                    :disabled="busy[inst.id]"
                    @click="closeMenu(); openCreateBacktest(inst.id)"
                  >Run backtest</button>
                  <!-- Link strategy (no strategy linked) -->
                  <button
                    v-if="!inst.strategy_id"
                    type="button" role="menuitem" class="ui-menu-item menu-focus"
                    :disabled="busy[inst.id]"
                    @click="closeMenu(); openLinkStrategy(inst.id)"
                  >Link strategy</button>
                  <!-- Edit strategy (strategy linked) -->
                  <button
                    v-if="inst.strategy_id"
                    type="button" role="menuitem" class="ui-menu-item menu-focus"
                    :disabled="busy[inst.id]"
                    @click="closeMenu(); openEditStrategy(inst.strategy_id)"
                  >
                    Edit strategy
                    <span class="ml-auto pl-4 text-xs text-slate-500 tabular-nums">ID {{ inst.strategy_id }}</span>
                  </button>
                  <!-- Change brokerage (only when a brokerage is linked) -->
                  <button
                    v-if="inst.brokerage_id"
                    type="button" role="menuitem" class="ui-menu-item menu-focus"
                    :disabled="busy[inst.id]"
                    @click="closeMenu(); openChangeBrokerage(inst)"
                  >Change brokerage</button>
                  <button
                    type="button" role="menuitem" class="ui-menu-item menu-focus"
                    @click="closeMenu(); openAddStock(inst.id)"
                  >
                    Edit stocks
                    <span class="ml-auto pl-4 text-xs text-slate-500 tabular-nums">{{ (inst.stocks || []).length }}</span>
                  </button>
                  <button
                    type="button" role="menuitem" class="ui-menu-item menu-focus"
                    @click="closeMenu(); togglePin(inst.id)"
                  >Pin to top</button>
                  <div class="ui-menu-sep" role="separator"></div>
                  <button
                    type="button" role="menuitem" class="ui-menu-item menu-focus text-loss"
                    :disabled="busy[inst.id]"
                    @click="closeMenu(); openDelete(inst.id, inst.name)"
                  >Delete</button>
                </div>
              </div>
            </div>
          </li>
        </ul>
      </div>
      </section>
      </template>

    </main>
  </AppShell>

  <!-- ── Delete Instance Modal ─────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showDelete"
        class="ui-overlay"
        @click.self="closeDelete"
      >
        <div class="modal-panel ui-modal max-w-sm">

          <div class="ui-modal-head">
            <h2 class="text-base font-medium text-slate-100">Delete instance</h2>
            <button @click="closeDelete" :disabled="deleting" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="ui-modal-body space-y-4">
            <!-- Normal state -->
            <div v-if="!deleteForce">
              <p class="text-sm text-slate-300">
                Delete
                <span class="text-slate-100">{{ deleteTarget?.name || deleteTarget?.id }}</span>?
              </p>
              <p class="text-sm text-slate-500 mt-1">This can't be undone</p>
            </div>

            <!-- Force state — linked to strategy -->
            <div v-else>
              <p class="text-sm text-slate-300">
                This instance is linked to a strategy and can't be deleted normally.
              </p>
              <p class="text-sm text-slate-500 mt-1">
                Force-deleting removes the instance without unlinking it from the strategy.
              </p>
            </div>

            <!-- Error message -->
            <Transition name="slide-up">
              <div v-if="deleteError" class="msg msg-err">
                {{ deleteError }}
              </div>
            </Transition>
          </div>

          <div class="ui-modal-foot">
            <button @click="closeDelete" :disabled="deleting" class="ui-btn-ghost h-9 px-4 rounded-lg">
              Cancel
            </button>
            <!-- Normal delete -->
            <button v-if="!deleteForce" @click="confirmDelete(false)" :disabled="deleting" class="ui-btn h-9 px-4 text-loss">
              <span v-if="deleting" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ deleting ? 'Deleting...' : 'Delete' }}
            </button>
            <!-- Force delete -->
            <button v-else @click="confirmDelete(true)" :disabled="deleting" class="ui-btn h-9 px-4 text-loss">
              <span v-if="deleting" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ deleting ? 'Deleting...' : 'Force delete' }}
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
        <div class="modal-panel ui-modal max-w-sm">
          <div class="ui-modal-head">
            <h2 class="text-base font-medium text-slate-100">Link strategy</h2>
            <button @click="closeLinkStrategy" :disabled="linkStrategyBusy" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>
          <div class="ui-modal-body space-y-4">
            <div>
              <label class="ui-field-label">Strategy</label>
              <select v-model="linkStrategyId" class="ui-input">
                <option value="">Select a strategy</option>
                <option v-for="s in strategies" :key="s.id" :value="s.id">{{ s.name || s.id }}</option>
              </select>
            </div>
            <Transition name="slide-up">
              <div
                v-if="linkStrategyMsg"
                class="msg"
                :class="linkStrategyOk ? 'msg-ok' : 'msg-err'"
              >
                {{ linkStrategyMsg }}
              </div>
            </Transition>
          </div>
          <div class="ui-modal-foot">
            <button @click="closeLinkStrategy" :disabled="linkStrategyBusy" class="ui-btn-ghost h-9 px-4 rounded-lg">
              Cancel
            </button>
            <button @click="submitLinkStrategy" :disabled="linkStrategyBusy || !linkStrategyId" class="ui-btn-primary">
              <span v-if="linkStrategyBusy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ linkStrategyBusy ? 'Linking...' : 'Link strategy' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Change Brokerage Modal ─────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showChangeBrokerage"
        class="ui-overlay"
        @click.self="closeChangeBrokerage"
      >
        <div class="modal-panel ui-modal max-w-sm">
          <div class="ui-modal-head">
            <h2 class="text-base font-medium text-slate-100">Change brokerage</h2>
            <button @click="closeChangeBrokerage" :disabled="changeBrokerageBusy" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>
          <div class="ui-modal-body space-y-4">
            <div>
              <label class="ui-field-label">Brokerage</label>
              <select v-model="changeBrokerageId" class="ui-input">
                <option value="">Unlink brokerage</option>
                <option v-for="b in brokerages" :key="b.id" :value="b.id">{{ b.account_name }} ({{ b.brokerage_type }})</option>
              </select>
            </div>
            <Transition name="slide-up">
              <div
                v-if="changeBrokerageMsg"
                class="msg"
                :class="changeBrokerageOk ? 'msg-ok' : 'msg-err'"
              >
                {{ changeBrokerageMsg }}
              </div>
            </Transition>
          </div>
          <div class="ui-modal-foot">
            <button @click="closeChangeBrokerage" :disabled="changeBrokerageBusy" class="ui-btn-ghost h-9 px-4 rounded-lg">
              Cancel
            </button>
            <button @click="submitChangeBrokerage" :disabled="changeBrokerageBusy" class="ui-btn-primary">
              <span v-if="changeBrokerageBusy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ changeBrokerageBusy ? 'Saving...' : 'Save' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Create Instance Modal ─────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showCreate"
        class="ui-overlay"
        @click.self="closeCreate"
      >
        <div class="modal-panel ui-modal max-w-md flex flex-col max-h-[90vh]">

          <div class="ui-modal-head shrink-0">
            <h2 class="text-base font-medium text-slate-100">New instance</h2>
            <button @click="closeCreate" class="ui-icon-btn ml-auto shrink-0" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="ui-modal-body flex-1 overflow-y-auto space-y-5">
            <div>
              <label class="ui-field-label">Instance ID <span class="text-slate-500">*</span></label>
              <input
                v-model="createForm.id"
                type="text"
                placeholder="e.g. my-live-instance"
                class="ui-input font-mono"
              />
              <p class="ui-help">Unique identifier for this instance</p>
            </div>
            <div>
              <label class="ui-field-label">Display name <span class="text-slate-500">(optional)</span></label>
              <input
                v-model="createForm.name"
                type="text"
                placeholder="e.g. Momentum strategy live"
                class="ui-input"
              />
            </div>
            <div>
              <label class="ui-field-label">Granularity</label>
              <div class="ui-seg flex-wrap" role="group" aria-label="Granularity">
                <button
                  v-for="g in GRANULARITIES"
                  :key="g.value"
                  @click="createForm.granularity = g.value"
                  class="ui-seg-btn tabular-nums"
                  :class="createForm.granularity === g.value ? 'ui-seg-btn-active' : ''"
                >
                  {{ g.label }}
                </button>
              </div>
            </div>
            <div class="flex items-center gap-3">
              <button
                type="button"
                @click="createForm.run_command = !createForm.run_command"
                class="ui-switch focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/25"
                role="switch"
                :aria-checked="createForm.run_command"
                aria-label="Start immediately after creation"
              >
                <span class="ui-switch-knob"></span>
              </button>
              <span class="text-sm text-slate-300">Start immediately after creation</span>
            </div>

            <!-- Brokerage -->
            <div v-if="brokerages.length">
              <label class="ui-field-label">Brokerage <span class="text-slate-500">(optional)</span></label>
              <select v-model="createForm.brokerage_id" class="ui-input">
                <option value="">None</option>
                <option v-for="b in brokerages" :key="b.id" :value="b.id">
                  {{ b.account_name }} ({{ b.brokerage_type }})
                </option>
              </select>
            </div>

            <!-- Max usage -->
            <div v-if="createForm.brokerage_id">
              <label class="ui-field-label">Max usage <span class="text-slate-500">($, optional)</span></label>
              <input
                v-model="createForm.max_usage"
                type="number"
                min="0"
                step="100"
                placeholder="e.g. 10000"
                class="ui-input tabular-nums"
              />
              <p class="ui-help">Maximum capital this instance can use from the brokerage</p>
            </div>

            <!-- Strategy -->
            <div>
              <label class="ui-field-label">Strategy <span class="text-slate-500">(optional)</span></label>
              <div class="flex gap-2">
                <select v-model="createForm.strategy_id" class="ui-input flex-1 min-w-0">
                  <option value="">None</option>
                  <option v-for="s in strategies" :key="s.id" :value="String(s.id)">
                    {{ s.name || s.id }}
                  </option>
                </select>
                <button
                  type="button"
                  @click="openCreateStrategy"
                  class="ui-btn h-9 shrink-0"
                  title="Create new strategy"
                >
                  New
                </button>
              </div>
              <p v-if="createForm.strategy_id" class="ui-help">
                Strategy will be linked after instance creation
              </p>
            </div>

            <Transition name="slide-up">
              <div
                v-if="createMsg"
                class="msg"
                :class="creating ? 'msg-busy' : createOk ? 'msg-ok' : 'msg-err'"
              >
                <span v-if="creating" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                  {{ createMsg }}
                </span>
                <span v-else>{{ createMsg }}</span>
              </div>
            </Transition>
          </div>

          <div class="ui-modal-foot shrink-0">
            <button @click="closeCreate" :disabled="creating" class="ui-btn-ghost h-9 px-4 rounded-lg">
              Cancel
            </button>
            <button @click="submitCreate" :disabled="creating" class="ui-btn-primary">
              <span v-if="creating" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ creating ? 'Creating...' : 'Create' }}
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
        <div class="modal-panel ui-modal max-w-2xl flex flex-col max-h-[90vh]">

          <!-- Header -->
          <div class="ui-modal-head shrink-0">
            <h2 class="text-base font-medium text-slate-100 truncate">Edit strategy</h2>
            <button @click="closeEditStrategy" :disabled="editStrategySaving" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <!-- Body -->
          <div class="ui-modal-body flex-1 overflow-y-auto space-y-6">

            <!-- Loading -->
            <div v-if="editStrategyLoading" class="space-y-3 py-2" aria-label="Loading strategy">
              <div class="ui-skel h-4 w-32"></div>
              <div class="ui-skel h-9 w-full rounded-lg"></div>
              <div class="ui-skel h-40 w-full rounded-xl"></div>
            </div>

            <template v-else-if="editStrategyData">
              <!-- Strategy name -->
              <div>
                <label class="ui-field-label">Strategy name</label>
                <input
                  v-model="editStrategyData.name"
                  type="text"
                  class="ui-input"
                />
              </div>

              <!-- Sub-strategies -->
              <div>
                <div v-if="availableStrategyOptions.length" class="mb-6">
                  <label class="ui-field-label">Add sub-strategy</label>
                  <div class="flex gap-2">
                    <select v-model="editStrategyPickName" class="ui-input flex-1 min-w-0">
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

                <p class="text-sm text-slate-200 mb-3">
                  Sub-strategies
                  <span class="text-slate-500 tabular-nums ml-1">{{ editStrategyData.strategies?.length || 0 }}</span>
                </p>

                <div v-if="!editStrategyData.strategies?.length" class="text-sm text-slate-500">
                  No sub-strategies configured
                </div>

                <div class="space-y-3">
                  <div
                    v-for="(sub, idx) in editStrategyData.strategies"
                    :key="idx"
                    class="rounded-xl border border-white/[0.07] p-4 space-y-5"
                  >
                    <!-- Sub-strategy header -->
                    <div class="flex items-start justify-between gap-3">
                      <div class="min-w-0">
                        <div class="flex items-baseline gap-2 flex-wrap">
                          <p class="text-sm font-medium text-slate-100">{{ strategyLabel(sub.strategy) }}</p>
                          <span class="text-xs text-slate-500 font-mono">{{ strategyCanonicalId(sub.strategy) }}</span>
                        </div>
                        <p v-if="getStrategyMeta(sub.strategy)?.description" class="text-xs text-slate-500 mt-1 leading-relaxed">
                          {{ getStrategyMeta(sub.strategy)?.description }}
                        </p>
                      </div>
                      <button
                        @click="removeSubStrategy(idx)"
                        class="ui-icon-btn -mr-2 -mt-1 shrink-0 hover:text-loss"
                        title="Remove sub-strategy"
                        aria-label="Remove sub-strategy"
                      >
                        <span class="material-symbols-outlined text-[18px]">delete</span>
                      </button>
                    </div>

                    <!-- Core fields -->
                    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
                      <!-- Weight -->
                      <div>
                        <label class="sub-label">Weight</label>
                        <input
                          v-model.number="sub.weight"
                          type="number" min="0" max="1" step="0.05"
                          class="ui-input tabular-nums"
                        />
                      </div>
                      <!-- Execution Position -->
                      <div>
                        <label class="sub-label">Exec position</label>
                        <input
                          v-model.number="sub.execution_position"
                          type="number" step="1"
                          class="ui-input tabular-nums"
                        />
                      </div>
                      <!-- Decision Phase -->
                      <div>
                        <label class="sub-label">Phase</label>
                        <div class="ui-seg w-full">
                          <button
                            v-for="p in ['pre', 'post']" :key="p"
                            @click="sub.decision_phase = p"
                            class="ui-seg-btn flex-1 h-8 px-0"
                            :class="sub.decision_phase === p ? 'ui-seg-btn-active' : ''"
                          >{{ p === 'pre' ? 'Pre' : 'Post' }}</button>
                        </div>
                      </div>
                      <!-- Execution Scope -->
                      <div>
                        <label class="sub-label">Scope</label>
                        <div class="ui-seg w-full">
                          <button
                            v-for="s in ['per_symbol', 'run_once']" :key="s"
                            @click="sub.execution_scope = s"
                            class="ui-seg-btn flex-1 h-8 px-0"
                            :class="sub.execution_scope === s ? 'ui-seg-btn-active' : ''"
                          >{{ s === 'per_symbol' ? 'Per sym' : 'Once' }}</button>
                        </div>
                      </div>
                    </div>

                    <!-- LLM -->
                    <div v-if="llmConfigGroups(sub).length">
                      <p class="sub-section">LLM</p>
                      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div
                          v-for="group in llmConfigGroups(sub)"
                          :key="group.label"
                          class="rounded-lg border border-white/[0.07] px-3 py-3"
                        >
                          <div class="flex items-start justify-between gap-3">
                            <div class="min-w-0">
                              <p class="text-sm text-slate-200">{{ group.label }}</p>
                              <p class="text-xs text-slate-500 mt-0.5 leading-relaxed">{{ llmConfigSummary(sub, group) }}</p>
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
                    </div>

                    <!-- Config fields -->
                    <div v-if="configEntries(sub).length">
                      <p class="sub-section">Config</p>
                      <div class="grid grid-cols-2 sm:grid-cols-3 gap-x-3 gap-y-4">
                      <div v-for="[key, val] in configEntries(sub)" :key="key">
                        <label class="sub-label">{{ getStrategyConfigFieldMeta(sub.strategy, key).label }}</label>
                        <!-- Boolean toggle -->
                        <template v-if="getConfigType(getRefConfig(sub.strategy)[key] ?? val) === 'boolean'">
                          <button
                            type="button"
                            @click="sub.config[key] = !sub.config[key]"
                              class="ui-switch focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/25"
                              role="switch"
                              :aria-checked="!!sub.config[key]"
                            >
                              <span class="ui-switch-knob"></span>
                            </button>
                          </template>
                          <!-- Number input -->
                          <template v-else-if="getConfigType(getRefConfig(sub.strategy)[key] ?? val) === 'number'">
                            <input
                              :value="sub.config[key]"
                              @input="updateConfigField(sub, key, $event.target.value, 'number')"
                              type="number"
                              class="ui-input tabular-nums"
                            />
                          </template>
                          <!-- String / fallback -->
                          <template v-else-if="getConfigType(getRefConfig(sub.strategy)[key] ?? val) === 'json'">
                            <textarea
                              :value="getConfigFieldValue(sub, key, val)"
                              @input="updateConfigField(sub, key, $event.target.value, 'json')"
                              @blur="syncConfigJsonDraft(sub, key, val)"
                              rows="4"
                              spellcheck="false"
                              class="ui-input text-xs font-mono resize-y"
                              :class="getConfigFieldError(sub, key) ? 'border-loss/60 focus:border-loss' : ''"
                            ></textarea>
                          </template>
                          <template v-else>
                            <input
                              v-model="sub.config[key]"
                              type="text"
                              class="ui-input font-mono"
                            />
                          </template>
                          <p v-if="getConfigFieldError(sub, key)" class="mt-1.5 text-xs leading-relaxed text-loss">
                            Invalid JSON: {{ getConfigFieldError(sub, key) }}
                          </p>
                        <p v-if="getStrategyConfigFieldMeta(sub.strategy, key).description" class="mt-1.5 text-xs leading-relaxed text-slate-500">
                          {{ getStrategyConfigFieldMeta(sub.strategy, key).description }}
                        </p>
                      </div>
                    </div>
                  </div>

                  </div>
                </div>
              </div>
            </template>

            <!-- Error (no data, no loading) -->
            <div v-else-if="editStrategyMsg && !editStrategyOk" class="msg msg-err">
              {{ editStrategyMsg }}
            </div>

            <!-- Status message (when data is present) -->
            <Transition name="slide-up">
              <div
                v-if="editStrategyMsg && editStrategyData"
                class="msg"
                :class="editStrategySaving ? 'msg-busy' : editStrategyOk ? 'msg-ok' : 'msg-err'"
              >
                {{ editStrategyMsg }}
              </div>
            </Transition>
          </div>

          <!-- Footer -->
          <div class="ui-modal-foot shrink-0">
            <button @click="closeEditStrategy" :disabled="editStrategySaving" class="ui-btn-ghost h-9 px-4 rounded-lg">
              Cancel
            </button>
            <button
              v-if="editStrategyData && !editStrategyLoading"
              @click="submitEditStrategy" :disabled="editStrategySaving"
              class="ui-btn-primary">
              <span v-if="editStrategySaving" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ editStrategySaving ? 'Saving...' : 'Save changes' }}
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
        <div class="modal-panel ui-modal max-w-md flex flex-col max-h-[90vh]">

          <div class="ui-modal-head shrink-0">
            <div class="min-w-0">
              <h2 class="text-base font-medium text-slate-100">Create backtest</h2>
              <p class="text-sm text-slate-500 mt-1 truncate">{{ createBtForm.instance_id }}</p>
            </div>
            <button @click="closeCreateBacktest" :disabled="creatingBacktest" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="ui-modal-body flex-1 overflow-y-auto space-y-5">
            <!-- Stocks -->
            <div>
              <label class="ui-field-label">Stocks <span class="text-slate-500">*</span></label>
              <input
                v-model="createBtForm.stocks"
                type="text"
                placeholder="e.g. AAPL, MSFT, TSLA"
                class="ui-input font-mono"
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
              <div class="ui-seg flex-wrap" role="group" aria-label="Granularity">
                <button
                  v-for="g in GRANULARITIES"
                  :key="g.value"
                  @click="createBtForm.granularity = g.value"
                  class="ui-seg-btn tabular-nums"
                  :class="createBtForm.granularity === g.value ? 'ui-seg-btn-active' : ''"
                >
                  {{ g.label }}
                </button>
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
                class="msg"
                :class="creatingBacktest ? 'msg-busy' : createBtOk ? 'msg-ok' : 'msg-err'"
              >
                <span v-if="creatingBacktest" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                  {{ createBtMsg }}
                </span>
                <span v-else>{{ createBtMsg }}</span>
              </div>
            </Transition>
          </div>

          <div class="ui-modal-foot shrink-0">
            <button @click="closeCreateBacktest" :disabled="creatingBacktest" class="ui-btn-ghost h-9 px-4 rounded-lg">
              Cancel
            </button>
            <button @click="submitCreateBacktest" :disabled="creatingBacktest" class="ui-btn-primary">
              <span v-if="creatingBacktest" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ creatingBacktest ? 'Queuing...' : 'Run backtest' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Create Strategy Modal (nested from Create Instance) ─────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showCreateStrategy"
        class="ui-overlay z-[60] bg-black/70"
      >
        <div class="modal-panel ui-modal max-w-2xl flex flex-col max-h-[90vh]">

          <!-- Header -->
          <div class="ui-modal-head shrink-0">
            <h2 class="text-base font-medium text-slate-100">New strategy</h2>
            <button @click="closeCreateStrategy" :disabled="creatingStrategy" class="ui-icon-btn ml-auto shrink-0 disabled:opacity-40" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <!-- Body -->
          <div class="ui-modal-body flex-1 overflow-y-auto space-y-6">

            <!-- Name -->
            <div>
              <label class="ui-field-label">Strategy name <span class="text-slate-500">*</span></label>
              <input
                v-model="newStrategyForm.name"
                type="text"
                placeholder="e.g. My momentum strategy"
                class="ui-input"
              />
            </div>

            <!-- Add sub-strategy -->
            <div v-if="availableStrategyOptions.length">
              <label class="ui-field-label">Add sub-strategy</label>
              <div class="flex gap-2">
                <select v-model="newStrategyPickName" class="ui-input flex-1 min-w-0">
                  <option v-for="s in availableStrategyOptions" :key="s.value" :value="s.value">{{ s.label }} · {{ s.value }}</option>
                </select>
                <button
                  type="button"
                  @click="addNewSubStrategy"
                  class="ui-btn h-9 shrink-0"
                >
                  Add
                </button>
              </div>
            </div>

            <!-- Sub-strategy cards -->
            <div>
              <p class="text-sm text-slate-200 mb-3">
                Sub-strategies
                <span class="text-slate-500 tabular-nums ml-1">{{ newStrategyForm.strategies.length }}</span>
              </p>
              <div v-if="!newStrategyForm.strategies.length" class="text-sm text-slate-500">
                No sub-strategies yet. Add one with the selector above.
              </div>
              <div class="space-y-3">
                <div
                  v-for="(sub, idx) in newStrategyForm.strategies"
                  :key="idx"
                  class="rounded-xl border border-white/[0.07] p-4 space-y-5"
                >
                  <!-- Header -->
                  <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                      <div class="flex items-baseline gap-2 flex-wrap">
                        <p class="text-sm font-medium text-slate-100">{{ strategyLabel(sub.strategy) }}</p>
                        <span class="text-xs text-slate-500 font-mono">{{ strategyCanonicalId(sub.strategy) }}</span>
                      </div>
                      <p v-if="getStrategyMeta(sub.strategy)?.description" class="text-xs text-slate-500 mt-1 leading-relaxed">
                        {{ getStrategyMeta(sub.strategy)?.description }}
                      </p>
                    </div>
                    <button
                      @click="removeNewSubStrategy(idx)"
                      class="ui-icon-btn -mr-2 -mt-1 shrink-0 hover:text-loss"
                      title="Remove"
                      aria-label="Remove sub-strategy"
                    >
                      <span class="material-symbols-outlined text-[18px]">delete</span>
                    </button>
                  </div>

                  <!-- Core fields -->
                  <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <div>
                      <label class="sub-label">Weight</label>
                      <input v-model.number="sub.weight" type="number" min="0" max="1" step="0.05"
                        class="ui-input tabular-nums" />
                    </div>
                    <div>
                      <label class="sub-label">Exec position</label>
                      <input v-model.number="sub.execution_position" type="number" step="1"
                        class="ui-input tabular-nums" />
                    </div>
                    <div>
                      <label class="sub-label">Phase</label>
                      <div class="ui-seg w-full">
                        <button v-for="p in ['pre', 'post']" :key="p" @click="sub.decision_phase = p"
                          class="ui-seg-btn flex-1 h-8 px-0"
                          :class="sub.decision_phase === p ? 'ui-seg-btn-active' : ''">
                          {{ p === 'pre' ? 'Pre' : 'Post' }}
                        </button>
                      </div>
                    </div>
                    <div>
                      <label class="sub-label">Scope</label>
                      <div class="ui-seg w-full">
                        <button v-for="s in ['per_symbol', 'run_once']" :key="s" @click="sub.execution_scope = s"
                          class="ui-seg-btn flex-1 h-8 px-0"
                          :class="sub.execution_scope === s ? 'ui-seg-btn-active' : ''">
                          {{ s === 'per_symbol' ? 'Per sym' : 'Once' }}
                        </button>
                      </div>
                    </div>
                  </div>

                  <!-- LLM -->
                  <div v-if="llmConfigGroups(sub).length">
                    <p class="sub-section">LLM</p>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div
                        v-for="group in llmConfigGroups(sub)"
                        :key="group.label"
                        class="rounded-lg border border-white/[0.07] px-3 py-3"
                      >
                        <div class="flex items-start justify-between gap-3">
                          <div class="min-w-0">
                            <p class="text-sm text-slate-200">{{ group.label }}</p>
                            <p class="text-xs text-slate-500 mt-0.5 leading-relaxed">{{ llmConfigSummary(sub, group) }}</p>
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
                  </div>

                  <!-- Config -->
                  <div v-if="configEntries(sub).length">
                    <p class="sub-section">Config</p>
                    <div class="grid grid-cols-2 sm:grid-cols-3 gap-x-3 gap-y-4">
                      <div v-for="[key, val] in configEntries(sub)" :key="key">
                        <label class="sub-label">{{ getStrategyConfigFieldMeta(sub.strategy, key).label }}</label>
                        <template v-if="getConfigType(getRefConfig(sub.strategy)[key] ?? val) === 'boolean'">
                          <button type="button" @click="sub.config[key] = !sub.config[key]"
                            class="ui-switch focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/25"
                            role="switch"
                            :aria-checked="!!sub.config[key]">
                            <span class="ui-switch-knob"></span>
                          </button>
                        </template>
                        <template v-else-if="getConfigType(getRefConfig(sub.strategy)[key] ?? val) === 'number'">
                          <input :value="sub.config[key]" @input="updateConfigField(sub, key, $event.target.value, 'number')"
                            type="number" class="ui-input tabular-nums" />
                        </template>
                        <template v-else-if="getConfigType(getRefConfig(sub.strategy)[key] ?? val) === 'json'">
                          <textarea
                            :value="getConfigFieldValue(sub, key, val)"
                            @input="updateConfigField(sub, key, $event.target.value, 'json')"
                            @blur="syncConfigJsonDraft(sub, key, val)"
                            rows="4"
                            spellcheck="false"
                            class="ui-input text-xs font-mono resize-y"
                            :class="getConfigFieldError(sub, key) ? 'border-loss/60 focus:border-loss' : ''"
                          ></textarea>
                        </template>
                        <template v-else>
                          <input v-model="sub.config[key]" type="text"
                            class="ui-input font-mono" />
                        </template>
                        <p v-if="getConfigFieldError(sub, key)" class="mt-1.5 text-xs leading-relaxed text-loss">
                          Invalid JSON: {{ getConfigFieldError(sub, key) }}
                        </p>
                        <p v-if="getStrategyConfigFieldMeta(sub.strategy, key).description" class="mt-1.5 text-xs leading-relaxed text-slate-500">
                          {{ getStrategyConfigFieldMeta(sub.strategy, key).description }}
                        </p>
                      </div>
                    </div>
                  </div>

                </div>
              </div>
            </div>

            <!-- Status -->
            <Transition name="slide-up">
              <div
                v-if="createStrategyMsg"
                class="msg"
                :class="creatingStrategy ? 'msg-busy' : createStrategyOk ? 'msg-ok' : 'msg-err'"
              >
                <span v-if="creatingStrategy" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                  {{ createStrategyMsg }}
                </span>
                <span v-else>{{ createStrategyMsg }}</span>
              </div>
            </Transition>
          </div>

          <!-- Footer -->
          <div class="ui-modal-foot shrink-0">
            <button @click="closeCreateStrategy" :disabled="creatingStrategy" class="ui-btn-ghost h-9 px-4 rounded-lg">
              Cancel
            </button>
            <button @click="submitCreateStrategy" :disabled="creatingStrategy" class="ui-btn-primary">
              <span v-if="creatingStrategy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ creatingStrategy ? 'Creating...' : 'Create strategy' }}
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
        class="ui-overlay z-[70] bg-black/70"
      >
        <div class="modal-panel ui-modal max-w-lg flex flex-col max-h-[90vh]">
          <div class="ui-modal-head shrink-0">
            <div class="min-w-0">
              <h2 class="text-base font-medium text-slate-100">{{ llmConfigTarget?.group?.label || 'LLM config' }}</h2>
              <p class="text-sm text-slate-500 mt-1">Pick one of your saved models</p>
            </div>
            <button @click="closeLlmConfigModal" class="ui-icon-btn ml-auto shrink-0" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="ui-modal-body flex-1 overflow-y-auto space-y-4">
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

            <!-- Show selected model details (read-only) -->
            <dl v-if="llmConfigDraft.modelId" class="rounded-lg border border-white/[0.07] px-4 py-1 divide-y divide-white/[0.05] text-sm">
              <div class="flex items-center justify-between gap-4 h-10">
                <dt class="text-slate-500">Provider</dt>
                <dd class="text-slate-200 truncate">{{ getLlmProviderLabel(llmConfigDraft.provider) }}</dd>
              </div>
              <div class="flex items-center justify-between gap-4 h-10">
                <dt class="text-slate-500">Model</dt>
                <dd class="text-slate-200 font-mono text-xs truncate">{{ llmConfigDraft.model }}</dd>
              </div>
              <div class="flex items-center justify-between gap-4 h-10">
                <dt class="text-slate-500">Effort</dt>
                <dd class="text-slate-200">{{ effortLabel(llmConfigDraft) }}</dd>
              </div>
            </dl>

            <p v-if="savedModels.length === 0" class="flex items-start gap-2 text-sm text-slate-400">
              <span class="ui-dot ui-dot-warn mt-2"></span>
              <span>No models saved yet. Add one on the <span class="text-slate-200">Models</span> page first.</span>
            </p>

            <div v-if="llmConfigMsg" class="msg msg-err">
              {{ llmConfigMsg }}
            </div>
          </div>

          <div class="ui-modal-foot shrink-0">
            <button @click="closeLlmConfigModal" class="ui-btn-ghost h-9 px-4 rounded-lg">
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

  <!-- ── Edit Stocks Modal (the add-stock flow, with the current list) ─────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showAddStock"
        class="ui-overlay"
        @click.self="closeAddStock"
      >
        <div class="modal-panel ui-modal max-w-sm flex flex-col max-h-[90vh]">
          <div class="ui-modal-head shrink-0">
            <div class="min-w-0">
              <h2 class="text-base font-medium text-slate-100">Edit stocks</h2>
              <p class="text-sm text-slate-500 mt-1 truncate">{{ addStockId }}</p>
            </div>
            <button @click="closeAddStock" class="ui-icon-btn ml-auto shrink-0" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>
          <div class="ui-modal-body flex-1 overflow-y-auto space-y-5">
            <!-- Current symbols, each removable -->
            <ul
              v-if="(instances.find(i => i.id === addStockId)?.stocks || []).length"
              class="rounded-lg border border-white/[0.07] divide-y divide-white/[0.05]"
            >
              <li
                v-for="sym in instances.find(i => i.id === addStockId).stocks"
                :key="sym"
                class="h-10 pl-4 pr-1 flex items-center justify-between gap-3"
              >
                <span class="text-sm text-slate-200 tabular-nums">{{ sym }}</span>
                <button
                  @click="removeStock(addStockId, sym)"
                  :disabled="busy[addStockId]"
                  class="ui-icon-btn hover:text-loss disabled:opacity-40"
                  :title="`Remove ${sym}`"
                  :aria-label="`Remove ${sym}`"
                >
                  <span class="material-symbols-outlined text-[18px]">close</span>
                </button>
              </li>
            </ul>
            <p v-else class="text-sm text-slate-500">No stocks yet</p>

            <div>
              <label class="ui-field-label">Add a ticker symbol</label>
              <div class="flex gap-2">
                <input
                  ref="addStockInput"
                  v-model="addStockSym"
                  type="text"
                  placeholder="e.g. AAPL"
                  autocapitalize="characters"
                  @keydown.enter="submitAddStock"
                  @input="addStockOk = ''"
                  class="ui-input font-mono flex-1 min-w-0"
                />
                <button @click="submitAddStock" :disabled="addStockBusy" class="ui-btn h-9 px-4 shrink-0">
                  <span v-if="addStockBusy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                  {{ addStockBusy ? 'Adding' : 'Add stock' }}
                </button>
              </div>
              <p v-if="addStockOk" class="mt-2 text-xs text-gain" role="status">{{ addStockOk }}</p>
            </div>
            <div v-if="addStockMsg" class="msg msg-err">
              {{ addStockMsg }}
            </div>
          </div>
          <div class="ui-modal-foot shrink-0">
            <button @click="closeAddStock" :disabled="addStockBusy" class="ui-btn-ghost h-9 px-4 rounded-lg">
              Done
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* Instance rows: one line each. Hidden cells take no track, so each
   breakpoint lists only what shows:
   phone  name (+ status dot) · actions
   md     name · status · actions
   lg+    name · status · strategy · brokerage · actions
   Actions = Start/Stop (64) + gap (8) + ⋯ (32). */
.inst-row {
  grid-template-columns: minmax(0, 1fr) auto;
  column-gap: 16px;
}
@media (min-width: 768px) {
  .inst-row { grid-template-columns: minmax(0, 1fr) 112px 104px; }
}
@media (min-width: 1024px) {
  .inst-row { grid-template-columns: minmax(0, 1.4fr) 112px minmax(0, 1fr) minmax(0, 1fr) 104px; column-gap: 24px; }
}

/* Pinned cards.
   Running is the one place this page moves, so it gets a little show:
   - a light orbits the card's border (a conic gradient spun behind a 1px
     ring mask) with a soft blurred glow following it;
   - the border and the top of the card take a faint green tint;
   - the status dot pings, the uptime ticks, and the 1D line draws in and
     ends in a pinging "now" dot;
   - Start flashes the whole border once; Stop fades the orbit out.
   Stopped cards are still; crashed cards get a red hairline. Only transform
   and opacity animate in a loop, and none of it runs under reduced motion. */
.pin-card {
  --pin-live: 76, 195, 138;
  transition: border-color 400ms cubic-bezier(0.22, 1, 0.36, 1);
}
.pin-card:hover { border-color: rgba(255, 255, 255, 0.12); }
.pin-running {
  border-color: rgba(var(--pin-live), 0.18);
  background-image: linear-gradient(180deg, rgba(var(--pin-live), 0.055) 0, rgba(var(--pin-live), 0) 128px);
}
.pin-running:hover { border-color: rgba(var(--pin-live), 0.3); }
.pin-crashed { border-color: rgba(229, 72, 77, 0.28); }
.pin-crashed:hover { border-color: rgba(229, 72, 77, 0.4); }

.pin-fx { position: absolute; inset: -1px; border-radius: 12px; pointer-events: none; }

/* A 1px ring: the element's padding box minus its content box. */
.pin-ring {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1px;
  overflow: hidden;
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0);
  opacity: 0;
  visibility: hidden;
  transition: opacity 700ms cubic-bezier(0.22, 1, 0.36, 1), visibility 0s linear 700ms;
}
.pin-running .pin-ring {
  opacity: 1;
  visibility: visible;
  transition: opacity 700ms cubic-bezier(0.22, 1, 0.36, 1);
}

/* The comet: a long faint tail brightening to a near-white head. The square
   is twice the card's width, so it covers the card at any aspect. */
.pin-orbit {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 200%;
  aspect-ratio: 1;
  background: conic-gradient(
    from 0deg,
    rgba(var(--pin-live), 0) 0turn,
    rgba(var(--pin-live), 0) 0.55turn,
    rgba(var(--pin-live), 0.36) 0.82turn,
    rgba(var(--pin-live), 0.9) 0.965turn,
    rgba(214, 255, 233, 1) 0.99turn,
    rgba(var(--pin-live), 0) 0.995turn
  );
  transform: translate(-50%, -50%) rotate(0turn);
  animation: pin-orbit 6s linear infinite;
  will-change: transform;
}
@keyframes pin-orbit {
  to { transform: translate(-50%, -50%) rotate(1turn); }
}

/* The glow: a wider copy of the ring, blurred, so light spills off the edge
   where the comet is. */
.pin-glow {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  filter: blur(10px);
  opacity: 0;
  visibility: hidden;
  transition: opacity 700ms cubic-bezier(0.22, 1, 0.36, 1), visibility 0s linear 700ms;
}
.pin-running .pin-glow {
  opacity: 0.9;
  visibility: visible;
  transition: opacity 700ms cubic-bezier(0.22, 1, 0.36, 1);
}
.pin-glow .pin-ring { padding: 3px; opacity: 1; visibility: inherit; transition: none; }

/* Start: the whole border lights and settles. */
.pin-flash {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  border: 1px solid rgba(var(--pin-live), 0.9);
  box-shadow: 0 0 24px rgba(var(--pin-live), 0.28), inset 0 0 24px rgba(var(--pin-live), 0.1);
  opacity: 0;
}
.pin-ignite .pin-flash { animation: pin-flash 1.5s cubic-bezier(0.22, 1, 0.36, 1) both; }
@keyframes pin-flash {
  0%   { opacity: 0; }
  10%  { opacity: 1; }
  100% { opacity: 0; }
}

/* Status dot: two staggered rings ping out of it while it runs. */
.pin-dot { position: relative; display: inline-block; width: 7px; height: 7px; border-radius: 9999px; flex-shrink: 0; }
.pin-dot-live { background: rgb(var(--pin-live)); box-shadow: 0 0 8px rgba(var(--pin-live), 0.55); }
.pin-dot-live::before,
.pin-dot-live::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 9999px;
  background: rgba(var(--pin-live), 0.5);
  animation: pin-ping 2.4s cubic-bezier(0, 0, 0.2, 1) infinite;
}
.pin-dot-live::after { animation-delay: 1.2s; }
@keyframes pin-ping {
  0%        { transform: scale(1);   opacity: 0.6; }
  70%, 100% { transform: scale(3.2); opacity: 0; }
}
.pin-ignite .pin-dot-live { animation: pin-pop 700ms cubic-bezier(0.22, 1, 0.36, 1); }
@keyframes pin-pop {
  0%   { transform: scale(1); }
  35%  { transform: scale(1.9); }
  100% { transform: scale(1); }
}

/* The 1D line: gain or loss by the day so far, a faint same-hue fade under
   it, and its last point marked as "now". */
.pin-spark.pin-up   { color: #4cc38a; }
.pin-spark.pin-down { color: #e5484d; }
.pin-spark:not(.pin-spark-live) { opacity: 0.6; }
.pin-spark-live .pin-spark-svg { animation: pin-draw 1.2s cubic-bezier(0.22, 1, 0.36, 1) both; }
@keyframes pin-draw {
  from { clip-path: inset(-4px 100% -4px 0); }
  to   { clip-path: inset(-4px -4px -4px 0); }
}
.pin-now {
  position: absolute;
  width: 6px;
  height: 6px;
  margin: -3px 0 0 -3px;
  border-radius: 9999px;
  background: currentColor;
  box-shadow: 0 0 0 2px #161617;
}
.pin-spark-live .pin-now { animation: pin-now-in 420ms cubic-bezier(0.22, 1, 0.36, 1) 1s both; }
.pin-spark-live .pin-now::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 9999px;
  background: currentColor;
  animation: pin-ping 2.4s cubic-bezier(0, 0, 0.2, 1) 1.4s infinite;
  opacity: 0;
}
@keyframes pin-now-in {
  from { transform: scale(0); opacity: 0; }
  to   { transform: scale(1); opacity: 1; }
}

.pin-filled { font-variation-settings: 'FILL' 1; }

@media (prefers-reduced-motion: reduce) {
  .pin-card { transition: none; }
  .pin-orbit { animation: none; background: rgba(var(--pin-live), 0.35); }
  .pin-glow { display: none; }
  .pin-ignite .pin-flash,
  .pin-ignite .pin-dot-live { animation: none; }
  .pin-dot-live::before,
  .pin-dot-live::after,
  .pin-spark-live .pin-now::after { animation: none; opacity: 0; }
  .pin-spark-live .pin-spark-svg,
  .pin-spark-live .pin-now { animation: none; }
}

/* Keyboard focus inside the ⋯ menu matches its hover step. */
.menu-focus { @apply focus-visible:outline-none focus-visible:bg-white/[0.04]; }

/* Small field label used inside sub-strategy panels. */
.sub-label { @apply block text-xs text-slate-500 mb-1.5; }
.sub-section { @apply text-sm text-slate-300 mb-3; }

/* Modal status line: busy is neutral, success gain, failure loss. */
.msg { @apply rounded-lg border px-4 py-3 text-sm break-words; }
.msg-busy { @apply border-white/[0.07] bg-raised text-slate-300; }
.msg-ok { @apply border-gain/20 bg-gain/5 text-gain; }
.msg-err { @apply border-loss/20 bg-loss/5 text-loss; }

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
