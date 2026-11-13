<script setup>
const cards = [
  {
    title: 'LLM-driven decisions',
    desc: 'Models reason over fundamentals, technicals, news, and your custom signals to produce buy/sell/hold calls, every cycle, every ticker.',
  },
  {
    title: 'Autonomous instances',
    desc: 'Each instance runs a strategy on its own cadence, manages capital, and writes trades to your linked brokerage. Stop and resume them at will.',
  },
  {
    title: 'Backtest before you trade',
    desc: 'Replay any strategy against years of bars. Compare runs, debug fills, and only push to live when the numbers look right.',
  },
  {
    title: 'Your keys, your data',
    desc: 'API keys are encrypted at rest. Everything runs on your infrastructure. No third-party service ever sees your portfolio.',
  },
]
</script>

<template>
  <div>
    <p class="text-sm text-slate-500">What is IntelliStock?</p>
    <h2 class="mt-2 text-xl sm:text-2xl font-medium leading-tight text-slate-100">
      An LLM trading desk you own
    </h2>
    <p class="mt-3 max-w-2xl text-sm sm:text-base leading-6 sm:leading-7 text-slate-400">
      IntelliStock turns large language models into a fleet of autonomous trading agents. Every component
      lives in your stack: data, strategies, broker connections, and execution.
    </p>

    <div class="mt-7 grid grid-cols-1 sm:grid-cols-2 gap-px rounded-xl overflow-hidden border border-white/[0.07] bg-white/[0.06]">
      <div v-for="card in cards" :key="card.title" class="bg-surface p-5">
        <p class="text-sm font-medium text-slate-100">{{ card.title }}</p>
        <p class="mt-1.5 text-[13px] leading-5 text-slate-400">{{ card.desc }}</p>
      </div>
    </div>
  </div>
</template>
