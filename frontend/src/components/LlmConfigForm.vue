<script setup>
import {
  LLM_PROVIDER_OPTIONS,
  LLM_REASONING_EFFORT_OPTIONS,
  NVIDIA_REASONING_EFFORT_OPTIONS,
  getLlmProviderLabel,
} from '../utils/strategyConfig.js'

const props = defineProps({
  draft: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
  readOnly: { type: Boolean, default: false },
})

const emit = defineEmits(['update:draft'])

function update(field, value) {
  emit('update:draft', { ...props.draft, [field]: value })
}

function onProviderChange(value) {
  const next = { ...props.draft, provider: value }
  if (value === 'claude-cli') {
    next.apiKey = ''
    next.openaiBaseUrl = ''
    next.nvidiaBaseUrl = ''
    next.azureEndpoint = ''
    next.azureApiVersion = '2024-10-21'
    next.reasoningEffort = ''
  } else {
    next.cliPath = ''
    next.extraArgs = ''
  }
  emit('update:draft', next)
}
</script>

<template>
  <div class="space-y-4">
    <div>
      <label class="block text-xs font-medium text-slate-400 mb-1.5">Provider</label>
      <select
        :value="draft.provider"
        @change="onProviderChange($event.target.value)"
        :disabled="disabled || readOnly"
        class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors disabled:opacity-50"
      >
        <option v-for="option in LLM_PROVIDER_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
      </select>
    </div>

    <div>
      <label class="block text-xs font-medium text-slate-400 mb-1.5">
        {{ draft.provider === 'azure' ? 'Deployment / Model Name' : 'Model' }}
      </label>
      <input
        :value="draft.model"
        @input="update('model', $event.target.value)"
        type="text"
        :disabled="disabled || readOnly"
        :placeholder="draft.provider === 'azure' ? 'e.g. gpt-5.2 deployment name' : (draft.provider === 'claude-cli' ? 'claude-sonnet-4-6' : 'e.g. gemini-3-flash-preview')"
        class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
      />
    </div>

    <template v-if="draft.provider === 'claude-cli'">
      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">CLI Path</label>
        <input
          :value="draft.cliPath"
          @input="update('cliPath', $event.target.value)"
          type="text"
          :disabled="disabled || readOnly"
          placeholder="claude"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
        />
        <p class="mt-1.5 text-[11px] leading-relaxed text-slate-500">
          Path to the claude binary. Leave as 'claude' if it's on PATH.
        </p>
      </div>
      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">Extra Args</label>
        <input
          :value="draft.extraArgs"
          @input="update('extraArgs', $event.target.value)"
          type="text"
          :disabled="disabled || readOnly"
          placeholder="--fallback-model claude-haiku-4-5"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
        />
        <p class="mt-1.5 text-[11px] leading-relaxed text-slate-500">
          Optional advanced flags. Only --fallback-model, --effort, --max-budget-usd are accepted; everything else is rejected for security.
        </p>
        <p class="mt-1 text-[11px] leading-relaxed text-slate-600">
          Saving will fail with the backend's error message if a flag isn't on the allowlist.
        </p>
      </div>
      <div class="rounded-lg border border-sky-500/20 bg-sky-500/5 px-3 py-3 text-[11px] leading-relaxed text-slate-400">
        Uses the locally-installed <span class="font-mono">claude</span> binary on the server. SSH to the server and run <span class="font-mono">claude</span> to log in before using. Tools are disabled — CC is used as a text-only LLM.
      </div>
    </template>

    <div v-if="draft.provider === 'azure' || draft.provider === 'openai' || draft.provider === 'nvidia'">
      <label class="block text-xs font-medium text-slate-400 mb-1.5">Reasoning Effort</label>
      <select
        :value="draft.reasoningEffort"
        @change="update('reasoningEffort', $event.target.value)"
        :disabled="disabled || readOnly"
        class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors disabled:opacity-50"
      >
        <option
          v-for="option in draft.provider === 'nvidia' ? NVIDIA_REASONING_EFFORT_OPTIONS : LLM_REASONING_EFFORT_OPTIONS"
          :key="option.value || 'default'"
          :value="option.value"
        >{{ option.label }}</option>
      </select>
      <p class="mt-1.5 text-[11px] leading-relaxed text-slate-500">
        <template v-if="draft.provider === 'nvidia'">
          Controls NVIDIA Super's reasoning mode. None disables thinking tokens, Low/Medium/High enable reasoning with increasing budget.
        </template>
        <template v-else-if="draft.reasoningEffort">
          Lower is faster. Cached/history model references will be stored like
          <span class="font-mono text-slate-400">{{ (draft.model || 'model') + '-' + String(draft.reasoningEffort).toUpperCase() }}</span>.
        </template>
        <template v-else>
          Lower is faster. Leave this at Default to keep the plain model/deployment name in caches and history.
        </template>
      </p>
    </div>

    <div v-if="draft.provider !== 'claude-cli'">
      <label class="block text-xs font-medium text-slate-400 mb-1.5">
        {{ draft.provider === 'azure' ? 'Azure API Key' : 'API Key' }}
      </label>
      <input
        :value="draft.apiKey"
        @input="update('apiKey', $event.target.value)"
        type="password"
        :disabled="disabled || readOnly"
        :placeholder="draft.provider === 'nvidia' ? 'NVIDIA API Key (nvapi-...)' : 'Optional if provided by environment'"
        class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
      />
    </div>

    <div v-if="draft.provider === 'openai'">
      <label class="block text-xs font-medium text-slate-400 mb-1.5">OpenAI Base URL</label>
      <input
        :value="draft.openaiBaseUrl"
        @input="update('openaiBaseUrl', $event.target.value)"
        type="text"
        :disabled="disabled || readOnly"
        placeholder="Optional custom base URL"
        class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
      />
    </div>

    <div v-if="draft.provider === 'nvidia'">
      <label class="block text-xs font-medium text-slate-400 mb-1.5">NVIDIA NIM Base URL</label>
      <input
        :value="draft.nvidiaBaseUrl"
        @input="update('nvidiaBaseUrl', $event.target.value)"
        type="text"
        :disabled="disabled || readOnly"
        placeholder="https://integrate.api.nvidia.com/v1"
        class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
      />
    </div>

    <template v-if="draft.provider === 'azure'">
      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">Azure Endpoint</label>
        <input
          :value="draft.azureEndpoint"
          @input="update('azureEndpoint', $event.target.value)"
          type="text"
          :disabled="disabled || readOnly"
          placeholder="https://your-resource.services.ai.azure.com"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
        />
      </div>
      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">API Version</label>
        <input
          :value="draft.azureApiVersion"
          @input="update('azureApiVersion', $event.target.value)"
          type="text"
          :disabled="disabled || readOnly"
          placeholder="2024-10-21"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
        />
      </div>
      <div class="rounded-lg border border-sky-500/20 bg-sky-500/5 px-3 py-3 text-[11px] leading-relaxed text-slate-400">
        Use the Azure resource root plus your deployment/model name. Do not use a full <span class="font-mono">/models/chat/completions</span> or <span class="font-mono">/openai/v1/</span> URL here.
      </div>
    </template>

    <div class="rounded-lg border border-border-subtle bg-[#0f1318] px-3 py-3 text-[11px] leading-relaxed text-slate-500">
      Current provider: <span class="text-slate-300">{{ getLlmProviderLabel(draft.provider) }}</span>
    </div>
  </div>
</template>
