<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '../layouts/AppShell.vue'
import VueApexCharts from 'vue3-apexcharts'
import { getToken } from '../utils/auth.js'

const route = useRoute()
const router = useRouter()
const btId = computed(() => route.params.id)

const API_BASE = import.meta.env.DEV
  ? '/api'
  : (import.meta.env.VITE_API_URL || '/api')

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

// --- State ---
const events = ref([])
const metadata = ref(null)
const loading = ref(true)
const error = ref('')
const playbackIndex = ref(-1)
const isPlaying = ref(false)
const speeds = [0.5, 1, 2, 5, 10]
const currentSpeedIndex = ref(2)
let timer = null

// --- Data Fetching ---
async function fetchPlaybackData() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`${API_BASE}/backtests/${btId.value}/playback-data`, { headers: authHeaders() })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    const data = await res.json()
    events.value = data.events || []
    metadata.value = data.metadata || {}
  } catch (e) {
    error.value = e.message
    events.value = []
    metadata.value = null
  } finally {
    loading.value = false
  }
}

// --- Computed ---
const visibleEvents = computed(() => events.value.slice(0, playbackIndex.value + 1))
const isFinished = computed(() => playbackIndex.value >= events.value.length - 1)

const initialCash = computed(() => (metadata.value?.initial_cash) || 100000)

// Get the last portfolio state up to the current playback index
const currentPortfolioEvent = computed(() => {
  for (let i = playbackIndex.value; i >= 0; i--) {
    if (events.value[i]?.type === 'portfolio') return events.value[i]
  }
  return { value: initialCash.value, holdings: [], date: null }
})

// Pre-compute all portfolio timestamps for fixed x-axis range
const allPortfolioPoints = computed(() =>
  events.value
    .filter(e => e.type === 'portfolio' && e.date)
    .map(e => ({
      time: new Date(e.date).getTime(),
      value: e.value
    }))
)

const xMin = computed(() => {
  const pts = allPortfolioPoints.value
  return pts.length ? pts[0].time - 86400000 : Date.now() - 7 * 86400000
})
const xMax = computed(() => {
  const pts = allPortfolioPoints.value
  return pts.length ? pts[pts.length - 1].time : Date.now()
})

const graphSeries = computed(() => {
  const data = []
  data.push([xMin.value, initialCash.value])
  for (let i = 0; i <= playbackIndex.value; i++) {
    const ev = events.value[i]
    if (ev?.type === 'portfolio' && ev.date) {
      data.push([new Date(ev.date).getTime(), ev.value])
    }
  }
  return [{ name: 'Portfolio Value', data }]
})

// --- Control Methods ---
const advanceFrame = () => {
  if (playbackIndex.value < events.value.length - 1) {
    playbackIndex.value++
    scrollToBottom()
  } else {
    stopPlayback()
  }
}

const togglePlay = () => {
  if (isPlaying.value) {
    stopPlayback()
  } else {
    if (isFinished.value) {
      playbackIndex.value = -1 // Restart
    }
    isPlaying.value = true
    const delay = 1000 / speeds[currentSpeedIndex.value]
    timer = setInterval(advanceFrame, delay)
  }
}

const stopPlayback = () => {
  isPlaying.value = false
  if (timer) clearInterval(timer)
}

const setSpeed = () => {
  // Cycle through speeds
  currentSpeedIndex.value = (currentSpeedIndex.value + 1) % speeds.length
  if (isPlaying.value) {
    stopPlayback()
    togglePlay()
  }
}

const resetPlayback = () => {
  stopPlayback()
  playbackIndex.value = -1
}

// Ensure the stepper container scrolls down as new items appear
const eventsContainerRef = ref(null)
const scrollToBottom = async () => {
  await nextTick()
  if (eventsContainerRef.value) {
    eventsContainerRef.value.scrollTop = eventsContainerRef.value.scrollHeight
  }
}

onMounted(async () => {
  await fetchPlaybackData()
  if (events.value.length > 0) {
    playbackIndex.value = 0
  }
})

onUnmounted(() => {
  stopPlayback()
})

// --- Current playback date ---
const currentDate = computed(() => {
  for (let i = playbackIndex.value; i >= 0; i--) {
    if (events.value[i]?.type === 'date') return `${events.value[i].label} — ${events.value[i].time}`
  }
  return '—'
})

// --- Formatting ---
function fmtMoney(v) {
  if (v == null) return '—'
  return '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtQty(v) {
  if (v == null) return '—'
  const n = Number(v)
  return n % 1 === 0 ? n.toString() : n.toFixed(4)
}

// Chart Options
const chartOptions = computed(() => ({
  theme: { mode: 'dark' },
  chart: {
    background: 'transparent',
    toolbar: { show: false },
    animations: { enabled: true, easing: 'linear', dynamicAnimation: { speed: 500 } }
  },
  grid: { borderColor: '#1e2535', strokeDashArray: 3 },
  stroke: { curve: 'smooth', width: 2.5 },
  fill: { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.3, opacityTo: 0.03 } },
  colors: ['#38bdf8'],
  xaxis: {
    type: 'datetime',
    min: xMin.value,
    max: xMax.value,
    labels: { style: { colors: '#64748b' } },
    crosshairs: { show: true, stroke: { color: '#38bdf8', width: 1, dashArray: 3 } },
  },
  yaxis: {
    labels: {
      formatter: v => '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 }),
      style: { colors: '#64748b' },
    },
  },
  dataLabels: { enabled: false },
  tooltip: {
    theme: 'dark',
    y: { formatter: v => fmtMoney(v) },
    x: { format: 'dd MMM yyyy' }
  }
}))
</script>


<template>
  <AppShell>
    <main class="flex flex-col h-screen overflow-hidden py-6 px-4 sm:px-6 lg:px-8 max-w-[1600px] mx-auto w-full">
      
      <!-- Loading State -->
      <div v-if="loading" class="flex-1 flex items-center justify-center">
        <div class="text-center">
          <div class="animate-spin rounded-full h-10 w-10 border-2 border-sky-500 border-t-transparent mx-auto mb-4"></div>
          <p class="text-sm text-slate-400">Loading playback data…</p>
        </div>
      </div>

      <!-- Error State -->
      <div v-else-if="error" class="flex-1 flex items-center justify-center">
        <div class="text-center max-w-md">
          <span class="material-symbols-outlined text-4xl text-red-400 mb-3 block">error</span>
          <p class="text-sm text-slate-300 font-semibold mb-1">Failed to load playback</p>
          <p class="text-xs text-slate-500 mb-4">{{ error }}</p>
          <button @click="router.push(`/backtests/${btId}`)" class="px-4 py-2 rounded-lg bg-surface border border-border-subtle text-sm text-slate-300 hover:text-white transition-colors">
            ← Back to Backtest
          </button>
        </div>
      </div>

      <!-- Empty State -->
      <div v-else-if="events.length === 0" class="flex-1 flex items-center justify-center">
        <div class="text-center max-w-md">
          <span class="material-symbols-outlined text-4xl text-slate-500 mb-3 block">play_disabled</span>
          <p class="text-sm text-slate-300 font-semibold mb-1">No playback data</p>
          <p class="text-xs text-slate-500 mb-4">This backtest has no portfolio snapshots or trades to replay.</p>
          <button @click="router.push(`/backtests/${btId}`)" class="px-4 py-2 rounded-lg bg-surface border border-border-subtle text-sm text-slate-300 hover:text-white transition-colors">
            ← Back to Backtest
          </button>
        </div>
      </div>

      <!-- Main Playback UI -->
      <template v-else>
      <!-- Top header -->
      <div class="shrink-0 flex items-center justify-between mb-4">
        <div class="flex items-center gap-2.5">
          <button @click="router.push(`/backtests/${btId}`)" class="p-1.5 rounded-lg bg-surface border border-border-subtle text-slate-400 hover:text-slate-200 hover:bg-white/5 transition-colors">
            <span class="material-symbols-outlined text-[16px]">arrow_back</span>
          </button>
          <div>
            <h1 class="text-lg font-bold text-slate-100 flex items-center gap-1.5">
              Backtest Playback <span class="px-1.5 py-px rounded text-[9px] font-mono leading-none bg-amber-500/10 text-amber-400 border border-amber-500/20">LIVE</span>
            </h1>
            <p class="text-[11px] text-slate-500 mt-0.5">Backtest #{{ btId }}</p>
          </div>
        </div>

        <!-- Current Date + Controls -->
        <div class="flex items-center gap-2">
          <div class="flex items-center gap-2 p-1.5 rounded-xl bg-[#0d1117] border border-border-subtle shadow-xl">
            <span class="material-symbols-outlined text-[14px] text-sky-400 ml-1">calendar_today</span>
            <span class="text-xs font-mono font-semibold text-slate-200 mr-1">{{ currentDate }}</span>
          </div>
          <div class="flex items-center p-1.5 rounded-xl bg-[#0d1117] border border-border-subtle shadow-xl z-20 gap-1.5">
            <button @click="togglePlay" class="flex items-center justify-center size-8 rounded-lg transition-colors" :class="isPlaying ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20' : 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'">
              <span class="material-symbols-outlined text-lg">{{ isPlaying ? 'pause' : 'play_arrow' }}</span>
            </button>
            <button @click="resetPlayback" class="flex items-center justify-center size-8 rounded-lg bg-surface border border-border-subtle text-slate-400 hover:text-slate-200 transition-colors" title="Reset">
              <span class="material-symbols-outlined text-[16px]">replay</span>
            </button>
            <button @click="setSpeed" class="flex items-center justify-center px-3 h-8 rounded-lg bg-surface border border-border-subtle text-slate-300 hover:text-white transition-colors text-[11px] font-mono font-bold">
              {{ speeds[currentSpeedIndex] }}x
            </button>
          </div>
        </div>
      </div>

      <div class="flex-1 min-h-0 flex gap-5 relative">
        
        <!-- MAIN SECTION: Vertical Stepper -->
        <div class="flex-[2] flex flex-col min-w-0 min-h-0 bg-[#0d1117]/50 rounded-3xl border border-border-subtle overflow-hidden relative backdrop-blur-sm">
          <div class="p-4 border-b border-border-subtle bg-[#04040c] shrink-0">
            <h2 class="text-sm font-semibold text-slate-200">Execution Log</h2>
          </div>
          <div class="flex-1 overflow-y-auto p-6 scroll-smooth" ref="eventsContainerRef">
            <div class="flex flex-col gap-6">
              
              <template v-for="(ev, i) in visibleEvents" :key="ev.id">
                
                <!-- Date / Granularity Marker -->
                <div v-if="ev.type === 'date'" class="relative flex items-center gap-4 animate-fade-in-up">
                  <div class="h-px flex-1 bg-border-subtle"></div>
                  <div class="px-3 py-1.5 rounded-full border border-primary/30 bg-primary/10 text-primary text-xs font-bold uppercase tracking-wider shadow-[0_0_15px_-3px_rgba(56,189,248,0.3)]">
                    {{ ev.label }} — {{ ev.time }}
                  </div>
                  <div class="h-px flex-1 bg-border-subtle"></div>
                </div>

                <!-- Strategy Executed -->
                <div v-else-if="ev.type === 'strategy'" class="flex gap-4 animate-fade-in-up">
                  <div class="flex flex-col items-center">
                    <div class="size-8 rounded-full border border-indigo-500/30 bg-indigo-500/10 flex items-center justify-center shrink-0 z-10 text-indigo-400 shadow-[0_0_10px_-2px_rgba(99,102,241,0.4)]">
                      <span class="material-symbols-outlined text-[16px]">psychology</span>
                    </div>
                    <div class="w-px flex-1 bg-border-subtle mt-2 mb-[-12px]" v-if="i < visibleEvents.length - 1"></div>
                  </div>
                  <div class="flex-1 pb-2">
                    <div class="glass-card rounded-2xl p-4 border-indigo-500/20 hover:border-indigo-500/40 transition-colors">
                      <div class="flex items-start justify-between mb-2">
                        <div>
                          <div v-if="ev.desc.includes(' → ')" class="flex items-center gap-2 mb-1.5">
                            <span class="text-xs font-bold text-slate-300 font-mono">{{ ev.desc.split(' → ')[0] }}</span>
                            <span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider"
                                  :class="{
                                    'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30': ev.desc.split(' → ')[1] === 'BUY',
                                    'bg-red-500/20 text-red-400 border border-red-500/30': ev.desc.split(' → ')[1] === 'SELL',
                                    'bg-slate-500/20 text-slate-400 border border-slate-500/30': ev.desc.split(' → ')[1] === 'HOLD'
                                  }">
                              {{ ev.desc.split(' → ')[1] }}
                            </span>
                          </div>
                          <p v-else class="text-[10px] uppercase font-bold text-indigo-400 tracking-wider mb-1.5">{{ ev.desc }}</p>
                          <p class="text-sm font-bold text-slate-100">{{ ev.name }}</p>
                        </div>
                      </div>
                      <p class="text-xs text-slate-400 mb-2"><span class="text-slate-500 font-semibold uppercase text-[10px] mr-1">Rsn:</span> {{ ev.reason }}</p>
                      
                      <div v-if="ev.decision" class="text-xs font-mono text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-3 py-1.5 rounded-lg inline-block">
                        -> {{ ev.decision }}
                      </div>
                      
                      <div v-if="ev.tickers" class="flex flex-wrap gap-1.5 mt-2">
                        <span class="text-[10px] uppercase font-bold text-slate-500 self-center mr-1">Scanning:</span>
                        <span v-for="t in ev.tickers" :key="t" class="px-2 py-0.5 rounded bg-surface border border-border-subtle text-[11px] font-mono font-semibold text-slate-300">
                          {{ t }}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- Strategy Outcome -->
                <div v-else-if="ev.type === 'outcome'" class="flex gap-4 animate-fade-in-up">
                  <div class="flex flex-col items-center">
                    <div class="size-8 rounded-full border border-sky-500/30 bg-sky-500/10 flex items-center justify-center shrink-0 z-10 text-sky-400 shadow-[0_0_10px_-2px_rgba(56,189,248,0.4)]">
                      <span class="material-symbols-outlined text-[16px]">score</span>
                    </div>
                    <div class="w-px flex-1 bg-border-subtle mt-2 mb-[-12px]" v-if="i < visibleEvents.length - 1"></div>
                  </div>
                  <div class="flex-1 pb-2">
                    <div class="p-3 border-l-2 border-sky-500/50 bg-gradient-to-r from-sky-500/5 to-transparent rounded-r-xl">
                      <p class="text-xs font-semibold text-sky-400 mb-1">{{ ev.name }}</p>
                      <p class="text-[11px] font-mono text-slate-400 break-words">{{ ev.details }}</p>
                    </div>
                  </div>
                </div>

                <!-- Final Decisions (Buys / Sells) -->
                <div v-else-if="ev.type === 'decision'" class="flex gap-4 animate-fade-in-up">
                  <div class="flex flex-col items-center">
                    <div class="size-8 rounded-full border border-amber-500/30 bg-amber-500/10 flex items-center justify-center shrink-0 z-10 text-amber-400 shadow-[0_0_10px_-2px_rgba(245,158,11,0.4)]">
                      <span class="material-symbols-outlined text-[16px]">gavel</span>
                    </div>
                    <div class="w-px flex-1 bg-border-subtle mt-2 mb-[-12px]" v-if="i < visibleEvents.length - 1"></div>
                  </div>
                  <div class="flex-1 pb-2">
                    <div class="glass-card rounded-2xl p-4 border-amber-500/20">
                      <p class="text-[10px] uppercase font-bold text-amber-500 tracking-wider mb-3">Execution Decisions</p>
                      
                      <div v-if="ev.buys.length === 0 && ev.sells.length === 0" class="text-xs text-slate-500 italic">
                        No actions taken.
                      </div>

                      <div class="space-y-2">
                        <div v-for="b in ev.buys" :key="b.ticker" class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 p-2 rounded-xl bg-emerald-500/5 border border-emerald-500/20">
                          <div class="flex items-center gap-2">
                            <span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-emerald-500/20 text-emerald-400">Buy</span>
                            <span class="text-sm font-bold font-mono text-slate-200">{{ b.ticker }}</span>
                            <span class="text-xs text-slate-400">{{ fmtQty(b.qty) }} shares @ {{ fmtMoney(b.price) }}</span>
                          </div>
                          <p class="text-[10px] text-slate-500 italic sm:text-right" v-if="b.reason">{{ b.reason }}</p>
                        </div>
                        
                        <div v-for="s in ev.sells" :key="s.ticker" class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 p-2 rounded-xl bg-red-500/5 border border-red-500/20">
                          <div class="flex items-center gap-2">
                            <span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-red-500/20 text-red-400">Sell</span>
                            <span class="text-sm font-bold font-mono text-slate-200">{{ s.ticker }}</span>
                            <span class="text-xs text-slate-400">{{ fmtQty(s.qty) }} shares @ {{ fmtMoney(s.price) }}</span>
                          </div>
                          <p class="text-[10px] text-slate-500 italic sm:text-right" v-if="s.reason">{{ s.reason }}</p>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- Skip Portfolio internally for left pane -->
                <div v-else-if="ev.type === 'portfolio'"></div>

              </template>
              
              <div v-if="isPlaying" class="flex gap-4 animate-pulse pt-2">
                <div class="size-8 rounded-full border border-slate-700 bg-surface flex items-center justify-center shrink-0 z-10 text-slate-500">
                  <span class="material-symbols-outlined text-[16px]">more_horiz</span>
                </div>
                <div class="flex-1 pb-4">
                  <div class="h-8 glass-card rounded-2xl border-slate-700 w-1/3"></div>
                </div>
              </div>

            </div>
          </div>
        </div>

        <!-- RIGHT SIDEBAR: Chart & Holdings -->
        <div class="flex-1 flex flex-col gap-4 min-w-[300px] min-h-0 overflow-y-auto">
          
          <!-- Portfolio Graph -->
          <div class="glass-card rounded-3xl p-5 border shadow-xl flex flex-col h-[350px] shrink-0">
            <div class="flex items-start justify-between mb-2">
              <div>
                <p class="text-xs font-semibold text-slate-400 uppercase tracking-widest mb-1">Portfolio Value</p>
                <div class="flex items-baseline gap-2">
                  <h3 class="text-3xl font-bold font-mono text-slate-100 transition-all duration-300">{{ fmtMoney(currentPortfolioEvent.value) }}</h3>
                </div>
              </div>
            </div>
            <div class="flex-1 min-h-0 relative -ml-3 -mr-2 mt-4">
              <VueApexCharts
                type="area"
                height="100%"
                :options="chartOptions"
                :series="graphSeries"
              />
            </div>
          </div>

          <!-- Current Holdings -->
          <div class="glass-card rounded-3xl p-5 border shadow-xl flex flex-col flex-1 min-h-0">
            <h3 class="text-xs font-semibold text-slate-400 uppercase tracking-widest mb-3 shrink-0">Current Holdings</h3>
            
            <div class="flex-1 overflow-y-auto">
              <div class="grid grid-cols-2 gap-2">
                <div v-if="currentPortfolioEvent.holdings.length === 0" class="col-span-2 text-xs text-slate-500 italic flex items-center justify-center py-6">
                  No active positions.
                </div>
                <div v-else v-for="h in currentPortfolioEvent.holdings" :key="h.ticker" class="bg-[#04040c] px-3 py-2 rounded-xl border border-border-subtle hover:border-slate-600 transition-colors">
                  <div class="flex items-center justify-between mb-1">
                    <span class="font-bold font-mono text-[13px] text-slate-200">{{ h.ticker }}</span>
                    <span class="font-mono text-[10px] text-slate-400 bg-surface px-1.5 py-0.5 rounded">{{ fmtQty(h.qty) }} sh</span>
                  </div>
                  <div class="flex items-center justify-between text-[10px] font-mono text-slate-500">
                    <span>{{ fmtMoney(h.avg) }}</span>
                    <span v-if="h.curr" class="font-semibold" :class="(h.curr > h.avg) ? 'text-emerald-400' : (h.curr < h.avg ? 'text-red-400' : 'text-slate-400')">
                      {{ fmtMoney(h.curr) }}
                    </span>
                  </div>
                  <div v-if="h.curr" class="mt-1.5 h-[3px] w-full bg-slate-800 rounded-full overflow-hidden">
                    <div class="h-full rounded-full transition-all duration-500" :class="h.curr >= h.avg ? 'bg-emerald-500' : 'bg-red-500'" :style="{ width: Math.min(100, 40 + Math.abs((h.curr - h.avg)/h.avg)*200) + '%' }"></div>
                  </div>
                </div>
              </div>
            </div>
          </div>

        </div>
      </div>
      </template>
    </main>
  </AppShell>
</template>

<style scoped>
.animate-fade-in-up {
  animation: fadeInUp 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

@keyframes textShimmer {
  0% { background-position: -200% center; }
  100% { background-position: 200% center; }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(15px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>


