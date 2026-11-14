<!-- frontend/src/components/analysis/CategoryGlyph.vue
     The chart marker for a move category, drawn small: the legend swatch on
     the filter chips, the event list and the detail panel. Hollow for an
     event that is not explained yet. -->
<template>
  <svg width="12" height="12" viewBox="0 0 12 12" class="shrink-0" aria-hidden="true">
    <circle v-if="hollow" cx="6" cy="6" r="4" :fill="PAGE_BG" :stroke="HOLLOW_STROKE" stroke-width="1.5" />
    <rect v-else-if="style.shape === 'square'" x="2" y="2" width="8" height="8" rx="1.5" :fill="style.color" />
    <path v-else-if="style.shape === 'diamond'" d="M6,0.75L11.25,6L6,11.25L0.75,6Z" :fill="style.color" />
    <circle v-else cx="6" cy="6" r="4.5" :fill="style.color" />
  </svg>
</template>

<script setup>
import { computed } from 'vue'
import { HOLLOW_STROKE, categoryStyle } from '../../utils/moveAnalysis.js'

const props = defineProps({
  category: { type: String, default: null },
  hollow: { type: Boolean, default: false },
})

const PAGE_BG = '#0f0f10'
const style = computed(() => categoryStyle(props.category))
</script>
