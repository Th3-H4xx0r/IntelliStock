# SDD ledger — plan: docs/superpowers/plans/2026-07-27-production-live-readiness-remediation.md

Branch: codex/production-live-readiness
Merge base: 69ac17f
Baseline: full collection blocked by test_breadth_scan.py module pollution.
Baseline excluding that file: 2943 passed, 17 failed, 13 skipped.
Environment: Python 3.14 cannot install pinned TensorFlow; frontend npm audit reports 1 high and 1 low advisory.
Task 1: minor (deferred): migration backup can remain after a post-write mode-check failure.
Task 1: fix round 1/5 (2 addressed, 1 open — kill-switch error returned raw brokerage row identifier; commits ccd7cee..88cd3cf)
Task 1: fix round 2/5 (1 addressed, 0 open; commits 88cd3cf..f249be4)
Task 1: complete (commits 69ac17f..f249be4, scoped review clean)
Task 2: review needs fixes (3 Critical, 2 Important; commit 1a8782a)
Task 2: fix round 1/5 (1 addressed, 4 open; 3 Critical and 5 Important in re-review; commits 1a8782a..a6f7ae2)
Task 2: fix round 2/5 (origin/redirect, Docker terminal-state, and reserved-identity subfindings addressed; 2 Critical and 7 Important remain; commits a6f7ae2..2839b0c)
Task 2: fix round 3/5 (round-2 findings addressed; re-review found 2 Critical and 4 Important issues; commits 2839b0c..7d14f02)
Task 2: fix round 4/5 in progress (fresh implementer; authoritative preflight/stop truth, paper-vs-live classification, relink identity, single role token, truncation detection)
Task 2: scope correction — equities/Alpaca stock readiness only; no new Kalshi or crypto behavior changes.
Task 2: fix round 4/5 implemented locally after agent usage-limit failure; 64 focused + 24 affected API/crash tests pass.
Task 1: reopened for interrupted-agent residual Alpaca secret inventory; Robinhood work discarded by user scope and deferred to final removal task.
Task 2: complete (commits f249be4..b25e2f1, review findings resolved and focused verification green)
Task 1: complete after Alpaca residual sweep (commit 55b572f, 148 focused tests and compilation green)
Task 3: implementation committed at a5544c4; 61 affected tests and compilation green; awaiting independent task review.
Tasks 4-6: independent implementations in progress in isolated worktrees.
Task 14: user-directed final task — remove Robinhood only after Tasks 1-13; re-verify Alpaca and untouched Kalshi/crypto; deploy inactive artifacts without starting any instance.
Task 3: review needs fixes (1 Critical — production same-event execution still immediate; 1 Important — non-equity compatibility not explicit/preserved; no minor findings).
Task 4: implementation commit a381ec5; independent review in progress.
Task 5: implementation commit bdb37f6; independent review in progress.
Task 6: implementation commit bbfe04d; independent review in progress.
Task 3: fix round 1/5 (Important non-equity compatibility addressed; Critical next-event production wiring open pending independently reviewed Task 5 integration; commit a5544c4..6ac37bb).
Task 6: review needs fixes (2 Critical — production attempts bypass preregistration/result identity, timestamp normalization masks valuation-time mismatch; 2 Important — benchmark leaks into default/Kalshi paths, manifest lacks content identity; 1 Minor — large integer canonicalization collision).
Task 4: review needs fixes (1 Critical — production backtests bypass PIT enforcement; 4 Important — current fundamentals, unscoped graph cache, Benzinga availability, and after-hours aggregation can leak future/current data).
Task 5: review needs fixes (3 Critical — stale bars relabeled as events, non-atomic fill/accounting commit, broker submission treated as confirmed fill; 3 Important and 1 Minor).
Tasks 3-7: integrated and verified together; 477 compatibility tests passed, including Kalshi and crypto regressions.
Task 8: complete locally; durable Alpaca lifecycle, ordered normalized event dispatch, confirmed-fill-only accounting, and persistent CAS backend implemented. 90 focused tests passed.
Task 8: committed at cf8e0b0.
Task 9: complete locally; broker-first startup plus continuous reconciliation, deferred ownership, manual/external quarantine, and fail-closed snapshot consistency implemented. 96 focused tests passed.
Task 9: committed at 99127c3.
Task 10: complete locally; authoritative typed marks, dependency evidence timestamps, skew/age gates, read-only Alpaca mark stream, and fail-closed calendar behavior implemented. 129 focused tests passed.
