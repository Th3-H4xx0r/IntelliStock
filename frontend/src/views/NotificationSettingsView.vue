<script setup>
import { ref, computed, onMounted } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import { authHeaders } from '../utils/auth.js'
import { FALLBACK_TYPES } from '../utils/notificationFallback.js'

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

// Fallback taxonomy if the API doesn't send `types` (older backend):
// utils/notificationFallback.js.

// The taxonomy (key/group/label/desc) supplied by the backend.
const types = ref([])

// Ordered group names, then the types in each group.
const groups = computed(() => {
  const seen = []
  for (const t of types.value) if (!seen.includes(t.group)) seen.push(t.group)
  return seen
})
function typesInGroup(g) {
  return types.value.filter((t) => t.group === g)
}

const categories = ref({})   // { key: { discord, push } }
const loading = ref(true)
const loadError = ref('')
const saving = ref(false)
const saveError = ref('')

const testing = ref('')      // 'discord' | 'push' while in-flight
const testMsg = ref('')
const testOk = ref(false)

const devices = ref([])
const devicesError = ref('')

function routeFor(key) {
  return categories.value[key] || { discord: true, push: false }
}

function headers() {
  return { ...authHeaders(), 'Content-Type': 'application/json' }
}

function tokenSuffix(t) {
  return t && t.length > 8 ? '…' + t.slice(-8) : (t || '')
}

// Display only: "iOS" rather than an upper-cased "IOS".
function platformLabel(p) {
  const s = p || 'ios'
  return s.toLowerCase() === 'ios' ? 'iOS' : s
}

async function fetchDevices() {
  devicesError.value = ''
  try {
    const res = await fetch(`${API_BASE}/push/devices`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    devices.value = data.devices || []
  } catch (e) {
    devicesError.value = `Could not load devices: ${e.message}`
  }
}

async function removeDevice(token) {
  try {
    const res = await fetch(`${API_BASE}/push/devices/${encodeURIComponent(token)}`, {
      method: 'DELETE',
      headers: authHeaders(),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    await fetchDevices()
  } catch (e) {
    devicesError.value = `Could not remove: ${e.message}`
  }
}

async function fetchPrefs() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await fetch(`${API_BASE}/notification-preferences`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    categories.value = data.categories || {}
    types.value = (data.types && data.types.length) ? data.types : FALLBACK_TYPES
  } catch (e) {
    loadError.value = `Failed to load preferences: ${e.message}`
  } finally {
    loading.value = false
  }
}

async function toggle(key, channel) {
  const prev = { ...routeFor(key) }
  const next = { ...prev, [channel]: !prev[channel] }
  categories.value = { ...categories.value, [key]: next } // optimistic
  saving.value = true
  saveError.value = ''
  try {
    const res = await fetch(`${API_BASE}/notification-preferences`, {
      method: 'PUT',
      headers: headers(),
      body: JSON.stringify({ categories: categories.value }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    categories.value = data.categories || categories.value
  } catch (e) {
    categories.value = { ...categories.value, [key]: prev } // revert
    saveError.value = `Could not save: ${e.message}`
  } finally {
    saving.value = false
  }
}

async function sendTest(channel) {
  testing.value = channel
  testMsg.value = ''
  try {
    const res = await fetch(`${API_BASE}/notifications/test`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ channel }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    const name = channel === 'discord' ? 'Discord' : 'iOS push'
    if (data.ok) {
      testOk.value = true
      testMsg.value = `${name} test sent`
    } else {
      testOk.value = false
      if (channel === 'push' && (data.devices ?? 0) === 0) {
        testMsg.value = 'No iOS device registered for push yet.'
      } else if (channel === 'push' && data.errors && data.errors.length) {
        testMsg.value = `Push failed: ${data.errors[0].reason} (status ${data.errors[0].status})`
      } else {
        testMsg.value = `${name} test could not be delivered.`
      }
    }
    if (channel === 'push') fetchDevices()  // env may have been auto-corrected
  } catch (e) {
    testOk.value = false
    testMsg.value = `Test failed: ${e.message}`
  } finally {
    testing.value = ''
  }
}

onMounted(() => { fetchPrefs(); fetchDevices() })
</script>

<template>
  <AppShell>
    <main class="ui-page">
      <!-- Header -->
      <header class="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
        <div class="min-w-0">
          <h1 class="ui-page-title">Notification preferences</h1>
          <p class="ui-page-sub">
            Choose Discord, iOS push or both for each alert category. iOS push needs the mobile app
          </p>
        </div>
      </header>

      <!-- Test delivery -->
      <section class="mt-8">
        <div class="ui-card px-5 py-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div class="min-w-0">
            <p class="text-sm text-slate-200">Test delivery</p>
            <p class="text-sm text-slate-500 mt-0.5">Send a sample notification to confirm a channel works</p>
            <p v-if="testMsg" class="mt-2 text-sm inline-flex items-center gap-2" :class="testOk ? 'text-gain' : 'text-slate-300'">
              <span v-if="!testOk" class="ui-dot ui-dot-warn"></span>
              {{ testMsg }}
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2 shrink-0">
            <button type="button" :disabled="testing === 'discord'"
              @click="sendTest('discord')"
              class="ui-btn">
              {{ testing === 'discord' ? 'Sending…' : 'Test Discord' }}
            </button>
            <button type="button" :disabled="testing === 'push'"
              @click="sendTest('push')"
              class="ui-btn">
              {{ testing === 'push' ? 'Sending…' : 'Test iOS push' }}
            </button>
          </div>
        </div>
      </section>

      <!-- Registered devices -->
      <section class="mt-12">
        <div class="mb-3">
          <h2 class="ui-section-title">Registered devices</h2>
        </div>
        <div class="ui-card overflow-hidden">
          <p v-if="devices.length === 0" class="px-5 py-4 text-sm text-slate-400 leading-relaxed">
            No iOS device registered yet. Open the IntelliStock iOS app, sign in, and tap
            <span class="text-slate-200">Settings → Notifications → Enable push on this device</span>,
            then allow notifications. Push needs a physical device; it doesn't work in the simulator.
          </p>
          <ul v-else class="ui-rows">
            <li v-for="d in devices" :key="d.device_token" class="min-h-14 px-5 py-2 flex items-center justify-between gap-3">
              <div class="min-w-0">
                <p class="text-sm text-slate-100 tabular-nums truncate">{{ tokenSuffix(d.device_token) }}</p>
                <p class="text-xs text-slate-500 mt-0.5">
                  {{ platformLabel(d.platform) }} · {{ d.env
                  }}<span v-if="d.last_seen"> · seen {{ (d.last_seen || '').split('T')[0] }}</span>
                </p>
              </div>
              <button @click="removeDevice(d.device_token)"
                class="ui-btn text-loss shrink-0">Remove</button>
            </li>
          </ul>
        </div>
        <p v-if="devicesError" class="text-sm text-loss mt-2">{{ devicesError }}</p>
      </section>

      <!-- Loading / error -->
      <div v-if="loading" class="mt-12 space-y-3" aria-hidden="true">
        <div class="ui-skel h-4 w-32"></div>
        <div class="h-[232px] rounded-xl bg-surface"></div>
      </div>
      <div v-else-if="loadError" class="mt-12 ui-error">{{ loadError }}</div>

      <!-- Grouped routing: a section per category group -->
      <div v-else>
        <section v-for="group in groups" :key="group" class="mt-12">
          <div class="mb-3">
            <h2 class="ui-section-title">{{ group }}</h2>
          </div>
          <div class="ui-card overflow-hidden">
            <div class="ui-th hidden sm:flex">
              <div class="flex-1">Type</div>
              <div class="w-24 text-center">Discord</div>
              <div class="w-24 text-center">iOS push</div>
            </div>
            <div class="ui-rows">
              <div v-for="c in typesInGroup(group)" :key="c.key"
                class="min-h-14 px-5 py-3 flex flex-col gap-3 sm:flex-row sm:items-center">
                <div class="flex-1 min-w-0">
                  <p class="text-sm text-slate-100">{{ c.label }}</p>
                  <p class="text-xs text-slate-500 mt-0.5">{{ c.desc }}</p>
                </div>
                <div class="flex gap-6 sm:gap-0">
                  <div class="w-24 flex sm:justify-center items-center gap-2">
                    <span class="sm:hidden text-xs text-slate-500 w-16">Discord</span>
                    <button type="button" @click="toggle(c.key, 'discord')"
                      role="switch" :aria-checked="!!routeFor(c.key).discord" :aria-label="`${c.label}: Discord`"
                      class="relative inline-flex items-center h-5 w-9 shrink-0 rounded-full border transition-colors duration-150 ease-out"
                      :class="routeFor(c.key).discord ? 'bg-slate-200 border-slate-200' : 'bg-raised border-white/[0.08]'">
                      <span class="inline-block size-3.5 rounded-full transition-transform duration-150 ease-out"
                        :class="routeFor(c.key).discord ? 'translate-x-[18px] bg-background-dark' : 'translate-x-0.5 bg-slate-500'"></span>
                    </button>
                  </div>
                  <div class="w-24 flex sm:justify-center items-center gap-2">
                    <span class="sm:hidden text-xs text-slate-500 w-16">iOS push</span>
                    <button type="button" @click="toggle(c.key, 'push')"
                      role="switch" :aria-checked="!!routeFor(c.key).push" :aria-label="`${c.label}: iOS push`"
                      class="relative inline-flex items-center h-5 w-9 shrink-0 rounded-full border transition-colors duration-150 ease-out"
                      :class="routeFor(c.key).push ? 'bg-slate-200 border-slate-200' : 'bg-raised border-white/[0.08]'">
                      <span class="inline-block size-3.5 rounded-full transition-transform duration-150 ease-out"
                        :class="routeFor(c.key).push ? 'translate-x-[18px] bg-background-dark' : 'translate-x-0.5 bg-slate-500'"></span>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>

      <p v-if="saveError" class="text-sm text-loss mt-3">{{ saveError }}</p>
      <p v-else-if="saving" class="text-sm text-slate-500 mt-3">Saving…</p>
    </main>
  </AppShell>
</template>
