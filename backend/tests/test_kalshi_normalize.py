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


def test_national_team_ampersand_vs_and():
    # ESPN uses "Bosnia & Herzegovina"; Kalshi/FIFA uses "Bosnia and Herzegovina".
    # Both must normalize to the same canonical name so match_score can join them.
    assert normalize_team("Bosnia & Herzegovina") == "Bosnia and Herzegovina"
    assert normalize_team("Bosnia and Herzegovina") == "Bosnia and Herzegovina"


def test_national_team_aliases():
    assert normalize_team("USA") == "United States"
    assert normalize_team("Korea Republic") == "South Korea"
    assert normalize_team("Czechia") == "Czech Republic"
    assert normalize_team("Czech Republic") == "Czech Republic"
    assert normalize_team("Ivory Coast") == "Ivory Coast"
    assert normalize_team("Cote d'Ivoire") == "Ivory Coast"
    assert normalize_team("Trinidad and Tobago") == "Trinidad and Tobago"
