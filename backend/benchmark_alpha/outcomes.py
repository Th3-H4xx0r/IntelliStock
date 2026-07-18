"""Strict trading-calendar horizon outcome evaluator (Task 7A).

Entry is the registered tradable observation on the first session AFTER the
forecast's ``as_of``; exit is the same observation exactly N exchange
sessions later. The session list is exchange-authoritative — the weekday
fallback in ``live_calendar`` is prohibited for promotion-eligible
evaluation. Every registered forecast (eligible, rejected, traded or not)
resolves; unresolvable evidence becomes a conservative missing outcome that
stays in the denominator. Corrected source data creates a new revision and
never mutates prior evidence.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from benchmark_alpha.records import HorizonOutcome

_EASTERN = ZoneInfo("America/New_York")

# Legacy intents that map to a real directional prediction. Queue/deferred
# states are typed rejections/holds elsewhere; ``unknown`` is never coerced.
_LEGACY_DIRECTIONAL_INTENTS = frozenset({"buy", "sell", "initial_buy"})


class StrictCalendarError(Exception):
    """The exchange-authoritative calendar is unavailable; the weekday
    fallback may not substitute for it."""


class HoldoutContaminationError(Exception):
    """A hypothesis-generating sample was offered as sealed-holdout evidence."""


@dataclass(frozen=True)
class StrictTradingCalendar:
    """Ordered exchange sessions (ISO dates), holidays/early closes resolved
    upstream by exchange_calendars — never approximated here."""
    sessions: tuple

    def __post_init__(self):
        sessions = tuple(str(s) for s in self.sessions)
        if list(sessions) != sorted(set(sessions)):
            raise ValueError("sessions must be strictly increasing ISO dates")
        object.__setattr__(self, "sessions", sessions)

    def session_index(self, session_date):
        try:
            return self.sessions.index(str(session_date))
        except ValueError:
            raise ValueError(
                f"{session_date!r} is not an exchange session") from None

    def session_after(self, as_of):
        """First session strictly after ``as_of``'s Eastern trading date."""
        as_of_date = as_of.astimezone(_EASTERN).date().isoformat()
        for session in self.sessions:
            if session > as_of_date:
                return session
        return None

    def session_at_offset(self, session_date, offset):
        idx = self.session_index(session_date) + int(offset)
        if idx < 0 or idx >= len(self.sessions):
            return None
        return self.sessions[idx]


def strict_calendar_from_live_calendar(start_date, end_date):
    """Build a StrictTradingCalendar from ``live_calendar``'s XNYS calendar.

    Raises ``StrictCalendarError`` when exchange_calendars is unavailable —
    the weekday fallback does not honor holidays/early closes and is
    prohibited for research and promotion evaluation."""
    import live_calendar
    if not getattr(live_calendar, "_HAS_LIB", False) or \
            getattr(live_calendar, "_CAL", None) is None:
        raise StrictCalendarError(
            "exchange_calendars is required for promotion-eligible outcome "
            "evaluation; the weekday fallback is prohibited")
    import pandas as pd
    sessions = live_calendar._CAL.sessions_in_range(
        pd.Timestamp(start_date), pd.Timestamp(end_date))
    return StrictTradingCalendar(
        sessions=tuple(s.date().isoformat() for s in sessions))


def _observation(series, session):
    obs = (series or {}).get(session)
    if not isinstance(obs, dict):
        return None
    adjusted = obs.get("close_adjusted")
    if adjusted is None or not float(adjusted) > 0:
        return None
    return obs


class OutcomeEvaluator:
    """Resolves registered forecasts into ``HorizonOutcome`` records."""

    def resolve(self, forecast, calendar, stock_series, spy_series,
                data_revision=0, hypothesis_generating=False,
                evaluated_at=None):
        evaluated_at = evaluated_at or datetime.now(timezone.utc)

        def outcome(entry_session="", exit_session=None, entry_obs=None,
                    exit_obs=None, spy_entry=None, spy_exit=None,
                    relative=None, action_state="none", missing=None):
            return HorizonOutcome(
                forecast_id=forecast.record_id,
                horizon_trading_days=forecast.horizon_trading_days,
                data_revision=int(data_revision),
                instance_id=forecast.instance_id,
                origin=forecast.origin,
                entry_session=entry_session,
                exit_session=exit_session,
                entry_price_raw=(entry_obs or {}).get("close_raw"),
                exit_price_raw=(exit_obs or {}).get("close_raw"),
                entry_price_adjusted=(entry_obs or {}).get("close_adjusted"),
                exit_price_adjusted=(exit_obs or {}).get("close_adjusted"),
                spy_entry_adjusted=(spy_entry or {}).get("close_adjusted"),
                spy_exit_adjusted=(spy_exit or {}).get("close_adjusted"),
                benchmark_relative_return=relative,
                corporate_action_state=action_state,
                missing_reason=missing,
                hypothesis_generating=bool(hypothesis_generating),
                evaluated_at=evaluated_at,
            )

        entry_session = calendar.session_after(forecast.as_of)
        if entry_session is None:
            return outcome(missing="entry_session_beyond_calendar")
        exit_session = calendar.session_at_offset(
            entry_session, forecast.horizon_trading_days)
        entry_obs = _observation(stock_series, entry_session)
        if exit_session is None:
            return outcome(entry_session=entry_session, entry_obs=entry_obs,
                           spy_entry=_observation(spy_series, entry_session),
                           missing="exit_session_beyond_calendar")
        exit_obs = _observation(stock_series, exit_session)
        spy_entry = _observation(spy_series, entry_session)
        spy_exit = _observation(spy_series, exit_session)
        action_state = (
            (exit_obs or {}).get("corporate_action")
            or (entry_obs or {}).get("corporate_action")
            or "none")
        if entry_obs is None:
            return outcome(entry_session=entry_session,
                           exit_session=exit_session, exit_obs=exit_obs,
                           spy_entry=spy_entry, spy_exit=spy_exit,
                           action_state=action_state,
                           missing="missing_entry_observation")
        if exit_obs is None:
            return outcome(entry_session=entry_session,
                           exit_session=exit_session, entry_obs=entry_obs,
                           spy_entry=spy_entry, spy_exit=spy_exit,
                           action_state=action_state,
                           missing="missing_exit_observation")
        if spy_entry is None or spy_exit is None:
            return outcome(entry_session=entry_session,
                           exit_session=exit_session, entry_obs=entry_obs,
                           exit_obs=exit_obs, action_state=action_state,
                           missing="missing_benchmark_observation")

        stock_ret = float(exit_obs["close_adjusted"]) / float(entry_obs["close_adjusted"]) - 1.0
        spy_ret = float(spy_exit["close_adjusted"]) / float(spy_entry["close_adjusted"]) - 1.0
        # Directional-forecast correctness is judged by consumers; the stored
        # value is ALWAYS the actual stock-minus-SPY return (never sign-
        # flipped for sells — the legacy inversion bug).
        return outcome(entry_session=entry_session, exit_session=exit_session,
                       entry_obs=entry_obs, exit_obs=exit_obs,
                       spy_entry=spy_entry, spy_exit=spy_exit,
                       relative=stock_ret - spy_ret,
                       action_state=action_state)


def forecast_from_legacy_row(row):
    """Boundary guard for legacy GraphNexus rows: an intent outside the
    directional allowlist (notably ``unknown`` and queue/deferred states)
    can never become a Forecast."""
    intent = str((row or {}).get("action_intent", "")).lower()
    if intent not in _LEGACY_DIRECTIONAL_INTENTS:
        raise ValueError(
            f"legacy intent {intent!r} is not a directional prediction — "
            "refusing to coerce it into a typed forecast")
    raise NotImplementedError(
        "full legacy adaptation is Task 8/10 scope; only the rejection "
        "boundary is defined here")


def assert_holdout_eligible(outcomes):
    """A previously-viewed/hypothesis-generating sample is never a sealed
    holdout (H09). Raises ``HoldoutContaminationError`` on contamination."""
    for outcome in outcomes or ():
        if getattr(outcome, "hypothesis_generating", False):
            raise HoldoutContaminationError(
                f"outcome {outcome.record_id} is hypothesis-generating and "
                "cannot serve as sealed-holdout evidence")
