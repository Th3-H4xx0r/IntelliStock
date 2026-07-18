"""Inception truth, flow-aware TWR lenses, and activity ingestion (Task 9).

``InceptionState`` is durable: an ordinary restart's current equity can
never replace inception or the high-water mark. Time-weighted return links
sub-periods around external cash flows so a deposit is not performance.
Account activities ingest idempotently by broker activity ID.
"""
from dataclasses import dataclass, replace
from datetime import datetime


@dataclass(frozen=True)
class InceptionState:
    inception_equity: float
    inception_at: datetime
    high_water_equity: float
    source: str


def update_inception_state(state, current_equity):
    """Reconcile durable inception state with a fresh equity observation.

    Only the high-water mark may move — and only UP. Inception is immutable
    here; establishing or resetting it is an explicit operator action."""
    if state is None or not isinstance(state, InceptionState):
        raise ValueError(
            "inception state is required — a restart may not synthesize one "
            "from current equity (that is the total-P&L=0 bug)")
    current = float(current_equity or 0.0)
    if current > state.high_water_equity:
        return replace(state, high_water_equity=current)
    return state


def time_weighted_return(values, flows):
    """TWR over ``values`` [(date, value), ...] with external ``flows``
    [(date, signed_amount), ...]. A flow dated D applies at the START of D:
    the sub-period ending at D is measured to the pre-flow value
    (value_at_D - flow), and the next sub-period starts at value_at_D."""
    # Collapse duplicate valuation dates to their LAST value (audit
    # 2026-07-18: a flow on a duplicated date was applied to every
    # sub-period ending that date — a flat account reported -10%), and
    # reject non-positive valuations outright.
    by_date = {}
    for d, v in sorted((str(d), float(v)) for d, v in values):
        if v <= 0:
            raise ValueError(f"non-positive valuation {v} on {d}")
        by_date[d] = v
    points = sorted(by_date.items())
    if len(points) < 2:
        raise ValueError("need at least two valuation points")
    valuation_dates = {d for d, _ in points}
    flow_by_date = {}
    for d, amount in flows or ():
        if str(d) not in valuation_dates:
            # A silently-ignored flow poisons every lens (bug sweep
            # 2026-07-18: a skipped $4,000 deposit reported +200.5% TWR).
            raise ValueError(
                f"external flow on {d} has no matching valuation point — "
                "supply the flow-date valuation")
        flow_by_date[str(d)] = flow_by_date.get(str(d), 0.0) + float(amount)
    growth = 1.0
    prev_value = points[0][1]
    for date, value in points[1:]:
        flow = flow_by_date.get(date, 0.0)
        pre_flow = value - flow
        if prev_value > 0:
            growth *= pre_flow / prev_value
        prev_value = value
    return growth - 1.0


def ingest_activities(pages):
    """Merge paginated Alpaca account-activity pages idempotently by
    activity ID. Later duplicates are byte-for-byte no-ops."""
    ledger = {}
    for page in pages or ():
        for activity in page or ():
            activity_id = str((activity or {}).get("id") or "")
            if not activity_id:
                raise ValueError("activity without an id cannot be ingested")
            ledger[activity_id] = dict(activity)
    return ledger


def reconcile_ledger(*, fifo_realized_pnl, unrealized_pnl, dividends, fees,
                     account_pnl, tolerance=0.05):
    """Economic ledger vs account P&L (July 18 contract: within $0.05)."""
    economic = (float(fifo_realized_pnl) + float(unrealized_pnl)
                + float(dividends) - abs(float(fees)))
    residual = float(account_pnl) - economic
    return {"economic_total": economic, "account_pnl": float(account_pnl),
            "residual": residual, "reconciled": abs(residual) <= tolerance}
