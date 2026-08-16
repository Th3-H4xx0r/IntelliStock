"""One turn of the loop, as a pure decision function.

The engine is I/O; THIS decides what should happen. Keeping it pure means every
branch that could move real money is testable without a database, a model, or a
broker — and that is the only reason a fully autonomous loop is reviewable at
all.

`plan_turn` returns a list of typed intents. It applies nothing. The engine
executes them, and every intent carries the reason it exists so the operator can
read the loop's thinking in the ledger instead of inferring it from behaviour.

Order is deliberate, and safety-first:

  1. BREAKER — if attributable drawdown breached the limit, unwind the live
     tier and stop. Nothing else in the turn matters.
  2. DEMOTIONS — failing changes come down before anything new goes up.
  3. PROMOTIONS — one rung, and only with judge + proof + a measured floor.
  4. DISPERSION — a target with no floor gets its floor measured, and can do
     nothing else until it has one.
  5. EXPERIMENTS — test the pre-registered hypotheses, budget and lease
     permitting.
  6. PROPOSALS — ask the generator for something new, last, because a loop that
     proposes faster than it tests just grows a backlog.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from self_learning import ladder as laddering
from self_learning import permissions as perms

BREAK = "break_live_tier"
DEMOTE = "demote"
PROMOTE = "promote"
MEASURE_FLOOR = "measure_noise_floor"
RUN_EXPERIMENT = "run_experiment"
REQUEST_APPROVAL = "request_approval"
PROPOSE = "propose_hypothesis"
IDLE = "idle"


@dataclass(frozen=True)
class Intent:
    kind: str
    reason: str
    target: str = ""
    hypothesis_id: str = ""
    experiment_id: str = ""
    document_id: str = ""
    rung: str = ""
    payload: dict = field(default_factory=dict)

    def to_doc(self) -> dict:
        return {"kind": self.kind, "reason": self.reason, "target": self.target,
                "hypothesis_id": self.hypothesis_id,
                "experiment_id": self.experiment_id,
                "document_id": self.document_id, "rung": self.rung,
                "payload": self.payload}


def plan_turn(*, config, matrix, floors, active_changes, hypotheses,
              budget_state, lease_decision, drawdown_pct, breaker_limit_pct,
              targets_seen) -> list:
    """Decide what this turn should do. Applies nothing.

    `active_changes` is the applied set: dicts with target, rung, hypothesis_id,
    document_id, consecutive_failures, judgement, proof, sessions_observed.
    """
    intents = []

    # 1. The breaker outranks everything, including the enabled flag: a
    #    subsystem being switched off does not un-apply what it already did.
    breaker_move = laddering.breaker(drawdown_pct=drawdown_pct,
                                     limit_pct=breaker_limit_pct)
    if breaker_move.direction == "demote":
        return [Intent(kind=BREAK, reason=breaker_move.reason,
                       payload={"transition": breaker_move.to_doc(),
                                "documents": sorted(
                                    {str(c.get("document_id"))
                                     for c in (active_changes or [])
                                     if c.get("rung") in perms.LIVE_RUNGS})})]

    if not (config or {}).get("enabled", True):
        return [Intent(kind=IDLE, reason="the subsystem is disabled")]

    mode = str((config or {}).get("mode") or "observe")
    allowlist = (config or {}).get("document_allowlist") or []

    # 2. Demotions. Never gated, so they are emitted regardless of mode.
    demote_after = int((config or {}).get("demote_after")
                       or laddering.DEFAULT_DEMOTE_AFTER)
    for change in (active_changes or []):
        move = laddering.demote(
            current_rung=str(change.get("rung") or ""),
            consecutive_failures=int(change.get("consecutive_failures") or 0),
            demote_after=demote_after)
        if move.direction == "demote":
            intents.append(Intent(
                kind=DEMOTE, reason=move.reason,
                target=str(change.get("target") or ""),
                hypothesis_id=str(change.get("hypothesis_id") or ""),
                document_id=str(change.get("document_id") or ""),
                rung=move.to_rung, payload={"transition": move.to_doc()}))

    if mode == "observe":
        intents.append(Intent(
            kind=IDLE,
            reason="mode is observe — the subsystem records and reports but "
                   "does not act"))
        return intents

    # 3. Promotions.
    for change in (active_changes or []):
        if any(i.kind == DEMOTE
               and i.hypothesis_id == str(change.get("hypothesis_id") or "")
               for i in intents):
            continue                    # do not promote something coming down
        target = str(change.get("target") or "")
        move = laddering.promote(
            current_rung=str(change.get("rung") or ""),
            judgement=change.get("judgement"), proof=change.get("proof"),
            floor=(floors or {}).get(target),
            sessions_observed=int(change.get("sessions_observed") or 0))
        if move.direction != "promote":
            continue
        gate = perms.decide(matrix=matrix, action_class=str(
            change.get("action_class") or perms.CONFIG_LEVERS),
            rung=move.to_rung, document_id=str(change.get("document_id") or ""),
            allowlist=allowlist, approval=change.get("approval"))
        kind = PROMOTE if gate["allowed"] else (
            REQUEST_APPROVAL if gate["needs_approval"] else IDLE)
        # A blocked promotion is still SAID. Emitting nothing left the operator
        # unable to distinguish "blocked" from "nothing happened" — the same
        # silence that made thirteen inert levers look like null results.
        intents.append(Intent(
            kind=kind, reason=f"{move.reason}; {gate['reason']}",
            target=target,
            hypothesis_id=str(change.get("hypothesis_id") or ""),
            document_id=str(change.get("document_id") or ""),
            rung=move.to_rung, payload={"transition": move.to_doc()}))

    # 4. A target with no measured floor can do exactly one thing.
    for target in sorted(set(targets_seen or [])):
        floor = (floors or {}).get(target)
        if floor is None or not getattr(floor, "measured", False):
            intents.append(Intent(
                kind=MEASURE_FLOOR, target=target,
                reason=("no measured dispersion for this target — until it "
                        "exists nothing here can be promoted, so the only "
                        "permitted experiment is the floor measurement")))

    # 5/6. Experiments and proposals need money and the backtest lease.
    affordable = bool((budget_state or {}).get("remaining_usd", 0) > 0) \
        if isinstance(budget_state, dict) else bool(
            getattr(budget_state, "remaining", 0) > 0)
    if not affordable:
        intents.append(Intent(
            kind=IDLE, reason="the spend ceiling is reached — no experiment "
                              "will be launched until it resets or is raised"))
        return intents
    if lease_decision is not None and not getattr(lease_decision, "granted", False):
        intents.append(Intent(
            kind=IDLE,
            reason=f"backtest lease unavailable: "
                   f"{getattr(lease_decision, 'reason', 'unknown')}"))
        return intents

    for hypothesis in (hypotheses or []):
        status = str((hypothesis or {}).get("status") or "")
        if status == "proposed":
            intents.append(Intent(
                kind=RUN_EXPERIMENT,
                reason="a pre-registered hypothesis is awaiting its experiment",
                target=str(hypothesis.get("target") or ""),
                hypothesis_id=str(hypothesis.get("id") or "")))

    if not any(i.kind in (RUN_EXPERIMENT, MEASURE_FLOOR) for i in intents):
        intents.append(Intent(
            kind=PROPOSE,
            reason=("nothing is queued and every target has a floor — ask the "
                    "generator for a new hypothesis")))
    return intents


def summarise(intents) -> dict:
    counts = {}
    for intent in (intents or []):
        counts[intent.kind] = counts.get(intent.kind, 0) + 1
    return {"intents": len(intents or []), "by_kind": counts,
            "breaking": any(i.kind == BREAK for i in (intents or []))}
