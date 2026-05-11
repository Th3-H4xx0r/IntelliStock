<script setup>
import OnboardingParticles from './OnboardingParticles.vue'
import OnboardingProgress from './OnboardingProgress.vue'

defineProps({
  steps:           { type: Array,  required: true },
  current:         { type: Number, required: true },
  direction:       { type: String, default: 'forward' }, // 'forward' | 'back'
  canGoBack:       { type: Boolean, default: false },
  canGoNext:       { type: Boolean, default: true },
  nextLabel:       { type: String,  default: 'Next' },
  backLabel:       { type: String,  default: 'Back' },
  showSkip:        { type: Boolean, default: false },
  skipLabel:       { type: String,  default: 'Skip' },
  showFooter:      { type: Boolean, default: true },
  hideProgress:    { type: Boolean, default: false },
  busy:            { type: Boolean, default: false },
})

const emit = defineEmits(['next', 'back', 'skip', 'exit'])
</script>

<template>
  <!-- Real flex-column layout so the header / progress / content sizes
       resolve automatically. The previous `calc(100% - 110px)` magic on
       the content area was off on mobile (URL bar + dynamic viewport) so
       the top of the card got pushed offscreen and couldn't be scrolled
       back into view. Now the outer is `flex flex-col`, the body is
       `flex-1 min-h-0 overflow-y-auto`, and we use `100dvh` on iOS so
       the panel respects the dynamic viewport. Top padding honours the
       iOS safe area so the title bar isn't clipped by the notch. -->
  <div
    class="fixed inset-0 z-50 flex flex-col overflow-hidden bg-[#05080b] text-slate-100"
    style="height: 100dvh; padding-top: env(safe-area-inset-top); padding-bottom: env(safe-area-inset-bottom);"
  >
    <!-- Animated background -->
    <OnboardingParticles />

    <!-- Top bar with logo + exit -->
    <div class="relative z-10 flex items-center justify-between px-4 sm:px-8 py-4 sm:py-5 shrink-0">
      <div class="flex items-center gap-2.5">
        <div class="size-8 rounded-lg bg-primary/15 border border-primary/30 flex items-center justify-center">
          <span class="material-symbols-outlined text-primary text-[18px]">auto_awesome</span>
        </div>
        <div class="flex flex-col leading-tight">
          <span class="text-[10px] uppercase tracking-[0.18em] text-slate-500">IntelliStock</span>
          <span class="text-xs font-semibold text-slate-200">Welcome flow</span>
        </div>
      </div>
      <button
        @click="emit('exit')"
        class="text-xs text-slate-500 hover:text-slate-200 transition-colors flex items-center gap-1.5"
      >
        Exit
        <span class="material-symbols-outlined text-[14px]">close</span>
      </button>
    </div>

    <!-- Progress bar -->
    <div v-if="!hideProgress" class="relative z-10 px-4 sm:px-8 pb-4 sm:pb-6 shrink-0">
      <OnboardingProgress :steps="steps" :current="current" />
    </div>

    <!-- Step content — fills remaining space and scrolls vertically. Both
         flex-1 and min-h-0 are needed: flex-1 takes remaining height,
         min-h-0 lets it actually shrink so overflow-y-auto kicks in
         instead of bleeding past the viewport. -->
    <div class="relative z-10 flex-1 min-h-0 overflow-y-auto flex items-start sm:items-center justify-center px-4 sm:px-6 pb-6">
      <div
        class="relative w-full max-w-3xl glass-card rounded-2xl border border-primary/15 bg-[#0c1216]/85 px-5 sm:px-10 py-6 sm:py-9 shadow-[0_0_60px_rgba(0,225,255,0.05)] my-auto"
        :data-direction="direction"
      >
        <Transition name="step" mode="out-in">
          <div :key="current" class="min-h-[280px]">
            <slot />
          </div>
        </Transition>

        <!-- Footer nav -->
        <div v-if="showFooter" class="mt-8 pt-5 border-t border-border-subtle/60 flex flex-col-reverse sm:flex-row items-stretch sm:items-center gap-3 sm:justify-between">
          <button
            v-if="canGoBack"
            @click="emit('back')"
            :disabled="busy"
            class="inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg text-sm text-slate-400 hover:text-slate-100 transition-colors disabled:opacity-40"
          >
            <span class="material-symbols-outlined text-[16px]">arrow_back</span>
            {{ backLabel }}
          </button>
          <span v-else class="hidden sm:inline-block"></span>

          <div class="flex items-center gap-3 justify-end">
            <button
              v-if="showSkip"
              @click="emit('skip')"
              :disabled="busy"
              class="text-sm text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40 px-2 py-2"
            >{{ skipLabel }}</button>
            <button
              @click="emit('next')"
              :disabled="busy || !canGoNext"
              class="inline-flex items-center justify-center gap-2 px-5 sm:px-6 py-2.5 rounded-lg bg-primary text-background-dark text-sm font-bold hover:brightness-110 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
              :class="{ 'onboarding-cta': canGoNext && !busy }"
            >
              <span v-if="busy" class="material-symbols-outlined text-base animate-spin">progress_activity</span>
              {{ nextLabel }}
              <span v-if="!busy" class="material-symbols-outlined text-[16px]">arrow_forward</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
