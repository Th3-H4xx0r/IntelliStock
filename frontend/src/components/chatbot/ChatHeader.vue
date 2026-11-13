<script setup>
defineProps({
  title:        { type: String,  default: 'Assistant' },
  modelName:    { type: String,  default: '' },
  showSettings: { type: Boolean, default: false },
  isFullscreen: { type: Boolean, default: false },
})

defineEmits(['toggleSettings', 'toggleFullscreen', 'clear', 'minimise', 'close'])
</script>

<template>
  <div class="flex items-center gap-1 pl-4 pr-2 h-14 shrink-0 border-b border-white/[0.06] bg-surface">
    <div class="min-w-0 flex-1 mr-2">
      <p class="text-sm font-medium text-slate-100 truncate leading-tight">{{ title }}</p>
      <p v-if="modelName" class="text-xs text-slate-500 leading-tight truncate mt-0.5">{{ modelName }}</p>
    </div>
    <button
      @click="$emit('toggleFullscreen')"
      class="ui-icon-btn hidden sm:inline-flex"
      :title="isFullscreen ? 'Exit fullscreen' : 'Fullscreen'"
      :aria-label="isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'"
    >
      <span class="material-symbols-outlined text-[18px]">{{ isFullscreen ? 'close_fullscreen' : 'open_in_full' }}</span>
    </button>
    <button
      @click="$emit('toggleSettings')"
      :class="showSettings ? 'bg-raised text-slate-100' : ''"
      class="ui-icon-btn"
      title="Settings"
      aria-label="Open settings"
      :aria-pressed="showSettings"
    >
      <span class="material-symbols-outlined text-[18px]">settings</span>
    </button>
    <button
      @click="$emit('clear')"
      class="ui-icon-btn hover:text-loss"
      title="Clear conversation"
      aria-label="Clear conversation"
    >
      <span class="material-symbols-outlined text-[18px]">delete_sweep</span>
    </button>
    <button
      @click="$emit('minimise')"
      class="ui-icon-btn"
      title="Minimise"
      aria-label="Minimise chatbot"
    >
      <span class="material-symbols-outlined text-[18px]">expand_more</span>
    </button>
  </div>
</template>
