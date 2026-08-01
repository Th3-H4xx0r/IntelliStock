"""Unadjusted stock-split detection.

The price feed delivers RAW (unadjusted) prices. When a symbol splits, its close
steps down by the split factor between two consecutive bars and then trades
normally at the new level. Nothing economic has happened — a holder receives
`ratio` times the shares at `1/ratio` the price — but every price-derived
computation in the system reads it as a crash:

    VGT  2026-04-20 19:45   $808.88
         2026-04-21 13:30   $101.57      ratio 7.964  (8-for-1)
         2026-04-21 13:45   $101.87      ... and trading continues normally

That single event cost three separate backtest runs: the portfolio emulator kept
the old share count so NAV fell 13.7% in one bar, and the strategy then
stop-lossed the "collapsing" position, turning a true +$168 into a booked -$765.

Reverse splits are the mirror case (price steps UP) and are equally real.

This module only DETECTS. Callers decide what to adjust — share counts multiply
by the ratio, stored prices (entry, peak, cached closes) divide by it.
"""

# Ratios a real corporate action actually uses. Requiring the observed ratio to
# land near one of these is what separates a split from a crash: a genuine -50%
# move does not arrive at exactly 2.000x, and moves large enough to imitate the
# bigger ratios halt the tape rather than printing calmly at the new level.
SPLIT_RATIOS = (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 15.0, 20.0)

# Fractional tolerance around a listed ratio. 1.5% comfortably absorbs the
# intraday drift between the pre-split close and the post-split open (VGT's
# observed 7.964 against 8.0 is 0.45% off) while staying far away from ordinary
# price action.
DEFAULT_TOLERANCE = 0.015

# Below this the move is ordinary volatility and must never be reinterpreted.
# The smallest real split (2:1) is a 50% step, so a 35% floor leaves margin
# without admitting routine gaps.
MIN_MOVE_PCT = 0.35


def detect_split_ratio(prev_price, new_price, tolerance=DEFAULT_TOLERANCE):
    """Return the split ratio between two consecutive prices, or None.

    The return is always expressed as "shares multiply by this":
      * forward split (price falls):  8-for-1  -> returns 8.0
      * reverse split (price rises):  1-for-10 -> returns 0.1

    So a caller can uniformly apply ``shares *= ratio`` and ``price /= ratio``.

    Returns None for any ordinary move, including a genuine crash: erasing a
    real loss by mistaking it for a split is far worse than missing a split, so
    this errs toward None.
    """
    try:
        prev_price = float(prev_price)
        new_price = float(new_price)
    except (TypeError, ValueError):
        return None
    if prev_price <= 0 or new_price <= 0:
        return None

    move = abs(new_price - prev_price) / prev_price
    if move < MIN_MOVE_PCT:
        return None

    if new_price < prev_price:          # forward split: shares multiply
        observed = prev_price / new_price
        for ratio in SPLIT_RATIOS:
            if abs(observed / ratio - 1.0) <= tolerance:
                return ratio
    else:                                # reverse split: shares divide
        observed = new_price / prev_price
        for ratio in SPLIT_RATIOS:
            if abs(observed / ratio - 1.0) <= tolerance:
                return 1.0 / ratio
    return None


def adjust_closes_for_splits(closes, tolerance=DEFAULT_TOLERANCE):
    """Return a split-CONTINUOUS copy of a close series, oldest-first.

    Every price before a detected split is divided by the ratio, so trailing
    returns, moving averages and drawdown-from-peak are computed on a series
    that reflects economics rather than share-count bookkeeping. Without this a
    20-day return spanning an 8-for-1 split reads roughly -87%.

    Returns (adjusted_closes, [(index, ratio), ...]) so the caller can log what
    was found. The input is not mutated.
    """
    try:
        out = [float(c) for c in (closes or [])]
    except (TypeError, ValueError):
        return list(closes or []), []
    found = []
    for i in range(len(out) - 1, 0, -1):
        ratio = detect_split_ratio(out[i - 1], out[i], tolerance)
        if ratio is None:
            continue
        found.append((i, ratio))
        # Restate everything BEFORE the split onto the post-split scale.
        for j in range(i):
            out[j] = out[j] / ratio
    found.reverse()
    return out, found


def reconcile_with_corporate_action(observed_ratio, action_multiplier,
                                    tolerance=0.05):
    """Confirm an inferred split against an authoritative corporate action.

    Price-ratio inference cannot separate a real crash from a split: a -90%
    collapse is arithmetically identical to a 10:1. Measured across 345 one-bar
    discontinuities in this system's caches, only 96 were splits — 176 were
    genuine crashes, a 65% false-positive rate. So inference alone must never
    silently rewrite stored prices.

    A corporate-actions feed resolves it. Benzinga's splits calendar is already
    fetched (`benzinga_client._fetch_splits_raw`) and now emits a numeric
    `share_multiplier`; Alpaca's /v1beta1/corporate-actions is an equivalent
    source. Pass the observed price ratio and the feed's multiplier:

      * both present and agreeing  -> confirmed, return the AUTHORITATIVE value
      * both present, disagreeing  -> None (the price move was not this action)
      * no corporate action        -> None (a crash, not a split)

    Returning the feed's value rather than the observed one matters: the
    observed ratio carries intraday drift (VGT printed 7.964 for a true 8.0),
    and restating prices by a slightly-wrong factor leaves a residual error in
    every downstream return.
    """
    if action_multiplier is None:
        return None
    try:
        action_multiplier = float(action_multiplier)
    except (TypeError, ValueError):
        return None
    if action_multiplier <= 0 or action_multiplier == 1.0:
        return None
    if observed_ratio is None:
        return action_multiplier          # feed is authoritative on its own
    try:
        observed_ratio = float(observed_ratio)
    except (TypeError, ValueError):
        return action_multiplier
    if observed_ratio <= 0:
        return action_multiplier
    if abs(observed_ratio / action_multiplier - 1.0) <= tolerance:
        return action_multiplier
    return None
