<script setup>
// The market today — the web twin of the iOS "Market" group: index tiles,
// sector ETF performance, the account screener's movers, Nexus momentum
// (only when the strategy produces it) and market news.
import { ref, computed, toRef } from 'vue'
import Sparkline from './Sparkline.vue'
import { useMarketData } from '../../composables/useDashboardData.js'
import { fmtUsd, fmtPrice, fmtPct, fmtRelative, signClass } from '../../utils/dashboardAnalytics.js'

const props = defineProps({
  brokerageId: { type: String, default: null },
})

const { indices, sectors, news, movers, momentum } = useMarketData(toRef(props, 'brokerageId'))

const moverSide = ref('gainers')
const moverRows = computed(() => movers.value?.[moverSide.value] || [])
const maxMomentum = computed(() => Math.max(0, ...(momentum.value || []).map(m => Math.abs(m.score))))

// A typical daily sector move is well under 3%, so bars scale against 3%.
const sectorScale = pct => Math.min(Math.abs(pct) / 3, 1)

const articles = computed(() => (news.value || [])
  .map(a => ({ ...a, href: safeUrl(a.url), title: cleanTitle(a.title, a.source) }))
  .filter(a => a.href)
  .slice(0, 6))

function safeUrl(u) {
  try {
    const url = new URL(u)
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null
  } catch { return null }
}
// Google News titles end in " - Source", which the meta line already shows.
function cleanTitle(title, source) {
  const t = String(title || '')
  return source && t.endsWith(` - ${source}`) ? t.slice(0, -(source.length + 3)) : t
}
</script>

<template>
  <section aria-labelledby="market-title">
    <div class="mb-3">
      <h2 id="market-title" class="text-base font-medium text-slate-100">Market</h2>
      <p class="text-sm text-slate-500 mt-0.5">Today's session</p>
    </div>

    <!-- Index tiles -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <template v-if="indices == null">
        <div v-for="n in 4" :key="n" class="h-[104px] rounded-xl border border-white/[0.07] bg-surface"></div>
      </template>
      <template v-else>
        <div
          v-for="q in indices"
          :key="q.symbol"
          class="rounded-xl border border-white/[0.07] bg-surface px-4 pt-3 pb-3"
        >
          <div class="flex items-baseline justify-between gap-2">
            <p class="text-sm text-slate-300 truncate">{{ q.label }}</p>
            <p class="text-xs text-slate-500">{{ q.symbol }}</p>
          </div>
          <div class="mt-1 flex items-baseline justify-between gap-2 tabular-nums">
            <p class="text-base font-medium text-slate-100">{{ fmtUsd(q.price) }}</p>
            <p class="text-sm" :class="signClass(q.pct)">{{ fmtPct(q.pct) }}</p>
          </div>
          <Sparkline :values="q.values" :width="200" :height="28" class="mt-2 w-full" />
        </div>
      </template>
    </div>

    <div class="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3 items-start">
      <!-- Sector performance -->
      <div v-if="sectors == null || sectors.length" class="rounded-xl border border-white/[0.07] bg-surface">
        <div class="h-12 px-5 flex items-center border-b border-white/[0.06]">
          <h3 class="text-sm font-medium text-slate-200">Sector performance</h3>
        </div>
        <ul class="px-5 py-3 space-y-2.5">
          <template v-if="sectors == null">
            <li v-for="n in 6" :key="n" class="h-4 rounded bg-raised"></li>
          </template>
          <template v-else>
            <li v-for="s in sectors" :key="s.symbol" class="grid grid-cols-[minmax(0,1fr)_72px_56px] items-center gap-3 text-sm">
              <span class="text-slate-300 truncate" :title="s.symbol">{{ s.label }}</span>
              <span class="relative h-1 rounded-full bg-raised overflow-hidden">
                <span
                  class="absolute inset-y-0 left-0 w-full rounded-full origin-left"
                  :class="s.pct >= 0 ? 'bg-gain/70' : 'bg-loss/70'"
                  :style="{ transform: `scaleX(${sectorScale(s.pct)})` }"
                ></span>
              </span>
              <span class="text-right tabular-nums" :class="signClass(s.pct)">{{ fmtPct(s.pct) }}</span>
            </li>
          </template>
        </ul>
      </div>

      <!-- Movers + momentum -->
      <div class="space-y-4">
        <div v-if="movers == null || movers.gainers.length || movers.losers.length" class="rounded-xl border border-white/[0.07] bg-surface">
          <div class="h-12 px-5 flex items-center justify-between border-b border-white/[0.06]">
            <h3 class="text-sm font-medium text-slate-200">Market movers</h3>
            <div class="inline-flex p-0.5 rounded-md bg-background-dark" role="group" aria-label="Movers">
              <button
                v-for="m in [['gainers', 'Gainers'], ['losers', 'Losers']]"
                :key="m[0]"
                type="button"
                class="h-6 px-2.5 rounded text-xs transition-colors"
                :class="moverSide === m[0] ? 'bg-raised text-slate-100' : 'text-slate-500 hover:text-slate-200'"
                :aria-pressed="moverSide === m[0]"
                @click="moverSide = m[0]"
              >{{ m[1] }}</button>
            </div>
          </div>
          <ul class="px-5 py-1 divide-y divide-white/[0.04]">
            <template v-if="movers == null">
              <li v-for="n in 4" :key="n" class="py-3"><div class="h-4 rounded bg-raised"></div></li>
            </template>
            <template v-else>
              <li v-for="r in moverRows" :key="r.symbol" class="h-10 flex items-center gap-3 text-sm">
                <span class="font-medium text-slate-200">{{ r.symbol }}</span>
                <span class="ml-auto text-slate-400 tabular-nums">{{ fmtPrice(r.price) }}</span>
                <span class="w-20 text-right tabular-nums" :class="signClass(r.pct)">{{ fmtPct(r.pct) }}</span>
              </li>
            </template>
          </ul>
        </div>

        <div v-if="momentum && momentum.length" class="rounded-xl border border-white/[0.07] bg-surface">
          <div class="h-12 px-5 flex items-center border-b border-white/[0.06]">
            <h3 class="text-sm font-medium text-slate-200">Nexus momentum</h3>
          </div>
          <ul class="px-5 py-3 space-y-2.5">
            <li v-for="m in momentum.slice(0, 10)" :key="m.symbol" class="grid grid-cols-[64px_minmax(0,1fr)] items-center gap-3 text-sm">
              <span class="font-medium text-slate-200">{{ m.symbol }}</span>
              <span class="relative h-1 rounded-full bg-raised overflow-hidden">
                <span
                  class="absolute inset-y-0 left-0 w-full rounded-full origin-left bg-slate-300"
                  :style="{ transform: `scaleX(${maxMomentum ? Math.abs(m.score) / maxMomentum : 0})` }"
                ></span>
              </span>
            </li>
          </ul>
        </div>
      </div>

      <!-- News -->
      <div v-if="news == null || articles.length" class="rounded-xl border border-white/[0.07] bg-surface">
        <div class="h-12 px-5 flex items-center border-b border-white/[0.06]">
          <h3 class="text-sm font-medium text-slate-200">Market news</h3>
        </div>
        <ul class="px-2 py-2">
          <template v-if="news == null">
            <li v-for="n in 4" :key="n" class="px-3 py-2.5"><div class="h-8 rounded bg-raised"></div></li>
          </template>
          <template v-else>
            <li v-for="a in articles" :key="a.href">
              <a
                :href="a.href"
                target="_blank"
                rel="noopener noreferrer"
                class="block px-3 py-2.5 rounded-lg hover:bg-white/[0.03] transition-colors"
              >
                <span class="news-title text-sm text-slate-200">{{ a.title }}</span>
                <span class="block text-xs text-slate-500 mt-1">
                  {{ [a.source, fmtRelative(a.published_at)].filter(Boolean).join(' · ') }}
                </span>
              </a>
            </li>
          </template>
        </ul>
      </div>
    </div>
  </section>
</template>

<style scoped>
.news-title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
