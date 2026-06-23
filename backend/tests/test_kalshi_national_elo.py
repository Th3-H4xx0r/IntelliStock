from kalshi.quant.national_elo import (
    DEFAULT_NATIONAL_ELO,
    is_national_team,
    national_elo,
)


def test_known_national_teams_recognized():
    assert is_national_team("Argentina")
    assert is_national_team("argentina")        # normalized (case-insensitive)
    assert is_national_team("Austria")
    assert is_national_team("United States")


def test_clubs_are_not_national_teams():
    assert not is_national_team("Arsenal")
    assert not is_national_team("Real Madrid")
    assert not is_national_team("")


def test_national_elo_lookup_and_ordering():
    # Stronger sides rate higher than weaker ones.
    assert national_elo("Argentina") > national_elo("Austria")
    assert national_elo("Brazil") > national_elo("Iraq")


def test_unknown_team_uses_default():
    assert national_elo("Atlantis") == DEFAULT_NATIONAL_ELO
    assert national_elo("Atlantis", default=1234.0) == 1234.0


def test_common_aliases_resolve_to_canonical_nation():
    # Alternate spellings feeds emit must not silently fall to the default.
    assert is_national_team("USA")
    assert national_elo("USA") == national_elo("United States")
    assert national_elo("Czech Republic") == national_elo("Czechia")
    assert national_elo("Korea Republic") == national_elo("South Korea")
    assert national_elo("IR Iran") == national_elo("Iran")
