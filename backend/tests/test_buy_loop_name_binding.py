"""No local in broker.py's buy loop may be READ before it is first ASSIGNED.

WHY THIS EXISTS
---------------
The 2026-08-03 "standing satellite weight cap" read `cash_to_use` about 80 lines
above its first assignment (`cash_to_use = cash_per_trade`). That block lives at
MODULE scope inside `for symbol in _exec_order:`, so the name survives between
iterations — the bug was invisible whenever an earlier iteration had already
bound it, and fatal on the first buy of a process that had not:

    NameError: name 'cash_to_use' is not defined   (broker.py, `if cash_to_use > _sat_room`)

bt 311771 hit it: doc-188 on 2026-07-07..08-01, a window that opens in a
confirmed bull, so the index core armed before any buy happened. doc-187 never
tripped it because its windows open in a bear with the core OFF, so ordinary
buys bound the name long before the core armed. In LIVE this would kill a
trading tick the first time a buy is taken with the core armed.

A unit test cannot call this code — it is loop body at module scope, and
broker.py argparse-SystemExits on import. So this checks the property
statically: within the buy loop, the first reference to each of the sizing
locals must be a STORE, not a LOAD.
"""
import ast
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

#: Sizing locals that are assigned inside the loop and must never be read first.
#: Kept for the named regression, but the general check below is what matters.
GUARDED = ("cash_to_use",)


def _module_bound_names(tree):
    """Names bindable at MODULE scope only.

    Must not descend into function or class bodies: a name assigned inside some
    other function is NOT available to module-level loop code. The first version
    of this helper used ast.walk and therefore collected function locals, which
    made it pass on the very bug it was written for.
    """
    import builtins
    bound = set(dir(builtins))

    def visit(node, top):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                bound.add(child.name)
                continue  # body is a separate scope
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                bound.add(child.id)
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                for a in child.names:
                    bound.add((a.asname or a.name).split(".")[0])
            elif isinstance(child, ast.ExceptHandler) and child.name:
                bound.add(child.name)
            elif isinstance(child, (ast.Global, ast.Nonlocal)):
                bound.update(child.names)
            visit(child, False)

    visit(tree, True)
    return bound


def test_no_buy_loop_reference_can_raise_nameerror():
    """Generalises the cash_to_use regression.

    2026-08-14: a patch added `nav` inside this loop. `nav` is bound only in
    other FUNCTIONS, never at module scope, so the first trimmed satellite buy
    would have raised NameError — which the surrounding
    `except (TypeError, ValueError, AttributeError)` does not catch, so it
    escapes to the tick-level BaseException handler and ends the run. The old
    GUARDED tuple did not list `nav`, so this file passed.

    A hardcoded list can only catch names someone already thought of. This
    checks the property itself: every name LOADED in the buy loop must be
    bindable somewhere at module scope.
    """
    src = open(os.path.join(_backend, "broker.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    loop = _buy_loop(tree)
    assert loop is not None, "buy loop not found"
    bound = _module_bound_names(tree)
    unbound = set()

    def scan(node):
        """Loads in the loop body, excluding nested scopes.

        A nested def inside the loop (e.g. `_buy_sort_key`) has its own
        parameters and locals; scanning into it while excluding its bindings
        reported false positives like `item`. Scope handling must be symmetric
        on both sides or the check is noise.
        """
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                if child.id not in bound:
                    unbound.add(f"{child.id} (line {child.lineno})")
            scan(child)

    scan(loop)
    assert not unbound, (
        "names loaded in the buy loop that are never bound at module scope — "
        f"each would raise NameError at runtime: {sorted(unbound)}")
def _buy_loop(tree):
    """The `for symbol in _exec_order:` loop node."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.For)
                and isinstance(node.target, ast.Name)
                and node.target.id == "symbol"
                and isinstance(node.iter, ast.Name)
                and node.iter.id == "_exec_order"):
            return node
    return None


def test_buy_loop_exists():
    src = open(os.path.join(_backend, "broker.py")).read()
    assert _buy_loop(ast.parse(src)) is not None, (
        "could not find `for symbol in _exec_order:` — if the loop was renamed, "
        "retarget this test rather than deleting it")


def test_sizing_locals_are_assigned_before_they_are_read():
    src = open(os.path.join(_backend, "broker.py")).read()
    loop = _buy_loop(ast.parse(src))
    assert loop is not None

    for name in GUARDED:
        refs = [
            n for n in ast.walk(loop)
            if isinstance(n, ast.Name) and n.id == name
        ]
        assert refs, f"{name} no longer appears in the buy loop"
        refs.sort(key=lambda n: (n.lineno, n.col_offset))
        first = refs[0]
        assert isinstance(first.ctx, ast.Store), (
            f"broker.py:{first.lineno} READS `{name}` before it is assigned "
            f"in the buy loop. At module scope this only 'works' when an "
            f"earlier iteration bound it, and raises NameError on the first "
            f"buy of a fresh process — the bt 311771 crash. Assign it first, "
            f"or operate on `cash_per_trade`, which is bound at the top of the "
            f"loop."
        )
