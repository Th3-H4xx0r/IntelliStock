"""No module may reference a global it never binds or imports.

Three separate production outages in one evening were this exact bug:

  * `discord_bot` and `cli` imported RETHINKDB_HOST/RETHINKDB_PORT from
    interactive_utils after the port deleted them — crash-loop on import;
  * `price_utils` guarded its bars cache on `_rethink`, a name the port
    renamed to `_store`, so EVERY Alpaca bars fetch raised NameError and a
    backtest ran to completion against zero price data;
  * `ml_news` kept a whole ReQL connect function built on `r`,
    `RETHINKDB_HOST` and `RETHINKDB_PORT`, none of which survive.

None of it failed a test. The modules need `discord`, a tty, or a live
market-data call to import or execute, so the bad line is simply never
reached in CI — it is reached in production, on the first request.

This walks the AST instead: bind everything a module defines, imports, or
takes as a parameter, then assert nothing is read that is not in that set.
It costs a second and catches the whole class.
"""
from __future__ import annotations

import ast
import builtins
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
# Names Python injects into every module namespace.
_IMPLICIT = {"__file__", "__name__", "__doc__", "__package__", "__spec__",
             "__loader__", "__builtins__", "__debug__"}
_KNOWN = set(dir(builtins)) | _IMPLICIT


def _bound_names(tree: ast.AST) -> set:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            out.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                out.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            out.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            out.update(node.names)
        elif isinstance(node, ast.comprehension):
            for t in ast.walk(node.target):
                if isinstance(t, ast.Name):
                    out.add(t.id)
    return out


def _undefined(path: pathlib.Path):
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return []
    bound = _bound_names(tree) | _KNOWN
    seen, out = set(), []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                and node.id not in bound and node.id not in seen):
            seen.add(node.id)
            out.append((node.lineno, node.id))
    return out


def test_no_module_reads_a_name_it_never_binds():
    offenders = []
    for path in sorted(_BACKEND.rglob("*.py")):
        p = str(path)
        if "/tests/" in p or "archive_rethinkdb" in p:
            continue
        for line, name in _undefined(path):
            offenders.append(f"{path.relative_to(_BACKEND)}:{line} reads undefined {name!r}")
    assert not offenders, (
        "these names are read but never defined or imported — a NameError "
        "waiting for the first request that reaches the line:\n  "
        + "\n  ".join(offenders))
