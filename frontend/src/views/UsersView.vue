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
    <main class="ui-page">

      <!-- Header -->
      <header class="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
        <div class="min-w-0">
          <h1 class="ui-page-title">Users</h1>
          <p class="ui-page-sub max-w-2xl">
            Every account has full access; there is no separate administrator tier.
            Create one per person who needs to sign in, and delete the ones that no
            longer should
          </p>
        </div>
      </header>

      <div class="mt-8 grid grid-cols-1 gap-x-8 gap-y-12 items-start lg:grid-cols-[minmax(0,1fr)_340px]">

        <!-- The list -->
        <section class="min-w-0" aria-labelledby="users-title">
          <div class="mb-3">
            <h2 id="users-title" class="ui-section-title">Accounts</h2>
          </div>

          <div v-if="loading" class="ui-card overflow-hidden" aria-hidden="true">
            <div class="ui-th flex"><div class="ui-skel h-3 w-20"></div></div>
            <div class="ui-rows">
              <div v-for="n in 3" :key="n" class="h-14 px-5 flex items-center">
                <div class="ui-skel h-4 w-40"></div>
              </div>
            </div>
          </div>

          <div v-else-if="loadError" class="ui-error">
            {{ loadError }}
          </div>

          <div v-else class="ui-card overflow-hidden">
            <div class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="h-10 text-left text-xs text-slate-500 border-b border-white/[0.06]">
                    <th scope="col" class="px-5 font-normal">Username</th>
                    <th scope="col" class="px-5 font-normal">Created</th>
                    <th scope="col" class="px-5 font-normal text-right"><span class="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/[0.05]">
                  <tr v-for="user in users" :key="user.id"
                      class="h-14 transition-colors duration-150 ease-out hover:bg-white/[0.03]">
                    <td class="px-5">
                      <div class="flex items-center gap-2 min-w-0">
                        <span class="text-slate-100 truncate">{{ user.username }}</span>
                        <span v-if="isMe(user)" class="text-xs text-slate-500 shrink-0">You</span>
                      </div>
                    </td>
                    <td class="px-5 text-slate-400 whitespace-nowrap tabular-nums">
                      {{ formatCreated(user.created_at) }}
                    </td>
                    <td class="px-5 text-right">
                      <button
                        type="button"
                        :disabled="!!deleteBlockedReason(user)"
                        :title="deleteBlockedReason(user) || `Delete ${user.username}`"
                        @click="askDelete(user)"
                        class="ui-btn text-loss"
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <!-- Create form -->
        <section class="min-w-0" aria-labelledby="add-user-title">
          <div class="mb-3">
            <h2 id="add-user-title" class="ui-section-title">Add a user</h2>
            <p class="ui-section-sub">They will be able to do everything you can</p>
          </div>

          <form @submit.prevent="createUser" class="ui-card p-5 space-y-4" novalidate>
            <div>
              <label for="new-username" class="ui-field-label">Username</label>
              <input
                id="new-username"
                v-model="form.username"
                type="text"
                autocomplete="off"
                :disabled="creating"
                class="ui-input"
              />
            </div>

            <div>
              <label for="new-password" class="ui-field-label">Password</label>
              <input
                id="new-password"
                v-model="form.password"
                type="password"
                autocomplete="new-password"
                placeholder="At least 8 characters"
                :disabled="creating"
                class="ui-input"
              />
            </div>

            <div>
              <label for="confirm-password" class="ui-field-label">Confirm password</label>
              <input
                id="confirm-password"
                v-model="form.confirm"
                type="password"
                autocomplete="new-password"
                :disabled="creating"
                class="ui-input"
              />
            </div>

            <p v-if="createErr" class="text-sm text-loss">
              {{ createErr }}
            </p>
            <p v-else-if="createdMsg" class="text-sm text-gain">
              {{ createdMsg }}
            </p>

            <button
              type="submit"
              :disabled="creating"
              class="ui-btn-primary w-full"
            >
              {{ creating ? 'Creating…' : 'Create user' }}
            </button>
          </form>
        </section>
      </div>

      <!-- Delete confirmation -->
      <div v-if="pendingDelete"
           class="ui-overlay"
           role="dialog" aria-modal="true" aria-labelledby="delete-user-title">
        <div class="ui-modal max-w-sm">
          <div class="ui-modal-head">
            <div class="min-w-0">
              <h2 id="delete-user-title" class="text-base font-medium text-slate-100">
                Delete {{ pendingDelete.username }}?
              </h2>
              <p class="text-sm text-slate-500 mt-1">
                They lose access immediately and any script signing in as them stops
                working. This cannot be undone.
              </p>
            </div>
          </div>

          <div v-if="deleteErr" class="ui-modal-body">
            <p class="text-sm text-loss">{{ deleteErr }}</p>
          </div>

          <div class="ui-modal-foot">
            <button
              type="button"
              :disabled="deleting"
              @click="pendingDelete = null"
              class="ui-btn-ghost"
            >Cancel</button>
            <button
              type="button"
              :disabled="deleting"
              @click="confirmDelete"
              class="ui-btn text-loss"
            >{{ deleting ? 'Deleting…' : 'Delete' }}</button>
          </div>
        </div>
      </div>
    </main>
  </AppShell>
</template>
