// Pure helpers for the AI Analysis screen (views/AnalysisView.vue, the
// components under components/analysis/) and its settings card. The API
// contract is the "API" section of
// docs/superpowers/specs/2026-11-14-why-it-moved-design.md.
// No Vue and no fetch, so `node --test` covers them.

import { isTradableSymbol, normalizeSymbol } from './trade.js'

export const ANALYSIS_RANGES = ['1M', '3M', '6M', '1Y']
export const DEFAULT_RANGE = '3M'
/** How often GET /analysis/moves is re-read while events are pending. */
export const POLL_MS = 3000

export const SIGMA_LIMITS = { min: 1, max: 6 }
export const MIN_MOVE_LIMITS = { min: 0.5, max: 25 }

/** The filter bucket for events with no category yet (unexplained, pending, failed). */
export const UNEXPLAINED_KEY = 'unexplained'
export const ALL_KEY = 'all'

// ── Category colour and marker shape ─────────────────────────────────────────
// Three hue families plus two greys, so a marker's colour never competes with
// the green/red that carry gains and losses. Blue, purple and amber pass the
// dark-surface lightness, chroma and contrast checks; blue and purple are
// close for protan vision, so each family also has its own shape.
const FAMILIES = {
  company: { color: '#3987e5', shape: 'circle' },    // results and what analysts make of them
  news: { color: '#b06fd8', shape: 'diamond' },      // deals, people, regulators, capital
  outside: { color: '#c98500', shape: 'square' },    // macro and sector peers
  market: { color: '#a1a1aa', shape: 'circle' },     // a market-wide day, labelled without the LLM
  none: { color: '#71717a', shape: 'circle' },       // no catalyst found
}

const CATEGORY_FAMILY = {
  earnings: 'company',
  guidance: 'company',
  analyst: 'company',
  deal_product: 'news',
  management: 'news',
  regulatory_legal: 'news',
  capital_insider: 'news',
  macro: 'outside',
  sector_peer: 'outside',
  market: 'market',
  no_catalyst: 'none',
}

/** Stroke for a hollow marker: unexplained, pending or failed. */
export const HOLLOW_STROKE = '#a1a1aa'

/** { color, shape } for a category key; unknown keys draw as "no catalyst". */
export function categoryStyle(key) {
  return FAMILIES[CATEGORY_FAMILY[key] || 'none']
}

/** Hollow markers for anything not yet explained. */
export function isHollow(event) {
  return event?.status !== 'explained'
}

/** The category filter bucket an event falls in. */
export function filterKeyOf(event) {
  return event?.status === 'explained' && event?.category ? String(event.category) : UNEXPLAINED_KEY
}

function humanize(key) {
  const s = String(key || '').replace(/_/g, ' ').trim()
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : 'Other'
}

/** A copy sorted newest first by `date` (YYYY-MM-DD sorts as text). */
export function sortNewestFirst(events) {
  return [...(events || [])].sort((a, b) => String(b?.date || '').localeCompare(String(a?.date || '')))
}

/**
 * Filter chips with counts: All, then each category that has events (in the
 * API's `categories` order, then any the list missed), then "Not explained".
 */
export function categoryChips(events, categories) {
  const list = events || []
  const counts = new Map()
  for (const e of list) {
    const k = filterKeyOf(e)
    counts.set(k, (counts.get(k) || 0) + 1)
  }
  const chips = [{ key: ALL_KEY, label: 'All', count: list.length }]
  const seen = new Set([ALL_KEY, UNEXPLAINED_KEY])
  for (const c of categories || []) {
    const key = c?.key ? String(c.key) : ''
    if (!key || seen.has(key)) continue
    seen.add(key)
    const n = counts.get(key) || 0
    if (n) chips.push({ key, label: c.label || humanize(key), count: n })
  }
  for (const e of list) {
    const key = filterKeyOf(e)
    if (seen.has(key)) continue
    seen.add(key)
    chips.push({ key, label: e.category_label || humanize(key), count: counts.get(key) || 0 })
  }
  const unexplained = counts.get(UNEXPLAINED_KEY) || 0
  if (unexplained) chips.push({ key: UNEXPLAINED_KEY, label: 'Not explained', count: unexplained })
  return chips
}

/** Events in one filter bucket (or all), newest first. */
export function filterEvents(events, key) {
  const sorted = sortNewestFirst(events)
  if (!key || key === ALL_KEY) return sorted
  return sorted.filter(e => filterKeyOf(e) === key)
}

/**
 * Where each event sits on the price chart: { event, index, close }, with
 * `index` into the oldest-first `bars`. An event dated off the series lands
 * on the next bar; one outside the series is left off.
 */
export function eventMarkers(bars, events) {
  const series = bars || []
  if (!series.length) return []
  const first = String(series[0]?.date || '')
  const last = String(series[series.length - 1]?.date || '')
  const byDate = new Map(series.map((b, i) => [String(b?.date || ''), i]))
  const out = []
  for (const event of events || []) {
    const date = String(event?.date || '')
    if (!date) continue
    let index = byDate.get(date)
    if (index == null) {
      if (date < first || date > last) continue
      index = series.findIndex(b => String(b?.date || '') >= date)
      if (index < 0) continue
    }
    const close = num(event.close) ?? num(series[index]?.close)
    if (close == null) continue
    out.push({ event, index, close })
  }
  return out
}

/** Largest absolute daily move in the bars, at least 1 (the strip's scale). */
export function stripMax(bars) {
  let max = 0
  for (const b of bars || []) {
    const r = num(b?.ret_pct)
    if (r != null && Math.abs(r) > max) max = Math.abs(r)
  }
  return Math.max(1, max)
}

function num(v) {
  if (v == null || v === '' || typeof v === 'boolean') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

// ── The three-way split ──────────────────────────────────────────────────────

/**
 * Market, sector and stock-specific rows with `share` = |value| over the
 * largest |value| (0 to 1), for a bar beside each number.
 */
export function splitRows(src, sectorEtf = null) {
  const rows = [
    { key: 'market', label: 'Market', value: num(src?.market_pct) },
    { key: 'sector', label: sectorEtf ? `Sector (${sectorEtf})` : 'Sector', value: num(src?.sector_pct) },
    { key: 'specific', label: 'Stock-specific', value: num(src?.specific_pct) },
  ]
  const max = Math.max(0, ...rows.map(r => Math.abs(r.value ?? 0)))
  return rows.map(r => ({ ...r, share: max > 0 && r.value != null ? Math.abs(r.value) / max : 0 }))
}

// ── Words ────────────────────────────────────────────────────────────────────

/** A plain status line for an event that is not explained, else ''. */
export function statusText(event) {
  switch (event?.status) {
    case 'unexplained': return 'Not explained yet'
    case 'pending': return 'Explaining…'
    case 'failed': return 'Explanation failed'
    default: return ''
  }
}

export function confidenceText(confidence) {
  const c = String(confidence || '').toLowerCase()
  if (c === 'low' || c === 'medium' || c === 'high') return `${c.charAt(0).toUpperCase()}${c.slice(1)} confidence`
  return ''
}

/** Events the "Explain moves" action would queue: unexplained or failed. */
export function explainableCount(events) {
  return (events || []).filter(e => e?.status === 'unexplained' || e?.status === 'failed').length
}

/** `pending` from the payload, else counted from the events. */
export function pendingCount(payload) {
  const n = num(payload?.pending)
  if (n != null) return Math.max(0, n)
  return (payload?.events || []).filter(e => e?.status === 'pending').length
}

/** 'YYYY-MM-DD' → "Oct 2" (or "Fri, Oct 2, 2026" when long), with no timezone shift. */
export function fmtEventDate(ymd, { long = false } = {}) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(ymd || ''))
  if (!m) return String(ymd || '')
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])))
  return d.toLocaleDateString('en-US', long
    ? { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }
    : { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

/**
 * A cited article as a row: { title, url, meta }. `url` is kept only when it
 * is http(s), so a stored `javascript:` link can never become an href.
 */
export function sourceRow(source) {
  const raw = String(source?.url || '').trim()
  const url = /^https?:\/\//i.test(raw) ? raw : ''
  let host = ''
  if (url) { try { host = new URL(url).hostname.replace(/^www\./, '') } catch { /* keep blank */ } }
  const title = String(source?.title || '').trim() || host || 'Untitled article'
  const meta = [String(source?.source || '').trim() || host, fmtStamp(source?.published_at)].filter(Boolean).join(' · ')
  return { title, url, meta }
}

/** An ISO time → "Oct 2, 12:31 PM" in the viewer's timezone, '' when unreadable. */
export function fmtStamp(iso) {
  const t = Date.parse(String(iso || ''))
  if (!Number.isFinite(t)) return ''
  return new Date(t).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

/** A readable failure for a request: the server's `detail` when it sent one. */
export function apiFailure(status, body, fallback) {
  if (!status) return "Couldn't reach the server. Check your connection and try again"
  const d = body?.detail
  if (typeof d === 'string' && d.trim()) return d.trim()
  if (Array.isArray(d) && d.length) {
    const text = d.map(x => (x && (x.msg || x.message)) || '').filter(Boolean).join('; ')
    if (text) return text
  }
  if (status === 401) return 'Your session has expired. Sign in again'
  return `${fallback} (HTTP ${status})`
}

// ── Symbol picker ────────────────────────────────────────────────────────────

/** Equity holdings as quick picks, largest first, each symbol once. */
export function quickPicks(positions) {
  const seen = new Set()
  return [...(positions || [])]
    .filter(p => isTradableSymbol(p?.symbol))
    .sort((a, b) => (Number(b.marketValue) || 0) - (Number(a.marketValue) || 0))
    .map(p => normalizeSymbol(p.symbol))
    .filter(s => (seen.has(s) ? false : (seen.add(s), true)))
}

/** The range from a query value, else the default. */
export function rangeFrom(value) {
  const r = String(value || '').toUpperCase()
  return ANALYSIS_RANGES.includes(r) ? r : DEFAULT_RANGE
}

// ── Settings ─────────────────────────────────────────────────────────────────

function limitIssue(value, { min, max }) {
  if (value === '' || value == null) return 'Enter a number'
  const n = Number(value)
  if (!Number.isFinite(n)) return 'Enter a number'
  if (n < min || n > max) return `Between ${min.toFixed(1)} and ${max.toFixed(1)}`
  return ''
}

/** Per-field problems with a settings draft: { sigma, min_move_pct }, '' when fine. */
export function settingsIssues(draft) {
  return {
    sigma: limitIssue(draft?.sigma, SIGMA_LIMITS),
    min_move_pct: limitIssue(draft?.min_move_pct, MIN_MOVE_LIMITS),
  }
}

/** The PUT /analysis/settings body; an empty model choice is null ("None"). */
export function settingsPayload(draft) {
  const id = draft?.model_id
  return {
    model_id: id == null || id === '' ? null : String(id),
    sigma: Number(draft?.sigma),
    min_move_pct: Number(draft?.min_move_pct),
  }
}

/** A draft from the GET /analysis/settings payload. */
export function settingsDraft(payload) {
  return {
    model_id: payload?.model_id == null ? '' : String(payload.model_id),
    sigma: num(payload?.sigma) ?? 2.5,
    min_move_pct: num(payload?.min_move_pct) ?? 3,
  }
}

/** True when the draft differs from what was saved. */
export function settingsDirty(draft, saved) {
  if (!saved) return false
  const a = settingsPayload(draft)
  const b = settingsPayload(saved)
  return a.model_id !== b.model_id || a.sigma !== b.sigma || a.min_move_pct !== b.min_move_pct
}
