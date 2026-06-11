<script setup>
import { ref, onMounted } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import { authHeaders } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

// The 9 categories (1:1 with the backend), in display order.
const CATEGORIES = [
  { key: 'order_submit',   label: 'Order submitted', desc: 'An order was sent to the broker' },
  { key: 'order_fill',     label: 'Order filled',    desc: 'An order was filled' },
  { key: 'order_reject',   label: 'Order rejected',  desc: 'The broker rejected an order' },
  { key: 'order_retry',    label: 'Order retried',   desc: 'An order is being retried after a recoverable reject' },
  { key: 'strategy_start', label: 'Strategy start',  desc: 'A strategy fired its first run of the session' },
  { key: 'strategy_error', label: 'Strategy error',  desc: 'An unrecoverable strategy error occurred' },
  { key: 'halt',           label: 'Halt',            desc: 'Live trading was halted' },
  { key: 'drawdown_halt',  label: 'Drawdown halt',   desc: 'A drawdown risk-off guard tripped' },
  { key: 'crash_loop',     label: 'Crash loop',      desc: 'The broker subprocess entered a crash loop' },
]

const categories = ref({})   // { key: { discord, push } }
const loading = ref(true)
const loadError = ref('')
const saving = ref(false)
const saveError = ref('')

const testing = ref('')      // 'discord' | 'push' while in-flight
const testMsg = ref('')
const testOk = ref(false)

function routeFor(key) {
  return categories.value[key] || { discord: true, push: false }
}

function headers() {
  return { ...authHeaders(), 'Content-Type': 'application/json' }
}

async function fetchPrefs() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await fetch(`${API_BASE}/notification-preferences`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    categories.value = data.categories || {}
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
      testMsg.value = `${name} test sent ✓`
    } else {
      testOk.value = false
      testMsg.value = channel === 'push' && (data.devices ?? 0) === 0
        ? 'No iOS device registered for push yet.'
        : `${name} test could not be delivered.`
    }
  } catch (e) {
    testOk.value = false
    testMsg.value = `Test failed: ${e.message}`
  } finally {
    testing.value = ''
  }
}

onMounted(fetchPrefs)
</script>

<template>
  <AppShell>
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10">
      <!-- Header -->
      <div class="mb-6 sm:mb-8">
        <p class="text-primary text-xs font-bold uppercase tracking-widest mb-1">Settings</p>
        <h1 class="text-2xl sm:text-3xl font-bold leading-tight">Notification Preferences</h1>
        <p class="text-slate-400 text-sm mt-1">
          Choose Discord and/or iOS push for each alert category. iOS push requires the mobile app.
        </p>
      </div>

      <!-- Test delivery -->
      <div class="mb-8 rounded-xl border border-slate-800 bg-slate-900/40 p-4">
        <p class="text-sm font-medium text-slate-200 mb-1">Test delivery</p>
        <p class="text-xs text-slate-500 mb-3">Send a sample notification to confirm a channel works.</p>
        <div class="flex flex-wrap items-center gap-3">
          <button type="button" :disabled="testing === 'discord'"
            @click="sendTest('discord')"
            class="px-4 py-2 rounded-lg bg-slate-800 text-slate-100 text-sm font-medium hover:bg-slate-700 disabled:opacity-60 transition-colors">
            {{ testing === 'discord' ? 'Sending…' : 'Test Discord' }}
          </button>
          <button type="button" :disabled="testing === 'push'"
            @click="sendTest('push')"
            class="px-4 py-2 rounded-lg bg-slate-800 text-slate-100 text-sm font-medium hover:bg-slate-700 disabled:opacity-60 transition-colors">
            {{ testing === 'push' ? 'Sending…' : 'Test iOS push' }}
          </button>
          <span v-if="testMsg" :class="testOk ? 'text-emerald-400' : 'text-amber-400'" class="text-sm">
            {{ testMsg }}
          </span>
        </div>
      </div>

      <!-- Loading / error -->
      <div v-if="loading" class="text-slate-400 text-sm py-10 text-center">Loading…</div>
      <div v-else-if="loadError" class="text-red-400 text-sm py-10 text-center">{{ loadError }}</div>

      <!-- Matrix -->
      <div v-else class="rounded-xl border border-slate-800 overflow-hidden">
        <div class="hidden sm:flex items-center px-4 py-2 bg-slate-900/60 text-[11px] uppercase tracking-wider text-slate-500">
          <div class="flex-1">Category</div>
          <div class="w-24 text-center">Discord</div>
          <div class="w-24 text-center">iOS push</div>
        </div>
        <div v-for="(c, i) in CATEGORIES" :key="c.key"
          class="flex flex-col sm:flex-row sm:items-center px-4 py-3 gap-3"
          :class="i > 0 ? 'border-t border-slate-800' : ''">
          <div class="flex-1">
            <p class="text-sm font-medium text-slate-200">{{ c.label }}</p>
            <p class="text-xs text-slate-500">{{ c.desc }}</p>
          </div>
          <div class="flex gap-6 sm:gap-0">
            <div class="w-24 flex sm:justify-center items-center gap-2">
              <span class="sm:hidden text-xs text-slate-500 w-16">Discord</span>
              <button type="button" @click="toggle(c.key, 'discord')"
                class="relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 transition-colors"
                :class="routeFor(c.key).discord ? 'bg-primary border-primary' : 'bg-slate-800 border-slate-700'">
                <span class="inline-block size-3.5 rounded-full bg-white shadow transition-transform"
                  :class="routeFor(c.key).discord ? 'translate-x-4' : 'translate-x-0.5'"></span>
              </button>
            </div>
            <div class="w-24 flex sm:justify-center items-center gap-2">
              <span class="sm:hidden text-xs text-slate-500 w-16">iOS push</span>
              <button type="button" @click="toggle(c.key, 'push')"
                class="relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 transition-colors"
                :class="routeFor(c.key).push ? 'bg-primary border-primary' : 'bg-slate-800 border-slate-700'">
                <span class="inline-block size-3.5 rounded-full bg-white shadow transition-transform"
                  :class="routeFor(c.key).push ? 'translate-x-4' : 'translate-x-0.5'"></span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <p v-if="saveError" class="text-amber-400 text-sm mt-3">{{ saveError }}</p>
      <p v-else-if="saving" class="text-slate-500 text-sm mt-3">Saving…</p>
    </main>
  </AppShell>
</template>
