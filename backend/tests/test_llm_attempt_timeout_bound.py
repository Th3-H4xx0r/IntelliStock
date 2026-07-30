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
    assert src.count("deadline=_call_budget_deadline") >= 6


def test_every_deadline_use_has_a_definition_in_the_same_function():
    """A use without a definition is a NameError that only fires in
    production, on the provider you happen to call."""
    import ast
    src = open(L.__file__).read()
    tree = ast.parse(src)
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        seg = ast.get_source_segment(src, fn) or ""
        if "deadline=_call_budget_deadline" in seg:
            assert "_call_budget_deadline = _call_deadline()" in seg, fn.name


def test_a_shared_deadline_bounds_the_whole_ladder():
    """A per-attempt cap CANNOT bound a ladder. Azure runs 5 attempts, so
    capping each rung at 300s still allowed 1380s of socket time -- past the
    900s maintenance join the cap existed to fit inside. Only a call-level
    deadline actually bounds it."""
    real = L.time.monotonic
    now = {"t": 1000.0}
    L.time.monotonic = lambda: now["t"]
    try:
        deadline = L._call_deadline()
        total = 0.0
        for attempt in range(5):
            slice_ = L._attempt_timeout(180, min(attempt, 2), deadline=deadline)
            total += slice_
            now["t"] += slice_          # the attempt actually burns that long
        assert total <= L._LLM_TOTAL_CALL_BUDGET + 5, total
        assert total < 900, "must fail before the maintenance join abandons it"
    finally:
        L.time.monotonic = real


def test_an_expired_deadline_never_yields_a_nonpositive_timeout():
    """requests treats timeout<=0 as 'no timeout' — the exact hang we are
    trying to remove."""
    assert L._attempt_timeout(180, 3, deadline=L.time.monotonic() - 99) >= 1.0


def test_failure_telemetry_is_filed_under_the_right_provider():
    """A copy-paste of an identical timeout block filed NVIDIA's failures under
    OpenRouter. The two functions have byte-identical except-blocks, so a
    string replace matches the wrong one — assert by enclosing function."""
    import ast
    src = open(L.__file__).read()
    tree = ast.parse(src)
    lines = src.split("\n")
    seen = {}
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        if not fn.name.startswith("_call_"):
            continue
        seg = "\n".join(lines[fn.lineno - 1:fn.end_lineno])
        for provider in ("nvidia", "openrouter"):
            if f'provider="{provider}", model=model, ok=False' in seg:
                seen.setdefault(fn.name, set()).add(provider)
    assert seen.get("_call_nvidia") == {"nvidia"}, seen
    assert seen.get("_call_openrouter") == {"openrouter"}, seen


def test_both_providers_record_their_timeouts():
    """Neither may go back to returning "" with no row."""
    import ast
    src = open(L.__file__).read()
    tree = ast.parse(src)
    lines = src.split("\n")
    for name in ("_call_nvidia", "_call_openrouter"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        seg = "\n".join(lines[fn.lineno - 1:fn.end_lineno])
        assert seg.count("ok=False") >= 2, f"{name} must record timeout AND transport errors"
