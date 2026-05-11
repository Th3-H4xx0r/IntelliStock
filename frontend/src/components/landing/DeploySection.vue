<script setup>
import { ref, onMounted } from 'vue'

const terminalRef  = ref(null)
const visibleLines = ref([])
const showCursor   = ref(false)
let   typed        = false

const LINES = [
  { text: '$ intellistock deploy --strategy graph_nexus_alpha', cls: 'text-emerald-400' },
  { text: 'Verifying credentials...',                           cls: 'text-slate-500'   },
  { text: '> Connected to Alpaca (Paper Trading)',              cls: 'text-primary'     },
  { text: 'Loading Graph Nexus — 85,412 edges OK',             cls: 'text-slate-500'   },
  { text: 'Spinning up strategy containers...',                 cls: 'text-slate-500'   },
  { text: '> Benzinga event stream: active',                    cls: 'text-primary'     },
  { text: '> LLM sentiment engine: ready (GPT-4o)',             cls: 'text-primary'     },
  { text: 'SUCCESS: Live trading active.',                      cls: 'text-emerald-400' },
]

const features = [
  { icon: 'package_2',  label: 'Containerized deployment', desc: 'Each strategy runs in its own isolated container' },
  { icon: 'sync',       label: 'Live event streaming',     desc: 'Benzinga feeds connect automatically on boot'      },
  { icon: 'hub',        label: 'Graph Nexus hot-reload',   desc: 'Edge updates propagate to running strategies'      },
  { icon: 'monitoring', label: 'Real-time P&L tracking',  desc: 'Position dashboards update every tick'             },
  { icon: 'cloud_done', label: 'Paper & live modes',       desc: 'Switch between paper and live with one flag'       },
]

onMounted(() => {
  const obs = new IntersectionObserver(([e]) => {
    if (e.isIntersecting && !typed) {
      typed = true
      showCursor.value = true
      LINES.forEach((line, i) => {
        setTimeout(() => {
          visibleLines.value.push(line)
          if (i === LINES.length - 1) showCursor.value = false
        }, i * 500 + 300)
      })
      obs.disconnect()
    }
  }, { threshold: 0.4 })
  if (terminalRef.value) obs.observe(terminalRef.value)
})
</script>

<template>
  <section class="bg-gradient-to-b from-background-dark to-surface" style="padding: 80px 0;">
    <div class="max-w-7xl mx-auto px-6">
      <div v-reveal class="reveal glass-card rounded-[2rem] p-8 md:p-12 flex flex-col xl:flex-row items-start justify-between gap-10 md:gap-12">

        <!-- Copy -->
        <div class="max-w-lg space-y-6 md:space-y-8">
          <div class="space-y-4">
            <p v-reveal class="reveal text-primary text-xs font-bold uppercase tracking-widest">Deployment</p>
            <h2 v-reveal class="reveal delay-100 text-4xl font-bold leading-tight">
              From idea to live<br> trading in seconds.
            </h2>
            <p v-reveal class="reveal delay-200 text-slate-400 text-lg">
              IntelliStock containerizes your strategy, wires it to the Graph Nexus,
              connects the Benzinga event stream, and deploys it to production —
              with no infrastructure management on your end.
            </p>
          </div>

          <!-- Feature list -->
          <ul v-reveal class="reveal delay-300 space-y-4">
            <li v-for="feat in features" :key="feat.label" class="flex items-start gap-3">
              <span class="material-symbols-outlined text-primary text-base mt-0.5 shrink-0">{{ feat.icon }}</span>
              <div>
                <span class="font-semibold text-sm">{{ feat.label }}</span>
                <span class="text-slate-400 text-sm"> — {{ feat.desc }}</span>
              </div>
            </li>
          </ul>

          <!-- Broker logos -->
          <div v-reveal class="reveal delay-400 space-y-3">
            <p class="text-xs font-bold uppercase tracking-widest text-slate-500">Supported Brokerages</p>
            <div class="flex items-center gap-6">
              <div class="opacity-40 hover:opacity-80 transition-opacity cursor-default">
                <img
                  src="https://alpaca.markets/img/newsroom/brand-assets/light-alpaca-logo.png"
                  alt="Alpaca"
                  class="h-7 w-auto"
                  style="filter: grayscale(100%);"
                />
              </div>
              <div class="w-px h-5 bg-border-subtle"></div>
              <div class="opacity-40 hover:opacity-80 transition-opacity cursor-default">
                <img
                  src="https://upload.wikimedia.org/wikipedia/commons/thumb/d/da/Robinhood_%28company%29_logo.svg/1280px-Robinhood_%28company%29_logo.svg.png"
                  alt="Robinhood"
                  class="h-7 w-auto"
                  style="filter: brightness(0) invert(1);"
                />
              </div>
            </div>
          </div>
        </div>

        <!-- Terminal -->
        <div v-reveal class="reveal from-right delay-200 w-full xl:w-auto xl:flex-1">
          <div ref="terminalRef" class="bg-background-dark p-8 rounded-2xl border border-border-subtle shadow-inner w-full">
            <!-- Traffic lights -->
            <div class="flex items-center gap-2 mb-6">
              <div class="size-3 rounded-full bg-red-500"></div>
              <div class="size-3 rounded-full bg-amber-500"></div>
              <div class="size-3 rounded-full bg-emerald-500"></div>
              <span class="ml-3 text-xs text-slate-600 font-mono">intellistock-cli</span>
            </div>

            <!-- Typewriter lines -->
            <code class="text-sm font-mono space-y-2 block min-h-[200px]">
              <p
                v-for="(line, i) in visibleLines"
                :key="i"
                :class="line.cls"
                style="animation: heroFadeUp 0.3s ease both;"
              >{{ line.text }}</p>
              <span v-if="showCursor" class="cursor-blink text-primary"></span>
            </code>
          </div>
        </div>

      </div>
    </div>
  </section>
</template>
