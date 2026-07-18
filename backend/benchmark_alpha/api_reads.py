"""Bounded, index-scoped read helpers for the alpha audit APIs (Task 8).

Every query requires an exact instance + origin scope or a run scope; limits
clamp to 1-500; cursors are opaque compound-index positions produced by the
backend. Storage failure surfaces as an error — never an empty success.
"""

DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def clamp_limit(value):
    if value is None:
        return DEFAULT_LIMIT
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, value))


def read_alpha_records(backend, table, *, instance_id, origin, run_id,
                       limit, cursor):
    """Read one page of records from ``table`` via a compound index.

    ``run_id`` selects the ``run_asof`` index; otherwise instance+origin
    select ``instance_origin_asof``. An unscoped query is refused."""
    limit = clamp_limit(limit)
    if run_id:
        return backend.read_by_index(
            table, "run_asof", (str(run_id),), limit=limit, cursor=cursor)
    if instance_id and origin:
        return backend.read_by_index(
            table, "instance_origin_asof", (str(instance_id), str(origin)),
            limit=limit, cursor=cursor)
    raise ValueError(
        "alpha reads require exact scope: run_id, or instance_id + origin")
