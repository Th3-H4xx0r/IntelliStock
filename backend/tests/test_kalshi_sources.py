from kalshi.data.sources.soccerdata_src import team_form_from_row, player_rate_from_row


def test_team_form_mapping_tolerant_keys():
    tf = team_form_from_row({"elo": 1700, "xg": 1.8, "xga": 1.0, "form_pts": 12})
    assert tf.elo == 1700 and tf.xg_for == 1.8 and tf.xg_against == 1.0 and tf.form_pts == 12


def test_player_rate_mapping():
    pr = player_rate_from_row({"player": "Saka", "minutes": 2000, "npxg_per90": 0.45})
    assert pr.name == "Saka" and pr.xg_per90 == 0.45 and pr.minutes == 2000


def test_mapping_handles_missing_keys():
    assert team_form_from_row({}).elo == 0.0
    assert player_rate_from_row({}).name == ""
