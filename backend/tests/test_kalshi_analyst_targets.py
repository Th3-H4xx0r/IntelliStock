from kalshi.engine import select_analyst_targets


def _m(i, edge):
    return {"id": i, "best_edge": edge}


def test_caps_to_max_calls_highest_edge_first():
    metas = [_m("a", 0.01), _m("b", 0.08), _m("c", 0.04), _m("d", 0.02)]
    # threshold 0.03, cap 0.05 -> contestable >= -0.02 (all here); pick top 2 by edge.
    t = select_analyst_targets(metas, edge_threshold=0.03, max_calls=2)
    assert t == {"b", "c"}


def test_drops_hopeless_matches_beyond_reach():
    metas = [_m("a", -0.10), _m("b", 0.00)]   # a is > cap below the bar -> never tradeable
    t = select_analyst_targets(metas, edge_threshold=0.03, max_calls=10)
    assert t == {"b"}


def test_zero_cap_means_no_calls():
    metas = [_m("a", 0.20)]
    assert select_analyst_targets(metas, edge_threshold=0.03, max_calls=0) == set()


def test_empty_metas():
    assert select_analyst_targets([], edge_threshold=0.03, max_calls=5) == set()
