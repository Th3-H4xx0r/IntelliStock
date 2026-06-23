from kalshi.normalize import normalize_team, TeamCrosswalk


def test_known_alias():
    assert normalize_team("Man Utd") == "Manchester United"
    assert normalize_team("man united") == "Manchester United"


def test_case_and_whitespace_insensitive():
    assert normalize_team("  SPURS  ") == "Tottenham Hotspur"


def test_unknown_passes_through_title_cased():
    assert normalize_team("brentford") == "Brentford"
    assert normalize_team("  some  new   club ") == "Some New Club"


def test_empty():
    assert normalize_team("") == ""


def test_crosswalk_extra_overrides():
    cw = TeamCrosswalk(extra={"the gunners": "Arsenal"})
    assert cw.normalize("The Gunners") == "Arsenal"
    assert cw.normalize("Man City") == "Manchester City"
