"""Point-in-time fundamental VETO on satellite buys.

WHY A VETO AND NOT A SELECTOR
-----------------------------
Measured over 11 runs and 98 stock positions, this system's satellite is worth
**-$7.80 per position**: 33.7% hit rate, average winner +$30 against average
loser -$27, i.e. no right tail. Two separate attempts to find a profitable slice
failed, and the second explained the first — **89% of buys carry the maximum raw
conviction score**, so the model has no gradient to rank its own picks with.

That rules out "use the signal to choose" (Grinold: concentrating a negative-IC
signal amplifies losses) and points at an EXTERNAL discriminator used only to
EXCLUDE (Bessembinder: 58% of stocks underperform T-bills, so removing likely
losers is the tractable half of the problem).

WHAT MUST NEVER HAPPEN
----------------------
A fundamentals feed must not be able to halt trading. Every failure mode here —
no data, an ETF, a name that had not reported by this bar, a network error, a
bad config — resolves to "do not block". The worst it may do is decline to have
an opinion. DEFAULT OFF.
"""
import ast
import os
import sys
from datetime import date, datetime

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_WANTED = {"_fundamental_veto_blocks", "_core_sleeve_cfg_raw",
           "_residual_sleeve_config", "_chop_ret20_cfg"}
_src = open(os.path.join(_backend, "broker.py")).read()
_tree = ast.parse(_src)
_ns = {"_log": lambda *a, **k: None, "math": __import__("math")}
for _node in _tree.body:
    if isinstance(_node, ast.FunctionDef) and _node.name in _WANTED:
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"), _ns)
for _w in _WANTED:
    assert _w in _ns, _w
veto = _ns["_fundamental_veto_blocks"]

AS_OF = datetime(2026, 4, 1, 13, 0)


def _spec(**over):
    cfg = {
        "residual_sleeve_enabled": True,
        "residual_sleeve_symbol": "SPY",
        "residual_sleeve_bear_symbol": "SQQQ",
        "fundamental_veto_enabled": True,
        "fundamental_veto_min_gp_a": 0.10,
    }
    cfg.update(over)
    return [{"strategy": "graph_nexus_analysis", "config": cfg}]


def _patch(monkeypatch, *, grade, red=None):
    """Stub the dossier the veto reads. The veto calls
    `company_research.research_company`, so patch THAT — patching a lower-level
    helper would leave the tested path unexercised."""
    import company_research as cr
    from datetime import date as _d

    def _fake(symbol, as_of, **kw):
        _seen["as_of"] = as_of
        return cr.Dossier(symbol=symbol, as_of=_d(2026, 4, 1),
                          period_end=None if grade is None else _d(2025, 12, 31),
                          grade=grade, red_flags=list(red or []))
    monkeypatch.setattr(cr, "research_company", _fake, raising=True)


_seen = {}


def test_default_off_never_blocks(monkeypatch):
    _patch(monkeypatch, grade="F", red=["burns cash from operations"])
    assert veto("ACME", _spec(fundamental_veto_enabled=False), AS_OF)[0] is False


def test_grade_f_is_blocked_with_its_reasons(monkeypatch):
    _patch(monkeypatch, grade="F", red=["burns cash from operations", "unprofitable"])
    blocked, why = veto("ACME", _spec(), AS_OF)
    assert blocked is True
    assert "grade F" in why and "burns cash" in why, why


def test_healthy_grades_pass(monkeypatch):
    for g in ("A", "B", "C"):
        _patch(monkeypatch, grade=g)
        blocked, why = veto("ACME", _spec(), AS_OF)
        assert blocked is False, (g, why)
        assert why == f"grade {g}"


def test_blocked_grades_are_configurable(monkeypatch):
    _patch(monkeypatch, grade="D")
    assert veto("ACME", _spec(), AS_OF)[0] is False
    assert veto("ACME", _spec(fundamental_veto_block_grades="F,D"), AS_OF)[0] is True


def test_unknown_is_allowed_by_default(monkeypatch):
    """A name with no filings is 'no opinion' unless the operator opts in."""
    _patch(monkeypatch, grade=None)
    blocked, why = veto("ACME", _spec(), AS_OF)
    assert blocked is False
    assert why == "no fundamentals"


def test_unknown_is_NEVER_blocked_even_when_the_flag_is_set(monkeypatch):
    """`fundamental_veto_block_unknown` is deliberately inert. It shipped once and
    suppressed every satellite buy (bt 301356: 5 trades in 7% of a window, all of
    them the SPY sleeve), because "no grade" conflates a genuine shell with an ETF
    and with a failed EDGAR lookup. Blocking on silence turns a research feed into
    a kill switch. Re-enable only once research_company reports WHY it has no
    grade."""
    _patch(monkeypatch, grade=None)
    blocked, why = veto("ACME", _spec(fundamental_veto_block_unknown=True), AS_OF)
    assert blocked is False, "silence must never block a trade"
    assert why == "no fundamentals"


def test_sleeve_symbols_are_exempt(monkeypatch):
    """Vetoing SPY disarms the index core; vetoing SQQQ disarms the bear hedge."""
    _patch(monkeypatch, grade="F", red=["unprofitable"])
    for sym in ("SPY", "SQQQ"):
        blocked, why = veto(sym, _spec(fundamental_veto_block_unknown=True), AS_OF)
        assert blocked is False, sym
        assert why == "sleeve symbol"


def test_a_raising_lookup_fails_open(monkeypatch):
    import company_research as cr

    def _boom(*a, **k):
        raise RuntimeError("EDGAR unreachable")
    monkeypatch.setattr(cr, "research_company", _boom, raising=True)
    blocked, why = veto("ACME", _spec(fundamental_veto_block_unknown=True), AS_OF)
    assert blocked is False, "a research outage must never halt trading"
    assert why == "error"


def test_as_of_is_the_decision_time_not_today(monkeypatch):
    """The no-lookahead property rests on the DECISION time reaching the dossier.
    If a refactor passes date.today(), a 2026-04 buy is judged on 2026-08 filings."""
    from datetime import date as _d
    _seen.clear()
    _patch(monkeypatch, grade="B")
    veto("ACME", _spec(), AS_OF)
    assert _seen["as_of"] == AS_OF, _seen
    assert _seen["as_of"] != _d.today()



def test_an_etf_survives_the_veto(monkeypatch):
    """QQQ/SOXX resolve to a CIK but have no GrossProfit, so they grade None.
    Blocking them would disarm every ETF lane the strategy uses."""
    _patch(monkeypatch, grade=None)
    for etf in ("QQQ", "SOXX", "BITO"):
        blocked, _ = veto(etf, _spec(fundamental_veto_block_unknown=True), AS_OF)
        assert blocked is False, etf
