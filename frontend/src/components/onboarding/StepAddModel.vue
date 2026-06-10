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
      <p class="text-primary text-xs font-bold uppercase tracking-widest mb-2 onboarding-letter" style="animation-delay: 0.05s">
        Step 1 · Add a model
      </p>
      <h2 class="onboarding-title text-2xl sm:text-3xl font-bold leading-tight onboarding-letter" style="animation-delay: 0.15s">
        Pick the brain that powers your trades.
      </h2>
      <p class="mt-3 text-slate-400 text-sm onboarding-letter" style="animation-delay: 0.25s">
        Drop in an API key for any supported LLM provider. Strategies reference models by ID, so you can
        swap or add more later — Gemini's free tier works great as a starter.
      </p>
    </div>

    <!-- Existing models -->
    <div v-if="models.length" class="space-y-2 onboarding-letter" style="animation-delay: 0.3s">
      <p class="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Already configured</p>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <div
          v-for="m in models"
          :key="m.id"
          class="rounded-lg border border-border-subtle bg-surface/40 px-3 py-2.5 flex items-center gap-3"
        >
          <span class="material-symbols-outlined text-primary text-[20px]">memory</span>
          <div class="min-w-0 flex-1">
            <p class="text-sm font-medium text-slate-100 truncate">{{ m.name }}</p>
            <p class="text-[11px] text-slate-500 truncate font-mono">
              {{ m.provider }} · {{ m.model }}
              <span v-if="m.provider === 'claude-cli'">· {{ m.cli_path || 'claude' }}</span>
              <span v-else-if="m.api_key">· {{ maskKey(m.api_key) }}</span>
            </p>
          </div>
        </div>
      </div>
      <button
        v-if="!showForm"
        @click="showForm = true"
        class="mt-1 inline-flex items-center gap-1.5 text-xs text-primary hover:brightness-125 transition-all"
      >
        <span class="material-symbols-outlined text-[14px]">add</span>
        Add another model
      </button>
    </div>

    <!-- Add form -->
    <div v-if="showForm" class="rounded-xl border border-border-subtle bg-surface/40 p-4 sm:p-5 space-y-4 onboarding-letter" style="animation-delay: 0.35s">
      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">Model Name <span class="text-red-400">*</span></label>
        <input
          v-model="draft.name"
          type="text"
          placeholder="e.g. gemini-flash-prod"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors"
        />
        <p class="text-[11px] text-slate-500 mt-1">A label you'll recognize when picking models in strategies.</p>
      </div>
      <LlmConfigForm :draft="draft" :disabled="submitting" @update:draft="updateDraft" />

      <Transition name="step">
        <div
          v-if="submitMsg"
          class="rounded-lg px-3 py-2.5 text-sm font-medium"
          :class="submitOk
            ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
            : 'bg-red-500/10 border border-red-500/20 text-red-400'"
        >
          <span v-if="submitting" class="inline-flex items-center gap-2">
            <span class="material-symbols-outlined text-base animate-spin">progress_activity</span>
            {{ submitMsg }}
          </span>
          <span v-else>{{ submitMsg }}</span>
        </div>
      </Transition>

      <div class="flex flex-col-reverse sm:flex-row items-stretch sm:items-center gap-2 sm:justify-end">
        <button
          v-if="models.length"
          @click="showForm = false"
          :disabled="submitting"
          class="px-3 py-2 text-sm text-slate-400 hover:text-slate-100 transition-colors disabled:opacity-40"
        >Cancel</button>
        <button
          @click="testAndSave"
          :disabled="submitting || !canSubmit"
          class="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary/90 text-background-dark text-sm font-bold hover:brightness-110 transition-all disabled:opacity-40"
        >
          <span v-if="submitting" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
          <span v-else class="material-symbols-outlined text-[16px]">bolt</span>
          {{ submitting ? 'Working…' : 'Test &amp; save' }}
        </button>
      </div>
    </div>
  </div>
</template>
