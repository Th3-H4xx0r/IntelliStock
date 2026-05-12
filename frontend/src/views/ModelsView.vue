<script setup>
import { ref, onMounted } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import LlmConfigForm from '../components/LlmConfigForm.vue'
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

// Modal
const showModal  = ref(false)
const editMode   = ref(false)
const editId     = ref(null)
const submitting = ref(false)
const submitMsg  = ref('')
const submitOk   = ref(false)

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
  }
  submitting.value = false
  submitMsg.value = ''
  submitOk.value = false
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
  }
  submitting.value = false
  submitMsg.value = ''
  submitOk.value = false
  showModal.value = true
}

function closeModal(force = false) {
  if (submitting.value && !force) return
  showModal.value = false
  editId.value = null
  submitMsg.value = ''
  submitOk.value = false
}

async function submitModel() {
  const d = formDraft.value
  if (!d.name.trim()) { submitMsg.value = 'Name is required'; submitOk.value = false; return }
  if (!d.model.trim()) { submitMsg.value = 'Model is required'; submitOk.value = false; return }

  submitting.value = true
  submitOk.value = false
  const isCli = d.provider === 'claude-cli'
  submitMsg.value = isCli ? (editMode.value ? 'Updating model...' : 'Saving model...') : 'Testing LLM configuration...'

  // Test the LLM config first. Skip for claude-cli (no key), and skip on edit when
  // no api_key is in the draft (user didn't intend to re-test creds they didn't change).
  const hasKey = !!d.apiKey.trim()
  if (!isCli && hasKey) {
    try {
      const testPayload = buildStrategyLlmTestPayload(d)
      const testRes = await fetch(`${API_BASE}/llm/test`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify(testPayload),
      })
      const testBody = await testRes.json().catch(() => ({}))
      if (!testRes.ok) throw new Error(_normalizeError(testBody, testRes.status))
    } catch (e) {
      submitOk.value = false
      submitMsg.value = `LLM test failed: ${e.message}`
      submitting.value = false
      return
    }
  }

  // Create or update
  submitMsg.value = editMode.value ? 'Updating model...' : 'Saving model...'
  try {
    const payload = {
      name: d.name.trim(),
      provider: d.provider,
      model: d.model.trim(),
    }
    if (isCli) {
      payload.cli_path = d.cliPath.trim() || undefined
      payload.extra_args = d.extraArgs.trim() || undefined
    } else {
      payload.openai_base_url = d.openaiBaseUrl.trim() || undefined
      payload.nvidia_base_url = d.nvidiaBaseUrl.trim() || undefined
      payload.azure_openai_endpoint = d.azureEndpoint.trim() || undefined
      payload.azure_openai_api_version = d.azureApiVersion.trim() || undefined
      payload.reasoning_effort = (d.reasoningEffort || '').trim() || undefined
      // Clear any leftover CLI fields when switching provider away from claude-cli
      payload.cli_path = undefined
      payload.extra_args = undefined
      // Only include api_key if it was provided (don't overwrite with empty on edit)
      if (d.apiKey.trim()) {
        payload.api_key = d.apiKey.trim()
      }
    }

    const url = editMode.value ? `${API_BASE}/models/${editId.value}` : `${API_BASE}/models`
    const method = editMode.value ? 'PUT' : 'POST'
    const res = await fetch(url, {
      method,
      headers: authHeaders(),
      body: JSON.stringify(payload),
    })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(_normalizeError(body, res.status))

    submitOk.value = true
    submitMsg.value = editMode.value ? 'Model updated.' : 'Model saved.'
    submitting.value = false
    closeModal(true)
    await fetchModels()
  } catch (e) {
    submitOk.value = false
    submitMsg.value = e.message || 'Failed to save model.'
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
          message: `✓ v${data.version || '?'}, ${loggedIn}${responseSegment}`,
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
    <div class="max-w-5xl mx-auto p-6">
      <!-- Header -->
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-xl font-bold text-slate-100">Models</h1>
          <p class="text-xs text-slate-500 mt-1">Centralized LLM model configurations. Strategies can reference these instead of storing credentials inline.</p>
        </div>
        <button
          @click="openCreateModal"
          class="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 transition-all"
        >
          <span class="material-symbols-outlined text-base">add</span>
          Add Model
        </button>
      </div>

      <!-- Loading / Error -->
      <div v-if="loading" class="text-sm text-slate-500">Loading models...</div>
      <div v-else-if="loadError" class="text-sm text-red-400">{{ loadError }}</div>

      <!-- Empty State -->
      <div
        v-else-if="models.length === 0"
        class="text-center py-16 border border-border-subtle rounded-2xl bg-surface"
      >
        <span class="material-symbols-outlined text-4xl text-slate-600 mb-3 block">psychology</span>
        <p class="text-sm text-slate-400 mb-1">No models saved yet.</p>
        <p class="text-xs text-slate-600">Add a model to start using centralized LLM configurations.</p>
      </div>

      <!-- Models Table -->
      <div v-else class="border border-border-subtle rounded-2xl bg-surface overflow-hidden">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-border-subtle text-left text-xs text-slate-500 uppercase tracking-wider">
              <th class="px-5 py-3 font-medium">Name</th>
              <th class="px-5 py-3 font-medium">Provider</th>
              <th class="px-5 py-3 font-medium">Model</th>
              <th class="px-5 py-3 font-medium">Effort</th>
              <th class="px-5 py-3 font-medium">API Key</th>
              <th class="px-5 py-3 font-medium">Created</th>
              <th class="px-5 py-3 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="m in models"
              :key="m.id"
              class="border-b border-border-subtle last:border-0 hover:bg-white/[0.02] transition-colors"
            >
              <td class="px-5 py-3.5 font-medium text-slate-200">{{ m.name }}</td>
              <td class="px-5 py-3.5 text-slate-400">{{ getLlmProviderLabel(m.provider) }}</td>
              <td class="px-5 py-3.5 text-slate-400 font-mono text-xs">{{ m.model }}</td>
              <td class="px-5 py-3.5 text-slate-400 text-xs">{{ m.provider === 'claude-cli' ? '—' : (m.reasoning_effort ? m.reasoning_effort.charAt(0).toUpperCase() + m.reasoning_effort.slice(1) : 'Default') }}</td>
              <td class="px-5 py-3.5 text-slate-500 font-mono text-xs">
                <template v-if="m.provider === 'claude-cli'">{{ m.cli_path || 'claude' }}</template>
                <template v-else>{{ m.api_key || '—' }}</template>
              </td>
              <td class="px-5 py-3.5 text-slate-500 text-xs">{{ fmtDate(m.created_at) }}</td>
              <td class="px-5 py-3.5 text-right whitespace-nowrap">
                <button
                  v-if="m.provider === 'claude-cli'"
                  @click="testCliConnection(m.id)"
                  :disabled="testCliStates[m.id]?.loading"
                  class="text-slate-500 hover:text-primary transition-colors mr-2 disabled:opacity-40"
                  title="Test connection"
                >
                  <span class="material-symbols-outlined text-base" :class="{ 'animate-spin': testCliStates[m.id]?.loading }">
                    {{ testCliStates[m.id]?.loading ? 'progress_activity' : 'cable' }}
                  </span>
                </button>
                <button
                  @click="openEditModal(m)"
                  class="text-slate-500 hover:text-primary transition-colors mr-2"
                  title="Edit"
                >
                  <span class="material-symbols-outlined text-base">edit</span>
                </button>
                <button
                  @click="deleteModel(m.id, m.name)"
                  class="text-slate-500 hover:text-red-400 transition-colors"
                  title="Delete"
                >
                  <span class="material-symbols-outlined text-base">delete</span>
                </button>
                <Transition name="fade">
                  <div
                    v-if="testCliStates[m.id]?.message && !testCliStates[m.id]?.loading"
                    class="mt-2 inline-block max-w-xs text-left rounded-md px-2 py-1.5 text-[11px] leading-relaxed border"
                    :class="testCliStates[m.id]?.ok
                      ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300'
                      : 'border-red-500/20 bg-red-500/10 text-red-300'"
                  >
                    {{ testCliStates[m.id].message }}
                  </div>
                </Transition>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </AppShell>

  <!-- ── Add / Edit Model Modal ──────────────────────────────────────────── -->
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="showModal"
        class="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
        @click.self="closeModal"
      >
        <div class="relative w-full max-w-lg bg-[#0f1318] border border-border-subtle rounded-2xl shadow-2xl flex flex-col max-h-[90vh]">
          <div class="flex items-center justify-between px-6 py-5 border-b border-border-subtle shrink-0">
            <div>
              <h2 class="text-base font-bold">{{ editMode ? 'Edit Model' : 'Add Model' }}</h2>
              <p class="text-[11px] text-slate-500 mt-0.5">Save a reusable LLM configuration that strategies can reference.</p>
            </div>
            <button @click="closeModal" class="text-slate-500 hover:text-slate-300 transition-colors">
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>

          <div class="flex-1 overflow-y-auto px-6 py-5 space-y-4">
            <!-- Name field (unique to Models) -->
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">Name</label>
              <input
                v-model="formDraft.name"
                type="text"
                placeholder="e.g. Gemini Flash — Main"
                class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors"
              />
              <p class="mt-1 text-[11px] text-slate-600">A human-readable label shown in strategy dropdowns.</p>
            </div>

            <!-- Shared LLM config form -->
            <LlmConfigForm
              :draft="formDraft"
              @update:draft="formDraft = $event"
              :disabled="submitting"
            />

            <!-- Status message -->
            <div
              v-if="submitMsg"
              class="rounded-lg px-3 py-3 text-[11px] leading-relaxed border"
              :class="submitOk
                ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300'
                : 'border-red-500/20 bg-red-500/10 text-red-300'"
            >
              {{ submitMsg }}
            </div>

            <div
              v-if="editMode"
              class="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-3 text-[11px] leading-relaxed text-amber-300/80"
            >
              Leave API Key empty to keep the existing key unchanged.
            </div>
          </div>

          <div class="px-6 pb-6 pt-4 border-t border-border-subtle flex gap-3 shrink-0">
            <button @click="closeModal"
              :disabled="submitting"
              class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors">
              Cancel
            </button>
            <button @click="submitModel"
              :disabled="submitting"
              class="flex-1 py-2.5 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 transition-all disabled:opacity-60 disabled:cursor-not-allowed">
              <template v-if="submitting">
                {{ formDraft.provider === 'claude-cli' ? (editMode ? 'Updating...' : 'Saving...') : 'Testing...' }}
              </template>
              <template v-else>
                {{ editMode ? 'Test & Update' : 'Test & Save' }}
              </template>
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
