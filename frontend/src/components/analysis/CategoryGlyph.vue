<!-- frontend/src/components/analysis/CategoryGlyph.vue
     The chart marker for a move, drawn small: the legend swatch on the
     filter chips, the event list, the detail panel and the legend under the
     chart. Filled in the category family's colour and shape when explained,
     a grey outline while unexplained or pending, a red outline when the
     explanation failed (as on iOS). -->
<template>
  <svg width="12" height="12" viewBox="0 0 12 12" class="shrink-0" aria-hidden="true">
    <circle v-if="style.kind !== 'filled'" cx="6" cy="6" r="4" :fill="PAGE_BG" :stroke="style.color" stroke-width="1.5" />
    <rect v-else-if="style.shape === 'square'" x="2" y="2" width="8" height="8" rx="1.5" :fill="style.color" />
    <path v-else-if="style.shape === 'diamond'" d="M6,0.75L11.25,6L6,11.25L0.75,6Z" :fill="style.color" />
    <circle v-else cx="6" cy="6" r="4.5" :fill="style.color" />
  </svg>
</template>

<script setup>
import { computed } from 'vue'
import { markerStyle } from '../../utils/moveAnalysis.js'

const props = defineProps({
  category: { type: String, default: null },
  // explained | unexplained | pending | failed
  status: { type: String, default: 'explained' },
})

const PAGE_BG = '#0f0f10'
const style = computed(() => markerStyle({ status: props.status, category: props.category }))
</script>
