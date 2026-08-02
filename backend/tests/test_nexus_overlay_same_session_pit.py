"""The overlay daily-bar cache must not expose the still-open session.

`_overlay_bars_compute_range` fetches 1Day bars all the way to wall-clock
today, and every consumer guarded itself with `bar_date[:10] <= date_key`.
Equities backtest at 15m/1h, so a decision taken at 09:45 ET on day D was
reading day D's daily bar — a bar whose close is the 16:00 print. These tests
pin the boundary at the exchange close and pin the four cases where the
same-session bar is legitimately visible.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from point_in_time_data import DatasetManifest, PointInTimeContext
from strategies import graph_nexus_analysis as graph


UTC = timezone.utc
DAY = "2026-03-02"


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _manifest() -> DatasetManifest:
    return DatasetManifest(
        manifest_id="overlay-same-session",
        source_hashes={"overlay": "sha256:overlay"},
        created_at=_ts("2026-01-01T00:00:00Z"),
    )


def _research_context(as_of: str) -> PointInTimeContext:
    """The declared legacy_unverified context every equities backtest runs in."""
    return PointInTimeContext(
        as_of=_ts(as_of),
        manifest=_manifest(),
        strict=False,
        is_live=False,
    )


def _session_close(session_date: date) -> datetime:
    return datetime.combine(session_date, time(21, 0), tzinfo=UTC)


def _cache(as_of: str | None, *, live: bool = False, increment: int = 900) -> dict:
    if as_of is None:
        context = None
    elif live:
        context = PointInTimeContext.for_live(
            as_of=_ts(as_of), manifest=_manifest()
        )
    else:
        context = _research_context(as_of)
    return {
        "_point_in_time_context": context,
        "_point_in_time_session_close_resolver": _session_close,
        "_resolved_time_increment_sec": increment,
    }


def _bars(*days_and_closes) -> list[dict]:
    return [
        {"t": f"{day}T05:00:00Z", "c": close}
        for day, close in days_and_closes
    ]


# --- the boundary itself --------------------------------------------------


def test_open_session_daily_bar_is_hidden_intraday():
    cache = _cache("2026-03-02T14:45:00Z")  # 09:45 ET, session wide open
    assert graph._overlay_daily_cutoff(cache, DAY) == "2026-03-01"


def test_daily_bar_becomes_visible_at_the_exchange_close():
    cache = _cache("2026-03-02T21:00:00Z")
    assert graph._overlay_daily_cutoff(cache, DAY) == DAY


def test_live_context_keeps_the_current_partial_bar():
    # In live, today's partial daily bar IS the current state. Hiding it here
    # would silently change live trading, which this fix must never do.
    cache = _cache("2026-03-02T14:45:00Z", live=True)
    assert graph._overlay_daily_cutoff(cache, DAY) == DAY


def test_missing_context_is_unchanged():
    assert graph._overlay_daily_cutoff({}, DAY) == DAY
    assert graph._overlay_daily_cutoff(None, DAY) == DAY


def test_daily_cadence_keeps_the_end_of_day_convention():
    # A daily bar IS the end-of-day state the decision is made on, and fills
    # land on the next bar. Same convention run_once's news window uses.
    cache = _cache("2026-03-02T14:45:00Z", increment=86400)
    assert graph._overlay_daily_cutoff(cache, DAY) == DAY


def test_escape_hatch_restores_the_legacy_boundary():
    cache = _cache("2026-03-02T14:45:00Z")
    config = {"overlay_allow_same_session_daily_bar": True}
    assert graph._overlay_daily_cutoff(cache, DAY, config) == DAY
    # ...and the cache-mirrored form, for the callers that take no config.
    cache["_overlay_allow_same_session_daily_bar"] = True
    assert graph._overlay_daily_cutoff(cache, DAY) == DAY


def test_broken_calendar_falls_back_to_the_latest_possible_close():
    """A resolver that raises must not silently re-open the leak.

    exchange_calendars is a hard requirement but an optional import; erring
    LATE (21:00 UTC = 16:00 ET under EST) only ever withholds a bar that was
    in fact available.
    """

    def _explode(session_date):
        raise RuntimeError("calendar unavailable")

    cache = _cache("2026-03-02T20:30:00Z")
    cache["_point_in_time_session_close_resolver"] = _explode
    assert graph._overlay_daily_cutoff(cache, DAY) == "2026-03-01"

    cache = _cache("2026-03-02T21:30:00Z")
    cache["_point_in_time_session_close_resolver"] = _explode
    assert graph._overlay_daily_cutoff(cache, DAY) == DAY


# --- the filter ------------------------------------------------------------


def test_visible_overlay_bars_drops_the_open_session():
    bars = _bars(("2026-02-27", 99.0), (DAY, 108.0), ("2026-03-03", 120.0))
    cache = _cache("2026-03-02T14:45:00Z")
    visible = graph._visible_overlay_bars(bars, cache, DAY)
    assert [b["c"] for b in visible] == [99.0]


def test_visible_overlay_bars_keeps_the_closed_session():
    bars = _bars(("2026-02-27", 99.0), (DAY, 108.0), ("2026-03-03", 120.0))
    cache = _cache("2026-03-02T21:00:00Z")
    visible = graph._visible_overlay_bars(bars, cache, DAY)
    assert [b["c"] for b in visible] == [99.0, 108.0]


# --- real consumers --------------------------------------------------------


def test_entry_gate_bars_cannot_see_the_open_session():
    # _resolve_asof_bars feeds the price-extension entry gates. Before the fix
    # a 09:45 decision saw the 16:00 close of the very bar it was buying into.
    cache = _cache("2026-03-02T14:45:00Z")
    cache["_overlay_bars_raw"] = {
        "CAR": _bars(("2026-02-27", 148.0), (DAY, 311.0)),
    }
    bars = graph._resolve_asof_bars("CAR", {}, cache, DAY, min_bars=1)
    assert [b["c"] for b in bars] == [148.0]


def test_macro_risk_spy_return_cannot_see_the_open_session():
    # _spy_20d_return gates the macro-bearish haircut. Reading today's
    # not-yet-final SPY close leaked the market move it is meant to anticipate.
    first = date(2026, 2, 1)
    series = [
        (f"{(first + timedelta(days=i)).isoformat()}", 100.0)
        for i in range(30)
    ]
    # Spike the decision day so a leak is unmistakable in the returned number.
    series = [(d, 100.0) for d, _ in series if d < DAY]
    series.append((DAY, 200.0))
    cache = _cache("2026-03-02T14:45:00Z")
    cache["_overlay_bars_raw"] = {"SPY": _bars(*series)}

    assert graph._spy_20d_return(cache, DAY) == 0.0

    after_close = _cache("2026-03-02T21:00:00Z")
    after_close["_overlay_bars_raw"] = cache["_overlay_bars_raw"]
    assert graph._spy_20d_return(after_close, DAY) == 1.0


# --- market cap: today's value gating a past decision ----------------------


def _mcap_cache(as_of: str, *, live: bool = False) -> dict:
    cache = _cache(as_of, live=live)
    # Overlay history: $10 at the decision date, $100 by the time the
    # current-state market cap was read. A 10x price run since then.
    cache["_overlay_bars_raw"] = {
        "RUNUP": _bars(
            ("2026-02-27", 10.0),
            (DAY, 11.0),
            ("2026-06-01", 100.0),
        ),
    }
    cache["_ticker_metadata"] = {"RUNUP": {"market_cap": 5_000_000_000.0}}
    return cache


def test_market_cap_is_deflated_to_the_decision_date():
    cache = _mcap_cache("2026-03-02T14:45:00Z")
    # $5B today, price 10x since the last CLOSED session ($10 of $100).
    assert graph._v32_get_market_cap("RUNUP", cache, {}) == 500_000_000.0


def test_market_cap_deflation_uses_the_closed_session_only():
    # After the close, the decision-day bar ($11) is the reference.
    cache = _mcap_cache("2026-03-02T21:00:00Z")
    assert graph._v32_get_market_cap("RUNUP", cache, {}) == 550_000_000.0


def test_live_market_cap_is_never_deflated():
    cache = _mcap_cache("2026-03-02T14:45:00Z", live=True)
    assert graph._v32_get_market_cap("RUNUP", cache, {}) == 5_000_000_000.0


def test_market_cap_deflation_has_an_escape_hatch():
    cache = _mcap_cache("2026-03-02T14:45:00Z")
    cache["_pit_scale_market_cap"] = False
    assert graph._v32_get_market_cap("RUNUP", cache, {}) == 5_000_000_000.0


def test_market_cap_unchanged_without_overlay_history():
    cache = _mcap_cache("2026-03-02T14:45:00Z")
    cache["_overlay_bars_raw"] = {}
    assert graph._v32_get_market_cap("RUNUP", cache, {}) == 5_000_000_000.0


def test_quality_metadata_market_cap_is_deflated():
    cache = _mcap_cache("2026-03-02T14:45:00Z")
    quality = graph._extract_quality_metadata(
        "RUNUP",
        {"quality_metadata": {"market_cap": 5_000_000_000.0}},
        None,
        None,
        strategy_cache=cache,
    )
    assert quality["market_cap"] == 500_000_000.0
