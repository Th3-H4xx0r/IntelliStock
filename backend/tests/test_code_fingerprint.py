"""The /health code fingerprint has to be right in the container, not just here.

The whole point of the fingerprint is to answer "is my commit actually live?"
without spending a backtest to find out. A fingerprint that silently reports
"unreadable" — because the path anchoring assumed the repo layout instead of
the image layout — would be worse than none: it would look like a working
check while telling you nothing.

These tests are deliberately paranoid about the path anchoring, because that is
the part that differs between this checkout (`backend/broker.py`) and the
deployed image (`/app/broker.py`, built with `context: ./backend`).
"""
import hashlib
import importlib.util
import os
import sys
import types

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_fingerprint_fn():
    """Pull the two fingerprint symbols out of api/main.py without importing it.

    api/main.py builds the whole FastAPI app at import time — routes, DB
    handles, auth. Importing it in a unit test would need the entire runtime.
    We only want two module-level definitions, so extract them by source.
    """
    import ast

    path = os.path.join(BACKEND, "api", "main.py")
    with open(path, "r") as fh:
        src = fh.read()
    tree = ast.parse(src)
    wanted = {"_CODE_FINGERPRINT_FILES", "_CODE_FINGERPRINT_CACHE", "_code_fingerprint"}
    keep = []
    for node in tree.body:
        name = getattr(node, "name", None)
        if name in wanted:
            keep.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(getattr(t, "id", None) in wanted for t in targets):
                keep.append(node)
    mod = types.ModuleType("_fp")
    # __file__ has to look like the real one — the function derives the root from it.
    mod.__file__ = path
    exec(compile(ast.Module(body=keep, type_ignores=[]), path, "exec"), mod.__dict__)
    return mod


def test_every_file_hashes_and_none_are_unreadable():
    mod = _load_fingerprint_fn()
    fp = mod._code_fingerprint()
    assert fp, "fingerprint is empty"
    for name, digest in fp.items():
        assert not digest.startswith("unreadable"), f"{name} -> {digest}"
        assert len(digest) == 12, f"{name} digest is {len(digest)} chars, expected 12"
        int(digest, 16)  # must be hex


def test_hashes_match_the_actual_files():
    """A fingerprint that does not track the file it names is decorative."""
    mod = _load_fingerprint_fn()
    fp = mod._code_fingerprint()
    for rel in mod._CODE_FINGERPRINT_FILES:
        path = os.path.join(BACKEND, rel)
        with open(path, "rb") as fh:
            expected = hashlib.sha256(fh.read()).hexdigest()[:12]
        assert fp[rel.split("/")[-1]] == expected, rel


def test_paths_are_relative_to_the_backend_root_not_the_repo_root():
    """Guards the exact bug this file exists for.

    In the image, `context: ./backend` means broker.py sits at /app/broker.py.
    A path of "backend/broker.py" resolves in this checkout and nowhere else.
    """
    mod = _load_fingerprint_fn()
    for rel in mod._CODE_FINGERPRINT_FILES:
        assert not rel.startswith("backend/"), (
            f"{rel!r} is anchored on the repo layout; the container has no "
            "backend/ directory inside /app"
        )
        assert os.path.exists(os.path.join(BACKEND, rel)), rel


def test_broker_is_covered():
    """broker.py is where every trade decision lives; it is the one that matters."""
    mod = _load_fingerprint_fn()
    assert "broker.py" in mod._CODE_FINGERPRINT_FILES


def test_result_is_cached():
    mod = _load_fingerprint_fn()
    first = mod._code_fingerprint()
    assert mod._code_fingerprint() is first, "recomputed instead of caching"
