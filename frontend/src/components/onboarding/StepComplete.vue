<script setup>
defineProps({
  modelCount:     { type: Number, default: 0 },
  brokerageCount: { type: Number, default: 0 },
  instanceCount:  { type: Number, default: 0 },
})

const RAY_COUNT = 14
const rays = Array.from({ length: RAY_COUNT }, (_, i) => ({
  rot: `${(360 / RAY_COUNT) * i}deg`,
  delay: `${(i % 4) * 0.06}s`,
}))
</script>

<template>
  <div class="relative flex flex-col items-center text-center gap-6 py-6">
    <!-- Ray burst -->
    <div class="relative w-44 h-44 flex items-center justify-center">
      <div
        v-for="(r, i) in rays"
        :key="i"
        class="onboarding-ray"
        :style="{ '--ray-rot': r.rot, animationDelay: r.delay }"
      ></div>
      <div class="onboarding-checkmark relative size-24 rounded-full bg-primary/15 border-2 border-primary/60 flex items-center justify-center shadow-[0_0_60px_rgba(0,225,255,0.4)]">
        <span class="material-symbols-outlined text-primary text-[56px]">check</span>
      </div>
    </div>

    <h2 class="onboarding-title text-3xl sm:text-4xl font-extrabold leading-tight max-w-xl onboarding-letter" style="animation-delay: 0.2s">
      You're all set.
    </h2>

    <p class="text-slate-400 text-sm sm:text-base max-w-xl onboarding-letter" style="animation-delay: 0.3s">
      IntelliStock is configured and ready. Open the dashboard to monitor portfolios, queue backtests,
      or jump straight into your live instance.
    </p>

    <div class="grid grid-cols-3 gap-2 sm:gap-3 max-w-md w-full mt-2">
      <div
        v-for="(card, idx) in [
          { icon: 'memory',          label: 'Models',     value: modelCount },
          { icon: 'account_balance', label: 'Brokerages', value: brokerageCount },
          { icon: 'rocket_launch',   label: 'Instances',  value: instanceCount },
        ]"
        :key="card.label"
        class="rounded-xl border border-border-subtle bg-surface/50 px-3 py-3 onboarding-letter flex flex-col items-center gap-1"
        :style="{ animationDelay: (0.4 + idx * 0.08) + 's' }"
      >
        <span class="material-symbols-outlined text-primary text-[22px]">{{ card.icon }}</span>
        <p class="text-2xl font-extrabold text-slate-100 leading-none">{{ card.value }}</p>
        <p class="text-[10px] uppercase tracking-widest text-slate-500">{{ card.label }}</p>
      </div>
    </div>
  </div>
</template>
