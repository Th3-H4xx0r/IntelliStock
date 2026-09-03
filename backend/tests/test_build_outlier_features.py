import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (os.path.join(_root, "backend"), os.path.join(_root, "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from build_outlier_features import select_liquid, rows_for_universe  # noqa: E402


def bars(n, close, vol, start_day=1):
    return [{"t": f"2026-01-{start_day + i:02d}T05:00:00Z", "c": close, "v": vol}
            for i in range(n)]


def test_select_liquid_applies_adv_price_and_history_floors():
    recent = {"BIG": bars(45, 50.0, 1_000_000),      # ADV $50M
              "THIN": bars(45, 50.0, 10_000),        # ADV $0.5M
              "PENNY": bars(45, 2.0, 100_000_000),   # $2
              "NEW": bars(10, 50.0, 1_000_000)}      # too few sessions
    assert select_liquid(recent, adv_min=1e7, price_min=3.0, min_bars=40) == ["BIG"]


def test_rows_for_universe_ranks_within_each_date_and_keys_ids():
    by_sym = {"AAA": bars(3, 10.0, 1e6), "BBB": bars(3, 20.0, 1e6)}
    for i, b in enumerate(by_sym["AAA"]):
        b["c"] = 10.0 + i            # rising
    for i, b in enumerate(by_sym["BBB"]):
        b["c"] = 20.0 - i            # falling
    rows = rows_for_universe(by_sym, adv_min=0.0)
    assert len(rows) == 6
    assert {r["id"] for r in rows} == {f"2026-01-0{d}|{s}" for d in (1, 2, 3) for s in ("AAA", "BBB")}
    # ret126 needs 126 sessions: ranks are None on a 3-bar history, never a crash
    assert all(r["rs_rank"] is None for r in rows)
