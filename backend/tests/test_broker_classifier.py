"""Unit tests for the WAL-based broker-position classifier and the
list_filled_for_prefix query method.

Schema we consume (per backend/broker_adapters/_wal.py + broker.py production
WAL store at backend/nexus_runtime_state.py:WALStore):
  - client_order_id (primary key, also used as the row id)
  - symbol
  - side ('BUY' / 'SELL')
  - state ('intent' | 'submitted' | 'accepted' | 'partial' | 'filled' | ...)
  - filled_qty
  - filled_avg_price
  - updated_at_utc (ISO string)

CID prefix for instance "main" is the first 8 alphanumeric chars of the
instance id + '-', so 'main-' per backend/broker_adapters/_client_order_id.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


class _FakeWAL:
    """In-process WAL with the same query surface as LiveOrderWAL.list_filled_for_prefix.

    Tests give this a list of WAL row dicts; the classifier calls
    list_filled_for_prefix(prefix, since_utc) and we filter accordingly.
    """

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def list_filled_for_prefix(
        self,
        cid_prefix: str,
        since_utc: str | None = None,
    ) -> list[dict]:
        out: list[dict] = []
        for r in self._rows:
            if not (r.get("client_order_id") or "").startswith(cid_prefix):
                continue
            if not r.get("filled_qty"):
                continue
            if since_utc is not None:
                ts = r.get("updated_at_utc") or r.get("created_at_utc")
                if ts is not None and ts < since_utc:
                    continue
            out.append(r)
        return out


def test_fake_wal_filters_by_prefix_and_time():
    """Sanity test for the test stub itself (so subsequent tests are trustworthy)."""
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    old = (now - timedelta(days=400)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()
    rows = [
        {"client_order_id": "main-aaa-0", "symbol": "TSLA", "side": "BUY", "state": "filled",
         "filled_qty": 10.0, "filled_avg_price": 200.0, "updated_at_utc": recent},
        {"client_order_id": "other-xyz-0", "symbol": "AAPL", "side": "BUY", "state": "filled",
         "filled_qty": 5.0, "filled_avg_price": 180.0, "updated_at_utc": recent},
        {"client_order_id": "main-bbb-0", "symbol": "TSLA", "side": "BUY", "state": "canceled",
         "filled_qty": 0.0, "filled_avg_price": None, "updated_at_utc": recent},
        {"client_order_id": "main-ccc-0", "symbol": "TSLA", "side": "BUY", "state": "filled",
         "filled_qty": 7.0, "filled_avg_price": 100.0, "updated_at_utc": old},
    ]
    wal = _FakeWAL(rows)
    out = wal.list_filled_for_prefix("main-", since_utc=(now - timedelta(days=180)).isoformat())
    assert len(out) == 1
    assert out[0]["client_order_id"] == "main-aaa-0"


def test_walstore_has_list_filled_for_prefix():
    """Production WALStore in nexus_runtime_state.py must expose the query method."""
    import inspect
    from nexus_runtime_state import WALStore
    assert hasattr(WALStore, "list_filled_for_prefix"), (
        "WALStore.list_filled_for_prefix is required for clean-room classifier"
    )
    sig = inspect.signature(WALStore.list_filled_for_prefix)
    params = list(sig.parameters)
    assert "cid_prefix" in params
    assert "since_utc" in params


def test_live_order_wal_has_list_filled_for_prefix_delegate():
    """LiveOrderWAL exposes list_filled_for_prefix that delegates to the store."""
    from broker_adapters._wal import LiveOrderWAL, InMemoryStore
    store = InMemoryStore()
    wal = LiveOrderWAL(store)
    assert hasattr(wal, "list_filled_for_prefix")
    # InMemoryStore implements it; default returns empty list when no rows
    assert wal.list_filled_for_prefix("main-") == []


def test_inmemory_store_list_filled_for_prefix():
    """InMemoryStore implements list_filled_for_prefix matching WALStore's contract."""
    from broker_adapters._wal import InMemoryStore
    store = InMemoryStore()
    store.insert({
        "client_order_id": "main-aaa-0", "symbol": "TSLA", "side": "BUY",
        "state": "filled", "filled_qty": 10.0, "filled_avg_price": 200.0,
        "updated_at_utc": "2026-05-20T00:00:00+00:00",
    })
    store.insert({
        "client_order_id": "other-xyz-0", "symbol": "AAPL", "side": "BUY",
        "state": "filled", "filled_qty": 5.0, "filled_avg_price": 180.0,
        "updated_at_utc": "2026-05-20T00:00:00+00:00",
    })
    rows = store.list_filled_for_prefix("main-")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "TSLA"


def test_base_adapter_has_external_positions_default():
    """The ABC must declare _external_positions and a default
    get_external_positions() that returns {}."""
    from broker_adapters.base import BrokerAdapter
    assert hasattr(BrokerAdapter, "get_external_positions"), \
        "BrokerAdapter must expose get_external_positions()"
    import inspect
    method = inspect.getattr_static(BrokerAdapter, "get_external_positions")
    assert method is not None
    # _external_positions is an annotated class attribute (not in __dict__)
    assert "_external_positions" in BrokerAdapter.__annotations__


def _pos(symbol: str, qty: float, mv: float = 0.0):
    """Helper: build a PositionDTO-shaped object for classifier tests."""
    from broker_adapters.base import PositionDTO
    return PositionDTO(symbol=symbol, qty=qty, avg_entry_price=0.0, market_value=mv)


def _wal_row(cid: str, symbol: str, side: str, filled_qty: float,
             filled_avg_price: float, ts_utc: datetime, state: str = "filled") -> dict:
    return {
        "client_order_id": cid,
        "symbol": symbol,
        "side": side,
        "state": state,
        "filled_qty": filled_qty,
        "filled_avg_price": filled_avg_price,
        "updated_at_utc": ts_utc.isoformat(),
        "broker_order_id": f"bx-{cid}",
    }


def test_derive_cid_prefix_handles_special_chars():
    """Mirrors broker_adapters/_client_order_id.py::_safe(instance, 8):
    strip non-alphanumerics, then truncate to 8."""
    from broker_adapters._classifier import derive_cid_prefix
    assert derive_cid_prefix("main") == "main-"
    # "nexus-live" -> "nexuslive" -> [:8] = "nexusliv"
    assert derive_cid_prefix("nexus-live") == "nexusliv-"
    # "nexus-testing" -> "nexustesting" -> [:8] = "nexustes"
    assert derive_cid_prefix("nexus-testing") == "nexustes-"
    assert derive_cid_prefix("") == "x-"
    assert derive_cid_prefix("!!!") == "x-"


def test_derive_cid_prefix_matches_actual_make_client_order_id():
    """Cross-check that the prefix we derive actually matches what
    make_client_order_id produces for the same instance id."""
    from broker_adapters._classifier import derive_cid_prefix
    from broker_adapters._client_order_id import make_client_order_id

    cid = make_client_order_id(
        instance_id="main", symbol="TSLA", bar_iso="2026-05-28T13:00:00", side="BUY", retry_n=0
    )
    prefix = derive_cid_prefix("main")
    assert cid.startswith(prefix), f"cid {cid!r} should start with prefix {prefix!r}"


def test_classifier_strategy_owned_match():
    """Broker has TSLA 10sh; WAL has filled BUY 10sh with matching prefix -> strategy-owned."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    rows = [_wal_row("main-abc-0", "TSLA", "BUY", 10.0, 200.0, now - timedelta(days=8))]
    owned, external, trades = classify_broker_positions(
        positions=[_pos("TSLA", 10.0, mv=2200.0)],
        wal_rows=rows,
        instance_id="main",
        now_utc=now,
    )
    assert owned == {"TSLA": 10.0}
    assert external == {}
    assert len(trades) == 1
    assert trades[0]["ticker"] == "TSLA"
    assert trades[0]["action"] == "buy"   # MUST be lowercase to satisfy strategy consumers
    assert trades[0]["source"] == "wal"


def test_classifier_external_no_wal_match():
    """Broker has AAPL 5sh; WAL has no rows -> external."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    owned, external, trades = classify_broker_positions(
        positions=[_pos("AAPL", 5.0, mv=920.0)],
        wal_rows=[],
        instance_id="main",
        now_utc=now,
    )
    assert owned == {}
    assert "AAPL" in external
    assert external["AAPL"]["qty"] == 5.0
    assert "no WAL trace" in external["AAPL"]["note"]
    assert trades == []


def test_classifier_cross_instance_cid_is_external():
    """WAL has matching symbol but DIFFERENT instance's prefix -> external."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    # CID "other-xyz-0" does NOT start with "main-"
    rows = [_wal_row("other-xyz-0", "TSLA", "BUY", 10.0, 200.0, now - timedelta(days=8))]
    owned, external, _ = classify_broker_positions(
        positions=[_pos("TSLA", 10.0, mv=2200.0)],
        wal_rows=rows,
        instance_id="main",
        now_utc=now,
    )
    assert owned == {}
    assert "TSLA" in external


def test_classifier_partial_external_split():
    """Broker shows 50sh; WAL implies only 30sh (10 BUY + 20 BUY) -> 30 owned + 20 external."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    rows = [
        _wal_row("main-a-0", "NVDA", "BUY", 10.0, 180.0, now - timedelta(days=27)),
        _wal_row("main-b-0", "NVDA", "BUY", 20.0, 200.0, now - timedelta(days=18)),
    ]
    owned, external, _ = classify_broker_positions(
        positions=[_pos("NVDA", 50.0, mv=10000.0)],
        wal_rows=rows,
        instance_id="main",
        now_utc=now,
    )
    assert owned == {"NVDA": 30.0}
    assert "NVDA" in external
    assert external["NVDA"]["qty"] == 20.0
    assert "partial external" in external["NVDA"]["note"]


def test_classifier_retention_window_excludes_old_buys():
    """A BUY older than retention_days does NOT count toward strategy ownership."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    rows = [_wal_row("main-old-0", "AAPL", "BUY", 5.0, 180.0, now - timedelta(days=400))]
    owned, external, _ = classify_broker_positions(
        positions=[_pos("AAPL", 5.0, mv=920.0)],
        wal_rows=rows,
        instance_id="main",
        retention_days=180,
        now_utc=now,
    )
    assert owned == {}
    assert "AAPL" in external


def test_classifier_manual_sell_during_downtime():
    """WAL says 10sh held; broker shows ZERO of that ticker -> not classified at all."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    rows = [_wal_row("main-old-0", "MSFT", "BUY", 10.0, 400.0, now - timedelta(days=5))]
    # Broker shows AAPL (no MSFT)
    owned, external, _ = classify_broker_positions(
        positions=[_pos("AAPL", 5.0, mv=920.0)],
        wal_rows=rows,
        instance_id="main",
        now_utc=now,
    )
    # AAPL has no WAL trace -> external; MSFT is absent from broker entirely
    assert owned == {}
    assert "AAPL" in external
    assert "MSFT" not in owned and "MSFT" not in external


def test_classifier_wal_implies_more_trusts_broker():
    """WAL says 15sh held; broker shows 10sh (mid-stream manual partial sell)
    -> owned == broker reality (10), no external row added."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    rows = [
        _wal_row("main-a-0", "MSFT", "BUY", 10.0, 400.0, now - timedelta(days=10)),
        _wal_row("main-b-0", "MSFT", "BUY", 5.0, 410.0, now - timedelta(days=8)),
    ]
    owned, external, _ = classify_broker_positions(
        positions=[_pos("MSFT", 10.0, mv=4150.0)],
        wal_rows=rows,
        instance_id="main",
        now_utc=now,
    )
    assert owned == {"MSFT": 10.0}
    assert external == {}


def test_classifier_handles_malformed_wal_row():
    """A WAL row with None qty or missing side does not crash the classifier."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    rows = [
        {  # malformed: filled_qty None
            "client_order_id": "main-bad-0", "symbol": "TSLA", "side": "BUY",
            "state": "filled", "filled_qty": None, "filled_avg_price": 200.0,
            "updated_at_utc": (now - timedelta(days=1)).isoformat(),
        },
        {  # missing side
            "client_order_id": "main-bad-1", "symbol": "TSLA",
            "state": "filled", "filled_qty": 5.0, "filled_avg_price": 200.0,
            "updated_at_utc": (now - timedelta(days=1)).isoformat(),
        },
        _wal_row("main-good-0", "TSLA", "BUY", 10.0, 200.0, now - timedelta(days=1)),
    ]
    owned, _, _ = classify_broker_positions(
        positions=[_pos("TSLA", 10.0, mv=2200.0)],
        wal_rows=rows,
        instance_id="main",
        now_utc=now,
    )
    assert owned == {"TSLA": 10.0}


def test_classifier_skips_zero_qty_broker_position():
    """Broker reports a zero-qty row (Alpaca occasionally does after liquidation).
    Bug-sweep 2026-05-28: must not pollute _external_positions with empty rows."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    owned, external, _ = classify_broker_positions(
        positions=[_pos("AAPL", 0.0, mv=0.0)],
        wal_rows=[],
        instance_id="main",
        now_utc=now,
    )
    assert owned == {}
    assert external == {}, "zero-qty broker position must not appear in either dict"


def test_classifier_quarantines_short_position():
    """Alpaca supports shorting; the long-only strategy must not adopt a short.
    Bug-sweep 2026-05-28: negative broker_qty -> quarantined as external
    regardless of WAL state."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    # WAL says we bought 5 of TSLA, but broker now shows -10 (short)
    rows = [_wal_row("main-a-0", "TSLA", "BUY", 5.0, 200.0, now - timedelta(days=2))]
    owned, external, _ = classify_broker_positions(
        positions=[_pos("TSLA", -10.0, mv=-2200.0)],
        wal_rows=rows,
        instance_id="main",
        now_utc=now,
    )
    assert owned == {}, "short position must not enter strategy-owned"
    assert "TSLA" in external
    assert external["TSLA"]["qty"] == -10.0
    assert "short" in external["TSLA"]["note"]


def test_classifier_tolerance_capped_at_half_share():
    """Bug-sweep 2026-05-28: 1% relative tolerance is CAPPED at 0.5sh so a
    50-share gap on a 9050-share position falls through to partial-external
    (would previously have been silently swallowed as owned)."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    # WAL: 9000 shares; broker: 9050 shares; gap = 50sh
    rows = [
        _wal_row("main-a-0", "MEGA", "BUY", 9000.0, 10.0, now - timedelta(days=10)),
    ]
    owned, external, _ = classify_broker_positions(
        positions=[_pos("MEGA", 9050.0, mv=90500.0)],
        wal_rows=rows,
        instance_id="main",
        now_utc=now,
    )
    # 50sh > 0.5sh cap -> must split partial-external, NOT swallow.
    assert owned == {"MEGA": 9000.0}
    assert "MEGA" in external
    assert external["MEGA"]["qty"] == 50.0


def test_classifier_handles_tz_naive_wal_timestamp():
    """Bug-sweep 2026-05-28: a tz-naive ISO string in updated_at_utc must
    not crash the cutoff comparison ('can't compare offset-naive and
    offset-aware datetimes'); it must be treated as UTC."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    rows = [
        {
            "client_order_id": "main-tz-0",
            "symbol": "TSLA", "side": "BUY", "state": "filled",
            "filled_qty": 10.0, "filled_avg_price": 200.0,
            # NO tz suffix
            "updated_at_utc": "2026-05-20T13:00:00",
        }
    ]
    # Should not raise; should classify as owned.
    owned, _, trades = classify_broker_positions(
        positions=[_pos("TSLA", 10.0, mv=2200.0)],
        wal_rows=rows,
        instance_id="main",
        now_utc=now,
    )
    assert owned == {"TSLA": 10.0}
    assert len(trades) == 1


def test_classifier_excludes_dry_run_synthetic_fills():
    """0-A (bug-sweep 2026-05-28): RH_DRY_RUN synthetic fills are written to the
    WAL as terminal 'filled' rows with broker_order_id 'dry-*' but were NEVER
    submitted to the broker. They must NOT feed classification or the _trades
    rebuild, else a clean-room boot after a dry-run cycle fabricates bogus
    recent_sell_block entries and V32 entry anchors on the real account."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    rows = [
        _wal_row("main-real-0", "TSLA", "BUY", 4.0, 200.0, now - timedelta(days=5)),
        # dry-run synthetic fill for the SAME ticker — must be ignored entirely
        {**_wal_row("main-dry-0", "TSLA", "BUY", 6.0, 200.0, now - timedelta(days=4)),
         "broker_order_id": "dry-abc123"},
        # a dry-run SELL on another ticker that would otherwise pollute recent_sell_block
        {**_wal_row("main-dry-1", "AAPL", "SELL", 3.0, 180.0, now - timedelta(days=2)),
         "broker_order_id": "dry-def456"},
    ]
    owned, external, trades = classify_broker_positions(
        positions=[_pos("TSLA", 4.0, mv=800.0)],
        wal_rows=rows,
        instance_id="main",
        now_utc=now,
    )
    # Only the real 4sh counts; broker shows 4sh -> fully owned, dry 6sh ignored.
    assert owned == {"TSLA": 4.0}
    assert external == {}
    # _trades has ONLY the real fill — no dry rows.
    assert len(trades) == 1
    assert trades[0]["client_order_id"] == "main-real-0"
    assert all(not str(t.get("broker_order_id") or "").startswith("dry-") for t in trades)


def test_classifier_aged_out_note_distinguishes_from_never_seen():
    """2-C (bug-sweep 2026-05-28): a still-held position whose only WAL BUY is
    beyond the retention window is quarantined FAIL-SAFE (we do not auto-adopt —
    the strategy's max_hold_days would have exited a real holding long ago), but
    its external note must distinguish 'aged-out' (had WAL provenance) from
    'no WAL trace' (truly unknown) so the operator can adopt manually if wanted."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    rows = [_wal_row("main-old-0", "AAPL", "BUY", 5.0, 180.0, now - timedelta(days=400))]
    owned, external, _ = classify_broker_positions(
        positions=[_pos("AAPL", 5.0, mv=920.0)],
        wal_rows=rows,
        instance_id="main",
        retention_days=180,
        now_utc=now,
    )
    assert owned == {}
    assert "AAPL" in external
    note = external["AAPL"]["note"].lower()
    assert "aged-out" in note, f"note should flag aged-out provenance distinctly: {note!r}"


def test_classifier_truly_external_note_unchanged():
    """A position with NO WAL provenance at all keeps the plain 'no WAL trace' note
    (the aged-out distinction must not bleed into the never-seen case)."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    owned, external, _ = classify_broker_positions(
        positions=[_pos("AAPL", 5.0, mv=920.0)],
        wal_rows=[],
        instance_id="main",
        now_utc=now,
    )
    assert "AAPL" in external
    assert "no WAL trace" in external["AAPL"]["note"]
    assert "aged-out" not in external["AAPL"]["note"].lower()


def test_inmemory_store_excludes_dry_run_fills():
    """Defense-in-depth: the boot-only WAL query also drops dry-* synthetic fills."""
    from broker_adapters._wal import InMemoryStore
    store = InMemoryStore()
    store.insert({
        "client_order_id": "main-real-0", "symbol": "TSLA", "side": "BUY",
        "state": "filled", "filled_qty": 10.0, "filled_avg_price": 200.0,
        "updated_at_utc": "2026-05-20T00:00:00+00:00", "broker_order_id": "bx-1",
    })
    store.insert({
        "client_order_id": "main-dry-0", "symbol": "TSLA", "side": "BUY",
        "state": "filled", "filled_qty": 6.0, "filled_avg_price": 200.0,
        "updated_at_utc": "2026-05-20T00:00:00+00:00", "broker_order_id": "dry-xyz",
    })
    rows = store.list_filled_for_prefix("main-")
    assert len(rows) == 1
    assert rows[0]["client_order_id"] == "main-real-0"


def test_classifier_sell_to_zero_then_rebuy_nets_correctly():
    """BUY 10 -> SELL 10 -> BUY 5 should yield net 5 (broker shows 5)."""
    from broker_adapters._classifier import classify_broker_positions

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    rows = [
        _wal_row("main-a-0", "TSLA", "BUY", 10.0, 200.0, now - timedelta(days=15)),
        _wal_row("main-b-0", "TSLA", "SELL", 10.0, 220.0, now - timedelta(days=10)),
        _wal_row("main-c-0", "TSLA", "BUY", 5.0, 215.0, now - timedelta(days=3)),
    ]
    owned, external, trades = classify_broker_positions(
        positions=[_pos("TSLA", 5.0, mv=1075.0)],
        wal_rows=rows,
        instance_id="main",
        now_utc=now,
    )
    assert owned == {"TSLA": 5.0}
    assert external == {}
    # All three trades reconstructed (oldest first)
    assert len(trades) == 3
    assert trades[0]["action"] == "buy"
    assert trades[1]["action"] == "sell"
    assert trades[2]["action"] == "buy"


def test_classifier_matches_dot_form_wal_to_dash_form_broker():
    """Scope D C3: a dot-form WAL symbol (BRK.B) must match the dash-form broker
    position (BRK-B) that RH's instruments endpoint returns. Without normalizing
    BOTH read paths, a share-class position the strategy actually owns stays
    quarantined as external (unsellable by the strategy)."""
    from broker_adapters._classifier import classify_broker_positions
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    rows = [_wal_row("main-brkb-0", "BRK.B", "BUY", 4.0, 400.0, now - timedelta(days=5))]
    owned, external, trades = classify_broker_positions(
        positions=[_pos("BRK-B", 4.0, mv=1600.0)],
        wal_rows=rows,
        instance_id="main",
        now_utc=now,
    )
    assert owned == {"BRK-B": 4.0}, f"dot-form WAL must match dash-form broker, got {owned}/{external}"
    assert external == {}
    assert trades and trades[0]["ticker"] == "BRK-B"
