<script setup>
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
       resolve automatically: the outer is `flex flex-col`, the body is
       `flex-1 min-h-0 overflow-y-auto`, and `100dvh` keeps the panel inside
       the dynamic viewport on iOS. Top padding honours the safe area so the
       title bar isn't clipped by the notch. -->
  <div
    class="fixed inset-0 z-50 flex flex-col overflow-hidden bg-background-dark text-slate-100"
    style="height: 100dvh; padding-top: env(safe-area-inset-top); padding-bottom: env(safe-area-inset-bottom);"
  >
    <!-- Top bar with logo + exit -->
    <div class="flex items-center justify-between gap-4 px-4 sm:px-8 h-16 shrink-0 border-b border-white/[0.06]">
      <div class="flex items-center gap-3 min-w-0">
        <img
          src="/new-logo.png"
          alt="IntelliStock"
          width="1200"
          height="240"
          class="block h-7 w-auto object-contain object-left"
          draggable="false"
        />
        <span class="hidden sm:block h-4 w-px bg-white/[0.1]" aria-hidden="true"></span>
        <span class="hidden sm:block text-sm text-slate-500">Setup</span>
      </div>
      <button type="button" @click="emit('exit')" class="ui-btn-ghost -mr-2">
        Exit
        <span class="material-symbols-outlined text-[16px]">close</span>
      </button>
    </div>

    <!-- Progress -->
    <div v-if="!hideProgress" class="px-4 sm:px-8 py-4 shrink-0 border-b border-white/[0.06]">
      <OnboardingProgress :steps="steps" :current="current" />
    </div>

    <!-- Step content — fills remaining space and scrolls vertically. Both
         flex-1 and min-h-0 are needed: flex-1 takes remaining height,
         min-h-0 lets it actually shrink so overflow-y-auto kicks in
         instead of bleeding past the viewport. -->
    <div class="flex-1 min-h-0 overflow-y-auto flex items-start sm:items-center justify-center px-4 sm:px-6 py-6 sm:py-10">
      <div
        class="w-full max-w-3xl rounded-2xl border border-white/[0.07] bg-surface px-5 sm:px-10 py-6 sm:py-8 my-auto"
        :data-direction="direction"
      >
        <Transition name="step" mode="out-in">
          <div :key="current" class="min-h-[280px]">
            <slot />
          </div>
        </Transition>

        <!-- Footer nav -->
        <div v-if="showFooter" class="mt-8 pt-5 border-t border-white/[0.06] flex flex-col-reverse sm:flex-row items-stretch sm:items-center gap-3 sm:justify-between">
          <button
            v-if="canGoBack"
            type="button"
            @click="emit('back')"
            :disabled="busy"
            class="ui-btn-ghost h-9 sm:-ml-3"
          >
            <span class="material-symbols-outlined text-[16px]">arrow_back</span>
            {{ backLabel }}
          </button>
          <span v-else class="hidden sm:inline-block"></span>

          <div class="flex items-center gap-2 justify-end">
            <button
              v-if="showSkip"
              type="button"
              @click="emit('skip')"
              :disabled="busy"
              class="ui-btn-ghost h-9"
            >{{ skipLabel }}</button>
            <button
              type="button"
              @click="emit('next')"
              :disabled="busy || !canGoNext"
              class="ui-btn-primary"
            >
              <span v-if="busy" class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
              {{ nextLabel }}
              <span v-if="!busy" class="material-symbols-outlined text-[16px]">arrow_forward</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
