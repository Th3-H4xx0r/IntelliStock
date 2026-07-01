<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import KalshiPortfolioChart from '../components/kalshi/KalshiPortfolioChart.vue'
import KalshiCreateInstanceModal from '../components/kalshi/KalshiCreateInstanceModal.vue'
import InstanceLiveLogs from '../components/InstanceLiveLogs.vue'
import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')
function authHeaders() {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

const route = useRoute()
const router = useRouter()
const id = route.params.id

const detail = ref(null)
const decisions = ref([])
const live = ref([])
const orders = ref({ placed: [], fills: [], mock: [], mock_history: [] })
const summary = ref({ total: 0, placed: 0, skipped: 0, queued: 0, blocked: 0 })
const paper = ref({ trades: 0, graded: 0, realized_pnl_cents: 0, unrealized_pnl_cents: 0, open_positions: 0 })
const loading = ref(true)
let liveTimer = null
let kickoffTimer = null
const nowMs = ref(Date.now())   // ticks every 1s for the live kickoff countdowns
const busy = ref(false)
const expanded = ref(new Set())

async function getJson(path) {
  try {
    const res = await fetch(`${API_BASE}${path}`, { headers: authHeaders() })
    if (!res.ok) return null
    return await res.json()
  } catch { return null }
}

async function load() {
  loading.value = true
  const [d, dec] = await Promise.all([
    getJson(`/instances/${id}/kalshi/detail`),
    getJson(`/instances/${id}/kalshi/decisions?limit=200`),
  ])
  detail.value = d
  if (dec) { decisions.value = dec.decisions || []; summary.value = dec.summary || summary.value; paper.value = dec.paper || paper.value }
  loading.value = false
  loadLive()
}

async function loadLive() {
  const [l, o] = await Promise.all([
    getJson(`/instances/${id}/kalshi/live`),
    getJson(`/instances/${id}/kalshi/orders?limit=50`),
  ])
  if (l) live.value = l.matches || []
  if (o) orders.value = { placed: o.placed || [], fills: o.fills || [], mock: o.mock || [], mock_history: o.mock_history || [] }
}

// Silent full refresh for the 10s auto-refresh — re-pulls detail + decisions + paper
// summary + live + orders WITHOUT the loading flash, so the page stays live.
async function refresh() {
  const [d, dec] = await Promise.all([
    getJson(`/instances/${id}/kalshi/detail`),
    getJson(`/instances/${id}/kalshi/decisions?limit=200`),
  ])
  if (d) detail.value = d
  if (dec) { decisions.value = dec.decisions || []; summary.value = dec.summary || summary.value; paper.value = dec.paper || paper.value }
  await loadLive()
}

async function startStop(start) {
  if (busy.value) return
  busy.value = true
  try {
    await fetch(`${API_BASE}/instances/${id}/${start ? 'start' : 'stop'}`, { method: 'POST', headers: authHeaders() })
    await load()
  } finally { busy.value = false }
}
async function kill() {
  if (busy.value || !detail.value) return
  if (!confirm('Stop this instance and cancel all resting orders?')) return
  busy.value = true
  try {
    await fetch(`${API_BASE}/brokerages/${detail.value.brokerage_id}/kalshi/kill`, { method: 'POST', headers: authHeaders() })
    await load()
  } finally { busy.value = false }
}

function toggle(i) {
  const s = new Set(expanded.value)
  s.has(i) ? s.delete(i) : s.add(i)
  expanded.value = s
}
const running = computed(() => !!detail.value?.running)
// Real money ONLY when a live brokerage has live_enabled AND paper_mode off.
const liveReal = computed(() => detail.value?.environment === 'live'
  && !!(detail.value?.config || {}).live_enabled && !(detail.value?.config || {}).paper_mode)

// Edit / delete
const showEdit = ref(false)
const deleting = ref(false)
const editBrokerages = computed(() => detail.value
  ? [{ id: detail.value.brokerage_id, account_name: detail.value.name, kalshi_environment: detail.value.environment }]
  : [])
function onSaved() { showEdit.value = false; load() }
async function del() {
  if (deleting.value) return
  if (!confirm('Delete this Kalshi instance? This cannot be undone.')) return
  deleting.value = true
  try {
    await fetch(`${API_BASE}/instances/${id}?force=true`, { method: 'DELETE', headers: authHeaders() })
    router.push('/kalshi')
  } finally { deleting.value = false }
}

function pct(v) { return v == null ? '—' : `${(v * 100).toFixed(1)}%` }
function decColor(d) {
  return { placed: 'text-emerald-400', skipped: 'text-slate-500', queued: 'text-amber-400', blocked: 'text-red-400' }[d] || 'text-slate-400'
}
function decBadge(d) {
  return { placed: 'bg-emerald-500/15 text-emerald-400', skipped: 'bg-slate-500/15 text-slate-500', queued: 'bg-amber-500/15 text-amber-400', blocked: 'bg-red-500/15 text-red-400' }[d] || 'bg-slate-500/15 text-slate-400'
}

// Decision log pagination
const decPage = ref(0)
const decPageSize = 12
const decPages = computed(() => Math.max(1, Math.ceil(decisions.value.length / decPageSize)))
const decStart = computed(() => Math.min(decPage.value, decPages.value - 1) * decPageSize)
const pagedDecisions = computed(() => decisions.value.slice(decStart.value, decStart.value + decPageSize))

// Kalshi price (cents) for a decision row: prefer the actual entry, else derive
// it from the fair value minus the edge (price = fair - edge).
function priceCents(d) {
  if (d.entry_avg_cents != null) return d.entry_avg_cents
  if (d.fused_fair == null || d.edge == null) return null
  return Math.round((d.fused_fair - d.edge) * 100)
}
function fmtNum(v) { return v == null ? null : (Number.isInteger(v) ? String(v) : v.toFixed(1)) }
// "Updated" stamp for each edge — relative if recent, else a short local date/time.
function fmtTs(ts) {
  if (!ts) return ''
  const t = new Date(ts); if (isNaN(t)) return ''
  const secs = Math.round((Date.now() - t.getTime()) / 1000)
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return t.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}
// Countdown to kickoff. ts is epoch SECONDS. Clean two-unit format ("5d 4h", "3m 2s").
// These are PREGAME-board games (the engine routes genuinely-live games elsewhere) and
// the time may be a date-only estimate, so we never say "live": once the match day has
// arrived show "today"; hide clearly-past games.
function kickoffCountdown(ts) {
  if (ts == null) return ''
  let secs = Math.round(Number(ts) - nowMs.value / 1000)
  if (Number.isNaN(secs)) return ''
  if (secs <= -86400) return ''        // a clearly past day -> hide
  if (secs <= 0) return 'today'        // match day arrived (not necessarily kicked off)
  const d = Math.floor(secs / 86400); secs -= d * 86400
  const h = Math.floor(secs / 3600); secs -= h * 3600
  const m = Math.floor(secs / 60); const s = secs - m * 60
  const u = [[d, 'd'], [h, 'h'], [m, 'm'], [s, 's']]
  const i = u.findIndex(([v]) => v > 0)
  const out = [`${u[i][0]}${u[i][1]}`]
  if (i + 1 < u.length && u[i + 1][0] > 0) out.push(`${u[i + 1][0]}${u[i + 1][1]}`)  // second unit, if non-zero
  return out.join(' ')
}
const SIDE_ORDER = { home: 0, draw: 1, away: 2 }

// Build an SVG <polyline> "points" string for a side's edge-over-time series,
// normalized into a ~60x16 box. Returns null if there are fewer than 2 points.
const SPARK_W = 60, SPARK_H = 16, SPARK_PAD = 2
function sparkPoints(history) {
  const pts = (history || []).map(h => h && h.edge).filter(e => e != null)
  if (pts.length < 2) return null
  const min = Math.min(...pts), max = Math.max(...pts)
  const span = max - min || 1
  const innerW = SPARK_W - SPARK_PAD * 2
  const innerH = SPARK_H - SPARK_PAD * 2
  return pts.map((e, i) => {
    const x = SPARK_PAD + (pts.length === 1 ? 0 : (i / (pts.length - 1)) * innerW)
    // Higher edge => higher on screen (smaller y).
    const y = SPARK_PAD + (1 - (e - min) / span) * innerH
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}
function sparkLast(history) {
  const pts = (history || []).map(h => h && h.edge).filter(e => e != null)
  return pts.length ? pts[pts.length - 1] : null
}

// Group the (read-only) decision rows by match so a game's three sides show
// together. Sorted by each game's best (max) edge, descending.
const matchBoard = computed(() => {
  const groups = new Map()
  for (const d of decisions.value) {
    const key = d.fixture_id || d.match || d.market_ticker || 'unknown'
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        match: d.match || d.market_ticker,
        home: d.home,
        away: d.away,
        home_elo: d.home_elo, away_elo: d.away_elo,
        home_xg: d.home_xg, away_xg: d.away_xg,
        home_logo: null, away_logo: null,
        kickoff_ts: d.kickoff_ts ?? null,
        sides: [],
      })
    }
    const g = groups.get(key)
    // Backfill context that may only be populated on some rows.
    if (g.home == null) g.home = d.home
    if (g.away == null) g.away = d.away
    if (g.kickoff_ts == null && d.kickoff_ts != null) g.kickoff_ts = d.kickoff_ts
    if (g.home_elo == null) g.home_elo = d.home_elo
    if (g.away_elo == null) g.away_elo = d.away_elo
    if (g.home_xg == null) g.home_xg = d.home_xg
    if (g.away_xg == null) g.away_xg = d.away_xg
    // The pick logo is the crest for that side — capture home/away crests.
    if (d.side === 'home' && d.pick_logo && !g.home_logo) g.home_logo = d.pick_logo
    if (d.side === 'away' && d.pick_logo && !g.away_logo) g.away_logo = d.pick_logo
    g.sides.push({
      side: d.side,
      pick_label: d.pick_label,
      pick_logo: d.pick_logo,
      fused_fair: d.fused_fair,
      sharp_prob: d.sharp_prob,
      edge: d.edge,
      decision: d.decision,
      block_reason: d.block_reason,
      paper: d.paper,
      price: priceCents(d),
      ts: d.ts,
      edge_history: d.edge_history,
      entry_edge: d.entry_edge,
    })
  }
  const list = [...groups.values()]
  for (const g of list) {
    // Candidate rows are keyed per-tick, so a single side accumulates many rows.
    // Collapse to ONE row per side (latest by ts) — but remember if it was EVER
    // placed, so a held side shows PLACED, not a later 'already positioned' skip.
    const bySide = new Map()
    for (const s of g.sides) {
      const prev = bySide.get(s.side)
      const everPlaced = (prev?.everPlaced || false) || s.decision === 'placed'
      // Remember the edge the side was actually placed at (from a 'placed' row).
      const placedEdge = s.decision === 'placed' && s.entry_edge != null
        ? s.entry_edge : (prev?.placedEdge ?? null)
      if (!prev || (s.ts || '') >= (prev.ts || '')) bySide.set(s.side, { ...s, everPlaced, placedEdge })
      else { prev.everPlaced = everPlaced; prev.placedEdge = placedEdge }
    }
    g.sides = [...bySide.values()].sort((a, b) => (SIDE_ORDER[a.side] ?? 9) - (SIDE_ORDER[b.side] ?? 9))
    for (const s of g.sides) if (s.everPlaced) s.decision = 'placed'
    const edges = g.sides.map(s => s.edge).filter(e => e != null)
    g.bestEdge = edges.length ? Math.max(...edges) : null
  }
  // Sort by soonest kickoff first; games with no known kickoff sink to the bottom.
  list.sort((a, b) => (a.kickoff_ts ?? Infinity) - (b.kickoff_ts ?? Infinity))
  return list
})

const ACTION_STYLE = {
  open: 'bg-emerald-500/15 text-emerald-400', add: 'bg-emerald-500/15 text-emerald-400',
  reduce: 'bg-amber-500/15 text-amber-400', exit: 'bg-red-500/15 text-red-400',
  hold: 'bg-slate-500/15 text-slate-400',
}
function topSide(m) {
  const p = m.market_probs || {}
  const k = Object.keys(p).sort((a, b) => p[b] - p[a])[0]
  return k ? { side: k, prob: p[k] } : null
}
function sideLabel(m, side) {
  if (side === 'home') return m.home
  if (side === 'away') return m.away
  return 'Draw'
}
function liveClock(m) {
  if (m.score && m.score.clock) return m.score.clock
  if (m.elapsed_min != null) return `${Math.round(m.elapsed_min)}'`
  return 'LIVE'
}
function initials(name) { return (name || '').replace(/[^A-Za-z ]/g, '').split(' ').map(w => w[0]).join('').slice(0, 3).toUpperCase() }

onMounted(() => {
  load()
  liveTimer = setInterval(refresh, 10000)   // auto-refresh the whole page every 10s
  kickoffTimer = setInterval(() => { nowMs.value = Date.now() }, 1000)   // tick countdowns
})
onUnmounted(() => {
  if (liveTimer) clearInterval(liveTimer)
  if (kickoffTimer) clearInterval(kickoffTimer)
})
</script>

<template>
  <AppShell>
    <main class="flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8 lg:py-10 space-y-6">
      <RouterLink to="/kalshi" class="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200">
        <span class="material-symbols-outlined text-[16px]">arrow_back</span> Kalshi
      </RouterLink>

      <div v-if="loading" class="glass-card rounded-2xl h-[160px] animate-pulse"></div>

      <template v-else-if="detail">
        <!-- Header -->
        <div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p class="text-primary text-xs font-bold uppercase tracking-widest mb-2 flex items-center gap-1.5">
              <span class="material-symbols-outlined text-[16px]">smart_toy</span> Kalshi instance
            </p>
            <h1 class="text-2xl sm:text-3xl font-bold text-slate-100">{{ detail.name }}</h1>
            <div class="flex items-center gap-2 mt-2 text-xs">
              <span class="px-2 py-0.5 rounded-md font-medium" :class="running ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-500/15 text-slate-400'">{{ running ? 'Running' : 'Stopped' }}</span>
              <span class="px-2 py-0.5 rounded-md font-medium" :class="liveReal ? 'bg-red-500/15 text-red-400' : 'bg-amber-500/15 text-amber-400'">{{ liveReal ? 'Live · real money' : (detail.environment === 'live' ? 'Paper (mock) · live data' : 'Paper') }}</span>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <button @click="startStop(!running)" :disabled="busy"
                    class="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold transition-all disabled:opacity-50"
                    :class="running ? 'border border-amber-500/40 text-amber-400 hover:bg-amber-500/10' : 'bg-primary text-background-dark hover:brightness-110'">
              <span class="material-symbols-outlined text-[18px]">{{ running ? 'pause' : 'play_arrow' }}</span>{{ running ? 'Stop' : 'Start' }}
            </button>
            <router-link :to="`/kalshi/instances/${id}/backtest`"
                    class="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border-subtle text-slate-300 text-sm font-semibold hover:text-slate-100 hover:border-primary/50 transition-all">
              <span class="material-symbols-outlined text-[18px]">science</span> Backtest
            </router-link>
            <button @click="showEdit = true" :disabled="busy"
                    class="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border-subtle text-slate-300 text-sm font-semibold hover:text-slate-100 hover:border-primary/50 transition-all disabled:opacity-50">
              <span class="material-symbols-outlined text-[18px]">tune</span> Edit
            </button>
            <button @click="del" :disabled="deleting"
                    class="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-red-500/40 text-red-400 text-sm font-semibold hover:bg-red-500/10 transition-all disabled:opacity-50">
              <span class="material-symbols-outlined text-[18px]">delete</span> Delete
            </button>
          </div>
        </div>

        <!-- Equity + decision summary -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <KalshiPortfolioChart :brokerage-id="detail.brokerage_id" />
          <div class="glass-card rounded-2xl p-4 sm:p-5">
            <div class="flex items-center gap-2 mb-4"><span class="material-symbols-outlined text-primary text-[18px]">insights</span><span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Decision summary</span></div>
            <div class="grid grid-cols-2 gap-3">
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3"><div class="text-lg font-bold text-emerald-400 tabular-nums">{{ summary.placed }}</div><div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Placed</div></div>
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3"><div class="text-lg font-bold text-slate-100 tabular-nums">{{ summary.skipped }}</div><div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Skipped</div></div>
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3"><div class="text-lg font-bold text-amber-400 tabular-nums">{{ summary.queued }}</div><div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Queued</div></div>
              <div class="rounded-xl border border-border-subtle bg-surface/40 p-3"><div class="text-lg font-bold text-red-400 tabular-nums">{{ summary.blocked }}</div><div class="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mt-0.5">Blocked</div></div>
            </div>
            <!-- Paper (mock) hypothetical P&L — "what your profit would have been" -->
            <div v-if="paper.trades" class="mt-3 flex items-center justify-between gap-2 rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2.5">
              <span class="text-[11px] uppercase tracking-wide text-amber-400/90 font-semibold flex items-center gap-1.5"><span class="text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">MOCK</span>Paper P&amp;L · {{ paper.trades }} trade{{ paper.trades === 1 ? '' : 's' }}<span v-if="paper.graded" class="text-slate-500 normal-case">({{ paper.graded }} settled)</span></span>
              <span class="text-right shrink-0">
                <span class="text-base font-bold tabular-nums" :class="(paper.realized_pnl_cents || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'">realized {{ (paper.realized_pnl_cents >= 0 ? '+' : '') + '$' + ((paper.realized_pnl_cents || 0) / 100).toFixed(2) }}</span>
                <span v-if="paper.unrealized_pnl_cents != null" class="block text-[11px] tabular-nums text-slate-500">
                  unrealized <span class="font-semibold" :class="(paper.unrealized_pnl_cents || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'">{{ (paper.unrealized_pnl_cents >= 0 ? '+' : '') + '$' + ((paper.unrealized_pnl_cents || 0) / 100).toFixed(2) }}</span> (live)<span v-if="paper.open_positions" class="text-slate-600"> · {{ paper.open_positions }} open</span>
                </span>
              </span>
            </div>
          </div>
        </div>

        <!-- Live now -->
        <div v-if="live.length" class="glass-card rounded-2xl p-4 sm:p-5">
          <div class="flex items-center gap-2 mb-3">
            <span class="relative flex h-2.5 w-2.5"><span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-500/60"></span><span class="relative inline-flex rounded-full h-2.5 w-2.5 bg-red-500"></span></span>
            <span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Live now · {{ live.length }} match{{ live.length === 1 ? '' : 'es' }}</span>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div v-for="m in live" :key="m.id" class="rounded-xl border border-border-subtle bg-surface/40 p-4">
              <!-- score header with flags -->
              <div class="flex items-center justify-between gap-2">
                <div class="flex flex-col items-center gap-1.5 w-20 shrink-0">
                  <img v-if="m.home_logo" :src="m.home_logo" referrerpolicy="no-referrer" class="w-11 h-11 rounded-full object-contain bg-white/5 ring-1 ring-border-subtle" :alt="m.home" />
                  <div v-else class="w-11 h-11 rounded-full bg-surface ring-1 ring-border-subtle flex items-center justify-center text-[11px] font-bold text-slate-400">{{ initials(m.home) }}</div>
                  <span class="text-[11px] text-slate-300 text-center truncate w-full">{{ m.home }}</span>
                </div>
                <div class="flex flex-col items-center">
                  <div class="text-2xl font-bold text-slate-100 tabular-nums leading-none">{{ m.score ? (m.score.home ?? 0) : 0 }}<span class="text-slate-600 mx-2">:</span>{{ m.score ? (m.score.away ?? 0) : 0 }}</div>
                  <div class="flex items-center gap-1.5 mt-2">
                    <span class="relative flex h-1.5 w-1.5"><span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-500/60"></span><span class="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500"></span></span>
                    <span class="text-[11px] text-emerald-400 font-semibold tabular-nums">{{ liveClock(m) }}</span>
                  </div>
                </div>
                <div class="flex flex-col items-center gap-1.5 w-20 shrink-0">
                  <img v-if="m.away_logo" :src="m.away_logo" referrerpolicy="no-referrer" class="w-11 h-11 rounded-full object-contain bg-white/5 ring-1 ring-border-subtle" :alt="m.away" />
                  <div v-else class="w-11 h-11 rounded-full bg-surface ring-1 ring-border-subtle flex items-center justify-center text-[11px] font-bold text-slate-400">{{ initials(m.away) }}</div>
                  <span class="text-[11px] text-slate-300 text-center truncate w-full">{{ m.away }}</span>
                </div>
              </div>

              <!-- market-implied probabilities -->
              <div v-if="topSide(m)" class="mt-3 space-y-1.5">
                <div v-for="(p, side) in m.market_probs" :key="side" class="flex items-center gap-2">
                  <span class="text-[11px] text-slate-400 w-20 truncate">{{ sideLabel(m, side) }}</span>
                  <div class="flex-1 h-1.5 rounded-full bg-surface overflow-hidden"><div class="h-full bg-primary/70 rounded-full" :style="{ width: `${Math.round((p || 0) * 100)}%` }"></div></div>
                  <span class="text-[11px] tabular-nums text-slate-300 w-9 text-right">{{ Math.round((p || 0) * 100) }}%</span>
                </div>
              </div>

              <p v-if="m.news" class="mt-3 text-[11px] text-slate-400 line-clamp-2 whitespace-pre-line">{{ m.news.split('\n')[0] }}</p>

              <!-- in-play decisions -->
              <div v-if="m.decisions && m.decisions.length" class="mt-3 flex flex-wrap gap-1.5">
                <span v-for="(d, di) in m.decisions.slice(0, 4)" :key="di" class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase" :class="ACTION_STYLE[d.action] || 'bg-slate-500/15 text-slate-400'">
                  {{ d.action }}<span v-if="d.size" class="font-normal normal-case">{{ d.size }}</span>
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- Pregame analysis — decisions grouped by match (read-only view of the fetched log) -->
        <div class="glass-card rounded-2xl p-4 sm:p-5">
          <div class="flex items-center gap-2 mb-3"><span class="material-symbols-outlined text-primary text-[18px]">stadium</span><span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Pregame analysis</span></div>
          <p v-if="!matchBoard.length" class="text-sm text-slate-500">No matches analyzed yet. Once the engine reviews a game's sides, the home / draw / away edges show together here.</p>
          <div v-else class="grid grid-cols-1 md:grid-cols-2 gap-3 max-h-[520px] overflow-y-auto -mr-2 pr-2">
            <div v-for="g in matchBoard" :key="g.key" class="rounded-xl border border-border-subtle bg-surface/40 p-4">
              <!-- Game header: home crest · matchup · away crest + best-edge chip -->
              <div class="flex items-center gap-3">
                <img v-if="g.home_logo" :src="g.home_logo" referrerpolicy="no-referrer" class="w-9 h-9 rounded-full object-contain bg-white/5 ring-1 ring-border-subtle shrink-0" :alt="g.home" />
                <div v-else class="w-9 h-9 rounded-full bg-surface ring-1 ring-border-subtle flex items-center justify-center text-[11px] font-bold text-slate-400 shrink-0">{{ initials(g.home) }}</div>
                <div class="min-w-0 flex-1">
                  <div class="text-sm font-semibold text-slate-100 truncate">{{ g.home || '—' }} <span class="text-slate-600">vs</span> {{ g.away || '—' }}<span v-if="kickoffCountdown(g.kickoff_ts)" class="ml-1.5 font-normal text-[11px] text-slate-500">· {{ kickoffCountdown(g.kickoff_ts) }}</span></div>
                  <div v-if="g.home_elo != null || g.away_elo != null || g.home_xg != null || g.away_xg != null" class="text-[11px] text-slate-500 truncate">
                    <span v-if="g.home_elo != null || g.away_elo != null">Elo {{ fmtNum(g.home_elo) ?? '?' }}/{{ fmtNum(g.away_elo) ?? '?' }}</span>
                    <span v-if="(g.home_elo != null || g.away_elo != null) && (g.home_xg != null || g.away_xg != null)" class="text-slate-700"> · </span>
                    <span v-if="g.home_xg != null || g.away_xg != null">xG {{ fmtNum(g.home_xg) ?? '?' }}/{{ fmtNum(g.away_xg) ?? '?' }}</span>
                  </div>
                </div>
                <img v-if="g.away_logo" :src="g.away_logo" referrerpolicy="no-referrer" class="w-9 h-9 rounded-full object-contain bg-white/5 ring-1 ring-border-subtle shrink-0" :alt="g.away" />
                <div v-else class="w-9 h-9 rounded-full bg-surface ring-1 ring-border-subtle flex items-center justify-center text-[11px] font-bold text-slate-400 shrink-0">{{ initials(g.away) }}</div>
              </div>
              <div v-if="g.bestEdge != null" class="mt-2">
                <span class="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded" :class="g.bestEdge > 0 ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'">
                  Best edge {{ (g.bestEdge >= 0 ? '+' : '') + pct(g.bestEdge) }}
                </span>
              </div>

              <!-- One row per side present (home · draw · away) -->
              <div class="mt-3 space-y-1.5">
                <div v-for="(s, si) in g.sides" :key="si" class="flex items-center gap-2">
                  <img v-if="s.pick_logo" :src="s.pick_logo" referrerpolicy="no-referrer" class="w-6 h-6 rounded-full object-contain bg-white/5 ring-1 ring-border-subtle shrink-0" />
                  <div v-else class="w-6 h-6 rounded-full bg-surface ring-1 ring-border-subtle flex items-center justify-center text-[9px] font-bold text-slate-400 shrink-0">{{ initials((s.pick_label || s.side || '').replace(' to win','')) }}</div>
                  <div class="min-w-0 flex-1">
                    <div class="text-xs text-slate-200 truncate flex items-center gap-1.5">
                      <span class="truncate">{{ s.pick_label || s.side }}</span>
                      <span v-if="s.sharp_prob == null" class="shrink-0 text-[9px] uppercase tracking-wide text-slate-500/80 border border-border-subtle rounded px-1 py-px" title="No sharp line — model-only fair value">model-only</span>
                    </div>
                    <div class="text-[10px] text-slate-500 tabular-nums">Fair {{ pct(s.fused_fair) }} · {{ s.price == null ? '—' : s.price + '¢' }}<span v-if="s.ts" class="text-slate-600"> · updated {{ fmtTs(s.ts) }}</span></div>
                    <div v-if="s.decision === 'placed' && s.placedEdge != null" class="text-[10px] text-emerald-400/80 tabular-nums">placed @ {{ (s.placedEdge >= 0 ? '+' : '') + pct(s.placedEdge) }}</div>
                  </div>
                  <!-- Tiny inline edge-over-time sparkline -->
                  <svg v-if="sparkPoints(s.edge_history)" :viewBox="`0 0 ${SPARK_W} ${SPARK_H}`" :width="SPARK_W" :height="SPARK_H" class="shrink-0" preserveAspectRatio="none" aria-hidden="true">
                    <polyline :points="sparkPoints(s.edge_history)" fill="none" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round" :class="(sparkLast(s.edge_history) ?? 0) >= 0 ? 'stroke-emerald-400' : 'stroke-red-400'" />
                  </svg>
                  <span class="text-xs tabular-nums font-semibold shrink-0 w-14 text-right" :class="s.edge == null ? 'text-slate-500' : (s.edge > 0 ? 'text-emerald-400' : 'text-red-400')">{{ s.edge == null ? '—' : (s.edge >= 0 ? '+' : '') + pct(s.edge) }}</span>
                  <span class="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded shrink-0" :class="decBadge(s.decision)">{{ s.decision }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Decision log (half) + Orders (half) -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-5 items-stretch h-[560px]">
          <div class="glass-card rounded-2xl p-4 sm:p-5 flex flex-col min-h-0">
            <div class="flex items-center gap-2 mb-3 shrink-0"><span class="material-symbols-outlined text-primary text-[18px]">history_edu</span><span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Decision log</span></div>
            <p v-if="!decisions.length" class="text-sm text-slate-500">No decisions logged yet. The engine writes a row for every bet placed and every candidate it considered.</p>
            <div class="flex-1 min-h-0 overflow-y-auto -mr-2 pr-2 space-y-2">
              <div v-for="(d, i) in pagedDecisions" :key="d.id || i" class="rounded-xl border border-border-subtle bg-surface/40 hover:border-primary/40 transition-colors">
                <button @click="toggle(decStart + i)" class="w-full flex items-center gap-3 p-3 text-left">
                  <img v-if="d.pick_logo" :src="d.pick_logo" referrerpolicy="no-referrer" class="w-9 h-9 rounded-full object-contain bg-white/5 ring-1 ring-border-subtle shrink-0" />
                  <div v-else class="w-9 h-9 rounded-full bg-surface ring-1 ring-border-subtle flex items-center justify-center text-[11px] font-bold text-slate-400 shrink-0">{{ initials((d.pick_label || '').replace(' to win','')) }}</div>
                  <div class="min-w-0 flex-1">
                    <div class="text-sm font-semibold text-slate-100 truncate">{{ d.match || d.market_ticker }}</div>
                    <div class="text-xs text-primary font-medium truncate">{{ d.pick_label || d.side }}</div>
                  </div>
                  <div class="flex flex-col items-end gap-1 shrink-0">
                    <span class="text-xs tabular-nums font-semibold" :class="(d.edge || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'">{{ d.edge == null ? '' : (d.edge >= 0 ? '+' : '') + pct(d.edge) }}</span>
                    <span class="flex items-center gap-1">
                      <span v-if="d.paper" class="text-[10px] font-bold uppercase px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400" title="Paper / mock — no real order placed">MOCK</span>
                      <span class="text-[10px] font-bold uppercase px-1.5 py-0.5 rounded" :class="decBadge(d.decision)">{{ d.decision }}</span>
                    </span>
                  </div>
                  <span class="material-symbols-outlined text-slate-500 text-[18px] transition-transform shrink-0" :class="{ 'rotate-180': expanded.has(decStart + i) }">expand_more</span>
                </button>
                <div v-if="expanded.has(decStart + i)" class="px-3 pb-3 text-xs text-slate-400 space-y-2">
                  <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    <div><span class="text-slate-600">Model</span> {{ pct(d.model_prob) }}</div>
                    <div><span class="text-slate-600">Sharp</span> {{ pct(d.sharp_prob) }}</div>
                    <div><span class="text-slate-600">LLM adj</span> {{ d.llm_adjustment == null ? '—' : (d.llm_adjustment >= 0 ? '+' : '') + pct(d.llm_adjustment) }}</div>
                    <div><span class="text-slate-600">Fair</span> {{ pct(d.fused_fair) }}</div>
                    <div><span class="text-slate-600">Size</span> {{ d.size || 0 }}</div>
                    <div><span class="text-slate-600">Opp score</span> {{ d.opportunity_score == null ? '—' : d.opportunity_score.toFixed(2) }}</div>
                    <div v-if="d.outcome"><span class="text-slate-600">Outcome</span> {{ d.outcome }}</div>
                    <div v-if="d.clv != null"><span class="text-slate-600">CLV</span> {{ pct(d.clv) }}</div>
                    <div v-if="d.paper && d.realized_pnl_cents != null"><span class="text-slate-600">Paper P&amp;L</span> <span :class="d.realized_pnl_cents >= 0 ? 'text-emerald-400' : 'text-red-400'">{{ (d.realized_pnl_cents >= 0 ? '+' : '') + '$' + (d.realized_pnl_cents / 100).toFixed(2) }}</span></div>
                  </div>
                  <p v-if="d.llm_rationale" class="text-slate-300 bg-surface/60 border border-border-subtle rounded-lg px-3 py-2">
                    <span class="material-symbols-outlined text-primary text-[14px] align-middle mr-1">psychology</span>{{ d.llm_rationale }}
                  </p>
                  <p v-if="d.block_reason" :class="d.decision === 'blocked' ? 'text-red-400/80' : 'text-slate-500'">{{ d.decision === 'blocked' ? 'Blocked: ' : 'Skipped — ' }}{{ d.block_reason }}</p>
                </div>
              </div>
            </div>
            <div v-if="decPages > 1" class="flex items-center justify-between pt-3 shrink-0">
              <button @click="decPage = Math.max(0, decPage - 1)" :disabled="decPage === 0" class="flex items-center justify-center w-8 h-8 rounded-lg border border-border-subtle text-slate-400 hover:text-slate-200 hover:border-primary/50 disabled:opacity-30 transition-colors"><span class="material-symbols-outlined text-[18px]">chevron_left</span></button>
              <span class="text-xs text-slate-500">Page {{ decPage + 1 }} / {{ decPages }}</span>
              <button @click="decPage = Math.min(decPages - 1, decPage + 1)" :disabled="decPage >= decPages - 1" class="flex items-center justify-center w-8 h-8 rounded-lg border border-border-subtle text-slate-400 hover:text-slate-200 hover:border-primary/50 disabled:opacity-30 transition-colors"><span class="material-symbols-outlined text-[18px]">chevron_right</span></button>
            </div>
          </div>

          <!-- Orders -->
          <div class="glass-card rounded-2xl p-4 sm:p-5 flex flex-col min-h-0">
            <div class="flex items-center gap-2 mb-3 shrink-0"><span class="material-symbols-outlined text-primary text-[18px]">receipt_long</span><span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Orders</span></div>
            <div class="flex-1 min-h-0 overflow-y-auto -mr-2 pr-2">
              <div class="flex items-center gap-1.5 mb-2"><span class="material-symbols-outlined text-amber-400 text-[14px]">pending</span><span class="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">Pending · {{ orders.placed.length }}</span></div>
              <p v-if="!orders.placed.length" class="text-xs text-slate-500 mb-2">No resting orders — everything filled.</p>
              <div v-for="(o, i) in orders.placed" :key="'p' + i" class="flex items-center gap-3 rounded-xl border border-border-subtle bg-surface/40 p-3 mb-2">
                <img v-if="o.pick_logo" :src="o.pick_logo" referrerpolicy="no-referrer" class="w-8 h-8 rounded-full object-contain bg-white/5 ring-1 ring-border-subtle shrink-0" />
                <div v-else class="w-8 h-8 rounded-full bg-surface ring-1 ring-border-subtle flex items-center justify-center text-[10px] font-bold text-slate-400 shrink-0">{{ initials((o.pick_label || '').replace(' to win','')) }}</div>
                <div class="min-w-0 flex-1">
                  <div class="flex items-center justify-between gap-2">
                    <span class="text-sm font-semibold text-slate-100 truncate">{{ o.match || o.market_ticker }}</span>
                    <span v-if="o.in_play" class="shrink-0 px-1.5 py-0.5 rounded bg-red-500/15 text-red-400 text-[9px] font-bold uppercase">live</span>
                  </div>
                  <div class="flex items-center justify-between gap-2 mt-0.5">
                    <span class="text-xs text-primary font-medium truncate">{{ o.pick_label || o.side }}</span>
                    <span class="flex items-center gap-2.5 shrink-0 text-xs">
                      <span class="text-slate-400 tabular-nums">{{ o.contracts || o.size }}×</span>
                      <span v-if="o.edge != null" class="tabular-nums font-semibold" :class="(o.edge || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'">{{ (o.edge >= 0 ? '+' : '') + pct(o.edge) }}</span>
                    </span>
                  </div>
                </div>
              </div>
              <!-- Mock (paper) positions — live unrealized P&L on the right -->
              <div class="flex items-center gap-1.5 mt-4 mb-2"><span class="text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">MOCK</span><span class="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">Mock positions · {{ orders.mock.length }}</span></div>
              <p v-if="!orders.mock.length" class="text-xs text-slate-500 mb-2">No open mock positions.</p>
              <div v-for="(m, i) in orders.mock" :key="'m' + i" class="flex items-center gap-3 rounded-xl border border-amber-500/20 bg-amber-500/5 p-3 mb-2">
                <img v-if="m.pick_logo" :src="m.pick_logo" referrerpolicy="no-referrer" class="w-8 h-8 rounded-full object-contain bg-white/5 ring-1 ring-border-subtle shrink-0" />
                <div v-else class="w-8 h-8 rounded-full bg-surface ring-1 ring-border-subtle flex items-center justify-center text-[10px] font-bold text-slate-400 shrink-0">{{ initials((m.pick_label || '').replace(' to win','')) }}</div>
                <div class="min-w-0 flex-1">
                  <div class="flex items-center justify-between gap-2">
                    <span class="text-sm font-semibold text-slate-100 truncate">{{ m.match || m.market_ticker }}</span>
                    <!-- Total position value (contracts × current mark) in big bold -->
                    <span class="text-lg font-bold tabular-nums text-slate-100 shrink-0">${{ ((m.contracts * (m.mark_cents ?? m.entry_cents ?? 0)) / 100).toFixed(2) }}</span>
                  </div>
                  <div class="flex items-center justify-between gap-2 mt-0.5">
                    <span class="flex items-center gap-1.5 min-w-0">
                      <span class="text-xs text-primary font-medium truncate">{{ m.pick_label || m.side }}</span>
                      <span class="shrink-0 text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">MOCK</span>
                    </span>
                    <span class="text-xs tabular-nums shrink-0" :class="m.unrealized_pnl_cents == null ? 'text-slate-500' : (m.unrealized_pnl_cents > 0 ? 'text-emerald-400' : (m.unrealized_pnl_cents < 0 ? 'text-red-400' : 'text-slate-400'))">{{ m.unrealized_pnl_cents == null ? '—' : (m.unrealized_pnl_cents >= 0 ? '+' : '') + '$' + (m.unrealized_pnl_cents / 100).toFixed(2) }} <span class="text-slate-600">P&amp;L</span></span>
                  </div>
                  <div class="text-[11px] text-slate-500 tabular-nums mt-0.5">{{ m.contracts }} @ {{ m.entry_cents }}¢ → {{ m.mark_cents == null ? '—' : m.mark_cents + '¢' }}</div>
                </div>
              </div>
              <template v-if="orders.fills.length">
                <div class="flex items-center gap-1.5 mt-4 mb-2"><span class="material-symbols-outlined text-emerald-400 text-[14px]">check_circle</span><span class="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">Filled · {{ orders.fills.length }}</span></div>
                <div v-for="(f, i) in orders.fills" :key="'f' + i" class="flex items-center gap-3 rounded-xl border border-border-subtle bg-surface/40 p-3 mb-2">
                  <img v-if="f.pick_logo" :src="f.pick_logo" referrerpolicy="no-referrer" class="w-8 h-8 rounded-full object-contain bg-white/5 ring-1 ring-border-subtle shrink-0" />
                  <div v-else class="w-8 h-8 rounded-full bg-surface ring-1 ring-border-subtle flex items-center justify-center text-[10px] font-bold text-slate-400 shrink-0">{{ initials((f.pick_label || '').replace(' to win','')) }}</div>
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center justify-between gap-2">
                      <span class="text-sm font-semibold text-slate-100 truncate">{{ f.match || f.market_ticker }}</span>
                      <span class="text-[10px] uppercase font-bold shrink-0" :class="f.action === 'sell' ? 'text-amber-400' : 'text-emerald-400'">{{ f.action }}</span>
                    </div>
                    <div class="flex items-center justify-between gap-2 mt-0.5">
                      <span class="text-xs text-primary font-medium truncate">{{ f.pick_label || f.side }}</span>
                      <span class="text-xs text-slate-400 tabular-nums shrink-0">{{ f.contracts }} @ {{ f.price_cents }}¢</span>
                    </div>
                  </div>
                </div>
              </template>

              <!-- Mock (paper) FILLED history — settled/expired paper trades with realized P&L -->
              <template v-if="orders.mock_history.length">
                <div class="flex items-center gap-1.5 mt-4 mb-2"><span class="text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">MOCK</span><span class="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">Mock filled · {{ orders.mock_history.length }}</span></div>
                <div v-for="(m, i) in orders.mock_history" :key="'mh' + i" class="flex items-center gap-3 rounded-xl border border-amber-500/20 bg-amber-500/5 p-3 mb-2">
                  <img v-if="m.pick_logo" :src="m.pick_logo" referrerpolicy="no-referrer" class="w-8 h-8 rounded-full object-contain bg-white/5 ring-1 ring-border-subtle shrink-0" />
                  <div v-else class="w-8 h-8 rounded-full bg-surface ring-1 ring-border-subtle flex items-center justify-center text-[10px] font-bold text-slate-400 shrink-0">{{ initials((m.pick_label || '').replace(' to win','')) }}</div>
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center justify-between gap-2">
                      <span class="text-sm font-semibold text-slate-100 truncate">{{ m.match || m.market_ticker }}</span>
                      <span class="shrink-0 text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">MOCK</span>
                    </div>
                    <div class="flex items-center justify-between gap-2 mt-0.5">
                      <span class="text-xs text-primary font-medium truncate">{{ m.pick_label || m.side }} · {{ m.contracts }} @ {{ m.price_cents }}¢</span>
                      <span class="text-xs tabular-nums shrink-0" :class="(m.realized_pnl_cents || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'">{{ m.realized_pnl_cents == null ? '—' : (m.realized_pnl_cents >= 0 ? '+' : '') + '$' + (m.realized_pnl_cents / 100).toFixed(2) }}</span>
                    </div>
                    <div v-if="m.outcome" class="text-[10px] uppercase tracking-wide text-slate-500 mt-0.5">{{ m.outcome }}</div>
                  </div>
                </div>
              </template>
            </div>
          </div>
        </div>

        <!-- Live logs -->
        <div class="glass-card rounded-2xl p-4 sm:p-5">
          <div class="flex items-center gap-2 mb-3"><span class="material-symbols-outlined text-primary text-[18px]">terminal</span><span class="text-[11px] sm:text-xs font-semibold text-slate-400 uppercase tracking-widest">Live logs</span></div>
          <InstanceLiveLogs :key="id" :instance-id="id" />
        </div>
      </template>

      <div v-else class="rounded-2xl border border-border-subtle bg-surface/20 px-8 py-12 text-center text-slate-400">
        Instance not found. <button @click="router.push('/kalshi')" class="text-primary">Back to Kalshi</button>
      </div>
    </main>

    <KalshiCreateInstanceModal
      v-if="showEdit && detail"
      :brokerages="editBrokerages"
      :initial-brokerage-id="detail.brokerage_id"
      :edit-instance="{ id, name: detail.name, config: detail.config }"
      @close="showEdit = false"
      @saved="onSaved"
    />
  </AppShell>
</template>
