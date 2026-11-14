"""Settings, event storage, queueing and payloads (move_analysis.service),
and the API wiring, against the in-process FakeStore."""
from __future__ import annotations

import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from db.fake import FakeStore  # noqa: E402
from move_analysis import prices as px  # noqa: E402
from move_analysis import service as svc  # noqa: E402

# A Saturday: the last finished session is Friday 2026-11-13.
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=px.NY)
LAST = "2026-11-13"
JUMP_DAY = "2026-11-10"


def _weekdays(start: date, end: date) -> list:
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _provider(jumps=None, market_drop=None):
    """Synthetic daily bars. ``jumps`` maps symbol -> {date: extra return};
    ``market_drop`` maps date -> SPY return that every stock follows."""
    jumps = jumps or {}
    market_drop = market_drop or {}
    days = _weekdays(date(2026, 1, 2), date(2026, 11, 13))
    rng = random.Random(11)
    market = [market_drop.get(d.isoformat(), rng.gauss(0, 0.008)) for d in days]

    def _rows(symbol, start):
        if symbol == "NOPE":
            return []
        own = random.Random(sum(map(ord, symbol)))
        beta = 1.0 if symbol in ("SPY", "XLK") else 1.4
        price, rows = 100.0, []
        for day, m in zip(days, market):
            noise = 0.0 if symbol == "SPY" else own.gauss(0, 0.004)
            ret = beta * m + noise + jumps.get(symbol, {}).get(day.isoformat(), 0.0)
            price *= 1 + ret
            if day >= start:
                rows.append({"date": day.isoformat(), "close": round(price, 4),
                             "adj": price, "volume": 1_000_000.0})
        return rows

    return _rows


def _inject(**provider_kwargs):
    return {"price_provider": _provider(**provider_kwargs),
            "sector_lookup": lambda s: "technology" if s in ("NVDA", "AMD") else "unknown",
            "now": NOW}


@pytest.fixture
def store():
    st = FakeStore()
    st.insert("Models", {"id": "m1", "name": "Opus", "provider": "claude-cli",
                         "model": "claude-opus-5-5", "api_key": "secret"})
    st.insert("Models", {"id": "m2", "name": "Haiku", "provider": "anthropic",
                         "model": "claude-haiku-5-5"})
    return st


# ── settings ─────────────────────────────────────────────────────────────────


def test_default_settings_list_models_without_keys(store):
    got = svc.get_settings(store)
    assert got["model_id"] is None and got["model_name"] is None
    assert got["sigma"] == 2.5 and got["min_move_pct"] == 3.0
    assert [m["name"] for m in got["models"]] == ["Haiku", "Opus"]
    assert all(set(m) == {"id", "name", "provider", "model"} for m in got["models"])


def test_settings_round_trip_and_partial_update(store):
    got = svc.put_settings(store, {"model_id": "m1", "sigma": 3.0})
    assert got["model_id"] == "m1" and got["model_name"] == "Opus" and got["sigma"] == 3.0
    got = svc.put_settings(store, {"min_move_pct": 5})
    assert got["model_id"] == "m1" and got["sigma"] == 3.0 and got["min_move_pct"] == 5.0
    assert svc.put_settings(store, {"model_id": None})["model_id"] is None


@pytest.mark.parametrize("patch", [
    {"model_id": "ghost"}, {"sigma": 0.2}, {"sigma": 9}, {"min_move_pct": 0},
    {"min_move_pct": 99}, {"sigma": "abc"}, {"sigma": float("nan")},
])
def test_bad_settings_are_rejected(store, patch):
    with pytest.raises(ValueError):
        svc.put_settings(store, patch)
    assert svc.get_settings(store)["sigma"] == 2.5


def test_a_deleted_model_reads_as_not_configured(store):
    svc.put_settings(store, {"model_id": "m1"})
    store.delete("Models", "m1")
    assert svc.get_settings(store)["model_id"] is None


# ── moves payload ────────────────────────────────────────────────────────────


def test_symbol_and_range_validation():
    assert svc.normalize_symbol(" brk.b ") == "BRK-B"
    for bad in ("", "BTC/USD", "AAPL261218C00200000", "1ABC", "A B"):
        with pytest.raises(ValueError):
            svc.normalize_symbol(bad)
    with pytest.raises(ValueError):
        svc.normalize_range("5Y")


def test_moves_payload_shape_and_flagged_event(store):
    out = svc.moves_payload(store, "nvda", "3M",
                            **_inject(jumps={"NVDA": {JUMP_DAY: 0.09}}))
    assert out["symbol"] == "NVDA" and out["range"] == "3M"
    assert out["sector"] == "technology" and out["sector_etf"] == "XLK"
    assert out["model_configured"] is False and out["pending"] == 0
    assert out["bars"][0]["date"] >= "2026-08-14" and out["bars"][-1]["date"] == LAST
    assert out["bars"] == sorted(out["bars"], key=lambda b: b["date"])
    event = next(e for e in out["events"] if e["date"] == JUMP_DAY)
    assert event["id"] == f"NVDA|{JUMP_DAY}" and event["status"] == "unexplained"
    assert event["category"] is None and event["sources"] == []
    assert event["specific_pct"] > 5
    assert {c["key"] for c in out["categories"]} >= {"earnings", "market", "no_catalyst"}
    assert [e["date"] for e in out["events"]] == sorted(
        (e["date"] for e in out["events"]), reverse=True)


def test_unknown_symbol_is_not_found(store):
    with pytest.raises(svc.NotFound):
        svc.moves_payload(store, "NOPE", "3M", **_inject())


def test_a_price_provider_failure_is_unavailable_not_not_found(store):
    def _down(symbol, start):
        raise RuntimeError("rate limited")

    with pytest.raises(svc.Unavailable):
        svc.moves_payload(store, "NVDA", "3M", price_provider=_down,
                          sector_lookup=lambda s: "technology", now=NOW)


def test_a_market_wide_drop_is_labelled_without_a_model(store):
    out = svc.moves_payload(store, "NVDA", "3M",
                            **_inject(market_drop={JUMP_DAY: -0.06}))
    event = next(e for e in out["events"] if e["date"] == JUMP_DAY)
    assert event["status"] == "explained" and event["category"] == "market"
    assert event["category_label"] == "Market" and event["model_name"] is None


def test_an_unclassified_fund_has_no_sector_part(store):
    out = svc.moves_payload(store, "VTI", "1M", **_inject())
    assert out["sector"] is None and out["sector_etf"] is None
    assert all(b["sector_pct"] in (0.0, None) for b in out["bars"])


def test_an_index_fund_is_split_against_its_own_index(store):
    out = svc.moves_payload(store, "TQQQ", "1M", **_inject())
    assert out["sector"] == "nasdaq_100" and out["sector_etf"] == "QQQ"


# ── explaining ───────────────────────────────────────────────────────────────


def _queue(store, *, answer=None, articles=None, fail=None, spawn=None, **kw):
    ran = []

    def _factory(doc):
        if fail == "setup":
            raise RuntimeError("no key")

        def _call(prompt, system):
            ran.append(prompt)
            if fail == "call":
                raise RuntimeError("provider down")
            return answer or {"category": "analyst", "headline": "Downgrade",
                              "explanation": "A broker cut it.", "confidence": "high",
                              "cited": [1]}
        return _call

    arts = articles if articles is not None else [
        {"title": "Broker cuts NVDA", "summary": "", "url": "https://e.com/a",
         "source": "Benzinga", "published_at": "2026-11-10T13:00:00Z"}]
    out = svc.queue_explain(
        store, "NVDA", "3M", kw.pop("dates", None), kw.pop("force", False),
        spawn=spawn or (lambda fn: fn()),
        articles_fn=lambda s, prev, day: arts, llm_factory=_factory,
        **_inject(jumps={"NVDA": {JUMP_DAY: 0.09}}), **kw)
    return out, ran


def test_explain_requires_a_model(store):
    with pytest.raises(ValueError, match="Choose a model"):
        _queue(store)


def test_explain_stores_the_cited_answer(store):
    svc.put_settings(store, {"model_id": "m1"})
    out, ran = _queue(store, dates=[JUMP_DAY])
    assert out == {"queued": 1} and len(ran) == 1
    row = store.get("MoveEvents", f"NVDA|{JUMP_DAY}")
    assert row["status"] == "explained" and row["model_name"] == "Opus"
    assert row["snapshot"]["specific_pct"] > 5 and row["article_count"] == 1

    payload = svc.moves_payload(store, "NVDA", "3M",
                                **_inject(jumps={"NVDA": {JUMP_DAY: 0.09}}))
    event = next(e for e in payload["events"] if e["date"] == JUMP_DAY)
    assert event["status"] == "explained" and event["category"] == "analyst"
    assert event["category_label"] == "Analysts" and event["model_name"] == "Opus"
    assert event["sources"][0]["url"] == "https://e.com/a"
    assert payload["model_configured"] is True


def test_explained_events_are_skipped_unless_forced(store):
    svc.put_settings(store, {"model_id": "m1"})
    _queue(store, dates=[JUMP_DAY])
    again, ran = _queue(store, dates=[JUMP_DAY])
    assert again == {"queued": 0} and ran == []
    forced, ran = _queue(store, dates=[JUMP_DAY], force=True)
    assert forced == {"queued": 1} and len(ran) == 1


def test_pending_rows_show_as_pending_then_go_stale(store):
    svc.put_settings(store, {"model_id": "m1"})
    out, ran = _queue(store, dates=[JUMP_DAY], spawn=lambda fn: None)  # worker never runs
    assert out == {"queued": 1} and ran == []
    fresh = svc.moves_payload(store, "NVDA", "3M",
                              **_inject(jumps={"NVDA": {JUMP_DAY: 0.09}}))
    assert fresh["pending"] == 1
    # A second request while it is pending does not queue it twice.
    assert _queue(store, dates=[JUMP_DAY], spawn=lambda fn: None)[0] == {"queued": 0}

    store.update("MoveEvents", f"NVDA|{JUMP_DAY}", {
        "pending_since": (NOW.astimezone(timezone.utc) - timedelta(minutes=30))
        .strftime("%Y-%m-%dT%H:%M:%SZ")})
    stale = svc.moves_payload(store, "NVDA", "3M",
                              **_inject(jumps={"NVDA": {JUMP_DAY: 0.09}}))
    event = next(e for e in stale["events"] if e["date"] == JUMP_DAY)
    assert stale["pending"] == 0 and event["status"] == "failed" and event["error"]
    assert _queue(store, dates=[JUMP_DAY])[0] == {"queued": 1}


@pytest.mark.parametrize("fail", ["setup", "call"])
def test_failures_are_stored_and_can_be_retried(store, fail):
    svc.put_settings(store, {"model_id": "m1"})
    out, _ = _queue(store, dates=[JUMP_DAY], fail=fail)
    assert out == {"queued": 1}
    row = store.get("MoveEvents", f"NVDA|{JUMP_DAY}")
    assert row["status"] == "failed" and row["error"]
    assert "secret" not in str(row)
    retried, ran = _queue(store, dates=[JUMP_DAY])
    assert retried == {"queued": 1} and len(ran) == 1


def test_an_uncited_answer_is_stored_as_no_catalyst(store):
    svc.put_settings(store, {"model_id": "m1"})
    _queue(store, dates=[JUMP_DAY], answer={"category": "earnings", "headline": "Beat",
                                            "explanation": "x", "confidence": "high",
                                            "cited": []})
    row = store.get("MoveEvents", f"NVDA|{JUMP_DAY}")
    assert row["status"] == "explained" and row["category"] == "no_catalyst"
    assert row["sources"] == []


def test_dates_that_are_not_flagged_queue_nothing(store):
    svc.put_settings(store, {"model_id": "m1"})
    assert _queue(store, dates=["2026-11-12"])[0] == {"queued": 0}
    assert _queue(store, dates=[])[0] == {"queued": 0}


def test_without_dates_every_unexplained_event_is_queued(store):
    svc.put_settings(store, {"model_id": "m1"})
    payload = svc.moves_payload(store, "NVDA", "3M",
                                **_inject(jumps={"NVDA": {JUMP_DAY: 0.09}}))
    todo = [e for e in payload["events"] if e["status"] == "unexplained"]
    out, ran = _queue(store)
    assert out == {"queued": len(todo)} and len(ran) == len(todo) >= 1


# ── account view ─────────────────────────────────────────────────────────────


def test_portfolio_parts_sum_to_each_contribution(store):
    positions = [{"symbol": "NVDA", "marketValue": 6000.0},
                 {"symbol": "TQQQ", "marketValue": 3000.0},
                 {"symbol": "AAPL261218C00200000", "marketValue": 500.0},
                 {"symbol": "NOPE", "marketValue": 100.0}]
    out = svc.portfolio_payload(store, positions, 1000.0,
                                **_inject(jumps={"NVDA": {LAST: 0.08}}))
    assert out["as_of"] == LAST and out["spy_pct"] is not None
    assert [h["symbol"] for h in out["holdings"]][0] == "NVDA"
    assert {h["symbol"] for h in out["holdings"]} == {"NVDA", "TQQQ"}
    for h in out["holdings"]:
        assert h["market_pct"] + h["sector_pct"] + h["specific_pct"] == pytest.approx(
            h["contribution_pct"], abs=2e-4)
        assert h["contribution_pct"] == pytest.approx(
            h["ret_pct"] * h["weight_pct"] / 100, abs=1e-3)
    nvda = out["holdings"][0]
    # 6000 of 10600: the option and the unpriced holding count toward the total.
    assert nvda["weight_pct"] == pytest.approx(56.6, abs=0.1)
    assert nvda["flagged"] and nvda["event"]["id"] == f"NVDA|{LAST}"
    tqqq = next(h for h in out["holdings"] if h["symbol"] == "TQQQ")
    assert tqqq["event"] is None or tqqq["flagged"]
    assert out["totals"]["contribution_pct"] == pytest.approx(
        sum(h["contribution_pct"] for h in out["holdings"]), abs=1e-3)


def test_an_option_counts_toward_the_account_total_but_is_not_listed(store):
    positions = [{"symbol": "NVDA", "marketValue": 5000.0},
                 {"symbol": "NVDA261218C00200000", "marketValue": 5000.0}]
    out = svc.portfolio_payload(store, positions, 0.0, **_inject())
    assert [h["symbol"] for h in out["holdings"]] == ["NVDA"]
    assert out["holdings"][0]["weight_pct"] == pytest.approx(50.0)


def test_a_long_batch_is_not_read_as_abandoned_and_is_not_run_twice(store, monkeypatch):
    svc.put_settings(store, {"model_id": "m1"})
    jumps = {"NVDA": {JUMP_DAY: 0.09, "2026-11-03": -0.08, "2026-10-20": 0.10}}
    clock = {"now": datetime(2026, 11, 14, 17, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(svc, "_now", lambda now=None: now or clock["now"])
    calls = []

    def _factory(doc):
        def _call(prompt, system):
            calls.append(prompt)
            clock["now"] += timedelta(minutes=6)   # each explanation is slow
            mid = svc.moves_payload(store, "NVDA", "3M", price_provider=_provider(jumps=jumps),
                                    sector_lookup=lambda s: "technology", now=NOW)
            assert not [e for e in mid["events"] if e["status"] == "failed"]
            return {"category": "analyst", "headline": "h", "explanation": "e",
                    "confidence": "low", "cited": [1]}
        return _call

    arts = [{"title": "t", "summary": "", "url": "", "source": "s",
             "published_at": "2026-11-10T13:00:00Z"}]
    out = svc.queue_explain(store, "NVDA", "3M", None, False, spawn=lambda fn: fn(),
                            articles_fn=lambda s, p, d: arts, llm_factory=_factory,
                            price_provider=_provider(jumps=jumps),
                            sector_lookup=lambda s: "technology")
    assert out["queued"] >= 3 and len(calls) == out["queued"]


def test_a_requeued_event_is_not_explained_by_the_older_batch(store):
    svc.put_settings(store, {"model_id": "m1"})
    held = []
    _queue(store, dates=[JUMP_DAY], spawn=held.append)           # batch A, not yet run
    store.update("MoveEvents", f"NVDA|{JUMP_DAY}", {"batch": "someone-else"})
    before = store.get("MoveEvents", f"NVDA|{JUMP_DAY}")
    held[0]()                                                     # batch A runs late
    after = store.get("MoveEvents", f"NVDA|{JUMP_DAY}")
    assert after["status"] == "pending" and after["batch"] == "someone-else"
    assert before["pending_since"] == after["pending_since"]


def test_portfolio_with_no_positions_is_empty(store):
    out = svc.portfolio_payload(store, [], 5000.0, **_inject())
    assert out["as_of"] is None and out["holdings"] == []
    assert svc.portfolio_payload(store, None, None, **_inject())["holdings"] == []


# ── price loading ────────────────────────────────────────────────────────────


def test_todays_unfinished_bar_is_dropped_during_the_session():
    rows = [{"date": "2026-11-12", "close": 1.0, "adj": 1.0, "volume": 1.0},
            {"date": "2026-11-13", "close": 2.0, "adj": 2.0, "volume": 1.0}]
    midday = datetime(2026, 11, 13, 11, 0, tzinfo=px.NY)
    after = datetime(2026, 11, 13, 16, 30, tzinfo=px.NY)
    assert len(px.fetch_daily("X", date(2026, 11, 1), provider=lambda s, d: list(rows),
                              now=midday)) == 1
    assert len(px.fetch_daily("X", date(2026, 11, 1), provider=lambda s, d: list(rows),
                              now=after)) == 2


# ── API wiring ───────────────────────────────────────────────────────────────


def test_endpoints_map_errors_and_pass_only_sent_fields(monkeypatch):
    from fastapi import HTTPException

    from api import main

    seen = {}
    monkeypatch.setattr(svc, "put_settings",
                        lambda store, patch: seen.update(patch=patch) or {"ok": True})
    body = main.MoveAnalysisSettingsBody(model_id=None)
    assert main.api_move_analysis_put_settings(body=body, current_user={"id": "u"}) == {"ok": True}
    assert seen["patch"] == {"model_id": None}
    main.api_move_analysis_put_settings(
        body=main.MoveAnalysisSettingsBody(sigma=3.0), current_user={"id": "u"})
    assert seen["patch"] == {"sigma": 3.0}

    def _missing(store, symbol, range_key):
        raise svc.NotFound("No price history for NOPE.")

    monkeypatch.setattr(svc, "moves_payload", _missing)
    with pytest.raises(HTTPException) as err:
        main.api_move_analysis_moves(symbol="NOPE", range="3M", current_user={"id": "u"})
    assert err.value.status_code == 404

    def _bad(store, symbol, range_key):
        raise ValueError("Invalid range")

    monkeypatch.setattr(svc, "moves_payload", _bad)
    with pytest.raises(HTTPException) as err:
        main.api_move_analysis_moves(symbol="NVDA", range="5Y", current_user={"id": "u"})
    assert err.value.status_code == 400
