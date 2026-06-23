<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { getToken } from '../../utils/auth.js'
import InfoTip from './InfoTip.vue'

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
  'World Cup', 'World Cup Qualifiers', 'Champions League', 'Europa League',
  'EPL', 'EFL Championship', 'Serie A', 'Serie B', 'La Liga', 'La Liga 2',
  'Bundesliga', '2. Bundesliga', 'Ligue 1', 'Ligue 2', 'Eredivisie',
  'Primeira Liga', 'MLS', 'Brasileirão',
]

// Risk presets tune every config value. dailyLossPct = daily-loss cap as a
// fraction of the effective bankroll. Users can still tweak any value after.
const RISK_PRESETS = {
  low:    { label: 'Low',    edgePct: 5, kelly: 0.15, maxContracts: 25,  exposurePct: 30,  leagueCapPct: 12, usagePct: 25,  poll: 90, dailyLossPct: 0.05,
            blurb: 'Conservative — fewer, higher-confidence trades; small stakes and a tight daily-loss cap.' },
  medium: { label: 'Medium', edgePct: 3, kelly: 0.25, maxContracts: 50,  exposurePct: 60,  leagueCapPct: 25, usagePct: 50,  poll: 60, dailyLossPct: 0.10,
            blurb: 'Balanced — the default. Quarter-Kelly with moderate exposure.' },
  high:   { label: 'High',   edgePct: 2, kelly: 0.40, maxContracts: 100, exposurePct: 80,  leagueCapPct: 40, usagePct: 75,  poll: 45, dailyLossPct: 0.20,
            blurb: 'Aggressive — lower edge bar, bigger Kelly and exposure. More trades, more variance.' },
  max:    { label: 'Max',    edgePct: 1, kelly: 0.60, maxContracts: 200, exposurePct: 100, leagueCapPct: 60, usagePct: 100, poll: 30, dailyLossPct: 0.35,
            blurb: 'Maximum — trades nearly everything +EV at full size. Highest variance and drawdown risk.' },
}

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
const dailyLossPct = ref(0.10)
const poll = ref(60)
const risk = ref('medium')
const riskBlurb = computed(() => RISK_PRESETS[risk.value]?.blurb || '')

function applyPreset(level) {
  const p = RISK_PRESETS[level]
  if (!p) return
  risk.value = level
  edgePct.value = p.edgePct
  kelly.value = p.kelly
  maxContracts.value = p.maxContracts
  exposurePct.value = p.exposurePct
  leagueCapPct.value = p.leagueCapPct
  usagePct.value = p.usagePct
  poll.value = p.poll
  dailyLossPct.value = p.dailyLossPct
  dailyLossTouched.value = false
  dailyLoss.value = Math.max(1, Math.round(effectiveBankroll.value * p.dailyLossPct))
}

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
  if (!dailyLossTouched.value) dailyLoss.value = Math.max(1, Math.round(v * dailyLossPct.value))
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
            <InfoTip text="Which Kalshi account this bot trades on. Demo = paper, Live = real money." />
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

        <!-- Risk tolerance preset -->
        <div>
          <div class="flex items-center gap-1.5 mb-1.5">
            <label class="text-xs font-medium text-slate-400">Risk tolerance</label>
            <InfoTip text="Pick a preset and we'll tune edge, Kelly, exposure, caps, bankroll usage, cadence, and the daily-loss cap. You can still tweak any value after." />
          </div>
          <div class="grid grid-cols-4 gap-1.5 bg-surface border border-border-subtle rounded-lg p-1">
            <button v-for="(p, k) in RISK_PRESETS" :key="k" type="button" @click="applyPreset(k)"
                    class="py-1.5 rounded-md text-xs font-semibold transition-all"
                    :class="risk === k ? 'bg-primary text-background-dark' : 'text-slate-400 hover:text-slate-200'">
              {{ p.label }}
            </button>
          </div>
          <p class="text-[11px] text-slate-500 mt-1.5 leading-snug">{{ riskBlurb }}</p>
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
            <InfoTip text="Which soccer leagues to scan. Thinner/lower divisions often carry more edge than marquee games." />
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
              <InfoTip text="How much of this account's balance the bot sizes against. The daily-loss cap scales with this automatically." />
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
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Edge threshold (%) <InfoTip text="Minimum +EV edge (fair − price − fee) needed to place a trade." size="13px" /></span>
            <input v-model.number="edgePct" type="number" step="0.5" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Kelly fraction <InfoTip text="Fraction of full Kelly to stake. ¼ (0.25) is conservative; higher = larger bets and more variance." size="13px" /></span>
            <input v-model.number="kelly" type="number" step="0.05" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Max contracts / market <InfoTip text="Hard cap on contracts bought in any single market." size="13px" /></span>
            <input v-model.number="maxContracts" type="number" step="5" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Max open exposure (%) <InfoTip text="Cap on total open exposure as a % of bankroll." size="13px" /></span>
            <input v-model.number="exposurePct" type="number" step="5" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Per-league cap (%) <InfoTip text="Cap on exposure to any one league — limits correlated risk." size="13px" /></span>
            <input v-model.number="leagueCapPct" type="number" step="5" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Scan cadence (s) <InfoTip text="How often it polls odds, respecting the OddsPapi monthly budget." size="13px" /></span>
            <input v-model.number="poll" type="number" step="15" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block col-span-2">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Daily-loss cap ($) <InfoTip text="Bot halts for the day if losses hit this. Auto-scales with your risk preset until you edit it." size="13px" /></span>
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
