"""swing_trader.account: the read-only book views both lanes share."""
import os
import sys
from types import SimpleNamespace as NS

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import account  # noqa: E402


class Health:
    """An adapter with the public option_positions_health() accessor."""

    def __init__(self, answer, rows=()):
        self.answer, self.rows = answer, list(rows)

    def option_positions_health(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return dict(self.answer) if isinstance(self.answer, dict) else self.answer

    def list_option_positions(self):
        return list(self.rows)


# -- G8b minor 1: an answer without stale_since fails closed -------------------------

@pytest.mark.parametrize("answer", [{"complete": True}, {"complete": True, "stale": None}])
def test_a_health_answer_without_stale_since_is_unreadable(answer):
    complete, stale_since, error = account.positions_health(Health(answer))
    assert error is not None and "stale_since" in error
    assert complete is False and stale_since is None
    rows, reason = account.option_book(Health(answer))
    assert reason is not None and "could not be read" in reason
    assert account.option_positions(Health(answer)) is None


def test_a_complete_current_answer_still_reads_healthy():
    assert account.positions_health(Health({"complete": True, "stale_since": None})) == (
        True, None, None)
    assert account.option_book(Health({"complete": True, "stale_since": None})) == ([], None)


def test_an_adapter_without_the_accessor_keeps_the_private_flag_fallback():
    legacy = NS(_option_positions_complete=True, _positions_stale_since=None)
    assert account.positions_health(legacy) == (True, None, None)
