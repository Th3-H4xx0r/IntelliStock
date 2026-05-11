<script setup>
const cards = [
  {
    icon: 'psychology',
    title: 'LLM-driven decisions',
    desc: 'Models reason over fundamentals, technicals, news, and your custom signals to produce buy/sell/hold calls — every cycle, every ticker.',
  },
  {
    icon: 'hub',
    title: 'Autonomous instances',
    desc: 'Each instance runs a strategy on its own cadence, manages capital, and writes trades to your linked brokerage. Stop and resume them at will.',
  },
  {
    icon: 'history',
    title: 'Backtest before you trade',
    desc: 'Replay any strategy against years of bars. Compare runs, debug fills, and only push to live when the numbers look right.',
  },
  {
    icon: 'shield_lock',
    title: 'Your keys, your data',
    desc: 'API keys are encrypted at rest. Everything runs on your infrastructure. No third-party service ever sees your portfolio.',
  },
]
</script>

<template>
  <div class="space-y-7">
    <div class="text-center sm:text-left">
      <p class="text-primary text-xs font-bold uppercase tracking-widest mb-2 onboarding-letter" style="animation-delay: 0.05s">
        What is IntelliStock?
      </p>
      <h2 class="onboarding-title text-2xl sm:text-3xl font-bold leading-tight onboarding-letter" style="animation-delay: 0.15s">
        An LLM-native trading desk you actually own.
      </h2>
      <p class="mt-3 text-slate-400 text-sm sm:text-base max-w-2xl onboarding-letter" style="animation-delay: 0.25s">
        IntelliStock turns large language models into a fleet of autonomous trading agents. Every component
        lives in your stack: data, strategies, broker connections, and execution.
      </p>
    </div>

    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
      <div
        v-for="(card, idx) in cards"
        :key="card.title"
        class="rounded-xl border border-border-subtle bg-surface/50 p-4 sm:p-5 flex gap-3 sm:gap-4 onboarding-letter card-hover"
        :style="{ animationDelay: (0.35 + idx * 0.08) + 's' }"
      >
        <div class="size-10 rounded-lg bg-primary/15 border border-primary/25 flex items-center justify-center shrink-0">
          <span class="material-symbols-outlined text-primary text-[20px]">{{ card.icon }}</span>
        </div>
        <div class="min-w-0">
          <p class="text-sm font-semibold text-slate-100">{{ card.title }}</p>
          <p class="text-xs text-slate-400 mt-1 leading-relaxed">{{ card.desc }}</p>
        </div>
      </div>
    </div>
  </div>
</template>
