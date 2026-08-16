"""Which runs the engine observes.

A one-function module because the alternative was reaching into the daemon,
which chdir()s and opens a database connection at import — so the predicate
could only be tested by regex-extracting it from the source. Pure helpers live
outside the CLI script here for exactly this reason; see `bot_decision_log`.
"""
from __future__ import annotations


def is_watched(doc, watched) -> bool:
    """Whether this run belongs to an instance the operator asked us to watch.

    An EMPTY watch list means EVERYTHING. Observing is read-only, so watching
    broadly is the useful default and narrowing is the deliberate act — the
    opposite of `document_allowlist`, where empty means write nowhere.

    A run whose instance cannot be determined is watched only when the list is
    empty. Guessing would silently widen the scope the operator set.
    """
    if not watched:
        return True
    doc = doc or {}
    instance = doc.get("instance_id") or doc.get("backtest_instance_id")
    return str(instance or "") in {str(w) for w in watched}
