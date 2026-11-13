<script setup>
defineProps({
  steps:   { type: Array,  required: true },
  current: { type: Number, required: true },
})
</script>

<template>
  <!-- Static stepper: done steps carry a check, the current one is lit,
       the rest wait in grey. Nothing animates. -->
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
        <span
          class="size-6 rounded-full flex items-center justify-center shrink-0 text-xs tabular-nums"
          :class="idx < current
            ? 'bg-raised text-slate-300'
            : idx === current
              ? 'bg-primary text-background-dark font-medium'
              : 'border border-white/[0.1] text-slate-500'"
        >
          <span v-if="idx < current" class="material-symbols-outlined text-[14px]">check</span>
          <span v-else>{{ idx + 1 }}</span>
        </span>
        <span
          class="text-sm truncate hidden md:inline"
          :class="idx === current ? 'text-slate-100' : idx < current ? 'text-slate-400' : 'text-slate-500'"
        >{{ step.label }}</span>
      </div>
      <span
        v-if="idx < steps.length - 1"
        class="flex-1 h-px min-w-[12px]"
        :class="idx < current ? 'bg-slate-500' : 'bg-white/[0.08]'"
      ></span>
    </template>
  </div>
</template>
