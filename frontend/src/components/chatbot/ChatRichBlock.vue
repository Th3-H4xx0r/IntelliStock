<script setup>
import { computed } from 'vue'
import VueApexCharts from 'vue3-apexcharts'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

// Force every rendered anchor to open in a new tab with rel=noopener so a
// model-emitted `<a target="_blank">` (or upstream-injected one) can't
// tabnab us via window.opener.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node && node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

const props = defineProps({
  block: { type: Object, required: true },
})

// Default ApexCharts options (dark theme + transparent bg, matching the rest of the app).
const chartDefaults = {
  theme: { mode: 'dark' },
  chart: {
    background: 'transparent',
    toolbar: { show: false },
    animations: { enabled: true },
    fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
  },
  grid: { borderColor: '#1e2535', strokeDashArray: 3 },
  legend: { labels: { colors: '#94a3b8' } },
  stroke: { curve: 'smooth', width: 2 },
  xaxis: {
    type: 'category',
    labels: { style: { colors: '#64748b', fontSize: '11px' } },
    axisBorder: { color: '#1e2535' },
    axisTicks: { color: '#1e2535' },
  },
  yaxis: { labels: { style: { colors: '#64748b' } } },
  tooltip: { theme: 'dark' },
  colors: ['#a78bfa', '#d8b4fe', '#34d399', '#f59e0b', '#f87171'],
}

const chartOptions = computed(() => {
  if (props.block?.type !== 'chart') return chartDefaults
  const overrides = props.block.options || {}
  const xType = props.block.x_axis_type || 'category'
  return {
    ...chartDefaults,
    ...overrides,
    chart: { ...chartDefaults.chart, ...(overrides.chart || {}) },
    xaxis: { ...chartDefaults.xaxis, type: xType, ...(overrides.xaxis || {}) },
    yaxis: { ...chartDefaults.yaxis, ...(overrides.yaxis || {}) },
  }
})

const chartHeight = computed(() => Math.min(280, Math.max(180, props.block?.height || 220)))

// ── Portfolio / price chart helpers (PortfolioChart-style look) ─────────────

function _xFormat(range) {
  const r = String(range || '').toUpperCase()
  if (r === '1D') return 'HH:mm'
  if (r === '1W' || r === '1M') return 'MMM dd'
  return 'MMM yyyy'
}

function _toEpoch(ts) {
  // Accept ISO strings, numeric ms, or numeric seconds.
  if (ts == null) return null
  if (typeof ts === 'number') return ts > 1e12 ? ts : ts * 1000
  const t = Date.parse(String(ts))
  return Number.isFinite(t) ? t : null
}

function _portfolioPaired(block) {
  const tsArr = block?.timestamps || []
  const vals = block?.values || []
  const out = []
  const n = Math.min(tsArr.length, vals.length)
  for (let i = 0; i < n; i++) {
    const epoch = _toEpoch(tsArr[i])
    const v = Number(vals[i])
    if (epoch == null || !Number.isFinite(v)) continue
    out.push([epoch, v])
  }
  return out
}

// Build sequential-index series so weekend / overnight gaps don't render as
// flat horizontal stretches with sharp vertical jumps when the market reopens.
// This is the same trick LiveTradingView uses on its per-position sparklines:
// pack every real bar into [index, value] pairs and look up the underlying
// timestamp from a parallel array for label / tooltip rendering.
function _priceSeries(block) {
  const series = block?.series || []
  return series.map(s => {
    const pts = []
    for (const p of (s.points || [])) {
      const epoch = _toEpoch(p?.ts)
      const v = Number(p?.value)
      if (epoch == null || !Number.isFinite(v) || v <= 0) continue
      pts.push([pts.length, v])
    }
    return { name: s.symbol || 'Series', data: pts }
  })
}

// Parallel timestamp array (per series) for axis label / tooltip lookup.
function _priceTimestamps(block) {
  const series = block?.series || []
  return series.map(s => {
    const ts = []
    for (const p of (s.points || [])) {
      const epoch = _toEpoch(p?.ts)
      const v = Number(p?.value)
      if (epoch == null || !Number.isFinite(v) || v <= 0) continue
      ts.push(epoch)
    }
    return ts
  })
}

const portfolioPositive = computed(() => Number(props.block?.change_abs || 0) >= 0)

const portfolioOptions = computed(() => {
  if (props.block?.type !== 'portfolio') return chartDefaults
  const positive = portfolioPositive.value
  const lineColor = positive ? '#34d399' : '#f87171'
  const xFmt = _xFormat(props.block.range)
  return {
    chart: {
      type: 'area',
      background: 'transparent',
      toolbar: { show: false },
      zoom: { enabled: false },
      animations: { enabled: true, speed: 400 },
      fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
    },
    colors: [lineColor],
    fill: {
      type: 'gradient',
      gradient: {
        shadeIntensity: 1,
        opacityFrom: 0.45,
        opacityTo: 0.02,
        stops: [0, 95],
        colorStops: [
          { offset: 0,   color: lineColor, opacity: 0.35 },
          { offset: 100, color: lineColor, opacity: 0.0  },
        ],
      },
    },
    stroke: { curve: 'smooth', width: 2 },
    dataLabels: { enabled: false },
    grid: {
      borderColor: 'rgba(255,255,255,0.05)',
      strokeDashArray: 4,
      xaxis: { lines: { show: false } },
      yaxis: { lines: { show: true } },
      padding: { left: 0, right: 8 },
    },
    xaxis: {
      type: 'datetime',
      labels: {
        style: { colors: '#64748b', fontSize: '11px' },
        datetimeUTC: false,
        format: xFmt,
      },
      axisBorder: { show: false },
      axisTicks: { show: false },
      tooltip: { enabled: false },
    },
    yaxis: {
      labels: {
        style: { colors: '#64748b', fontSize: '11px' },
        // Plain function (not arrow) — vue3-apexcharts has historically lost
        // arrow-fn formatters when re-cloning options across re-renders.
        formatter: function (v) {
          const n = Number(v)
          if (!Number.isFinite(n)) return '$0'
          return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 })
        },
      },
      tickAmount: 4,
      // Hard cap on auto-decimal expansion. Without this, ApexCharts can
      // pick 10+ decimals on tightly-clustered data and the formatter
      // override doesn't always win.
      decimalsInFloat: 2,
      forceNiceScale: true,
    },
    tooltip: {
      theme: 'dark',
      x: { format: xFmt === 'HH:mm' ? 'MMM dd · HH:mm' : 'MMM dd, yyyy' },
      y: { formatter: function (v) { return '$' + Number(v).toFixed(2) } },
    },
    markers: { size: 0, hover: { size: 4, strokeColors: lineColor, strokeWidth: 2 } },
    theme: { mode: 'dark' },
  }
})

// For a single-symbol chart, derive positive/negative from first→last close
// so the area gradient + line color match the LiveTradingTerminal look.
const priceTrendPositive = computed(() => {
  if (props.block?.type !== 'price') return true
  const series = props.block.series || []
  if (series.length !== 1) return true
  const pts = series[0].points || []
  if (pts.length < 2) return true
  const first = Number(pts[0]?.value)
  const last = Number(pts[pts.length - 1]?.value)
  if (!Number.isFinite(first) || !Number.isFinite(last)) return true
  return last >= first
})

const priceOptions = computed(() => {
  if (props.block?.type !== 'price') return chartDefaults
  const series = props.block.series || []
  const isSingle = series.length === 1
  const range = String(props.block.range || '').toUpperCase()
  const positive = priceTrendPositive.value
  const lineColor = isSingle ? (positive ? '#34d399' : '#f87171') : '#00e1ff'

  // Build a flat list of timestamps aligned to the index used in the
  // sequential-x series. For multi-symbol charts we just use the first
  // series' timestamps for label rendering — they all cover the same span.
  const tsList = (priceTimestampsAll.value && priceTimestampsAll.value[0]) || []

  function fmtTs(epoch) {
    if (!Number.isFinite(epoch)) return ''
    const d = new Date(epoch)
    if (range === '1D') {
      return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
    }
    if (range === '1W' || range === '1M') {
      return d.toLocaleDateString('en-US', { month: 'short', day: '2-digit' })
    }
    return d.toLocaleDateString('en-US', { month: 'short', year: 'numeric' })
  }

  const opts = {
    chart: {
      type: isSingle ? 'area' : 'line',
      background: 'transparent',
      toolbar: { show: false },
      zoom: { enabled: false },
      sparkline: { enabled: false },
      animations: { enabled: true, easing: 'easeinout', speed: 400 },
      fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
    },
    colors: isSingle ? [lineColor] : ['#a78bfa', '#d8b4fe', '#34d399', '#f59e0b', '#f87171'],
    // Straight curve — same as LiveTradingView's equity + per-position
    // charts. Smoothing on sparse intraday bars produces fake sine-wave
    // artifacts when there are stretches with no real ticks (overnight,
    // weekends, halts). Straight gives RH-style honest tick rendering.
    stroke: { curve: 'straight', width: 1.75 },
    dataLabels: { enabled: false },
    grid: {
      borderColor: 'rgba(255,255,255,0.05)',
      strokeDashArray: 4,
      xaxis: { lines: { show: false } },
      yaxis: { lines: { show: true } },
      padding: { left: 0, right: 8 },
    },
    xaxis: {
      // Numeric sequential x — same trick LiveTradingView uses on its
      // per-position sparklines. Treats every data point as one unit
      // wide so closed-market gaps don't render as ugly flat segments
      // joined by vertical jumps when trading resumes.
      type: 'numeric',
      tickAmount: 4,
      labels: {
        style: { colors: '#64748b', fontSize: '11px', fontFamily: 'inherit' },
        formatter: function (val) {
          const i = Math.round(Number(val))
          const epoch = tsList[i]
          return epoch != null ? fmtTs(epoch) : ''
        },
      },
      axisBorder: { show: false },
      axisTicks: { show: false },
      crosshairs: { stroke: { color: 'rgba(255,255,255,0.15)', width: 1, dashArray: 4 } },
      tooltip: { enabled: false },
    },
    yaxis: {
      labels: {
        style: { colors: '#64748b', fontSize: '11px', fontFamily: 'inherit' },
        offsetX: -8,
        minWidth: 56,
        formatter: function (v) {
          const n = Number(v)
          if (!Number.isFinite(n)) return '$0.00'
          return '$' + n.toLocaleString('en-US', {
            minimumFractionDigits: n < 100 ? 2 : 0,
            maximumFractionDigits: 2,
          })
        },
      },
      tickAmount: 4,
      decimalsInFloat: 2,
      forceNiceScale: true,
    },
    tooltip: {
      theme: 'dark',
      x: {
        formatter: function (val) {
          const i = Math.round(Number(val))
          const epoch = tsList[i]
          if (epoch == null) return ''
          const d = new Date(epoch)
          if (range === '1D') {
            return d.toLocaleString('en-US', { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' })
          }
          return d.toLocaleDateString('en-US', { month: 'short', day: '2-digit', year: 'numeric' })
        },
      },
      y: { formatter: function (v) { return '$' + Number(v).toFixed(2) } },
    },
    legend: isSingle
      ? { show: false }
      : { labels: { colors: '#cbd5e1' }, position: 'top', horizontalAlign: 'left' },
    markers: { size: 0, hover: { size: 4, strokeColors: lineColor, strokeWidth: 2 } },
    theme: { mode: 'dark' },
  }

  if (isSingle) {
    opts.fill = {
      type: 'gradient',
      gradient: {
        shadeIntensity: 1,
        opacityFrom: 0.45,
        opacityTo: 0.02,
        stops: [0, 95],
        colorStops: [
          { offset: 0,   color: lineColor, opacity: 0.35 },
          { offset: 100, color: lineColor, opacity: 0.0  },
        ],
      },
    }
  } else {
    opts.fill = { type: 'solid', opacity: 0 }
  }

  return opts
})

const portfolioSeries = computed(() => {
  if (props.block?.type !== 'portfolio') return []
  return [{ name: props.block.title || 'Portfolio', data: _portfolioPaired(props.block) }]
})

const priceSeriesNormalised = computed(() => {
  if (props.block?.type !== 'price') return []
  return _priceSeries(props.block)
})

// Per-series parallel timestamp arrays — used by the x-axis label and
// tooltip formatters to map the numeric index back to a real wall-clock
// time without including the index in the series payload itself.
const priceTimestampsAll = computed(() => {
  if (props.block?.type !== 'price') return []
  return _priceTimestamps(props.block)
})

// Header card metadata for a single-symbol price chart — symbol + latest
// close + signed change vs the first point in the range. Empty for multi-
// symbol charts (which fall back to a generic title).
const priceHeader = computed(() => {
  if (props.block?.type !== 'price') return { symbol: '', last: null, changeAbs: null, changePct: null }
  const series = props.block.series || []
  if (series.length !== 1) {
    return {
      symbol: props.block.title || (series.map(s => s.symbol).filter(Boolean).join(', ')) || 'Price history',
      last: null, changeAbs: null, changePct: null,
    }
  }
  const s = series[0]
  const sym = s.symbol || (props.block.title || 'Price')
  const pts = (s.points || []).filter(p => Number.isFinite(Number(p?.value)))
  if (pts.length === 0) return { symbol: sym, last: null, changeAbs: null, changePct: null }
  const first = Number(pts[0].value)
  const last = Number(pts[pts.length - 1].value)
  const abs = last - first
  const pct = first ? (abs / first) * 100 : null
  return { symbol: sym, last, changeAbs: abs, changePct: pct }
})

function fmtMoney(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(n)
}

function fmtPct(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return ''
  const s = n >= 0 ? '+' : ''
  return `${s}${n.toFixed(2)}%`
}

function fmtSignedMoney(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return ''
  return (n >= 0 ? '+' : '') + fmtMoney(n)
}

const renderedMarkdown = computed(() => {
  if (props.block?.type !== 'markdown') return ''
  const raw = String(props.block.content || '')
  try {
    const html = marked(raw, { breaks: true, gfm: true })
    // Rely on DOMPurify's default URI allowlist (blocks javascript:, vbscript:,
    // data:, file:, and leading-whitespace tricks). The afterSanitizeAttributes
    // hook above adds target/rel safely.
    return DOMPurify.sanitize(html, {
      ALLOWED_TAGS: [
        'p', 'br', 'strong', 'em', 'code', 'pre', 'a', 'ul', 'ol', 'li',
        'blockquote', 'h1', 'h2', 'h3', 'h4', 'span', 'hr',
        'table', 'thead', 'tbody', 'tr', 'th', 'td',
      ],
      ALLOWED_ATTR: ['href', 'title', 'class'],
    })
  } catch {
    return DOMPurify.sanitize(raw)
  }
})

function alignClass(align) {
  if (align === 'right') return 'text-right'
  if (align === 'center') return 'text-center'
  return 'text-left'
}

function fmtCell(value) {
  if (value == null) return ''
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return String(value)
    if (Math.abs(value) >= 1000) return value.toLocaleString()
    return value.toString()
  }
  return String(value)
}

const trendIcon = computed(() => {
  if (props.block?.type !== 'stat') return null
  if (props.block.trend === 'up') return { icon: 'trending_up', cls: 'text-emerald-400' }
  if (props.block.trend === 'down') return { icon: 'trending_down', cls: 'text-red-400' }
  return null
})
</script>

<template>
  <div class="chat-rich-block w-full max-w-full min-w-0 overflow-hidden">
    <!-- Markdown -->
    <div
      v-if="block?.type === 'markdown'"
      class="chat-md text-sm text-slate-200 leading-relaxed max-w-full overflow-hidden break-words"
      v-html="renderedMarkdown"
    />

    <!-- Chart -->
    <div v-else-if="block?.type === 'chart'" class="rounded-xl border border-border-subtle bg-surface/50 p-3 sm:p-4 max-w-full overflow-hidden">
      <p v-if="block.title" class="text-[11px] font-semibold uppercase tracking-widest text-slate-500 mb-1 truncate">{{ block.title }}</p>
      <VueApexCharts
        :type="block.chart_type || 'area'"
        :series="block.series || []"
        :options="chartOptions"
        :height="chartHeight"
      />
    </div>

    <!-- Portfolio chart (PortfolioChart-style: gradient area, dynamic green/red, header + stats) -->
    <div v-else-if="block?.type === 'portfolio'" class="rounded-xl border border-border-subtle bg-surface/50 p-3 sm:p-4 max-w-full overflow-hidden">
      <div class="flex items-start justify-between gap-3 mb-2">
        <div class="min-w-0">
          <p class="text-[10px] font-semibold uppercase tracking-widest text-slate-500 truncate">{{ block.title || 'Portfolio' }}</p>
          <p class="text-base sm:text-lg font-bold text-slate-100 leading-tight mt-0.5">
            {{ fmtMoney(block.current_value) }}
          </p>
          <p
            v-if="block.change_abs != null || block.change_pct != null"
            class="text-[11px] font-mono mt-0.5"
            :class="portfolioPositive ? 'text-emerald-400' : 'text-red-400'"
          >
            {{ fmtSignedMoney(block.change_abs) }}<span v-if="block.change_pct != null"> · {{ fmtPct(block.change_pct) }}</span>
          </p>
        </div>
        <span
          v-if="block.range"
          class="shrink-0 text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-md border border-primary/30 text-primary bg-primary/10"
        >{{ block.range }}</span>
      </div>
      <VueApexCharts
        type="area"
        :series="portfolioSeries"
        :options="portfolioOptions"
        :height="220"
      />
    </div>

    <!-- Price-history chart — matches LiveTradingTerminal style.
         Single symbol: gradient area + dynamic green/red, header shows
         current price + signed change. Multi-symbol: line chart with legend. -->
    <div v-else-if="block?.type === 'price'" class="rounded-xl border border-border-subtle bg-surface/50 p-3 sm:p-4 max-w-full overflow-hidden">
      <div class="flex items-start justify-between gap-3 mb-2">
        <div class="min-w-0">
          <p class="text-[10px] font-semibold uppercase tracking-widest text-slate-500 truncate">
            {{ priceHeader.symbol }}
          </p>
          <p v-if="priceHeader.last != null" class="text-base sm:text-lg font-bold text-slate-100 leading-tight mt-0.5">
            {{ fmtMoney(priceHeader.last) }}
          </p>
          <p
            v-if="priceHeader.changeAbs != null"
            class="text-[11px] font-mono mt-0.5"
            :class="priceTrendPositive ? 'text-emerald-400' : 'text-red-400'"
          >
            {{ fmtSignedMoney(priceHeader.changeAbs) }}<span v-if="priceHeader.changePct != null"> · {{ fmtPct(priceHeader.changePct) }}</span>
          </p>
        </div>
        <span
          v-if="block.range"
          class="shrink-0 text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-md border border-primary/30 text-primary bg-primary/10"
        >{{ block.range }}</span>
      </div>
      <VueApexCharts
        :type="(block.series || []).length === 1 ? 'area' : 'line'"
        :series="priceSeriesNormalised"
        :options="priceOptions"
        :height="220"
      />
    </div>

    <!-- Table -->
    <div v-else-if="block?.type === 'table'" class="rounded-xl border border-border-subtle bg-surface/50 px-3 py-3 sm:px-4 max-w-full overflow-x-auto">
      <p v-if="block.title" class="text-[11px] font-semibold uppercase tracking-widest text-slate-500 mb-2">{{ block.title }}</p>
      <table class="w-full text-xs table-fixed">
        <thead>
          <tr class="border-b border-border-subtle/60">
            <th
              v-for="h in (block.headers || [])"
              :key="h.key || h.label"
              :class="['px-2 py-2 text-slate-500 font-semibold uppercase tracking-wider truncate', alignClass(h.align)]"
            >{{ h.label }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(row, idx) in (block.rows || [])"
            :key="idx"
            class="border-b border-border-subtle/30 last:border-b-0"
          >
            <td
              v-for="h in (block.headers || [])"
              :key="(h.key || h.label) + ':' + idx"
              :class="['px-2 py-2 text-slate-200 align-top break-words', alignClass(h.align)]"
              style="max-width: 220px; overflow-wrap: anywhere;"
            >{{ fmtCell(row[h.key]) }}</td>
          </tr>
          <tr v-if="!(block.rows || []).length">
            <td :colspan="(block.headers || []).length || 1" class="px-2 py-3 text-slate-500 italic text-center">No rows.</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Stat -->
    <div v-else-if="block?.type === 'stat'" class="rounded-xl border border-border-subtle bg-surface/50 px-4 py-3 inline-flex flex-col">
      <p class="text-[10px] uppercase tracking-widest text-slate-500 mb-1">{{ block.label }}</p>
      <div class="flex items-baseline gap-2">
        <p class="text-xl font-bold text-slate-100">{{ block.value }}</p>
        <span
          v-if="trendIcon"
          class="material-symbols-outlined text-base"
          :class="trendIcon.cls"
        >{{ trendIcon.icon }}</span>
      </div>
      <p v-if="block.detail" class="text-[11px] text-slate-500 mt-1">{{ block.detail }}</p>
    </div>

    <!-- Navigation pill (rendered as a callout; the dock catches navigate blocks separately) -->
    <div v-else-if="block?.type === 'navigate'" class="text-[11px] text-primary inline-flex items-center gap-1">
      <span class="material-symbols-outlined text-[14px]">north_east</span>
      Navigated to <code class="font-mono ml-1">{{ block.route }}</code>
    </div>

    <div v-else class="text-xs text-slate-500 italic">(unsupported block)</div>
  </div>
</template>

<style scoped>
.chat-md { word-break: break-word; overflow-wrap: anywhere; }
.chat-md :deep(p) { margin: 0 0 0.6em; line-height: 1.55; word-break: break-word; overflow-wrap: anywhere; }
.chat-md :deep(p:last-child) { margin-bottom: 0; }
.chat-md :deep(strong) { color: #f1f5f9; font-weight: 600; }
.chat-md :deep(em) { color: #cbd5e1; }
.chat-md :deep(code) { background: rgba(188, 154, 255, 0.10); padding: 1px 5px; border-radius: 4px; color: #d8b4fe; font-size: 0.85em; word-break: break-all; }
.chat-md :deep(pre) { background: #0a0716; border: 1px solid #231a3d; padding: 10px 12px; border-radius: 8px; overflow-x: auto; margin: 0.4em 0 0.6em; max-width: 100%; }
.chat-md :deep(pre code) { background: transparent; padding: 0; color: #cbd5e1; word-break: normal; white-space: pre; }
.chat-md :deep(ul), .chat-md :deep(ol) { padding-left: 1.25em; margin: 0.3em 0 0.6em; }
.chat-md :deep(li) { margin: 0.15em 0; }
.chat-md :deep(a) { color: #c98fff; text-decoration: underline; text-underline-offset: 2px; word-break: break-all; }
.chat-md :deep(table) { border-collapse: collapse; margin: 0.4em 0 0.6em; font-size: 0.85em; max-width: 100%; display: block; overflow-x: auto; }
.chat-md :deep(th), .chat-md :deep(td) { border-bottom: 1px solid #233136; padding: 4px 8px; }
.chat-md :deep(blockquote) { border-left: 2px solid #233136; padding-left: 0.75em; color: #94a3b8; margin: 0.3em 0; }
</style>
