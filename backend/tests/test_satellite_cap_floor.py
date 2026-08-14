"""The satellite cap trimmed buys into a band the execution floor refuses.

Found by six independent log-audit agents on 2026-08-14 (bt 523085, 718107,
102463). `_CORE_MIN_SATELLITE_TRIM_USD` is $25 (broker.py ~3255) while
`_exec_min_position_floor` is max($50, NAV*min_position_nav_pct) ~ $370 - 15x
apart, and neither knows the other. Measured: 47 trims clipped to $154-$260
against floors of $361-$383; 44 of 44 that reached the floor were refused on the
NEXT line; ZERO trims ever filled; ~$67k of intended notional per window. The
core was sold to raise cash for buys that then died.

The fix DECLINES rather than trimming into an unfillable order, and reports the
skip the way every sibling refusal site does - the satellite cap was the only
refusal that never reached `_broker_skipped_buys`, so the backfill queue and
next-bar scoring never learned those names were refused.
"""
import ast
import os
import re
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_src = open(os.path.join(_backend, "broker.py"), encoding="utf-8").read()
_tree = ast.parse(_src)
_ns = {"_core_sleeve_cfg_raw": lambda specs: (
    (specs or [{}])[0].get("config") if isinstance(specs, list) and specs
    and isinstance(specs[0], dict) else None)}
for _n in _tree.body:
    if isinstance(_n, ast.FunctionDef) and _n.name == "_conversion_fixes":
        exec(compile(ast.Module(body=[_n], type_ignores=[]), "broker.py", "exec"), _ns)
flag = _ns["_conversion_fixes"]


def _block():
    lines = _src.splitlines()
    i = next(k for k, l in enumerate(lines) if "_cf_decline = False" in l)
    indent = len(lines[i]) - len(lines[i].lstrip())
    out = []
    for l in lines[i:]:
        if out and l.strip() and (len(l) - len(l.lstrip())) < indent:
            break
        out.append(l)
        if "SATELLITE CAP:" in l:
            break
    return "\n".join(out)


def test_flag_defaults_off_and_never_raises():
    assert flag(None) is False
    assert flag([]) is False
    assert flag(object()) is False
    assert flag([{"config": {}}]) is False
    assert flag([{"config": {"conversion_fixes_enabled": True}}]) is True


def test_decline_is_impossible_when_the_flag_is_off():
    """_cf_decline is initialised False and only set inside the flag branch."""
    b = _block()
    assert "_cf_decline = False" in b
    assigns = re.findall(r"^\s*_cf_decline = (.+)$", b, re.M)
    assert assigns[0].strip() == "False"
    assert len(assigns) == 2, "exactly one initialisation and one conditional set"
    i_flag = b.index("if _conversion_fixes(_cached_strategies):")
    i_set = b.index("_cf_decline = (")
    assert "_sat_room + 1e-9 < _cf_floor" in b
    assert i_flag < i_set, "the conditional set must be inside the flag branch"


def test_held_names_are_never_declined():
    """A held name may still be trimmed; only NEW positions are declined."""
    b = _block()
    assert "and not _cf_held" in b


def test_exception_defaults_are_the_safe_direction():
    """On any lookup failure we must fall back to the OLD behaviour (trim)."""
    b = _block()
    assert "_cf_held = True" in b, "unknown holding must mean 'held' => do not decline"
    assert "_cf_floor = 0.0" in b, "unknown floor must be 0 => condition false => trim"
    assert "_cf_hard = False" in b, "only the NAV floor is hard; the $50 default still fills"
    assert "noqa: BLE001" in b, "a broad catch must carry its justification inline"


def test_skip_is_reported_like_every_sibling_refusal():
    """The satellite cap was the ONLY refusal missing from _broker_skipped_buys."""
    b = _block()
    for field in ("ticker", "allocated", "reason", "price", "raw_net_score",
                  "signal_source", "is_watchlist_member", "is_watchlist_priority",
                  "is_propagation_expansion"):
        assert f'"{field}"' in b, f"skip report missing {field!r} that consumers read"
    assert "_trade_skipped_no_price = True" in b
    assert "_anchor_reinforcement_block(" in b, "anchor pending state would leak"


def test_skip_report_schema_matches_the_sibling_site():
    """Field-for-field with broker.py:15810, so no consumer sees a new shape."""
    anchor = '_nexus_cache.setdefault("_broker_skipped_buys", []).append({'
    first = _src.index(anchor)          # the sibling site at ~15810
    sib = _src[first: _src.index("})", first)]
    mine = _block()
    sib_fields = set(re.findall(r'"(\w+)":', sib))
    my_fields = set(re.findall(r'"(\w+)":', mine))
    assert sib_fields == my_fields, (
        f"schema drift vs the sibling site: {sib_fields ^ my_fields}")


def test_uses_a_module_scope_nav_source():
    """`nav` is not bound at module scope; using it raised NameError on 2026-08-14."""
    b = _block()
    assert "portfolio_emulator.get_portfolio_value(prices)" in b
    assert not re.search(r"_exec_min_position_floor\([^)]*\bnav\b", b)
