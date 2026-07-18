from nexus_telemetry import (
    summarize_outcomes,
    normalize_backfill_item,
    newest_watchlist,
    dedupe_latest_contexts,
)


def test_summarize_outcomes_empty():
    s = summarize_outcomes([])
    assert s == {"hit_rate": 0.0, "n": 0, "n_correct": 0, "avg_return": 0.0,
                 "recent": [], "data_status": "legacy_untrusted"}


def test_summarize_outcomes_hit_rate_and_direction():
    docs = [
        {"symbol": "A", "action_intent": "buy", "latest_return": 5.0,
         "latest_observation_date": "2026-06-10", "entry_date": "2026-06-01"},
        {"symbol": "B", "action_intent": "buy", "latest_return": -2.0,
         "latest_observation_date": "2026-06-11", "entry_date": "2026-06-02"},
        {"symbol": "C", "action_intent": "sell", "latest_return": -3.0,
         "latest_observation_date": "2026-06-12", "entry_date": "2026-06-03"},
        {"symbol": "D", "action_intent": "backfill_rotation_buy", "latest_return": 1.0,
         "latest_observation_date": "2026-06-13", "entry_date": "2026-06-04"},
    ]
    s = summarize_outcomes(docs)
    # buy+pos=correct, buy+neg=wrong, sell+neg=correct, *buy+pos=correct -> 3/4
    assert s["n"] == 4
    assert s["n_correct"] == 3
    assert abs(s["hit_rate"] - 0.75) < 1e-9
    assert abs(s["avg_return"] - 0.25) < 1e-9
    # recent sorted by latest_observation_date desc, newest first
    assert [r["symbol"] for r in s["recent"]][:2] == ["D", "C"]


def test_normalize_backfill_item_uses_real_cache_keys():
    # Real _backfill_queue items use signal_source + is_watchlist_priority,
    # NOT source/priority. The normalizer must read the real keys.
    a = normalize_backfill_item({"ticker": "nvda", "raw_net_score": 1.4, "n_paths": 3,
                                 "signal_source": "propagation", "is_watchlist_priority": True})
    assert a == {"ticker": "NVDA", "score": 1.4, "n_paths": 3,
                 "source": "propagation", "priority": True}
    # is_propagation_expansion also marks priority.
    assert normalize_backfill_item(
        {"ticker": "t", "is_propagation_expansion": True})["priority"] is True
    # Defaults + legacy source/score forms still tolerated.
    b = normalize_backfill_item({"ticker": "amd", "score": 0.9})
    assert b["ticker"] == "AMD" and b["score"] == 0.9 and b["n_paths"] == 0
    assert b["source"] == "" and b["priority"] is False
    c = normalize_backfill_item({"ticker": "x", "source": "momentum", "priority": 1})
    assert c["source"] == "momentum" and c["priority"] is True


def test_newest_watchlist_sorts_caps_and_reads_first_seen_price():
    # Real _momentum_watchlist meta holds first_seen_bar + first_seen_price only.
    wl = {
        "AAA": {"first_seen_bar": 10, "first_seen_price": 1.1},
        "BBB": {"first_seen_bar": 30, "first_seen_price": 2.2},
        "CCC": {"first_seen_bar": 20, "first_seen_price": 3.3},
    }
    out = newest_watchlist(wl, limit=2)
    assert [e["symbol"] for e in out] == ["BBB", "CCC"]
    assert out[0] == {"symbol": "BBB", "first_seen_bar": 30, "first_seen_price": 2.2}


def test_dedupe_latest_contexts_keeps_first_per_symbol_and_truncates_reason():
    docs = [
        {"symbol": "NVDA", "reason": "x" * 400, "dominant_event_type": "supply_disruption",
         "action_intent": "buy", "score": 3.0, "date_key": "2026-06-15"},
        {"symbol": "NVDA", "reason": "older", "dominant_event_type": "general",
         "action_intent": "hold", "score": 1.0, "date_key": "2026-06-10"},
        {"symbol": "AMD", "reason": "peer", "dominant_event_type": "m_and_a",
         "action_intent": "buy", "score": 2.0, "date_key": "2026-06-14"},
    ]
    out = dedupe_latest_contexts(docs, limit=10)
    assert [c["symbol"] for c in out] == ["NVDA", "AMD"]
    assert len(out[0]["reason"]) == 240
    assert out[1]["dominant_event_type"] == "m_and_a"
