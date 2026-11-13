<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

const props = defineProps({
  cliPath: { type: String, default: 'codex' },
})

const status = ref({
  installed: false,
  version: null,
  authenticated: false,
  auth_message: '',
  install_method: 'unknown',
  loading: true,
  error: '',
})

const installJob = ref({
  jobId: null,
  state: null,         // running | success | failed
  exitCode: null,
  log: [],
  error: '',
})

const loginJob = ref({
  jobId: null,
  state: null,         // pending | parsed | success | failed | expired | cancelled
  pairingUrl: '',
  pairingCode: '',
  error: '',
})

// Per-instance state (closures inside <script setup>, NOT module-level):
//   - _installTimer / _loginTimer are split so the install poll can't
//     cancel a concurrent login poll (and vice versa).
//   - _abortController cancels any in-flight fetches when the panel
//     unmounts so we don't mutate refs on a torn-down component.
//   - _mounted guards reschedule-after-await paths from resurrecting
//     timers after onUnmounted has cleared them.
let _installTimer = null
let _loginTimer = null
let _abortController = new AbortController()
let _mounted = true

function _fetchOpts(extra = {}) {
  return {
    ...extra,
    headers: { ...authHeaders(), ...(extra.headers || {}) },
    signal: _abortController.signal,
  }
}

// Frontend allowlist of OpenAI hosts — defence in depth, in case the
// backend regex is ever loosened and a non-OpenAI URL leaks through.
const _ALLOWED_PAIRING_HOSTS = new Set([
  'chatgpt.com',
  'platform.openai.com',
  'auth.openai.com',
])

function isSafePairingUrl(u) {
  if (!u) return false
  try {
    const parsed = new URL(u)
    if (!['http:', 'https:'].includes(parsed.protocol)) return false
    if (parsed.username || parsed.password) return false
    return _ALLOWED_PAIRING_HOSTS.has(parsed.hostname.toLowerCase())
  } catch (e) {
    return false
  }
}

async function fetchStatus() {
  status.value.loading = true
  status.value.error = ''
  try {
    const res = await fetch(`${API_BASE}/codex/status`, _fetchOpts())
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    if (!_mounted) return
    status.value = {
      installed: !!data.installed,
      version: data.version || null,
      authenticated: !!data.authenticated,
      auth_message: data.auth_message || '',
      install_method: data.install_method || 'unknown',
      loading: false,
      error: '',
    }
  } catch (e) {
    if (e.name === 'AbortError' || !_mounted) return
    status.value.loading = false
    status.value.error = e.message || 'Status probe failed'
  }
}

async function startInstall() {
  installJob.value = { jobId: null, state: 'running', exitCode: null, log: [], error: '' }
  try {
    const res = await fetch(`${API_BASE}/codex/install`, _fetchOpts({
      method: 'POST',
      body: JSON.stringify({}),
    }))
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    if (!_mounted) return
    installJob.value.jobId = data.job_id
    installJob.value.state = data.state || 'running'
    _scheduleInstallPoll()
  } catch (e) {
    if (e.name === 'AbortError' || !_mounted) return
    installJob.value.state = 'failed'
    installJob.value.error = e.message || 'Install start failed'
  }
}

function _scheduleInstallPoll() {
  if (!_mounted) return
  if (_installTimer) clearTimeout(_installTimer)
  _installTimer = setTimeout(() => {
    _installTimer = null
    pollInstall()
  }, 1500)
}

async function pollInstall() {
  const jobId = installJob.value.jobId
  if (!jobId || !_mounted) return
  try {
    const res = await fetch(
      `${API_BASE}/codex/install/${encodeURIComponent(jobId)}`,
      _fetchOpts(),
    )
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    if (!_mounted) return
    installJob.value.state = data.state
    installJob.value.exitCode = data.exit_code
    installJob.value.log = data.log_tail || []
    installJob.value.error = data.error || ''
    if (data.state === 'running') {
      _scheduleInstallPoll()
    } else {
      await fetchStatus()
    }
  } catch (e) {
    if (e.name === 'AbortError' || !_mounted) return
    installJob.value.state = 'failed'
    installJob.value.error = e.message || 'Install poll failed'
  }
}

async function startLogin() {
  loginJob.value = {
    jobId: null, state: 'pending',
    pairingUrl: '', pairingCode: '', error: '',
  }
  try {
    const res = await fetch(`${API_BASE}/codex/login/start`, _fetchOpts({
      method: 'POST',
      body: JSON.stringify({ cli_path: props.cliPath || 'codex' }),
    }))
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    if (!_mounted) return
    loginJob.value.jobId = data.job_id
    loginJob.value.state = data.state || 'pending'
    // Validate pairing URL host before surfacing to the operator.
    const incomingUrl = data.pairing_url || ''
    loginJob.value.pairingUrl = isSafePairingUrl(incomingUrl) ? incomingUrl : ''
    if (incomingUrl && !loginJob.value.pairingUrl) {
      loginJob.value.error = 'codex returned a non-OpenAI pairing URL; ignoring for safety'
    }
    loginJob.value.pairingCode = data.pairing_code || ''
    if (!loginJob.value.error) loginJob.value.error = data.error || ''
    _scheduleLoginPoll()
  } catch (e) {
    if (e.name === 'AbortError' || !_mounted) return
    loginJob.value.state = 'failed'
    loginJob.value.error = e.message || 'Login start failed'
  }
}

function _scheduleLoginPoll() {
  if (!_mounted) return
  if (_loginTimer) clearTimeout(_loginTimer)
  _loginTimer = setTimeout(() => {
    _loginTimer = null
    pollLogin()
  }, 2000)
}

async function pollLogin() {
  const jobId = loginJob.value.jobId
  if (!jobId || !_mounted) return
  try {
    const res = await fetch(
      `${API_BASE}/codex/login/${encodeURIComponent(jobId)}/status`,
      _fetchOpts(),
    )
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    if (!_mounted) return
    loginJob.value.state = data.state
    if (data.pairing_url && isSafePairingUrl(data.pairing_url)) {
      loginJob.value.pairingUrl = data.pairing_url
    }
    loginJob.value.pairingCode = data.pairing_code || loginJob.value.pairingCode
    loginJob.value.error = data.error || ''
    if (['pending', 'parsed'].includes(data.state)) {
      _scheduleLoginPoll()
    } else {
      await fetchStatus()
    }
  } catch (e) {
    if (e.name === 'AbortError' || !_mounted) return
    loginJob.value.state = 'failed'
    loginJob.value.error = e.message || 'Login poll failed'
  }
}

async function cancelLogin() {
  const jobId = loginJob.value.jobId
  if (!jobId) return
  try {
    await fetch(
      `${API_BASE}/codex/login/${encodeURIComponent(jobId)}/cancel`,
      _fetchOpts({ method: 'POST' }),
    )
  } catch (e) {
    // Soft-fail — UI just reflects whatever the next poll reports.
  }
  loginJob.value.state = 'cancelled'
}

async function logout() {
  if (!confirm('Sign out of OpenAI Codex on this host? All strategies using codex-cli will need to re-authenticate.')) return
  try {
    const res = await fetch(`${API_BASE}/codex/logout`, _fetchOpts({
      method: 'POST',
    }))
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
  } catch (e) {
    if (e.name !== 'AbortError') alert(`Logout failed: ${e.message || e}`)
  } finally {
    await fetchStatus()
  }
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text)
  } catch (e) {
    // ignore — older browsers / insecure context
  }
}

onMounted(fetchStatus)
onUnmounted(() => {
  _mounted = false
  if (_installTimer) clearTimeout(_installTimer)
  if (_loginTimer) clearTimeout(_loginTimer)
  try {
    _abortController.abort()
  } catch (e) {
    // ignore
  }
})
</script>

<template>
  <div class="rounded-lg border border-white/[0.07] px-4 py-3 space-y-3 text-xs leading-relaxed text-slate-300">
    <div class="flex items-center justify-between gap-3">
      <div class="text-sm font-medium text-slate-200">OpenAI Codex CLI setup</div>
      <button
        @click="fetchStatus"
        :disabled="status.loading"
        class="ui-icon-btn -mr-2 disabled:opacity-40"
        title="Refresh status"
        aria-label="Refresh status"
      >
        <span class="material-symbols-outlined text-[18px]" :class="{ 'animate-spin': status.loading }">refresh</span>
      </button>
    </div>

    <!-- Status row -->
    <div v-if="status.loading" class="text-slate-500">Probing codex CLI status...</div>
    <div v-else-if="status.error" class="text-loss">Status probe failed: {{ status.error }}</div>
    <div v-else class="flex flex-wrap gap-x-5 gap-y-1">
      <span class="inline-flex items-center gap-1.5">
        <span class="text-slate-500">Installed</span>
        <span class="ui-dot" :class="status.installed ? 'bg-gain' : 'ui-dot-warn'"></span>
        <span class="text-slate-200">{{ status.installed ? 'Yes' : 'No' }}</span>
      </span>
      <span v-if="status.installed" class="inline-flex items-center gap-1.5">
        <span class="text-slate-500">Version</span>
        <span class="font-mono text-slate-200">{{ status.version || 'unknown' }}</span>
      </span>
      <span v-if="status.installed" class="inline-flex items-center gap-1.5">
        <span class="text-slate-500">Authenticated</span>
        <span class="ui-dot" :class="status.authenticated ? 'bg-gain' : 'ui-dot-warn'"></span>
        <span class="text-slate-200">{{ status.authenticated ? 'Yes' : 'No' }}</span>
      </span>
      <span v-else class="inline-flex items-center gap-1.5">
        <span class="text-slate-500">Install method</span>
        <span class="font-mono text-slate-200">{{ status.install_method }}</span>
      </span>
    </div>

    <!-- Install panel -->
    <div v-if="!status.loading && !status.installed && !status.error" class="space-y-3">
      <p class="text-slate-400">
        Codex CLI is not installed on the server. One-click install uses
        <span class="font-mono text-slate-300">{{ status.install_method === 'brew' ? 'brew install codex' : 'npm install -g @openai/codex' }}</span>.
      </p>
      <div v-if="status.install_method === 'unknown'" class="space-y-1.5">
        <p class="flex items-start gap-2 text-slate-200">
          <span class="ui-dot ui-dot-warn mt-1.5"></span>
          <span>
            The IntelliStock backend container has neither
            <span class="font-mono">npm</span> nor <span class="font-mono">brew</span>
            available — it was likely built with
            <span class="font-mono">INSTALL_CODEX_CLI=0</span>.
          </span>
        </p>
        <p class="text-slate-400">
          Rebuild the backend image with
          <span class="font-mono">INSTALL_CODEX_CLI=1</span> (the default in
          recent versions). Quick command on the host:
        </p>
        <pre class="font-mono text-[11px] text-slate-300 bg-background-dark rounded-md px-3 py-2 whitespace-pre-wrap break-all">docker compose build --build-arg INSTALL_CODEX_CLI=1 backend &amp;&amp; docker compose up -d backend api</pre>
        <p class="text-slate-500">
          Once codex is baked into the image, this panel will skip straight
          to the OpenAI device-code login. Credentials are written to a
          Docker named volume (<span class="font-mono">codex_auth</span>),
          never the host filesystem.
        </p>
      </div>
      <button
        v-else
        @click="startInstall"
        :disabled="installJob.state === 'running'"
        class="ui-btn"
      >
        <template v-if="installJob.state === 'running'">Installing...</template>
        <template v-else>Install Codex CLI</template>
      </button>
      <div v-if="installJob.state" class="space-y-1.5">
        <div class="flex flex-wrap items-center gap-1.5 text-slate-400">
          Install state
          <span class="ui-dot" :class="installJob.state === 'success' ? 'bg-gain' : (installJob.state === 'failed' ? 'ui-dot-error' : 'ui-dot-warn')"></span>
          <span class="text-slate-200">{{ installJob.state }}</span>
          <span v-if="installJob.exitCode != null" class="text-slate-500 tabular-nums">(exit {{ installJob.exitCode }})</span>
        </div>
        <pre v-if="installJob.log && installJob.log.length"
          class="font-mono text-[11px] text-slate-300 bg-background-dark rounded-md px-3 py-2 max-h-40 overflow-auto whitespace-pre-wrap break-all">{{ installJob.log.join('\n') }}</pre>
        <div v-if="installJob.error" class="text-loss">{{ installJob.error }}</div>
      </div>
    </div>

    <!-- Login panel -->
    <div v-if="!status.loading && status.installed && !status.authenticated && !status.error" class="space-y-3">
      <p class="text-slate-400">
        Codex is installed but not authenticated. Start the OpenAI device-code
        login below — you'll get a pairing URL and code to enter on
        chatgpt.com to grant this server access to your subscription.
      </p>
      <div class="flex flex-wrap items-center gap-2">
        <button
          @click="startLogin"
          :disabled="['pending', 'parsed'].includes(loginJob.state)"
          class="ui-btn"
        >
          <template v-if="['pending', 'parsed'].includes(loginJob.state)">Waiting for sign-in...</template>
          <template v-else>Sign in with OpenAI</template>
        </button>
        <button
          v-if="['pending', 'parsed'].includes(loginJob.state)"
          @click="cancelLogin"
          class="ui-btn-ghost text-[13px]"
        >
          Cancel
        </button>
      </div>

      <div v-if="loginJob.state && loginJob.state !== 'pending'" class="space-y-2">
        <div class="flex flex-wrap items-center gap-1.5 text-slate-400">
          Login state
          <span class="ui-dot" :class="loginJob.state === 'success' ? 'bg-gain' : ['failed', 'expired', 'cancelled'].includes(loginJob.state) ? 'ui-dot-error' : 'ui-dot-warn'"></span>
          <span class="text-slate-200">{{ loginJob.state }}</span>
        </div>
        <div v-if="loginJob.pairingUrl" class="space-y-1.5">
          <div class="flex flex-wrap items-center gap-x-1">
            <span class="text-slate-500">Open this URL:</span>
            <a :href="loginJob.pairingUrl" target="_blank" rel="noopener"
              class="font-mono text-slate-200 underline decoration-slate-600 underline-offset-2 hover:decoration-slate-300 break-all transition-colors duration-150 ease-out">{{ loginJob.pairingUrl }}</a>
            <button
              @click="copyToClipboard(loginJob.pairingUrl)"
              class="ui-icon-btn size-7"
              title="Copy URL"
              aria-label="Copy URL"
            >
              <span class="material-symbols-outlined text-[16px]">content_copy</span>
            </button>
          </div>
          <div v-if="loginJob.pairingCode" class="flex flex-wrap items-center gap-x-1">
            <span class="text-slate-500">Enter this code:</span>
            <span class="font-mono text-base text-slate-100">{{ loginJob.pairingCode }}</span>
            <button
              @click="copyToClipboard(loginJob.pairingCode)"
              class="ui-icon-btn size-7"
              title="Copy code"
              aria-label="Copy code"
            >
              <span class="material-symbols-outlined text-[16px]">content_copy</span>
            </button>
          </div>
        </div>
        <div v-if="loginJob.error" class="text-loss">{{ loginJob.error }}</div>
      </div>
    </div>

    <!-- Authenticated → logout panel -->
    <div v-if="!status.loading && status.installed && status.authenticated && !status.error" class="space-y-3">
      <p class="text-slate-300">
        Codex CLI is installed and authenticated. Strategies can now select <span class="font-mono">codex-cli</span> as their provider.
      </p>
      <p v-if="status.auth_message" class="text-slate-500">
        {{ status.auth_message }}
      </p>
      <button
        @click="logout"
        class="ui-btn"
      >
        Sign out of OpenAI
      </button>
    </div>
  </div>
</template>
