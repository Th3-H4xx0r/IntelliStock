from kalshi.data.ticker_names import name_for_code, parse_market_ticker


def test_name_for_code():
    assert name_for_code("CZE") == "Czechia"
    assert name_for_code("RSA") == "South Africa"
    assert name_for_code("COD") == "Congo DR"
    assert name_for_code("ZZZ") == "Zzz"   # unknown -> title-cased code


def test_parse_home_pick():
    p = parse_market_ticker("KXWCGAME-26JUN24CZEMEX-CZE", "home")
    assert p["home"] == "Czechia" and p["away"] == "Mexico"
    assert p["match"] == "Czechia vs Mexico"
    assert p["pick_label"] == "Czechia to win"


def test_parse_away_pick():
    p = parse_market_ticker("KXWCGAME-26JUN25TUNNED-NED", "away")
    assert p["home"] == "Tunisia" and p["away"] == "Netherlands"
    assert p["pick_label"] == "Netherlands to win"


def test_parse_draw_pick():
    p = parse_market_ticker("KXWCGAME-26JUN23ENGGHA-TIE", "draw")
    assert p["home"] == "England" and p["away"] == "Ghana"
    assert p["pick_label"] == "Draw"


def test_parse_from_ticker_only_infers_side():
    # No side passed (e.g. a broker fill) — infer from the suffix code.
    assert parse_market_ticker("KXWCGAME-26JUN24RSAKOR-RSA")["pick_label"] == "South Africa to win"
    assert parse_market_ticker("KXWCGAME-26JUN24RSAKOR-KOR")["pick_label"] == "South Korea to win"


def test_parse_unparseable_falls_back():
    p = parse_market_ticker("garbage")
    assert p["match"] == "garbage"
