from kalshi.runtime.scheduler import next_wake_seconds


def test_base_cadence_when_no_settlement():
    assert next_wake_seconds(60) == 60
    assert next_wake_seconds(60, None) == 60


def test_wakes_earlier_for_imminent_settlement():
    assert next_wake_seconds(60, pending_settlement_in=10) == 10


def test_poll_wins_when_settlement_is_later():
    assert next_wake_seconds(60, pending_settlement_in=100) == 60


def test_floor():
    assert next_wake_seconds(1) == 5
    assert next_wake_seconds(60, pending_settlement_in=0) == 5
