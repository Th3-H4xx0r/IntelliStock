"""The seven swing-trader tables: registered, keyed the way the contract says,
and readable the way the lanes read them (spec §7)."""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from db import schema  # noqa: E402
from db.store import P  # noqa: E402

SWING_TABLES = ("SwingSignals", "SwingWheelScans", "SwingIvSnapshots",
                "SwingMacroDaily", "SwingIndexMembership", "SwingSectorMap",
                "SwingDailyBars")


def test_the_seven_tables_are_registered_with_text_ids():
    for name in SWING_TABLES:
        assert name in schema.ALL_TABLES, name
        assert schema.spec(name).id_type == "text", name


def test_the_record_tables_index_instance_id():
    for name in ("SwingSignals", "SwingWheelScans"):
        assert schema.TABLES[name].indexed_fields == ("instance_id",)


def test_the_dated_series_are_prefix_scanned_on_id():
    for name in ("SwingIvSnapshots", "SwingMacroDaily", "SwingIndexMembership",
                 "SwingDailyBars"):
        assert schema.TABLES[name].prefix_fields == ("id",)


def test_between_on_the_id_is_strictly_before_the_upper_date(store):
    """`VIX|<ny date>` as the OPEN upper bound is the whole point-in-time rule:
    the row dated the session itself carries a close from the future."""
    store.insert("SwingMacroDaily", [
        {"id": f"VIX|{d}", "series": "VIX", "date": d, "close": c, "source": "cboe"}
        for d, c in (("2026-06-01", 15.0), ("2026-06-02", 16.0),
                     ("2026-06-03", 40.0))], conflict="replace")
    rows = store.run(store.limit(store.order_by(
        store.between("SwingMacroDaily", "VIX|", "VIX|2026-06-03"),
        index="id", desc=True), 1))
    assert [r["date"] for r in rows] == ["2026-06-02"]


def test_signals_filter_by_instance(store):
    store.insert("SwingSignals", [
        {"id": "a1", "instance_id": "swing-paper", "status": "pending"},
        {"id": "b1", "instance_id": "other", "status": "pending"}])
    rows = store.run(store.filter(
        "SwingSignals", P.field("instance_id").eq("swing-paper")))
    assert [r["id"] for r in rows] == ["a1"]
