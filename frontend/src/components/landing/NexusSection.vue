<template>
  <section aria-labelledby="nexus-title">
    <div class="mx-auto w-full max-w-[1200px] px-4 sm:px-6 lg:px-8">
      <div class="border-t border-white/[0.06] py-20 md:py-28">

        <div class="max-w-3xl">
          <p class="text-sm text-slate-500">Graph Nexus</p>
          <h2 id="nexus-title" class="mt-3 text-3xl leading-[1.15] md:text-[40px] md:leading-[1.1] font-medium tracking-[-0.02em] text-slate-100">
            How public companies are connected
          </h2>
          <p class="mt-5 text-base leading-7 md:text-lg md:leading-8 text-slate-400">
            The Graph Nexus is a knowledge graph of competitors, suppliers, owners,
            federal contracts, patents and shared board members, built from SEC filings
            and public data sets. Strategies query it as they trade.
          </p>
        </div>

        <!-- Edge types: one ledger, hairlines between cells -->
        <div class="mt-14 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-px rounded-xl overflow-hidden border border-white/[0.07] bg-white/[0.06]">
          <div v-for="edge in edges" :key="edge.type" class="bg-surface p-5">
            <p class="font-mono text-xs text-slate-500">{{ edge.type }}</p>
            <p class="mt-3 text-sm font-medium text-slate-100">{{ edge.title }}</p>
            <p class="mt-1.5 text-[13px] leading-5 text-slate-400">{{ edge.desc }}</p>
          </div>
        </div>

        <div class="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px] items-stretch">

          <!-- A slice of the graph around one company -->
          <div class="relative flex flex-col rounded-xl border border-white/[0.07] bg-sidebar overflow-hidden">
            <svg
              class="block w-full h-[300px] sm:h-[420px]"
              viewBox="0 0 560 400"
              preserveAspectRatio="xMidYMid meet"
              role="img"
              aria-label="Graph slice around NVDA: TSMC supplies it, AMD and Intel compete with it, Microsoft buys from it, Vanguard holds it"
            >
              <g v-for="e in graphEdges" :key="e.to">
                <line
                  :x1="center.x" :y1="center.y" :x2="nodeById[e.to].x" :y2="nodeById[e.to].y"
                  :stroke="e.lead ? '#ededed' : 'rgba(255,255,255,0.14)'"
                  :stroke-width="e.lead ? 1.5 : 1"
                />
                <text
                  :x="(center.x + nodeById[e.to].x) / 2 + e.dx"
                  :y="(center.y + nodeById[e.to].y) / 2 + e.dy"
                  :fill="e.lead ? '#d4d4d8' : '#71717a'"
                  stroke="#0b0b0c"
                  stroke-width="6"
                  paint-order="stroke"
                  dominant-baseline="middle"
                  font-size="10"
                  font-family="ui-monospace, SFMono-Regular, Menlo, monospace"
                  text-anchor="middle"
                >{{ e.type }}</text>
              </g>
              <g v-for="n in nodes" :key="n.id">
                <circle :cx="n.x" :cy="n.y" r="5" fill="#1e1e20" :stroke="n.lead ? '#ededed' : 'rgba(255,255,255,0.28)'" stroke-width="1" />
                <text :x="n.x" :y="n.y + n.ly" fill="#a1a1aa" font-size="12" text-anchor="middle">{{ n.label }}</text>
              </g>
              <circle :cx="center.x" :cy="center.y" r="9" fill="#ededed" />
              <text :x="center.x" :y="center.y + 28" fill="#ededed" font-size="13" font-weight="500" text-anchor="middle">NVDA</text>
            </svg>

            <div class="m-4 sm:m-0 sm:absolute sm:left-6 sm:bottom-6 sm:max-w-[280px] rounded-xl border border-white/[0.08] bg-surface px-4 py-3">
              <p class="inline-flex items-center gap-2 text-xs text-slate-400">
                <span class="ui-dot ui-dot-live"></span>Live edge detected
              </p>
              <p class="mt-2 text-sm font-medium text-slate-100">Supply chain shock</p>
              <p class="mt-1 text-xs leading-5 text-slate-400">
                TSMC to NVDA <span class="font-mono text-slate-300">SUPPLIER_OF</span>
                sentiment: <span class="text-gain tabular-nums">Bullish +0.82</span>
              </p>
            </div>
          </div>

          <!-- Figures + sources -->
          <div class="ui-card divide-y divide-white/[0.06]">
            <div v-for="stat in stats" :key="stat.label" class="px-5 py-4">
              <p class="text-2xl font-medium text-slate-100 tabular-nums">{{ stat.value }}</p>
              <p class="mt-1 text-sm text-slate-400">{{ stat.label }}</p>
            </div>
            <div class="px-5 py-4">
              <p class="text-xs text-slate-500">Data sources</p>
              <ul class="mt-2.5 flex flex-wrap gap-x-4 gap-y-1.5">
                <li v-for="src in dataSources" :key="src" class="text-sm text-slate-300">{{ src }}</li>
              </ul>
            </div>
          </div>
        </div>

      </div>
    </div>
  </section>
</template>

<script setup>
const edges = [
  { type: 'COMPETES_WITH', title: 'Competitors', desc: 'Companies in the same SIC industry group, linked both ways.' },
  { type: 'SUPPLY_CHAIN', title: 'Suppliers & customers', desc: '10-K customer/supplier disclosures from SEC EDGAR, confidence-scored.' },
  { type: 'HELD_BY', title: 'Institutional ownership', desc: 'SEC 13F-HR quarterly filings: who holds what, in shares and USD value.' },
  { type: 'CONTRACTS_WITH', title: 'Gov. contracts', desc: 'USASpending.gov federal awards, LLM-validated per recipient.' },
  { type: 'CONTROLS', title: 'Subsidiary control', desc: 'Wikidata SPARQL ownership + GLEIF LEI legal hierarchy, LLM-validated.' },
  { type: 'PATENT_PARTNER', title: 'Patent co-filing', desc: 'PatentsView co-assigned patents across S&P 500, resolved via LLM.' },
  { type: 'BOARD_OVERLAP', title: 'Board interlocks', desc: 'Shared board members extracted from DEF 14A proxy filings.' },
  { type: 'STRATEGIC_PARTNER', title: '8-K agreements', desc: 'Material agreements from 8-K SEC filings parsed into bilateral edges.' },
]


const dataSources = [
  'Polygon.io', 'SEC EDGAR', 'USASpending', 'PatentsView',
  'Wikidata', 'GLEIF LEI', 'DEF 14A', '8-K', 'BEA I-O',
]

// Counts taken from the lists on this page, so they can't drift from it.
const stats = [
  { value: String(edges.length), label: 'Relationship types' },
  { value: String(dataSources.length), label: 'Data sources' },
]

// Illustrative slice of the graph. The lead edge is the one the card describes.
const center = { x: 300, y: 190 }
const nodes = [
  { id: 'tsm',  label: 'TSMC',      x: 120, y: 90,  ly: -14, lead: true },
  { id: 'amd',  label: 'AMD',       x: 480, y: 100, ly: -14 },
  { id: 'intc', label: 'Intel',     x: 500, y: 270, ly: 22 },
  { id: 'msft', label: 'Microsoft', x: 330, y: 340, ly: 22 },
  { id: 'vg',   label: 'Vanguard',  x: 130, y: 280, ly: 22 },
]
const nodeById = Object.fromEntries(nodes.map((n) => [n.id, n]))
// Labels sit on the edge midpoint; the text halo cuts the line behind them.
const graphEdges = [
  { to: 'tsm',  type: 'SUPPLIER_OF',   dx: 0, dy: 0, lead: true },
  { to: 'amd',  type: 'COMPETES_WITH', dx: 0, dy: 0 },
  { to: 'intc', type: 'COMPETES_WITH', dx: 0, dy: 0 },
  { to: 'msft', type: 'CUSTOMER_OF',   dx: 0, dy: 0 },
  { to: 'vg',   type: 'HELD_BY',       dx: 0, dy: 0 },
]
</script>
