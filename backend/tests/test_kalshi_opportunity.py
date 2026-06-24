from kalshi.capital.opportunity import score, liquidity_factor, time_factor


def test_monotonic_in_edge_and_confidence():
    base = score(edge=0.04, model_confidence=0.7, liquidity=500, hours_to_kickoff=10)
    assert score(edge=0.08, model_confidence=0.7, liquidity=500, hours_to_kickoff=10) > base
    assert score(edge=0.04, model_confidence=0.9, liquidity=500, hours_to_kickoff=10) > base


def test_near_kickoff_scores_higher_than_far():
    near = score(edge=0.05, model_confidence=0.8, liquidity=800, hours_to_kickoff=3)
    far = score(edge=0.05, model_confidence=0.8, liquidity=800, hours_to_kickoff=100)
    assert near > far


def test_factors_bounded():
    assert 0 < liquidity_factor(5000) <= 1.0
    assert liquidity_factor(0) == 0.1
    assert 0.2 <= time_factor(2) <= 1.0
    assert time_factor(0) == 0.2
