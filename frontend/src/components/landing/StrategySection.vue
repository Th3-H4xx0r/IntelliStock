<template>
  <section style="padding: 80px 0 80px;">
    <div class="max-w-7xl mx-auto px-6">

      <!-- Section header -->
      <div class="max-w-2xl mb-14 md:mb-16">
        <p v-reveal class="reveal text-primary text-xs font-bold uppercase tracking-widest mb-4">Strategy Engine</p>
        <h2 v-reveal class="reveal delay-100 text-4xl md:text-5xl font-bold leading-tight">
          Build strategies from<br class="hidden md:block"> real market signals.
        </h2>
        <p v-reveal class="reveal delay-200 text-slate-400 text-lg mt-6">
          IntelliStock's strategy engine wires together live data sources, AI-driven signals,
          and relationship intelligence from the Graph Nexus — all configurable without writing boilerplate.
        </p>
      </div>

      <!-- Two-column layout -->
      <div class="grid lg:grid-cols-2 gap-12 md:gap-14 items-start">

        <!-- Feature list -->
        <ul class="space-y-8">
          <li v-reveal class="reveal delay-100 flex items-start gap-5">
            <div class="shrink-0 size-11 bg-primary/10 border border-primary/20 rounded-xl flex items-center justify-center mt-0.5">
              <span class="material-symbols-outlined text-primary text-xl">hub</span>
            </div>
            <div>
              <h4 class="font-bold text-lg mb-1">Graph Nexus Analysis</h4>
              <p class="text-slate-400 text-sm leading-relaxed">
                Query the knowledge graph for supply-chain shocks, institutional ownership shifts,
                government contract wins, and patent partnerships — then feed insights directly into
                your strategy's decision layer.
              </p>
            </div>
          </li>

          <li v-reveal class="reveal delay-200 flex items-start gap-5">
            <div class="shrink-0 size-11 bg-primary/10 border border-primary/20 rounded-xl flex items-center justify-center mt-0.5">
              <span class="material-symbols-outlined text-primary text-xl">news</span>
            </div>
            <div>
              <h4 class="font-bold text-lg mb-1">Benzinga Real-Time Events</h4>
              <p class="text-slate-400 text-sm leading-relaxed">
                Pull live ratings changes, analyst upgrades, insider trades, government trades,
                M&amp;A activity, IPO calendars, earnings surprises, and prediction-market odds
                — all cached and deduped automatically.
              </p>
            </div>
          </li>

          <li v-reveal class="reveal delay-300 flex items-start gap-5">
            <div class="shrink-0 size-11 bg-primary/10 border border-primary/20 rounded-xl flex items-center justify-center mt-0.5">
              <span class="material-symbols-outlined text-primary text-xl">psychology</span>
            </div>
            <div>
              <h4 class="font-bold text-lg mb-1">LLM Sentiment Engine</h4>
              <p class="text-slate-400 text-sm leading-relaxed">
                An LLM-powered sentiment layer synthesizes Benzinga events, graph relationships,
                and historic outcome patterns into a single directional signal with confidence scores
                — configurable by model (GPT-4o, Gemini, Claude).
              </p>
            </div>
          </li>

          <li v-reveal class="reveal delay-400 flex items-start gap-5">
            <div class="shrink-0 size-11 bg-primary/10 border border-primary/20 rounded-xl flex items-center justify-center mt-0.5">
              <span class="material-symbols-outlined text-primary text-xl">school</span>
            </div>
            <div>
              <h4 class="font-bold text-lg mb-1">Adaptive Learning</h4>
              <p class="text-slate-400 text-sm leading-relaxed">
                Outcome tracking records predicted signals against next-day actual prices.
                A learning stage runs at session start to summarize what worked — and feeds
                those patterns back to the LLM on the next trade decision.
              </p>
            </div>
          </li>
        </ul>

        <!-- Visual card panel -->
        <div v-reveal class="reveal from-right delay-150 space-y-4">

          <!-- Signal pipeline card -->
          <div class="glass-card rounded-2xl p-7 space-y-5">
            <div class="flex items-center justify-between">
              <p class="text-xs font-bold uppercase tracking-widest text-primary">Signal Pipeline</p>
              <div class="px-2 py-0.5 bg-emerald-500/10 border border-emerald-500/20 rounded-full text-emerald-400 text-[10px] font-bold uppercase">Live</div>
            </div>

            <!-- Pipeline steps -->
            <div class="space-y-2">
              <div v-for="step in pipeline" :key="step.label"
                class="flex items-center gap-3 p-3 rounded-lg bg-surface border border-border-subtle">
                <div class="size-2 rounded-full flex-shrink-0" :class="step.color"></div>
                <span class="text-sm font-medium flex-1">{{ step.label }}</span>
                <span class="text-xs text-slate-500">{{ step.source }}</span>
              </div>
            </div>
          </div>

          <!-- Data sources row -->
          <div class="grid grid-cols-3 gap-3">
            <div v-for="src in sources" :key="src.label"
              class="card-hover bg-surface border border-border-subtle rounded-xl p-4 flex flex-col items-center gap-2 text-center">
              <span class="material-symbols-outlined text-primary text-xl">{{ src.icon }}</span>
              <span class="text-[11px] font-medium text-slate-300">{{ src.label }}</span>
            </div>
          </div>

        </div>
      </div>

    </div>
  </section>
</template>

<script setup>
const pipeline = [
  { label: 'Graph Nexus relationships',    source: 'Neo4j',       color: 'bg-primary' },
  { label: 'Benzinga event calendar',      source: 'Benzinga',    color: 'bg-violet-400' },
  { label: 'Institutional ownership data', source: 'SEC 13F',     color: 'bg-amber-400' },
  { label: 'LLM sentiment + confidence',   source: 'GPT-4o / Gemini', color: 'bg-emerald-400' },
  { label: 'Outcome-weighted learning',    source: 'RethinkDB',   color: 'bg-rose-400' },
]

const sources = [
  { label: 'Polygon.io',    icon: 'candlestick_chart' },
  { label: 'SEC EDGAR',     icon: 'gavel' },
  { label: 'Benzinga',      icon: 'newspaper' },
  { label: 'USASpending',   icon: 'account_balance' },
  { label: 'PatentsView',   icon: 'lightbulb' },
  { label: 'Wikidata',      icon: 'device_hub' },
]
</script>
