# Preregistration: bear-leg fresh-20d-low filter (cold pair, window c)

Date: 2026-08-21. Registered BEFORE the runs. Motivated by the Opus path-analysis of
bt 712452/808739: the −7.4% drawdown (02-10→03-19) ran its entire 27-session course with the
hedge OFF-BOOK, because `residual_sleeve_bear_block_at_fresh_low_bars=2` refuses to open a
hedge while the proxy is within 2 bars of a fresh 20d low — and in a sustained decline the
proxy makes a new 20d low every 1-2 sessions. The filter blocked deploy on 03-14/16/17/18/20/
21/23/24, the hedge bought 03-25 (4 sessions AFTER the trough), and its +$433 landed entirely
in the recovery. The same filter then blocked re-entry on 04-01.

## Hypothesis
The filter is structurally self-blocking in exactly the regime the hedge exists for. Disabling
it moves the SQQQ deploy from 03-25 to ≈03-13 (bear declaration + dwell=2 satisfied), putting
the hedge on the book during the declining leg it is designed to monetize.

## Known risk (the filter's original purpose)
Opening a short-proxy at the bottom of a range: if the tape V-bounces immediately after a fresh
low, an early hedge loses. The 10% leg stop and 10%/5% trail bound that loss. The A/B measures
whether the protection is worth the 8 sessions of hedge it costs in a real bear.

## Lever
`residual_sleeve_bear_block_at_fresh_low_bars: 2 → 0` (treatment arm only), doc 195, window c
(2026-02-01..2026-04-01), $6,000, 3600s, standard cold protocol via run_paired_experiment.py.
Restore to 2 after the pair regardless of outcome.

## Endpoints (primary decides)
1. **PRIMARY (mechanism):** SQQQ deploy date moves earlier by ≥5 sessions (control replication
   ≈03-25 → treatment ≤03-18), and the hedge is ON BOOK for ≥5 sessions of the decline phase
   (peak→trough window as measured in the control).
2. **SECONDARY:** (a) max peak-to-trough drawdown shallower in treatment by ≥1pp;
   (b) return delta vs the 0.5pp cold floor.
3. **GUARDS (any breach = not adopted):**
   - SQQQ leg net P&L not worse than control by >20% (early entry must not turn the hedge into
     a loser via stop-outs — count leg stop-out events).
   - No hedge whipsaw churn: ≤2 SQQQ round trips in the window.
   - Long-book behavior unchanged (same trims; the lever touches only the sleeve deploy gate).
4. NOTE: window c overlap was 78% cold-READABLE for the max_positions pair; if this pair goes
   VOID on overlap, fall back to mechanism endpoints only (deploy dates are deterministic).

## Decision rule
Adopt iff PRIMARY fires, no guard breached, and drawdown endpoint (2a) met. A positive return
delta with the hedge still late = reject (mechanism failed). An earlier hedge that stops out
repeatedly = reject (the filter is load-bearing; document the measured cost of keeping it).

## Result (bt 621886 control +6.70% / bt 362992 treatment +4.48%; 3-agent parallel sweep)
**NOT ADOPTED — filter stays at 2 — and the pair is VOID for outcome inference.** The three
independent analyses (mechanism autopsy, path/delta attribution, adversarial refuter) agree:

- **PRIMARY (mechanism) FIRED:** filter OFF deploys the hedge **7 sessions earlier** (03-16 vs
  03-25; a separate `bear_confirm_days=2` gate costs 2 more). The early hedge's closed round
  trip was **+$100.24 better** than the control's, with a better basis (74.71 vs 76.10).
- **Secondary 2a FAILED, inverted:** treatment max DD **−7.30% vs control −6.21%** — the early
  hedge deepened and extended the trough (bought into the 03-16..18 bounce; below control every
  day 03-10..03-18). The "earlier hedge shallows the dip" prediction is absent from this pair.
- **The −2.22pp headline is NOT the lever.** 128% of it is one selection fork on 03-09 (control
  bought ADEA +$105.59; treatment bought ASO −$64.39 in the same slot at the same tick), seeded
  by discovery-candidate divergence starting 02-11 — five weeks before the filter's first
  action. The SQQQ leg itself was +$32.90 (net) / +$100 (through 03-31) in the TREATMENT's
  favor. The delta and the lever point in opposite directions.
- **The filter's design case fired exactly once and won:** 04-01, leg flat post-trail-exit, the
  filter blocked re-entry at a fresh 20d low; the unfiltered arm re-opened $4,069 @ 78.88 and
  marked 77.57 same day (−$67). Purpose vindicated, once, worth ≈ the early-deploy gain.
- **Real noise floor for a 78%-overlap pair ≈ 2.8pp per divergent name slot** (measured: the
  ADEA/ASO slot alone = 2.83pp; shared-name P&L differences = $0.00 to the cent). 2.22pp is
  unreadable. Corroborated by same-config cross-time dispersion: +2.97% (morning bt 712452) vs
  +6.70% (tonight) = 3.73pp.

**Two new tickets from the sweep:**
1. **Bear-refill hedge leak:** `residual_bear_refill` trims SQQQ into strength to restore cash
   targets and re-buys higher — treatment sold 31.5 sh @ avg 75.94, re-bought 26.6 sh @ avg
   76.53, and arrived at the payoff move with a **7.7% smaller hedge** despite 7 extra sessions
   of exposure. Independent of this filter; live-money leak candidate.
2. **The news/LLM corpus is an unfrozen input.** Arms 1h apart diverge in the discovery layer
   even from attested-cold starts (universe counts differ from 02-04, candidates from 02-11).
   Cold-clearing pins instance state, not the corpus. This is the deepest remaining
   nondeterminism; the warm-protocol A/A in flight doubles as its measurement.

**Disposition:** filter stays 2 (default retained on its one vindicated fire + unreadable
outcome). Re-test only under a frozen corpus (warm protocol at minimum), aggregated over ≥5
pairs, including a window where the proxy makes a fresh low and BOUNCES (the design case).
