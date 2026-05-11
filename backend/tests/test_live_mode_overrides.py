"""live_mode_overrides: pure, non-DB-mutating runtime overrides.

Verifies the hard rule that this module never writes to RethinkDB.
"""
import os
import pathlib
import sys

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from live_mode_overrides import apply_live_overrides, LIVE_OVERRIDES


def test_overrides_applied():
    cfg = {"analyst_panel_enabled": True, "foo": "bar"}
    out = apply_live_overrides(cfg)
    assert out["analyst_panel_enabled"] is False
    assert out["foo"] == "bar"


def test_input_not_mutated():
    cfg = {"analyst_panel_enabled": True}
    _ = apply_live_overrides(cfg)
    assert cfg["analyst_panel_enabled"] is True


def test_returns_new_dict():
    cfg = {"a": 1}
    out = apply_live_overrides(cfg)
    assert out is not cfg


def test_handles_none():
    out = apply_live_overrides(None)
    assert isinstance(out, dict)
    for k in LIVE_OVERRIDES:
        assert out[k] == LIVE_OVERRIDES[k]


def test_all_overrides_applied_on_empty():
    out = apply_live_overrides({})
    for k, v in LIVE_OVERRIDES.items():
        assert out[k] == v


def test_critical_safety_defaults():
    out = apply_live_overrides({})
    assert out["analyst_panel_enabled"] is False
    assert out["quality_filter_missing_metadata_policy"] == "block"
    assert out["nexus_live_fail_closed_on_missing_mcap"] is True
    assert out["portfolio_drawdown_halt_enabled"] is True


def test_source_never_imports_rethinkdb():
    """Verify live_mode_overrides.py has no DB access.

    We block any rethinkdb import or use of the RethinkDB driver object.
    Python dict.update() is allowed and expected (that's the whole point).
    """
    source_path = pathlib.Path(_backend) / "live_mode_overrides.py"
    src = source_path.read_text(encoding="utf-8")
    code_lines = []
    in_docstring = False
    for raw in src.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            in_docstring = not in_docstring
            continue
        if in_docstring or stripped.startswith("#"):
            continue
        code_lines.append(stripped)
    code = "\n".join(code_lines).lower()
    # rethinkdb / driver / r.db / table('Strategies') are all hard bans.
    forbidden_patterns = (
        "import rethinkdb",
        "from rethinkdb",
        "rethinkdb(",
        "r.db(",
        "table('strategies')",
        'table("strategies")',
    )
    for pattern in forbidden_patterns:
        assert pattern not in code, (
            f"live_mode_overrides.py must not reference {pattern!r}"
        )
