<script setup>
import { ref, onMounted } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import LlmConfigForm from '../components/LlmConfigForm.vue'
import AnalysisSettingsCard from '../components/analysis/AnalysisSettingsCard.vue'
import { getToken } from '../utils/auth.js'
import {
  buildStrategyLlmTestPayload,
  getLlmProviderLabel,
} from '../utils/strategyConfig.js'

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

// ── State ─────────────────────────────────────────────────────────────────────
const models    = ref([])
const loading   = ref(true)
const loadError = ref('')
// Bumped on every list refresh so the AI Analysis model dropdown follows adds and deletes.
const modelsVersion = ref(0)

// Modal
const showModal  = ref(false)
const editMode   = ref(false)
const editId     = ref(null)
const submitting = ref(false)
const submitMsg  = ref('')
const submitOk   = ref(false)
// True only after submitModel actually saves a row. Test only success
// flips submitOk but NOT saved — so the Test only / Test & Save buttons
// stay visible and the operator can re-test or save without reopening
// the modal.
const saved      = ref(false)
// Full LLM test response so operators can verify the test is real
// (not a canned 200 OK from an upstream proxy). Populated by submitModel
// after /llm/test succeeds; cleared by closeModal / openModal reset paths.
const testResult = ref(null)

function _prettyJson(value) {
  if (value == null) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch (e) {
    return String(value)
  }
}

// Render the "Reasoning" cell in the Models table. For Ollama rows we
// show the ollama_think value (Off/On/Low/Medium/High) because that's
// what controls reasoning on Ollama models — reasoning_effort is the
// Azure/OpenAI/NVIDIA field and is not used for Ollama.
function _reasoningCell(m) {
  if (m.provider === 'claude-cli' || m.provider === 'codex-cli') return '—'
  if (m.provider === 'ollama') {
    const think = String(m.ollama_think || '').trim().toLowerCase()
    if (!think) return 'Default'
    if (think === 'true' || think === 'on') return 'On'
    if (think === 'false' || think === 'off') return 'Off'
    return think.charAt(0).toUpperCase() + think.slice(1)
  }
  if (m.provider === 'bedrock') {
    const r = String(m.bedrock_reasoning || '').trim().toLowerCase()
    if (!r || r === 'off') return 'Off'
    return r.charAt(0).toUpperCase() + r.slice(1)
  }
  const eff = String(m.reasoning_effort || '').trim()
  if (!eff) return 'Default'
  return eff.charAt(0).toUpperCase() + eff.slice(1)
}

const formDraft = ref({
  name: '',
  provider: 'gemini',
  model: '',
  apiKey: '',
  openaiBaseUrl: '',
  nvidiaBaseUrl: '',
  azureEndpoint: '',
  azureApiVersion: '2024-10-21',
  reasoningEffort: '',
  cliPath: '',
  extraArgs: '',
  // Ollama provider config — only used when provider === 'ollama'.
  ollamaBaseUrl: 'http://localhost:11434',
  ollamaKeepAlive: '',
  ollamaThink: '',
  // Bedrock provider config — only used when provider === 'bedrock'.
  bedrockRegion: 'us-east-1',
  bedrockReasoning: 'off',
  // OpenRouter provider config — only used when provider === 'openrouter'.
  openrouterBaseUrl: 'https://openrouter.ai/api/v1',
  openrouterReferer: '',
  openrouterTitle: '',
  // Cache-grouping tag — share LLM cache across same-model-different-name rows.
  modelCacheFamily: '',
  // Optional per-model pricing override ($/1M tokens). null = use
  // backend llm_pricing.yaml defaults.
  inputCostPer1m: null,
  outputCostPer1m: null,
  cacheCreationCostPer1m: null,
  cacheReadCostPer1m: null,
})

const testCliStates = ref({})

// ── Helpers ───────────────────────────────────────────────────────────────────
function _normalizeError(data, status) {
  const d = data?.detail
  if (Array.isArray(d)) return d.map(x => (x && (x.msg || x.message)) || JSON.stringify(x)).join('; ')
  if (typeof d === 'string') return d
  if (d && typeof d === 'object') return d.msg || d.message || JSON.stringify(d)
  if (data?.error) return data.error
  return `HTTP ${status}`
}

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

// ── CRUD ──────────────────────────────────────────────────────────────────────
async function fetchModels() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await fetch(`${API_BASE}/models`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    models.value = data.models || []
    modelsVersion.value += 1
  } catch (e) {
    loadError.value = `Failed to load models: ${e.message}`
  } finally {
    loading.value = false
  }
}

function openCreateModal() {
  editMode.value = false
  editId.value = null
  formDraft.value = {
    name: '',
    provider: 'gemini',
    model: '',
    apiKey: '',
    openaiBaseUrl: '',
    nvidiaBaseUrl: '',
    azureEndpoint: '',
    azureApiVersion: '2024-10-21',
    reasoningEffort: '',
    cliPath: '',
    extraArgs: '',
    modelCacheFamily: '',
    inputCostPer1m: null,
    outputCostPer1m: null,
    cacheCreationCostPer1m: null,
    cacheReadCostPer1m: null,
  }
  submitting.value = false
  submitMsg.value = ''
  submitOk.value = false
  saved.value = false
  testResult.value = null
  showModal.value = true
}

function openEditModal(m) {
  editMode.value = true
  editId.value = m.id
  formDraft.value = {
    name: m.name || '',
    provider: m.provider || 'gemini',
    model: m.model || '',
    apiKey: '', // don't prefill masked key
    openaiBaseUrl: m.openai_base_url || '',
    nvidiaBaseUrl: m.nvidia_base_url || '',
    azureEndpoint: m.azure_openai_endpoint || '',
    azureApiVersion: m.azure_openai_api_version || '2024-10-21',
    reasoningEffort: m.reasoning_effort || '',
    cliPath: m.cli_path || '',
    extraArgs: m.extra_args || '',
    // Ollama: hydrate base_url + keep_alive + think; default base_url when
    // blank so editing a stored ollama row never lands on an empty picker.
    ollamaBaseUrl: m.ollama_base_url || 'http://localhost:11434',
    ollamaKeepAlive: m.ollama_keep_alive || '',
    ollamaThink: m.ollama_think || '',
    // Bedrock: hydrate region + reasoning; default region when blank.
    bedrockRegion: m.bedrock_region || 'us-east-1',
    bedrockReasoning: m.bedrock_reasoning || 'off',
    // OpenRouter: hydrate base_url + attribution headers; default base when blank.
    openrouterBaseUrl: m.openrouter_base_url || 'https://openrouter.ai/api/v1',
    openrouterReferer: m.openrouter_referer || '',
    openrouterTitle: m.openrouter_title || '',
    modelCacheFamily: m.model_cache_family || '',
    // Pricing override: row stores snake_case; null/undefined → blank input.
    inputCostPer1m: (m.input_cost_per_1m ?? null),
    outputCostPer1m: (m.output_cost_per_1m ?? null),
    cacheCreationCostPer1m: (m.cache_creation_cost_per_1m ?? null),
    cacheReadCostPer1m: (m.cache_read_cost_per_1m ?? null),
  }
  submitting.value = false
  submitMsg.value = ''
  submitOk.value = false
  saved.value = false
  testResult.value = null
  showModal.value = true
}

function closeModal(force = false) {
  if (submitting.value && !force) return
  showModal.value = false
  editId.value = null
  submitMsg.value = ''
  submitOk.value = false
  saved.value = false
  testResult.value = null
}

// Run the /llm/test smoke against the current draft. Used by both
// ``submitModel`` (Test & Save) and ``testOnly`` (the dry-run button).
// Returns true on success, false on failure — caller sets submitting +
// any follow-up state. Sets ``testResult`` directly so the response
// panel below the form populates immediately.
async function _runLlmTest(d) {
  // When editing, pass the row id so the backend reuses the SAVED key if the
  // key field was left blank (the user shouldn't have to re-enter it to test).
  const testPayload = {
    ...buildStrategyLlmTestPayload(d),
    model_id: editMode.value ? editId.value : undefined,
  }
  const testRes = await fetch(`${API_BASE}/llm/test`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(testPayload),
  })
  const testBody = await testRes.json().catch(() => ({}))
  if (!testRes.ok) throw new Error(_normalizeError(testBody, testRes.status))
  // Capture the actual LLM response so the user can verify the test
  // is real (e.g. a proxy returning canned 200s would fail the
  // structured-parse step or surface a suspicious payload). Backend
  // also runs a real free-form generation smoke (smoke_* fields) using
  // the same call_llm_by_provider path the strategy uses.
  testResult.value = {
    provider: testBody.provider,
    model: testBody.model,
    effective_model: testBody.effective_model,
    result: testBody.result,
    latency_ms: testBody.latency_ms,
    provider_meta: testBody.provider_meta,
    smoke_prompt: testBody.smoke_prompt,
    smoke_response: testBody.smoke_response,
    smoke_thinking: testBody.smoke_thinking,
    smoke_content_chars: testBody.smoke_content_chars,
    smoke_thinking_chars: testBody.smoke_thinking_chars,
    smoke_latency_ms: testBody.smoke_latency_ms,
    smoke_error: testBody.smoke_error,
    message: testBody.message,
  }
}

async function submitModel() {
  const d = formDraft.value
  if (!d.name.trim()) { submitMsg.value = 'Name is required'; submitOk.value = false; return }
  if (!d.model.trim()) { submitMsg.value = 'Model is required'; submitOk.value = false; return }

  submitting.value = true
  submitOk.value = false
  testResult.value = null
  // claude-cli is not run through the /llm/test endpoint here — instead
  // it gets the REAL /models/{id}/test-cli probe AFTER the row is saved
  // (see the claude-cli branch below), so we never claim "LLM test
  // passed" without an honest connection check. codex-cli DOES go
  // through /llm/test — the codex provider's structured + smoke calls
  // verify both that the local binary is authenticated AND that the
  // operator-configured model name actually resolves on OpenAI's side
  // (catches misconfigurations like ``gpt-5.4-mini`` which is an Azure
  // deployment name, not a codex model).
  const skipTest = d.provider === 'claude-cli'
  submitMsg.value = skipTest ? (editMode.value ? 'Updating model...' : 'Saving model...') : 'Testing LLM configuration...'

  // For non-CLI providers, also skip the test on edit when no api_key
  // is in the draft (user didn't intend to re-test creds they didn't
  // change). For codex-cli, run the test unconditionally — there is
  // no api_key gate. For ollama, run the test unconditionally too
  // (local Ollama legitimately has no api_key and we still want
  // "Test & Save" to actually probe the host).
  const hasKey = !!d.apiKey.trim()
  // On edit we can test even with a blank key field — the backend falls back to
  // the saved key via model_id (see _runLlmTest). So always test when editing.
  const shouldTest = !skipTest && (
    d.provider === 'codex-cli' || d.provider === 'ollama' || hasKey || editMode.value
  )
  if (shouldTest) {
    try {
      await _runLlmTest(d)
    } catch (e) {
      submitOk.value = false
      submitMsg.value = `LLM test failed: ${e.message}`
      submitting.value = false
      return
    }
  }

  // isCli is still used below for the save payload shape (cli_path /
  // extra_args go in for both claude-cli and codex-cli).
  const isCli = d.provider === 'claude-cli' || d.provider === 'codex-cli'

  // Create or update
  submitMsg.value = editMode.value ? 'Updating model...' : 'Saving model...'
  try {
    const payload = {
      name: d.name.trim(),
      provider: d.provider,
      model: d.model.trim(),
    }
    // Reasoning effort applies to every provider whose form exposes the
    // dropdown (openai/azure/nvidia + claude-cli/codex-cli where it
    // maps to a CLI flag). Send for both branches so saving 'High' on
    // a CLI model actually persists — the previous CLI branch dropped
    // it silently, so the row kept the prior value.
    payload.reasoning_effort = (d.reasoningEffort || '').trim() || undefined
    if (isCli) {
      payload.cli_path = d.cliPath.trim() || undefined
      payload.extra_args = d.extraArgs.trim() || undefined
    } else {
      payload.openai_base_url = d.openaiBaseUrl.trim() || undefined
      payload.nvidia_base_url = d.nvidiaBaseUrl.trim() || undefined
      payload.azure_openai_endpoint = d.azureEndpoint.trim() || undefined
      payload.azure_openai_api_version = d.azureApiVersion.trim() || undefined
      // Clear any leftover CLI fields when switching to a non-CLI provider
      payload.cli_path = undefined
      payload.extra_args = undefined
      // Only include api_key if it was provided (don't overwrite with empty on edit)
      if (d.apiKey.trim()) {
        payload.api_key = d.apiKey.trim()
      }
    }
    // Ollama fields — always sent when ollama provider is selected so a
    // fresh row gets the base_url stored even if it equals the default.
    if (d.provider === 'ollama') {
      payload.ollama_base_url = (d.ollamaBaseUrl || '').trim() || 'http://localhost:11434'
      payload.ollama_keep_alive = (d.ollamaKeepAlive || '').trim() || undefined
      payload.ollama_think = (d.ollamaThink || '').trim() || undefined
    }
    // Bedrock fields — region is required; reasoning defaults to off.
    if (d.provider === 'bedrock') {
      payload.bedrock_region = (d.bedrockRegion || '').trim() || undefined
      payload.bedrock_reasoning = (d.bedrockReasoning || '').trim().toLowerCase() || undefined
    }
    // OpenRouter fields — base_url always sent (defaults to public API);
    // referer/title are optional attribution headers.
    if (d.provider === 'openrouter') {
      payload.openrouter_base_url = (d.openrouterBaseUrl || '').trim() || 'https://openrouter.ai/api/v1'
      payload.openrouter_referer = (d.openrouterReferer || '').trim() || undefined
      payload.openrouter_title = (d.openrouterTitle || '').trim() || undefined
    }
    // Cache-grouping tag applies to every provider.
    payload.model_cache_family = (d.modelCacheFamily || '').trim().toLowerCase() || undefined

    // Pricing override ($/1M tokens). Convert camelCase → snake_case and
    // send only when a finite number was entered (empty/null = "no
    // override; fall back to llm_pricing.yaml" on the backend).
    const _costOrUndef = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : undefined)
    payload.input_cost_per_1m = _costOrUndef(d.inputCostPer1m)
    payload.output_cost_per_1m = _costOrUndef(d.outputCostPer1m)
    payload.cache_creation_cost_per_1m = _costOrUndef(d.cacheCreationCostPer1m)
    payload.cache_read_cost_per_1m = _costOrUndef(d.cacheReadCostPer1m)

    const url = editMode.value ? `${API_BASE}/models/${editId.value}` : `${API_BASE}/models`
    const method = editMode.value ? 'PUT' : 'POST'
    const res = await fetch(url, {
      method,
      headers: authHeaders(),
      body: JSON.stringify(payload),
    })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(_normalizeError(body, res.status))

    // The row is saved at this point — claude-cli's honesty check runs
    // AFTER and never un-saves it.
    saved.value = true

    if (d.provider === 'claude-cli') {
      // claude-cli never went through /llm/test (skipTest above), so we
      // must NOT claim "LLM test passed". Instead run the REAL connection
      // test (POST /models/{id}/test-cli — the same probe the per-row
      // cable icon uses) against the just-saved row and surface its
      // actual result. The save already succeeded; the test result only
      // affects the message, never whether the model persists. For a new
      // model the id comes back in the create response (body.model.id).
      const savedId = editMode.value ? editId.value : body?.model?.id
      const savedLabel = editMode.value ? 'Model updated.' : 'Model saved.'
      submitOk.value = true
      submitMsg.value = `${savedLabel} Testing Claude CLI connection...`
      submitting.value = false
      if (!savedId) {
        // No id to test against — be honest that we couldn't probe.
        submitMsg.value = `${savedLabel} Could not run the Claude CLI test (missing model id) — use the cable icon on the row to test.`
        await fetchModels()
        return
      }
      try {
        const testRes = await fetch(`${API_BASE}/models/${savedId}/test-cli`, {
          method: 'POST',
          headers: authHeaders(),
        })
        const testBody = await testRes.json().catch(() => ({}))
        if (!testRes.ok) throw new Error(_normalizeError(testBody, testRes.status))
        if (testBody.ok) {
          const loggedIn = testBody.logged_in ? 'logged in' : 'not logged in'
          submitOk.value = true
          submitMsg.value = `${savedLabel} Claude CLI connection OK (${loggedIn}).`
        } else {
          submitOk.value = false
          submitMsg.value = `${savedLabel} But the Claude CLI test failed: ${testBody.error || 'unknown error'}`
        }
      } catch (e) {
        submitOk.value = false
        submitMsg.value = `${savedLabel} But the Claude CLI test failed: ${e.message || e}`
      }
      await fetchModels()
      return
    }

    submitOk.value = true
    submitMsg.value = editMode.value
      ? 'LLM test passed. Model updated.'
      : 'LLM test passed. Model saved.'
    submitting.value = false
    // No auto-close — the user explicitly asked to inspect the LLM
    // test output before dismissing the modal. They close it via the
    // Close button (Cancel re-labels itself after success). Refresh
    // the list in the background so it's ready when they do close.
    await fetchModels()
  } catch (e) {
    submitOk.value = false
    submitMsg.value = e.message || 'Failed to save model.'
    submitting.value = false
  }
}

async function testOnly() {
  // Dry-run: hit /llm/test against the current draft WITHOUT saving.
  // Useful for verifying a config — auth, model name resolution, smoke
  // generation — before committing the change to the Models table.
  const d = formDraft.value
  if (!d.model.trim()) { submitMsg.value = 'Model is required'; submitOk.value = false; return }
  if (d.provider === 'claude-cli') {
    submitMsg.value = 'Use the cable icon on the saved row to test a claude-cli model.'
    submitOk.value = false
    return
  }
  submitting.value = true
  submitOk.value = false
  testResult.value = null
  submitMsg.value = 'Testing LLM configuration...'
  try {
    await _runLlmTest(d)
    submitMsg.value = 'LLM test passed (not saved).'
    submitOk.value = true
  } catch (e) {
    submitMsg.value = `LLM test failed: ${e.message}`
    submitOk.value = false
  } finally {
    submitting.value = false
  }
}

async function testCliConnection(id) {
  testCliStates.value = { ...testCliStates.value, [id]: { loading: true, ok: null, message: 'Testing...' } }
  try {
    const res = await fetch(`${API_BASE}/models/${id}/test-cli`, {
      method: 'POST',
      headers: authHeaders(),
    })
    const data = await res.json().catch(() => ({}))
    if (res.status === 404) {
      testCliStates.value = {
        ...testCliStates.value,
        [id]: { loading: false, ok: false, message: 'Model no longer exists. Refreshed.' },
      }
      await fetchModels()
      return
    }
    if (!res.ok) throw new Error(_normalizeError(data, res.status))
    if (data.ok) {
      const loggedIn = data.logged_in ? 'logged in' : 'not logged in'
      const responseSegment = data.model_response ? `, response: ${data.model_response}` : ''
      testCliStates.value = {
        ...testCliStates.value,
        [id]: {
          loading: false,
          ok: true,
          message: `Connected · v${data.version || '?'}, ${loggedIn}${responseSegment}`,
        },
      }
    } else {
      testCliStates.value = {
        ...testCliStates.value,
        [id]: { loading: false, ok: false, message: data.error || 'Unknown error' },
      }
    }
  } catch (e) {
    testCliStates.value = {
      ...testCliStates.value,
      [id]: { loading: false, ok: false, message: e.message || 'Test failed' },
    }
  }
}

async function deleteModel(id, name) {
  if (!confirm(`Delete "${name}"? If strategies reference this model, they will revert to inline credentials.`)) return
  try {
    const res = await fetch(`${API_BASE}/models/${id}?force=true`, {
      method: 'DELETE',
      headers: authHeaders(),
    })
    if (!res.ok) {
      const d = await res.json().catch(() => ({}))
      throw new Error(d.detail || `HTTP ${res.status}`)
    }
    await fetchModels()
  } catch (e) {
    alert(`Failed to delete: ${e.message}`)
  }
}

onMounted(fetchModels)
</script>

<template>
  <AppShell>
    <main class="ui-page">
      <!-- Header -->
      <header class="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <div class="min-w-0">
          <h1 class="ui-page-title">Models</h1>
          <p class="ui-page-sub">Saved LLM configurations. Strategies reference these instead of storing credentials inline</p>
        </div>
        <button @click="openCreateModal" class="ui-btn-primary">Add model</button>
      </header>

      <section class="mt-8">
        <!-- Loading -->
        <div v-if="loading" class="ui-card overflow-hidden" aria-label="Loading models">
          <div class="h-10 border-b border-white/[0.06]"></div>
          <div class="ui-rows">
            <div v-for="n in 4" :key="n" class="h-14 px-5 flex items-center gap-8">
              <div class="ui-skel h-4 w-36"></div>
              <div class="ui-skel h-4 w-24"></div>
              <div class="ui-skel h-4 w-40"></div>
            </div>
          </div>
        </div>

        <!-- Error -->
        <div v-else-if="loadError" class="ui-error">{{ loadError }}</div>

        <!-- Empty State -->
        <div v-else-if="models.length === 0" class="ui-card">
          <div class="ui-empty">
            <p>No models saved yet. Add one to share an LLM configuration across strategies.</p>
          </div>
        </div>

        <!-- Models Table -->
        <div v-else class="ui-card overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs text-slate-500 border-b border-white/[0.06]">
                <th class="h-10 px-5 font-normal">Name</th>
                <th class="h-10 px-5 font-normal">Provider</th>
                <th class="h-10 px-5 font-normal">Model</th>
                <th class="h-10 px-5 font-normal">Effort</th>
                <th class="h-10 px-5 font-normal">API key</th>
                <th class="h-10 px-5 font-normal">Created</th>
                <th class="h-10 px-5 font-normal"><span class="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody class="divide-y divide-white/[0.05]">
              <tr
                v-for="m in models"
                :key="m.id"
                class="transition-colors duration-150 ease-out hover:bg-white/[0.02]"
              >
                <td class="h-14 px-5 py-2 text-slate-100 whitespace-nowrap">{{ m.name }}</td>
                <td class="h-14 px-5 py-2 text-slate-400 whitespace-nowrap">{{ getLlmProviderLabel(m.provider) }}</td>
                <td class="h-14 px-5 py-2 text-slate-300 font-mono text-xs whitespace-nowrap">{{ m.model }}</td>
                <td class="h-14 px-5 py-2 text-slate-400 whitespace-nowrap">{{ _reasoningCell(m) }}</td>
                <td class="h-14 px-5 py-2 text-slate-500 font-mono text-xs whitespace-nowrap">
                  <template v-if="m.provider === 'claude-cli'">{{ m.cli_path || 'claude' }}</template>
                  <template v-else-if="m.provider === 'codex-cli'">{{ m.cli_path || 'codex' }}</template>
                  <template v-else>{{ m.api_key || '—' }}</template>
                </td>
                <td class="h-14 px-5 py-2 text-slate-500 whitespace-nowrap tabular-nums">{{ fmtDate(m.created_at) }}</td>
                <td class="h-14 px-5 py-2 text-right">
                  <div class="flex flex-col items-end gap-1.5">
                    <div class="flex items-center justify-end gap-0.5 whitespace-nowrap">
                      <button
                        v-if="m.provider === 'claude-cli'"
                        @click="testCliConnection(m.id)"
                        :disabled="testCliStates[m.id]?.loading"
                        class="ui-icon-btn disabled:opacity-40"
                        title="Test connection"
                        aria-label="Test connection"
                      >
                        <span class="material-symbols-outlined text-[18px]" :class="{ 'animate-spin': testCliStates[m.id]?.loading }">
                          {{ testCliStates[m.id]?.loading ? 'progress_activity' : 'cable' }}
                        </span>
                      </button>
                      <button
                        @click="openEditModal(m)"
                        class="ui-icon-btn"
                        title="Edit"
                        aria-label="Edit"
                      >
                        <span class="material-symbols-outlined text-[18px]">edit</span>
                      </button>
                      <button
                        @click="deleteModel(m.id, m.name)"
                        class="ui-icon-btn hover:text-loss"
                        title="Delete"
                        aria-label="Delete"
                      >
                        <span class="material-symbols-outlined text-[18px]">delete</span>
                      </button>
                    </div>
                    <Transition name="fade">
                      <p
                        v-if="testCliStates[m.id]?.message && !testCliStates[m.id]?.loading"
                        class="max-w-60 text-left whitespace-normal break-words text-xs leading-relaxed"
                        :class="testCliStates[m.id]?.ok ? 'text-gain' : 'text-loss'"
                      >
                        {{ testCliStates[m.id].message }}
                      </p>
                    </Transition>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Which model explains moves on the AI Analysis screen -->
      <div class="mt-12">
        <AnalysisSettingsCard :models-version="modelsVersion" />
      </div>
    </main>
  </AppShell>

  <!-- ── Add / Edit Model Modal ──────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showModal"
        class="ui-overlay z-[70] bg-black/70"
        @click.self="closeModal"
      >
        <div class="modal-panel ui-modal max-w-lg flex flex-col max-h-[90vh]">
          <div class="ui-modal-head shrink-0">
            <div class="min-w-0">
              <h2 class="text-base font-medium text-slate-100">{{ editMode ? 'Edit model' : 'Add model' }}</h2>
              <p class="text-sm text-slate-500 mt-1">A reusable LLM configuration that strategies can reference</p>
            </div>
            <button @click="closeModal" class="ui-icon-btn ml-auto shrink-0" aria-label="Close">
              <span class="material-symbols-outlined text-[18px]">close</span>
            </button>
          </div>

          <div class="ui-modal-body flex-1 overflow-y-auto space-y-5">
            <!-- Name field (unique to Models) -->
            <div>
              <label class="ui-field-label">Name</label>
              <input
                v-model="formDraft.name"
                type="text"
                placeholder="e.g. Gemini Flash — Main"
                class="ui-input"
              />
              <p class="ui-help">Shown in strategy dropdowns</p>
            </div>

            <!-- Shared LLM config form -->
            <LlmConfigForm
              :draft="formDraft"
              @update:draft="formDraft = $event"
              :disabled="submitting"
              :editing="editMode"
            />

            <!-- Pricing override (optional). When any of these are set,
                 telemetry's cost computation will use them instead of
                 backend/llm_pricing.yaml. Leaving them blank keeps the
                 YAML defaults. -->
            <details class="group rounded-lg border border-white/[0.07]">
              <summary class="h-10 px-4 flex items-center justify-between gap-3 cursor-pointer list-none text-sm text-slate-300 [&::-webkit-details-marker]:hidden">
                <span>Pricing override <span class="text-slate-500">(optional)</span></span>
                <span class="material-symbols-outlined text-[18px] text-slate-500 transition-transform duration-150 ease-out group-open:rotate-180" aria-hidden="true">expand_more</span>
              </summary>
              <div class="px-4 pb-4">
                <p class="text-xs text-slate-500 mb-4 leading-relaxed">
                  Leave blank to use the backend <span class="font-mono">llm_pricing.yaml</span> defaults.
                  Values are dollars per 1M tokens.
                </p>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-4">
                  <div>
                    <label class="ui-field-label">Input cost <span class="text-slate-500">($/1M tokens)</span></label>
                    <input
                      v-model.number="formDraft.inputCostPer1m"
                      type="number"
                      step="0.01"
                      min="0"
                      :disabled="submitting"
                      placeholder="e.g. 3.00"
                      class="ui-input tabular-nums"
                    />
                  </div>
                  <div>
                    <label class="ui-field-label">Output cost <span class="text-slate-500">($/1M tokens)</span></label>
                    <input
                      v-model.number="formDraft.outputCostPer1m"
                      type="number"
                      step="0.01"
                      min="0"
                      :disabled="submitting"
                      placeholder="e.g. 15.00"
                      class="ui-input tabular-nums"
                    />
                  </div>
                  <div>
                    <label class="ui-field-label">Cache creation cost <span class="text-slate-500">($/1M tokens)</span></label>
                    <input
                      v-model.number="formDraft.cacheCreationCostPer1m"
                      type="number"
                      step="0.01"
                      min="0"
                      :disabled="submitting"
                      placeholder="e.g. 3.75"
                      class="ui-input tabular-nums"
                    />
                  </div>
                  <div>
                    <label class="ui-field-label">Cache read cost <span class="text-slate-500">($/1M tokens)</span></label>
                    <input
                      v-model.number="formDraft.cacheReadCostPer1m"
                      type="number"
                      step="0.01"
                      min="0"
                      :disabled="submitting"
                      placeholder="e.g. 0.30"
                      class="ui-input tabular-nums"
                    />
                  </div>
                </div>
              </div>
            </details>

            <!-- Status message -->
            <div
              v-if="submitMsg"
              class="msg"
              :class="submitting ? 'msg-busy' : submitOk ? 'msg-ok' : 'msg-err'"
            >
              {{ submitMsg }}
            </div>

            <!-- LLM test response (so operators can verify the test
                 isn't lying — e.g. an upstream proxy returning canned
                 200s would fail structured parse or surface odd values).
                 Shows the parsed Pydantic payload + latency + effective
                 model + any provider_meta the backend returned. -->
            <div
              v-if="testResult"
              class="rounded-lg border border-white/[0.07] px-4 py-4 text-xs leading-relaxed text-slate-400 space-y-4"
            >
              <div class="flex items-center gap-2 text-sm text-slate-200">
                <span class="ui-dot bg-gain"></span>
                LLM connectivity test response
              </div>
              <dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5">
                <dt class="text-slate-500">Provider</dt>
                <dd class="font-mono text-slate-200 break-all">{{ testResult.provider }}</dd>
                <dt class="text-slate-500">Model</dt>
                <dd class="font-mono text-slate-200 break-all">{{ testResult.model }}</dd>
                <template v-if="testResult.effective_model && testResult.effective_model !== testResult.model">
                  <dt class="text-slate-500">Effective</dt>
                  <dd class="font-mono text-slate-200 break-all">{{ testResult.effective_model }}</dd>
                </template>
                <template v-if="testResult.latency_ms != null">
                  <dt class="text-slate-500">Latency</dt>
                  <dd class="text-slate-200 tabular-nums">{{ testResult.latency_ms }} ms</dd>
                </template>
              </dl>
              <div v-if="testResult.result">
                <div class="text-slate-500 mb-1.5">Structured connectivity probe (parsed response)</div>
                <pre class="test-pre break-all">{{ _prettyJson(testResult.result) }}</pre>
              </div>
              <div v-if="testResult.smoke_prompt || testResult.smoke_response || testResult.smoke_thinking || testResult.smoke_error" class="space-y-1.5">
                <div class="text-slate-500">
                  Real-generation smoke
                  <span v-if="testResult.smoke_latency_ms != null" class="tabular-nums">· {{ testResult.smoke_latency_ms }} ms</span>
                  <span v-if="testResult.smoke_content_chars != null || testResult.smoke_thinking_chars != null" class="tabular-nums">
                    · content {{ testResult.smoke_content_chars ?? 0 }} chars
                    · reasoning {{ testResult.smoke_thinking_chars ?? 0 }} chars
                  </span>
                </div>
                <div v-if="testResult.smoke_prompt" class="text-slate-500">
                  Prompt: <span class="text-slate-300">{{ testResult.smoke_prompt }}</span>
                </div>

                <!-- Visible answer (content) — prefer this when populated. -->
                <template v-if="testResult.smoke_response && (testResult.smoke_content_chars == null || testResult.smoke_content_chars > 0)">
                  <div class="text-slate-500">Content (visible answer)</div>
                  <pre class="test-pre break-words">{{ testResult.smoke_response }}</pre>
                </template>

                <!-- Reasoning / thinking — for Ollama reasoning models. -->
                <details v-if="testResult.smoke_thinking">
                  <summary class="cursor-pointer text-slate-500 hover:text-slate-300 transition-colors duration-150 ease-out">
                    Reasoning (<span class="tabular-nums">{{ testResult.smoke_thinking_chars ?? testResult.smoke_thinking.length }}</span> chars)
                  </summary>
                  <pre class="test-pre break-words mt-1.5 text-slate-400">{{ testResult.smoke_thinking }}</pre>
                </details>

                <!-- Empty-content fallback: only thinking was returned. -->
                <div
                  v-if="!testResult.smoke_response && !testResult.smoke_thinking && testResult.smoke_error"
                  class="text-loss whitespace-pre-wrap break-words">
                  Smoke generation failed: {{ testResult.smoke_error }}
                </div>
                <div
                  v-else-if="!testResult.smoke_response && !testResult.smoke_thinking"
                  class="flex items-start gap-2 text-slate-300">
                  <span class="ui-dot ui-dot-warn mt-1.5"></span>
                  <span>Smoke generation returned empty — the structured check passed but the
                  model produced no free-form text. Strategy use may still fail.</span>
                </div>
                <div
                  v-else-if="testResult.smoke_content_chars === 0 && testResult.smoke_thinking_chars > 0"
                  class="flex items-start gap-2 text-slate-300">
                  <span class="ui-dot ui-dot-warn mt-1.5"></span>
                  <span>Model produced only reasoning tokens — no visible answer. Set Thinking/Effort to Off or Low, or raise the smoke max_output_tokens budget.</span>
                </div>
              </div>
              <div v-if="testResult.provider_meta && Object.keys(testResult.provider_meta).length">
                <div class="text-slate-500 mb-1.5">Provider meta</div>
                <pre class="test-pre break-all">{{ _prettyJson(testResult.provider_meta) }}</pre>
              </div>
            </div>

            <p v-if="editMode" class="ui-help">
              Leave the API key empty to keep the existing key
            </p>
          </div>

          <div class="ui-modal-foot shrink-0">
            <button @click="closeModal"
              :disabled="submitting"
              :class="saved ? 'ui-btn-primary' : 'ui-btn-ghost h-9 px-4 rounded-lg'">
              {{ saved ? 'Close' : 'Cancel' }}
            </button>
            <!-- Test-only button: hits /llm/test against the current
                 draft without touching the Models table. Useful when
                 the operator is iterating on a config and wants to
                 verify auth + model resolution + smoke generation
                 before committing. Hidden for claude-cli (which has a
                 separate per-row cable-icon test). Stays visible after
                 a successful Test only run so the operator can re-test
                 or jump straight to Save without reopening the modal. -->
            <button v-if="!saved && formDraft.provider !== 'claude-cli'"
              @click="testOnly"
              :disabled="submitting"
              class="ui-btn h-9 px-4">
              <template v-if="submitting">Testing...</template>
              <template v-else>Test only</template>
            </button>
            <button v-if="!saved"
              @click="submitModel"
              :disabled="submitting"
              class="ui-btn-primary">
              <template v-if="submitting">
                {{ formDraft.provider === 'claude-cli' ? (editMode ? 'Updating...' : 'Saving...') : 'Testing...' }}
              </template>
              <template v-else>
                {{ editMode ? 'Test & update' : 'Test & save' }}
              </template>
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* Modal status line: busy is neutral, success gain, failure loss. */
.msg { @apply rounded-lg border px-4 py-3 text-sm break-words; }
.msg-busy { @apply border-white/[0.07] bg-raised text-slate-300; }
.msg-ok { @apply border-gain/20 bg-gain/5 text-gain; }
.msg-err { @apply border-loss/20 bg-loss/5 text-loss; }

.test-pre { @apply font-mono text-[11px] leading-relaxed text-slate-300 bg-background-dark rounded-md px-3 py-2 overflow-x-auto whitespace-pre-wrap; }

.fade-enter-active, .fade-leave-active { transition: opacity 180ms cubic-bezier(0.22, 1, 0.36, 1); }
.fade-enter-from, .fade-leave-to       { opacity: 0; }
.fade-enter-active .modal-panel, .fade-leave-active .modal-panel { transition: transform 220ms cubic-bezier(0.22, 1, 0.36, 1); }
.fade-enter-from .modal-panel, .fade-leave-to .modal-panel { transform: translateY(6px); }
@media (prefers-reduced-motion: reduce) {
  .fade-enter-active, .fade-leave-active,
  .fade-enter-active .modal-panel, .fade-leave-active .modal-panel { transition: none; }
}
</style>
