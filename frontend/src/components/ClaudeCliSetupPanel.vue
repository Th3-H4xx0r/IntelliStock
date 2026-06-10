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
  cliPath: { type: String, default: 'claude' },
})

const status = ref({
  installed: false,
  version: null,
  authenticated: false,
  account: null,
  auth_message: '',
  loading: true,
  error: '',
})

const loginJob = ref({
  jobId: null,
  state: null,         // parsed | submitting | success | failed | cancelled
  loginUrl: '',
  code: '',            // operator-entered authorization code
  outputTail: '',
  error: '',
})

// Per-instance state (closures inside <script setup>, NOT module-level):
//   - _abortController cancels any in-flight fetches when the panel
//     unmounts so we don't mutate refs on a torn-down component.
//   - _mounted guards async-after-await paths from mutating refs after
//     onUnmounted has run.
// Unlike the Codex panel, Claude's OAuth is a synchronous paste-back flow
// (start → show URL → submit code → done), so there are no polling timers.
let _abortController = new AbortController()
let _mounted = true

function _fetchOpts(extra = {}) {
  return {
    ...extra,
    headers: { ...authHeaders(), ...(extra.headers || {}) },
    signal: _abortController.signal,
  }
}

// Frontend allowlist of Claude / Anthropic OAuth hosts — defence in depth,
// in case the backend ever returns a non-Anthropic URL we'd otherwise
// render as a clickable link.
const _ALLOWED_LOGIN_HOSTS = new Set([
  'claude.ai',
  'www.claude.ai',
  'claude.com',
  'www.claude.com',
  'console.anthropic.com',
  'anthropic.com',
])

function isSafeLoginUrl(u) {
  if (!u) return false
  try {
    const parsed = new URL(u)
    if (!['http:', 'https:'].includes(parsed.protocol)) return false
    if (parsed.username || parsed.password) return false
    return _ALLOWED_LOGIN_HOSTS.has(parsed.hostname.toLowerCase())
  } catch (e) {
    return false
  }
}

async function fetchStatus() {
  status.value.loading = true
  status.value.error = ''
  try {
    const url = `${API_BASE}/claude/auth/status?cli_path=${encodeURIComponent(props.cliPath || 'claude')}`
    const res = await fetch(url, _fetchOpts())
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    if (!_mounted) return
    status.value = {
      installed: !!data.installed,
      version: data.version || null,
      authenticated: !!data.authenticated,
      account: data.account || null,
      auth_message: data.auth_message || '',
      loading: false,
      error: '',
    }
  } catch (e) {
    if (e.name === 'AbortError' || !_mounted) return
    status.value.loading = false
    status.value.error = e.message || 'Status probe failed'
  }
}

async function startLogin() {
  loginJob.value = {
    jobId: null, state: 'parsed',
    loginUrl: '', code: '', outputTail: '', error: '',
  }
  try {
    const res = await fetch(`${API_BASE}/claude/login/start`, _fetchOpts({
      method: 'POST',
      body: JSON.stringify({ cli_path: props.cliPath || 'claude' }),
    }))
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    if (!_mounted) return
    loginJob.value.jobId = data.job_id
    loginJob.value.state = data.state || 'parsed'
    // Validate the login URL host before surfacing it as a clickable link.
    const incomingUrl = data.login_url || ''
    loginJob.value.loginUrl = isSafeLoginUrl(incomingUrl) ? incomingUrl : ''
    if (incomingUrl && !loginJob.value.loginUrl) {
      // Keep the raw text available so the operator can still copy it, but
      // never render an unverified host as an <a href>.
      loginJob.value.loginUrl = ''
      loginJob.value.rawLoginUrl = incomingUrl
    }
    loginJob.value.error = data.error || ''
    if (data.state === 'failed' && !loginJob.value.error) {
      loginJob.value.error = 'claude did not return a login URL'
    }
  } catch (e) {
    if (e.name === 'AbortError' || !_mounted) return
    loginJob.value.state = 'failed'
    loginJob.value.error = e.message || 'Login start failed'
  }
}

async function submitCode() {
  const jobId = loginJob.value.jobId
  const code = (loginJob.value.code || '').trim()
  if (!jobId || !code) return
  loginJob.value.state = 'submitting'
  loginJob.value.error = ''
  try {
    const res = await fetch(
      `${API_BASE}/claude/login/${encodeURIComponent(jobId)}/submit`,
      _fetchOpts({ method: 'POST', body: JSON.stringify({ code }) }),
    )
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    if (!_mounted) return
    loginJob.value.state = data.state || 'failed'
    loginJob.value.outputTail = data.output_tail || ''
    loginJob.value.error = data.error || ''
    if (data.state === 'success') {
      await fetchStatus()
    } else if (!loginJob.value.error) {
      loginJob.value.error = 'Login failed — check the code and try again.'
    }
  } catch (e) {
    if (e.name === 'AbortError' || !_mounted) return
    loginJob.value.state = 'failed'
    loginJob.value.error = e.message || 'Code submission failed'
  }
}

async function cancelLogin() {
  const jobId = loginJob.value.jobId
  if (!jobId) return
  try {
    await fetch(
      `${API_BASE}/claude/login/${encodeURIComponent(jobId)}/cancel`,
      _fetchOpts({ method: 'POST' }),
    )
  } catch (e) {
    // Soft-fail — UI just reflects the cancelled state regardless.
  }
  if (!_mounted) return
  loginJob.value.state = 'cancelled'
}

async function logout() {
  if (!confirm('Sign out of Claude on this host? All models using claude-cli will need to re-authenticate.')) return
  try {
    const res = await fetch(`${API_BASE}/claude/logout`, _fetchOpts({
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

const _LIVE_STATES = ['parsed', 'submitting']

onMounted(fetchStatus)
onUnmounted(() => {
  _mounted = false
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
      <div class="font-semibold text-slate-200">Claude Code CLI setup</div>
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
    <div v-if="status.loading" class="text-slate-500">Probing claude CLI status...</div>
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
      <span v-if="status.installed && status.authenticated && status.account">
        <span class="text-slate-500">account:</span>
        <span class="font-mono text-slate-200">{{ status.account }}</span>
      </span>
    </div>

    <!-- Not installed -->
    <div v-if="!status.loading && !status.installed && !status.error" class="space-y-1.5">
      <p class="text-amber-300">
        The <span class="font-mono">claude</span> binary was not found on the server.
      </p>
      <p class="text-slate-400">
        Install the Claude Code CLI on the backend host (it must be on PATH or
        set the CLI Path field above to its absolute path), then refresh this
        panel to sign in.
      </p>
    </div>

    <!-- Installed but not authenticated → two-step paste-back login -->
    <div v-if="!status.loading && status.installed && !status.authenticated && !status.error" class="space-y-2">
      <p class="text-slate-400">
        Claude is installed but not authenticated. Start the Claude subscription
        sign-in below — you'll get a link to open in your browser, then paste the
        authorization code back here to finish.
      </p>
      <div class="flex flex-wrap items-center gap-2">
        <button
          @click="startLogin"
          :disabled="_LIVE_STATES.includes(loginJob.state)"
          class="px-3 py-1.5 rounded-md bg-primary text-background-dark text-xs font-bold hover:brightness-110 transition-all disabled:opacity-60"
        >
          <template v-if="loginJob.state === 'submitting'">Verifying...</template>
          <template v-else-if="loginJob.state === 'parsed'">Sign-in in progress...</template>
          <template v-else>Re-authenticate Claude</template>
        </button>
        <button
          v-if="_LIVE_STATES.includes(loginJob.state)"
          @click="cancelLogin"
          class="px-2 py-1 rounded-md border border-border-subtle text-slate-400 hover:text-slate-200 text-xs"
        >
          Cancel
        </button>
      </div>

      <!-- Step-by-step login flow -->
      <div v-if="loginJob.state && loginJob.state !== 'failed'" class="space-y-2">
        <div v-if="loginJob.loginUrl" class="space-y-1.5">
          <div>
            <span class="text-slate-500">1. Open this link and sign in:</span>
            <a :href="loginJob.loginUrl" target="_blank" rel="noopener noreferrer"
              class="ml-1 font-mono text-sky-300 underline break-all">{{ loginJob.loginUrl }}</a>
            <button
              @click="copyToClipboard(loginJob.loginUrl)"
              class="ml-2 text-slate-500 hover:text-slate-300"
              title="Copy URL"
            >
              <span class="material-symbols-outlined text-base align-middle">content_copy</span>
            </button>
          </div>
          <div class="text-slate-500">2. Copy the authorization code shown after you sign in.</div>
          <div class="text-slate-500">3. Paste it below:</div>
        </div>
        <!-- Unsafe host: show the raw text only, never a clickable link. -->
        <div v-else-if="loginJob.rawLoginUrl" class="space-y-1.5">
          <div class="text-amber-300">
            claude returned a login URL on an unrecognised host — open it
            manually (not rendered as a link for safety):
          </div>
          <div class="font-mono text-slate-300 break-all bg-black/40 rounded px-2 py-1">{{ loginJob.rawLoginUrl }}</div>
          <div class="text-slate-500">Then copy the authorization code and paste it below.</div>
        </div>

        <div v-if="loginJob.loginUrl || loginJob.rawLoginUrl || loginJob.jobId" class="flex flex-wrap items-center gap-2">
          <input
            v-model="loginJob.code"
            type="text"
            placeholder="Paste authorization code"
            :disabled="loginJob.state === 'submitting' || loginJob.state === 'success'"
            @keyup.enter="submitCode"
            class="flex-1 min-w-[200px] bg-surface border border-border-subtle rounded-md px-3 py-2 text-xs text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
          />
          <button
            @click="submitCode"
            :disabled="!loginJob.code.trim() || loginJob.state === 'submitting' || loginJob.state === 'success'"
            class="px-3 py-2 rounded-md bg-primary text-background-dark text-xs font-bold hover:brightness-110 transition-all disabled:opacity-60 inline-flex items-center gap-1.5"
          >
            <span v-if="loginJob.state === 'submitting'" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
            <template v-if="loginJob.state === 'submitting'">Submitting...</template>
            <template v-else>Submit code</template>
          </button>
        </div>
        <p v-if="loginJob.state === 'submitting'" class="text-slate-500">
          Exchanging the code with Claude — this can take up to ~30 seconds.
        </p>

        <div v-if="loginJob.state === 'success'" class="rounded-md border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-emerald-300">
          ✓ Claude re-authenticated.
        </div>
      </div>

      <div v-if="loginJob.error" class="rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-red-300 space-y-1">
        <div>{{ loginJob.error }}</div>
        <pre v-if="loginJob.outputTail"
          class="font-mono text-[10px] text-red-200/80 bg-black/30 rounded px-2 py-1 max-h-40 overflow-auto whitespace-pre-wrap break-all">{{ loginJob.outputTail }}</pre>
      </div>
    </div>

    <!-- Authenticated → logout panel -->
    <div v-if="!status.loading && status.installed && status.authenticated && !status.error" class="space-y-2">
      <p class="text-emerald-300">
        ✓ Claude Code CLI is installed and authenticated. Models can now use <span class="font-mono">claude-cli</span> as their provider.
      </p>
      <p v-if="status.auth_message" class="text-slate-500">
        {{ status.auth_message }}
      </p>
      <div class="flex flex-wrap items-center gap-2">
        <button
          @click="startLogin"
          :disabled="_LIVE_STATES.includes(loginJob.state)"
          class="px-3 py-1.5 rounded-md border border-border-subtle text-slate-300 hover:text-primary hover:border-primary/40 text-xs font-medium transition-colors disabled:opacity-60"
        >
          Re-authenticate Claude
        </button>
        <button
          @click="logout"
          class="px-3 py-1.5 rounded-md border border-border-subtle text-slate-300 hover:text-red-300 hover:border-red-500/30 text-xs font-medium transition-colors"
        >
          Sign out of Claude
        </button>
      </div>

      <!-- Re-auth flow when already authenticated (operator forcing a refresh
           of an expired/expiring token). Mirrors the not-authenticated flow. -->
      <div v-if="loginJob.state && loginJob.state !== 'failed'" class="space-y-2 pt-1">
        <div v-if="loginJob.loginUrl" class="space-y-1.5">
          <div>
            <span class="text-slate-500">1. Open this link and sign in:</span>
            <a :href="loginJob.loginUrl" target="_blank" rel="noopener noreferrer"
              class="ml-1 font-mono text-sky-300 underline break-all">{{ loginJob.loginUrl }}</a>
            <button
              @click="copyToClipboard(loginJob.loginUrl)"
              class="ml-2 text-slate-500 hover:text-slate-300"
              title="Copy URL"
            >
              <span class="material-symbols-outlined text-base align-middle">content_copy</span>
            </button>
          </div>
          <div class="text-slate-500">2. Copy the authorization code shown after you sign in.</div>
          <div class="text-slate-500">3. Paste it below:</div>
        </div>
        <div v-else-if="loginJob.rawLoginUrl" class="space-y-1.5">
          <div class="text-amber-300">
            claude returned a login URL on an unrecognised host — open it
            manually (not rendered as a link for safety):
          </div>
          <div class="font-mono text-slate-300 break-all bg-black/40 rounded px-2 py-1">{{ loginJob.rawLoginUrl }}</div>
          <div class="text-slate-500">Then copy the authorization code and paste it below.</div>
        </div>

        <div v-if="loginJob.loginUrl || loginJob.rawLoginUrl || loginJob.jobId" class="flex flex-wrap items-center gap-2">
          <input
            v-model="loginJob.code"
            type="text"
            placeholder="Paste authorization code"
            :disabled="loginJob.state === 'submitting' || loginJob.state === 'success'"
            @keyup.enter="submitCode"
            class="flex-1 min-w-[200px] bg-surface border border-border-subtle rounded-md px-3 py-2 text-xs text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary transition-colors font-mono disabled:opacity-50"
          />
          <button
            @click="submitCode"
            :disabled="!loginJob.code.trim() || loginJob.state === 'submitting' || loginJob.state === 'success'"
            class="px-3 py-2 rounded-md bg-primary text-background-dark text-xs font-bold hover:brightness-110 transition-all disabled:opacity-60 inline-flex items-center gap-1.5"
          >
            <span v-if="loginJob.state === 'submitting'" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
            <template v-if="loginJob.state === 'submitting'">Submitting...</template>
            <template v-else>Submit code</template>
          </button>
          <button
            v-if="_LIVE_STATES.includes(loginJob.state)"
            @click="cancelLogin"
            class="px-2 py-1 rounded-md border border-border-subtle text-slate-400 hover:text-slate-200 text-xs"
          >
            Cancel
          </button>
        </div>
        <p v-if="loginJob.state === 'submitting'" class="text-slate-500">
          Exchanging the code with Claude — this can take up to ~30 seconds.
        </p>
        <div v-if="loginJob.state === 'success'" class="rounded-md border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-emerald-300">
          ✓ Claude re-authenticated.
        </div>
      </div>

      <div v-if="loginJob.error" class="rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-red-300 space-y-1">
        <div>{{ loginJob.error }}</div>
        <pre v-if="loginJob.outputTail"
          class="font-mono text-[10px] text-red-200/80 bg-black/30 rounded px-2 py-1 max-h-40 overflow-auto whitespace-pre-wrap break-all">{{ loginJob.outputTail }}</pre>
      </div>
    </div>
  </div>
</template>
