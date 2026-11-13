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
  const n = Number(v)
  // Sign before the currency symbol: -$1,234.56, never $-1,234.56.
  return (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtQty(v) {
  if (v == null) return '—'
  const n = Number(v)
  return n % 1 === 0 ? n.toString() : n.toFixed(4)
}

// Chart options: the equity line is the money, so it carries gain/loss
// against the starting cash, over a hairline grid with muted axis labels.
const AXIS_LABEL = { colors: '#71717a', fontSize: '11px', fontFamily: 'inherit' }
const lineColor = computed(() =>
  (Number(currentPortfolioEvent.value?.value ?? initialCash.value) >= initialCash.value ? '#4cc38a' : '#e5484d'))

const chartOptions = computed(() => ({
  theme: { mode: 'dark' },
  chart: {
    background: 'transparent',
    fontFamily: 'Inter, sans-serif',
    toolbar: { show: false },
    parentHeightOffset: 0,
    animations: { enabled: true, easing: 'linear', dynamicAnimation: { speed: 500 } }
  },
  grid: { borderColor: 'rgba(255,255,255,0.05)', strokeDashArray: 0, xaxis: { lines: { show: false } }, padding: { left: 4, right: 8, top: 0, bottom: 0 } },
  stroke: { curve: 'smooth', width: 2 },
  fill: {
    type: 'gradient',
    gradient: {
      colorStops: [
        { offset: 0,   color: lineColor.value, opacity: 0.12 },
        { offset: 100, color: lineColor.value, opacity: 0 },
      ],
    },
  },
  colors: [lineColor.value],
  markers: { size: 0, strokeWidth: 0, hover: { size: 4 } },
  xaxis: {
    type: 'datetime',
    min: xMin.value,
    max: xMax.value,
    labels: { style: AXIS_LABEL },
    axisBorder: { show: false },
    axisTicks: { show: false },
    crosshairs: { show: true, stroke: { color: 'rgba(237,237,237,0.28)', width: 1, dashArray: 3 } },
    tooltip: { enabled: false },
  },
  yaxis: {
    labels: {
      formatter: v => '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 }),
      style: AXIS_LABEL,
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
    <main class="ui-page flex flex-col lg:h-screen lg:overflow-hidden">

      <!-- Loading State -->
      <div v-if="loading" class="flex-1 flex flex-col min-h-0" aria-hidden="true">
        <div class="ui-skel h-7 w-56"></div>
        <div class="ui-skel h-4 w-28 mt-3"></div>
        <div class="mt-6 flex-1 min-h-[420px] flex flex-col lg:flex-row gap-5">
          <div class="lg:flex-[2] min-h-[320px] rounded-xl bg-surface"></div>
          <div class="lg:flex-1 min-h-[320px] rounded-xl bg-surface"></div>
        </div>
      </div>

      <!-- Error State -->
      <div v-else-if="error" class="ui-card ui-empty">
        <div>
          <p class="text-slate-200">Failed to load playback</p>
          <p class="mt-1 text-xs text-slate-500">{{ error }}</p>
        </div>
        <button @click="router.push(`/backtests/${btId}`)" class="ui-btn">
          Back to backtest
        </button>
      </div>

      <!-- Empty State -->
      <div v-else-if="events.length === 0" class="ui-card ui-empty">
        <div>
          <p class="text-slate-200">No playback data</p>
          <p class="mt-1 text-xs text-slate-500">This backtest has no portfolio snapshots or trades to replay</p>
        </div>
        <button @click="router.push(`/backtests/${btId}`)" class="ui-btn">
          Back to backtest
        </button>
      </div>

      <!-- Main Playback UI -->
      <template v-else>
      <!-- Top header -->
      <nav class="shrink-0 -ml-3 mb-4" aria-label="Breadcrumb">
        <button @click="router.push(`/backtests/${btId}`)" class="ui-btn-ghost px-3" title="Back to backtest">
          <span class="material-symbols-outlined text-[16px]">arrow_back</span>
          Back
        </button>
      </nav>
      <header class="shrink-0 flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <div class="min-w-0">
          <h1 class="ui-page-title">Backtest playback</h1>
          <p class="ui-page-sub tabular-nums">Backtest #{{ btId }}</p>
        </div>

        <!-- Current Date + Controls -->
        <div class="flex items-center gap-4">
          <p class="text-sm text-slate-300 tabular-nums">{{ currentDate }}</p>
          <div class="flex items-center gap-1.5">
            <button @click="togglePlay" class="ui-btn-primary w-9 px-0" :aria-label="isPlaying ? 'Pause' : 'Play'" :title="isPlaying ? 'Pause' : 'Play'">
              <span class="material-symbols-outlined text-[20px]">{{ isPlaying ? 'pause' : 'play_arrow' }}</span>
            </button>
            <button @click="resetPlayback" class="ui-btn h-9 w-9 px-0" title="Reset" aria-label="Reset">
              <span class="material-symbols-outlined text-[18px]">replay</span>
            </button>
            <button @click="setSpeed" class="ui-btn h-9 min-w-12 tabular-nums" title="Playback speed">
              {{ speeds[currentSpeedIndex] }}x
            </button>
          </div>
        </div>
      </header>

      <div class="mt-6 flex-1 min-h-0 flex flex-col lg:flex-row gap-5">

        <!-- MAIN SECTION: Vertical Stepper -->
        <section class="lg:flex-[2] flex flex-col min-w-0 min-h-0 ui-card overflow-hidden" aria-labelledby="exec-log-title">
          <div class="h-12 px-5 flex items-center border-b border-white/[0.06] shrink-0">
            <h2 id="exec-log-title" class="text-sm font-medium text-slate-100">Execution log</h2>
          </div>
          <div class="flex-1 overflow-y-auto px-5 py-5 scroll-smooth max-h-[70vh] lg:max-h-none" ref="eventsContainerRef">
            <div class="flex flex-col gap-4">

              <template v-for="(ev, i) in visibleEvents" :key="ev.id">

                <!-- Date / Granularity Marker -->
                <div v-if="ev.type === 'date'" class="ev-in flex items-center gap-3 py-1">
                  <div class="h-px flex-1 bg-white/[0.06]"></div>
                  <p class="text-xs text-slate-400 tabular-nums">{{ ev.label }} — {{ ev.time }}</p>
                  <div class="h-px flex-1 bg-white/[0.06]"></div>
                </div>

                <!-- Strategy Executed -->
                <div v-else-if="ev.type === 'strategy'" class="ev-in flex gap-4">
                  <div class="w-3 shrink-0 flex flex-col items-center pt-5">
                    <span class="size-2 rounded-full bg-slate-400 shrink-0"></span>
                    <div class="w-px flex-1 bg-white/[0.06] mt-2 -mb-4" v-if="i < visibleEvents.length - 1"></div>
                  </div>
                  <div class="flex-1 min-w-0 rounded-lg border border-white/[0.06] p-4">
                    <div v-if="ev.desc.includes(' → ')" class="flex items-center gap-2 mb-1.5">
                      <span class="text-xs text-slate-400">{{ ev.desc.split(' → ')[0] }}</span>
                      <span class="inline-flex items-center gap-1.5 text-xs text-slate-200">
                        <span
                          class="size-1.5 rounded-full shrink-0"
                          :class="{
                            'bg-gain': ev.desc.split(' → ')[1] === 'BUY',
                            'bg-loss': ev.desc.split(' → ')[1] === 'SELL',
                            'bg-slate-600': ev.desc.split(' → ')[1] === 'HOLD'
                          }"
                        ></span>
                        <span class="inline-block lowercase first-letter:uppercase">{{ ev.desc.split(' → ')[1] }}</span>
                      </span>
                    </div>
                    <p v-else class="text-xs text-slate-500 mb-1.5">{{ ev.desc }}</p>
                    <p class="text-sm font-medium text-slate-100">{{ ev.name }}</p>
                    <p class="mt-2 text-sm text-slate-400"><span class="text-xs text-slate-500 mr-1.5">Reason</span>{{ ev.reason }}</p>

                    <p v-if="ev.decision" class="mt-2 text-sm text-slate-200">
                      → {{ ev.decision }}
                    </p>

                    <div v-if="ev.tickers" class="mt-2 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
                      <span class="text-xs text-slate-500 mr-1">Scanning</span>
                      <span v-for="t in ev.tickers" :key="t" class="text-slate-300">{{ t }}</span>
                    </div>
                  </div>
                </div>

                <!-- Strategy Outcome -->
                <div v-else-if="ev.type === 'outcome'" class="ev-in flex gap-4">
                  <div class="w-3 shrink-0 flex flex-col items-center pt-1.5">
                    <span class="size-2 rounded-full border border-slate-500 shrink-0"></span>
                    <div class="w-px flex-1 bg-white/[0.06] mt-2 -mb-4" v-if="i < visibleEvents.length - 1"></div>
                  </div>
                  <div class="flex-1 min-w-0">
                    <p class="text-xs font-medium text-slate-300">{{ ev.name }}</p>
                    <p class="mt-1 text-xs text-slate-500 tabular-nums break-words">{{ ev.details }}</p>
                  </div>
                </div>

                <!-- Final Decisions (Buys / Sells) -->
                <div v-else-if="ev.type === 'decision'" class="ev-in flex gap-4">
                  <div class="w-3 shrink-0 flex flex-col items-center pt-5">
                    <span class="size-2 rounded-full bg-slate-200 shrink-0"></span>
                    <div class="w-px flex-1 bg-white/[0.06] mt-2 -mb-4" v-if="i < visibleEvents.length - 1"></div>
                  </div>
                  <div class="flex-1 min-w-0 rounded-lg border border-white/[0.06] p-4">
                    <p class="text-xs text-slate-500 mb-2">Execution decisions</p>

                    <p v-if="ev.buys.length === 0 && ev.sells.length === 0" class="text-sm text-slate-500">
                      No actions taken
                    </p>

                    <div class="divide-y divide-white/[0.05]">
                      <div v-for="b in ev.buys" :key="b.ticker" class="py-2 first:pt-0 last:pb-0 flex flex-col sm:flex-row sm:items-center justify-between gap-1 sm:gap-4">
                        <div class="flex items-center gap-2 text-sm min-w-0">
                          <span class="size-1.5 rounded-full bg-gain shrink-0"></span>
                          <span class="text-slate-300">Buy</span>
                          <span class="font-medium text-slate-100">{{ b.ticker }}</span>
                          <span class="text-slate-400 tabular-nums">{{ fmtQty(b.qty) }} shares @ {{ fmtMoney(b.price) }}</span>
                        </div>
                        <p class="text-xs text-slate-500 sm:text-right" v-if="b.reason">{{ b.reason }}</p>
                      </div>

                      <div v-for="s in ev.sells" :key="s.ticker" class="py-2 first:pt-0 last:pb-0 flex flex-col sm:flex-row sm:items-center justify-between gap-1 sm:gap-4">
                        <div class="flex items-center gap-2 text-sm min-w-0">
                          <span class="size-1.5 rounded-full bg-loss shrink-0"></span>
                          <span class="text-slate-300">Sell</span>
                          <span class="font-medium text-slate-100">{{ s.ticker }}</span>
                          <span class="text-slate-400 tabular-nums">{{ fmtQty(s.qty) }} shares @ {{ fmtMoney(s.price) }}</span>
                        </div>
                        <p class="text-xs text-slate-500 sm:text-right" v-if="s.reason">{{ s.reason }}</p>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- Skip Portfolio internally for left pane -->
                <div v-else-if="ev.type === 'portfolio'" class="hidden"></div>

              </template>

              <div v-if="isPlaying" class="flex gap-4" aria-hidden="true">
                <div class="w-3 shrink-0 flex justify-center pt-1">
                  <span class="size-2 rounded-full bg-slate-700"></span>
                </div>
                <div class="ui-skel h-4 w-1/3"></div>
              </div>

            </div>
          </div>
        </section>

        <!-- RIGHT SIDEBAR: Chart & Holdings -->
        <div class="lg:flex-1 flex flex-col gap-5 lg:min-w-[300px] min-h-0 lg:overflow-y-auto">

          <!-- Portfolio Graph -->
          <section class="ui-card p-5 flex flex-col h-[350px] shrink-0" aria-labelledby="pb-value-title">
            <p id="pb-value-title" class="ui-label">Portfolio value</p>
            <p class="mt-2 text-[32px] leading-none font-medium text-slate-100 tabular-nums">{{ fmtMoney(currentPortfolioEvent.value) }}</p>
            <div class="chart flex-1 min-h-0 relative -ml-2 -mr-1 mt-4">
              <VueApexCharts
                type="area"
                height="100%"
                :options="chartOptions"
                :series="graphSeries"
              />
            </div>
          </section>

          <!-- Current Holdings -->
          <section class="ui-card flex flex-col lg:flex-1 min-h-0 overflow-hidden" aria-labelledby="pb-holdings-title">
            <div class="h-12 px-5 flex items-center border-b border-white/[0.06] shrink-0">
              <h2 id="pb-holdings-title" class="text-sm font-medium text-slate-100">Current holdings</h2>
            </div>

            <div class="flex-1 overflow-y-auto">
              <p v-if="currentPortfolioEvent.holdings.length === 0" class="px-5 py-8 text-center text-sm text-slate-500">
                No active positions
              </p>
              <ul v-else class="ui-rows">
                <li v-for="h in currentPortfolioEvent.holdings" :key="h.ticker" class="min-h-14 px-5 py-2 flex items-center justify-between gap-3">
                  <span class="min-w-0">
                    <span class="block text-sm font-medium text-slate-100">{{ h.ticker }}</span>
                    <span class="block text-xs text-slate-500 mt-0.5 tabular-nums">{{ fmtQty(h.qty) }} sh</span>
                  </span>
                  <span class="text-right tabular-nums">
                    <span v-if="h.curr" class="block text-sm" :class="(h.curr > h.avg) ? 'text-gain' : (h.curr < h.avg ? 'text-loss' : 'text-slate-300')">
                      {{ fmtMoney(h.curr) }}
                    </span>
                    <span class="block text-xs text-slate-500" :class="h.curr ? 'mt-0.5' : ''">avg {{ fmtMoney(h.avg) }}</span>
                  </span>
                </li>
              </ul>
            </div>
          </section>

        </div>
      </div>
      </template>
    </main>
  </AppShell>
</template>

<style scoped>
/* New steps fade in where they appear: the one motion that explains the replay. */
.ev-in {
  animation: ev-in 240ms cubic-bezier(0.22, 1, 0.36, 1) both;
}

@keyframes ev-in {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: none; }
}

@media (prefers-reduced-motion: reduce) {
  .ev-in { animation: none; }
}

.chart :deep(.apexcharts-text) { font-variant-numeric: tabular-nums; }
</style>
