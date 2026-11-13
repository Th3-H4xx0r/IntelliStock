<script setup>
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { login, createFirstAccount, isAuthenticated } from '../utils/auth.js'

const router   = useRouter()
const route    = useRoute()
const username = ref('')
const password = ref('')
const showPw   = ref(false)
const loading  = ref(false)
const error    = ref('')

// First-run bootstrap. Only ever turned on by the server's own "no_users"
// signal on a login refusal — never guessed at, so a normal deployment never
// shows a way to create an account without signing in.
const firstRun = ref(false)
const confirmPassword = ref('')

// Redirect already-authenticated users
onMounted(() => {
  if (isAuthenticated()) router.replace('/dashboard')
})

function goAfterAuth() {
  // Open-redirect guard: only honour same-origin path-style redirects.
  // Reject protocol-relative (//attacker.com), userinfo paths (/@evil.com),
  // backslash-prefixed paths (/\evil.com), and any absolute URLs.
  const requested = route.query.redirect
  const safe = (typeof requested === 'string'
                && requested.startsWith('/')
                && !requested.startsWith('//')
                && !requested.startsWith('/\\')
                && !requested.includes('@'))
               ? requested
               : '/dashboard'
  router.push(safe)
}

async function submit() {
  error.value = ''
  if (!username.value.trim() || !password.value) {
    error.value = 'Please enter your username and password.'
    return
  }
  loading.value = true
  try {
    await login(username.value.trim(), password.value)
    goAfterAuth()
  } catch (e) {
    if (e.noUsers) {
      firstRun.value = true
      error.value = ''
    } else {
      error.value = e.message
    }
  } finally {
    loading.value = false
  }
}

async function createAccount() {
  error.value = ''
  const name = username.value.trim()
  if (!name) {
    error.value = 'Choose a username.'
    return
  }
  if (password.value.length < 8) {
    error.value = 'Password must be at least 8 characters.'
    return
  }
  if (password.value !== confirmPassword.value) {
    error.value = 'The two passwords do not match.'
    return
  }
  loading.value = true
  try {
    await createFirstAccount(name, password.value)
    goAfterAuth()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen bg-background-dark text-slate-100 flex flex-col">

    <!-- Top-left brand -->
    <div class="px-4 py-5 sm:px-8 sm:py-6">
      <RouterLink to="/" class="inline-flex items-center rounded-md" aria-label="IntelliStock home">
        <img
          src="/new-logo.png"
          alt="IntelliStock"
          width="1200"
          height="240"
          class="block h-8 w-auto max-w-full object-contain object-left"
          draggable="false"
        />
      </RouterLink>
    </div>

    <!-- Centered form -->
    <div class="flex-1 flex items-center justify-center px-4 pb-16">
      <div class="w-full max-w-sm">

        <div v-if="firstRun">
          <h1 class="text-2xl font-medium text-slate-100 leading-tight">Set up your account</h1>
          <p class="mt-2 text-sm leading-6 text-slate-400">
            This deployment has no accounts yet. Create the first one. It
            gets full access, and it can create the rest from the Users tab.
          </p>
        </div>
        <div v-else>
          <h1 class="text-2xl font-medium text-slate-100 leading-tight">Sign in to IntelliStock</h1>
          <p class="mt-2 text-sm leading-6 text-slate-400">
            IntelliStock is invite-only. Sign in with your credentials.
          </p>
        </div>

        <!-- Form card -->
        <div class="ui-card mt-8 p-6">

          <!-- Error message -->
          <Transition name="fade-down">
            <div v-if="error" class="ui-error mb-5 px-4 py-3" role="alert">{{ error }}</div>
          </Transition>

          <form @submit.prevent="firstRun ? createAccount() : submit()" class="space-y-5" novalidate>

            <!-- Username field -->
            <div>
              <label for="username" class="ui-field-label">Username</label>
              <input
                id="username"
                v-model="username"
                type="text"
                autocomplete="username"
                placeholder="your username"
                :disabled="loading"
                class="ui-input h-10"
              />
            </div>

            <!-- Password field -->
            <div>
              <label for="password" class="ui-field-label">Password</label>
              <div class="relative">
                <input
                  id="password"
                  v-model="password"
                  :type="showPw ? 'text' : 'password'"
                  :autocomplete="firstRun ? 'new-password' : 'current-password'"
                  :placeholder="firstRun ? 'at least 8 characters' : '••••••••'"
                  :disabled="loading"
                  class="ui-input h-10 pr-11"
                />
                <button
                  type="button"
                  tabindex="-1"
                  @click="showPw = !showPw"
                  class="ui-icon-btn absolute right-1 top-1/2 -translate-y-1/2"
                  :aria-label="showPw ? 'Hide password' : 'Show password'"
                >
                  <span class="material-symbols-outlined text-[18px]">{{ showPw ? 'visibility_off' : 'visibility' }}</span>
                </button>
              </div>
            </div>

            <!-- Confirm password — first run only -->
            <div v-if="firstRun">
              <label for="confirm-password" class="ui-field-label">Confirm password</label>
              <input
                id="confirm-password"
                v-model="confirmPassword"
                :type="showPw ? 'text' : 'password'"
                autocomplete="new-password"
                placeholder="••••••••"
                :disabled="loading"
                class="ui-input h-10"
              />
            </div>

            <!-- Submit -->
            <button
              type="submit"
              :disabled="loading"
              class="ui-btn-primary w-full h-10"
            >
              <svg v-if="loading" class="size-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-dasharray="31.4" stroke-dashoffset="10" stroke-linecap="round"/>
              </svg>
              <span>{{ loading
                ? (firstRun ? 'Creating…' : 'Signing in…')
                : (firstRun ? 'Create the first account' : 'Sign in') }}</span>
            </button>
          </form>

        </div>

        <!-- Footer note -->
        <p v-if="firstRun" class="mt-6 text-xs leading-5 text-slate-500">
          This offer appears only while the deployment has no accounts at all.
        </p>
        <p v-else class="mt-6 text-xs leading-5 text-slate-500">
          Access is by invitation only. Ask someone with an account to create one for you.
        </p>

      </div>
    </div>
  </div>
</template>

<style scoped>
.fade-down-enter-active,
.fade-down-leave-active { transition: opacity 160ms cubic-bezier(0.22, 1, 0.36, 1), transform 160ms cubic-bezier(0.22, 1, 0.36, 1); }
.fade-down-enter-from,
.fade-down-leave-to { opacity: 0; transform: translateY(-4px); }
</style>
