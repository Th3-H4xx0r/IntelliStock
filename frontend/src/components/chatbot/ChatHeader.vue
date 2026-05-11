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
  <div class="flex items-center gap-2 px-3 sm:px-4 py-2.5 sm:py-3 border-b border-border-subtle bg-[#0a0f13]/85 backdrop-blur-md">
    <div class="size-7 rounded-md bg-primary/15 border border-primary/30 flex items-center justify-center shrink-0">
      <span class="material-symbols-outlined text-primary text-[16px]">smart_toy</span>
    </div>
    <div class="min-w-0 flex-1">
      <p class="text-sm font-semibold text-slate-100 truncate leading-tight">{{ title }}</p>
      <p v-if="modelName" class="text-[10px] uppercase tracking-widest text-slate-500 leading-tight font-mono truncate">{{ modelName }}</p>
    </div>
    <button
      @click="$emit('toggleFullscreen')"
      class="size-8 rounded-md flex items-center justify-center text-slate-400 hover:text-slate-100 transition hidden sm:flex"
      :title="isFullscreen ? 'Exit fullscreen' : 'Fullscreen'"
      :aria-label="isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'"
    >
      <span class="material-symbols-outlined text-[18px]">{{ isFullscreen ? 'close_fullscreen' : 'open_in_full' }}</span>
    </button>
    <button
      @click="$emit('toggleSettings')"
      :class="showSettings ? 'text-primary bg-primary/10' : 'text-slate-400 hover:text-slate-100'"
      class="size-8 rounded-md flex items-center justify-center transition"
      title="Settings"
      aria-label="Open settings"
    >
      <span class="material-symbols-outlined text-[18px]">settings</span>
    </button>
    <button
      @click="$emit('clear')"
      class="size-8 rounded-md flex items-center justify-center text-slate-400 hover:text-red-300 transition"
      title="Clear conversation"
      aria-label="Clear conversation"
    >
      <span class="material-symbols-outlined text-[18px]">delete_sweep</span>
    </button>
    <button
      @click="$emit('minimise')"
      class="size-8 rounded-md flex items-center justify-center text-slate-400 hover:text-slate-100 transition"
      title="Minimise"
      aria-label="Minimise chatbot"
    >
      <span class="material-symbols-outlined text-[18px]">expand_more</span>
    </button>
  </div>
</template>
