<script setup>
// Landing hero: the headline, the page's actions (passed in by the view, so
// each view keeps its own CTA logic) and a preview of the real dashboard.
// The preview is built from the app's own patterns with made-up data — one
// account's trading day — so the first thing a visitor sees is the product.
import Sparkline from '../dashboard/Sparkline.vue'

// Deterministic random walk pinned to open at `from` and close at `to`.
function walk(n, from, to, vol, seed) {
  let s = seed
  const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647) - 0.5
  const raw = [0]
  for (let i = 1; i < n; i++) raw.push(raw[i - 1] + rnd() * vol)
  const end = raw[n - 1]
  return raw.map((v, i) => {
    const t = i / (n - 1)
    return from + (to - from) * t + v - end * t
  })
}

const usd = (n) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n)
const signedUsd = (n) => (n >= 0 ? '+' : '−') + usd(Math.abs(n))
const signedPct = (n) => (n >= 0 ? '+' : '−') + Math.abs(n).toFixed(2) + '%'
const signClass = (n) => (n >= 0 ? 'text-gain' : 'text-loss')

// One account, one day. Holdings + cash add up to the headline value and the
// holdings' day P&L adds up to the headline change.
const account = { name: 'Alpaca live', kind: 'Alpaca · Live', open: 24542.15, value: 24860.42 }
const change = account.value - account.open
const changePct = (change / account.open) * 100

const cash = 1201.02
const holdings = [
  { symbol: 'QQQ',  qty: 14, value: 7173.60, pnl: 84.12,  pct: 1.19 },
  { symbol: 'TQQQ', qty: 60, value: 4692.00, pnl: 205.55, pct: 4.58 },
  { symbol: 'GLD',  qty: 18, value: 4339.80, pnl: -21.60, pct: -0.50 },
  { symbol: 'MSFT', qty: 9,  value: 3762.00, pnl: 37.80,  pct: 1.01 },
  { symbol: 'XLE',  qty: 40, value: 3692.00, pnl: 12.40,  pct: 0.34 },
].map((h, i) => ({
  ...h,
  weight: (h.value / account.value) * 100,
  spark: walk(26, 100, 100 * (1 + h.pct / 100), 0.9, 7 + i * 13),
}))

// Equity line, 5-minute bars from the open to now.
const W = 600
const H = 180
const series = walk(79, account.open, account.value, 58, 41)
const lo = Math.min(...series, account.open)
const hi = Math.max(...series, account.open)
const pad = (hi - lo) * 0.12
const yOf = (v) => H - ((v - (lo - pad)) / (hi - lo + pad * 2)) * H
const pts = series.map((v, i) => [(i / (series.length - 1)) * W, yOf(v)])

// Catmull-Rom through the points, as cubic Béziers, for a smooth line.
const linePath = pts.reduce((d, p, i) => {
  if (i === 0) return `M${p[0].toFixed(1)},${p[1].toFixed(1)}`
  const p0 = pts[i - 2] || pts[i - 1]
  const p1 = pts[i - 1]
  const p3 = pts[i + 1] || p
  const c1 = [p1[0] + (p[0] - p0[0]) / 6, p1[1] + (p[1] - p0[1]) / 6]
  const c2 = [p[0] - (p3[0] - p1[0]) / 6, p[1] - (p3[1] - p1[1]) / 6]
  return `${d} C${c1[0].toFixed(1)},${c1[1].toFixed(1)} ${c2[0].toFixed(1)},${c2[1].toFixed(1)} ${p[0].toFixed(1)},${p[1].toFixed(1)}`
}, '')
const areaPath = `${linePath} L${W},${H} L0,${H} Z`
const openY = yOf(account.open)

const ranges = ['1D', '1W', '1M', '3M', 'YTD', '1Y', 'ALL']

const nav = [
  { label: 'Trading', items: [
    { label: 'Dashboard', icon: 'dashboard', active: true },
    { label: 'Brokerages', icon: 'account_balance' },
    { label: 'Instances', icon: 'memory' },
  ] },
  { label: 'Research', items: [
    { label: 'Backtests', icon: 'analytics' },
    { label: 'Nexus graph', icon: 'hub' },
    { label: 'Strategies', icon: 'schema' },
  ] },
]

const instances = [
  { name: 'Momentum live', sub: 'Alpaca · Live', status: 'Running', dot: 'ui-dot-live', text: 'text-slate-200' },
  { name: 'Swing paper', sub: 'Alpaca · Paper', status: 'Paused', dot: 'ui-dot-warn', text: 'text-slate-300' },
  { name: 'Nexus lab', sub: 'Alpaca · Paper', status: 'Stopped', dot: 'ui-dot-off', text: 'text-slate-500' },
]

const orders = [
  { side: 'Buy', qty: 4, symbol: 'TQQQ', price: 78.20, time: '10:42', instance: 'Momentum live' },
  { side: 'Sell', qty: 6, symbol: 'GLD', price: 241.10, time: '10:15', instance: 'Momentum live' },
  { side: 'Buy', qty: 2, symbol: 'QQQ', price: 512.40, time: '9:48', instance: 'Momentum live' },
  { side: 'Buy', qty: 3, symbol: 'MSFT', price: 418.00, time: '9:41', instance: 'Momentum live' },
]
</script>

<template>
  <section class="pt-16" aria-labelledby="hero-title">
    <div class="mx-auto w-full max-w-[1200px] px-4 sm:px-6 lg:px-8">

      <!-- Headline left, the case and the actions right -->
      <div class="pt-14 sm:pt-20 lg:pt-24 grid gap-x-16 gap-y-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)] lg:items-end">
        <h1
          id="hero-title"
          class="max-w-[14ch] text-[40px] leading-[1.06] sm:text-[56px] lg:text-[64px] lg:leading-[1.02] font-medium tracking-[-0.03em] text-slate-100"
        >
          An AI trading desk you run yourself
        </h1>
        <div>
          <p class="text-base leading-7 sm:text-lg sm:leading-8 text-slate-400">
            IntelliStock combines a company-relationship graph, real-time market data and automated
            execution. Backtest a strategy, then let it trade a paper or live Alpaca account.
          </p>
          <div class="mt-6 flex flex-wrap items-center gap-3">
            <slot name="actions" />
          </div>
        </div>
      </div>

      <!-- Product preview: the dashboard, with made-up data -->
      <div
        class="mt-12 sm:mt-16 rounded-2xl border border-white/[0.08] bg-background-dark overflow-hidden shadow-[0_24px_48px_rgba(0,0,0,0.5)] select-none"
        role="img"
        aria-label="IntelliStock dashboard: an Alpaca live account up 1.30% today, its holdings, running instances and recent orders"
      >
        <div class="flex" aria-hidden="true">

          <!-- Sidebar -->
          <aside class="hidden lg:flex w-52 shrink-0 flex-col bg-sidebar border-r border-white/[0.06]">
            <div class="h-14 px-4 flex items-center">
              <img src="/new-logo.png" alt="" width="1200" height="240" class="block h-6 w-auto object-contain object-left" draggable="false" />
            </div>
            <div class="px-3 pb-5">
              <div v-for="(group, gi) in nav" :key="group.label" :class="gi === 0 ? 'pt-1' : 'pt-5'">
                <p class="px-3 pb-2 text-xs text-slate-500">{{ group.label }}</p>
                <div class="space-y-0.5">
                  <div
                    v-for="item in group.items"
                    :key="item.label"
                    class="h-8 px-3 flex items-center gap-3 rounded-lg text-[13px]"
                    :class="item.active ? 'bg-raised text-slate-100 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]' : 'text-slate-400'"
                  >
                    <span class="material-symbols-outlined w-[18px] text-[18px]" :class="item.active ? 'text-slate-200' : 'text-slate-500'">{{ item.icon }}</span>
                    {{ item.label }}
                  </div>
                </div>
              </div>
            </div>
          </aside>

          <!-- Main -->
          <div class="flex-1 min-w-0 p-5 sm:p-6 lg:p-7">
            <div>
              <p class="text-xl font-medium text-slate-100 leading-tight">Dashboard</p>
              <p class="mt-1 text-sm text-slate-500 flex items-center gap-2">
                Thursday, November 12
                <span class="text-slate-600">·</span>
                <span class="inline-flex items-center gap-1.5 text-slate-300">
                  <span class="ui-dot ui-dot-live"></span>
                  Market open
                </span>
              </p>
            </div>

            <div class="mt-6 grid gap-x-7 gap-y-8 lg:grid-cols-[minmax(0,1fr)_248px] items-start">

              <!-- Equity + holdings -->
              <div class="min-w-0">
                <p class="text-sm text-slate-400"><span class="font-medium text-slate-200">{{ account.name }}</span></p>
                <p class="mt-1 text-xs text-slate-500">{{ account.kind }}</p>
                <p class="mt-4 text-[32px] sm:text-[40px] leading-none font-medium tracking-[-0.02em] text-slate-100 tabular-nums">{{ usd(account.value) }}</p>
                <p class="mt-2 text-sm tabular-nums">
                  <span class="text-gain">{{ signedUsd(change) }} ({{ signedPct(changePct) }})</span>
                  <span class="ml-1.5 text-slate-500">Today</span>
                </p>

                <svg class="mt-5 block w-full h-[140px] sm:h-[180px]" :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none">
                  <defs>
                    <linearGradient id="hero-area" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0" stop-color="#4cc38a" stop-opacity="0.12" />
                      <stop offset="1" stop-color="#4cc38a" stop-opacity="0" />
                    </linearGradient>
                  </defs>
                  <line x1="0" :y1="openY" :x2="W" :y2="openY" stroke="rgba(237,237,237,0.28)" stroke-width="1" stroke-dasharray="2 3" vector-effect="non-scaling-stroke" />
                  <path :d="areaPath" fill="url(#hero-area)" />
                  <path :d="linePath" fill="none" stroke="#4cc38a" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" />
                </svg>

                <div class="mt-4 pb-4 flex items-center gap-1 border-b border-white/[0.06]">
                  <span
                    v-for="r in ranges"
                    :key="r"
                    class="h-8 px-2.5 sm:px-3 inline-flex items-center rounded-md text-xs font-medium tabular-nums"
                    :class="r === '1D' ? 'bg-white/[0.06] text-gain' : 'text-slate-500'"
                  >{{ r }}</span>
                </div>

                <div class="mt-8 mb-3 flex items-end justify-between gap-4">
                  <div>
                    <p class="text-base font-medium text-slate-100">Holdings</p>
                    <p class="mt-0.5 text-sm text-slate-500 tabular-nums">{{ holdings.length }} positions</p>
                  </div>
                  <div class="ui-seg">
                    <span class="ui-seg-btn ui-seg-btn-active inline-flex items-center">Daily</span>
                    <span class="ui-seg-btn inline-flex items-center">Total</span>
                  </div>
                </div>

                <div class="ui-card overflow-hidden">
                  <div class="hero-hold ui-th hidden sm:grid">
                    <span>Symbol</span>
                    <span class="hidden md:block">Weight</span>
                    <span>Today</span>
                    <span class="text-right">Value</span>
                    <span class="text-right">Day P&amp;L</span>
                  </div>
                  <div class="ui-rows">
                    <div v-for="h in holdings" :key="h.symbol" class="hero-hold grid items-center min-h-12 px-4 sm:px-5 py-2">
                      <span class="min-w-0">
                        <span class="block text-sm font-medium text-slate-100">{{ h.symbol }}</span>
                        <span class="block mt-0.5 text-xs text-slate-500 tabular-nums">{{ h.qty }} shares</span>
                      </span>
                      <span class="hidden md:flex items-center gap-2">
                        <span class="hero-track"><span class="hero-fill" :style="{ transform: `scaleX(${h.weight / 100})` }"></span></span>
                        <span class="w-8 text-xs text-slate-400 tabular-nums">{{ h.weight.toFixed(0) }}%</span>
                      </span>
                      <span class="hidden sm:flex items-center">
                        <Sparkline :values="h.spark" :width="72" :height="20" />
                      </span>
                      <span class="text-right text-sm text-slate-100 tabular-nums">{{ usd(h.value) }}</span>
                      <span class="text-right tabular-nums">
                        <span class="block text-sm" :class="signClass(h.pnl)">{{ signedUsd(h.pnl) }}</span>
                        <span class="block mt-0.5 text-xs" :class="signClass(h.pct)">{{ signedPct(h.pct) }}</span>
                      </span>
                    </div>
                    <div class="hero-hold grid items-center min-h-12 px-4 sm:px-5 py-2">
                      <span class="min-w-0">
                        <span class="block text-sm text-slate-100">Cash</span>
                        <span class="block mt-0.5 text-xs text-slate-500 truncate">Available to invest</span>
                      </span>
                      <span class="hidden md:flex items-center gap-2">
                        <span class="hero-track"><span class="hero-fill hero-fill-cash" :style="{ transform: `scaleX(${cash / account.value})` }"></span></span>
                        <span class="w-8 text-xs text-slate-400 tabular-nums">{{ ((cash / account.value) * 100).toFixed(0) }}%</span>
                      </span>
                      <span class="hidden sm:block"></span>
                      <span class="text-right text-sm text-slate-100 tabular-nums">{{ usd(cash) }}</span>
                      <span class="text-right text-sm text-slate-600">—</span>
                    </div>
                  </div>
                </div>
              </div>

              <!-- Instances + orders: what the strategies are doing -->
              <div class="hidden lg:flex flex-col gap-8 min-w-0">
                <div>
                  <p class="mb-3 text-base font-medium text-slate-100">Instances</p>
                  <div class="ui-card ui-rows">
                    <div v-for="inst in instances" :key="inst.name" class="min-h-14 px-4 py-2 flex items-center justify-between gap-3">
                      <span class="min-w-0">
                        <span class="block text-sm text-slate-100 truncate">{{ inst.name }}</span>
                        <span class="block mt-0.5 text-xs text-slate-500">{{ inst.sub }}</span>
                      </span>
                      <span class="inline-flex items-center gap-2 text-xs shrink-0" :class="inst.text">
                        <span class="ui-dot" :class="inst.dot"></span>{{ inst.status }}
                      </span>
                    </div>
                  </div>
                </div>

                <div>
                  <p class="mb-3 text-base font-medium text-slate-100">Recent orders</p>
                  <div class="ui-card ui-rows">
                    <div v-for="o in orders" :key="o.time" class="min-h-14 px-4 py-2 flex items-center justify-between gap-3">
                      <span class="min-w-0">
                        <span class="block text-sm text-slate-100 tabular-nums">{{ o.side }} {{ o.qty }} {{ o.symbol }}</span>
                        <span class="block mt-0.5 text-xs text-slate-500 tabular-nums">{{ o.instance }} · {{ o.time }}</span>
                      </span>
                      <span class="text-sm text-slate-300 tabular-nums shrink-0">{{ usd(o.price) }}</span>
                    </div>
                  </div>
                </div>
              </div>

            </div>
          </div>
        </div>
      </div>

    </div>
  </section>
</template>

<style scoped>
/* Same column logic as the dashboard holdings table:
   phone  symbol · value · P&L
   sm     symbol · line · value · P&L
   md+    symbol · weight · line · value · P&L */
.hero-hold {
  grid-template-columns: minmax(0, 1fr) 96px 96px;
  column-gap: 12px;
}
@media (min-width: 640px) {
  .hero-hold { grid-template-columns: minmax(0, 1fr) 72px 104px 96px; column-gap: 16px; }
}
@media (min-width: 768px) {
  .hero-hold { grid-template-columns: minmax(0, 1fr) 104px 72px 104px 96px; }
}
.hero-track {
  position: relative;
  display: block;
  height: 4px;
  width: 56px;
  border-radius: 999px;
  background: #1e1e20;
  overflow: hidden;
}
.hero-fill {
  position: absolute;
  inset: 0;
  border-radius: 999px;
  background: #d4d4d8;
  transform-origin: left center;
}
.hero-fill-cash { background: #71717a; }
</style>
