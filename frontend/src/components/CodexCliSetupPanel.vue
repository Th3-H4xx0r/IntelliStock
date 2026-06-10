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
  <div class="rounded-lg border border-sky-500/20 bg-sky-500/5 px-3 py-3 space-y-3 text-[11px] leading-relaxed text-slate-300">
    <div class="flex items-center justify-between">
      <div class="font-semibold text-slate-200">OpenAI Codex CLI setup</div>
      <button
        @click="fetchStatus"
        :disabled="status.loading"
        class="text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40"
        title="Refresh status"
      >
        <span class="material-symbols-outlined text-base" :class="{ 'animate-spin': status.loading }">refresh</span>
      </button>
    </div>

    <!-- Status row -->
    <div v-if="status.loading" class="text-slate-500">Probing codex CLI status...</div>
    <div v-else-if="status.error" class="text-red-300">Status probe failed: {{ status.error }}</div>
    <div v-else class="flex flex-wrap gap-x-4 gap-y-1">
      <span>
        <span class="text-slate-500">installed:</span>
        <span :class="status.installed ? 'text-emerald-300' : 'text-amber-300'">
          {{ status.installed ? '✓ yes' : '✗ no' }}
        </span>
      </span>
      <span v-if="status.installed">
        <span class="text-slate-500">version:</span>
        <span class="font-mono text-slate-200">{{ status.version || 'unknown' }}</span>
      </span>
      <span v-if="status.installed">
        <span class="text-slate-500">authenticated:</span>
        <span :class="status.authenticated ? 'text-emerald-300' : 'text-amber-300'">
          {{ status.authenticated ? '✓ yes' : '✗ no' }}
        </span>
      </span>
      <span v-else>
        <span class="text-slate-500">install method:</span>
        <span class="font-mono text-slate-200">{{ status.install_method }}</span>
      </span>
    </div>

    <!-- Install panel -->
    <div v-if="!status.loading && !status.installed && !status.error" class="space-y-2">
      <p class="text-slate-400">
        Codex CLI is not installed on the server. One-click install uses
        <span class="font-mono">{{ status.install_method === 'brew' ? 'brew install codex' : 'npm install -g @openai/codex' }}</span>.
      </p>
      <div v-if="status.install_method === 'unknown'" class="space-y-1.5">
        <p class="text-amber-300">
          The IntelliStock backend container has neither
          <span class="font-mono">npm</span> nor <span class="font-mono">brew</span>
          available — it was likely built with
          <span class="font-mono">INSTALL_CODEX_CLI=0</span>.
        </p>
        <p class="text-slate-400">
          Rebuild the backend image with
          <span class="font-mono">INSTALL_CODEX_CLI=1</span> (the default in
          recent versions). Quick command on the host:
        </p>
        <pre class="font-mono text-[10px] text-slate-200 bg-black/40 rounded px-2 py-1 whitespace-pre-wrap break-all">docker compose build --build-arg INSTALL_CODEX_CLI=1 backend &amp;&amp; docker compose up -d backend api</pre>
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
        class="px-3 py-1.5 rounded-md bg-primary text-background-dark text-xs font-bold hover:brightness-110 transition-all disabled:opacity-60"
      >
        <template v-if="installJob.state === 'running'">Installing...</template>
        <template v-else>Install Codex CLI</template>
      </button>
      <div v-if="installJob.state" class="space-y-1">
        <div class="text-slate-400">
          Install state:
          <span :class="installJob.state === 'success' ? 'text-emerald-300' : (installJob.state === 'failed' ? 'text-red-300' : 'text-amber-300')">
            {{ installJob.state }}
          </span>
          <span v-if="installJob.exitCode != null" class="text-slate-500">(exit {{ installJob.exitCode }})</span>
        </div>
        <pre v-if="installJob.log && installJob.log.length"
          class="font-mono text-[10px] text-slate-200 bg-black/40 rounded px-2 py-1 max-h-40 overflow-auto whitespace-pre-wrap break-all">{{ installJob.log.join('\n') }}</pre>
        <div v-if="installJob.error" class="text-red-300">{{ installJob.error }}</div>
      </div>
    </div>

    <!-- Login panel -->
    <div v-if="!status.loading && status.installed && !status.authenticated && !status.error" class="space-y-2">
      <p class="text-slate-400">
        Codex is installed but not authenticated. Click below to start the OpenAI
        device-code login — you'll be given a pairing URL and code to enter on
        chatgpt.com to grant this server access to your subscription.
      </p>
      <div class="flex flex-wrap items-center gap-2">
        <button
          @click="startLogin"
          :disabled="['pending', 'parsed'].includes(loginJob.state)"
          class="px-3 py-1.5 rounded-md bg-primary text-background-dark text-xs font-bold hover:brightness-110 transition-all disabled:opacity-60"
        >
          <template v-if="['pending', 'parsed'].includes(loginJob.state)">Waiting for sign-in...</template>
          <template v-else>Sign in with OpenAI</template>
        </button>
        <button
          v-if="['pending', 'parsed'].includes(loginJob.state)"
          @click="cancelLogin"
          class="px-2 py-1 rounded-md border border-border-subtle text-slate-400 hover:text-slate-200 text-xs"
        >
          Cancel
        </button>
      </div>

      <div v-if="loginJob.state && loginJob.state !== 'pending'" class="space-y-1.5">
        <div class="text-slate-400">
          Login state:
          <span :class="loginJob.state === 'success' ? 'text-emerald-300' : ['failed', 'expired', 'cancelled'].includes(loginJob.state) ? 'text-red-300' : 'text-amber-300'">
            {{ loginJob.state }}
          </span>
        </div>
        <div v-if="loginJob.pairingUrl" class="space-y-1">
          <div>
            <span class="text-slate-500">Open this URL:</span>
            <a :href="loginJob.pairingUrl" target="_blank" rel="noopener"
              class="ml-1 font-mono text-sky-300 underline break-all">{{ loginJob.pairingUrl }}</a>
            <button
              @click="copyToClipboard(loginJob.pairingUrl)"
              class="ml-2 text-slate-500 hover:text-slate-300"
              title="Copy URL"
            >
              <span class="material-symbols-outlined text-base align-middle">content_copy</span>
            </button>
          </div>
          <div v-if="loginJob.pairingCode">
            <span class="text-slate-500">Enter this code:</span>
            <span class="ml-1 font-mono text-emerald-300 text-base tracking-widest">{{ loginJob.pairingCode }}</span>
            <button
              @click="copyToClipboard(loginJob.pairingCode)"
              class="ml-2 text-slate-500 hover:text-slate-300"
              title="Copy code"
            >
              <span class="material-symbols-outlined text-base align-middle">content_copy</span>
            </button>
          </div>
        </div>
        <div v-if="loginJob.error" class="text-red-300">{{ loginJob.error }}</div>
      </div>
    </div>

    <!-- Authenticated → logout panel -->
    <div v-if="!status.loading && status.installed && status.authenticated && !status.error" class="space-y-2">
      <p class="text-emerald-300">
        ✓ Codex CLI is installed and authenticated. Strategies can now select <span class="font-mono">codex-cli</span> as their provider.
      </p>
      <p v-if="status.auth_message" class="text-slate-500">
        {{ status.auth_message }}
      </p>
      <button
        @click="logout"
        class="px-3 py-1.5 rounded-md border border-border-subtle text-slate-300 hover:text-red-300 hover:border-red-500/30 text-xs font-medium transition-colors"
      >
        Sign out of OpenAI
      </button>
    </div>
  </div>
</template>
