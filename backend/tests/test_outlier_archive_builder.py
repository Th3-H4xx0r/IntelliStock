"""Feature preparation invariants; no portfolio or return simulation."""
import sys
from pathlib import Path
from datetime import date, timedelta

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from build_versioned_outlier_features import symbol_rows, rank_session, publish_rows


def bars(prices, volume=1000):
    return [{"t": (date(2020, 1, 1) + timedelta(days=i)).isoformat(),
             "c": p, "v": volume} for i, p in enumerate(prices)]


def test_early_liquid_later_dead_security_is_retained():
    raw = bars([10] * 150 + [.01] * 100)
    rows = symbol_rows("DEAD", raw, raw, "v1", adv_min=9000)
    assert len(rows) == 250
    assert rows[130]["rank_eligible"] is True
    assert rows[-1]["rank_eligible"] is False


def test_future_price_does_not_change_earlier_features_or_rank_eligibility():
    first = bars([10 + i / 100 for i in range(150)])
    future = bars([10 + i / 100 for i in range(150)] + [100] * 30)
    a = symbol_rows("AAA", first, first, "v1", adv_min=9000)
    b = symbol_rows("AAA", future, future, "v1", adv_min=9000)
    assert a == b[:len(a)]


def test_raw_prices_and_volume_control_eligibility_after_future_split():
    raw = bars([10] * 150)
    adjusted = bars([1] * 150, volume=10000)
    row = symbol_rows("AAA", adjusted, raw, "v1", adv_min=9000)[-1]
    assert row["close"] == 1
    assert row["nominal_close"] == 10
    assert row["adv20"] == 10000
    assert row["rank_eligible"] is True


def test_missing_raw_bar_refuses_misaligned_history():
    raw = bars([10] * 150)
    with pytest.raises(ValueError, match="date mismatch"):
        symbol_rows("AAA", raw, raw[:-1], "v1", adv_min=9000)


def test_currently_ineligible_future_winner_does_not_change_past_ranks():
    rows = [{"symbol": "AAA", "ret126": .2, "rank_eligible": True},
            {"symbol": "BBB", "ret126": .4, "rank_eligible": True},
            {"symbol": "LATER", "ret126": 10, "rank_eligible": False}]
    rank_session(rows)
    assert [r["rs_rank"] for r in rows] == [0, 1, None]


def test_equal_returns_receive_equal_ranks():
    rows = [{"symbol": s, "ret126": ret, "rank_eligible": True}
            for s, ret in [("AAA", .1), ("BBB", .1), ("CCC", .3)]]
    rank_session(rows)
    assert rows[0]["rs_rank"] == rows[1]["rs_rank"] == .25


def test_completed_dataset_is_immutable(store):
    store.insert("PointInTimeDatasetSnapshots", {"id": "outlier:v1", "complete": True})
    with pytest.raises(ValueError, match="already exists"):
        publish_rows(store, "v1", [], {"build_id": "new"})


def test_manifest_completes_only_after_successful_rows(store):
    def failing():
        yield [{"id": "v1|2026-01-02|AAA", "date": "2026-01-02", "symbol": "AAA"}]
        raise RuntimeError("source failed")
    with pytest.raises(RuntimeError, match="source failed"):
        publish_rows(store, "v1", failing(), {"build_id": "abc"})
    assert store.get("PointInTimeDatasetSnapshots", "outlier:v1")["complete"] is False


def test_long_observed_history_gap_restarts_features_without_changing_prior_rows():
    first = bars([10] * 150)
    second = [{**b, "t": (date(2025, 1, 1) + timedelta(days=i)).isoformat()}
              for i, b in enumerate(bars([100] * 130))]
    actual = symbol_rows("REUSED", first + second, first + second, "v1", adv_min=9000)
    assert actual[:150] == symbol_rows("REUSED", first, first, "v1", adv_min=9000)
    assert actual[150]["n_bars"] == 1
    assert actual[150]["ret126"] is None
    assert actual[150]["first_bar"] == "2025-01-01"


def test_consolidated_liquidity_preserves_native_prices_and_does_not_use_future_volume():
    native = bars([10] * 150, volume=100)
    consolidated = bars([11] * 150, volume=2000000)
    rows = symbol_rows('ABC', native, native, 'sip', liquidity=consolidated)
    assert rows[-1]['adv20'] == 22000000
    assert rows[-1]['nominal_close'] == rows[-1]['close'] == 10
    assert rows[-1]['rank_eligible'] is True
    changed = [*consolidated[:-1], {**consolidated[-1], 'v': 9000000}]
    later = symbol_rows('ABC', native, native, 'sip', liquidity=changed)
    assert rows[:-1] == later[:-1]


def test_missing_consolidated_session_blocks_entry_but_retains_exit_history():
    native = bars([10] * 150, volume=100)
    consolidated = bars([11] * 149, volume=2000000)
    rows = symbol_rows('ABC', native, native, 'sip', liquidity=consolidated)
    assert len(rows) == 150
    assert rows[-1]['adv20'] == 0
    assert rows[-1]['rank_eligible'] is False
    assert rows[-1]['close'] == 10


def test_consolidated_adv_uses_consolidated_sessions_including_native_gaps():
    consolidated = bars([10] * 151, volume=2000000)
    consolidated[-21]['v'] = 100000000
    native = [b for i,b in enumerate(consolidated) if i != 149]
    rows = symbol_rows('ABC', native, native, 'sip', liquidity=consolidated)
    assert rows[-1]['adv20'] == 20000000


def test_consolidated_liquidity_requires_twenty_observed_sessions():
    native = bars([10] * 150, volume=100)
    consolidated = bars([11] * 150, volume=2000000)[-19:]
    assert symbol_rows('ABC', native, native, 'sip', liquidity=consolidated) == []


def test_preparation_binds_consolidated_source_and_reads_its_batches(tmp_path):
    import gzip
    import hashlib
    import json
    from build_versioned_outlier_features import prepare
    archive, sip, output = [tmp_path / name for name in ('iex', 'sip', 'features')]
    archive.mkdir(); sip.mkdir()
    source = archive / 'manifest.json'
    source.write_text(json.dumps({'symbols': 1, 'limitations': []}))
    manifest = {'symbols': 1, 'feed': 'sip', 'adjustment': 'raw', 'complete': True,
                'source_manifest_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    (sip / 'manifest.json').write_text(json.dumps(manifest))
    for folder, adjustment, data in [(archive, 'raw', bars([10] * 150, volume=100)),
                                     (archive, 'split', bars([10] * 150, volume=100)),
                                     (sip, 'raw', bars([11] * 150, volume=2000000))]:
        with gzip.open(folder / f'00000-{adjustment}.json.gz', 'wt') as stream:
            json.dump({'requested_symbols': ['ABC'], 'bars': {'ABC': data}}, stream)
    assert prepare(archive, output, 'sip', '2020-01-01', sip)
    rows = [json.loads(line) for line in gzip.open(output / 'rows.jsonl.gz', 'rt')]
    assert len(rows) == 150 and rows[-1]['adv20'] == 22000000 and rows[-1]['rs_rank'] == 1
    assert json.loads((output / 'metadata.json').read_text())['source_settings']['liquidity_archive'] == manifest
    manifest['source_manifest_sha256'] = 'wrong'
    (sip / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='source mismatch'):
        prepare(archive, tmp_path / 'wrong', 'wrong', '2020-01-01', sip)
