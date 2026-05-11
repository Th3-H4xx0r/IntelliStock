<script setup>
import { computed } from 'vue'

defineProps({
  username: { type: String, default: '' },
})

const headline = 'Welcome to IntelliStock'
const letters = computed(() => Array.from(headline))
</script>

<template>
  <div class="flex flex-col items-center text-center gap-7 py-6 sm:py-10">
    <div class="size-20 rounded-2xl bg-primary/10 border border-primary/30 flex items-center justify-center relative">
      <span class="material-symbols-outlined text-primary text-[42px]">auto_awesome</span>
      <div class="absolute -inset-2 rounded-3xl border border-primary/20 animate-pulse"></div>
    </div>

    <h1 class="onboarding-title text-3xl sm:text-5xl font-extrabold leading-tight tracking-tight max-w-2xl">
      <span
        v-for="(ch, idx) in letters"
        :key="idx"
        class="onboarding-letter"
        :style="{ animationDelay: (0.05 + idx * 0.04) + 's' }"
      >{{ ch === ' ' ? ' ' : ch }}</span>
    </h1>

    <p
      class="text-slate-400 text-sm sm:text-base max-w-xl onboarding-letter"
      :style="{ animationDelay: (0.1 + letters.length * 0.04) + 's' }"
    >
      <template v-if="username">Hey <span class="text-primary font-semibold">{{ username }}</span> —</template>
      <template v-else>Glad you're here —</template>
      let's get your account-aware autonomous trading workspace dialed in. We'll set up an LLM model,
      link a brokerage, and spin up your first instance. Should take about two minutes.
    </p>

    <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 max-w-2xl w-full mt-2">
      <div
        v-for="(item, idx) in [
          { icon: 'memory',           label: 'LLM models',  desc: 'OpenAI · Gemini · Azure · NVIDIA · DeepSeek' },
          { icon: 'account_balance',  label: 'Brokerages',  desc: 'Alpaca · Robinhood' },
          { icon: 'rocket_launch',    label: 'Instances',   desc: 'Live or paper, fully autonomous' },
        ]"
        :key="item.label"
        class="rounded-xl border border-border-subtle bg-surface/50 px-4 py-3 flex items-center gap-3 onboarding-letter text-left"
        :style="{ animationDelay: (0.5 + idx * 0.1) + 's' }"
      >
        <span class="material-symbols-outlined text-primary text-[22px]">{{ item.icon }}</span>
        <div class="min-w-0">
          <p class="text-xs font-semibold text-slate-200">{{ item.label }}</p>
          <p class="text-[11px] text-slate-500 truncate">{{ item.desc }}</p>
        </div>
      </div>
    </div>
  </div>
</template>
