<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { getToken } from '../../utils/auth.js'

const props = defineProps({
  brokerages: { type: Array, required: true },      // kalshi accounts [{id, account_name, kalshi_environment}]
  initialBrokerageId: { type: String, default: '' },
})
const emit = defineEmits(['close', 'created'])

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')
function authHeaders() {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

const LEAGUES = [
  'EPL', 'EFL Championship', 'Serie A', 'Serie B', 'La Liga', 'La Liga 2',
  'Bundesliga', '2. Bundesliga', 'Ligue 1', 'Ligue 2', 'Eredivisie',
  'Primeira Liga', 'MLS', 'Brasileirão', 'Champions League',
]

const brokerageId = ref(props.initialBrokerageId || (props.brokerages[0]?.id ?? ''))
const accountBalance = ref(0)
const loadingBalance = ref(false)
const balanceErr = ref('')

const name = ref('')
const leagues = ref(['EPL', 'Serie B', 'Ligue 2'])
const edgePct = ref(3)
const kelly = ref(0.25)
const maxContracts = ref(50)
const exposurePct = ref(60)
const leagueCapPct = ref(25)
const usagePct = ref(50)
const manualBankroll = ref(1000)
const dailyLoss = ref(0)
const dailyLossTouched = ref(false)
const poll = ref(60)

const leaguesOpen = ref(false)
const creating = ref(false)
const err = ref('')

const selectedBrokerage = computed(() => props.brokerages.find((b) => b.id === brokerageId.value) || null)
const isLive = computed(() => (selectedBrokerage.value?.kalshi_environment || 'demo') === 'live')
const hasBalance = computed(() => accountBalance.value > 0)
const effectiveBankroll = computed(() =>
  hasBalance.value ? Math.round(accountBalance.value * usagePct.value / 100) : Math.round(Number(manualBankroll.value) || 0),
)

async function loadBalance() {
  if (!brokerageId.value) return
  loadingBalance.value = true
  balanceErr.value = ''
  try {
    const res = await fetch(`${API_BASE}/brokerages/${brokerageId.value}/kalshi/portfolio`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const d = await res.json()
    accountBalance.value = (d.cash && d.cash > 0) ? d.cash : (d.value || 0)
  } catch (e) {
    accountBalance.value = 0
    balanceErr.value = "Couldn't read live balance — enter a bankroll manually."
  } finally {
    loadingBalance.value = false
  }
}

// Scale the dollar-denominated daily-loss cap from the effective bankroll
// (default 10%) unless the user has manually edited it.
watch(effectiveBankroll, (v) => {
  if (!dailyLossTouched.value) dailyLoss.value = Math.max(1, Math.round(v * 0.10))
}, { immediate: true })
watch(brokerageId, loadBalance)
onMounted(loadBalance)

function toggleLeague(l) {
  const i = leagues.value.indexOf(l)
  if (i >= 0) leagues.value.splice(i, 1)
  else leagues.value.push(l)
}

async function submit() {
  if (creating.value) return
  if (!name.value.trim()) { err.value = 'Name is required'; return }
  if (!brokerageId.value) { err.value = 'Select a brokerage'; return }
  if (!leagues.value.length) { err.value = 'Pick at least one league'; return }
  creating.value = true
  err.value = ''
  try {
    const body = {
      name: name.value.trim(),
      leagues: leagues.value,
      edge_threshold: Number(edgePct.value) / 100,
      kelly_fraction: Number(kelly.value),
      max_contracts_per_market: Number(maxContracts.value),
      max_open_exposure_frac: Number(exposurePct.value) / 100,
      per_league_cap_frac: Number(leagueCapPct.value) / 100,
      daily_loss_cap_dollars: Number(dailyLoss.value),
      bankroll_dollars: effectiveBankroll.value,
      poll_seconds: Number(poll.value),
    }
    const res = await fetch(`${API_BASE}/brokerages/${brokerageId.value}/kalshi/instances`, {
      method: 'POST', headers: authHeaders(), body: JSON.stringify(body),
    })
    if (!res.ok) { err.value = `Create failed (HTTP ${res.status})`; return }
    emit('created', brokerageId.value)
  } catch (e) { err.value = String(e.message || e) }
  finally { creating.value = false }
}

function fmt(n) { return `$${Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}` }
</script>

<template>
  <div class="fixed inset-0 z-[60] flex items-center justify-center p-4">
    <div @click="emit('close')" class="absolute inset-0 bg-black/60 backdrop-blur-sm"></div>
    <div class="relative glass-card rounded-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
      <div class="px-6 pt-5 pb-3 border-b border-border-subtle flex items-center justify-between sticky top-0 bg-surface/80 backdrop-blur z-10">
        <h3 class="text-base font-bold text-slate-100 flex items-center gap-2"><span class="material-symbols-outlined text-primary text-[20px]">smart_toy</span> Create Kalshi instance</h3>
        <button @click="emit('close')" class="text-slate-500 hover:text-slate-300"><span class="material-symbols-outlined">close</span></button>
      </div>

      <div class="px-6 py-5 space-y-4">
        <!-- Brokerage selector + details -->
        <div>
          <div class="flex items-center gap-1.5 mb-1.5">
            <label class="text-xs font-medium text-slate-400">Kalshi account</label>
            <span class="material-symbols-outlined text-slate-600 text-[14px]" title="Which Kalshi account this bot trades on. Demo = paper, Live = real money.">info</span>
          </div>
          <div class="relative">
            <select v-model="brokerageId" class="w-full appearance-none bg-surface border border-border-subtle rounded-lg px-3 pr-9 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary">
              <option v-for="b in brokerages" :key="b.id" :value="b.id">{{ b.account_name }} · {{ b.kalshi_environment || 'demo' }}</option>
            </select>
            <span class="material-symbols-outlined text-slate-500 text-[18px] absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none">expand_more</span>
          </div>
          <div class="mt-2 flex items-center gap-2 text-xs">
            <span class="px-2 py-0.5 rounded-md font-medium" :class="isLive ? 'bg-red-500/15 text-red-400' : 'bg-primary/15 text-primary'">{{ isLive ? 'Live · real money' : 'Paper' }}</span>
            <span class="text-slate-500">
              Balance:
              <span v-if="loadingBalance" class="text-slate-400">…</span>
              <span v-else-if="hasBalance" class="text-slate-300 font-semibold tabular-nums">{{ fmt(accountBalance) }}</span>
              <span v-else class="text-amber-400/80">unavailable</span>
            </span>
          </div>
        </div>

        <!-- Name -->
        <div>
          <label class="block text-xs font-medium text-slate-400 mb-1.5">Instance name</label>
          <input v-model="name" type="text" placeholder="e.g. Soccer edge — demo" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary" />
        </div>

        <!-- Leagues multi-select -->
        <div>
          <div class="flex items-center gap-1.5 mb-1.5">
            <label class="text-xs font-medium text-slate-400">Leagues</label>
            <span class="material-symbols-outlined text-slate-600 text-[14px]" title="Which soccer leagues to scan. Thinner/lower divisions often carry more edge than marquee games.">info</span>
          </div>
          <div class="relative">
            <button @click="leaguesOpen = !leaguesOpen" type="button" class="w-full flex items-center justify-between gap-2 bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-200 hover:border-primary/50 transition-colors">
              <span class="flex flex-wrap gap-1 items-center min-h-[20px]">
                <span v-if="!leagues.length" class="text-slate-600">Select leagues…</span>
                <span v-for="l in leagues" :key="l" class="px-1.5 py-0.5 rounded bg-primary/15 text-primary text-[11px] font-medium">{{ l }}</span>
              </span>
              <span class="material-symbols-outlined text-slate-500 text-[18px] transition-transform" :class="{ 'rotate-180': leaguesOpen }">expand_more</span>
            </button>
            <div v-if="leaguesOpen" @click="leaguesOpen = false" class="fixed inset-0 z-40"></div>
            <div v-if="leaguesOpen" class="absolute left-0 right-0 mt-1.5 z-50 glass-card rounded-xl py-1 max-h-56 overflow-y-auto shadow-xl shadow-black/40">
              <button v-for="l in LEAGUES" :key="l" type="button" @click="toggleLeague(l)" class="w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-primary/10 transition-colors" :class="leagues.includes(l) ? 'text-primary' : 'text-slate-300'">
                <span class="material-symbols-outlined text-[16px]" :class="leagues.includes(l) ? 'text-primary' : 'text-transparent'">check</span>{{ l }}
              </button>
            </div>
          </div>
        </div>

        <!-- Bankroll usage -->
        <div>
          <div class="flex items-center justify-between mb-1.5">
            <div class="flex items-center gap-1.5">
              <label class="text-xs font-medium text-slate-400">Bankroll usage</label>
              <span class="material-symbols-outlined text-slate-600 text-[14px]" title="How much of this account's balance the bot sizes against. The daily-loss cap scales with this automatically.">info</span>
            </div>
            <span class="text-xs text-slate-300 font-semibold tabular-nums">{{ usagePct }}% · {{ fmt(effectiveBankroll) }}</span>
          </div>
          <input v-if="hasBalance" v-model.number="usagePct" type="range" min="5" max="100" step="5" class="w-full accent-[var(--accent,#a78bfa)]" style="accent-color:#a78bfa" />
          <div v-else>
            <input v-model.number="manualBankroll" type="number" step="50" placeholder="Bankroll ($)" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
            <p class="text-[11px] text-amber-400/70 mt-1">{{ balanceErr || 'Live balance unavailable — enter a bankroll manually.' }}</p>
          </div>
        </div>

        <!-- Numeric config grid with info tooltips -->
        <div class="grid grid-cols-2 gap-x-3 gap-y-3">
          <label class="block">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Edge threshold (%) <span class="material-symbols-outlined text-slate-600 text-[13px]" title="Minimum +EV edge (fair − price − fee) needed to place a trade.">info</span></span>
            <input v-model.number="edgePct" type="number" step="0.5" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Kelly fraction <span class="material-symbols-outlined text-slate-600 text-[13px]" title="Fraction of full Kelly to stake. ¼ (0.25) is conservative; higher = larger bets and more variance.">info</span></span>
            <input v-model.number="kelly" type="number" step="0.05" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Max contracts / market <span class="material-symbols-outlined text-slate-600 text-[13px]" title="Hard cap on contracts bought in any single market.">info</span></span>
            <input v-model.number="maxContracts" type="number" step="5" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Max open exposure (%) <span class="material-symbols-outlined text-slate-600 text-[13px]" title="Cap on total open exposure as a % of bankroll.">info</span></span>
            <input v-model.number="exposurePct" type="number" step="5" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Per-league cap (%) <span class="material-symbols-outlined text-slate-600 text-[13px]" title="Cap on exposure to any one league — limits correlated risk.">info</span></span>
            <input v-model.number="leagueCapPct" type="number" step="5" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Scan cadence (s) <span class="material-symbols-outlined text-slate-600 text-[13px]" title="How often it polls odds, respecting the OddsPapi monthly budget.">info</span></span>
            <input v-model.number="poll" type="number" step="15" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block col-span-2">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Daily-loss cap ($) <span class="material-symbols-outlined text-slate-600 text-[13px]" title="Bot halts for the day if losses hit this. Auto-scales to 10% of bankroll usage until you edit it.">info</span></span>
            <input v-model.number="dailyLoss" @input="dailyLossTouched = true" type="number" step="25" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
        </div>

        <p v-if="isLive" class="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">⚠ LIVE account — live execution is ON at creation. Starting this instance trades real money.</p>
        <p v-if="err" class="text-xs text-red-400">{{ err }}</p>
      </div>

      <div class="px-6 py-4 border-t border-border-subtle flex gap-3 sticky bottom-0 bg-surface/80 backdrop-blur">
        <button @click="emit('close')" class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200">Cancel</button>
        <button @click="submit" :disabled="creating" class="flex-1 py-2.5 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 disabled:opacity-50">{{ creating ? 'Creating…' : 'Create instance' }}</button>
      </div>
    </div>
  </div>
</template>
