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
onMounted(load)
</script>

<template>
  <div v-if="ready && account"
       @click="router.push('/kalshi')"
       class="rounded-xl border border-cyan-800/60 bg-[#111c30] p-4 cursor-pointer hover:border-cyan-600 transition-colors">
    <div class="flex items-center justify-between mb-1">
      <div class="text-[11px] font-bold uppercase tracking-wide text-sky-300">⚽ Kalshi portfolio</div>
      <span class="text-[10px] text-sky-400 font-bold">Open →</span>
    </div>
    <div class="flex items-baseline gap-2">
      <span class="text-2xl font-extrabold text-slate-50">${{ value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}</span>
      <span class="text-xs font-bold" :class="positive ? 'text-emerald-400' : 'text-red-400'">
        {{ positive ? '▲' : '▼' }} ${{ Math.abs(dayChange).toFixed(2) }}
      </span>
    </div>
    <div class="text-[11px] text-slate-500 mt-1">{{ account.account_name }} · {{ account.kalshi_environment || 'demo' }} · {{ positions }} open</div>
  </div>
</template>
