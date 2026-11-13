<script setup>
import { ref, computed } from 'vue'
import LlmConfigForm from '../LlmConfigForm.vue'
import { authHeaders, ONBOARDING_API_BASE as API_BASE } from '../../utils/auth.js'

const props = defineProps({
  models: { type: Array, default: () => [] },
})
const emit = defineEmits(['added'])

const showForm = ref(props.models.length === 0)
const submitting = ref(false)
const submitMsg = ref('')
const submitOk = ref(false)

const draft = ref({
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

function updateDraft(next) { draft.value = next }

const canSubmit = computed(() =>
  !!draft.value.name.trim() && !!draft.value.provider && !!draft.value.model.trim()
)

function _normalizeError(data, status) {
  const d = data?.detail
  if (Array.isArray(d)) return d.map(x => (x && (x.msg || x.message)) || JSON.stringify(x)).join('; ')
  if (typeof d === 'string') return d
  if (d && typeof d === 'object') return d.msg || d.message || JSON.stringify(d)
  return `HTTP ${status}`
}

function _payload() {
  const isCli = draft.value.provider === 'claude-cli'
  return {
    name: draft.value.name.trim(),
    provider: draft.value.provider,
    model: draft.value.model.trim(),
    api_key: isCli ? undefined : (draft.value.apiKey.trim() || undefined),
    openai_base_url: isCli ? undefined : (draft.value.openaiBaseUrl.trim() || undefined),
    nvidia_base_url: isCli ? undefined : (draft.value.nvidiaBaseUrl.trim() || undefined),
    azure_openai_endpoint: isCli ? undefined : (draft.value.azureEndpoint.trim() || undefined),
    azure_openai_api_version: isCli ? undefined : (draft.value.azureApiVersion.trim() || undefined),
    reasoning_effort: isCli ? undefined : (draft.value.reasoningEffort || undefined),
    cli_path: isCli ? (draft.value.cliPath.trim() || undefined) : undefined,
    extra_args: isCli ? (draft.value.extraArgs.trim() || undefined) : undefined,
  }
}

async function testAndSave() {
  if (!canSubmit.value) {
    submitOk.value = false
    submitMsg.value = 'Name, provider, and model are required.'
    return
  }
  submitting.value = true
  submitOk.value = false
  submitMsg.value = 'Testing model connection…'
  try {
    const body = _payload()
    if (body.api_key) {
      const t = await fetch(`${API_BASE}/llm/test`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: body.provider,
          model: body.model,
          api_key: body.api_key,
          openai_base_url: body.openai_base_url,
          nvidia_base_url: body.nvidia_base_url,
          azure_openai_endpoint: body.azure_openai_endpoint,
          azure_openai_api_version: body.azure_openai_api_version,
          reasoning_effort: body.reasoning_effort,
        }),
      })
      const td = await t.json().catch(() => ({}))
      if (!t.ok || td?.ok === false) throw new Error(_normalizeError(td, t.status))
    }
    submitMsg.value = 'Saving model…'
    const res = await fetch(`${API_BASE}/models`, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(_normalizeError(data, res.status))
    submitOk.value = true
    submitMsg.value = `Model "${data.name || body.name}" saved.`
    showForm.value = false
    draft.value = {
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
    emit('added', data)
  } catch (e) {
    submitOk.value = false
    submitMsg.value = e.message || 'Something went wrong'
  } finally {
    submitting.value = false
  }
}

function maskKey(k) {
  if (!k) return ''
  const s = String(k)
  if (s.length <= 4) return '••••'
  return '••••' + s.slice(-4)
}
</script>

<template>
  <div class="space-y-6">
    <div>
      <p class="text-sm text-slate-500">Step 1 · Add a model</p>
      <h2 class="mt-2 text-xl sm:text-2xl font-medium leading-tight text-slate-100">
        Choose the LLM your strategies call
      </h2>
      <p class="mt-3 text-sm leading-6 text-slate-400">
        Drop in an API key for any supported LLM provider. Strategies reference models by ID, so you can
        swap or add more later. Gemini's free tier works well as a starter.
      </p>
    </div>

    <!-- Existing models -->
    <div v-if="models.length">
      <p class="ui-label mb-2">Already configured</p>
      <div class="rounded-xl border border-white/[0.07] ui-rows">
        <div v-for="m in models" :key="m.id" class="min-h-14 px-4 py-2 flex flex-col justify-center">
          <p class="text-sm text-slate-100 truncate">{{ m.name }}</p>
          <p class="mt-0.5 text-xs text-slate-500 truncate font-mono">
            {{ m.provider }} · {{ m.model }}
            <span v-if="m.provider === 'claude-cli'">· {{ m.cli_path || 'claude' }}</span>
            <span v-else-if="m.api_key">· {{ maskKey(m.api_key) }}</span>
          </p>
        </div>
      </div>
      <button
        v-if="!showForm"
        type="button"
        @click="showForm = true"
        class="ui-btn-ghost mt-2 -ml-3"
      >
        <span class="material-symbols-outlined text-[16px]">add</span>
        Add another model
      </button>
    </div>

    <!-- Add form -->
    <div v-if="showForm" class="rounded-xl border border-white/[0.07] p-4 sm:p-5 space-y-5">
      <div>
        <label class="ui-field-label">Model name <span class="text-slate-500">*</span></label>
        <input
          v-model="draft.name"
          type="text"
          placeholder="e.g. gemini-flash-prod"
          class="ui-input"
        />
        <p class="ui-help">A label you'll recognize when picking models in strategies</p>
      </div>
      <LlmConfigForm :draft="draft" :disabled="submitting" @update:draft="updateDraft" />

      <Transition name="step">
        <div
          v-if="submitMsg"
          class="rounded-xl border px-4 py-3 text-sm"
          :class="submitOk ? 'border-gain/20 bg-gain/5 text-gain' : 'border-loss/20 bg-loss/5 text-loss'"
          role="status"
        >
          <span v-if="submitting" class="inline-flex items-center gap-2 text-slate-300">
            <span class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
            {{ submitMsg }}
          </span>
          <span v-else>{{ submitMsg }}</span>
        </div>
      </Transition>

      <div class="flex flex-col-reverse sm:flex-row items-stretch sm:items-center gap-2 sm:justify-end">
        <button
          v-if="models.length"
          type="button"
          @click="showForm = false"
          :disabled="submitting"
          class="ui-btn-ghost h-9"
        >Cancel</button>
        <button
          type="button"
          @click="testAndSave"
          :disabled="submitting || !canSubmit"
          class="ui-btn-primary"
        >
          <span v-if="submitting" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
          {{ submitting ? 'Working…' : 'Test &amp; save' }}
        </button>
      </div>
    </div>
  </div>
</template>
