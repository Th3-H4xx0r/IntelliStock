<script setup>
// What every run reports, instead of a cherry-picked result.
const reports = ['Return and drawdown', 'Every trade and its reason', 'Trading costs on every fill']

const features = [
  {
    title: 'Historical replay',
    desc: 'Pick any date range and step through it on bars from one minute to one day, then watch the run back in live playback, trade by trade.',
  },
  {
    title: 'Costs included',
    desc: 'Every simulated fill pays a trading cost, so a strategy that only works when trading is free shows up as one that does not work.',
  },
  {
    title: 'Point-in-time data',
    desc: 'Each step sees only what was known at that moment. Future prices are stripped server-side before any signal is computed.',
  },
]
</script>

<template>
  <section aria-labelledby="backtest-title">
    <div class="mx-auto w-full max-w-[1200px] px-4 sm:px-6 lg:px-8">
      <div class="border-t border-white/[0.06] py-20 md:py-28">

        <!-- Header left, the example run right -->
        <div class="flex flex-col gap-10 lg:flex-row lg:items-end lg:justify-between">
          <div class="max-w-2xl">
            <p class="text-sm text-slate-500">Backtesting</p>
            <h2 id="backtest-title" class="mt-3 text-3xl leading-[1.15] md:text-[40px] md:leading-[1.1] font-medium tracking-[-0.02em] text-slate-100">
              Simulate before you deploy
            </h2>
            <p class="mt-5 text-base leading-7 md:text-lg md:leading-8 text-slate-400">
              Replay market history through the same strategy code that trades live. Test it across
              rising, falling and sideways markets before it touches real money.
            </p>
          </div>

          <div class="ui-card shrink-0 w-full sm:w-auto px-5 py-4">
            <p class="text-xs text-slate-500">Every run reports</p>
            <ul class="mt-3 space-y-2">
              <li v-for="r in reports" :key="r" class="text-sm text-slate-200">{{ r }}</li>
            </ul>
          </div>
        </div>

        <!-- Three methods, one card -->
        <div class="mt-14 ui-card grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-white/[0.06]">
          <div v-for="f in features" :key="f.title" class="p-6">
            <h3 class="text-base font-medium text-slate-100">{{ f.title }}</h3>
            <p class="mt-2 text-sm leading-6 text-slate-400">{{ f.desc }}</p>
          </div>
        </div>

        <!-- Leakage prevention -->
        <div class="mt-4 rounded-xl border border-white/[0.07] px-5 py-4 sm:px-6">
          <h3 class="text-sm font-medium text-slate-100">Data leakage prevention, enforced at the source</h3>
          <p class="mt-1.5 text-sm leading-6 text-slate-400">
            Future EPS actuals are stripped from earnings calendars. Unpriced IPO final prices are withheld.
            <code class="px-1.5 py-0.5 rounded bg-raised font-mono text-[13px] text-slate-200">benzinga_lookahead_days=0</code>
            forces strict backtest mode with zero forward-looking data, enforced server-side.
          </p>
        </div>

      </div>
    </div>
  </section>
</template>
