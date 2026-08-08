"""The index core must be reachable in LIVE and in BACKTEST, at one point each.

This file exists because the exact bug it guards against already shipped here.
`_residual_sleeve_deploy` had a single call site, and that call site sat in the
`else` of a two-branch ``mode == MODE_LIVE`` chain — backtest-only — so for
weeks the live book could liquidate the sleeve but never park into it, and the
live order service that had been carefully threaded into the call could only
ever evaluate to None. Every sleeve unit test passed the whole time, because
every one of them called the function directly.

So this asserts CALL-SITE REACHABILITY, not behaviour, and it does it
TRANSITIVELY: `_core_sleeve_decide` is not called from the tick body, it is
called from inside `_residual_sleeve_release` / `_residual_sleeve_deploy`. A
check that only walked the `if`/`elif`/`else` chain around the call itself would
happily pass while the enclosing function was unreachable in one mode — which
is precisely the shape of the original bug, one level up.

The second thing this file pins is that the LIVE and BACKTEST paths reach the
SAME decision function. If the core ever grows a mode-specific sizing copy, the
backtest stops describing the account, and that divergence is undetectable from
either run on its own.
"""
import ast
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

_SRC = open(os.path.join(_BACKEND, "broker.py"), encoding="utf-8").read()
_TREE = ast.parse(_SRC)

_PARENT = {}
for _node in ast.walk(_TREE):
    for _child in ast.iter_child_nodes(_node):
        _PARENT[_child] = _node

_FUNCS = {}
for _node in ast.walk(_TREE):
    if isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _FUNCS.setdefault(_node.name, []).append(_node)


# ---------------------------------------------------------------- reachability


def _mode_operand(node):
    """Canonical name for one side of a `mode ==/!= X` comparison."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {"live": "MODE_LIVE", "backtest": "MODE_BACKTEST"}.get(node.value)
    return None


def _eval_under_mode(test, live):
    """Truth of `test` when the ONLY thing known is the run mode.

    True/False when `mode` alone decides it; None when it turns on anything
    else (tick mode, a config flag, ...), which for reachability purposes means
    "does not block".
    """
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        left = _mode_operand(test.left)
        right = _mode_operand(test.comparators[0])
        if left == "mode" or right == "mode":
            other = right if left == "mode" else left
            if other in ("MODE_LIVE", "MODE_BACKTEST"):
                same = (other == "MODE_LIVE") == bool(live)
                if isinstance(test.ops[0], ast.Eq):
                    return same
                if isinstance(test.ops[0], ast.NotEq):
                    return not same
        return None
    if isinstance(test, ast.BoolOp):
        values = [_eval_under_mode(v, live) for v in test.values]
        if isinstance(test.op, ast.And):
            if any(v is False for v in values):
                return False
            return True if all(v is True for v in values) else None
        if any(v is True for v in values):
            return True
        return False if all(v is False for v in values) else None
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _eval_under_mode(test.operand, live)
        return None if inner is None else (not inner)
    return None


def _contains(root, target):
    return any(n is target for n in ast.walk(root))


def _walk_up(node):
    """(parent, child) pairs from `node` outward to the module root."""
    child, parent = node, _PARENT.get(node)
    while parent is not None:
        yield parent, child
        child, parent = parent, _PARENT.get(parent)


def _enclosing_ifs(node, stop_at_function=True):
    """(If, in_body) pairs from innermost outward.

    `elif` is a nested If in the parent's orelse, which is exactly how the
    original bug hid. Stops at the enclosing function by default so the caller
    can recurse through call sites deliberately rather than by accident.
    """
    out = []
    for parent, child in _walk_up(node):
        if stop_at_function and isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            break
        if isinstance(parent, ast.If):
            if any(child is s or _contains(s, child) for s in parent.body):
                out.append((parent, True))
            elif any(child is s or _contains(s, child) for s in parent.orelse):
                out.append((parent, False))
    return out


def _enclosing_function(node):
    for parent, _child in _walk_up(node):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return parent
    return None


# The tree call sites are looked up in. Normally broker.py; the self-tests below
# swap in a tiny synthetic module so the harness can be pinned against branch
# shapes whose answers are known by inspection.
_ACTIVE_TREE = [_TREE]


def _call_sites(func_name):
    return [
        n for n in ast.walk(_ACTIVE_TREE[0])
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == func_name
    ]


def _locally_reachable(node, live):
    """Is `node` reachable under `mode`, ignoring who calls its function?"""
    for branch, in_body in _enclosing_ifs(node):
        verdict = _eval_under_mode(branch.test, live)
        if in_body and verdict is False:
            return False
        if not in_body and verdict is True:
            return False
    return True


def _is_reachable(node, live, _seen=frozenset()):
    """TRANSITIVE reachability: the branch chain around `node`, and then the
    branch chain around every call to the function `node` lives in, recursively
    up to the tick body.

    A function with no call sites at all is unreachable in both modes — that is
    the "dead code" verdict, and it is the right one.
    """
    if not _locally_reachable(node, live):
        return False
    func = _enclosing_function(node)
    if func is None:
        return True  # module/tick-body level: nothing further gates it
    if func.name in _seen:
        return False  # recursion; no fresh path to the top through here
    sites = _call_sites(func.name)
    if not sites:
        return False
    return any(_is_reachable(c, live, _seen | {func.name}) for c in sites)


def _reachable_sites(func_name, live):
    return [c for c in _call_sites(func_name) if _is_reachable(c, live)]


# -- the harness itself has to be trustworthy, so pin it to known shapes -----


def _parse_with_parents(src):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            _PARENT[child] = node
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _FUNCS.setdefault(node.name, []).append(node)
    _ACTIVE_TREE[0] = tree
    return tree


def _find_call(tree, name):
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") == name)


def test_harness_detects_a_backtest_only_branch():
    """The literal shape of the shipped bug: the trailing `else` of a
    two-branch MODE_LIVE chain is backtest-only."""
    saved_parent, saved_funcs = dict(_PARENT), dict(_FUNCS)
    try:
        tree = _parse_with_parents(
            "if mode == MODE_LIVE and tick == 'IDLE':\n"
            "    pass\n"
            "elif mode == MODE_LIVE:\n"
            "    live_only()\n"
            "else:\n"
            "    backtest_only()\n"
        )
        assert _is_reachable(_find_call(tree, "live_only"), live=True)
        assert not _is_reachable(_find_call(tree, "live_only"), live=False)
        assert not _is_reachable(_find_call(tree, "backtest_only"), live=True)
        assert _is_reachable(_find_call(tree, "backtest_only"), live=False)
    finally:
        _PARENT.clear()
        _PARENT.update(saved_parent)
        _FUNCS.clear()
        _FUNCS.update(saved_funcs)
        _ACTIVE_TREE[0] = _TREE


def test_harness_propagates_unreachability_through_a_call():
    """The property the older reachability file does not have. `inner()` sits in
    an unconditional block, so a local-only check calls it live-reachable; it is
    not, because the only function that calls it is itself backtest-only."""
    saved_parent, saved_funcs = dict(_PARENT), dict(_FUNCS)
    try:
        tree = _parse_with_parents(
            "def helper():\n"
            "    inner()\n"
            "if mode == MODE_LIVE:\n"
            "    pass\n"
            "else:\n"
            "    helper()\n"
        )
        call = _find_call(tree, "inner")
        assert _locally_reachable(call, live=True), "local check must be fooled"
        assert not _is_reachable(call, live=True), (
            "transitive check must see that helper() is backtest-only"
        )
        assert _is_reachable(call, live=False)
    finally:
        _PARENT.clear()
        _PARENT.update(saved_parent)
        _FUNCS.clear()
        _FUNCS.update(saved_funcs)
        _ACTIVE_TREE[0] = _TREE


def test_harness_calls_an_uncalled_function_dead():
    saved_parent, saved_funcs = dict(_PARENT), dict(_FUNCS)
    try:
        tree = _parse_with_parents("def orphan():\n    inner()\n")
        call = _find_call(tree, "inner")
        assert not _is_reachable(call, live=True)
        assert not _is_reachable(call, live=False)
    finally:
        _PARENT.clear()
        _PARENT.update(saved_parent)
        _FUNCS.clear()
        _FUNCS.update(saved_funcs)
        _ACTIVE_TREE[0] = _TREE


# -- the core sizing rule runs in both modes ---------------------------------


def test_core_sleeve_decide_is_reachable_in_live_and_backtest():
    """THE test this file is for. `_core_sleeve_decide` is the single sizing
    rule; if either mode cannot reach it, that mode is running a different
    strategy from the one the other mode measured."""
    assert _reachable_sites("_core_sleeve_decide", live=True), (
        "_core_sleeve_decide has no LIVE-reachable call site — the index core "
        "is inert in live while the backtest sizes a book that does not exist"
    )
    assert _reachable_sites("_core_sleeve_decide", live=False), (
        "_core_sleeve_decide has no BACKTEST-reachable call site — the core "
        "would ship unmeasured"
    )


def test_core_decide_is_reached_from_both_the_buy_and_the_sell_side():
    """Deploy owns the BUY side and release the SELL side. If only one of them
    reaches the decision function the core can grow but never shrink (or the
    reverse), which is the sell-only failure the residual sleeve already had."""
    holders = {
        f.name for f in
        (_enclosing_function(c) for c in _call_sites("_core_sleeve_decide"))
        if f is not None
    }
    assert "_residual_sleeve_deploy" in holders
    assert "_residual_sleeve_release" in holders


def test_both_core_entry_points_are_reachable_in_both_modes():
    """`_residual_sleeve_deploy` was live-unreachable for weeks. Pin both entry
    points in both modes so the core cannot inherit that failure."""
    for entry in ("_residual_sleeve_deploy", "_residual_sleeve_release"):
        assert _reachable_sites(entry, live=True), f"{entry} is not live-reachable"
        assert _reachable_sites(entry, live=False), f"{entry} is not backtest-reachable"


def test_core_config_gate_is_reachable_in_both_modes():
    """The master flag has to be READ in both modes; a config gate evaluated in
    only one of them is a flag that means different things per mode."""
    assert _reachable_sites("_core_sleeve_cfg", live=True)
    assert _reachable_sites("_core_sleeve_cfg", live=False)


def test_there_is_exactly_one_core_sizing_rule():
    """No mode-specific copy. `core_rebalance_order` is the sizing arithmetic;
    broker.py must call it from one place only, so live and backtest cannot
    drift apart in a way neither run can detect on its own."""
    sites = _call_sites("core_rebalance_order")
    assert len(sites) == 1, (
        f"core_rebalance_order is called from {len(sites)} places in broker.py "
        "— a second call site is a second sizing rule"
    )
    holder = _enclosing_function(sites[0])
    assert holder is not None and holder.name == "_core_sleeve_decide"


# -- the turnover budget is enforced in both modes ---------------------------


def test_turnover_budget_is_evaluated_in_both_modes():
    """A budget that binds in backtest but not live overstates the fix; one
    that binds in live but not backtest means the backtest never saw it."""
    assert _reachable_sites("_core_turnover_state", live=True)
    assert _reachable_sites("_core_turnover_state", live=False)


def test_turnover_is_recorded_in_both_modes():
    """The ledger has to be fed by BOTH execution branches. The live submit and
    the backtest submit are different statements; if only one books notional the
    budget silently never binds in the other."""
    assert _reachable_sites("_turnover_ledger_record", live=True)
    assert _reachable_sites("_turnover_ledger_record", live=False)


def test_the_main_signal_turnover_book_is_on_the_shared_path():
    """The main-signal buy/sell path forks on `mode` to submit and rejoins
    afterwards. The ledger write must sit AFTER the rejoin — a write inside
    either fork counts one mode's trades only."""
    shared = [
        c for c in _call_sites("_turnover_ledger_record")
        if _is_reachable(c, live=True) and _is_reachable(c, live=False)
        and _enclosing_function(c) is None
    ]
    assert shared, (
        "no _turnover_ledger_record call on the shared tick path — the "
        "main-signal ledger write drifted into a mode-specific branch"
    )


def _turnover_skip_blocks():
    """`if _turnover_blocked:` branches that actually REFUSE a trade.

    Matched by the `continue` in the body, so the sibling `if _turnover_blocked:`
    that only emits the once-per-tick operator log is not mistaken for a gate.

    2026-08-08: the refusal is now `if _turnover_blocked and not _tb_bypass:`
    (a conviction buy at raw >= the satellite-overflow cutoff may pass a pinned
    brake — bt 264179 refused SNDK at 14.6% of NAV three times on a 67% budget
    and then bought it on 01-30 at 96.7% through its move for $126). Match any
    test that READS `_turnover_blocked`, so the invariants below keep holding
    whatever the guard is conjoined with.
    """
    def _reads_turnover_blocked(test) -> bool:
        return any(
            isinstance(sub, ast.Name) and sub.id == "_turnover_blocked"
            for sub in ast.walk(test)
        )

    return [
        n for n in ast.walk(_TREE)
        if isinstance(n, ast.If)
        and _reads_turnover_blocked(n.test)
        and any(isinstance(s, ast.Continue) for s in ast.walk(n))
    ]


def test_turnover_budget_blocks_buys_at_the_shared_execution_gate():
    """The block must be in the tick body (module level, shared by both modes),
    not inside a mode fork — the regime cap right next to it is enforced there
    for the same reason: it is the one hop every buy lane passes through."""
    blocks = _turnover_skip_blocks()
    assert blocks, "the turnover budget no longer refuses anything"
    assert any(
        _enclosing_function(b) is None
        and _is_reachable(b, live=True)
        and _is_reachable(b, live=False)
        for b in blocks
    ), "the turnover-budget block is not on the shared live+backtest path"


def test_turnover_budget_never_gates_a_sell():
    """The one asymmetry that must hold. Every budget refusal sits under a
    `decision == 1` test, so a protective exit, a stop, a DD-circuit exit and a
    reduce-only trim are all unaffected. A budget that traps the book in a loser
    costs more than the commissions it saves."""
    blocks = _turnover_skip_blocks()
    assert blocks
    for block in blocks:
        buy_only = any(
            in_body
            and isinstance(branch.test, ast.Compare)
            and isinstance(branch.test.left, ast.Name)
            and branch.test.left.id == "decision"
            and isinstance(branch.test.ops[0], ast.Eq)
            and getattr(branch.test.comparators[0], "value", None) == 1
            for branch, in_body in _enclosing_ifs(block, stop_at_function=False)
        )
        assert buy_only, (
            "a turnover-budget refusal is not confined to the BUY side — it "
            "can now block a risk-reducing sell"
        )


# -- the master flag really is a master flag ---------------------------------


def test_every_core_branch_is_gated_on_the_master_flag():
    """`_core_sleeve_cfg` returns None when `core_sleeve_enabled` is unset, and
    every core branch must be behind an `is None` / `is not None` test on it.
    That is what makes today's behaviour byte-identical while this ships dark on
    a real-money system."""
    gated = [
        n for n in ast.walk(_TREE)
        if isinstance(n, ast.If)
        and isinstance(n.test, ast.Compare)
        and isinstance(n.test.left, ast.Name)
        and n.test.left.id == "_core"
        and any(isinstance(op, (ast.Is, ast.IsNot)) for op in n.test.ops)
    ]
    assert len(gated) >= 2, (
        "expected the deploy and release core branches to be gated on "
        f"`_core is not None`; found {len(gated)}"
    )
    holders = {
        f.name for f in (_enclosing_function(g) for g in gated) if f is not None
    }
    assert {"_residual_sleeve_deploy", "_residual_sleeve_release"} <= holders


def test_core_state_survives_a_restart():
    """Both core fields are turnover controls, so losing them fails in the
    expensive direction: a lost rebalance stamp bypasses the 5-day cadence and a
    lost ledger refunds a budget that had already bound. This host restarts
    often — 17 times in 12 days on record."""
    fields = None
    for node in _TREE.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_RESIDUAL_SLEEVE_PERSIST_FIELDS"
            for t in node.targets
        ):
            fields = {e.value for e in node.value.elts
                      if isinstance(e, ast.Constant)}
    assert fields is not None
    assert "last_core_rebalance_ts" in fields
    assert "turnover_ledger" in fields
