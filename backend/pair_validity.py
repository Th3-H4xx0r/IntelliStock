"""Is a paired A/B measuring the lever, or the draw?

On 2026-08-16 bt 453789 was run against bt 333727 on the same document, window,
instance, granularity and cash, differing in exactly ONE config flag. The two arms
traded 4 of 20 names in common — **20% overlap**. The two names the experiment was
designed around did not appear in the treatment at all. The return difference between
those books measures which names discovery happened to draw, not the flag.

That is not a one-off. Nexus state is shared and mutable, and the isolation recipe the
handoffs recommend (`history_scope_salt` + `active_event_history_scope_salt` +
`nexus_discovery_bootstrap_enabled=false` + `nexus_discovery_snapshot_enabled=false`) was
ALL armed on that document and was still not enough. It is the mechanism behind the
~10pp same-config dispersion measured earlier (bt 873929 +16.41% vs bt 523085 +6.00%,
identical config, the whole gap one name).

So this module answers the question that must be asked BEFORE a pair's return delta is
read at all. Everything here is pure: it takes text and numbers and returns a verdict, so
it can be tested without a database, a broker or a two-hour run.
"""
from __future__ import annotations

import re

#: Below this share of shared names, a return delta is not attributable to the lever.
#: 0.60 is deliberately lenient — the pair that motivated this scored 0.20, and even a
#: well-behaved pair here drifts. It is a floor for "worth reading", not a quality bar.
DEFAULT_MIN_OVERLAP = 0.60

#: Same-config dispersion for a WARM pair, in percentage points of return. Measured on
#: bt 873929 (+16.41%) vs bt 523085 (+6.00%) — identical config, the whole gap one name.
MEASURED_DISPERSION_PP = 10.0

#: Same-config dispersion for a COLD pair, measured 2026-08-16 by an explicit A/A:
#: bt 479057 vs bt 193668, same document and window, each preceded by a full state clear
#: verified `cold=True`. Result: **100% traded-name overlap and BYTE-IDENTICAL P&L** —
#: MSFT $143.34, NVDA $166.26, NVTS −$36.93, OIH $49.34, RIVN −$23.05, SPY $342.75,
#: VDE −$37.68 in both arms.
#:
#: So the ~10pp figure above was never inherent nondeterminism. It was carried state, and it
#: is removable. Against a 10pp floor a real 3pp lever is indistinguishable from noise, which
#: is the mechanical reason years of tuning returned nulls.
#:
#: **The zero is NOT taken at face value, deliberately.** This project's own guard rule says
#: a floor of zero is not a floor — byte-identical repeats have previously meant a forgotten
#: `history_scope_salt`, i.e. a configuration error rather than a quiet system. Two facts say
#: this particular zero is partly cache-mediated: both arms resolved the SAME scope id
#: (`abb5b0232cf8`), and arm B logged 413 MORE cache-hit lines than arm A, so it reused what
#: arm A computed rather than recomputing it. That the answers matched means the caches are
#: faithful; it does not mean the engine would agree from a globally-cold cache.
#:
#: For an A/B that is the right property anyway — the shared cache is a COMMON input to both
#: arms, so it cannot confound a single-lever comparison. The residual to watch is a lever
#: that changes WHICH symbols get scored, because then the two arms' cache coverage differs;
#: the overlap check catches gross cases of that.
#:
#: Hence 0.5 rather than 0.0: an order of magnitude of headroom, because one A/A pair is not
#: a distribution and a zero floor would certify anything.
COLD_START_DISPERSION_PP = 0.5

# NOT anchored with `^`: the real line carries a `[2026-08-15 19:55:40] [BROKER]`
# prefix, so a start-of-line anchor matches nothing. The first draft of this regex
# did exactly that and returned an empty universe — which would have scored every
# pair VOID for the wrong reason. Requiring `P&L =` is what separates these from the
# "Stock movement" block four lines below, which repeats the same tickers.
_PNL_LINE = re.compile(r"(?:^|\s)([A-Z][A-Z0-9.\-]{0,9}):\s*P&L\s*=", re.MULTILINE)


def traded_symbols(log_text):
    """Symbols the run actually traded, from its own end-of-run P&L block.

    Read from the run's OWN summary rather than from fills, because the summary is the
    thing the operator sees; if the two ever disagree, the operator's view is the one a
    verdict must be built on.
    """
    return {m.group(1).upper() for m in _PNL_LINE.finditer(log_text or "")}


def overlap(control_symbols, treatment_symbols):
    """Jaccard overlap of two traded universes, in [0.0, 1.0].

    Jaccard rather than "share of control also in treatment", because the asymmetric
    version scores 1.0 when the treatment merely ADDS eight new names — which is exactly
    as contaminating and would pass silently.
    """
    a, b = set(control_symbols or ()), set(treatment_symbols or ())
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def assess_pair(control, treatment, min_overlap=DEFAULT_MIN_OVERLAP,
                dispersion_pp=None, cold_start=False):
    """Verdict for a paired run.

    `control`/`treatment` are dicts with `symbols` (iterable) and `return_pct` (float or
    None). Returns a dict carrying `verdict`, `overlap`, `delta_pp`, `shared`,
    `control_only`, `treatment_only` and a human `reason`.

    Three outcomes, and the ordering matters:

    * ``VOID``       — the arms are not comparable. Reported FIRST, because a delta
                       computed over different books should never be quoted at all, not
                       even as "inside the noise floor".
    * ``NOISE``      — comparable, but the delta is inside measured dispersion.
    * ``READABLE``   — comparable and outside dispersion. Still n=1; this says the
                       number is worth reading, NOT that the lever works.

    `cold_start=True` selects the COLD dispersion floor (0.5pp) instead of the warm one
    (10pp). Pass it ONLY when both arms were verified `cold=True` by `paired_state_attest`,
    because the entire difference between those two numbers is whether carried state was
    cleared. Claiming a cold floor for a warm pair would turn noise into a finding.
    """
    if dispersion_pp is None:
        dispersion_pp = COLD_START_DISPERSION_PP if cold_start else MEASURED_DISPERSION_PP
    cs = set(control.get("symbols") or ())
    ts = set(treatment.get("symbols") or ())
    ov = overlap(cs, ts)

    cr, tr = control.get("return_pct"), treatment.get("return_pct")
    delta = None
    if cr is not None and tr is not None:
        delta = float(tr) - float(cr)

    out = {
        "overlap": ov,
        "delta_pp": delta,
        "shared": sorted(cs & ts),
        "control_only": sorted(cs - ts),
        "treatment_only": sorted(ts - cs),
        "min_overlap": min_overlap,
        "dispersion_pp": dispersion_pp,
    }

    if not cs or not ts:
        out["verdict"] = "VOID"
        out["reason"] = ("one arm traded nothing (or its summary was not reached — a "
                         "STOPPED run prints no P&L block)")
        return out

    if ov < min_overlap:
        out["verdict"] = "VOID"
        out["reason"] = (
            f"arms share {ov:.0%} of their traded names (floor {min_overlap:.0%}); "
            f"the return delta measures which names discovery drew, not the lever"
        )
        return out

    if delta is None:
        out["verdict"] = "VOID"
        out["reason"] = "a return is missing from one arm"
        return out

    if abs(delta) < dispersion_pp:
        out["verdict"] = "NOISE"
        out["reason"] = (
            f"delta {delta:+.2f}pp is inside the measured same-config dispersion "
            f"of ~{dispersion_pp:.0f}pp"
        )
        return out

    out["verdict"] = "READABLE"
    out["reason"] = (
        f"delta {delta:+.2f}pp exceeds ~{dispersion_pp:.0f}pp dispersion at "
        f"{ov:.0%} overlap — worth reading, still n=1"
    )
    return out
