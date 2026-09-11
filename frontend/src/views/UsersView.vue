<script setup>
import { ref, computed, onMounted } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import { getToken, getUser } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

// ── State ─────────────────────────────────────────────────────────────────────
const users     = ref([])
const loading   = ref(true)
const loadError = ref('')

const form       = ref({ username: '', password: '', confirm: '' })
const creating   = ref(false)
const createErr  = ref('')
const createdMsg = ref('')

const pendingDelete = ref(null)   // the user row awaiting confirmation
const deleting      = ref(false)
const deleteErr     = ref('')

const me = computed(() => getUser())

function isMe(user) {
  return !!(me.value && user && user.id === me.value.id)
}

// The server refuses both of these too. Disabling them here is about telling
// the operator why before they click, not about enforcement.
const lastUserRemaining = computed(() => users.value.length <= 1)

function deleteBlockedReason(user) {
  if (isMe(user)) return 'You cannot delete the account you are signed in as.'
  if (lastUserRemaining.value) return 'This is the last account — deleting it would lock everyone out.'
  return ''
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

// Pydantic v2 returns 422 errors as {detail: [{loc,msg,...}]}, the routes
// return {detail: "..."} , and the login route returns {detail: {code,message}}.
// Surface whichever one arrived rather than a bare status code.
function errorDetail(data, fallbackStatus) {
  const d = data?.detail
  if (Array.isArray(d)) return d.map(x => (x && (x.msg || x.message)) || JSON.stringify(x)).join('; ')
  if (typeof d === 'string') return d
  if (d && typeof d === 'object') return d.message || d.msg || JSON.stringify(d)
  return `HTTP ${fallbackStatus}`
}

async function readError(res) {
  let data = null
  try { data = await res.json() } catch { /* empty or non-JSON body */ }
  return errorDetail(data, res.status)
}

function formatCreated(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString()
}

// ── API ───────────────────────────────────────────────────────────────────────
async function fetchUsers() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await fetch(`${API_BASE}/auth/users`, { headers: authHeaders() })
    if (!res.ok) throw new Error(await readError(res))
    const data = await res.json()
    users.value = (data.users || []).slice().sort(
      (a, b) => String(a.username || '').localeCompare(String(b.username || '')))
  } catch (e) {
    loadError.value = `Failed to load users: ${e.message}`
  } finally {
    loading.value = false
  }
}

async function createUser() {
  createErr.value = ''
  createdMsg.value = ''
  const username = form.value.username.trim()
  if (!username) {
    createErr.value = 'Enter a username.'
    return
  }
  if (form.value.password.length < 8) {
    createErr.value = 'Password must be at least 8 characters.'
    return
  }
  if (form.value.password !== form.value.confirm) {
    createErr.value = 'The two passwords do not match.'
    return
  }
  creating.value = true
  try {
    const res = await fetch(`${API_BASE}/auth/users`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ username, password: form.value.password }),
    })
    if (!res.ok) throw new Error(await readError(res))
    createdMsg.value = `Created ${username}.`
    form.value = { username: '', password: '', confirm: '' }
    await fetchUsers()
  } catch (e) {
    createErr.value = e.message
  } finally {
    creating.value = false
  }
}

function askDelete(user) {
  deleteErr.value = ''
  pendingDelete.value = user
}

async function confirmDelete() {
  if (!pendingDelete.value) return
  deleting.value = true
  deleteErr.value = ''
  try {
    const res = await fetch(`${API_BASE}/auth/users/${encodeURIComponent(pendingDelete.value.id)}`, {
      method: 'DELETE',
      headers: authHeaders(),
    })
    if (!res.ok) throw new Error(await readError(res))
    pendingDelete.value = null
    await fetchUsers()
  } catch (e) {
    deleteErr.value = e.message
  } finally {
    deleting.value = false
  }
}

onMounted(fetchUsers)
</script>

<template>
  <AppShell>
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10">

      <!-- Header -->
      <div class="mb-6 sm:mb-8">
        <p class="text-primary text-xs font-bold uppercase tracking-widest mb-1">Users</p>
        <h1 class="text-2xl sm:text-3xl font-bold leading-tight">Accounts</h1>
        <p class="text-slate-400 text-sm mt-1 max-w-xl">
          Every account has full access — there is no separate administrator tier.
          Create one per person who needs to sign in, and delete the ones that no
          longer should.
        </p>
      </div>

      <div class="grid grid-cols-1 xl:grid-cols-3 gap-6">

        <!-- The list -->
        <div class="xl:col-span-2">
          <div v-if="loading" class="flex items-center gap-3 text-slate-400 text-sm">
            <span class="material-symbols-outlined animate-spin text-xl">progress_activity</span>
            Loading users...
          </div>

          <div v-else-if="loadError"
               class="rounded-xl bg-red-500/10 border border-red-500/20 px-5 py-4 text-red-400 text-sm">
            {{ loadError }}
          </div>

          <div v-else class="glass-card rounded-2xl overflow-hidden">
            <div class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="text-left text-[11px] uppercase tracking-widest text-slate-500 border-b border-border-subtle">
                    <th scope="col" class="px-5 py-3 font-semibold">Username</th>
                    <th scope="col" class="px-5 py-3 font-semibold">Created</th>
                    <th scope="col" class="px-5 py-3 font-semibold text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="user in users" :key="user.id"
                      class="border-b border-border-subtle/60 last:border-0">
                    <td class="px-5 py-3.5">
                      <div class="flex items-center gap-2 min-w-0">
                        <span class="font-medium text-slate-100 truncate">{{ user.username }}</span>
                        <span v-if="isMe(user)"
                              class="px-2 py-0.5 rounded-full bg-primary/15 text-primary text-[10px] font-bold uppercase tracking-widest shrink-0">
                          you
                        </span>
                      </div>
                    </td>
                    <td class="px-5 py-3.5 text-slate-400 whitespace-nowrap">
                      {{ formatCreated(user.created_at) }}
                    </td>
                    <td class="px-5 py-3.5 text-right">
                      <button
                        type="button"
                        :disabled="!!deleteBlockedReason(user)"
                        :title="deleteBlockedReason(user) || `Delete ${user.username}`"
                        @click="askDelete(user)"
                        class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
                               border border-red-500/30 text-red-400 hover:bg-red-500/10
                               disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent
                               transition-colors"
                      >
                        <span class="material-symbols-outlined text-[16px]">delete</span>
                        Delete
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <!-- Create form -->
        <div class="glass-card rounded-2xl p-5 sm:p-6 h-fit">
          <h2 class="font-semibold text-slate-100">Add a user</h2>
          <p class="text-xs text-slate-500 mt-1 mb-4">
            They will be able to do everything you can.
          </p>

          <form @submit.prevent="createUser" class="space-y-4" novalidate>
            <div class="space-y-1.5">
              <label for="new-username"
                     class="text-xs font-semibold uppercase tracking-widest text-slate-400">Username</label>
              <input
                id="new-username"
                v-model="form.username"
                type="text"
                autocomplete="off"
                :disabled="creating"
                class="w-full bg-background-dark border border-border-subtle rounded-xl px-4 py-2.5 text-sm text-slate-100
                       placeholder-slate-600 focus:outline-none focus:border-primary/60 focus:ring-1 focus:ring-primary/30
                       disabled:opacity-50 transition-colors"
              />
            </div>

            <div class="space-y-1.5">
              <label for="new-password"
                     class="text-xs font-semibold uppercase tracking-widest text-slate-400">Password</label>
              <input
                id="new-password"
                v-model="form.password"
                type="password"
                autocomplete="new-password"
                placeholder="at least 8 characters"
                :disabled="creating"
                class="w-full bg-background-dark border border-border-subtle rounded-xl px-4 py-2.5 text-sm text-slate-100
                       placeholder-slate-600 focus:outline-none focus:border-primary/60 focus:ring-1 focus:ring-primary/30
                       disabled:opacity-50 transition-colors"
              />
            </div>

            <div class="space-y-1.5">
              <label for="confirm-password"
                     class="text-xs font-semibold uppercase tracking-widest text-slate-400">Confirm password</label>
              <input
                id="confirm-password"
                v-model="form.confirm"
                type="password"
                autocomplete="new-password"
                :disabled="creating"
                class="w-full bg-background-dark border border-border-subtle rounded-xl px-4 py-2.5 text-sm text-slate-100
                       placeholder-slate-600 focus:outline-none focus:border-primary/60 focus:ring-1 focus:ring-primary/30
                       disabled:opacity-50 transition-colors"
              />
            </div>

            <div v-if="createErr"
                 class="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-red-400 text-xs">
              {{ createErr }}
            </div>
            <div v-else-if="createdMsg"
                 class="rounded-xl bg-emerald-500/10 border border-emerald-500/20 px-4 py-3 text-emerald-400 text-xs">
              {{ createdMsg }}
            </div>

            <button
              type="submit"
              :disabled="creating"
              class="w-full inline-flex items-center justify-center gap-2 py-2.5 rounded-xl font-semibold text-sm
                     bg-primary text-background-dark hover:brightness-110 active:brightness-95
                     disabled:opacity-60 disabled:cursor-not-allowed transition-all"
            >
              <span class="material-symbols-outlined text-[18px]">person_add</span>
              {{ creating ? 'Creating…' : 'Create user' }}
            </button>
          </form>
        </div>
      </div>

      <!-- Delete confirmation -->
      <div v-if="pendingDelete"
           class="fixed inset-0 z-50 flex items-center justify-center px-4 bg-black/60 backdrop-blur-sm"
           role="dialog" aria-modal="true" aria-labelledby="delete-user-title">
        <div class="glass-card rounded-2xl p-6 w-full max-w-sm space-y-4">
          <h2 id="delete-user-title" class="font-semibold text-slate-100">
            Delete {{ pendingDelete.username }}?
          </h2>
          <p class="text-sm text-slate-400">
            They lose access immediately and any script signing in as them stops
            working. This cannot be undone.
          </p>

          <div v-if="deleteErr"
               class="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-red-400 text-xs">
            {{ deleteErr }}
          </div>

          <div class="flex gap-3 pt-1">
            <button
              type="button"
              :disabled="deleting"
              @click="pendingDelete = null"
              class="flex-1 py-2.5 rounded-xl text-sm font-semibold border border-border-subtle
                     text-slate-300 hover:bg-surface/60 disabled:opacity-50 transition-colors"
            >Cancel</button>
            <button
              type="button"
              :disabled="deleting"
              @click="confirmDelete"
              class="flex-1 py-2.5 rounded-xl text-sm font-semibold bg-red-500/90 text-white
                     hover:bg-red-500 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
            >{{ deleting ? 'Deleting…' : 'Delete' }}</button>
          </div>
        </div>
      </div>
    </main>
  </AppShell>
</template>
