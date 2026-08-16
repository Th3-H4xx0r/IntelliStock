"""Prompt construction for the four roles, as pure functions.

Kept out of the LLM call sites so the exact text handed to a model is testable
without a network, and so the prompt HASH — which is recorded on every output —
is reproducible.

The generator prompt is the one that matters. It carries three things the first
version of this project never gave a human either:

* the target strategy's DECLARED SCHEMA, so a proposal names real levers;
* the measured NOISE FLOOR, so a proposal is forced to predict something big
  enough to be detectable rather than "it should help a bit";
* the REJECTED hypotheses, so it stops re-proposing what has already failed.
"""
from __future__ import annotations

import hashlib
import json

MAX_LEVERS_IN_PROMPT = 120
MAX_REJECTIONS_IN_PROMPT = 15

_ANALYST_SYSTEM = (
    "You analyse a trading system's own decision log. You are given counts, not "
    "opinions. Report what the numbers say and nothing beyond them. If the data "
    "cannot support a conclusion, say that instead of producing one."
)

_GENERATOR_SYSTEM = (
    "You propose ONE change to a trading strategy's configuration, and you "
    "commit to a prediction that can be proven wrong.\n"
    "Rules:\n"
    "1. Name only levers that appear in the supplied schema. A lever that does "
    "not exist cannot be tested.\n"
    "2. Give a MECHANISM — the causal chain from the change to the outcome. "
    "'It should help' is not a mechanism, and a null result against it teaches "
    "nothing.\n"
    "3. Predict a DIRECTION and a MAGNITUDE RANGE in percentage points. Your "
    "range must exceed the measured noise floor you are given, or the "
    "experiment cannot detect it and will not be run.\n"
    "4. Do not re-propose anything in the rejected list."
)

_JUDGE_SYSTEM = (
    "You are given a prediction that was registered BEFORE an experiment ran, "
    "and the measured result. Decide whether the result honoured the "
    "prediction.\n"
    "Answer with exactly one of: confirm, hold, demote, veto.\n"
    "You cannot promote something the statistics rejected — that decision is "
    "already made and is not yours. You can only be more cautious than it."
)


def prompt_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:32]


def _json_block(payload) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _finding_digest(findings) -> list:
    return [{"kind": f.get("kind"), "title": f.get("title"),
             "detail": f.get("detail")} for f in (findings or [])]


def analyst_prompt(*, target, summary, findings, refusal_cost) -> tuple:
    """(system, user). Facts only — the analyst does not get to speculate."""
    user = (
        f"Trading target: {target}\n\n"
        f"Run funnel:\n{_json_block(summary)}\n\n"
        f"Refusal cost (what the names it declined did next):\n"
        f"{_json_block(refusal_cost)}\n\n"
        f"Guard findings already raised:\n"
        f"{_json_block(_finding_digest(findings))}\n\n"
        "Write at most 200 words explaining what this run did and what the "
        "refusals cost. Do not propose changes."
    )
    return _ANALYST_SYSTEM, user


def generator_prompt(*, target, levers, noise_floor, summary, refusal_cost,
                     findings, rejected) -> tuple:
    """(system, user) for the hypothesis generator."""
    lever_lines = [
        f"- {l.get('key')} ({l.get('value_type')}, default={l.get('default')!r})"
        for l in (levers or [])[:MAX_LEVERS_IN_PROMPT]
    ]
    truncated = max(0, len(levers or []) - MAX_LEVERS_IN_PROMPT)
    rejected_lines = [
        f"- {r.get('claim')} -> {r.get('status')}: {r.get('status_reason') or 'no reason recorded'}"
        for r in (rejected or [])[:MAX_REJECTIONS_IN_PROMPT]
    ]

    floor_pp = (noise_floor or {}).get("floor_pp")
    floor_note = (
        f"The measured noise floor for this target is {floor_pp}pp. Two runs of "
        f"the same window under an identical config differ by about that much, "
        f"so any effect smaller than it is undetectable and the experiment will "
        f"not be run."
        if (noise_floor or {}).get("measured")
        else "NO noise floor has been measured for this target yet, so nothing "
             "can be promoted from it. Propose only if the effect you expect is "
             "large and obvious."
    )

    user = (
        f"Trading target: {target}\n\n"
        f"{floor_note}\n\n"
        f"Run funnel:\n{_json_block(summary)}\n\n"
        f"What the refusals cost:\n{_json_block(refusal_cost)}\n\n"
        f"Guard findings:\n{_json_block(_finding_digest(findings))}\n\n"
        f"Available levers ({len(levers or [])} total"
        + (f", showing {MAX_LEVERS_IN_PROMPT}" if truncated else "") + "):\n"
        + "\n".join(lever_lines) + "\n\n"
        + ("Already tried and closed — do NOT propose these again:\n"
           + "\n".join(rejected_lines) + "\n\n" if rejected_lines else "")
        + "Return JSON only:\n"
        '{"claim": "...", "mechanism": "...", "lever_keys": ["..."], '
        '"predicted_direction": "increase"|"decrease", '
        '"predicted_min_pp": 0.0, "predicted_max_pp": 0.0}'
    )
    return _GENERATOR_SYSTEM, user


def judge_prompt(context) -> tuple:
    """(system, user). `context` comes from `judge.judge_prompt_context`, which
    deliberately excludes the generator's reasoning."""
    user = (
        f"{_json_block(context)}\n\n"
        'Return JSON only: {"verdict": "confirm"|"hold"|"demote"|"veto", '
        '"reason": "..."}'
    )
    return _JUDGE_SYSTEM, user
