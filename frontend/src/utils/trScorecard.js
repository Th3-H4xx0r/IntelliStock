/**
 * Pure helpers for the TR scorecard section of the backtest detail page
 * ("vs S&P 500 by market type"). No Vue and no fetch, so the rules run under
 * `npm test` (node --test) without mounting the view.
 *
 * Shape: backend/trend_research/scorecard.py — `tr_scorecard` on the
 * backtest summary is absent (render nothing), `{error}` (render a note), or
 * `{overall, optimistic, honest, meta}`. Every number is already a percent
 * (`excess_pp` is percentage points a year) and may be null; verdicts are
 * true, false or "untested".
 */

export const TR_PERIODS = [
  { key: 'overall', label: 'Whole test' },
  { key: 'honest', label: 'Honest period (after AI training cutoff)' },
  { key: 'optimistic', label: 'Optimistic period (AI may remember)' },
]

const REGIME_LABELS = { bull: 'Bull', chop: 'Choppy', bear: 'Bear' }
const DASH = '—'

function finite(v) {
  return typeof v === 'number' && Number.isFinite(v)
}

/** "+12.3%" / "-4.0%" / "0.0%"; a dash when not a number. */
export function fmtSignedPct1(v) {
  if (!finite(v)) return DASH
  return `${v > 0 ? '+' : ''}${v.toFixed(1)}%`
}

/** "-12.3%" (no forced sign: drawdowns and percentiles). */
export function fmtPct1(v) {
  return finite(v) ? `${v.toFixed(1)}%` : DASH
}

/** Percentage points: "+4.8 pts". */
export function fmtPts1(v) {
  if (!finite(v)) return DASH
  return `${v > 0 ? '+' : ''}${v.toFixed(1)} pts`
}

/** A pass flag: true, false, or anything else ("untested", null). */
export function verdictLabel(v) {
  if (v === true) return { label: 'Pass', tone: 'pos' }
  if (v === false) return { label: 'Fail', tone: 'neg' }
  return { label: 'Not tested yet', tone: 'muted' }
}

export function regimeLabel(kind) {
  return REGIME_LABELS[kind] ?? String(kind ?? DASH)
}

function toneOf(v) {
  if (!finite(v) || v === 0) return 'muted'
  return v > 0 ? 'pos' : 'neg'
}

function dateRange(start, end) {
  return start && end ? `${start} – ${end}` : null
}

function windowRow(w) {
  const beat = w?.beat === true ? 'Yes' : (w?.beat === false ? 'No' : DASH)
  return {
    key: `${w?.tag ?? ''}|${w?.start ?? ''}`,
    kind: regimeLabel(w?.kind),
    dates: dateRange(w?.start, w?.end) ?? DASH,
    strategy: fmtSignedPct1(w?.ret),
    spy: fmtSignedPct1(w?.spy_ret),
    beat,
    tone: w?.beat === true ? 'pos' : (w?.beat === false ? 'neg' : 'muted'),
  }
}

function periodView({ key, label }, p) {
  const seg = p && typeof p === 'object' ? p : null
  const untested = !seg || seg.untested === true
  const random = []
  if (!untested && finite(seg.random_pctile_candidates)) {
    random.push(`Better than ${fmtPct1(seg.random_pctile_candidates)} of random picks from the AI's own shortlist`)
  }
  if (!untested && finite(seg.random_pctile_universe)) {
    random.push(`Better than ${fmtPct1(seg.random_pctile_universe)} of random picks from all names it considered`)
  }
  return {
    key,
    label,
    untested,
    dates: untested ? null : dateRange(seg.start, seg.end),
    cells: {
      cagr: fmtSignedPct1(seg?.cagr),
      spyCagr: fmtSignedPct1(seg?.spy_cagr),
      diff: fmtPts1(seg?.excess_pp),
      maxDd: fmtPct1(seg?.max_dd),
      spyMaxDd: fmtPct1(seg?.spy_max_dd),
    },
    diffTone: toneOf(seg?.excess_pp),
    allRegimes: verdictLabel(seg?.pass_all_regimes),
    plus5: verdictLabel(seg?.pass_plus5),
    windows: untested || !Array.isArray(seg.windows) ? [] : seg.windows.map(windowRow),
    random,
  }
}

/**
 * The section's view model: null (render nothing), `{error}` (one note), or
 * `{periods, notes, cutoff}` with every figure already formatted.
 */
export function trScorecardView(card) {
  if (!card || typeof card !== 'object' || Array.isArray(card)) return null
  if (card.error) return { error: `Scorecard unavailable: ${card.error}` }
  const meta = card.meta && typeof card.meta === 'object' ? card.meta : {}
  return {
    periods: TR_PERIODS.map(p => periodView(p, card[p.key])),
    notes: Array.isArray(meta.notes) ? meta.notes.map(String) : [],
    cutoff: meta.cutoff ?? null,
  }
}
