"""Action adapters: apply, roll back, and prove it ran.

Every mutation the subsystem makes goes through one of these. The contract is
three methods and it is not optional:

* `plan(...)` — what would change, as explicit (key, before, after) triples,
  computed against the CURRENT document rather than a remembered one;
* `rollback_token(...)` — everything needed to put it back, captured BEFORE the
  change lands;
* `proof_probe(...)` — what would demonstrate the change reached the decision
  path, so Guard 2 has something positive to check.

The rollback token is captured from the live document at plan time, not
reconstructed later from what we think we set. A config document is shared
mutable state — an operator can edit it between plan and revert — so a token
that says "set it back to 6" would clobber a human's change. The token records
what was there and the revert refuses if the value moved underneath it.

Pure: no I/O. The caller reads and writes the document.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from self_learning.permissions import CODE, CONFIG_LEVERS, LLM_MODELS, UNIVERSE

_MISSING = "\x00absent"


class ActionError(ValueError):
    pass


@dataclass(frozen=True)
class ActionPlan:
    action_class: str
    document_id: str
    changes: tuple            # ((key, before, after), ...)
    rollback_token: dict = field(default_factory=dict)
    proof_probe: dict = field(default_factory=dict)
    notes: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.changes

    def to_doc(self) -> dict:
        return {
            "action_class": self.action_class,
            "document_id": self.document_id,
            "changes": [[k, _plain(b), _plain(a)] for k, b, a in self.changes],
            "rollback_token": self.rollback_token,
            "proof_probe": self.proof_probe,
            "notes": self.notes,
        }


def _plain(value):
    return None if value is _MISSING else value


def _current(config, key):
    return config.get(key, _MISSING) if isinstance(config, dict) else _MISSING


def plan_config_levers(*, document_id, config, changes) -> ActionPlan:
    """`changes` is {key: new_value}.

    A key whose value already equals the requested one is DROPPED. Applying a
    no-op would produce an inert treatment that Guard 2 then has to catch — far
    better not to spend the runs.
    """
    if not isinstance(changes, dict) or not changes:
        raise ActionError("no changes supplied")
    config = config if isinstance(config, dict) else {}

    triples, before_map = [], {}
    for key, after in sorted(changes.items()):
        key = str(key)
        before = _current(config, key)
        if before == after:
            continue
        triples.append((key, before, after))
        before_map[key] = before

    return ActionPlan(
        action_class=CONFIG_LEVERS, document_id=str(document_id),
        changes=tuple(triples),
        rollback_token={"document_id": str(document_id),
                        "before": {k: _plain(v) for k, v in before_map.items()},
                        "absent_keys": sorted(k for k, v in before_map.items()
                                              if v is _MISSING)},
        # Guard 2 wants positive evidence. A config lever's is the config hash
        # plus the keys it touched: if neither the hash nor any decision moved,
        # the lever did not reach the decision path.
        proof_probe={"kind": "config_keys", "keys": [k for k, _b, _a in triples]},
        notes=("no change: every key already holds the requested value"
               if not triples else ""))


def plan_llm_model(*, document_id, config, role_key, model_id) -> ActionPlan:
    """Swap the model behind one LLM role."""
    role_key = str(role_key)
    if not role_key.endswith("llm_model_id"):
        raise ActionError(
            f"{role_key!r} is not an LLM role key — expected one ending in "
            f"'llm_model_id', which is what model_resolver looks for")
    return plan_config_levers(document_id=document_id, config=config,
                              changes={role_key: model_id})._replace_class(LLM_MODELS)


def plan_universe(*, document_id, config, changes) -> ActionPlan:
    return plan_config_levers(document_id=document_id, config=config,
                              changes=changes)._replace_class(UNIVERSE)


def plan_code(*, branch, diff_summary, test_command, revert_commit="") -> ActionPlan:
    """A code change. The rollback token is a REVERT COMMIT, because the backend
    auto-deploys from main: a merged patch is a deployed patch, so 'undo' means
    landing a revert, not editing a document."""
    if not branch:
        raise ActionError("a code action needs a branch")
    return ActionPlan(
        action_class=CODE, document_id=str(branch), changes=(("branch", "", branch),),
        rollback_token={"kind": "revert_commit", "branch": str(branch),
                        "revert_commit": str(revert_commit)},
        proof_probe={"kind": "log_line",
                     "note": "the flag must emit its own log line — this repo "
                             "has shipped 13 levers that never announced "
                             "themselves and were scored as no-effect"},
        notes=f"{diff_summary}; verify with: {test_command}")


def _replace_class(self, action_class):
    # `dataclasses.replace` rather than a hand-listed constructor call: the
    # hand-written list silently dropped any field added later, and it would
    # have dropped it only on the LLM_MODELS and UNIVERSE paths — producing an
    # unrevertable plan with an empty rollback token.
    from dataclasses import replace as _dc_replace
    return _dc_replace(self, action_class=action_class)


ActionPlan._replace_class = _replace_class


def apply_to_config(config, plan: ActionPlan) -> dict:
    """Return a NEW config with the plan applied. Never mutates the input."""
    updated = dict(config or {})
    for key, _before, after in plan.changes:
        updated[key] = after
    return updated


def from_doc(row) -> ActionPlan:
    """Rebuild a persisted plan.

    Without this a plan written to the database could not be turned back into
    an ActionPlan, so the 3am rollback after a container restart had nothing to
    revert with.
    """
    row = dict(row or {})
    return ActionPlan(
        action_class=str(row.get("action_class") or ""),
        document_id=str(row.get("document_id") or ""),
        changes=tuple(tuple(c) for c in (row.get("changes") or [])),
        rollback_token=dict(row.get("rollback_token") or {}),
        proof_probe=dict(row.get("proof_probe") or {}),
        notes=str(row.get("notes") or ""))


def revert_config(config, plan: ActionPlan, *, force: bool = False) -> dict:
    """Put a document back, refusing if it moved underneath us.

    A config document is shared mutable state. If an operator changed the same
    key after the subsystem set it, blindly restoring the old value would
    silently discard a human's edit. `force` exists for the breaker, where
    getting out matters more than politeness.
    """
    config = dict(config or {})
    token = plan.rollback_token or {}

    # A token that cannot restore this plan's keys must RAISE, not quietly
    # return the input unchanged. `plan_code`'s token is a revert commit and has
    # no `before` map at all — the breaker would have fired, "reverted", logged
    # success, and left the change live.
    if "before" not in token:
        raise ActionError(
            f"this rollback token cannot restore a config document "
            f"(kind={token.get('kind') or 'unknown'}) — "
            f"{plan.action_class} changes are not reverted this way")

    before = token.get("before") or {}
    absent = set(token.get("absent_keys") or [])
    missing = [k for k, _b, _a in plan.changes if k not in before]
    if missing:
        raise ActionError(
            f"the rollback token has no prior value for "
            f"{', '.join(sorted(missing))} — reverting would leave the change "
            f"in place while reporting success")

    if not force:
        drifted = []
        for key, _before_value, after in plan.changes:
            current = config.get(key, _MISSING)
            # A tuple applied here comes back from RethinkDB as a list, and
            # ("SPY","QQQ") != ["SPY","QQQ"] would refuse the revert forever.
            if isinstance(current, (list, tuple)) and isinstance(after, (list, tuple)):
                if list(current) != list(after):
                    drifted.append(key)
            elif current != after:
                drifted.append(key)
        if drifted:
            raise ActionError(
                f"refusing to revert: {', '.join(sorted(drifted))} no longer "
                f"holds the value this subsystem set, so someone else changed "
                f"it. Reverting would discard their edit.")

    for key, value in before.items():
        if key in absent:
            config.pop(key, None)
        else:
            config[key] = value
    return config


def is_noop(plan: ActionPlan) -> bool:
    return plan is None or plan.is_empty
