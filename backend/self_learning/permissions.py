"""The permission matrix: action class x rung -> autonomous / ask / blocked.

The operator's answers, encoded:

* action classes = config levers, LLM model+prompt, universe/entry timing, and
  strategy code;
* the matrix itself is editable in the tab, so tightening or loosening it is not
  a code change;
* unanswered approvals hold forever at LIVE rungs and auto-proceed below them.

Two rules are NOT configurable, deliberately:

* **Reverting is never gated.** Permission modes govern APPLYING a change.
  Rolling one back is a safety action and always executes immediately — a
  rollback that waits for approval is a rollback that does not happen at 3am.
* **A document must be on the allowlist.** An empty allowlist means the
  subsystem writes nowhere, whatever the matrix says.
"""
from __future__ import annotations

AUTONOMOUS = "autonomous"
ASK = "ask"
BLOCKED = "blocked"
MODES = frozenset({AUTONOMOUS, ASK, BLOCKED})

CONFIG_LEVERS = "config_levers"
LLM_MODELS = "llm_models"
UNIVERSE = "universe"
CODE = "code"
ACTION_CLASSES = (CONFIG_LEVERS, LLM_MODELS, UNIVERSE, CODE)

BACKTEST = "BACKTEST"
SHADOW = "SHADOW"
PAPER = "PAPER"
LIVE_CAPPED = "LIVE_CAPPED"
LIVE_FULL = "LIVE_FULL"
RUNGS = (BACKTEST, SHADOW, PAPER, LIVE_CAPPED, LIVE_FULL)

LIVE_RUNGS = frozenset({LIVE_CAPPED, LIVE_FULL})

# Defaults from the design conversation. Code is `ask` everywhere because the
# backend auto-deploys from main, so a merged patch is a deployed patch.
DEFAULT_MATRIX = {
    CONFIG_LEVERS: {BACKTEST: AUTONOMOUS, SHADOW: AUTONOMOUS, PAPER: AUTONOMOUS,
                    LIVE_CAPPED: ASK, LIVE_FULL: ASK},
    LLM_MODELS: {BACKTEST: AUTONOMOUS, SHADOW: AUTONOMOUS, PAPER: AUTONOMOUS,
                 LIVE_CAPPED: ASK, LIVE_FULL: ASK},
    UNIVERSE: {BACKTEST: AUTONOMOUS, SHADOW: AUTONOMOUS, PAPER: AUTONOMOUS,
               LIVE_CAPPED: ASK, LIVE_FULL: ASK},
    CODE: {BACKTEST: ASK, SHADOW: ASK, PAPER: ASK,
           LIVE_CAPPED: ASK, LIVE_FULL: ASK},
}


class PermissionError_(PermissionError):
    pass


def merge_matrix(stored) -> dict:
    """Stored overrides on top of the defaults, with unknown values discarded.

    An unrecognised mode falls back to the DEFAULT for that cell rather than to
    `autonomous`. A typo in a config document must never widen permission.
    """
    merged = {cls: dict(rungs) for cls, rungs in DEFAULT_MATRIX.items()}
    if not isinstance(stored, dict):
        return merged
    for action_class, rungs in stored.items():
        if action_class not in merged or not isinstance(rungs, dict):
            continue
        for rung, mode in rungs.items():
            if rung in merged[action_class] and str(mode) in MODES:
                merged[action_class][rung] = str(mode)
    return merged


def mode_for(matrix, action_class, rung) -> str:
    """The configured mode, defaulting to BLOCKED for anything unrecognised."""
    table = (matrix or {}).get(action_class)
    if not isinstance(table, dict):
        return BLOCKED
    return table.get(rung, BLOCKED)


def decide(*, matrix, action_class, rung, document_id, allowlist,
           approval=None) -> dict:
    """May this change be applied now?

    Returns {"allowed", "needs_approval", "reason"}.
    """
    if action_class not in ACTION_CLASSES:
        return {"allowed": False, "needs_approval": False,
                "reason": f"unknown action class {action_class!r}"}
    if rung not in RUNGS:
        return {"allowed": False, "needs_approval": False,
                "reason": f"unknown rung {rung!r}"}

    # A bare string iterates as characters: storing "179" would arm docs 1, 7
    # and 9 while refusing 179.
    if isinstance(allowlist, str):
        return {"allowed": False, "needs_approval": False,
                "reason": ("document_allowlist must be a list, not a string — "
                           "a string would arm each character as a document id")}
    allow = {str(d) for d in (allowlist or [])}
    if str(document_id) not in allow:
        return {"allowed": False, "needs_approval": False,
                "reason": (f"document {document_id} is not on the allowlist — "
                           f"the subsystem may not write to it. Arm it in the "
                           f"Learning tab.")}

    mode = mode_for(matrix, action_class, rung)
    if mode == BLOCKED:
        return {"allowed": False, "needs_approval": False,
                "reason": f"{action_class} is blocked at {rung}"}
    if mode == AUTONOMOUS:
        return {"allowed": True, "needs_approval": False,
                "reason": f"{action_class} is autonomous at {rung}"}

    # ASK: an approval must exist, be affirmative, AND have been granted for
    # THIS rung, action class and document.
    #
    # Reading only `status` meant an approval that auto-proceeded at PAPER on a
    # 4-hour timeout — i.e. one the operator never answered — was accepted as
    # consent for LIVE_CAPPED. Silence below live minted a token that was
    # honoured above it, which defeats the one rule the operator actually
    # stated. It also meant a single explicit "yes" at PAPER carried a change
    # through BOTH live rungs without ever asking again.
    approval = approval if isinstance(approval, dict) else (
        approval.to_doc() if hasattr(approval, "to_doc") else {})
    status = str(approval.get("status") or "").strip().lower()

    if status == "rejected":
        return {"allowed": False, "needs_approval": False,
                "reason": "the operator rejected this proposal"}

    if status in ("approved", "auto_approved"):
        mismatch = []
        if str(approval.get("rung") or "") != rung:
            mismatch.append(f"rung {approval.get('rung')!r} != {rung!r}")
        if str(approval.get("action_class") or action_class) != action_class:
            mismatch.append(
                f"action class {approval.get('action_class')!r} != {action_class!r}")
        if str(approval.get("document_id") or document_id) != str(document_id):
            mismatch.append(
                f"document {approval.get('document_id')!r} != {document_id!r}")
        if mismatch:
            return {"allowed": False, "needs_approval": True,
                    "reason": ("the approval on file was granted for something "
                               "else (" + "; ".join(mismatch) + ") — it is not "
                               "consent for this change")}
        if status == "auto_approved" and rung in LIVE_RUNGS:
            # Belt and braces with `approvals.auto_proceed`, which refuses to
            # mint this. If one ever appears — a hand-written row, an older
            # schema — it is not consent for real money.
            return {"allowed": False, "needs_approval": True,
                    "reason": ("an auto-proceeded approval is never consent at "
                               "a live rung — silence is not a yes for real "
                               "money")}
        return {"allowed": True, "needs_approval": False,
                "reason": f"approved for {rung} ({status})"}

    return {"allowed": False, "needs_approval": True,
            "reason": (f"{action_class} requires approval at {rung}"
                       + (" — live rungs wait indefinitely"
                          if rung in LIVE_RUNGS else ""))}


def revert_allowed(*_args, **_kwargs) -> dict:
    """Reverting is ALWAYS allowed.

    Not a configurable cell in the matrix and not an oversight: a rollback that
    waits for permission is a rollback that does not happen while the operator
    is asleep, which is exactly when it is needed.
    """
    return {"allowed": True, "needs_approval": False,
            "reason": "reverting is a safety action and is never gated"}
