"""How long a name waits between being seen and being bought.

The objective's blocker #1 is entry timing — *"another 19 days after the gap that was its whole
+54.7%"*. Lag is the right way to measure it, for one reason worth stating plainly:

    The obvious statistic — "how far through its move did we buy?" — needs the move's END
    price, which is not knowable at entry. It is lookahead-contaminated and can never become a
    signal. Lag needs only the run's own log.

Lag is also far less draw-sensitive than return, which matters here because paired runs on this
system share as little as 11-23% of their traded names. It asks how long a name WAITED, not
which names appeared. It is *less* sensitive, not insensitive — a run trading different names
has a different lag distribution for that reason alone, and any comparison must say so.

## The bug this module exists to prevent

The first version of this measurement defined "first seen" from two momentum-specific log lines
(`Discovered stock (momentum): X` and `Discovered stocks (N active): ...`). Against a real run it
produced **negative lags** — names bought before they were "first seen" — because names entering
through the news or graph lanes never print those lines, so the first capture happened later
than the buy. It also silently dropped most bought names: 2 of 8 survived.

A negative lag is not a fast entry, it is proof the parse is wrong. This module therefore:

  * defines "considered" LANE-AGNOSTICALLY, as the first bar on which the broker evaluated the
    symbol at all (`SYM @ DATE TIME ($price)`), which every lane prints; and
  * treats a negative lag as a PARSE ERROR and refuses to fold it into the statistics.

Fail loudly beats a plausible number.
"""
from __future__ import annotations

import datetime
import re
import statistics

#: The broker's per-bar evaluation line. Every lane prints it, which is the whole point.
_CONSIDERED = re.compile(
    r"\b([A-Z][A-Z0-9.]{0,5}) @ (\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} \(\$"
)
_BUY = re.compile(r"FILL BUY ([A-Z.]+) .*?quote=(\d{4}-\d{2}-\d{2})")

#: The passive index core is not an alpha entry — it is the funding leg, bought and sold on
#: rebalance bands. Including it would report a lag for a decision the thesis never makes.
DEFAULT_EXCLUDED = frozenset({"SPY"})


def _days(a, b):
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


def measure(log_text, excluded=DEFAULT_EXCLUDED):
    """Per-symbol entry lag for one run.

    Returns ``{"entries": [...], "parse_errors": [...], "stats": {...}}`` where each entry is
    ``{"symbol", "considered", "bought", "lag_days"}``, sorted by lag.

    `parse_errors` holds any symbol whose buy precedes its first evaluation. That is
    impossible, so it is surfaced rather than clamped — a clamp would turn a broken parse into
    a flattering zero.
    """
    first, buys = {}, {}
    for line in (log_text or "").splitlines():
        m = _CONSIDERED.search(line)
        if m:
            first.setdefault(m.group(1), m.group(2))
        b = _BUY.search(line)
        if b:
            buys.setdefault(b.group(1), b.group(2))

    entries, errors = [], []
    for sym, bought in buys.items():
        if sym in excluded:
            continue
        seen = first.get(sym)
        if seen is None:
            errors.append({"symbol": sym, "bought": bought,
                           "reason": "bought but never evaluated — parse gap"})
            continue
        lag = _days(seen, bought)
        row = {"symbol": sym, "considered": seen, "bought": bought, "lag_days": lag}
        if lag < 0:
            row["reason"] = "bought BEFORE first evaluation — impossible, parse is wrong"
            errors.append(row)
        else:
            entries.append(row)

    entries.sort(key=lambda e: (e["lag_days"], e["symbol"]))
    return {"entries": entries, "parse_errors": errors, "stats": summarize(entries)}


def summarize(entries, slow_threshold_days=12):
    """Median, mean, max and the SLOW TAIL.

    The tail is reported because it is where the money went: in bt 333727 the entries at 14, 23
    and 35 days were AAOI, AEHR and AXTI — the three names that run lost money on — while
    everything bought within 3 days was profitable. A median alone hides that completely.
    """
    lags = [e["lag_days"] for e in (entries or [])]
    if not lags:
        return {"n": 0}
    slow = [e for e in entries if e["lag_days"] >= slow_threshold_days]
    return {
        "n": len(lags),
        "median_days": statistics.median(lags),
        "mean_days": round(statistics.mean(lags), 2),
        "max_days": max(lags),
        "slow_threshold_days": slow_threshold_days,
        "slow_count": len(slow),
        "slow_symbols": [e["symbol"] for e in slow],
    }


def compare(control, treatment):
    """Compare two runs' lag profiles, with the caveat attached rather than assumed.

    `control`/`treatment` are `measure()` results. The returned `comparable` flag is FALSE when
    the two runs share few traded names, because lag is a per-name property: a run trading
    different names has a different lag distribution for that reason alone.
    """
    cs = {e["symbol"] for e in control.get("entries", [])}
    ts = {e["symbol"] for e in treatment.get("entries", [])}
    shared = cs & ts
    union = cs | ts
    overlap = (len(shared) / len(union)) if union else 0.0
    return {
        "control": control.get("stats", {}),
        "treatment": treatment.get("stats", {}),
        "shared_symbols": sorted(shared),
        "overlap": overlap,
        "comparable": overlap >= 0.60,
        "caveat": (
            "" if overlap >= 0.60 else
            f"arms share {overlap:.0%} of traded names — lag is a per-name property, so this "
            f"difference is directional evidence, not an attributable effect"
        ),
    }
