"""Feature table: pure feature math, cross-sectional ranks, and the PIT reader."""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from outlier_features import (  # noqa: E402
    FEATURES_TABLE, PEERS_TABLE, feature_id, compute_features,
    rank_cross_section, cross_section, visible_dates, peers_for,
)


def test_tables_are_registered_with_a_prefix_index_on_id():
    from db import schema
    assert FEATURES_TABLE in schema.ALL_TABLES and PEERS_TABLE in schema.ALL_TABLES
    assert schema.TABLES[FEATURES_TABLE].prefix_fields == ("id",)


def test_feature_id_is_date_pipe_upper_symbol():
    assert feature_id("2026-06-03", "sndk") == "2026-06-03|SNDK"


def test_compute_features_trailing_windows_are_inclusive_and_pit_safe():
    n = 260
    dates = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]
    closes = [float(i + 1) for i in range(n)]        # rising 1..260
    volumes = [1000.0] * n
    rows = compute_features(closes, volumes, dates)
    assert len(rows) == n
    last = rows[-1]
    assert last["date"] == dates[-1] and last["close"] == 260.0
    assert last["hi252"] == 260.0                    # includes this session
    assert last["ret126"] == 260.0 / 134.0 - 1.0
    assert last["n_bars"] == 260 and last["first_bar"] == dates[0]
    assert abs(last["sma200"] - sum(range(61, 261)) / 200.0) < 1e-9
    assert abs(last["adv20"] - sum(range(241, 261)) / 20.0 * 1000.0) < 1e-6
    young = rows[100]
    assert young["ret126"] is None and young["sma200"] is None
    assert young["hi252"] == 101.0 and young["n_bars"] == 101


def test_rank_cross_section_is_a_percentile_among_liquid_rows_only():
    rows = [{"date": "d", "symbol": s, "ret126": r, "adv20": a}
            for s, r, a in (("A", 0.1, 1e8), ("B", 0.5, 1e8), ("C", 0.9, 1e8),
                            ("D", 2.0, 1e5), ("E", None, 1e8))]
    out = {r["symbol"]: r["rs_rank"] for r in rank_cross_section(rows, adv_min=1e7)}
    assert out["A"] == 0.0 and out["B"] == 0.5 and out["C"] == 1.0
    assert out["D"] is None and out["E"] is None


def test_reader_returns_one_dates_cross_section_and_visible_dates(store):
    docs = []
    for d in ("2026-06-01", "2026-06-02", "2026-06-03"):
        for s in ("AAA", "BBB"):
            docs.append({"id": feature_id(d, s), "date": d, "symbol": s, "close": 1.0})
    store.insert(FEATURES_TABLE, docs, conflict="replace")
    rows = cross_section(store, "2026-06-02")
    assert sorted(r["symbol"] for r in rows) == ["AAA", "BBB"]
    assert all(r["date"] == "2026-06-02" for r in rows)
    assert visible_dates(store, "2026-06-03") == ["2026-06-01", "2026-06-02"]
    assert visible_dates(store, "2026-06-04") == ["2026-06-01", "2026-06-02", "2026-06-03"]


def test_peers_for_reads_the_exported_sets(store):
    store.insert(PEERS_TABLE, [{"id": "AAA", "sector": "Technology", "peers": ["P1", "P2"]}],
                 conflict="replace")
    assert peers_for(store, ["AAA", "ZZZ"]) == {"AAA": ["P1", "P2"]}
