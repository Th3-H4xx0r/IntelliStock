"""Bounded collection of LLM futures, with PER-CALL timeouts.

Every LLM thread pool in the strategy layer used `as_completed(futures)` and
`future.result()` with no timeout. The outer joins that looked like guards
(900s maintenance, 600s macro pipeline) sat ABOVE those unbounded inner waits,
so they could only abandon work — never stop it. One wedged provider request
pinned an entire stage and the outer join then discarded the whole batch.

The timeout here is **per call, not per batch**. A whole-batch budget would
punish a large healthy batch: twenty legitimate 60s calls through four workers
is five honest minutes, and a fixed batch budget would cut it off. Instead the
clock resets every time any future completes — so progress keeps the pool
alive, and only a genuine stall (nothing at all finishing for a full per-call
window) abandons the remainder.

This does NOT kill the worker — Python cannot interrupt a thread blocked in a
socket read. Bounding the provider call itself (llm_utils' per-call deadline)
is what stops the work; this bounds how long the CALLER waits for it.

IMPORTANT: do not pair this with `with ThreadPoolExecutor(...)`. That context
manager exits via `shutdown(wait=True)`, which re-blocks on the very straggler
this just abandoned, undoing the bound. Use `shutdown_bounded(ex)` instead.
"""
from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, wait


class BoundedJoinResult(list):
    """Results that arrived in time, plus what was abandoned."""

    def __init__(self, results, *, abandoned: int = 0, elapsed: float = 0.0):
        super().__init__(results)
        self.abandoned = int(abandoned)
        self.elapsed = float(elapsed)

    @property
    def degraded(self) -> bool:
        """True when the caller is holding an incomplete set.

        Callers must branch on this rather than treating a short list as a
        complete one — silently proceeding on partial LLM output is how a
        timeout becomes an invisible strategy change.
        """
        return self.abandoned > 0


def shutdown_bounded(executor) -> None:
    """Release an executor without waiting on abandoned work.

    `with ThreadPoolExecutor(...)` exits through `shutdown(wait=True)`, so a
    pool whose straggler was just abandoned would block again on exit and the
    bound would buy nothing.
    """
    if executor is None:
        return
    try:
        executor.shutdown(wait=False, cancel_futures=True)
    except TypeError:      # cancel_futures is 3.9+
        try:
            executor.shutdown(wait=False)
        except Exception:
            pass
    except Exception:
        pass


def collect_bounded(futures, *, per_call_timeout, max_total=None,
                    on_error=None, log=None, label="llm"):
    """Collect `futures`, abandoning the remainder once progress stalls.

    `per_call_timeout` is the window ONE call may take. The window restarts
    whenever any future completes, so a batch that keeps making progress is
    never cut short however large it is; only a stall — nothing completing for
    a full window — abandons what is left.

    `max_total` is an optional absolute ceiling for pathological cases where
    calls trickle in just fast enough to keep resetting the window forever.

    `on_error(exc, future)` is called for a future that raised; its result is
    skipped. Exceptions never propagate — one bad worker must not take down
    the batch.

    Results are returned in SUBMISSION order, never completion order. This is
    a correctness requirement, not a nicety: callers `.extend()` these results
    straight into candidate/article lists that are later ranked and truncated,
    so a completion-order list makes the strategy's output depend on which
    provider call happened to return first. `wait()` hands back a *set* of
    futures, and a set of `Future` objects iterates in id()-hash — i.e. memory
    address — order, which no amount of PYTHONHASHSEED can stabilise. Two runs
    of the same backtest would therefore rank the same articles differently.
    Indexing by submission position and emitting in that order removes it.
    """
    ordered = list(futures or [])
    if not ordered:
        return BoundedJoinResult([])
    # First occurrence wins if a caller ever submits the same future twice.
    index_of = {}
    for position, future in enumerate(ordered):
        index_of.setdefault(future, position)
    pending = set(ordered)

    def _seconds(value, fallback):
        try:
            out = float(value)
        except (TypeError, ValueError):
            return fallback
        return out if out > 0 else fallback

    window = _seconds(per_call_timeout, 180.0)
    started = time.monotonic()
    hard_deadline = None
    if max_total is not None:
        hard_deadline = started + _seconds(max_total, window)

    by_index: dict[int, object] = {}
    while pending:
        budget = window
        if hard_deadline is not None:
            budget = min(budget, hard_deadline - time.monotonic())
        if budget <= 0:
            break
        # FIRST_COMPLETED: any completion is progress and restarts the window.
        done, pending = wait(pending, timeout=budget, return_when=FIRST_COMPLETED)
        if not done:
            break          # a full window with nothing finishing = stalled
        # `done` is a set; sort by submission index so the on_error callbacks
        # also fire in a stable order (several of them append to shared lists).
        for future in sorted(done, key=lambda f: index_of.get(f, 0)):
            try:
                by_index[index_of.get(future, len(ordered))] = future.result(timeout=0)
            except Exception as exc:
                if on_error is not None:
                    try:
                        on_error(exc, future)
                    except Exception:
                        pass

    results = [by_index[i] for i in sorted(by_index)]
    abandoned = 0
    for future in pending:
        abandoned += 1
        # Only cancels futures that never started; a running one keeps going.
        # Bounding the provider call is what actually stops that work.
        future.cancel()

    elapsed = time.monotonic() - started
    if abandoned and log is not None:
        try:
            log(
                f"{label}: abandoned {abandoned} future(s) after {elapsed:.1f}s — "
                f"no call completed within {window:.0f}s; results are INCOMPLETE",
                "yellow",
            )
        except Exception:
            pass
    return BoundedJoinResult(results, abandoned=abandoned, elapsed=elapsed)
