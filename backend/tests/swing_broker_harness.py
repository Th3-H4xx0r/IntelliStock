"""AST extraction of broker.py functions for the swing-port tests.

broker.py argparses at import and SystemExits under pytest, so its functions
are lifted out of the syntax tree and run in a namespace the test provides.
``extract`` refuses to hand back a function whose free names the namespace
does not bind: a NameError inside a blanket ``except`` reads as a quiet
wrong answer (15 failures in test_strategy_x_broker_coexistence once).
"""
from __future__ import annotations

import ast
import builtins
import os

BROKER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "broker.py")
_TREE = None


def source() -> str:
    with open(BROKER_PATH, encoding="utf-8") as handle:
        return handle.read()


def tree():
    global _TREE
    if _TREE is None:
        _TREE = ast.parse(source())
    return _TREE


def _assigned(node) -> set:
    """Names a module-level ``x = ...`` or ``x: T = ...`` statement binds."""
    if isinstance(node, ast.Assign):
        return {t.id for t in node.targets if isinstance(t, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def module_assign(name):
    """A module-level literal assignment, evaluated."""
    for node in tree().body:
        if name in _assigned(node) and node.value is not None:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found at broker.py module scope")


def function_source(name) -> str:
    node = _function(name)
    return ast.get_source_segment(source(), node)


def _function(name):
    for node in tree().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in broker.py")


def free_names(function_name) -> set:
    """Module-scope names the function reads but never binds."""
    fn = _function(function_name)
    bound = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.arguments):
            for arg in node.posonlyargs + node.args + node.kwonlyargs:
                bound.add(arg.arg)
            for slot in (node.vararg, node.kwarg):
                if slot is not None:
                    bound.add(slot.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    loads = {n.id for n in ast.walk(fn)
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return loads - bound - set(dir(builtins))


def extract(functions, *, assigns=(), namespace=None, check=None):
    """Execute the named top-level functions (and module assignments) of
    broker.py in ``namespace``. Every function in ``check`` (default: all of
    ``functions``) must have all of its free names provided."""
    wanted = set(functions)
    wanted_assigns = set(assigns)
    nodes = [
        node for node in tree().body
        if (isinstance(node, ast.FunctionDef) and node.name in wanted)
        or (_assigned(node) & wanted_assigns)
    ]
    found = {n.name for n in nodes if isinstance(n, ast.FunctionDef)}
    for node in nodes:
        found |= _assigned(node)
    missing = (wanted | wanted_assigns) - found
    assert not missing, f"missing from broker.py: {sorted(missing)}"
    ns = {"__name__": "broker_extract"}
    ns.update(namespace or {})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), BROKER_PATH, "exec"), ns)
    unbound = {}
    for name in (check if check is not None else wanted):
        gaps = sorted(free_names(name) - set(ns))
        if gaps:
            unbound[name] = gaps
    assert not unbound, f"harness does not provide: {unbound}"
    return ns
