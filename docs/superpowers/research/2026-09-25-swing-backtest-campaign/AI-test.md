# Swing AI-gate test: gpt-5.4-mini score ≥70 vs no gate (2026-08-03 → 2026-09-18)

Instance swing-paper (doc 204), daily bars, $100k. AI_OFF = backtest 243642; AI_ON_70 = backtest 175059.
Logs: `swing_bt/logs/{243642,175059}.log`. Replay code and CSVs: `swing_bt/aitest/` (`replay.py`, `stats.py`,
`scored_replay.csv`, `passer_replay.csv`). This was read-only: no code, config or database row was changed.

## 1. Headline

SPY total return (yfinance auto_adjust) on the engine clock, from the 07-31 close to the 09-17 close, was **+2.08%**.
From the 07-31 close to the 09-18 close it was +2.22%.

| run | return | vs SPY-TR | buys / sells | closed wins | avg names held | model calls | wall time |
|---|---|---|---|---|---|---|---|
| AI_OFF 243642 | **−1.58%** | −3.7 pp | 19 / 11 | 3 of 11 | 7.21 of 8 | 0 | 243 s |
| AI_ON_70 175059 | **−0.69%** | −2.8 pp | 18 / 10 | 4 of 10 | 6.26 of 8 | 60 (59 scored + 1 bad-JSON fail, TT) | 627 s |

Both runs lost money while SPY rose. The gate did not change that. It only narrowed the gap by 0.9 pp.

**Closed trades, AI_OFF.** Wins:
- COHR +14.9% (target)
- BBY +7.7%
- ADM +7.8%

Losses:
- COHR −0.2% (a gap-through re-entry)
- Stops: MGM −6.5%, CARR −6.1%, DAL −7.7%, ADI −6.4%, KLAC −8.5%, ESS −6.1%, LNT −5.6%

Open at the end: ABBV, ADP, BALL, CB, KO, LIN (exit pending), PWR, QQQ.

**Closed trades, AI_ON.** Wins:
- COHR +14.9%
- VRTX +10.0%
- TRGP +11.6%
- ADM +7.8% (at half size)

Losses:
- CAT −0.2% (gapped through the target at the entry open)
- Stops: CAT −5.8%, MGM −6.5%, UAL −7.6%, ADI −6.4%, ROST −6.6%

Open at the end: ABBV, ED, FAST, GRMN, KO, LIN (exit pending), VMRK, WRB.

**Score distribution (n=59).**
- Range 56–82, median 66, mean 68.3.
- **No score fell below 50**, so the "rejected" bucket is empty. The model never rejects; it only sorts candidates into 19 auto-enters and 40 band skips.
- The scores are bimodal:

| score | 56–59 | 61–64 | 66–68 | 70 | 76–79 | 80–82 |
|---|---|---|---|---|---|---|
| count | 7 | 17 | 16 | 1 | 12 | 6 |

- Scores for the same name are unstable from day to day:
  - ED: 58, 62, then 80 within 6 sessions. It lost −5.2%, −4.5% and −4.9%.
  - UAL: 79, then 61. ROST: 78, then 66. COHR: 78, then 67. VMRK: 66, then 78.
- The score also drives size. ADM (70) and SW (77) were entered at a 0.5x size adjustment.

**Not every technical passer was scored.** Scoring happens only after the slot and sector checks. The funnel passed 266 candidates across 34 sessions. The union of both runs holds 235 distinct (date, name) passers, and 176 of them were never scored.

## 2. The meaningful test: replay every scored candidate

**Method.** Each candidate was replayed on yfinance daily bars (raw OHLC):
- **Engine clock.** A session dated D decides on the close of the last bar before D and fills at D's open. I verified this against the logs: COHR's "08-03 close 263.00" is the yfinance 07-31 close of 262.89.
- **Bracket.** Stop −6% and target +9% on the signal close. If a bar spans both, the stop is taken first. A gap through either level fills at the open, including on the entry day.
- **Exits at the next open.** RSI(14) crossing 70 (using the lane's own `_rsi`), or the close-based ±9/−6% versus entry (`signals.exit_signal`).
- **Horizon.** Open positions are marked at the 2026-09-24 close.
- **Returns are gross.** The engine's ~46 bps round trip hits both buckets equally.

| bucket | n | mean | 95% bootstrap CI | median | win rate | stopped out |
|---|---|---|---|---|---|---|
| approved ≥70 | 19 | **−0.44%** | [−3.28, +2.63] | −4.35% | 37% | 42% |
| band 50–69 | 40 | **−1.70%** | [−3.20, −0.11] | −4.71% | 35% | 45% |
| rejected <50 | 0 | n/a | n/a | n/a | n/a | n/a |
| unscored passers (reference) | 176 | −2.22% | [−3.02, −1.40] | n/a | n/a | n/a |

**Approved minus band: +1.26 pp.**
- 95% bootstrap CI [−2.08, +4.67].
- Permutation p = 0.43; Mann–Whitney p = 0.80.
- Clustering the bootstrap by signal date gives [−1.32, +4.82].
- As excess over SPY for the same holding period: +1.40 pp, CI [−1.69, +4.69], p = 0.35.

**Spearman ρ(score, replayed return) = +0.04** (p = 0.74, bootstrap CI [−0.22, +0.30]). Against the SPY excess, ρ = +0.10 (p = 0.46).

**Not monotonic.** Mean return by score:
- 50–64 (n=24): −2.47%
- 65–69 (n=16): −0.54%
- 70–77 (n=4): −1.17%
- 78–82 (n=15): −0.24%

**Split in two, the "edge" comes only from the first half:**
- Aug 3–21: approved −1.07% vs band −2.95%. The band included six utilities and REITs that bled.
- Aug 24–Sep 18: approved +0.64% vs band +0.19%, essentially equal.

**Plainly: the gap is not statistically meaningful.** The difference is 1.3 pp, per-trade SD is 5.7%, and there are 19 vs 40 trades. The CI is about four times wider than the effect and centred near it. At this effect size, 80% power needs about 320 candidates per bucket, and a 1 pp gap needs about 500.

**Fidelity caveat.** The replay matched the engine's executed trades on direction and size, except for one:
- The replay and engine agree on COHR +12/+15%, VRTX +11/+10%, TRGP +11/+12%, and the ADI, MGM, LIN, UAL and ROST stops.
- **ADM is the exception.** It was +7.8% in the engine and −6.9% in the replay.
- The cause is the open. yfinance opens ADM at 81.85, the engine filled at 81.38, and the 08-07 close of 76.59 sits between the two −6% thresholds (76.94 vs 76.50).
- A 0.6% open difference flipping a trade by 15 pp shows how noisy single trades are.
- If ADM had counted as +7.6%, the approved mean would rise to about +0.33%. The conclusion does not change.

## 3. Why AI_ON ended $891 better

It was not fewer trades: 18 vs 19 buys. The difference decomposes by per-symbol P&L from the broker summary:

| component | $ | detail |
|---|---|---|
| names only AI_OFF held | **−2,200** | CARR −764, LNT −704, ESS −785, KLAC −1,082, CB −120, ADP −185, DAL −66, BALL −20; BBY +887, QQQ +486, PWR +153 |
| names only AI_ON held | **−736** | VRTX +1,264, TRGP +1,406; UAL −984, ROST −837, CAT −739, VMRK −495, ED −129, FAST −96, WRB −88, GRMN −38 |
| shared names, different size | **−565** | ADM half-size (score 70 → 0.5x) −511, MGM −96, COHR +34, KO +8 |
| net | **≈ +890** | matches −691 vs −1,583 |

**What the gate actually did.** It band-skipped CARR, QQQ, LNT and ESS on Aug 3–5 (net −$1,767 in AI_OFF). That kept slots and sectors free for:
- CAT, which AI_OFF had sector-blocked behind CARR.
- VRTX and TRGP, which AI_OFF skipped with "no open slot (8/8 used)".

Two gap-up target fills, VRTX and TRGP, supply +$2,670. Without them, AI_ON's own names lose $3,406, which is worse than AI_OFF's.

**Path dependence.** Everything after mid-August was path-dependent. BBY, KLAC, CB and ADP entered in AI_OFF only because AI_ON held ROST, GRMN and WRB in those sectors. None of those were model decisions.

**Exposure.** AI_ON held about 1 fewer name on average (6.26 vs 7.21). With the book losing while SPY rose, that is worth roughly +0.2 pp.

**Verdict on the NAV gap: mostly luck and path, plus a little underexposure.** The replay shows the approved names were not reliably better candidates. The run gap of +0.9 pp is well inside the replay CI.

## 4. Verdict

**Does the score add information on these weeks? No detectable information.**
- ρ = +0.04.
- The approved−band gap of +1.3 pp has a CI from −2.1 to +4.7 and p = 0.43.
- The scale is compressed (56–82) and never rejects.
- The same name's score swings 20 points in a week with no change in its outcome.
- The +0.9 pp NAV win comes from two gap-up fills that the skips made room for, not from the approved set out-earning the band.
- Both the approved and band sets lost money gross while SPY made +2–3%. The technical screen itself is the bigger problem: all 235 passers averaged −2.0%.

**What a longer test would need:**
1. **Score every technical passer, not only the ones that reach a free slot.** Log the score and replay each candidate book-free as done here. This removes path dependence and gives about 7.8 candidates per session.
2. **Sample size.** At least 600–1,000 scored candidates, about 4–6 months at the current passer rate. It should span more than one regime (a bull stretch, a chop, and a VIX > 25 episode). Use a date-clustered CI and a pre-registered test: ρ > 0, and approved − band > 0 net of 46 bps.
3. **Only windows after the model's training cutoff.** In practice that means forward paper capture: score daily and freeze the record. Backfilled history is not clean.
4. **Test-retest.** Score the same prompt 3× per candidate to measure score noise. Test whether the average or the minimum of the three predicts better.
5. **An ablation.** Compare against a trivial scorer (for example a sector-momentum rank) and the unscored-passer baseline. The AI earns its 60 calls and 2.6x wall time only if it beats both.
