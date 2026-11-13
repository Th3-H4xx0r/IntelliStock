<script setup>
defineProps({
  modelCount:     { type: Number, default: 0 },
  brokerageCount: { type: Number, default: 0 },
  instanceCount:  { type: Number, default: 0 },
})
</script>

<template>
  <div class="py-2 sm:py-6">
    <span class="size-10 rounded-full bg-raised border border-white/[0.08] flex items-center justify-center">
      <span class="material-symbols-outlined text-[20px] text-gain">check</span>
    </span>

    <h2 class="mt-5 text-2xl sm:text-3xl font-medium leading-tight tracking-[-0.01em] text-slate-100">
      Setup complete
    </h2>

    <p class="mt-3 max-w-xl text-sm sm:text-base leading-6 sm:leading-7 text-slate-400">
      IntelliStock is configured and ready. Open the dashboard to monitor portfolios, queue backtests,
      or jump straight into your live instance.
    </p>

    <div class="mt-8 max-w-md ui-card grid grid-cols-3 divide-x divide-white/[0.06]">
      <div
        v-for="card in [
          { label: 'Models',     value: modelCount },
          { label: 'Brokerages', value: brokerageCount },
          { label: 'Instances',  value: instanceCount },
        ]"
        :key="card.label"
        class="px-4 py-3"
      >
        <p class="text-xs text-slate-500">{{ card.label }}</p>
        <p class="mt-1 text-2xl font-medium text-slate-100 tabular-nums">{{ card.value }}</p>
      </div>
    </div>
  </div>
</template>
