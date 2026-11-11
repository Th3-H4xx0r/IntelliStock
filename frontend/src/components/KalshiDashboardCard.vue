<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')
function authHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

const router = useRouter()
const account = ref(null)
const value = ref(0)
const dayChange = ref(0)
const positions = ref(0)
const ready = ref(false)

async function load() {
  try {
    const res = await fetch(`${API_BASE}/brokerages`, { headers: authHeaders() })
    const d = await res.json()
    const kalshi = (d.accounts || []).filter((a) => a.brokerage_type === 'kalshi')
    if (!kalshi.length) { ready.value = true; return }
    account.value = kalshi[0]
    const [pf, pos] = await Promise.all([
      fetch(`${API_BASE}/brokerages/${account.value.id}/kalshi/portfolio`, { headers: authHeaders() }).then((r) => r.ok ? r.json() : null),
      fetch(`${API_BASE}/brokerages/${account.value.id}/kalshi/positions`, { headers: authHeaders() }).then((r) => r.ok ? r.json() : null),
    ])
    if (pf) { value.value = pf.value || 0; dayChange.value = pf.day_change || 0 }
    if (pos) positions.value = pos.count || 0
  } finally { ready.value = true }
}

const positive = computed(() => dayChange.value >= 0)
const usd = (n) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
const fmtValue = computed(() => usd(value.value))
const fmtChange = computed(() => `${positive.value ? '+' : '−'}${usd(Math.abs(dayChange.value))}`)
onMounted(load)
</script>

<template>
  <section v-if="ready && account">
    <div class="flex items-center justify-between mb-3">
      <div>
        <h2 class="text-base font-medium text-slate-100">Prediction markets</h2>
        <p class="text-sm text-slate-500 mt-0.5">Kalshi event contracts</p>
      </div>
      <RouterLink
        to="/kalshi"
        class="shrink-0 whitespace-nowrap inline-flex items-center gap-1.5 h-8 px-3 -mr-3 rounded-md text-sm text-slate-400 hover:text-slate-100 hover:bg-raised transition-colors"
      >
        Open Kalshi
        <span class="material-symbols-outlined text-[16px]">arrow_forward</span>
      </RouterLink>
    </div>

    <button
      type="button"
      @click="router.push('/kalshi')"
      class="card-hover w-full rounded-xl border border-white/[0.07] bg-surface px-5 py-4 grid grid-cols-2 sm:grid-cols-[minmax(0,1fr)_auto_auto_auto] items-center gap-x-8 gap-y-3 text-left"
    >
      <span class="min-w-0 col-span-2 sm:col-span-1">
        <span class="block text-sm text-slate-100 truncate">{{ account.account_name }}</span>
        <span class="block text-xs text-slate-500 mt-0.5">Kalshi</span>
      </span>
      <span>
        <span class="block text-xs text-slate-500">Balance</span>
        <span class="block text-sm text-slate-100 tabular-nums mt-0.5">{{ fmtValue }}</span>
      </span>
      <span>
        <span class="block text-xs text-slate-500">Today</span>
        <span class="block text-sm tabular-nums mt-0.5" :class="positive ? 'text-gain' : 'text-loss'">{{ fmtChange }}</span>
      </span>
      <span>
        <span class="block text-xs text-slate-500">Open positions</span>
        <span class="block text-sm text-slate-100 tabular-nums mt-0.5">{{ positions }}</span>
      </span>
    </button>
  </section>
</template>
