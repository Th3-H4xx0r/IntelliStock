// Data loading for the dashboard's Holdings, Insights and Market sections.
// Endpoints and transforms follow the iOS dashboard
// (ios/IntelliStock/Features/Dashboard/Data/DashboardRepository.swift and
// Model/DashboardInsightsModel.swift); the math lives in dashboardAnalytics.js.

import { ref, watch, onUnmounted } from 'vue'
import { getToken } from '../utils/auth.js'
import {
  INDEX_SYMBOLS, SECTOR_ETFS, pctChangeOf, rangeForHoldingAge, sliceSinceMidnight, totalSpark,
  isOccOptionSymbol, aggregateBySector, riskMetrics,
} from '../utils/dashboardAnalytics.js'

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')

export async function apiGet(path, { signal } = {}) {
  const token = getToken()
  const res = await fetch(`${API_BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

/** GET /symbol-historicals → { SYMBOL: [{ts, value}] } */
async function historicals(symbols, range) {
  if (!symbols.length) return {}
  const q = new URLSearchParams({ symbols: symbols.join(','), range })
  const d = await apiGet(`/symbol-historicals?${q}`)
  return d?.results || {}
}

/** Run `fn` every `ms` while the page is visible; stops on unmount. */
function poll(fn, ms) {
  const timer = setInterval(() => { if (document.visibilityState === 'visible') fn() }, ms)
  onUnmounted(() => clearInterval(timer))
}

/**
 * The selected account's cash and positions, plus per-holding sparklines for
 * the Daily (1D since midnight) and Total (since purchase) modes.
 * `brokerageId` is a ref; null means "no stock account selected".
 */
export function useAccountHoldings(brokerageId) {
  const holdings = ref(null)        // { cash, positions } | null while loading
  const sparks = ref({ daily: null, total: null }) // { SYMBOL: number[] } per mode
  let generation = 0

  async function loadHoldings() {
    const id = brokerageId.value
    if (!id) { holdings.value = null; return }
    const gen = generation
    try {
      const d = await apiGet(`/brokerages/${id}/positions`)
      if (gen !== generation) return
      holdings.value = {
        cash: Number.isFinite(Number(d?.cash)) && d?.cash != null ? Number(d.cash) : null,
        positions: (d?.positions || [])
          .filter(p => p?.symbol)
          .map(p => ({
            symbol: p.symbol,
            qty: Number(p.qty) || 0,
            marketValue: Number(p.marketValue) || 0,
            unrealizedPnl: Number(p.unrealizedPnl) || 0,
            unrealizedPnlPct: Number(p.unrealizedPnlPct) || 0,
            lastPrice: p.lastPrice == null ? null : Number(p.lastPrice),
            avgEntryPrice: p.avgEntryPrice == null ? null : Number(p.avgEntryPrice),
          })),
      }
    } catch {
      // Keep the last good holdings on a failed poll; an empty book on a failed first load.
      if (gen === generation && holdings.value == null) holdings.value = { cash: null, positions: [] }
    }
  }

  async function loadDailySparks() {
    const positions = holdings.value?.positions || []
    const gen = generation
    try {
      const hist = await historicals(positions.map(p => p.symbol), '1D')
      if (gen !== generation) return
      const out = {}
      for (const [sym, pts] of Object.entries(hist)) {
        const vals = sliceSinceMidnight(pts)
        if (vals) out[sym] = vals
      }
      sparks.value = { ...sparks.value, daily: out }
    } catch {
      if (gen === generation) sparks.value = { ...sparks.value, daily: {} }
    }
  }

  async function loadTotalSparks() {
    const id = brokerageId.value
    const positions = holdings.value?.positions || []
    if (!id || !positions.length || sparks.value.total) return
    const gen = generation
    let opens = {}
    try { opens = (await apiGet(`/brokerages/${id}/holding-opens`))?.opens || {} } catch { /* full series */ }
    const now = Date.now()
    const byRange = new Map()
    for (const p of positions) {
      const opened = opens[p.symbol] ? new Date(opens[p.symbol]) : null
      const range = opened ? rangeForHoldingAge(Math.floor((now - opened.getTime()) / 86_400_000)) : 'ALL'
      if (!byRange.has(range)) byRange.set(range, [])
      byRange.get(range).push({ p, opened })
    }
    const out = {}
    await Promise.all([...byRange.entries()].map(async ([range, items]) => {
      try {
        const hist = await historicals(items.map(i => i.p.symbol), range)
        for (const { p, opened } of items) {
          const vals = totalSpark(hist[p.symbol], opened, p.avgEntryPrice, p.lastPrice)
          if (vals) out[p.symbol] = vals
        }
      } catch { /* those rows draw no line */ }
    }))
    if (gen === generation) sparks.value = { ...sparks.value, total: out }
  }

  async function reload() {
    generation += 1
    holdings.value = null
    sparks.value = { daily: null, total: null }
    if (!brokerageId.value) return
    await loadHoldings()
    await loadDailySparks()
  }

  watch(brokerageId, reload, { immediate: true })
  poll(loadHoldings, 30_000)
  poll(loadDailySparks, 120_000)

  return { holdings, sparks, loadTotalSparks }
}

// Sector lookups are slow and never change within a session.
const sectorCache = new Map()
async function sectorOf(symbol) {
  if (sectorCache.has(symbol)) return sectorCache.get(symbol)
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), 8000)
  let sector = null
  try {
    const info = await apiGet(`/symbols/${encodeURIComponent(symbol)}/info`, { signal: ctl.signal })
    sector = typeof info?.sector === 'string' ? info.sector : null
  } catch { /* unknown */ } finally { clearTimeout(timer) }
  sectorCache.set(symbol, sector)
  return sector
}

/** Sector slices for a positions list (stocks only). */
export async function loadSectorAllocation(positions) {
  const stocks = (positions || []).filter(p => p.marketValue > 0 && !isOccOptionSymbol(p.symbol))
  if (!stocks.length) return []
  const sectors = {}
  await Promise.all(stocks.map(async p => { sectors[p.symbol] = await sectorOf(p.symbol) }))
  return aggregateBySector(stocks.map(p => ({ symbol: p.symbol, value: p.marketValue })), sectors)
}

/** Risk summary from the account's 1Y equity curve. */
export async function loadRisk(brokerageId) {
  try {
    const h = await apiGet(`/brokerages/${brokerageId}/portfolio-history?range=1Y`)
    return riskMetrics((h?.values || []).map(Number))
  } catch {
    return riskMetrics([])
  }
}

/** Index or sector-ETF snapshots for today: { symbol, label, pct, price, values }. */
async function quotes(universe, sortByPct) {
  const hist = await historicals(universe.map(u => u.symbol), '1D')
  const out = []
  for (const u of universe) {
    const vals = (hist[u.symbol] || []).map(p => Number(p.value))
    const pct = pctChangeOf(vals)
    if (pct == null) continue
    out.push({ ...u, pct, price: vals[vals.length - 1], values: vals })
  }
  return sortByPct ? out.sort((a, b) => b.pct - a.pct) : out
}

/**
 * Market-wide data (indices, sector performance, news), plus the account
 * screener's movers and Nexus momentum for the selected stock account.
 */
export function useMarketData(brokerageId) {
  const indices = ref(null)
  const sectors = ref(null)
  const news = ref(null)
  const movers = ref(null)
  const momentum = ref(null)

  async function loadQuotes() {
    const [i, s] = await Promise.all([
      quotes(INDEX_SYMBOLS, false).catch(() => []),
      quotes(SECTOR_ETFS, true).catch(() => []),
    ])
    indices.value = i
    sectors.value = s
  }

  async function loadNews() {
    try {
      const d = await apiGet('/market/news?limit=15')
      news.value = (d?.articles || []).filter(a => a?.title)
    } catch { news.value = [] }
  }

  async function loadAccountMarket() {
    const id = brokerageId.value
    movers.value = null
    momentum.value = null
    if (!id) { movers.value = { gainers: [], losers: [] }; momentum.value = []; return }
    const [m, n] = await Promise.all([
      apiGet(`/brokerages/${id}/movers?top=6`).catch(() => null),
      apiGet(`/brokerages/${id}/nexus-momentum`).catch(() => null),
    ])
    if (id !== brokerageId.value) return
    const rows = l => (l || []).filter(r => r?.symbol).map(r => ({ symbol: r.symbol, pct: r.pct, price: r.price }))
    movers.value = { gainers: rows(m?.gainers), losers: rows(m?.losers) }
    momentum.value = (n?.momentum || []).filter(r => r?.symbol).map(r => ({ symbol: r.symbol, score: Number(r.score) || 0 }))
  }

  loadQuotes()
  loadNews()
  watch(brokerageId, loadAccountMarket, { immediate: true })
  poll(loadQuotes, 60_000)
  poll(loadNews, 10 * 60_000)

  return { indices, sectors, news, movers, momentum }
}

/**
 * Open orders and recent order history for the selected stock account,
 * straight from the broker (GET /brokerages/{id}/order-book). Polls every
 * 30s; a failed poll keeps the last good book.
 */
export function useOrderBook(brokerageId) {
  const book = ref(null)   // { supported, open, recent } | null while loading
  const error = ref('')
  let generation = 0

  async function load() {
    const id = brokerageId.value
    if (!id) { book.value = null; return }
    const gen = generation
    try {
      const d = await apiGet(`/brokerages/${encodeURIComponent(id)}/order-book?recent_limit=25`)
      if (gen !== generation) return
      book.value = {
        supported: d?.supported !== false,
        open: Array.isArray(d?.open) ? d.open : [],
        recent: Array.isArray(d?.recent) ? d.recent : [],
      }
      error.value = ''
    } catch {
      if (gen === generation && book.value == null) error.value = "Couldn't load orders"
    }
  }

  function reload() {
    generation += 1
    book.value = null
    error.value = ''
    load()
  }

  watch(brokerageId, reload, { immediate: true })
  poll(load, 30_000)

  return { book, error, reload }
}

/**
 * The instances that trade the selected account (GET /instances, filtered by
 * brokerage_id), polled every 30s so a start or stop shows up. A failed poll
 * keeps the last good list.
 */
export function useAccountInstances(brokerageId) {
  const all = ref(null)   // every instance | null while loading
  const error = ref('')

  async function load() {
    try {
      const d = await apiGet('/instances')
      all.value = Array.isArray(d?.instances) ? d.instances : (Array.isArray(d) ? d : [])
      error.value = ''
    } catch {
      if (all.value == null) error.value = "Couldn't load instances"
    }
  }

  const instances = ref(null)
  watch([all, brokerageId], ([list, id]) => {
    instances.value = list == null ? null : list.filter(i => id != null && String(i?.brokerage_id ?? '') === String(id))
  }, { immediate: true })

  load()
  poll(load, 30_000)
  return { instances, error }
}

/**
 * The bots' latest buy/sell decisions on the selected account, newest first
 * (GET /brokerages/{id}/bot-activity), polled every 60s.
 */
export function useBotActivity(brokerageId, limit = 6) {
  const events = ref(null)  // [] | null while loading
  const total = ref(0)
  let generation = 0

  async function load() {
    const id = brokerageId.value
    if (!id) { events.value = null; return }
    const gen = generation
    try {
      const d = await apiGet(`/brokerages/${encodeURIComponent(id)}/bot-activity?per_page=${limit}`)
      if (gen !== generation) return
      events.value = Array.isArray(d?.events) ? d.events : []
      total.value = Number(d?.total) || events.value.length
    } catch {
      if (gen === generation && events.value == null) events.value = []
    }
  }

  watch(brokerageId, () => { generation += 1; events.value = null; load() }, { immediate: true })
  poll(load, 60_000)
  return { events, total }
}
