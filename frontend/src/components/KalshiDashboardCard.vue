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
const fmtValue = computed(() => `$${value.value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)
onMounted(load)
</script>

<template>
  <section v-if="ready && account">
    <div class="flex items-center justify-between mb-5">
      <div>
        <h2 class="text-lg font-bold text-slate-100 flex items-center gap-2">
          <span class="material-symbols-outlined text-primary text-[20px]">sports_soccer</span> Kalshi
        </h2>
        <p class="text-slate-500 text-xs mt-0.5">Prediction-markets portfolio.</p>
      </div>
      <RouterLink to="/kalshi" class="flex items-center gap-1.5 text-xs font-medium text-primary hover:brightness-110 transition-all">
        Open Kalshi
        <span class="material-symbols-outlined text-[14px]">arrow_forward</span>
      </RouterLink>
    </div>

    <div @click="router.push('/kalshi')"
         class="glass-card card-hover rounded-2xl p-4 sm:p-5 cursor-pointer">
      <div class="flex items-center gap-2 mb-3">
        <span class="material-symbols-outlined text-primary text-[18px]">account_balance_wallet</span>
        <span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">{{ account.account_name }}</span>
      </div>
      <div class="flex flex-wrap items-end gap-2 sm:gap-3">
        <span class="text-xl sm:text-2xl font-bold text-slate-100 tabular-nums break-all">{{ fmtValue }}</span>
        <span class="flex items-center gap-1 text-xs sm:text-sm font-semibold mb-0.5"
              :class="positive ? 'text-emerald-400' : 'text-red-400'">
          <span class="material-symbols-outlined text-[16px]">{{ positive ? 'trending_up' : 'trending_down' }}</span>
          {{ positive ? '+' : '-' }}${{ Math.abs(dayChange).toFixed(2) }}
        </span>
      </div>
      <p class="text-xs text-slate-500 mt-3">{{ positions }} open position{{ positions === 1 ? '' : 's' }}</p>
    </div>
  </section>
</template>
