"""2026-08-10 (bt 789099): a bar with no buys must not abort the whole strategy.

`scores["_nexus_max_positions"] = int(_max_positions)` runs unconditionally at the end
of the nexus strategy, but `_max_positions` was only ever assigned inside

    if portfolio_total > 0 and _primary_buy_budget > 0:
        if stock_buys or _bfq_pending:

On a pure-hold bar neither condition holds, the read raised

    cannot access local variable '_max_positions' where it is not associated with a value

and the ENTIRE invocation was abandoned for that bar ("Run-once strategy
'graph_nexus_analysis' error"), so the bar produced no decisions at all. Observed 7+
times in the first third of the bear window 2026-03-02..2026-03-30, where the book is
capped at max_positions_bear=2 and most bars are holds.

This test drives the real module source rather than a copy: it asserts that every read
of `_max_positions` in that function is dominated by an unconditional assignment at the
function's own indent level. A test that re-implemented the control flow would agree
with itself and prove nothing.
"""
import ast
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g  # noqa: E402


def _function_containing(tree, name, marker):
    """The FunctionDef whose body mentions `marker`."""
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            src = ast.dump(node)
            if marker in src and name in src:
                if best is None or (node.end_lineno - node.lineno) > (best.end_lineno - best.lineno):
                    continue
                best = node
            elif marker in src and name in src:
                best = node
    return best


def test_max_positions_has_an_unconditional_assignment_before_it_is_published():
    src = open(g.__file__, encoding="utf-8").read()
    tree = ast.parse(src)

    target = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        dumped = ast.dump(node)
        if "_nexus_max_positions" in dumped and "_max_positions" in dumped:
            if target is None or node.lineno > target.lineno:
                target = node
    assert target is not None, "could not locate the function publishing _nexus_max_positions"

    # Every statement directly in the function body (depth 1) that assigns
    # `_max_positions` is unconditional by construction.
    uncond_lines = []
    for stmt in target.body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_max_positions" for t in sub.targets
            ):
                # depth-1 only: the statement itself must BE the assignment
                if sub is stmt or (isinstance(stmt, ast.Assign) and stmt is sub):
                    uncond_lines.append(sub.lineno)

    assert uncond_lines, (
        "_max_positions has NO unconditional assignment in the function that publishes "
        "_nexus_max_positions — a pure-hold bar will raise UnboundLocalError and abandon "
        "the whole strategy invocation"
    )

    publish_lines = [
        n.lineno
        for n in ast.walk(target)
        if isinstance(n, ast.Subscript)
        and isinstance(n.slice, ast.Constant)
        and n.slice.value == "_nexus_max_positions"
    ]
    assert publish_lines, "no _nexus_max_positions publish site found"
    assert min(uncond_lines) < min(publish_lines), (
        f"the unconditional assignment (line {min(uncond_lines)}) must come BEFORE the "
        f"publish (line {min(publish_lines)})"
    )


def test_the_static_cap_is_the_fallback_value():
    """The pre-init must use the same key the broker falls back to, so a bar that
    never reaches the Z4.1 regime block publishes the number the broker would have
    used anyway — not 0, and not a hard-coded literal that drifts."""
    src = open(g.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            d = ast.dump(node)
            if "_nexus_max_positions" in d and "_max_positions" in d:
                if target is None or node.lineno > target.lineno:
                    target = node
    first = None
    for stmt in target.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_max_positions" for t in stmt.targets
        ):
            first = stmt
            break
    assert first is not None
    text = ast.unparse(first)
    assert "max_positions" in text and "config" in text, (
        f"the pre-init must read config['max_positions'], got: {text}"
    )
