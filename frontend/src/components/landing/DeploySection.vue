<script setup>
// The deploy log, shown whole (no typewriter).
const LINES = [
  { text: 'Starting instance momentum-paper',     cls: 'text-slate-200' },
  { text: 'Loading strategy and credentials',     cls: 'text-slate-500' },
  { text: '> Connected to Alpaca (paper)',        cls: 'text-slate-300' },
  { text: 'Loading the Graph Nexus',              cls: 'text-slate-500' },
  { text: 'Starting the instance container',      cls: 'text-slate-500' },
  { text: '> Benzinga news: connected',           cls: 'text-slate-300' },
  { text: '> LLM provider: ready',                cls: 'text-slate-300' },
  { text: 'Running',                              cls: 'text-gain'      },
]

const features = [
  { label: 'One container per instance', desc: 'Each instance runs isolated, with its own logs'           },
  { label: 'News while it trades',       desc: 'Benzinga news reaches the strategy as it runs'            },
  { label: 'Live P&L',                   desc: 'Positions, orders and P&L on the web and on iPhone'       },
  { label: 'Paper first',                desc: 'Run on a paper account, then point the instance at live'  },
]
</script>

<template>
  <section aria-labelledby="deploy-title">
    <div class="mx-auto w-full max-w-[1200px] px-4 sm:px-6 lg:px-8">
      <div class="border-t border-white/[0.06] py-20 md:py-28 grid gap-12 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] xl:gap-16 items-start">

        <!-- Copy -->
        <div class="max-w-xl">
          <p class="text-sm text-slate-500">Deployment</p>
          <h2 id="deploy-title" class="mt-3 text-3xl leading-[1.15] md:text-[40px] md:leading-[1.1] font-medium tracking-[-0.02em] text-slate-100">
            From idea to live trading
          </h2>
          <p class="mt-5 text-base leading-7 md:text-lg md:leading-8 text-slate-400">
            An instance pairs a strategy with a brokerage account. Start it and it runs
            in its own container, connected to the Graph Nexus, Benzinga news and the
            LLM you chose, until you stop it.
          </p>

          <ul class="mt-8 divide-y divide-white/[0.06] border-y border-white/[0.06]">
            <li v-for="feat in features" :key="feat.label" class="py-3 text-sm">
              <span class="text-slate-100">{{ feat.label }}</span>
              <span class="text-slate-400"> — {{ feat.desc }}</span>
            </li>
          </ul>

          <div class="mt-8">
            <p class="text-xs text-slate-500">Supported brokerages</p>
            <img
              src="https://alpaca.markets/img/newsroom/brand-assets/light-alpaca-logo.png"
              alt="Alpaca"
              class="mt-3 h-6 w-auto opacity-50 grayscale"
            />
          </div>
        </div>

        <!-- Terminal -->
        <div class="rounded-xl border border-white/[0.07] bg-sidebar overflow-hidden">
          <div class="h-10 px-4 flex items-center gap-2 border-b border-white/[0.06]">
            <span class="size-2.5 rounded-full bg-white/[0.1]"></span>
            <span class="size-2.5 rounded-full bg-white/[0.1]"></span>
            <span class="size-2.5 rounded-full bg-white/[0.1]"></span>
            <span class="ml-2 font-mono text-xs text-slate-500">Instance log</span>
          </div>
          <code class="block px-5 py-5 font-mono text-[13px] leading-6 overflow-x-auto">
            <p v-for="(line, i) in LINES" :key="i" class="whitespace-pre" :class="line.cls">{{ line.text }}</p>
          </code>
        </div>

      </div>
    </div>
  </section>
</template>
