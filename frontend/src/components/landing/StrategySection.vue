<template>
  <section aria-labelledby="strategy-title">
    <div class="mx-auto w-full max-w-[1200px] px-4 sm:px-6 lg:px-8">
      <div class="border-t border-white/[0.06] py-20 md:py-28">

        <!-- Section header -->
        <div class="max-w-2xl">
          <p class="text-sm text-slate-500">Strategy engine</p>
          <h2 id="strategy-title" class="mt-3 text-3xl leading-[1.15] md:text-[40px] md:leading-[1.1] font-medium tracking-[-0.02em] text-slate-100">
            Build strategies from real market signals
          </h2>
          <p class="mt-5 text-base leading-7 md:text-lg md:leading-8 text-slate-400">
            IntelliStock's strategy engine wires together live data sources, AI-driven signals,
            and relationship intelligence from the Graph Nexus, all configurable without writing boilerplate.
          </p>
        </div>

        <!-- Features left, the pipeline they feed right -->
        <div class="mt-14 grid gap-12 lg:grid-cols-2 lg:gap-16 items-start">

          <ul class="divide-y divide-white/[0.06] border-y border-white/[0.06]">
            <li v-for="f in features" :key="f.title" class="py-6">
              <h3 class="text-base font-medium text-slate-100">{{ f.title }}</h3>
              <p class="mt-2 text-sm leading-6 text-slate-400">{{ f.desc }}</p>
            </li>
          </ul>

          <div class="space-y-4">
            <!-- Signal pipeline -->
            <div class="ui-card overflow-hidden">
              <div class="h-12 px-5 flex items-center justify-between border-b border-white/[0.06]">
                <p class="text-sm font-medium text-slate-200">Signal pipeline</p>
                <span class="inline-flex items-center gap-2 text-xs text-slate-300">
                  <span class="ui-dot ui-dot-live"></span>Live
                </span>
              </div>
              <ol class="ui-rows">
                <li v-for="(step, i) in pipeline" :key="step.label" class="min-h-12 px-5 py-2 flex items-center gap-4">
                  <span class="w-4 text-xs text-slate-600 tabular-nums">{{ i + 1 }}</span>
                  <span class="flex-1 min-w-0 text-sm text-slate-200">{{ step.label }}</span>
                  <span class="text-xs text-slate-500 text-right">{{ step.source }}</span>
                </li>
              </ol>
            </div>

            <!-- Data sources -->
            <div class="ui-card px-5 py-4">
              <p class="text-xs text-slate-500">Data sources</p>
              <ul class="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-2.5">
                <li v-for="src in sources" :key="src" class="text-sm text-slate-300">{{ src }}</li>
              </ul>
            </div>
          </div>
        </div>

      </div>
    </div>
  </section>
</template>

<script setup>
const features = [
  {
    title: 'Graph Nexus analysis',
    desc: "Query the knowledge graph for supply-chain shocks, institutional ownership shifts, government contract wins, and patent partnerships, then feed insights directly into your strategy's decision layer.",
  },
  {
    title: 'Benzinga real-time events',
    desc: 'Pull live ratings changes, analyst upgrades, insider trades, government trades, M&A activity, IPO calendars, earnings surprises, and prediction-market odds, all cached and deduped automatically.',
  },
  {
    title: 'LLM sentiment engine',
    desc: 'A sentiment layer synthesizes Benzinga events, graph relationships, and historic outcome patterns into a single directional signal with confidence scores, using the model you choose: Claude, Gemini, OpenRouter, a local Ollama model and others.',
  },
  {
    title: 'Adaptive learning',
    desc: 'Outcome tracking records predicted signals against next-day actual prices. A learning stage runs at session start to summarize what worked, and feeds those patterns back to the LLM on the next trade decision.',
  },
]

const pipeline = [
  { label: 'Graph Nexus relationships',    source: 'Neo4j' },
  { label: 'Benzinga event calendar',      source: 'Benzinga' },
  { label: 'Institutional ownership data', source: 'SEC 13F' },
  { label: 'LLM sentiment + confidence',   source: 'Your chosen model' },
  { label: 'Outcome-weighted learning',    source: 'PostgreSQL' },
]

const sources = ['Polygon.io', 'SEC EDGAR', 'Benzinga', 'USASpending', 'PatentsView', 'Wikidata']
</script>
