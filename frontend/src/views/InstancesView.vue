<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import { getToken } from '../utils/auth.js'
import LlmConfigForm from '../components/LlmConfigForm.vue'
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
    const res = await fetch(`${API_BASE}/instances/${encodeURIComponent(addStockId.value)}/stocks`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ symbol: sym }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    await fetchInstances()
    showAddStock.value = false
  } catch (e) {
    addStockMsg.value = e.message || 'Failed to add stock'
  } finally {
    addStockBusy.value = false
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
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10">

      <!-- Header -->
      <div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between mb-6">
        <div>
          <p class="text-primary text-xs font-bold uppercase tracking-widest mb-1">Trading</p>
          <h1 class="text-2xl sm:text-3xl font-bold leading-tight">Instances</h1>
          <p class="text-slate-400 text-sm mt-1 max-w-md">Manage live trading and backtesting instances.</p>
        </div>
        <div class="flex items-center gap-3 w-full sm:w-auto">
          <button
            @click="fetchInstances"
            class="inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border border-border-subtle text-sm text-slate-400 hover:text-slate-200 hover:bg-surface transition-all shrink-0"
            title="Refresh"
          >
            <span class="material-symbols-outlined text-[18px]">refresh</span>
          </button>
          <button
            @click="openCreate"
            class="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary text-background-dark font-semibold text-sm hover:brightness-110 transition-all flex-1 sm:flex-none"
          >
            <span class="material-symbols-outlined text-[18px]">add</span>
            New Instance
          </button>
        </div>
      </div>

      <!-- Filter pills -->
      <div class="flex flex-wrap items-center gap-2 mb-6">
        <button
          v-for="f in [{ label: 'All', value: 'all' }, { label: 'User Created', value: 'user' }, { label: 'AI Created', value: 'ai' }]"
          :key="f.value"
          @click="filter = f.value"
          class="px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all"
          :class="filter === f.value
            ? 'bg-primary/15 text-primary border-primary/40'
            : 'border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-surface'"
        >
          {{ f.label }}
          <span v-if="f.value !== 'all'" class="ml-1 text-[10px] opacity-60">
            ({{ instances.filter(i => (i.created_by || 'user') === f.value).length }})
          </span>
          <span v-else class="ml-1 text-[10px] opacity-60">({{ instances.length }})</span>
        </button>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="flex items-center gap-3 text-slate-400 text-sm">
        <span class="material-symbols-outlined animate-spin text-xl">progress_activity</span>
        Loading instances...
      </div>

      <!-- Error -->
      <div v-else-if="loadError" class="rounded-xl bg-red-500/10 border border-red-500/20 px-5 py-4 text-red-400 text-sm">
        {{ loadError }}
      </div>

      <!-- Empty state (no instances at all) -->
      <div
        v-else-if="instances.length === 0"
        class="rounded-2xl border border-border-subtle bg-surface/30 px-8 py-16 flex flex-col items-center gap-4 text-center"
      >
        <span class="material-symbols-outlined text-5xl text-slate-600">memory</span>
        <p class="text-slate-300 font-semibold">No instances yet</p>
        <p class="text-slate-500 text-sm max-w-xs">Create an instance to run live trading or backtesting strategies.</p>
        <button
          @click="openCreate"
          class="mt-2 px-5 py-2 rounded-lg bg-primary text-background-dark font-semibold text-sm hover:brightness-110 transition-all"
        >
          Create your first instance
        </button>
      </div>

      <!-- Empty state (filter has no results) -->
      <div
        v-else-if="filteredInstances.length === 0"
        class="rounded-2xl border border-border-subtle bg-surface/30 px-8 py-12 flex flex-col items-center gap-3 text-center"
      >
        <span class="material-symbols-outlined text-4xl text-slate-600">filter_alt_off</span>
        <p class="text-slate-400 text-sm">No {{ filter === 'ai' ? 'AI-created' : 'user-created' }} instances.</p>
      </div>

      <!-- Instance cards -->
      <div v-else class="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-5">
        <div
          v-for="inst in filteredInstances"
          :key="inst.id"
          class="glass-card rounded-2xl p-5 sm:p-6 flex flex-col gap-4"
        >
          <!-- Card header -->
          <div class="flex items-start justify-between gap-3 min-w-0">
            <div class="flex items-center gap-3 min-w-0">
              <div class="size-10 rounded-xl bg-surface border border-border-subtle flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-primary text-xl">memory</span>
              </div>
              <div class="min-w-0">
                <p class="font-semibold text-slate-100 leading-tight truncate" :title="inst.name || inst.id">{{ inst.name || inst.id }}</p>
                <p class="text-xs text-slate-500 font-mono mt-0.5 truncate">{{ inst.id }}</p>
              </div>
            </div>
            <!-- Badges -->
            <div class="flex flex-col items-end gap-1.5 shrink-0">
              <!-- Origin badge -->
              <span
                class="text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider"
                :class="(inst.created_by || 'user') === 'ai'
                  ? 'bg-violet-500/15 text-violet-400 border border-violet-500/20'
                  : 'bg-slate-500/10 text-slate-500 border border-slate-700'"
              >
                {{ (inst.created_by || 'user') === 'ai' ? 'AI' : 'User' }}
              </span>
              <!-- Running status (crashed = held open for log capture) -->
              <div
                class="flex items-center gap-1.5 text-xs font-semibold px-2 py-1 rounded-full"
                :class="inst.crashed
                  ? 'bg-red-500/10 text-red-400 border border-red-500/20'
                  : inst.runCommand
                    ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                    : 'bg-slate-500/10 text-slate-500 border border-slate-700'"
              >
                <div class="size-1.5 rounded-full" :class="inst.crashed ? 'bg-red-400' : inst.runCommand ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'"></div>
                {{ inst.crashed ? 'Crashed' : inst.runCommand ? 'Running' : 'Stopped' }}
              </div>
            </div>
          </div>

          <!-- Meta info -->
          <div class="space-y-1.5 text-xs text-slate-500">
            <div v-if="inst.strategy_id" class="flex items-center gap-1.5 min-w-0">
              <span class="text-slate-400 shrink-0">Strategy:</span>
              <span class="relative group/strat inline-flex items-center gap-1 cursor-default min-w-0">
                <span class="text-slate-300 truncate" :title="strategyMap[inst.strategy_id] || inst.strategy_id">{{ strategyMap[inst.strategy_id] || inst.strategy_id }}</span>
                <!-- Hover popover with strategy ID -->
                <span
                  class="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 whitespace-nowrap
                         rounded-md bg-[#1a2030] border border-border-subtle px-2 py-1
                         text-[11px] font-mono text-slate-400 shadow-lg
                         opacity-0 group-hover/strat:opacity-100 pointer-events-none transition-opacity z-10"
                >
                  ID: {{ inst.strategy_id }}
                </span>
              </span>
            </div>
            <div v-else class="flex items-center gap-1.5">
              <span class="text-slate-600 italic">No strategy linked</span>
            </div>
            <div v-if="inst.brokerage_id" class="flex items-center gap-1.5 min-w-0">
              <span class="text-slate-400 shrink-0">Brokerage:</span>
              <span class="text-slate-300 truncate">{{ brokerageMap[inst.brokerage_id] || inst.brokerage_id }}</span>
            </div>
          </div>

          <!-- Stocks list -->
          <div>
            <div class="flex items-center justify-between mb-2">
              <p class="text-xs font-medium text-slate-400 uppercase tracking-wider">
                Stocks
                <span class="text-slate-600 ml-1">({{ (inst.stocks || []).length }})</span>
              </p>
              <button
                @click="openAddStock(inst.id)"
                class="flex items-center gap-0.5 text-[11px] text-slate-500 hover:text-primary transition-colors"
              >
                <span class="material-symbols-outlined text-[14px]">add</span>
                Add
              </button>
            </div>

            <div v-if="(inst.stocks || []).length === 0" class="text-xs text-slate-600 italic">
              No stocks added
            </div>
            <div v-else class="flex flex-wrap gap-1.5">
              <span
                v-for="sym in inst.stocks"
                :key="sym"
                class="flex items-center gap-1 px-2 py-0.5 rounded-md bg-surface border border-border-subtle text-xs font-mono text-slate-300"
              >
                {{ sym }}
                <button
                  @click="removeStock(inst.id, sym)"
                  :disabled="busy[inst.id]"
                  class="text-slate-600 hover:text-red-400 transition-colors disabled:opacity-40"
                  :title="`Remove ${sym}`"
                >
                  <span class="material-symbols-outlined text-[12px]">close</span>
                </button>
              </span>
            </div>
          </div>

          <!-- Actions -->
          <div class="flex flex-wrap items-center gap-2 pt-1 mt-auto">
            <!-- View -->
            <button
              @click="router.push('/instances/' + encodeURIComponent(inst.id))"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-primary hover:bg-primary/10 border border-primary/20 transition-colors"
            >
              <span class="material-symbols-outlined text-[14px]">open_in_new</span>
              View
            </button>
            <!-- Create Backtest -->
            <button
              @click="openCreateBacktest(inst.id)"
              :disabled="busy[inst.id]"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-sky-400 hover:bg-sky-500/10 border border-sky-500/20 transition-colors disabled:opacity-40"
            >
              <span class="material-symbols-outlined text-[14px]">play_circle</span>
              Backtest
            </button>
            <!-- Link Strategy (no strategy linked) -->
            <button
              v-if="!inst.strategy_id"
              @click="openLinkStrategy(inst.id)"
              :disabled="busy[inst.id]"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-violet-400 hover:bg-violet-500/10 border border-violet-500/20 transition-colors disabled:opacity-40"
            >
              <span class="material-symbols-outlined text-[14px]">link</span>
              Link Strategy
            </button>
            <!-- Edit Strategy (strategy linked) -->
            <button
              v-if="inst.strategy_id"
              @click="openEditStrategy(inst.strategy_id)"
              :disabled="busy[inst.id]"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-amber-400 hover:bg-amber-500/10 border border-amber-500/20 transition-colors disabled:opacity-40"
            >
              <span class="material-symbols-outlined text-[14px]">tune</span>
              Strategy
            </button>
            <!-- Edit Brokerage (only when brokerage is linked) -->
            <button
              v-if="inst.brokerage_id"
              @click="openChangeBrokerage(inst)"
              :disabled="busy[inst.id]"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-teal-400 hover:bg-teal-500/10 border border-teal-500/20 transition-colors disabled:opacity-40"
            >
              <span class="material-symbols-outlined text-[14px]">account_balance</span>
              Brokerage
            </button>
            <!-- Start / Stop -->
            <button
              v-if="!inst.runCommand"
              @click="startInstance(inst.id)"
              :disabled="busy[inst.id]"
              class="w-full sm:w-auto inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-emerald-400 hover:bg-emerald-500/10 border border-emerald-500/20 transition-colors disabled:opacity-40"
            >
              <span v-if="busy[inst.id]" class="material-symbols-outlined text-[14px] animate-spin">progress_activity</span>
              <span v-else class="material-symbols-outlined text-[14px]">play_arrow</span>
              Start
            </button>
            <button
              v-else
              @click="stopInstance(inst.id)"
              :disabled="busy[inst.id]"
              class="w-full sm:w-auto inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-amber-400 hover:bg-amber-500/10 border border-amber-500/20 transition-colors disabled:opacity-40"
            >
              <span v-if="busy[inst.id]" class="material-symbols-outlined text-[14px] animate-spin">progress_activity</span>
              <span v-else class="material-symbols-outlined text-[14px]">stop</span>
              Stop
            </button>

            <!-- Delete -->
            <button
              @click="openDelete(inst.id, inst.name)"
              :disabled="busy[inst.id]"
              class="w-full sm:w-auto inline-flex items-center justify-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-500 hover:text-red-400 hover:bg-red-500/5 border border-border-subtle transition-colors sm:ml-auto disabled:opacity-40"
            >
              <span class="material-symbols-outlined text-[14px]">delete</span>
              Delete
            </button>
          </div>
        </div>
      </div>

    </main>
  </AppShell>

  <!-- ── Delete Instance Modal ─────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showDelete"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeDelete"
      >
        <div class="relative w-full max-w-sm bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">

          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle">
            <div class="flex items-center gap-3">
              <div class="size-9 rounded-xl bg-red-500/10 border border-red-500/20 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-red-400 text-lg">delete</span>
              </div>
              <h2 class="text-base font-bold">Delete Instance</h2>
            </div>
            <button @click="closeDelete" :disabled="deleting" class="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>

          <div class="px-6 py-5 space-y-4">
            <!-- Normal state -->
            <div v-if="!deleteForce">
              <p class="text-sm text-slate-300">
                Are you sure you want to delete
                <span class="font-semibold text-slate-100">{{ deleteTarget?.name || deleteTarget?.id }}</span>?
              </p>
              <p class="text-xs text-slate-500 mt-1.5">This action cannot be undone.</p>
            </div>

            <!-- Force state — linked to strategy -->
            <div v-else>
              <p class="text-sm text-slate-300">
                This instance is linked to a strategy and cannot be deleted normally.
              </p>
              <p class="text-xs text-slate-500 mt-1.5">
                Force-deleting will remove the instance without unlinking it from the strategy.
              </p>
            </div>

            <!-- Error message -->
            <Transition name="slide-up">
              <div
                v-if="deleteError"
                class="rounded-lg px-4 py-3 text-xs bg-red-500/10 border border-red-500/20 text-red-400 font-mono break-words"
              >
                {{ deleteError }}
              </div>
            </Transition>
          </div>

          <div class="px-6 pb-6 flex gap-3">
            <button @click="closeDelete" :disabled="deleting"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40">
              Cancel
            </button>
            <!-- Normal delete -->
            <button v-if="!deleteForce" @click="confirmDelete(false)" :disabled="deleting"
              class="flex-1 py-2.5 rounded-lg bg-red-500/80 hover:bg-red-500 text-white text-sm font-bold transition-all disabled:opacity-50 flex items-center justify-center gap-2">
              <span v-if="deleting" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              {{ deleting ? 'Deleting...' : 'Delete' }}
            </button>
            <!-- Force delete -->
            <button v-else @click="confirmDelete(true)" :disabled="deleting"
              class="flex-1 py-2.5 rounded-lg bg-red-600 hover:bg-red-500 text-white text-sm font-bold transition-all disabled:opacity-50 flex items-center justify-center gap-2 border border-red-400/30">
              <span v-if="deleting" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              <span v-else class="material-symbols-outlined text-base">warning</span>
              {{ deleting ? 'Deleting...' : 'Force Delete' }}
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
        <div class="relative w-full max-w-sm bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">
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
                <option v-for="s in strategies" :key="s.id" :value="s.id">{{ s.name || s.id }}</option>
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

  <!-- ── Change Brokerage Modal ─────────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showChangeBrokerage"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeChangeBrokerage"
      >
        <div class="relative w-full max-w-sm bg-[#0f0a1c] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">
          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle">
            <div class="flex items-center gap-3">
              <div class="size-9 rounded-xl bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-primary text-lg">account_balance</span>
              </div>
              <h2 class="text-base font-bold">Change Brokerage</h2>
            </div>
            <button @click="closeChangeBrokerage" :disabled="changeBrokerageBusy" class="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>
          <div class="px-6 py-5 space-y-4">
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Brokerage</label>
              <select
                v-model="changeBrokerageId"
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors"
              >
                <option value="">Unlink brokerage</option>
                <option v-for="b in brokerages" :key="b.id" :value="b.id">{{ b.account_name }} ({{ b.brokerage_type }})</option>
              </select>
            </div>
            <Transition name="slide-up">
              <div
                v-if="changeBrokerageMsg"
                class="rounded-lg px-4 py-3 text-xs font-mono break-words"
                :class="changeBrokerageOk ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400' : 'bg-red-500/10 border border-red-500/20 text-red-400'"
              >
                {{ changeBrokerageMsg }}
              </div>
            </Transition>
          </div>
          <div class="px-6 pb-6 flex gap-3">
            <button @click="closeChangeBrokerage" :disabled="changeBrokerageBusy"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40">
              Cancel
            </button>
            <button @click="submitChangeBrokerage" :disabled="changeBrokerageBusy"
              class="flex-1 py-2.5 rounded-lg bg-primary hover:brightness-110 text-white text-sm font-bold transition-all disabled:opacity-50 flex items-center justify-center gap-2">
              <span v-if="changeBrokerageBusy" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
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
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeCreate"
      >
        <div class="relative w-full max-w-sm bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">

          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle">
            <h2 class="text-base font-bold">New Instance</h2>
            <button @click="closeCreate" class="text-slate-500 hover:text-slate-300 transition-colors">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>

          <div class="px-6 py-5 space-y-4">
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Instance ID <span class="text-red-400">*</span></label>
              <input
                v-model="createForm.id"
                type="text"
                placeholder="e.g. my-live-instance"
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono"
              />
              <p class="text-[11px] text-slate-600 mt-1">Unique identifier for this instance</p>
            </div>
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Display Name <span class="text-slate-600">(optional)</span></label>
              <input
                v-model="createForm.name"
                type="text"
                placeholder="e.g. Momentum Strategy Live"
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors"
              />
            </div>
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Granularity</label>
              <div class="flex gap-1.5 flex-wrap">
                <button
                  v-for="g in GRANULARITIES"
                  :key="g.value"
                  @click="createForm.granularity = g.value"
                  class="px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all"
                  :class="createForm.granularity === g.value
                    ? 'bg-primary/15 text-primary border-primary/40'
                    : 'border-border-subtle text-slate-400 hover:text-slate-200'"
                >
                  {{ g.label }}
                </button>
              </div>
            </div>
            <div class="flex items-center gap-3">
              <button
                type="button"
                @click="createForm.run_command = !createForm.run_command"
                class="relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 transition-colors"
                :class="createForm.run_command ? 'bg-primary border-primary' : 'bg-surface border-border-subtle'"
              >
                <span
                  class="inline-block size-3.5 rounded-full bg-white shadow transition-transform"
                  :class="createForm.run_command ? 'translate-x-4' : 'translate-x-0.5'"
                ></span>
              </button>
              <span class="text-sm text-slate-300">Start immediately after creation</span>
            </div>

            <!-- Brokerage -->
            <div v-if="brokerages.length">
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Brokerage <span class="text-slate-600">(optional)</span></label>
              <select
                v-model="createForm.brokerage_id"
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors"
              >
                <option value="">None</option>
                <option v-for="b in brokerages" :key="b.id" :value="b.id">
                  {{ b.account_name }} ({{ b.brokerage_type }})
                </option>
              </select>
            </div>

            <!-- Max usage -->
            <div v-if="createForm.brokerage_id">
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Max Usage <span class="text-slate-600">($, optional)</span></label>
              <input
                v-model="createForm.max_usage"
                type="number"
                min="0"
                step="100"
                placeholder="e.g. 10000"
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono"
              />
              <p class="text-[11px] text-slate-600 mt-1">Maximum capital this instance can use from the brokerage</p>
            </div>

            <!-- Strategy -->
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Strategy <span class="text-slate-600">(optional)</span></label>
              <div class="flex gap-2">
                <select
                  v-model="createForm.strategy_id"
                  class="flex-1 bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors"
                >
                  <option value="">None</option>
                  <option v-for="s in strategies" :key="s.id" :value="String(s.id)">
                    {{ s.name || s.id }}
                  </option>
                </select>
                <button
                  type="button"
                  @click="openCreateStrategy"
                  class="shrink-0 flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold text-violet-400 hover:bg-violet-500/10 border border-violet-500/20 transition-colors"
                  title="Create new strategy"
                >
                  <span class="material-symbols-outlined text-[14px]">add</span>
                  New
                </button>
              </div>
              <p v-if="createForm.strategy_id" class="text-[11px] text-slate-600 mt-1">
                Strategy will be linked after instance creation
              </p>
            </div>

            <Transition name="slide-up">
              <div
                v-if="createMsg"
                class="rounded-lg px-4 py-3 text-sm font-medium"
                :class="createOk
                  ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                  : 'bg-red-500/10 border border-red-500/20 text-red-400'"
              >
                <span v-if="creating" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-base animate-spin">progress_activity</span>
                  {{ createMsg }}
                </span>
                <span v-else>{{ createMsg }}</span>
              </div>
            </Transition>
          </div>

          <div class="px-6 pb-6 flex gap-3">
            <button @click="closeCreate" :disabled="creating"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40">
              Cancel
            </button>
            <button @click="submitCreate" :disabled="creating"
              class="flex-1 py-2.5 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 transition-all disabled:opacity-50 flex items-center justify-center gap-2">
              <span v-if="creating" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
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
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeEditStrategy"
      >
        <div class="relative w-full max-w-2xl bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl flex flex-col max-h-[90vh]">

          <!-- Header -->
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

          <!-- Body -->
          <div class="flex-1 overflow-y-auto px-6 py-5 space-y-5">

            <!-- Loading -->
            <div v-if="editStrategyLoading" class="flex items-center gap-3 text-slate-400 text-sm py-8 justify-center">
              <span class="material-symbols-outlined animate-spin text-xl">progress_activity</span>
              Loading strategy...
            </div>

            <template v-else-if="editStrategyData">
              <!-- Strategy name -->
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1.5">Strategy Name</label>
                <input
                  v-model="editStrategyData.name"
                  type="text"
                  class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors"
                />
              </div>

              <!-- Sub-strategies -->
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
                    <!-- Sub-strategy header -->
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
                      <button
                        @click="removeSubStrategy(idx)"
                        class="shrink-0 text-slate-600 hover:text-red-400 transition-colors"
                        title="Remove sub-strategy"
                      >
                        <span class="material-symbols-outlined text-[18px]">delete</span>
                      </button>
                    </div>

                    <!-- Core fields -->
                    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
                      <!-- Weight -->
                      <div>
                        <label class="block text-[11px] font-medium text-slate-500 mb-1">Weight</label>
                        <input
                          v-model.number="sub.weight"
                          type="number" min="0" max="1" step="0.05"
                          class="w-full bg-[#0f1318] border border-border-subtle rounded-lg px-2.5 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary transition-colors font-mono"
                        />
                      </div>
                      <!-- Execution Position -->
                      <div>
                        <label class="block text-[11px] font-medium text-slate-500 mb-1">Exec Position</label>
                        <input
                          v-model.number="sub.execution_position"
                          type="number" step="1"
                          class="w-full bg-[#0f1318] border border-border-subtle rounded-lg px-2.5 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary transition-colors font-mono"
                        />
                      </div>
                      <!-- Decision Phase -->
                      <div>
                        <label class="block text-[11px] font-medium text-slate-500 mb-1">Phase</label>
                        <div class="flex gap-1">
                          <button
                            v-for="p in ['pre', 'post']" :key="p"
                            @click="sub.decision_phase = p"
                            class="flex-1 py-1.5 rounded-md text-[11px] font-semibold border transition-all"
                            :class="sub.decision_phase === p
                              ? 'bg-primary/15 text-primary border-primary/40'
                              : 'border-border-subtle text-slate-500 hover:text-slate-300'"
                          >{{ p }}</button>
                        </div>
                      </div>
                      <!-- Execution Scope -->
                      <div>
                        <label class="block text-[11px] font-medium text-slate-500 mb-1">Scope</label>
                        <div class="flex gap-1">
                          <button
                            v-for="s in ['per_symbol', 'run_once']" :key="s"
                            @click="sub.execution_scope = s"
                            class="flex-1 py-1.5 rounded-md text-[10px] font-semibold border transition-all"
                            :class="sub.execution_scope === s
                              ? 'bg-primary/15 text-primary border-primary/40'
                              : 'border-border-subtle text-slate-500 hover:text-slate-300'"
                          >{{ s === 'per_symbol' ? 'per sym' : 'once' }}</button>
                        </div>
                      </div>
                    </div>

                    <!-- LLM -->
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

                    <!-- Config fields -->
                    <div v-if="configEntries(sub).length">
                      <p class="text-[11px] font-medium text-slate-500 uppercase tracking-wider mb-2">Config</p>
                      <div class="grid grid-cols-2 sm:grid-cols-3 gap-3">
                      <div v-for="[key, val] in configEntries(sub)" :key="key">
                        <label class="block text-[11px] font-medium text-slate-500 mb-1">{{ getStrategyConfigFieldMeta(sub.strategy, key).label }}</label>
                        <!-- Boolean toggle -->
                        <template v-if="getConfigType(getRefConfig(sub.strategy)[key] ?? val) === 'boolean'">
                          <button
                            type="button"
                            @click="sub.config[key] = !sub.config[key]"
                              class="relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 transition-colors"
                              :class="sub.config[key] ? 'bg-primary border-primary' : 'bg-[#0f1318] border-border-subtle'"
                            >
                              <span
                                class="inline-block size-3.5 rounded-full bg-white shadow transition-transform"
                                :class="sub.config[key] ? 'translate-x-4' : 'translate-x-0.5'"
                              ></span>
                            </button>
                          </template>
                          <!-- Number input -->
                          <template v-else-if="getConfigType(getRefConfig(sub.strategy)[key] ?? val) === 'number'">
                            <input
                              :value="sub.config[key]"
                              @input="updateConfigField(sub, key, $event.target.value, 'number')"
                              type="number"
                              class="w-full bg-[#0f1318] border border-border-subtle rounded-lg px-2.5 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary transition-colors font-mono"
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
                              class="w-full bg-[#0f1318] border border-border-subtle rounded-lg px-2.5 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary transition-colors font-mono resize-y"
                              :class="getConfigFieldError(sub, key) ? 'border-red-500/60 focus:border-red-400' : ''"
                            ></textarea>
                          </template>
                          <template v-else>
                            <input
                              v-model="sub.config[key]"
                              type="text"
                              class="w-full bg-[#0f1318] border border-border-subtle rounded-lg px-2.5 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary transition-colors font-mono"
                            />
                          </template>
                          <p v-if="getConfigFieldError(sub, key)" class="mt-1 text-[10px] leading-relaxed text-red-400">
                            Invalid JSON: {{ getConfigFieldError(sub, key) }}
                          </p>
                        <p v-if="getStrategyConfigFieldMeta(sub.strategy, key).description" class="mt-1 text-[10px] leading-relaxed text-slate-600">
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
            <div v-else-if="editStrategyMsg && !editStrategyOk" class="rounded-lg px-4 py-3 text-sm bg-red-500/10 border border-red-500/20 text-red-400">
              {{ editStrategyMsg }}
            </div>

            <!-- Status message (when data is present) -->
            <Transition name="slide-up">
              <div
                v-if="editStrategyMsg && editStrategyData"
                class="rounded-lg px-4 py-3 text-sm font-medium"
                :class="editStrategyOk
                  ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                  : 'bg-red-500/10 border border-red-500/20 text-red-400'"
              >
                {{ editStrategyMsg }}
              </div>
            </Transition>
          </div>

          <!-- Footer -->
          <div class="px-6 pb-6 pt-4 border-t border-border-subtle flex gap-3 shrink-0">
            <button @click="closeEditStrategy" :disabled="editStrategySaving"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40">
              Cancel
            </button>
            <button
              v-if="editStrategyData && !editStrategyLoading"
              @click="submitEditStrategy" :disabled="editStrategySaving"
              class="flex-1 py-2.5 rounded-lg bg-amber-500 text-black text-sm font-bold hover:bg-amber-400 transition-all disabled:opacity-50 flex items-center justify-center gap-2">
              <span v-if="editStrategySaving" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              {{ editStrategySaving ? 'Saving...' : 'Save Changes' }}
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
                <h2 class="text-base font-bold">Create Backtest</h2>
                <p class="text-[11px] text-slate-500 font-mono mt-0.5">{{ createBtForm.instance_id }}</p>
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
              <div class="flex gap-1.5 flex-wrap">
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

  <!-- ── Create Strategy Modal (nested from Create Instance) ─────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showCreateStrategy"
        class="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
      >
        <div class="relative w-full max-w-2xl bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl flex flex-col max-h-[90vh]">

          <!-- Header -->
          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle shrink-0">
            <div class="flex items-center gap-3 min-w-0">
              <div class="size-9 rounded-xl bg-violet-500/10 border border-violet-500/20 flex items-center justify-center shrink-0">
                <span class="material-symbols-outlined text-violet-400 text-lg">add_circle</span>
              </div>
              <h2 class="text-base font-bold">New Strategy</h2>
            </div>
            <button @click="closeCreateStrategy" :disabled="creatingStrategy" class="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40 ml-4 shrink-0">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>

          <!-- Body -->
          <div class="flex-1 overflow-y-auto px-6 py-5 space-y-5">

            <!-- Name -->
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Strategy Name <span class="text-red-400">*</span></label>
              <input
                v-model="newStrategyForm.name"
                type="text"
                placeholder="e.g. My Momentum Strategy"
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors"
              />
            </div>

            <!-- Add sub-strategy -->
            <div v-if="availableStrategyOptions.length">
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Add Sub-strategy</label>
              <div class="flex gap-2">
                <select
                  v-model="newStrategyPickName"
                  class="flex-1 bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors"
                >
                  <option v-for="s in availableStrategyOptions" :key="s.value" :value="s.value">{{ s.label }} · {{ s.value }}</option>
                </select>
                <button
                  type="button"
                  @click="addNewSubStrategy"
                  class="shrink-0 flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold text-primary hover:bg-primary/10 border border-primary/20 transition-colors"
                >
                  <span class="material-symbols-outlined text-[14px]">add</span>
                  Add
                </button>
              </div>
            </div>

            <!-- Sub-strategy cards -->
            <div>
              <p class="text-xs font-medium text-slate-400 uppercase tracking-wider mb-3">
                Sub-strategies
                <span class="text-slate-600 ml-1">({{ newStrategyForm.strategies.length }})</span>
              </p>
              <div v-if="!newStrategyForm.strategies.length" class="text-xs text-slate-600 italic">
                No sub-strategies added yet. Use the selector above to add one.
              </div>
              <div class="space-y-4">
                <div
                  v-for="(sub, idx) in newStrategyForm.strategies"
                  :key="idx"
                  class="rounded-xl border border-border-subtle bg-surface/40 p-4 space-y-4"
                >
                  <!-- Header -->
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
                    <button
                      @click="removeNewSubStrategy(idx)"
                      class="shrink-0 text-slate-600 hover:text-red-400 transition-colors"
                      title="Remove"
                    >
                      <span class="material-symbols-outlined text-[18px]">delete</span>
                    </button>
                  </div>

                  <!-- Core fields -->
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
                          :class="sub.decision_phase === p ? 'bg-primary/15 text-primary border-primary/40' : 'border-border-subtle text-slate-500 hover:text-slate-300'">
                          {{ p }}
                        </button>
                      </div>
                    </div>
                    <div>
                      <label class="block text-[11px] font-medium text-slate-500 mb-1">Scope</label>
                      <div class="flex gap-1">
                        <button v-for="s in ['per_symbol', 'run_once']" :key="s" @click="sub.execution_scope = s"
                          class="flex-1 py-1.5 rounded-md text-[10px] font-semibold border transition-all"
                          :class="sub.execution_scope === s ? 'bg-primary/15 text-primary border-primary/40' : 'border-border-subtle text-slate-500 hover:text-slate-300'">
                          {{ s === 'per_symbol' ? 'per sym' : 'once' }}
                        </button>
                      </div>
                    </div>
                  </div>

                  <!-- LLM -->
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

                  <!-- Config -->
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
                          <input :value="sub.config[key]" @input="updateConfigField(sub, key, $event.target.value, 'number')"
                            type="number" class="w-full bg-[#0f1318] border border-border-subtle rounded-lg px-2.5 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-primary transition-colors font-mono" />
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
                  </div>

                </div>
              </div>
            </div>

            <!-- Status -->
            <Transition name="slide-up">
              <div
                v-if="createStrategyMsg"
                class="rounded-lg px-4 py-3 text-sm font-medium"
                :class="createStrategyOk
                  ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                  : 'bg-red-500/10 border border-red-500/20 text-red-400'"
              >
                <span v-if="creatingStrategy" class="inline-flex items-center gap-2">
                  <span class="material-symbols-outlined text-base animate-spin">progress_activity</span>
                  {{ createStrategyMsg }}
                </span>
                <span v-else>{{ createStrategyMsg }}</span>
              </div>
            </Transition>
          </div>

          <!-- Footer -->
          <div class="px-6 pb-6 pt-4 border-t border-border-subtle flex gap-3 shrink-0">
            <button @click="closeCreateStrategy" :disabled="creatingStrategy"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40">
              Cancel
            </button>
            <button @click="submitCreateStrategy" :disabled="creatingStrategy"
              class="flex-1 py-2.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-bold transition-all disabled:opacity-50 flex items-center justify-center gap-2">
              <span v-if="creatingStrategy" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              {{ creatingStrategy ? 'Creating...' : 'Create Strategy' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <!-- ── Add Stock Modal ───────────────────────────────────────────────────── -->
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

            <!-- Show selected model details (read-only) -->
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

  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showAddStock"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        @click.self="closeAddStock"
      >
        <div class="relative w-full max-w-xs bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl overflow-hidden">
          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle">
            <h2 class="text-base font-bold">Add Stock</h2>
            <button @click="closeAddStock" class="text-slate-500 hover:text-slate-300 transition-colors">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>
          <div class="px-6 py-5 space-y-4">
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Ticker Symbol</label>
              <input
                v-model="addStockSym"
                type="text"
                placeholder="e.g. AAPL"
                @keydown.enter="submitAddStock"
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono uppercase"
              />
            </div>
            <div v-if="addStockMsg" class="rounded-lg px-4 py-3 text-sm bg-red-500/10 border border-red-500/20 text-red-400">
              {{ addStockMsg }}
            </div>
          </div>
          <div class="px-6 pb-6 flex gap-3">
            <button @click="closeAddStock" :disabled="addStockBusy"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-40">
              Cancel
            </button>
            <button @click="submitAddStock" :disabled="addStockBusy"
              class="flex-1 py-2.5 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 transition-all disabled:opacity-50 flex items-center justify-center gap-2">
              <span v-if="addStockBusy" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              {{ addStockBusy ? 'Adding...' : 'Add Stock' }}
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
