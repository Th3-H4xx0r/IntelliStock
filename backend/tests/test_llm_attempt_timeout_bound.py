"""A single hung provider request must not stall a whole lookback bar.

bt#661213 sat 12+ minutes on the third of three parallel event-maintenance
batches with nothing logged and no retry fired. Two timeouts were in play and
neither helped:

  inner  LLM_REQUEST_TIMEOUT=180s, DOUBLED per attempt -> 180 + 360 + 720 = 1260s
  outer  _maint_fut.result(timeout=900)

The inner ladder outlived the outer join, so the join gave up first and
swallowed the batch as "non-fatal" — losing 15 minutes and the batch's output,
while the socket sat open for a request that was never coming back.

Bounding the per-attempt timeout puts the ladder (780s) inside the join budget
(900s), so the inner call fails first with a real recorded error and the retry
can actually do something.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import llm_utils as L  # noqa: E402


def test_first_attempt_uses_the_base_timeout():
    assert L._attempt_timeout(180, 0) == 180.0


def test_backoff_still_grows():
    assert L._attempt_timeout(30, 1) == 60.0
    assert L._attempt_timeout(30, 2) == 120.0


def test_growth_is_capped():
    assert L._attempt_timeout(180, 1) == L._LLM_MAX_ATTEMPT_TIMEOUT
    assert L._attempt_timeout(180, 9) == L._LLM_MAX_ATTEMPT_TIMEOUT


def test_full_ladder_fits_inside_the_maintenance_join_budget():
    """This is the actual invariant that was violated."""
    ladder = sum(L._attempt_timeout(180, a) for a in range(3))
    assert ladder < 900, f"ladder {ladder}s must fail before the 900s join"


def test_bad_inputs_fall_back_to_a_sane_base():
    for bad in (None, 0, "", "x"):
        assert L._attempt_timeout(bad, 0) == 180.0
    assert L._attempt_timeout(180, "x") == 180.0
    assert L._attempt_timeout(180, -5) == 180.0


def test_every_call_path_uses_the_bounded_helper():
    """Six providers shared the same unbounded doubling; none may keep it."""
    src = open(L.__file__).read()
    assert "timeout if attempt == 0 else timeout * 2" not in src
    assert src.count("_attempt_timeout(timeout, attempt)") >= 6
