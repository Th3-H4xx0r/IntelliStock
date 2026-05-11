<template>
  <section class="bg-surface/30" style="padding: 80px 0;">
    <div class="max-w-7xl mx-auto px-6">
      <div class="text-center max-w-3xl mx-auto space-y-6 mb-14 md:mb-16">
        <p v-reveal class="reveal text-primary text-xs font-bold uppercase tracking-widest">Graph Nexus</p>
        <h2 v-reveal class="reveal delay-100 text-4xl md:text-5xl font-bold tracking-tight">
          Every relationship between<br class="hidden md:block"> every public company.
        </h2>
        <p v-reveal class="reveal delay-200 text-slate-400 text-lg">
          The Graph Nexus is a knowledge graph built from 10 data sources -
          spanning SEC filings, government contracts, patents, board interlocks,
          and Wikidata ownership. Each edge is validated, confidence-scored, and
          queryable in real time from any strategy.
        </p>
      </div>

      <div v-reveal class="reveal from-scale delay-100 grid grid-cols-2 md:grid-cols-4 gap-4 mb-14 md:mb-16">
        <div
          v-for="edge in edges"
          :key="edge.type"
          class="card-hover bg-background-dark border border-border-subtle rounded-2xl p-5 space-y-3"
        >
          <div class="size-10 rounded-lg flex items-center justify-center" :class="edge.bg">
            <span class="material-symbols-outlined text-xl" :class="edge.color">{{ edge.icon }}</span>
          </div>
          <div>
            <p class="text-[10px] font-bold uppercase tracking-widest mb-1" :class="edge.color">{{ edge.type }}</p>
            <p class="text-sm font-semibold text-slate-200 mb-1">{{ edge.title }}</p>
            <p class="text-xs text-slate-500 leading-relaxed">{{ edge.desc }}</p>
          </div>
        </div>
      </div>

      <div class="grid lg:grid-cols-[1fr_340px] gap-8 md:gap-10 items-center">
        <div
          v-reveal
          class="reveal from-scale delay-200 relative h-[460px] bg-[#060511] rounded-3xl border border-border-subtle overflow-hidden flex items-center justify-center"
        >
          <div class="absolute inset-0 opacity-40 bg-[radial-gradient(circle_at_center,_rgba(196,124,255,0.42)_0%,_rgba(113,44,226,0.14)_34%,_transparent_74%)]"></div>

          <svg class="absolute inset-0 w-full h-full pointer-events-none" viewBox="0 0 500 500" preserveAspectRatio="xMidYMid meet">
            <line x1="250" y1="250" x2="250" y2="122" stroke="#b97aff" stroke-width="0.8" stroke-dasharray="5 4" opacity="0.2">
              <animate attributeName="stroke-dashoffset" from="18" to="0" dur="2s" repeatCount="indefinite" />
            </line>
            <line x1="250" y1="250" x2="250" y2="378" stroke="#b97aff" stroke-width="0.8" stroke-dasharray="5 4" opacity="0.2">
              <animate attributeName="stroke-dashoffset" from="18" to="0" dur="2.4s" repeatCount="indefinite" />
            </line>
            <line x1="250" y1="250" x2="122" y2="250" stroke="#b97aff" stroke-width="0.8" stroke-dasharray="5 4" opacity="0.2">
              <animate attributeName="stroke-dashoffset" from="18" to="0" dur="2.2s" repeatCount="indefinite" />
            </line>
            <line x1="250" y1="250" x2="378" y2="250" stroke="#b97aff" stroke-width="0.8" stroke-dasharray="5 4" opacity="0.2">
              <animate attributeName="stroke-dashoffset" from="18" to="0" dur="1.9s" repeatCount="indefinite" />
            </line>
            <line x1="250" y1="250" x2="152" y2="152" stroke="#d49cff" stroke-width="0.5" stroke-dasharray="4 6" opacity="0.12">
              <animate attributeName="stroke-dashoffset" from="20" to="0" dur="3s" repeatCount="indefinite" />
            </line>
            <line x1="250" y1="250" x2="348" y2="152" stroke="#d49cff" stroke-width="0.5" stroke-dasharray="4 6" opacity="0.12">
              <animate attributeName="stroke-dashoffset" from="20" to="0" dur="3.5s" repeatCount="indefinite" />
            </line>
            <line x1="250" y1="250" x2="152" y2="348" stroke="#d49cff" stroke-width="0.5" stroke-dasharray="4 6" opacity="0.12">
              <animate attributeName="stroke-dashoffset" from="20" to="0" dur="2.8s" repeatCount="indefinite" />
            </line>
            <line x1="250" y1="250" x2="348" y2="348" stroke="#d49cff" stroke-width="0.5" stroke-dasharray="4 6" opacity="0.12">
              <animate attributeName="stroke-dashoffset" from="20" to="0" dur="3.2s" repeatCount="indefinite" />
            </line>
          </svg>

          <div class="relative size-64">
            <div class="absolute inset-0 rounded-full border border-primary/20 animate-[spin_20s_linear_infinite]"></div>
            <div class="absolute inset-[-40px] rounded-full border border-primary/10 animate-[spin_30s_linear_infinite_reverse]"></div>

            <div class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 size-14 bg-[radial-gradient(circle_at_30%_30%,_#f5e1ff_0%,_#ca86ff_36%,_#8139e8_100%)] rounded-full shadow-[0_0_55px_rgba(191,122,255,0.72)] flex items-center justify-center z-10">
              <span class="material-symbols-outlined text-[#080511] font-bold text-2xl">hub</span>
            </div>

            <div class="absolute top-0 left-1/2 -translate-x-1/2 size-4 bg-[#1a1232] rounded-full border border-violet-300/70 shadow-[0_0_14px_rgba(191,122,255,0.45)]"></div>
            <div class="absolute bottom-0 left-1/2 -translate-x-1/2 size-4 bg-[#1a1232] rounded-full border border-violet-300/70 shadow-[0_0_14px_rgba(191,122,255,0.45)]"></div>
            <div class="absolute left-0 top-1/2 -translate-y-1/2 size-4 bg-[#1a1232] rounded-full border border-violet-300/70 shadow-[0_0_14px_rgba(191,122,255,0.45)]"></div>
            <div class="absolute right-0 top-1/2 -translate-y-1/2 size-4 bg-[#1a1232] rounded-full border border-violet-300/70 shadow-[0_0_14px_rgba(191,122,255,0.45)]"></div>
          </div>

          <div v-reveal class="reveal delay-400 absolute bottom-8 left-8 glass-card p-5 rounded-xl text-left border border-white/10 max-w-[260px]">
            <p class="text-[10px] font-bold text-primary mb-2 uppercase tracking-widest">Live Edge Detected</p>
            <h4 class="text-sm font-bold mb-1">Supply Chain Shock</h4>
            <p class="text-xs text-slate-400">TSMC to NVDA <span class="text-slate-300 font-medium">SUPPLIER_OF</span> sentiment: <span class="text-emerald-400 font-bold">Bullish +0.82</span></p>
          </div>
        </div>

        <div class="space-y-4">
          <div
            v-reveal
            v-for="(stat, i) in stats"
            :key="stat.label"
            class="reveal from-right glass-card rounded-2xl p-6 flex items-center gap-5"
            :class="`delay-${(i + 1) * 100}`"
          >
            <div class="size-12 rounded-xl flex items-center justify-center shrink-0" :class="stat.bg">
              <span class="material-symbols-outlined" :class="stat.color">{{ stat.icon }}</span>
            </div>
            <div>
              <p class="text-2xl font-bold">{{ stat.value }}</p>
              <p class="text-sm text-slate-400">{{ stat.label }}</p>
            </div>
          </div>

          <div v-reveal class="reveal from-right delay-500 glass-card rounded-2xl p-6">
            <p class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-4">Data Sources</p>
            <div class="flex flex-wrap gap-2">
              <span
                v-for="src in dataSources"
                :key="src"
                class="px-2.5 py-1 text-[11px] font-medium bg-surface border border-border-subtle rounded-full text-slate-300"
              >
                {{ src }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
const edges = [
  {
    type: 'COMPETES_WITH',
    title: 'Competitors',
    desc: 'Companies in the same SIC industry group - bidirectional, 85K+ edges.',
    icon: 'compare_arrows',
    color: 'text-rose-400',
    bg: 'bg-rose-400/10',
  },
  {
    type: 'SUPPLY_CHAIN',
    title: 'Suppliers & Customers',
    desc: '10-K customer/supplier disclosures from SEC EDGAR, confidence-scored.',
    icon: 'account_tree',
    color: 'text-amber-400',
    bg: 'bg-amber-400/10',
  },
  {
    type: 'HELD_BY',
    title: 'Institutional Ownership',
    desc: 'SEC 13F-HR quarterly filings: who holds what, in shares and USD value.',
    icon: 'business_center',
    color: 'text-violet-400',
    bg: 'bg-violet-400/10',
  },
  {
    type: 'CONTRACTS_WITH',
    title: 'Gov. Contracts',
    desc: 'USASpending.gov federal awards - LLM-validated per recipient.',
    icon: 'account_balance',
    color: 'text-primary',
    bg: 'bg-primary/10',
  },
  {
    type: 'CONTROLS',
    title: 'Subsidiary Control',
    desc: 'Wikidata SPARQL ownership + GLEIF LEI legal hierarchy, LLM-validated.',
    icon: 'corporate_fare',
    color: 'text-emerald-400',
    bg: 'bg-emerald-400/10',
  },
  {
    type: 'PATENT_PARTNER',
    title: 'Patent Co-Filing',
    desc: 'PatentsView co-assigned patents across S&P 500, resolved via LLM.',
    icon: 'lightbulb',
    color: 'text-yellow-400',
    bg: 'bg-yellow-400/10',
  },
  {
    type: 'BOARD_OVERLAP',
    title: 'Board Interlocks',
    desc: 'Shared board members extracted from DEF 14A proxy filings.',
    icon: 'groups',
    color: 'text-sky-400',
    bg: 'bg-sky-400/10',
  },
  {
    type: 'STRATEGIC_PARTNER',
    title: '8-K Agreements',
    desc: 'Material agreements from 8-K SEC filings parsed into bilateral edges.',
    icon: 'handshake',
    color: 'text-orange-400',
    bg: 'bg-orange-400/10',
  },
]

const stats = [
  { value: '12', label: 'Data ingestion phases', icon: 'database', color: 'text-primary', bg: 'bg-primary/10' },
  { value: '85K+', label: 'COMPETES_WITH edges', icon: 'compare_arrows', color: 'text-rose-400', bg: 'bg-rose-400/10' },
  { value: '500+', label: 'S&P 500 companies mapped', icon: 'domain', color: 'text-emerald-400', bg: 'bg-emerald-400/10' },
  { value: 'LLM', label: 'Validation on every edge', icon: 'verified', color: 'text-violet-400', bg: 'bg-violet-400/10' },
]

const dataSources = [
  'Polygon.io', 'SEC EDGAR', 'USASpending', 'PatentsView',
  'Wikidata', 'GLEIF LEI', 'DEF 14A', '8-K', 'BEA I-O',
]
</script>
