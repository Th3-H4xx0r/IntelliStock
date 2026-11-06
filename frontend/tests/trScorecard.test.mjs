import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  TR_PERIODS,
  fmtSignedPct1,
  fmtPct1,
  fmtPts1,
  verdictLabel,
  regimeLabel,
  trScorecardView,
} from '../src/utils/trScorecard.js'

const PERIOD = {
  untested: false, start: '2023-01-03', end: '2024-12-31', sessions: 502,
  ret: 41.23456, spy_ret: 30.1, cagr: 18.84, spy_cagr: 14.05, excess_pp: 4.79,
  max_dd: -12.345, spy_max_dd: -9.9, roll12_beat_pct: 62.5,
  windows: [
    { tag: 'ru1', kind: 'bull', start: '2023-01-01', end: '2023-07-31', source: 'battery',
      ret: 22.25, spy_ret: 18.0, beat: true },
    { tag: 'nc1', kind: 'chop', start: '2023-02-01', end: '2023-06-15', source: 'battery',
      ret: -1.0, spy_ret: 2.0, beat: false },
    { tag: 'rule-1', kind: 'bear', start: '2024-04-01', end: '2024-05-01', source: 'rule',
      ret: null, spy_ret: -3.0, beat: null },
  ],
  random_pctile_candidates: 71.25, random_pctile_universe: 88.0,
  pass_all_regimes: false, pass_plus5: false, pass: false,
}
const UNTESTED = {
  untested: true, start: null, end: null, sessions: 0, ret: null, spy_ret: null,
  cagr: null, spy_cagr: null, excess_pp: null, max_dd: null, spy_max_dd: null,
  roll12_beat_pct: null, windows: [], random_pctile_candidates: null,
  random_pctile_universe: null, pass_all_regimes: 'untested', pass_plus5: 'untested',
  pass: 'untested',
}
const CARD = {
  overall: PERIOD,
  optimistic: { ...PERIOD, pass_all_regimes: true, pass_plus5: true },
  honest: UNTESTED,
  meta: { cutoff: null, notes: ['Engine NAV is price-only.', 'Survivorship: delisted names.'] },
}

test('the three periods in the order the page shows them', () => {
  assert.deepEqual(TR_PERIODS.map(p => p.key), ['overall', 'honest', 'optimistic'])
  assert.deepEqual(TR_PERIODS.map(p => p.label), [
    'Whole test',
    'Honest period (after AI training cutoff)',
    'Optimistic period (AI may remember)',
  ])
})

test('percentages carry one decimal; null and non-finite read as a dash', () => {
  assert.equal(fmtSignedPct1(18.84), '+18.8%')
  assert.equal(fmtSignedPct1(-1.06), '-1.1%')
  assert.equal(fmtSignedPct1(0), '0.0%')
  assert.equal(fmtPct1(-12.345), '-12.3%')
  assert.equal(fmtPct1(71.25), '71.3%')
  assert.equal(fmtPts1(4.79), '+4.8 pts')
  assert.equal(fmtPts1(-2), '-2.0 pts')
  for (const v of [null, undefined, NaN, Infinity, 'x']) {
    assert.equal(fmtSignedPct1(v), '—')
    assert.equal(fmtPct1(v), '—')
    assert.equal(fmtPts1(v), '—')
  }
})

test('verdicts: true is Pass, false is Fail, anything else is Not tested yet', () => {
  assert.deepEqual(verdictLabel(true), { label: 'Pass', tone: 'pos' })
  assert.deepEqual(verdictLabel(false), { label: 'Fail', tone: 'neg' })
  assert.deepEqual(verdictLabel('untested'), { label: 'Not tested yet', tone: 'muted' })
  assert.deepEqual(verdictLabel(null), { label: 'Not tested yet', tone: 'muted' })
})

test('regime kinds read as plain market types', () => {
  assert.equal(regimeLabel('bull'), 'Bull')
  assert.equal(regimeLabel('chop'), 'Choppy')
  assert.equal(regimeLabel('bear'), 'Bear')
  assert.equal(regimeLabel('other'), 'other')
})

test('no scorecard renders nothing', () => {
  assert.equal(trScorecardView(undefined), null)
  assert.equal(trScorecardView(null), null)
  assert.equal(trScorecardView('nope'), null)
  assert.equal(trScorecardView([]), null)
})

test('a scorecard error renders the note only', () => {
  assert.deepEqual(trScorecardView({ error: 'ValueError: no bars' }),
    { error: 'Scorecard unavailable: ValueError: no bars' })
})

test('a normal scorecard: rows, verdicts, windows, random baselines and notes', () => {
  const view = trScorecardView(CARD)
  assert.equal(view.error, undefined)
  assert.deepEqual(view.periods.map(p => p.label), TR_PERIODS.map(p => p.label))

  const overall = view.periods[0]
  assert.equal(overall.untested, false)
  assert.equal(overall.dates, '2023-01-03 – 2024-12-31')
  assert.deepEqual(overall.cells, {
    cagr: '+18.8%', spyCagr: '+14.1%', diff: '+4.8 pts', maxDd: '-12.3%', spyMaxDd: '-9.9%',
  })
  assert.equal(overall.diffTone, 'pos')
  assert.deepEqual(overall.allRegimes, { label: 'Fail', tone: 'neg' })
  assert.deepEqual(overall.plus5, { label: 'Fail', tone: 'neg' })
  assert.deepEqual(overall.windows, [
    { key: 'ru1|2023-01-01', kind: 'Bull', dates: '2023-01-01 – 2023-07-31',
      strategy: '+22.3%', spy: '+18.0%', beat: 'Yes', tone: 'pos' },
    { key: 'nc1|2023-02-01', kind: 'Choppy', dates: '2023-02-01 – 2023-06-15',
      strategy: '-1.0%', spy: '+2.0%', beat: 'No', tone: 'neg' },
    { key: 'rule-1|2024-04-01', kind: 'Bear', dates: '2024-04-01 – 2024-05-01',
      strategy: '—', spy: '-3.0%', beat: '—', tone: 'muted' },
  ])
  assert.deepEqual(overall.random, [
    "Better than 71.3% of random picks from the AI's own shortlist",
    'Better than 88.0% of random picks from all names it considered',
  ])

  assert.deepEqual(view.periods[2].allRegimes, { label: 'Pass', tone: 'pos' })
  assert.deepEqual(view.periods[2].plus5, { label: 'Pass', tone: 'pos' })
  assert.deepEqual(view.notes, ['Engine NAV is price-only.', 'Survivorship: delisted names.'])
  assert.equal(view.cutoff, null)
})

test('the honest period untested: dashes and Not tested yet, no windows or baselines', () => {
  const honest = trScorecardView(CARD).periods[1]
  assert.equal(honest.label, 'Honest period (after AI training cutoff)')
  assert.equal(honest.untested, true)
  assert.equal(honest.dates, null)
  assert.deepEqual(honest.cells, { cagr: '—', spyCagr: '—', diff: '—', maxDd: '—', spyMaxDd: '—' })
  assert.equal(honest.diffTone, 'muted')
  assert.deepEqual(honest.allRegimes, { label: 'Not tested yet', tone: 'muted' })
  assert.deepEqual(honest.plus5, { label: 'Not tested yet', tone: 'muted' })
  assert.deepEqual(honest.windows, [])
  assert.deepEqual(honest.random, [])
})

test('a period missing from the card reads as untested', () => {
  const view = trScorecardView({ overall: PERIOD, meta: { cutoff: '2025-01-01' } })
  assert.equal(view.periods[1].untested, true)
  assert.equal(view.periods[2].untested, true)
  assert.deepEqual(view.periods[2].plus5, { label: 'Not tested yet', tone: 'muted' })
  assert.equal(view.cutoff, '2025-01-01')
  assert.deepEqual(view.notes, [])
})

test('the TR scorecard view keys repeated text by index, not by the text', async () => {
  // Two identical notes or random lines share a text: keyed by the text, Vue
  // warns about duplicate keys and patches the wrong node.
  const { readFile } = await import('node:fs/promises')
  const vue = await readFile(new URL('../src/views/BacktestDetailView.vue', import.meta.url), 'utf8')
  assert.ok(!vue.includes(':key="note"'))
  assert.ok(!vue.includes(':key="line"'))
  assert.ok(!vue.includes(':key="w.key"'))
  assert.ok(vue.includes('v-for="(note, i) in trScorecard.notes" :key="`n-${i}`"'))
  assert.ok(vue.includes('v-for="(line, i) in p.random" :key="`r-${i}`"'))
})
