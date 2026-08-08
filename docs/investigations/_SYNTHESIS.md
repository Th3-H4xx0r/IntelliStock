# Synthesis — 2026-08-08 parallel investigation (5 agents, all evidence-backed)

## THE META-FINDING (inv-regress, corroborated by inv-entry + inv-discovery)
The lever ladder 820236 -> 718249 -> 613166 -> 725146 MEASURES NOTHING.
  820236 n 718249 held-name overlap = 0/18.  Noise floor >= 4.94pp > any lever effect.
  The four runs straddle 3 commits, 3 discovery universes, 2 price-history depths,
  4 event states, and one truncated run. 725146 is NOT negative (+0.11%, stopped 79.65%).

## ROOT CAUSES, ranked by evidence x dollars

1. CASH RACE (inv-capital, inv-exits independently)
   Core submits the funding SPY sell on the SAME tick as the buy; backtest execution is
   next-event, so the buy gate reads PRE-SELL cash. $14,084 of $14,801 refused (95.2%).
   Canonical: need $703.22, released $703.18 -- sized to the cent, only the clock was wrong.
   PLUS: in-flight SPY deploy holds a reservation the gate cannot see ->
   $2,302.79 approved -> $408.00 filled (17.7%) across 4 exact reconstructions.

2. MAX_POSITIONS PLUMBING MISMATCH (inv-entry)
   Z4.1 lifts the cap 6->8 (chop) / 6->14 (bull) 164 times; the broker reads the STATIC
   cfg["max_positions"]=6 (nexus_broker_utils.py:148-152). 2,409 arm lines, ZERO read 8/14.
   Book sat at held=6,cap=6 on 94.5% of 634 bars. 45 buys / $41,453 in 820236 alone.

3. POISONED SHARED PRICE-BAR CACHE (inv-regress)
   Bear run 342380 ran between 820236 and 718249 on the SAME history_scope_id and rewrote
   183 cache rows with a narrow fetch_start. 718249/613166/725146 then had 54 closes, so
   the 60d return returned a FABRICATED 0.0 for 12-14 of 15 bar-1 names (gna:13899).
   WDC (+$450 = 61% of 820236's P&L) was discovered ONLY via the 60d gate.
   Fix key exists and is OFF/absent: overlay_bars_min_history_bars.

4. DISCOVERY SORTS ON THE WRONG KEY (inv-discovery)
   IC(60d)=+0.201 p=0.0001 | IC(20d)=-0.127 | IC(max(20d,60d))=-0.003  <- max() IS the key
   (gna:13920). 60d-only wins 6/6 at K=12/20/30; top-12 +19.02%->+24.19%, -0.16%->+6.44%.

5. ROTATION SELLS WITHOUT BUYING (inv-exits) -- 5 for 5
   Every momentum rotation/swap sold the position and failed to buy the replacement.
   $4,277 of alpha exposure out, $87 back. 820236 has ZERO rotations and is the best run.

6. MY OWN LEVERS: two inert, one harmful
   rank_band_momentum_exempt_min_score  -> NEVER FIRED (0 in all 6 logs). Reader gna:23016,
       writer of momentum_watchlist_score gna:28622 -- ~1,000 lines LATER.
   turnover_budget_conviction_bypass_max_pct=0.8 -> HARMFUL. Refused SNDK at raw=1.700/88%.
       820236 admitted SNDK 4x through a 105% budget for +$100.95. Bought ZERO churn
       reduction (gross $19,946 -> $21,182; BINDING 263 -> 605).
   satellite_conviction_reserve_pct=0.15 -> do not judge on 725146; do not re-run until the
       cash race is fixed (it moves plain buys aside but the release still arrives a bar late).

## DO NOT TOUCH (inv-exits, measured)
   Exits are NOT the leak. Capture vs ACTUAL entry = 99.99%. Only 2 non-sleeve sells in
   820236, both losers, and they MADE +$303.64 vs holding.
   trailing_stop suppressed 331x -- re-arming it would have exited ALL FIVE big winners.
   The -10% circuit breaker is profitable. Leave the exit stack alone.

## THE PRIZE (inv-entry counterfactual, perfect hindsight = upper bound)
   4 conviction slots at first-refusal prices (SNDK+AAOI+VICR+WDC, 59% of NAV) = +$3,705,
   i.e. 820236 goes +12.33% -> ~+65%. The sizing engine ALREADY wants 14.5%/name; 93% of
   sized conviction notional ($265,932 -> $11,103) never converts.
