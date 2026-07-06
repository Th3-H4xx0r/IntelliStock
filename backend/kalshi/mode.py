"""Pure paper-vs-real mode discriminator + decision-row scoping. No DB, no imports
of the heavy engine module — so the API can import this cheaply on hot read paths.

`is_real_mode` MIRRORS `engine.should_execute` (the real-order gate). The gate itself
is NOT modified; `test_kalshi_mode.test_is_real_mode_matches_should_execute_contract`
enforces the two never diverge.
"""
from __future__ import annotations


def is_real_mode(environment: str, live_enabled: bool, paper_mode: bool = False) -> bool:
    """True iff this instance places REAL orders. Demo executes freely; live requires
    the explicit gate; paper_mode is a HARD override (never real). The single
    discriminator for paper-vs-real scoping across engine, API, and UI."""
    if paper_mode:
        return False
    env = (environment or "").lower()
    if env == "demo":
        return True
    if env in ("live", "prod"):
        return bool(live_enabled)
    return False


def scope_decisions(rows: list[dict], show_paper: bool) -> list[dict]:
    """Keep only rows for the active mode. show_paper=True -> paper rows (paper
    truthy); False -> real rows (paper falsy / absent)."""
    want = bool(show_paper)
    return [r for r in rows if bool(r.get("paper")) == want]
