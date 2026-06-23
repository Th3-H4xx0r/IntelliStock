from kalshi.engine import price_cents


def test_integer_cents_passthrough():
    assert price_cents({"yes_ask": 47}, "yes_ask") == 47
    assert price_cents({"yes_ask": 99}, "yes_ask") == 99


def test_dollar_scale_is_converted_to_cents():
    assert price_cents({"yes_ask": 0.47}, "yes_ask") == 47
    assert price_cents({"yes_ask": 0.01}, "yes_ask") == 1


def test_zero_and_missing_fall_through_to_next_key():
    assert price_cents({"yes_ask": 0}, "yes_ask") == 0
    assert price_cents({"yes_ask": 0, "last_price": 50}, "yes_ask", "last_price") == 50
    assert price_cents({}, "yes_ask") == 0
    assert price_cents({"yes_ask": None}, "yes_ask") == 0
    assert price_cents({"yes_ask": "bad"}, "yes_ask") == 0
