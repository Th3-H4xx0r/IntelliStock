"""TR Alpaca (Benzinga) news backfill (Task 2) -- fake HTTP, never the network.

Each day is paged ascending, 50 per call, until Alpaca stops returning a
``next_page_token``; every article lands in TrendNewsPIT keyed by its
availability (ruling R5: the later of ``created_at`` and ``updated_at``,
because the content fetched is the latest edit). A re-run resumes each day
from the newest article already stored. Credentials come only from a paper
brokerage.
"""
from __future__ import annotations

import os
import sys
from datetime import date

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from stock_credential_boundary import StockAlpacaCredentials  # noqa: E402
from trend_research import ingest_alpaca_news as ian  # noqa: E402

DAY = date(2026, 3, 2)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


def article(n, *, created="2026-03-02T14:00:00Z", updated=None, **extra):
    item = {"id": n, "author": "Benzinga Newsdesk", "created_at": created,
            "updated_at": updated or created, "headline": "headline %d" % n,
            "summary": "summary %d" % n, "content": "<p>body %d</p>" % n,
            "images": [], "source": "benzinga", "symbols": ["nvda", "AAPL"],
            "url": "https://www.benzinga.com/%d" % n}
    item.update(extra)
    return item


def stamp(n):
    """A distinct second on DAY for article ``n`` (n < 3600)."""
    return "2026-03-02T%02d:%02d:%02dZ" % (10 + n // 3600, (n // 60) % 60, n % 60)


class FakeHttp:
    """Serves ``pages`` in order; records every call's url/params/headers."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, *, params, headers, timeout):
        self.calls.append({"url": url, "params": dict(params),
                           "headers": dict(headers), "timeout": timeout})
        if not self.responses:
            return FakeResponse(200, {"news": [], "next_page_token": None})
        return self.responses.pop(0)


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def pages_of(articles, size=50):
    chunks = [articles[i:i + size] for i in range(0, len(articles), size)]
    out = []
    for i, chunk in enumerate(chunks):
        token = "tok%d" % (i + 1) if i + 1 < len(chunks) else None
        out.append(FakeResponse(200, {"news": chunk, "next_page_token": token}))
    return out


def run(store, http, start=DAY, end=DAY, clock=None):
    clock = clock or FakeClock()
    return ian.backfill_news(start, end, key="K-test", secret="S-test",
                             http_get=http, sleep=clock.sleep, clock=clock.clock,
                             store=store)


def stored(store):
    return list(store.iter(store.between("TrendNewsPIT", "0", "~")))


# -- pagination and request shape ----------------------------------------------

def test_paginates_until_no_token(store):
    arts = [article(n, created=stamp(n)) for n in range(120)]
    http = FakeHttp(pages_of(arts))
    assert run(store, http) == 120
    assert len(stored(store)) == 120
    assert [c["params"].get("page_token") for c in http.calls] == [None, "tok1", "tok2"]


def test_requests_ascending_and_limit_50(store):
    http = FakeHttp(pages_of([article(1, created=stamp(1))]))
    run(store, http)
    call = http.calls[0]
    assert call["url"] == "https://data.alpaca.markets/v1beta1/news"
    p = call["params"]
    assert p["sort"] == "asc"
    assert p["limit"] == 50
    assert p["include_content"] == "true"
    assert p["start"] == "2026-03-02T00:00:00Z"
    assert p["end"] == "2026-03-03T00:00:00Z"
    assert "symbols" not in p
    assert call["headers"] == {"APCA-API-KEY-ID": "K-test",
                               "APCA-API-SECRET-KEY": "S-test"}


def test_walks_each_day_of_an_inclusive_range(store):
    http = FakeHttp([])
    run(store, http, start=date(2026, 3, 1), end=date(2026, 3, 3))
    assert [c["params"]["start"] for c in http.calls] == [
        "2026-03-01T00:00:00Z", "2026-03-02T00:00:00Z", "2026-03-03T00:00:00Z"]


def test_rejects_an_inverted_range(store):
    with pytest.raises(ValueError):
        run(store, FakeHttp([]), start=date(2026, 3, 3), end=date(2026, 3, 1))


def test_repeated_page_token_is_an_error_not_a_loop(store):
    looping = [FakeResponse(200, {"news": [article(n, created=stamp(n))],
                                  "next_page_token": "same"}) for n in range(3)]
    with pytest.raises(RuntimeError, match="page token"):
        run(store, FakeHttp(looping))


# -- rate limiting and retries ---------------------------------------------------

def test_paces_requests_at_or_under_200_per_minute(store):
    arts = [article(n, created=stamp(n)) for n in range(500)]
    clock = FakeClock()
    times = []
    http = FakeHttp(pages_of(arts))
    inner = http.__call__

    def timed(url, **kw):
        times.append(clock.now)
        return inner(url, **kw)

    run(store, timed, clock=clock)
    assert len(times) == 10
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert min(gaps) >= 60.0 / 200


def test_backs_off_on_429(store):
    http = FakeHttp([FakeResponse(429, None, headers={"Retry-After": "7"}),
                     *pages_of([article(1, created=stamp(1))])])
    clock = FakeClock()
    assert run(store, http, clock=clock) == 1
    assert len(http.calls) == 2
    assert 7 in clock.sleeps
    assert len(stored(store)) == 1


def test_429_without_retry_after_backs_off_exponentially(store):
    http = FakeHttp([FakeResponse(429), FakeResponse(503),
                     *pages_of([article(1, created=stamp(1))])])
    clock = FakeClock()
    assert run(store, http, clock=clock) == 1
    backoffs = [s for s in clock.sleeps if s >= 1]
    assert backoffs == [2.0, 4.0]


class BadJsonResponse(FakeResponse):
    def json(self):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


def test_malformed_json_on_200_is_retried(store):
    http = FakeHttp([BadJsonResponse(200),
                     *pages_of([article(1, created=stamp(1))])])
    clock = FakeClock()
    assert run(store, http, clock=clock) == 1
    assert len(http.calls) == 2
    assert 2.0 in clock.sleeps


def test_malformed_json_retry_is_bounded(store):
    http = FakeHttp([BadJsonResponse(200) for _ in range(50)])
    with pytest.raises(RuntimeError, match="malformed JSON"):
        run(store, http)
    assert len(http.calls) == ian.MAX_ATTEMPTS


def test_gives_up_after_bounded_retries(store):
    http = FakeHttp([FakeResponse(429) for _ in range(50)])
    with pytest.raises(RuntimeError, match="429"):
        run(store, http)
    assert len(http.calls) == ian.MAX_ATTEMPTS


def test_client_error_raises_without_leaking_credentials(store):
    http = FakeHttp([FakeResponse(400, None, text="limit larger than 50")])
    with pytest.raises(RuntimeError) as exc:
        run(store, http)
    assert "400" in str(exc.value)
    assert "K-test" not in str(exc.value) and "S-test" not in str(exc.value)
    assert len(http.calls) == 1


def test_network_error_is_retried(store):
    calls = {"n": 0}
    ok = pages_of([article(1, created=stamp(1))])

    def flaky(url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("reset")
        return ok.pop(0)

    assert run(store, flaky) == 1


# -- row shape and availability --------------------------------------------------

def test_row_fields(store):
    http = FakeHttp(pages_of([article(42, created="2026-03-02T14:00:05Z",
                                      symbols=[" nvda", "AAPL", "NVDA", ""])]))
    run(store, http)
    (row,) = stored(store)
    assert row["id"] == "2026-03-02T14:00:05Z|alpaca|42"
    assert row["source"] == "alpaca"
    assert row["source_id"] == "42"
    assert row["published_at"] == "2026-03-02T14:00:05Z"
    assert row["available_at"] == "2026-03-02T14:00:05Z"
    assert row["headline"] == "headline 42"
    assert row["summary"] == "summary 42"
    assert row["content"] == "<p>body 42</p>"
    assert row["symbols"] == ["NVDA", "AAPL"]
    assert row["url"] == "https://www.benzinga.com/42"
    assert row["author"] == "Benzinga Newsdesk"


def test_missing_content_and_symbols_store_empty(store):
    http = FakeHttp(pages_of([article(1, created=stamp(1), content=None,
                                      symbols=None, summary=None)]))
    run(store, http)
    (row,) = stored(store)
    assert row["content"] == "" and row["summary"] == "" and row["symbols"] == []


def test_available_at_is_updated_at_when_later(store):
    # R5: the fetched content is the latest edit, so it is not available
    # before the edit. published_at stays the original created_at.
    http = FakeHttp(pages_of([article(7, created="2026-03-02T14:00:00Z",
                                      updated="2026-03-02T16:30:00Z")]))
    run(store, http)
    (row,) = stored(store)
    assert row["published_at"] == "2026-03-02T14:00:00Z"
    assert row["available_at"] == "2026-03-02T16:30:00Z"
    assert row["id"] == "2026-03-02T16:30:00Z|alpaca|7"


def test_available_at_ignores_an_earlier_updated_at(store):
    http = FakeHttp(pages_of([article(8, created="2026-03-02T14:00:00Z",
                                      updated="2026-03-02T13:00:00Z")]))
    run(store, http)
    (row,) = stored(store)
    assert row["available_at"] == "2026-03-02T14:00:00Z"


def test_sub_second_stamps_round_availability_up_never_down(store):
    http = FakeHttp(pages_of([article(9, created="2026-03-02T14:29:59.400Z")]))
    run(store, http)
    (row,) = stored(store)
    assert row["available_at"] == "2026-03-02T14:30:00Z"
    assert row["published_at"] == "2026-03-02T14:29:59Z"


@pytest.mark.parametrize("bad", [
    {"created_at": None}, {"created_at": "yesterday"},
    {"created_at": "2026-03-02T14:00:00"},            # naive: unprovable zone
    {"updated_at": "garbage"}, {"id": None},
])
def test_articles_with_unprovable_availability_are_skipped(store, bad):
    good = article(1, created=stamp(1))
    broken = article(2, created=stamp(2))
    broken.update(bad)
    http = FakeHttp(pages_of([good, broken]))
    assert run(store, http) == 1
    assert [r["source_id"] for r in stored(store)] == ["1"]


def test_content_and_summary_are_capped(store):
    http = FakeHttp(pages_of([article(1, created=stamp(1), content="c" * 9000,
                                      summary="s" * 3000)]))
    run(store, http)
    (row,) = stored(store)
    assert row["content"] == "c" * 4000
    assert row["summary"] == "s" * 1000


def test_short_content_and_summary_are_untouched(store):
    http = FakeHttp(pages_of([article(1, created=stamp(1), content="c" * 4000,
                                      summary="s" * 1000)]))
    run(store, http)
    (row,) = stored(store)
    assert len(row["content"]) == 4000 and len(row["summary"]) == 1000


def test_warns_when_most_of_a_day_is_unparseable(store, caplog):
    arts = [article(1, created=stamp(1))] + [
        article(n, created="garbage") for n in range(2, 5)]
    with caplog.at_level("WARNING", logger=ian.__name__):
        assert run(store, FakeHttp(pages_of(arts))) == 1
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("3 of 4" in r.getMessage() and "2026-03-02" in r.getMessage()
               for r in warnings)


@pytest.mark.parametrize("bad", [1, 2])     # 1 of 3, and exactly half (2 of 4)
def test_no_warning_at_or_under_half_skipped(store, caplog, bad):
    arts = [article(n, created=stamp(n)) for n in range(1, 3)] + [
        article(n, created="garbage") for n in range(10, 10 + bad)]
    with caplog.at_level("WARNING", logger=ian.__name__):
        run(store, FakeHttp(pages_of(arts)))
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_put_rows_failure_propagates(store, monkeypatch):
    def boom(table, rows, **kw):
        raise ian.StoreError("rejected")
    monkeypatch.setattr(ian, "put_rows", boom)
    with pytest.raises(ian.StoreError):
        run(store, FakeHttp(pages_of([article(1, created=stamp(1))])))


# -- resume (ruling R10: the newest stored day is refetched whole) ---------------

def alpaca_row(source_id, published, available=None):
    available = available or published
    return {"id": "%s|alpaca|%s" % (available, source_id), "source": "alpaca",
            "source_id": source_id, "published_at": published,
            "available_at": available}


def test_resume_from_latest_stored(store):
    # Days before the newest stored day are complete; that day restarts at
    # 00:00Z (no mid-day frontier); later days are fetched normally.
    ian.put_rows("TrendNewsPIT", [alpaca_row("1", "2026-03-01T15:00:00Z"),
                                  alpaca_row("2", "2026-03-02T11:00:00Z")], store=store)
    http = FakeHttp([])
    run(store, http, start=date(2026, 3, 1), end=date(2026, 3, 3))
    assert [c["params"]["start"] for c in http.calls] == [
        "2026-03-02T00:00:00Z", "2026-03-03T00:00:00Z"]


def test_crash_mid_day_rerun_refetches_the_day_from_midnight(store):
    # Alpaca's asc order may follow updated_at: article #3 was created early
    # but edited late, so it sits on page 2 behind later-created articles.
    # A published_at frontier would resume after 12:00 and lose it forever.
    page1 = [article(n, created="2026-03-02T12:00:%02dZ" % n) for n in range(1, 3)]
    late_edit = article(3, created="2026-03-02T09:00:00Z", updated="2026-03-02T13:00:00Z")
    crash = FakeHttp([FakeResponse(200, {"news": page1, "next_page_token": "p2"}),
                      FakeResponse(400, None, text="boom")])
    with pytest.raises(RuntimeError):
        run(store, crash)
    assert sorted(r["source_id"] for r in stored(store)) == ["1", "2"]

    rerun = FakeHttp([FakeResponse(200, {"news": page1, "next_page_token": "p2"}),
                      FakeResponse(200, {"news": [late_edit], "next_page_token": None})])
    assert run(store, rerun) == 1
    assert rerun.calls[0]["params"]["start"] == "2026-03-02T00:00:00Z"
    assert "page_token" not in rerun.calls[0]["params"]
    assert sorted(r["source_id"] for r in stored(store)) == ["1", "2", "3"]


def test_nothing_stored_fetches_every_day(store):
    http = FakeHttp([])
    run(store, http, start=date(2026, 3, 1), end=date(2026, 3, 2))
    assert [c["params"]["start"] for c in http.calls] == [
        "2026-03-01T00:00:00Z", "2026-03-02T00:00:00Z"]


def test_resume_ignores_other_sources(store):
    ian.put_rows("TrendNewsPIT", [
        alpaca_row("1", "2026-03-01T15:00:00Z"),
        {"id": "2026-03-03T20:00:00Z|gdelt|abc", "source": "gdelt", "source_id": "abc",
         "published_at": "2026-03-03T20:00:00Z", "available_at": "2026-03-03T20:00:00Z"},
    ], store=store)
    http = FakeHttp([])
    run(store, http, start=date(2026, 3, 1), end=date(2026, 3, 3))
    assert http.calls[0]["params"]["start"] == "2026-03-01T00:00:00Z"
    assert len(http.calls) == 3


def test_resume_day_is_the_published_day_not_the_edit_day(store):
    # Published late on 03-02, edited into 03-03: the interrupted day was
    # 03-02 (or earlier), never 03-03, so 03-02 is refetched.
    ian.put_rows("TrendNewsPIT", [
        alpaca_row("5", "2026-03-02T23:50:00Z", available="2026-03-03T01:00:00Z"),
    ], store=store)
    http = FakeHttp([])
    run(store, http, start=date(2026, 3, 1), end=date(2026, 3, 3))
    assert [c["params"]["start"] for c in http.calls] == [
        "2026-03-02T00:00:00Z", "2026-03-03T00:00:00Z"]


# -- credentials -------------------------------------------------------------------

def creds(paper):
    return StockAlpacaCredentials(brokerage_id="b1", key="K", secret="S",
                                  paper=paper, data_feed="iex")


def seed_instance(store, *, paper=True, instance_id="alpaca-paper-fwd"):
    store.insert("Instances", {"id": instance_id, "kind": "equity",
                               "brokerage_id": "b-trade",
                               "alpaca_data_brokerage_id": "b1"})
    store.insert("BrokerageAccounts", {"id": "b1", "brokerage_type": "alpaca",
                                       "alpaca_paper": paper})


def test_refuses_live_brokerage(store):
    seed_instance(store, paper=True)
    with pytest.raises(RuntimeError, match="paper"):
        ian.resolve_paper_credentials(store=store, resolver=lambda *a, **k: creds(False))


def test_refuses_a_brokerage_row_not_marked_paper(store):
    seed_instance(store, paper=False)
    called = []

    def resolver(*a, **k):
        called.append(1)
        return creds(True)

    with pytest.raises(RuntimeError, match="paper"):
        ian.resolve_paper_credentials(store=store, resolver=resolver)
    assert not called


def test_refuses_the_live_instance_by_name(store):
    seed_instance(store, paper=True, instance_id="alpaca-main")
    with pytest.raises(RuntimeError):
        ian.resolve_paper_credentials("alpaca-main", store=store,
                                      resolver=lambda *a, **k: creds(True))


def test_missing_instance_is_refused(store):
    with pytest.raises(RuntimeError):
        ian.resolve_paper_credentials(store=store, resolver=lambda *a, **k: creds(True))


def test_resolves_the_data_link_of_a_paper_instance(store):
    seed_instance(store, paper=True)
    seen = {}

    def resolver(inst, brokerage, *, data):
        seen.update(inst=inst["id"], brokerage=brokerage["id"], data=data)
        return creds(True)

    assert ian.resolve_paper_credentials(store=store, resolver=resolver) == ("K", "S")
    assert seen == {"inst": "alpaca-paper-fwd", "brokerage": "b1", "data": True}


def test_cli_resolves_paper_credentials_then_backfills(monkeypatch, capsys):
    got = {}
    monkeypatch.setattr(ian, "resolve_paper_credentials",
                        lambda instance_id: got.setdefault("inst", instance_id)
                        and ("KEY-XYZ", "SECRET-XYZ"))

    def fake_backfill(start, end, *, key, secret):
        got.update(start=start, end=end, key=key, secret=secret)
        return 3

    monkeypatch.setattr(ian, "backfill_news", fake_backfill)
    assert ian.main(["--start", "2025-01-01", "--end", "2026-11-05"]) == 0
    assert got == {"inst": "alpaca-paper-fwd", "start": date(2025, 1, 1),
                   "end": date(2026, 11, 5), "key": "KEY-XYZ", "secret": "SECRET-XYZ"}
    out = capsys.readouterr()
    assert "KEY-XYZ" not in out.out + out.err
    assert "SECRET-XYZ" not in out.out + out.err
    assert "3" in out.out
