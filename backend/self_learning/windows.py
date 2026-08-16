"""Window classes and the in-sample registry.

Two separate jobs, both about not fooling yourself with a window.

**Window class.** Dispersion is not one number. A 15-minute run has far more
decision points than an hourly one, and a bear window has more forced exits, so
a noise floor measured in one class says nothing about another. The class is
``(granularity, length bucket, regime)`` and a floor is never substituted across
classes. Granularity buckets are EXACT, not ranges: a 30-minute run and an
hourly run are different cadences, and a `<=` bucket boundary silently merged
them.

**In-sample registry.** 52 of this project's first 100 backtests used ONE window
(2026-01-01..2026-03-01). Every mechanism in the codebase was tuned while
looking at it, so a result on it measures memory, not skill. Contamination is
measured in SHARED DAYS rather than as a fraction, because the fraction let a
long candidate absorb eleven of W0's days and still read clean.

A malformed window — reversed, zero-span, or unparseable — is CONTAMINATED, not
clean. Treating a typo as fresh evidence is how an already-used window gets
re-run and believed.
"""
from __future__ import annotations

from self_learning.timeline import to_naive_utc

# The window 52 of the first 100 backtests ran on. Permanently in-sample.
W0_START = "2026-01-01"
W0_END = "2026-03-01"

# Any candidate sharing more than this many days with an already-used window is
# not independent evidence. Days, not a fraction: a fraction scales with the
# candidate's own length, so a long window could swallow a short used one whole
# and still look clean.
MAX_SHARED_DAYS = 5

# Exact-match cadence buckets. Anything unlisted keeps its raw seconds so two
# genuinely different cadences can never share a floor.
_GRANULARITY_LABELS = {
    60: "1m", 300: "5m", 900: "15m", 1800: "30m",
    3600: "1h", 14400: "4h", 86400: "1d",
}

_LENGTH_BUCKETS = ((45, "short"), (120, "medium"), (400, "long"))


class WindowError(ValueError):
    pass


def granularity_label(seconds) -> str:
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        return "unknown"
    if seconds <= 0:
        return "unknown"
    return _GRANULARITY_LABELS.get(seconds, f"{seconds}s")


def length_days(start, end):
    """Span in days, or None when the window is malformed.

    None — not 0 — for reversed or unparseable windows. Zero would make them
    look like a legitimate same-day window, and every overlap check would then
    read "no overlap".
    """
    a, b = to_naive_utc(start), to_naive_utc(end)
    if a is None or b is None:
        return None
    delta = (b - a).total_seconds()
    if delta <= 0:
        # Zero-span too: a backtest window of no length is not a window, and
        # treating it as a valid 0-day span made it overlap nothing — so a
        # single day sitting inside W0 certified clean.
        return None
    return delta / 86400.0


def is_malformed(start, end) -> bool:
    return length_days(start, end) is None


def length_label(start, end) -> str:
    days = length_days(start, end)
    if days is None:
        return "unknown"
    for bound, label in _LENGTH_BUCKETS:
        if days <= bound:
            return label
    return "very_long"


def window_class(*, granularity_seconds, start, end, regime="unknown") -> str:
    """The comparability key for a noise floor."""
    return "/".join((
        granularity_label(granularity_seconds),
        length_label(start, end),
        str(regime or "unknown").strip().lower() or "unknown",
    ))


def overlap_days(a_start, a_end, b_start, b_end) -> float:
    """Shared span in days. A malformed window overlaps NOTHING measurably, so
    callers must check `is_malformed` — see `InSampleRegistry.is_contaminated`,
    which refuses malformed windows outright rather than trusting this 0."""
    if is_malformed(a_start, a_end) or is_malformed(b_start, b_end):
        return 0.0
    a0, a1 = to_naive_utc(a_start), to_naive_utc(a_end)
    b0, b1 = to_naive_utc(b_start), to_naive_utc(b_end)
    latest_start = max(a0, b0)
    earliest_end = min(a1, b1)
    shared = (earliest_end - latest_start).total_seconds()
    return max(0.0, shared / 86400.0)


def overlap_fraction(a_start, a_end, b_start, b_end) -> float:
    """Shared days as a fraction of the SHORTER window. Symmetric by
    construction — the denominator does not depend on argument order."""
    shared = overlap_days(a_start, a_end, b_start, b_end)
    if not shared:
        return 0.0
    spans = [length_days(a_start, a_end), length_days(b_start, b_end)]
    spans = [s for s in spans if s and s > 0]
    if not spans:
        return 0.0
    return min(1.0, shared / min(spans))


def is_w0(start, end) -> bool:
    """True when a window touches the permanently in-sample W0 at all.

    Deliberately strict. W0 is in-sample because every mechanism here was tuned
    while looking at it, so borrowing a couple of weeks of it is not a little
    bit contaminated — it is contaminated.
    """
    if is_malformed(start, end):
        return True            # a malformed window is never certified clean
    return overlap_days(start, end, W0_START, W0_END) > MAX_SHARED_DAYS


def effective_n(windows) -> float:
    """How many INDEPENDENT windows a set really contains.

    The design-effect form `n / (1 + mean_pairwise_overlap * (n - 1))`. An
    earlier version charged every window its full worst overlap, so two
    identical windows scored 0.0 — less evidence than one window, and less than
    none, which breaks any downstream ratio.

    A four-window sweep in this project shared 37 days, so its effective n was
    ~3.4 rather than 4. Reporting 4 overstates the evidence, which is how a
    marginal result gets promoted.
    """
    windows = [w for w in (windows or []) if w and len(w) >= 2]
    n = len(windows)
    if n == 0:
        return 0.0
    if n == 1:
        return 1.0
    pairs, total = 0, 0.0
    for i in range(n):
        for j in range(i + 1, n):
            total += overlap_fraction(windows[i][0], windows[i][1],
                                      windows[j][0], windows[j][1])
            pairs += 1
    mean_overlap = (total / pairs) if pairs else 0.0
    return round(n / (1.0 + mean_overlap * (n - 1)), 3)


class InSampleRegistry:
    """Windows already used as evidence. W0 is blacklisted from birth."""

    def __init__(self, used=None):
        self._used = [tuple(w) for w in (used or []) if w and len(w) >= 2]

    @property
    def used(self) -> list:
        return [tuple(w) for w in self._used]

    def is_contaminated(self, start, end):
        """Returns a reason string when the window may not be used, else None."""
        if is_malformed(start, end):
            return (f"malformed window ({start}..{end}) — reversed, zero-span "
                    f"or unparseable. A typo must not pass as fresh evidence.")
        if is_w0(start, end):
            return (f"overlaps W0 ({W0_START}..{W0_END}), which 52 of the first "
                    f"100 backtests used — in-sample for every mechanism here")
        # Accumulated, not pairwise: three priors each sharing 4 days would each
        # pass a pairwise check while together covering 12 days of the window.
        shared_total = 0.0
        for prior_start, prior_end in self._used:
            shared_total += overlap_days(start, end, prior_start, prior_end)
        if shared_total > MAX_SHARED_DAYS:
            return (f"shares {shared_total:.0f} day(s) in total with windows "
                    f"already used as evidence (limit {MAX_SHARED_DAYS})")
        return None

    def record(self, start, end) -> None:
        self._used.append((str(start), str(end)))

    def to_doc(self) -> list:
        return [list(w) for w in self._used]
