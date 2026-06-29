from kalshi.data.sources.natelo import parse_team_codes, parse_world_elo
from kalshi.quant.national_elo import (
    national_elo, national_elo_from, canonical_team, DEFAULT_NATIONAL_ELO,
)

# Real eloratings.net shapes: en.teams.tsv = "<code>\t<name>[\t<aliases>]",
# World.tsv = "<localrank>\t<globalrank>\t<code>\t<rating>\t...".
_TEAMS_TSV = "AR\tArgentina\nES\tSpain\nFR\tFrance\nCV\tCape Verde\tCape Verde Islands\n"
_WORLD_TSV = (
    "1\t1\tAR\t2148\t1\t2172\t5\t1988\n"
    "2\t2\tES\t2144\t1\t2189\t7\t1946\n"
    "3\t3\tFR\t2123\t1\t2150\t9\t1990\n"
    "40\t40\tCV\t1605\t1\t1620\t9\t1500\n"
)


def test_parse_team_codes():
    codes = parse_team_codes(_TEAMS_TSV)
    assert codes["AR"] == "Argentina"
    assert codes["CV"] == "Cape Verde"  # only col 1, aliases ignored


def test_parse_world_elo_keys_by_canonical_name():
    table = parse_world_elo(_WORLD_TSV, parse_team_codes(_TEAMS_TSV), canon=canonical_team)
    assert table["Argentina"] == 2148.0
    assert table["Spain"] == 2144.0
    assert table["France"] == 2123.0
    # Unknown codes / short rows are skipped, not crashed.
    assert all(v > 0 for v in table.values())


def test_parse_world_elo_empty_input():
    assert parse_world_elo("", {}, canon=canonical_team) == {}
    assert parse_team_codes("") == {}


def test_national_elo_from_prefers_live_then_static_then_default():
    live = parse_world_elo(_WORLD_TSV, parse_team_codes(_TEAMS_TSV), canon=canonical_team)
    # Live table wins (2148 vs the static 2100).
    assert national_elo_from(live, "Argentina") == 2148.0
    # Alias still resolves into the live table.
    assert national_elo_from(live, "USA") == national_elo_from(live, "United States")
    # Empty/None live table -> identical to the static national_elo().
    assert national_elo_from({}, "Argentina") == national_elo("Argentina")
    assert national_elo_from(None, "Brazil") == national_elo("Brazil")
    # Team in neither live nor static -> default.
    assert national_elo_from(live, "Atlantis") == DEFAULT_NATIONAL_ELO
