"""The package's import boundary and the dependency pins it is built from.

The image's build context is backend/mcp_server alone, so an import of anything
else in backend/ works on a developer checkout and fails in the container.
"""
import ast
import importlib.util
import re
import sys
from pathlib import Path

import mcp_server

PKG = Path(mcp_server.__file__).resolve().parent

ALLOWED = {"mcp", "mcp_types", "webauthn", "pyotp", "jwt", "cryptography", "httpx", "starlette",
           "uvicorn", "pydantic", "jsonschema", "segno", "anyio", "mcp_server"}

RUNTIME_PINS = {
    "mcp": "2.1.1",
    "mcp-types": "2.1.1",
    "webauthn": "3.0.1",
    "pyotp": "2.12.1",
    "pyjwt": "2.15.1",
    "cryptography": "50.0.2",
    "httpx": "0.28.1",
    "starlette": "1.10.0",
    "uvicorn": "0.54.0",
    "pydantic": "2.14.3",
    "jsonschema": "4.26.0",
}
TEST_PINS = {"pytest": "9.2.0", "pytest-asyncio": "1.5.0"}

_REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==(?P<version>[^\s;]+)")
_HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}")


def _sources():
    """(relative path, AST) for every package module outside tests/."""
    out = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG)
        if rel.parts[0] == "tests" or any(part.startswith(".") for part in rel.parts):
            continue
        out.append((rel, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))
    assert out, f"no package modules found under {PKG}"
    return out


def _normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _entries(filename):
    """Each requirement of a compiled requirements file, continuation lines joined."""
    entries, current = [], ""
    for raw in (PKG / filename).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        current += " " + line.rstrip("\\").strip()
        if not line.endswith("\\"):
            entries.append(current.strip())
            current = ""
    if current:
        entries.append(current.strip())
    return entries


def _pins(filename):
    """{normalized name: version}; every entry must be name==version with a sha256 hash."""
    pins, offenders = {}, []
    for entry in _entries(filename):
        match = _REQUIREMENT.match(entry)
        if match is None or not _HASH.search(entry):
            offenders.append(entry[:120])
            continue
        pins[_normalize(match.group("name"))] = match.group("version")
    assert offenders == [], f"{filename}: entries not pinned with a hash"
    return pins


def test_package_imports_only_stdlib_pinned_deps_and_itself():
    known = set(sys.stdlib_module_names) | ALLOWED
    offenders = []
    for rel, tree in _sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    offenders.append(f"{rel}:{node.lineno} relative import")
                    continue
                names = [node.module]
            else:
                continue
            for name in names:
                if name.split(".")[0] not in known:
                    offenders.append(f"{rel}:{node.lineno} imports {name}")
    assert offenders == []


def test_no_match_statements():
    found = [f"{rel}:{node.lineno}" for rel, tree in _sources()
             for node in ast.walk(tree) if isinstance(node, ast.Match)]
    assert found == []


def test_backend_modules_are_not_importable_in_tests():
    assert importlib.util.find_spec("interactive_utils") is None
    assert importlib.util.find_spec("db") is None


def test_requirements_are_hash_pinned():
    runtime = _pins("requirements.txt")
    test = _pins("requirements-test.txt")
    assert {name: runtime.get(name) for name in RUNTIME_PINS} == RUNTIME_PINS
    assert "segno" in runtime
    assert {name: test.get(name) for name in TEST_PINS} == TEST_PINS
    assert "pyyaml" in test
    # The suite runs on requirements-test.txt and the image on requirements.txt:
    # a version that differs between them is a version the tests never ran.
    assert {name: test.get(name) for name in runtime} == runtime
