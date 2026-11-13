<script setup>
defineProps({
  username: { type: String, default: '' },
})

const items = [
  { label: 'LLM models',      desc: 'OpenAI · Gemini · Azure · NVIDIA · DeepSeek' },
  { label: 'Stock brokerage', desc: 'Alpaca' },
  { label: 'Instances',       desc: 'Live or paper, fully autonomous' },
]
</script>

<template>
  <div class="py-2 sm:py-6">
    <h1 class="text-2xl sm:text-3xl font-medium leading-tight tracking-[-0.01em] text-slate-100">
      Set up IntelliStock
    </h1>

    <p class="mt-3 max-w-xl text-sm sm:text-base leading-6 sm:leading-7 text-slate-400">
      We'll set up an LLM model, link a brokerage, and create your first autonomous trading
      instance. It takes about two minutes.
    </p>
    <p class="mt-2 text-sm text-slate-500">
      <template v-if="username">Signed in as <span class="text-slate-300">{{ username }}</span></template>
      <template v-else>Glad you're here</template>
    </p>

    <div class="mt-8 grid grid-cols-1 sm:grid-cols-3 gap-px rounded-xl overflow-hidden border border-white/[0.07] bg-white/[0.06]">
      <div v-for="item in items" :key="item.label" class="bg-surface px-4 py-4">
        <p class="text-sm font-medium text-slate-100">{{ item.label }}</p>
        <p class="mt-1 text-xs leading-5 text-slate-500">{{ item.desc }}</p>
      </div>
    </div>
  </div>
</template>
