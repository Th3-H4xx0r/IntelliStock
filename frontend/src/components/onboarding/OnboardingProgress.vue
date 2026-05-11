<script setup>
defineProps({
  steps:   { type: Array,  required: true },
  current: { type: Number, required: true },
})
</script>

<template>
  <div
    class="flex items-center gap-2 sm:gap-3"
    role="progressbar"
    aria-label="Onboarding progress"
    :aria-valuenow="current + 1"
    aria-valuemin="1"
    :aria-valuemax="steps.length"
    :aria-valuetext="(current + 1) + ' of ' + steps.length + (steps[current] ? ' · ' + steps[current].label : '')"
  >
    <template v-for="(step, idx) in steps" :key="step.id">
      <div class="flex items-center gap-2 min-w-0">
        <div
          class="size-7 rounded-full border flex items-center justify-center shrink-0 transition-all duration-500"
          :class="idx < current
            ? 'bg-primary/15 border-primary/60 text-primary'
            : idx === current
              ? 'border-primary text-primary onboarding-cta'
              : 'border-border-subtle text-slate-600'"
        >
          <span v-if="idx < current" class="material-symbols-outlined text-[16px]">check</span>
          <span v-else class="text-[11px] font-bold">{{ idx + 1 }}</span>
        </div>
        <span
          class="text-[11px] sm:text-xs font-medium uppercase tracking-wider truncate hidden md:inline"
          :class="idx === current ? 'text-slate-200' : idx < current ? 'text-primary/80' : 'text-slate-600'"
        >{{ step.label }}</span>
      </div>
      <div
        v-if="idx < steps.length - 1"
        class="flex-1 h-px bg-border-subtle relative overflow-hidden rounded-full min-w-[12px]"
      >
        <div
          class="absolute inset-y-0 left-0 onboarding-progress-fill rounded-full transition-all duration-700"
          :style="{ width: idx < current ? '100%' : (idx === current ? '50%' : '0%') }"
        ></div>
      </div>
    </template>
  </div>
</template>
