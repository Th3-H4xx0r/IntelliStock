<script setup>
import { computed, ref, watch } from 'vue'
import {
  LLM_PROVIDER_OPTIONS,
  LLM_REASONING_EFFORT_OPTIONS,
  NVIDIA_REASONING_EFFORT_OPTIONS,
  CLAUDE_CLI_EFFORT_OPTIONS,
  OLLAMA_THINK_OPTIONS,
  BEDROCK_REASONING_OPTIONS,
  getLlmProviderLabel,
} from '../utils/strategyConfig.js'
import { loadOllamaModels } from '../composables/useOllamaModels.js'
import { loadBedrockModels } from '../composables/useBedrockModels.js'
import CodexCliSetupPanel from './CodexCliSetupPanel.vue'

const props = defineProps({
  draft: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
  readOnly: { type: Boolean, default: false },
})

const emit = defineEmits(['update:draft'])

function update(field, value) {
  emit('update:draft', { ...props.draft, [field]: value })
}

const effortOptions = computed(() => {
  if (props.draft.provider === 'nvidia') return NVIDIA_REASONING_EFFORT_OPTIONS
  if (props.draft.provider === 'claude-cli') return CLAUDE_CLI_EFFORT_OPTIONS
  return LLM_REASONING_EFFORT_OPTIONS
})

const isCliProvider = computed(() =>
  props.draft.provider === 'claude-cli' || props.draft.provider === 'codex-cli'
)

// ── Ollama-specific state ────────────────────────────────────────────────────
const ollamaModels = ref([])
const ollamaListLoading = ref(false)
const ollamaListError = ref('')

const ollamaCloudHost = computed(() => {
  const url = String(props.draft?.ollamaBaseUrl || '').trim()
  if (!url) return false
  try {
    const host = new URL(url).hostname.toLowerCase()
    return host === 'ollama.com' || host.endsWith('.ollama.com')
  } catch (_) {
    return false
  }
})

const ollamaListSource = ref('')  // 'backend' | 'browser' | ''

async function fetchOllamaModels({ force = false } = {}) {
  const baseUrl = String(props.draft?.ollamaBaseUrl || '').trim()
  if (!baseUrl) {
    ollamaModels.value = []
    ollamaListError.value = ''
    ollamaListSource.value = ''
    return
  }
  ollamaListLoading.value = true
  ollamaListError.value = ''
  const { models, error, source } = await loadOllamaModels({
    baseUrl,
    apiKey: props.draft?.apiKey || '',
    force,
  })
  ollamaModels.value = models || []
  ollamaListError.value = error || ''
  ollamaListSource.value = source || ''
  ollamaListLoading.value = false
}

function refreshOllamaModels() {
  // Cancel any pending debounced auto-fetch so the explicit refresh wins.
  if (_debounceHandle) { clearTimeout(_debounceHandle); _debounceHandle = null }
  fetchOllamaModels({ force: true })
}

// 500ms debounce on the watcher so a user typing "http://localhost:11434"
// triggers ONE fetch when they pause, not one per character. The Refresh
// button still fires immediately via refreshOllamaModels().
let _debounceHandle = null
const FETCH_DEBOUNCE_MS = 500

watch(
  () => [props.draft?.provider, props.draft?.ollamaBaseUrl, props.draft?.apiKey],
  ([provider]) => {
    if (_debounceHandle) clearTimeout(_debounceHandle)
    if (provider !== 'ollama') {
      ollamaModels.value = []
      ollamaListError.value = ''
      return
    }
    _debounceHandle = setTimeout(() => {
      _debounceHandle = null
      fetchOllamaModels()
    }, FETCH_DEBOUNCE_MS)
  },
  { immediate: true },
)

// ── Bedrock-specific state ───────────────────────────────────────────────────
const bedrockModels = ref([])
const bedrockListLoading = ref(false)
const bedrockListError = ref('')
const BEDROCK_COMMON_REGIONS = [
  'us-east-1', 'us-west-2', 'us-east-2',
  'eu-central-1', 'eu-west-1', 'eu-west-3',
  'ap-southeast-1', 'ap-southeast-2', 'ap-northeast-1',
]

async function fetchBedrockModels({ force = false } = {}) {
  const region = String(props.draft?.bedrockRegion || '').trim()
  const apiKey = String(props.draft?.apiKey || '').trim()
  if (!region || !apiKey) {
    bedrockModels.value = []
    bedrockListError.value = ''
    return
  }
  bedrockListLoading.value = true
  bedrockListError.value = ''
  const { models, error } = await loadBedrockModels({ apiKey, region, force })
  bedrockModels.value = models || []
  bedrockListError.value = error || ''
  bedrockListLoading.value = false
}

function refreshBedrockModels() {
  if (_bedrockDebounceHandle) { clearTimeout(_bedrockDebounceHandle); _bedrockDebounceHandle = null }
  fetchBedrockModels({ force: true })
}

let _bedrockDebounceHandle = null

watch(
  () => [props.draft?.provider, props.draft?.bedrockRegion, props.draft?.apiKey],
  ([provider]) => {
    if (_bedrockDebounceHandle) clearTimeout(_bedrockDebounceHandle)
    if (provider !== 'bedrock') {
      bedrockModels.value = []
      bedrockListError.value = ''
      return
    }
    _bedrockDebounceHandle = setTimeout(() => {
      _bedrockDebounceHandle = null
      fetchBedrockModels()
    }, FETCH_DEBOUNCE_MS)
  },
  { immediate: true },
)

function onProviderChange(value) {
  const next = { ...props.draft, provider: value }
  if (value === 'claude-cli' || value === 'codex-cli') {
    next.apiKey = ''
    next.openaiBaseUrl = ''
    next.nvidiaBaseUrl = ''
    next.azureEndpoint = ''
    next.azureApiVersion = '2024-10-21'
    next.reasoningEffort = ''
    // Leave cli_path/extra_args untouched — the input placeholder
    // surfaces the right default ("codex" or "claude") visually, and
    // the backend treats an empty value as that same default.
  } else {
    next.cliPath = ''
    next.extraArgs = ''
  }
  if (value === 'ollama') {
    // Default a base URL so the picker can fire immediately. Operators
    // editing an existing row keep their stored value.
    if (!next.ollamaBaseUrl) next.ollamaBaseUrl = 'http://localhost:11434'
    next.reasoningEffort = ''  // model-specific for Ollama, not standard
  } else {
    // Clear Ollama-only fields when switching away so they don't leak
    // into a save payload meant for another provider. submitModel
    // already gates this on provider === 'ollama', but keeping the
    // draft clean is the same defensive pattern used above for CLI.
    next.ollamaBaseUrl = ''
    next.ollamaKeepAlive = ''
    next.ollamaThink = ''
  }
  if (value === 'bedrock') {
    // Seed sensible defaults so the picker can fire once a key is entered.
    if (!next.bedrockRegion) next.bedrockRegion = 'us-east-1'
    if (!next.bedrockReasoning) next.bedrockReasoning = 'off'
    next.reasoningEffort = ''  // bedrock reasoning is its own field
  } else {
    next.bedrockRegion = ''
    next.bedrockReasoning = ''
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
        :placeholder="draft.provider === 'azure' ? 'e.g. gpt-5.2 deployment name' : (draft.provider === 'claude-cli' ? 'claude-sonnet-4-6' : (draft.provider === 'codex-cli' ? 'gpt-5-codex' : 'e.g. gemini-3-flash-preview'))"
        class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
      />
    </div>

    <template v-if="isCliProvider">
      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">CLI Path</label>
        <input
          :value="draft.cliPath"
          @input="update('cliPath', $event.target.value)"
          type="text"
          :disabled="disabled || readOnly"
          :placeholder="draft.provider === 'codex-cli' ? 'codex' : 'claude'"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
        />
        <p class="mt-1.5 text-[11px] leading-relaxed text-slate-500">
          <template v-if="draft.provider === 'codex-cli'">
            Path to the <span class="font-mono">codex</span> binary. Leave as 'codex' if it's on PATH.
          </template>
          <template v-else>
            Path to the claude binary. Leave as 'claude' if it's on PATH.
          </template>
        </p>
      </div>
      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">Extra Args</label>
        <input
          :value="draft.extraArgs"
          @input="update('extraArgs', $event.target.value)"
          type="text"
          :disabled="disabled || readOnly"
          :placeholder="draft.provider === 'codex-cli' ? '--sandbox read-only' : '--fallback-model claude-haiku-4-5'"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
        />
        <p class="mt-1.5 text-[11px] leading-relaxed text-slate-500">
          <template v-if="draft.provider === 'codex-cli'">
            Optional advanced flags. Only --sandbox, --model, --config, --no-browser, --headless, --quiet, --profile, --ask-for-approval are accepted; everything else (especially --exec/--run) is rejected for security.
          </template>
          <template v-else>
            Optional advanced flags. Only --fallback-model, --effort, --max-budget-usd are accepted; everything else is rejected for security.
          </template>
        </p>
        <p class="mt-1 text-[11px] leading-relaxed text-slate-600">
          Saving will fail with the backend's error message if a flag isn't on the allowlist.
        </p>
      </div>
      <div v-if="draft.provider === 'claude-cli'" class="rounded-lg border border-sky-500/20 bg-sky-500/5 px-3 py-3 text-[11px] leading-relaxed text-slate-400">
        Uses the locally-installed <span class="font-mono">claude</span> binary on the server. SSH to the server and run <span class="font-mono">claude</span> to log in before using. Tools are disabled — CC is used as a text-only LLM.
      </div>
      <CodexCliSetupPanel
        v-if="draft.provider === 'codex-cli'"
        :cli-path="draft.cliPath || 'codex'"
      />
    </template>

    <!-- Ollama-specific configuration -->
    <template v-if="draft.provider === 'ollama'">
      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">Ollama Base URL</label>
        <input
          :value="draft.ollamaBaseUrl"
          @input="update('ollamaBaseUrl', $event.target.value)"
          type="text"
          :disabled="disabled || readOnly"
          placeholder="http://localhost:11434 or https://ollama.com/v1"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
        />
        <p class="mt-1.5 text-[11px] leading-relaxed text-slate-500">
          Local default <span class="font-mono">http://localhost:11434</span> · Ollama Cloud <span class="font-mono">https://ollama.com/v1</span> · or a remote self-hosted host.
        </p>
      </div>

      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">
          API Key
          <span v-if="ollamaCloudHost" class="text-amber-400">(required for ollama.com)</span>
          <span v-else class="text-slate-600">(optional — local Ollama has no auth)</span>
        </label>
        <input
          :value="draft.apiKey"
          @input="update('apiKey', $event.target.value)"
          type="password"
          :disabled="disabled || readOnly"
          placeholder="Ollama Cloud Bearer token, or leave blank for local"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
        />
      </div>

      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5 flex items-center justify-between">
          <span>Pick from installed models</span>
          <button
            type="button"
            @click="refreshOllamaModels"
            :disabled="disabled || readOnly || ollamaListLoading"
            class="text-[11px] text-primary hover:underline disabled:opacity-50"
          >{{ ollamaListLoading ? 'Loading…' : 'Refresh' }}</button>
        </label>
        <div v-if="ollamaListError" class="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11px] leading-relaxed text-amber-300 mb-2 space-y-1">
          <div>Couldn't reach Ollama at this base URL.</div>
          <div class="text-amber-200/70 font-mono whitespace-pre-wrap break-words">{{ ollamaListError }}</div>
          <div class="text-amber-200/70">
            We try the IntelliStock backend first, then fall back to a direct browser fetch. Both failed.
            For Tailscale / LAN / mDNS hosts that only your laptop can resolve, set
            <span class="font-mono">OLLAMA_ORIGINS=&quot;*&quot;</span>
            on the Ollama server (or pin to this app's origin) so the browser-direct
            fallback works. Otherwise enter the model name manually in the Model field above.
          </div>
        </div>
        <div v-if="!ollamaListError && ollamaListSource === 'browser'" class="rounded-lg border border-sky-500/20 bg-sky-500/5 px-3 py-2 text-[11px] leading-relaxed text-sky-300/80 mb-2">
          Model list loaded directly from your browser — the backend couldn't reach this host. Strategy calls run through the backend, so the model needs to be reachable from there too at runtime.
        </div>
        <select
          v-else
          :value="draft.model"
          @change="update('model', $event.target.value)"
          :disabled="disabled || readOnly || !ollamaModels.length"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors disabled:opacity-50"
        >
          <option value="" disabled>{{ ollamaModels.length ? 'Select a model' : 'No models found — install one with `ollama pull <name>`' }}</option>
          <option v-for="m in ollamaModels" :key="m.name" :value="m.name">
            {{ m.name }}<template v-if="m.parameter_size"> — {{ m.parameter_size }}</template><template v-if="m.quantization_level"> / {{ m.quantization_level }}</template><template v-if="m.context_length"> · ctx {{ m.context_length }}</template>
          </option>
        </select>
        <p class="mt-1.5 text-[11px] leading-relaxed text-slate-500">
          The Model field above is the source of truth — picking from the list just fills it in. Free-text entry works too (useful for cloud-only or just-pulled models).
        </p>
      </div>

      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">Thinking / Effort</label>
        <select
          :value="draft.ollamaThink"
          @change="update('ollamaThink', $event.target.value)"
          :disabled="disabled || readOnly"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors disabled:opacity-50"
        >
          <option v-for="o in OLLAMA_THINK_OPTIONS" :key="o.value || 'default'" :value="o.value">{{ o.label }}</option>
        </select>
        <p class="mt-1.5 text-[11px] leading-relaxed text-slate-500">
          Maps to Ollama's <span class="font-mono">think</span> parameter and is sent to every model. <span class="font-mono">On</span>/<span class="font-mono">Off</span> toggles internal reasoning on models that support it (qwen3, deepseek-r1, etc.). <span class="font-mono">Low</span>/<span class="font-mono">Medium</span>/<span class="font-mono">High</span> sets the reasoning effort on models that accept tiered effort. Models that don't honour the parameter silently ignore it.
        </p>
      </div>

      <details class="rounded-lg border border-border-subtle bg-[#0f1318] px-3 py-2">
        <summary class="cursor-pointer text-[11px] text-slate-400">Advanced</summary>
        <div class="mt-2">
          <label class="block text-xs font-medium text-slate-400 mb-1.5">Keep Alive</label>
          <input
            :value="draft.ollamaKeepAlive"
            @input="update('ollamaKeepAlive', $event.target.value)"
            type="text"
            :disabled="disabled || readOnly"
            placeholder="5m"
            class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
          />
          <p class="mt-1.5 text-[11px] leading-relaxed text-slate-500">
            How long Ollama keeps the model resident between calls. Use a Go duration like <span class="font-mono">5m</span> (default), <span class="font-mono">60m</span>, or <span class="font-mono">1h</span>. Plain integers are seconds — <span class="font-mono">-1</span> means never-unload, <span class="font-mono">0</span> means unload immediately.
          </p>
        </div>
      </details>
    </template>

    <!-- AWS Bedrock-specific configuration -->
    <template v-if="draft.provider === 'bedrock'">
      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">AWS Region</label>
        <input
          :value="draft.bedrockRegion"
          @input="update('bedrockRegion', $event.target.value)"
          type="text"
          list="bedrock-region-list"
          :disabled="disabled || readOnly"
          placeholder="us-east-1"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
        />
        <datalist id="bedrock-region-list">
          <option v-for="r in BEDROCK_COMMON_REGIONS" :key="r" :value="r" />
        </datalist>
        <p class="mt-1.5 text-[11px] leading-relaxed text-slate-500">
          Bedrock is regional — model availability varies by region. Required.
        </p>
      </div>

      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">
          API Key <span class="text-amber-400">(required — Bedrock API key / bearer token)</span>
        </label>
        <input
          :value="draft.apiKey"
          @input="update('apiKey', $event.target.value)"
          type="password"
          :disabled="disabled || readOnly"
          placeholder="Bedrock API key (bearer token)"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
        />
      </div>

      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5 flex items-center justify-between">
          <span>Pick from available models</span>
          <button
            type="button"
            @click="refreshBedrockModels"
            :disabled="disabled || readOnly || bedrockListLoading"
            class="text-[11px] text-primary hover:underline disabled:opacity-50"
          >{{ bedrockListLoading ? 'Loading…' : 'Refresh' }}</button>
        </label>
        <div v-if="bedrockListError" class="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11px] leading-relaxed text-amber-300 mb-2 space-y-1">
          <div>Couldn't list Bedrock models for this region / key.</div>
          <div class="text-amber-200/70 font-mono whitespace-pre-wrap break-words">{{ bedrockListError }}</div>
          <div class="text-amber-200/70">
            Bedrock API keys can be scoped without <span class="font-mono">bedrock:ListFoundationModels</span> — discovery may be unavailable even when the key can run Converse. Enter the model or inference-profile id manually in the Model field above (e.g. <span class="font-mono">us.anthropic.claude-3-5-sonnet-20241022-v2:0</span>).
          </div>
        </div>
        <select
          v-else
          :value="draft.model"
          @change="update('model', $event.target.value)"
          :disabled="disabled || readOnly || !bedrockModels.length"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors disabled:opacity-50"
        >
          <option value="" disabled>{{ bedrockModels.length ? 'Select a model' : 'No models — enter the model id manually above' }}</option>
          <option v-for="m in bedrockModels" :key="m.id" :value="m.id">
            {{ m.id }}<template v-if="m.kind === 'inference_profile'"> · profile</template><template v-if="m.provider_name"> — {{ m.provider_name }}</template>
          </option>
        </select>
        <p class="mt-1.5 text-[11px] leading-relaxed text-slate-500">
          The Model field above is the source of truth — picking just fills it in. Newer models often need a cross-region inference-profile id (the <span class="font-mono">us.</span>/<span class="font-mono">eu.</span> prefixed entries).
        </p>
      </div>

      <div>
        <label class="block text-xs font-medium text-slate-400 mb-1.5">Reasoning</label>
        <select
          :value="draft.bedrockReasoning"
          @change="update('bedrockReasoning', $event.target.value)"
          :disabled="disabled || readOnly"
          class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors disabled:opacity-50"
        >
          <option v-for="o in BEDROCK_REASONING_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
        <p class="mt-1.5 text-[11px] leading-relaxed text-slate-500">
          Sent via Converse. Anthropic Claude 3.7+ uses it as a thinking-token budget; OpenAI <span class="font-mono">gpt-oss</span> uses it as reasoning effort. Other families (Llama, Nova, Mistral) ignore it — leave <span class="font-mono">Off</span>.
        </p>
      </div>
    </template>

    <div v-if="['azure', 'openai', 'nvidia', 'claude-cli', 'codex-cli'].includes(draft.provider)">
      <label class="block text-xs font-medium text-slate-400 mb-1.5">Reasoning Effort</label>
      <select
        :value="draft.reasoningEffort"
        @change="update('reasoningEffort', $event.target.value)"
        :disabled="disabled || readOnly"
        class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary transition-colors disabled:opacity-50"
      >
        <option
          v-for="option in effortOptions"
          :key="option.value || 'default'"
          :value="option.value"
        >{{ option.label }}</option>
      </select>
      <p class="mt-1.5 text-[11px] leading-relaxed text-slate-500">
        <template v-if="draft.provider === 'nvidia'">
          Controls NVIDIA Super's reasoning mode. None disables thinking tokens, Low/Medium/High enable reasoning with increasing budget.
        </template>
        <template v-else-if="draft.provider === 'claude-cli'">
          Maps to <span class="font-mono">--effort</span> on the claude CLI. Higher levels let the model reason longer. Default leaves CC's behaviour unchanged.
        </template>
        <template v-else-if="draft.provider === 'codex-cli'">
          Reserved for Codex models that accept a reasoning_effort hint. Default leaves Codex's behaviour unchanged.
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

    <!-- Standard API-key field for non-CLI, non-Ollama, non-Bedrock providers. -->
    <!-- Ollama and Bedrock have their own API-key fields above. -->
    <div v-if="!isCliProvider && draft.provider !== 'ollama' && draft.provider !== 'bedrock'">
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
