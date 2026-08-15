import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.types import Observation
from self_learning.variance import assess_observations, assess_variance


def test_a_constant_field_is_saturated():
    rep = assess_variance([1.0] * 100, field_name="normalized_score")
    assert rep.saturated is True
    assert rep.distinct == 1 and rep.top_share == 1.0


def test_the_real_history_ratio_is_saturated():
    # 717 of 723 candidates scored exactly +1.000 (measured across
    # bt 866880 / 235194 / 559934 / 599773). This is the case the guard exists
    # for: it must fire on THIS, not only on a perfect constant.
    rep = assess_variance([1.0] * 717 + [0.4] * 6, field_name="normalized_score")
    assert rep.saturated is True
    assert round(rep.top_share, 3) == 0.992


def test_a_healthy_spread_is_not_saturated():
    rep = assess_variance([i / 100.0 for i in range(100)],
                          field_name="normalized_score")
    assert rep.saturated is False


def test_a_small_sample_is_never_declared_saturated():
    # 5 identical values is not evidence of a constant signal, it is a small
    # sample. Declaring it saturated would raise a finding on every fresh run.
    rep = assess_variance([1.0] * 5, field_name="normalized_score")
    assert rep.saturated is False
    assert rep.n == 5


def test_none_values_are_excluded_from_the_denominator():
    rep = assess_variance([1.0] * 40 + [None] * 10, field_name="x")
    assert rep.n == 40


def test_all_none_is_not_saturated_and_does_not_divide_by_zero():
    rep = assess_variance([None] * 50, field_name="x")
    assert rep.n == 0 and rep.saturated is False and rep.top_share == 0.0


def test_assess_observations_reads_the_named_field():
    obs = [Observation(
        run_id="1", origin="backtest", venue="equity", strategy_id="s",
        as_of=f"2026-04-{i:02d}", symbol="X", action="buy", decision=1,
        normalized_score=1.0, executed=False, refusal_reason="unknown",
        votes=(), config_hash=None) for i in range(1, 32)]
    rep = assess_observations(obs)
    assert rep.field_name == "normalized_score" and rep.saturated is True
