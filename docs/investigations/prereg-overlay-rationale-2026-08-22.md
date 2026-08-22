# Preregistration: overlay rationale field (WARM pair, window d)

Date: 2026-08-22. Registered BEFORE the runs.

## Motivation
The 2026-08-22 overlay slim (dropping fb/fp/ra output fields) coincided with the window-d warm
baseline moving +3.09% → +0.58% on one draw, and the book shifting from 4 metals ETFs toward
single names (AXTI/CODX/IMNM/RIVN). Requiring a model to justify its call can act as implicit
chain-of-thought — the cost cut may have been paid in decision quality. fb/fp carry no plausible
reasoning value and stay dropped; ra is the testable half.

## Lever (config-only, code shipped this commit)
`overlay_request_rationale: absent(False) → true` (treatment arm only), doc 195, window d,
$6,000, 3600s, both arms from the proven warm snapshot
(/tmp/_pair_warm_v2-conv-trt_2026-04-01.state.json) via --snapshot. Control = slim prompt
(current default). Cost of the lever if adopted: ~$0.10-0.15/fresh run of output tokens.

## Endpoints
1. PRIMARY (mechanism): arms DIVERGE (overlap < 100%) — proving the rationale request changes
   decisions at all. If the books stay byte-identical, ra is decision-neutral: keep it OFF and
   bank the savings, question closed.
2. SECONDARY: return delta vs the warm floor (0.0pp measured). Given ONE prior draw suggested
   ~+2.5pp for the ra-era prompt, treatment better by > +1pp = evidence ra is load-bearing →
   ADOPT (set on doc 195). Treatment worse or within ±1pp = keep OFF.
3. GUARDS: overlay non-response/timeouts not elevated in treatment (runaway reasoning);
   overlay_llm_max_output_tokens cap unchanged (2500).

## Decision rule
Adopt ra iff arms diverge AND treatment > control by +1pp AND no guard breach. Explicitly NOT
adoptable on mechanism alone — divergence without improvement means the old number was draw
luck, not reasoning value.

## Result window d (bt 138148 control +1.35% / bt 608532 treatment +2.81%)
PRIMARY FIRED: 25% overlap — the rationale request rewires decisions wholesale (implicit
chain-of-thought confirmed as a real mechanism, not telemetry). SECONDARY: +1.46pp nominal but
**VOID by the overlap rule** — adopting on a VOID delta is the documented anti-pattern, even
though the preregistered rule's letter was met. Evidence state: three same-direction draws
(ra-era +3.09 / slim +0.58 and +1.35 / ra-restored +2.81). Cache note: the control re-paid full
price ($2.06) because f68af81's own prompt changes re-keyed the cache AND changed decisions
(+0.58 → +1.35) — at temp 0.2, every prompt edit is a strategy change and must be A/B'd as one.

DECISION DEFERRED to the window-c cold pair (readable regime) — running.

## Result window c (cold pair)
_pending_
