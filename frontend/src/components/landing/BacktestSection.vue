<script setup>
import { ref, onMounted } from 'vue'

const statsRef   = ref(null)
const sharpe     = ref('0.00')
const drawdown   = ref('0.0')
let   counted    = false

function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3) }

function animateNum(from, to, decimals, duration, setter) {
  const start = performance.now()
  const diff  = to - from
  const step  = (now) => {
    const t = Math.min((now - start) / duration, 1)
    setter((from + diff * easeOutCubic(t)).toFixed(decimals))
    if (t < 1) requestAnimationFrame(step)
  }
  requestAnimationFrame(step)
}

onMounted(() => {
  const obs = new IntersectionObserver(([e]) => {
    if (e.isIntersecting && !counted) {
      counted = true
      animateNum(0, 3.24, 2, 1600, v => (sharpe.value = v))
      animateNum(0, 4.2,  1, 1600, v => (drawdown.value = v))
      obs.disconnect()
    }
  }, { threshold: 0.5 })
  if (statsRef.value) obs.observe(statsRef.value)
})
</script>

<template>
  <section class="py-20 md:py-24">
    <div class="max-w-7xl mx-auto px-6">

      <!-- Header row -->
      <div class="flex flex-col md:flex-row justify-between items-end gap-6 md:gap-8 mb-12 md:mb-14">
        <div class="max-w-xl space-y-4">
          <p v-reveal class="reveal text-primary text-xs font-bold uppercase tracking-widest">Backtesting</p>
          <h2 v-reveal class="reveal delay-100 text-4xl md:text-5xl font-bold leading-tight">
            Simulate before you deploy.
          </h2>
          <p v-reveal class="reveal delay-200 text-slate-400 text-lg">
            Run simulations against 20 years of tick-level market data with sub-millisecond precision.
            Every strategy is stress-tested across historical regimes before it touches real capital —
            with strict data-leakage prevention built in.
          </p>
        </div>

        <!-- Animated stat cards -->
        <div ref="statsRef" class="flex flex-wrap gap-4 shrink-0">
          <div v-reveal class="reveal from-right delay-200 px-6 py-4 rounded-xl bg-surface border border-border-subtle">
            <p class="text-xs text-slate-500 font-medium mb-1">Sharpe Ratio</p>
            <p class="text-3xl font-bold text-primary tabular-nums">{{ sharpe }}</p>
            <p class="text-xs text-slate-600 mt-1">example run</p>
          </div>
          <div v-reveal class="reveal from-right delay-300 px-6 py-4 rounded-xl bg-surface border border-border-subtle">
            <p class="text-xs text-slate-500 font-medium mb-1">Max Drawdown</p>
            <p class="text-3xl font-bold text-emerald-400 tabular-nums">-{{ drawdown }}%</p>
            <p class="text-xs text-slate-600 mt-1">example run</p>
          </div>
        </div>
      </div>

      <!-- Feature cards -->
      <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-10 md:mb-12">
        <div v-reveal class="reveal delay-100 card-hover p-8 rounded-2xl bg-surface border border-border-subtle group">
          <span class="material-symbols-outlined text-primary mb-4 block text-3xl">history</span>
          <h4 class="text-xl font-bold mb-3">Historical Replay</h4>
          <p class="text-slate-400 text-sm leading-relaxed">
            Replay specific market sessions tick-by-tick — earnings days, FOMC announcements,
            or supply-chain shocks — to understand exactly how your strategy would have performed.
          </p>
        </div>
        <div v-reveal class="reveal delay-200 card-hover p-8 rounded-2xl bg-surface border border-border-subtle group">
          <span class="material-symbols-outlined text-primary mb-4 block text-3xl">analytics</span>
          <h4 class="text-xl font-bold mb-3">Monte Carlo Analysis</h4>
          <p class="text-slate-400 text-sm leading-relaxed">
            Stress-test across 10,000 randomized market permutations. See the full distribution
            of outcomes — not just the median — so tail risk is never a surprise.
          </p>
        </div>
        <div v-reveal class="reveal delay-300 card-hover p-8 rounded-2xl bg-surface border border-border-subtle group">
          <span class="material-symbols-outlined text-primary mb-4 block text-3xl">verified_user</span>
          <h4 class="text-xl font-bold mb-3">Walk-Forward Validation</h4>
          <p class="text-slate-400 text-sm leading-relaxed">
            Automated out-of-sample testing with rolling optimization windows.
            No look-ahead bias — future actuals are stripped server-side before any signal is computed.
          </p>
        </div>
      </div>

      <!-- Leakage prevention banner -->
      <div v-reveal class="reveal delay-200 flex flex-col sm:flex-row items-start sm:items-center gap-4 p-6 rounded-2xl bg-primary/5 border border-primary/20">
        <span class="material-symbols-outlined text-primary text-3xl shrink-0">shield_lock</span>
        <div>
          <h5 class="font-bold text-base mb-1">Data Leakage Prevention — Enforced at the Source</h5>
          <p class="text-sm text-slate-400">
            Future EPS actuals are stripped from earnings calendars. Unpriced IPO final prices are withheld.
            <span class="text-primary font-medium">benzinga_lookahead_days=0</span> forces strict backtest mode
            with zero forward-looking data — all guaranteed server-side.
          </p>
        </div>
      </div>

    </div>
  </section>
</template>
