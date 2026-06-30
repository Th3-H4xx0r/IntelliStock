<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { getToken } from '../../utils/auth.js'
import InfoTip from './InfoTip.vue'

const props = defineProps({
  brokerages: { type: Array, required: true },      // kalshi accounts [{id, account_name, kalshi_environment}]
  initialBrokerageId: { type: String, default: '' },
  editInstance: { type: Object, default: null },     // {id, name, config} when editing
})
const emit = defineEmits(['close', 'created', 'saved'])

const isEdit = computed(() => !!props.editInstance)

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
// Survival-first presets: fractional Kelly + small exposure caps + cash reserve.
// (All far below the old defaults — kelly was 0.25-0.60, exposure 60-100%.)
const RISK_PRESETS = {
  low:    { label: 'Low',    edgePct: 5, kelly: 0.10,  maxContracts: 25,  exposurePct: 10, leagueCapPct: 12, usagePct: 40, poll: 90, dailyLossPct: 0.05,
            blurb: 'Conservative — fewer, higher-confidence trades; small stakes and a tight daily-loss cap.' },
  medium: { label: 'Medium', edgePct: 4, kelly: 0.125, maxContracts: 50,  exposurePct: 15, leagueCapPct: 25, usagePct: 50, poll: 60, dailyLossPct: 0.08,
            blurb: 'Balanced — the default. Fractional-Kelly with a small exposure cap and a cash reserve.' },
  high:   { label: 'High',   edgePct: 3, kelly: 0.15,  maxContracts: 75,  exposurePct: 25, leagueCapPct: 30, usagePct: 60, poll: 45, dailyLossPct: 0.10,
            blurb: 'More active — lower edge bar, slightly bigger Kelly and exposure. More trades, more variance.' },
  max:    { label: 'Max',    edgePct: 2, kelly: 0.20,  maxContracts: 100, exposurePct: 40, leagueCapPct: 40, usagePct: 70, poll: 30, dailyLossPct: 0.15,
            blurb: 'Aggressive — more +EV spots at larger size. Highest variance, but still capped well below the old defaults.' },
}

const brokerageId = ref(props.initialBrokerageId || (props.brokerages[0]?.id ?? ''))
const accountBalance = ref(0)
const loadingBalance = ref(false)
const balanceErr = ref('')

const name = ref('')
const leagues = ref(['EPL', 'Serie B', 'Ligue 2'])
const edgePct = ref(4)
const kelly = ref(0.125)
const maxContracts = ref(50)
const exposurePct = ref(15)
const leagueCapPct = ref(25)
const usagePct = ref(50)
const manualBankroll = ref(1000)
const dailyLoss = ref(0)
const dailyLossTouched = ref(false)
const dailyLossPct = ref(0.08)
const poll = ref(60)
const liveMonitoring = ref(true)
const oddsApiKey = ref('')
const sharpWeight = ref(85)
// Price band + draw gate (favorite-longshot guard) — tunable; 15/90/10 are the safe defaults.
const minPrice = ref(15)
const maxPrice = ref(90)
const drawMinEdge = ref(10)   // % min edge required to take a draw
const orderSizeMin = ref(0)   // order-size range floor ($/trade); 0/0 = auto (Kelly)
const orderSizeMax = ref(0)   // order-size range ceiling ($/trade)
const risk = ref('medium')
// Live brokerage: dry-run (read real prices, place NO real orders) by default. Toggle
// OFF to place REAL orders. Demo ignores this (it sandbox-executes).
const paperMode = ref(true)
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
// LLM model for the analyst panel
const models = ref([])
const selectedModel = ref('')
async function loadModels() {
  try {
    const res = await fetch(`${API_BASE}/models`, { headers: authHeaders() })
    const d = await res.json()
    models.value = (d.models || d || []).filter((m) => m && m.id)
  } catch { models.value = [] }
}

function prefillFromEdit() {
  const c = props.editInstance?.config || {}
  name.value = props.editInstance?.name || ''
  if (Array.isArray(c.leagues) && c.leagues.length) leagues.value = [...c.leagues]
  if (c.edge_threshold != null) edgePct.value = Math.round(c.edge_threshold * 1000) / 10
  if (c.kelly_fraction != null) kelly.value = c.kelly_fraction
  if (c.max_contracts_per_market != null) maxContracts.value = c.max_contracts_per_market
  if (c.max_open_exposure_frac != null) exposurePct.value = Math.round(c.max_open_exposure_frac * 100)
  if (c.order_size_min_cents != null) orderSizeMin.value = Math.round(c.order_size_min_cents) / 100
  if (c.order_size_max_cents != null) orderSizeMax.value = Math.round(c.order_size_max_cents) / 100
  if (c.per_league_cap_frac != null) leagueCapPct.value = Math.round(c.per_league_cap_frac * 100)
  if (c.daily_loss_cap_cents != null) { dailyLoss.value = Math.round(c.daily_loss_cap_cents / 100); dailyLossTouched.value = true }
  if (c.bankroll_cents != null) manualBankroll.value = Math.round(c.bankroll_cents / 100)
  if (c.bankroll_usage_pct != null) usagePct.value = c.bankroll_usage_pct
  if (c.poll_seconds != null) poll.value = c.poll_seconds
  if (c.live_monitoring != null) liveMonitoring.value = !!c.live_monitoring
  if (c.odds_api_key) oddsApiKey.value = c.odds_api_key
  if (c.sharp_weight != null) sharpWeight.value = Math.round(c.sharp_weight * 100)
  if (c.min_price_cents != null) minPrice.value = c.min_price_cents
  if (c.max_price_cents != null) maxPrice.value = c.max_price_cents
  if (c.draw_min_edge != null) drawMinEdge.value = Math.round(c.draw_min_edge * 1000) / 10
  if (c.tier) risk.value = c.tier
  if (c.model) selectedModel.value = c.model
  if (c.paper_mode != null) paperMode.value = !!c.paper_mode
}

watch(brokerageId, loadBalance)
onMounted(() => {
  loadModels()
  if (isEdit.value) prefillFromEdit()
  loadBalance()
})

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
      bankroll_usage_pct: Number(usagePct.value),
      live_monitoring: liveMonitoring.value,
      odds_api_key: oddsApiKey.value.trim(),
      sharp_weight: Number(sharpWeight.value) / 100,
      min_price_cents: Number(minPrice.value),
      max_price_cents: Number(maxPrice.value),
      draw_min_edge: Number(drawMinEdge.value) / 100,
      order_size_min_dollars: Number(orderSizeMin.value) || 0,
      order_size_max_dollars: Number(orderSizeMax.value) || 0,
      tier: risk.value,
      model: selectedModel.value || null,
      // live brokerage: send the paper toggle (default dry-run). demo always sandbox-executes.
      paper_mode: isLive.value ? paperMode.value : false,
    }
    if (isEdit.value) {
      const res = await fetch(`${API_BASE}/instances/${props.editInstance.id}/kalshi/config`, {
        method: 'PATCH', headers: authHeaders(), body: JSON.stringify(body),
      })
      if (!res.ok) { err.value = `Save failed (HTTP ${res.status})`; return }
      emit('saved')
    } else {
      const res = await fetch(`${API_BASE}/brokerages/${brokerageId.value}/kalshi/instances`, {
        method: 'POST', headers: authHeaders(), body: JSON.stringify(body),
      })
      if (!res.ok) { err.value = `Create failed (HTTP ${res.status})`; return }
      emit('created', brokerageId.value)
    }
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
        <h3 class="text-base font-bold text-slate-100 flex items-center gap-2"><span class="material-symbols-outlined text-primary text-[20px]">smart_toy</span> {{ isEdit ? 'Edit Kalshi instance' : 'Create Kalshi instance' }}</h3>
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

        <!-- LLM model (analyst panel) -->
        <div>
          <div class="flex items-center gap-1.5 mb-1.5">
            <label class="text-xs font-medium text-slate-400">Analyst LLM model</label>
            <InfoTip text="The model that reads injuries/lineups/form and adjusts probabilities + writes the per-bet rationale. Leave on Default to use the system model." />
          </div>
          <div class="relative">
            <select v-model="selectedModel" class="w-full appearance-none bg-surface border border-border-subtle rounded-lg px-3 pr-9 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary">
              <option value="">Default (system model)</option>
              <option v-for="m in models" :key="m.id" :value="m.id">{{ m.name || m.model || m.id }}</option>
            </select>
            <span class="material-symbols-outlined text-slate-500 text-[18px] absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none">expand_more</span>
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
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Min price (¢) <InfoTip text="Lowest YES price to buy. The 15¢ floor blocks cheap longshots that lost money; lower it to allow cheaper contracts." size="13px" /></span>
            <input v-model.number="minPrice" type="number" step="1" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Max price (¢) <InfoTip text="Highest YES price to buy. Above ~90¢ fees eat the thin upside." size="13px" /></span>
            <input v-model.number="maxPrice" type="number" step="1" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Draw min edge (%) <InfoTip text="Draws are model-overconfident; they need at least this much edge to trade." size="13px" /></span>
            <input v-model.number="drawMinEdge" type="number" step="1" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Scan cadence (s) <InfoTip text="How often it polls odds, respecting the OddsPapi monthly budget." size="13px" /></span>
            <input v-model.number="poll" type="number" step="15" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block col-span-2">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Order size ($ / trade) <InfoTip text="Target dollar size per trade as a RANGE — the bot sizes within it by edge conviction (stronger edge → bigger trade), so trades stay meaningful even on a small account. Leave both 0 for auto (Kelly-sized)." size="13px" /></span>
            <div class="flex items-center gap-2">
              <input v-model.number="orderSizeMin" type="number" min="0" step="1" placeholder="min $" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary" />
              <span class="text-slate-500 text-sm">to</span>
              <input v-model.number="orderSizeMax" type="number" min="0" step="1" placeholder="max $" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-primary" />
            </div>
          </label>
          <label class="block col-span-2">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Daily-loss cap ($) <InfoTip text="Bot halts for the day if losses hit this. Auto-scales with your risk preset until you edit it." size="13px" /></span>
            <input v-model.number="dailyLoss" @input="dailyLossTouched = true" type="number" min="0" step="1" class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
        </div>

        <button type="button" @click="liveMonitoring = !liveMonitoring"
                class="w-full flex items-center justify-between gap-3 rounded-lg border border-border-subtle bg-surface/50 px-3 py-2.5 text-left hover:border-primary/50 transition-colors">
          <span class="min-w-0">
            <span class="flex items-center gap-1 text-sm font-medium text-slate-200">Live in-match trading
              <InfoTip text="While a match is in play, monitor the live Kalshi market, react to material price moves (goals/red cards), re-read news, and trade two-way (open/add/reduce/exit) under tighter in-play caps." size="13px" /></span>
            <span class="block text-[11px] text-slate-500 mt-0.5">Two-way in-play trading on live matches</span>
          </span>
          <span class="shrink-0 w-10 h-6 rounded-full transition-colors relative" :class="liveMonitoring ? 'bg-primary' : 'bg-border-subtle'">
            <span class="absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform" :class="liveMonitoring ? 'translate-x-4' : ''"></span>
          </span>
        </button>

        <div class="rounded-lg border border-border-subtle bg-surface/50 px-3 py-3 space-y-3">
          <label class="block">
            <span class="flex items-center gap-1 text-sm font-medium text-slate-200 mb-1.5">Odds API key — sharp anchor
              <InfoTip text="Anchor fair value to de-vig'd sharp bookmaker odds (The-Odds-API) so the bot bets where Kalshi disagrees with the books — the real edge. Free key at the-odds-api.com. Leave blank to price on the model only (rarely trades on efficient markets)." size="13px" /></span>
            <input v-model="oddsApiKey" type="password" autocomplete="off" placeholder="the-odds-api.com key (optional)"
                   class="w-full bg-surface border border-border-subtle rounded-lg px-3 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-primary" />
          </label>
          <label class="block">
            <span class="flex items-center gap-1 text-xs font-medium text-slate-400 mb-1.5">Sharp weight ({{ sharpWeight }}%)
              <InfoTip text="How much the sharp bookmaker line anchors fair value vs the in-house model. 70% = trust the books; lower leans on the model where books are thin." size="13px" /></span>
            <input v-model.number="sharpWeight" type="range" min="0" max="100" step="5" class="w-full accent-primary" />
          </label>
        </div>

        <!-- LIVE account: paper-mode (dry-run) toggle. Default ON = read real prices, place NO real orders. -->
        <div v-if="isLive" class="rounded-lg border px-3 py-2.5" :class="paperMode ? 'border-primary/20 bg-primary/5' : 'border-red-500/30 bg-red-500/10'">
          <label class="flex items-center justify-between gap-3 cursor-pointer">
            <span>
              <span class="text-sm font-semibold" :class="paperMode ? 'text-primary' : 'text-red-400'">{{ paperMode ? 'Paper mode (dry-run)' : 'REAL orders' }}</span>
              <span class="block text-[11px] text-slate-400 mt-0.5">{{ paperMode ? 'Reads real prices, places NO real orders — safe to test on a funded account.' : '⚠ Places REAL orders with REAL money when started.' }}</span>
            </span>
            <input type="checkbox" v-model="paperMode" class="size-5 accent-primary shrink-0" />
          </label>
        </div>
        <p v-if="err" class="text-xs text-red-400">{{ err }}</p>
      </div>

      <div class="px-6 py-4 border-t border-border-subtle flex gap-3 sticky bottom-0 bg-surface/80 backdrop-blur">
        <button @click="emit('close')" class="flex-1 py-2.5 rounded-lg border border-border-subtle text-sm font-medium text-slate-400 hover:text-slate-200">Cancel</button>
        <button @click="submit" :disabled="creating" class="flex-1 py-2.5 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 disabled:opacity-50">{{ creating ? (isEdit ? 'Saving…' : 'Creating…') : (isEdit ? 'Save changes' : 'Create instance') }}</button>
      </div>
    </div>
  </div>
</template>
