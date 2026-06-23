<script setup>
import { ref } from 'vue'

defineProps({
  text: { type: String, required: true },
  size: { type: String, default: '14px' },
})

const show = ref(false)
const x = ref(0)
const y = ref(0)

function enter(e) {
  const r = e.currentTarget.getBoundingClientRect()
  x.value = r.left + r.width / 2
  y.value = r.top
  show.value = true
}
function leave() { show.value = false }
</script>

<template>
  <span class="inline-flex items-center align-middle" @mouseenter="enter" @mouseleave="leave">
    <span class="material-symbols-outlined text-slate-600 hover:text-slate-400 cursor-help transition-colors" :style="{ fontSize: size }">info</span>
    <Teleport to="body">
      <span
        v-if="show"
        class="fixed z-[100] -translate-x-1/2 -translate-y-full pointer-events-none w-56 rounded-lg border border-border-subtle bg-[#181030] px-2.5 py-1.5 text-[11px] leading-snug text-slate-300 shadow-xl shadow-black/60"
        :style="{ left: x + 'px', top: (y - 8) + 'px' }"
      >{{ text }}</span>
    </Teleport>
  </span>
</template>
