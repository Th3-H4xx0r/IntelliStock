"""Pure allocation tests for Strategy XS."""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategy_xs import diversifier_basket  # noqa: E402


def cfg(**overrides):
    value = {
        "diversifier_symbols": ["GLD", "UUP", "DBMF"],
        "diversifier_min_history_bars": 60,
    }
    value.update(overrides)
    return value


def series(n, start=100.0):
    return [start + i * 0.1 for i in range(n)]


PRICES = {"GLD": 200.0, "UUP": 28.0, "DBMF": 26.0}


def test_all_members_qualify_when_priceable_and_long_enough():
    closes = {s: series(80) for s in ("GLD", "UUP", "DBMF")}
    assert diversifier_basket(closes, PRICES, cfg()) == ("GLD", "UUP", "DBMF")


def test_a_member_without_a_price_is_dropped():
    closes = {s: series(80) for s in ("GLD", "UUP", "DBMF")}
    prices = dict(PRICES, DBMF=0.0)
    assert diversifier_basket(closes, prices, cfg()) == ("GLD", "UUP")


def test_a_member_with_too_little_history_is_dropped():
    """DBMF has no history before 2019-05, so this is the ordinary case for
    any window starting earlier, not an edge case."""
    closes = {"GLD": series(80), "UUP": series(80), "DBMF": series(30)}
    assert diversifier_basket(closes, PRICES, cfg()) == ("GLD", "UUP")


def test_a_nonfinite_close_in_the_required_window_drops_that_member():
    closes = {"GLD": series(80), "UUP": series(80),
              "DBMF": series(59) + [float("nan")]}
    assert diversifier_basket(closes, PRICES, cfg()) == ("GLD", "UUP")


def test_order_follows_the_configured_list_not_the_dict():
    closes = {s: series(80) for s in ("DBMF", "GLD", "UUP")}
    assert diversifier_basket(closes, PRICES,
                              cfg(diversifier_symbols=["UUP", "DBMF", "GLD"])
                              ) == ("UUP", "DBMF", "GLD")


def test_no_qualifying_member_returns_empty():
    assert diversifier_basket({}, {}, cfg()) == ()


def test_a_nonfinite_history_requirement_falls_back_to_the_default():
    closes = {"GLD": series(80), "UUP": series(30), "DBMF": series(80)}
    for bad in (float("nan"), float("inf"), None, "sixty"):
        assert diversifier_basket(closes, PRICES,
                                  cfg(diversifier_min_history_bars=bad)
                                  ) == ("GLD", "DBMF"), bad


from strategy_xs import DEFAULTS, xs_targets  # noqa: E402


def acfg(**overrides):
    value = dict(DEFAULTS)
    value.update(overrides)
    return value


def total(targets):
    return round(sum(targets.values()), 6)


def test_risk_on_pays_the_sleeves_first_and_the_core_is_the_residual():
    targets, _ = xs_targets(risk_on=True, config=acfg(), basket=("GLD", "UUP"))
    assert total(targets) == 1.0
    assert targets["QLD"] == 0.6             # 0.70 residual x 6/7 core_weight
    assert targets["BIL"] == 0.1             # the unheld part of the residual
    assert targets["GLD"] == targets["UUP"] == 0.15


def test_risk_off_sends_the_core_to_cash_not_to_the_index():
    """The whole difference from Strategy X. Strategy X routes the de-levered
    weight to SPY, so a nominal 70% TQQQ book is really 27% TQQQ and 57% SPY
    and tracks SPY."""
    targets, _ = xs_targets(risk_on=False, config=acfg(core_off_symbol=""),
                            basket=("GLD", "UUP"))
    assert "QLD" not in targets
    assert targets["BIL"] == 0.70
    assert targets["GLD"] == 0.15
    assert total(targets) == 1.0


def test_the_diversifier_stays_on_in_both_regimes():
    on, _ = xs_targets(risk_on=True, config=acfg(), basket=("GLD", "UUP"))
    off, _ = xs_targets(risk_on=False, config=acfg(), basket=("GLD", "UUP"))
    assert on["GLD"] == off["GLD"] == 0.15


def test_a_short_basket_redistributes_and_never_reaches_the_core():
    """bt 773215: an unfilled sleeve handed its weight to the 3x fund, bar 1
    went 80% TQQQ instead of 60%, and TQQQ alone was 133% of the loss."""
    targets, _ = xs_targets(risk_on=True, config=acfg(), basket=("GLD",))
    assert targets["GLD"] == 0.30
    assert targets["QLD"] == 0.6
    assert total(targets) == 1.0


def test_an_empty_basket_goes_to_cash_not_to_the_core():
    targets, _ = xs_targets(risk_on=True, config=acfg(), basket=())
    assert targets["QLD"] == 0.6
    assert targets["BIL"] == 0.4
    assert total(targets) == 1.0


def test_arming_the_graph_sleeve_delevers_the_core():
    """Intended: it swaps levered index beta for stock-picking beta rather
    than stacking both. 135% beta -> 106%."""
    targets, _ = xs_targets(risk_on=True, config=acfg(satellite_pct=0.20),
                            basket=("GLD", "UUP"),
                            satellite_ranked=["AAPL", "MSFT"])
    assert targets["QLD"] == 0.428571        # 0.50 residual x 4/7
    assert targets["AAPL"] == targets["MSFT"] == 0.1
    assert total(targets) == 1.0
    beta = round(targets["QLD"] * 2 + 0.2, 3)
    assert 1.05 <= beta <= 1.06               # 120% -> 106%


def test_an_unranked_graph_sleeve_does_not_raise_core_leverage():
    targets, _ = xs_targets(risk_on=True, config=acfg(satellite_pct=0.20),
                            basket=("GLD", "UUP"), satellite_ranked=[])
    assert targets["QLD"] == 0.428571
    assert total(targets) == 1.0


def test_the_vol_scale_reduces_the_levered_leg_into_cash():
    targets, _ = xs_targets(risk_on=True, config=acfg(),
                            basket=("GLD", "UUP"), vol_scale=0.5)
    assert targets["QLD"] == 0.3
    assert total(targets) == 1.0


def test_the_vol_scale_can_never_raise_exposure():
    for bad in (2.0, float("nan"), float("inf"), None, "1.5"):
        targets, _ = xs_targets(risk_on=True, config=acfg(),
                                basket=("GLD", "UUP"), vol_scale=bad)
        assert targets["QLD"] <= 0.6, bad


def test_weights_never_sum_past_one_for_any_sleeve_split():
    for n in range(1, 6):
        basket = tuple(f"D{i}" for i in range(n))
        targets, _ = xs_targets(risk_on=True, config=acfg(), basket=basket)
        assert total(targets) <= 1.0


from strategy_xs import strategy_xs_universe  # noqa: E402


def test_the_universe_is_declared_from_config_not_the_watchlist():
    """The strategy owns its universe. Without this the filter symbol has no
    bars and the traded legs have no price, and BOTH failures are silent."""
    assert strategy_xs_universe(acfg()) == [
        "SPY", "QLD", "BIL", "GLD", "UUP"]


def test_the_universe_includes_the_inverse_leg_only_when_armed():
    assert "SQQQ" not in strategy_xs_universe(acfg())
    armed = acfg(inverse_symbol="SQQQ", inverse_pct=0.2)
    assert "SQQQ" in strategy_xs_universe(armed)


def test_the_universe_omits_the_diversifier_when_the_sleeve_is_off():
    assert strategy_xs_universe(acfg(diversifier_pct=0.0)) == [
        "SPY", "QLD", "BIL"]


def test_the_universe_is_freshly_allocated_so_callers_cannot_mutate_defaults():
    first = strategy_xs_universe(acfg())
    first.append("JUNK")
    assert "JUNK" not in strategy_xs_universe(acfg())


# ── the risk-off destination, corrected by the 4,104-construction search ─────

def test_risk_off_goes_to_the_configured_destination_not_always_cash():
    """Selected on 2011-2018 alone across 4,104 constructions, risk-off into
    SPY beat risk-off into cash. Going to cash forfeits every false signal;
    Strategy X's failure was its weak risk-ON leg, not the SPY destination."""
    targets, _ = xs_targets(risk_on=False, config=acfg(core_off_symbol="SPY"),
                            basket=("GLD", "UUP"))
    assert targets["SPY"] == 0.70
    assert "BIL" not in targets
    assert total(targets) == 1.0


def test_an_empty_off_symbol_still_means_cash():
    targets, _ = xs_targets(risk_on=False, config=acfg(core_off_symbol="ZZZ"),
                            basket=("GLD", "UUP"))
    assert targets["ZZZ"] == 0.70


def test_the_off_destination_never_receives_the_levered_leg():
    """Risk-off must not park the core in the 3x fund by a config typo."""
    targets, _ = xs_targets(risk_on=False,
                            config=acfg(core_off_symbol="QLD"),
                            basket=("GLD", "UUP"))
    assert "QLD" not in targets
    assert targets["BIL"] == 0.70


def test_the_searched_configuration_allocates_as_measured():
    """40% TQQQ + 30% GLD/UUP + 30% cash risk-on; 70% SPY + 30% basket off."""
    cfg = acfg()
    on, _ = xs_targets(risk_on=True, config=cfg, basket=("GLD", "UUP"))
    assert on["QLD"] == 0.6 and on["GLD"] == on["UUP"] == 0.15
    assert on["BIL"] == 0.1
    off, _ = xs_targets(risk_on=False, config=cfg, basket=("GLD", "UUP"))
    assert off["SPY"] == 0.70 and off["GLD"] == 0.15
    assert total(on) == total(off) == 1.0
