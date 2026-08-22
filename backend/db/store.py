"""The typed store API. Every call site in the repo goes through this module.

Selection is a lazy, immutable query builder (table + WHERE terms + ORDER +
LIMIT/OFFSET). It is never executed until run/iter/count/delete/update touches
it -- matching ReQL, where ``.filter(...).delete()`` is one server-side
statement, not a fetch-then-delete.
"""
from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass, field, replace as _replace
from typing import Any, Iterator, Optional, Sequence

from . import json as dbjson
from . import pool as dbpool
from . import schema as dbschema
from .errors import StoreError
from .merge import Literal, deep_merge, encode_patch   # re-exported for callers

Doc = dict

# ReQL fails loudly above a 100k-element array. Nothing in the repo defends
# against it, but nothing proves nothing RELIES on the loud failure either --
# so run() keeps it and iter() is the explicit unbounded path.
PG_MAX_ROWS = int(os.environ.get("PG_MAX_ROWS", "100000"))

WRITE_CHUNK = 500     # self_learning/store.py:64 -- a 15k-document insert is
                      # one oversized request that can fail as a unit.

_DUP_ERROR = "Duplicate primary key `id`"
_C = ' COLLATE "C"'


@dataclass(frozen=True)
class Order:
    field: str
    desc: bool = False
    numeric: bool = False

    def to_sql(self) -> str:
        if self.numeric:
            expr = "(doc->>'%s')::numeric" % self.field
        else:
            expr = "(doc->>'%s')%s" % (self.field, _C)
        return "%s %s" % (expr, "DESC" if self.desc else "ASC")


def asc(field_name: str, *, numeric: bool = False) -> Order:
    return Order(field_name, False, numeric)


def desc(field_name: str, *, numeric: bool = False) -> Order:
    return Order(field_name, True, numeric)


@dataclass(frozen=True)
class _RawOrder:
    """An ORDER BY term over an index expression rather than a doc key.

    Duck-typed against Order: Selection.to_sql only calls .to_sql() on each
    element, so the two types are interchangeable there.
    """
    sql_text: str

    def to_sql(self) -> str:
        return self.sql_text


@dataclass(frozen=True)
class Selection:
    table: str
    terms: tuple = ()          # ((sql_fragment, params_tuple), ...)
    orders: tuple = ()
    limit_n: Optional[int] = None
    offset_n: int = 0

    def where(self, fragment: str, params: Sequence = ()) -> "Selection":
        return _replace(self, terms=self.terms + ((fragment, tuple(params)),))

    def ordered(self, orders: Sequence) -> "Selection":
        return _replace(self, orders=tuple(orders))

    def with_limit(self, n: Optional[int], offset: int = 0) -> "Selection":
        return _replace(self, limit_n=n, offset_n=offset)

    def where_sql(self):
        if not self.terms:
            return "", ()
        params: list = []
        frags = []
        for frag, prms in self.terms:
            frags.append("(%s)" % frag)
            params.extend(prms)
        return " WHERE " + " AND ".join(frags), tuple(params)

    def to_sql(self, columns: str = "doc"):
        where, params = self.where_sql()
        sql_ = "SELECT %s FROM %s%s" % (columns, dbschema.quoted(self.table), where)
        if self.orders:
            sql_ += " ORDER BY " + ", ".join(o.to_sql() for o in self.orders)
        if self.limit_n is not None:
            sql_ += " LIMIT %d" % int(self.limit_n)
        if self.offset_n:
            sql_ += " OFFSET %d" % int(self.offset_n)
        return sql_, params


def _as_selection(target) -> Selection:
    return target if isinstance(target, Selection) else Selection(str(target))


def coerce_id(table: str, value: Any) -> str:
    """RethinkDB primary keys are type-strict. ``id`` is text in every Postgres
    table, so an int-keyed table coerces both ways: get(t, 460555) and
    get(t, "460555") must return the same row. A non-integer input to an
    id_type="int" table raises rather than creating a shadow row.
    """
    spec_ = dbschema.spec(table)
    if spec_.id_type == "int":
        try:
            return str(int(value))
        except (TypeError, ValueError):
            raise StoreError("%s.id must be an integer, got %r" % (table, value))
    return value if isinstance(value, str) else str(value)


def _decode_times(table: str, doc):
    """Decode TableSpec.time_fields back to timezone-aware datetimes, so call
    sites that relied on the RethinkDB driver's datetime objects are
    unchanged. Anything not listed stays the ISO string it already was."""
    spec_ = dbschema.spec(table)
    if not spec_.time_fields or not isinstance(doc, dict):
        return doc
    out = dict(doc)
    for key in spec_.time_fields:
        value = out.get(key)
        if isinstance(value, str):
            try:
                out[key] = _dt.datetime.fromisoformat(value)
            except ValueError:
                pass
    return out


def get(table: str, row_id: Any) -> Optional[Doc]:
    with dbpool.cursor() as cur:
        cur.execute('SELECT doc FROM %s WHERE id = %%s' % dbschema.quoted(table),
                    (coerce_id(table, row_id),))
        row = cur.fetchone()
    return _decode_times(table, row["doc"]) if row else None


def get_all(table: str, *keys: Any, index: Optional[str] = None) -> list:
    """No dedupe: get_all("a","a","b") returns 3 rows if all exist, matching
    ReQL. ``= ANY()`` would collapse them, so this joins against an ordinal
    unnest instead."""
    if not keys:
        return []
    column = index or "id"
    if column == "id":
        values = [coerce_id(table, k) for k in keys]
    else:
        values = [k if isinstance(k, str) else str(k) for k in keys]
    with dbpool.cursor() as cur:
        cur.execute(
            "SELECT t.doc FROM unnest(%%s::text[]) WITH ORDINALITY AS k(v, ord) "
            'JOIN %s t ON t."%s" = k.v ORDER BY k.ord'
            % (dbschema.quoted(table), column),
            (values,))
        rows = cur.fetchall()
    return [_decode_times(table, r["doc"]) for r in rows]


def run(selection) -> list:
    sel = _as_selection(selection)
    sql_, params = sel.to_sql()
    with dbpool.cursor() as cur:
        cur.execute(sql_, params)
        rows = cur.fetchall()
    if sel.limit_n is None and len(rows) > PG_MAX_ROWS:
        raise StoreError(
            "%s returned %d rows, above PG_MAX_ROWS=%d; use store.iter()"
            % (sel.table, len(rows), PG_MAX_ROWS))
    return [_decode_times(sel.table, r["doc"]) for r in rows]


def iter(selection, *, batch: int = 1000) -> Iterator:   # noqa: A001 - ReQL's name
    """Server-side cursor. The explicit unbounded path: the migration script,
    pg_retention, clear_instance_state's PK materialisation, and assemble()."""
    sel = _as_selection(selection)
    sql_, params = sel.to_sql()
    with dbpool.connection() as conn:
        with conn.cursor(name="store_iter_%d" % id(sel)) as cur:
            cur.itersize = int(batch)
            cur.execute(sql_, params)
            for row in cur:
                yield _decode_times(sel.table, row["doc"])


def count(table_or_selection) -> int:
    sel = _as_selection(table_or_selection)
    where, params = sel.where_sql()
    with dbpool.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM %s%s"
                    % (dbschema.quoted(sel.table), where), params)
        return int(cur.fetchone()["n"])


def limit(selection, n: int) -> Selection:
    return _as_selection(selection).with_limit(int(n))


def slice(selection, start: int, end: int) -> Selection:   # noqa: A001
    return _as_selection(selection).with_limit(int(end) - int(start), int(start))


def pluck(rows_or_selection, *fields):
    """ReQL omits missing fields. jsonb_build_object('a', doc->'a') would
    yield {"a": null} and flip every ``if 'a' in result`` in the codebase, so
    this is done in Python over the materialised rows."""
    rows = rows_or_selection
    if isinstance(rows, Selection):
        rows = run(rows)
    return [_pluck_one(row, fields) for row in rows]


def _pluck_one(row, fields):
    picked = {}
    for f in fields:
        if isinstance(f, dict):
            for key, sub in f.items():
                if key in row and isinstance(row[key], dict):
                    picked[key] = _pluck_one(row[key], tuple(sub))
                elif key in row:
                    picked[key] = row[key]
        elif f in row:
            picked[f] = row[f]
    return picked


def sql(query: str, params=()) -> list:
    """Escape hatch for the handful of sites that need hand-written SQL (the
    BacktestResults list endpoint, the migration verifier). Predicates are
    never string-interpolated with user data; pass everything through params.

    ``params`` may be a sequence for ``%s`` placeholders or a mapping for
    ``%(name)s`` placeholders. A mapping is passed through unchanged; anything
    else is coerced to a tuple. Commits, so a write issued here is durable.
    """
    from collections.abc import Mapping
    from psycopg.rows import dict_row
    bound = params if isinstance(params, Mapping) else tuple(params)
    with dbpool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, bound)
            rows = list(cur.fetchall()) if cur.description is not None else []
        conn.commit()
    return rows


def table_list() -> list:
    rows = sql("SELECT tablename FROM pg_tables WHERE schemaname = "
               "ANY(current_schemas(false)) ORDER BY tablename")
    return [r["tablename"] for r in rows]


def table_create(name: str, *, primary_key: str = "id") -> bool:
    """False when the table already existed, matching the ensure-blocks that
    swallow RethinkDB's ReqlOpFailedError today."""
    if name in table_list():
        return False
    if primary_key != "id" and dbschema.spec(name).pk_field != primary_key:
        raise StoreError(
            "%s: primary_key=%r contradicts the registry (%r); update db/schema.py"
            % (name, primary_key, dbschema.spec(name).pk_field))
    dbschema.ensure_table(name)
    return True


def index_list(table: str) -> list:
    """The ReQL index names for a table: the generated-column indexes and the
    compound expression indexes, with the "<Table>_"/"_idx" affixes stripped."""
    spec_ = dbschema.spec(table)
    known = set(spec_.indexed_fields) | set(spec_.compound_indexes) | set(spec_.generated)
    rows = sql("SELECT indexname FROM pg_indexes WHERE tablename = %s", (table,))
    out = []
    for r in rows:
        name = r["indexname"]
        if name.startswith(table + "_") and name.endswith("_idx"):
            short = name[len(table) + 1:-4]
            if short in known:
                out.append(short)
    return sorted(out)


class _Sentinel:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return "store.%s" % self.name


MINVAL = _Sentinel("MINVAL")     # r.minval: omit the lower bound
MAXVAL = _Sentinel("MAXVAL")     # r.maxval: omit the upper bound


def escape_like(value: str) -> str:
    """Escape the three LIKE metacharacters. '|' needs no escaping here --
    only the regex form did."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True)
class Predicate:
    fragment: str
    params: tuple = ()

    def to_sql(self):
        return self.fragment, self.params

    def __and__(self, other: "Predicate") -> "Predicate":
        return Predicate("(%s) AND (%s)" % (self.fragment, other.fragment),
                         self.params + other.params)

    def __or__(self, other: "Predicate") -> "Predicate":
        return Predicate("(%s) OR (%s)" % (self.fragment, other.fragment),
                         self.params + other.params)

    def __invert__(self) -> "Predicate":
        # NOT NULL-safe: a NULL comparison is false, and its negation must be
        # false too, matching "row lacks the key" in ReQL rather than flipping.
        return Predicate("COALESCE(NOT (%s), false)" % self.fragment, self.params)


class FieldRef:
    """One doc key, plus the transforms the ported call sites actually use."""

    __slots__ = ("expr", "params")

    def __init__(self, expr: str, params: tuple = ()) -> None:
        self.expr = expr
        self.params = params

    def default(self, value: Any) -> "FieldRef":
        return FieldRef("coalesce(%s, %%s)" % self.expr, self.params + (value,))

    def coerce_to_string(self) -> "FieldRef":
        # ->> already stringifies; a JSON number 5 becomes '5'.
        return FieldRef(self.expr, self.params)

    def downcase(self) -> "FieldRef":
        return FieldRef("lower(%s)" % self.expr, self.params)

    def split_nth(self, sep: str, n: int) -> "FieldRef":
        # split_part is 1-based; ReQL .nth(0) is index 1.
        return FieldRef("split_part(%s, %%s, %%s)" % self.expr,
                        self.params + (sep, int(n) + 1))

    def _cmp(self, op: str, value: Any) -> Predicate:
        return Predicate("%s %s %%s" % (self.expr, op), self.params + (value,))

    def eq(self, value: Any) -> Predicate:
        return self._cmp("=", value)

    def ne(self, value: Any) -> Predicate:
        return self._cmp("<>", value)

    def lt(self, value: Any) -> Predicate:
        return self._cmp("<", value)

    def le(self, value: Any) -> Predicate:
        return self._cmp("<=", value)

    def gt(self, value: Any) -> Predicate:
        return self._cmp(">", value)

    def ge(self, value: Any) -> Predicate:
        return self._cmp(">=", value)

    def is_in(self, seq: Sequence) -> Predicate:
        values = [v if isinstance(v, str) else str(v) for v in seq]
        if not values:
            return Predicate("false", ())
        return Predicate("%s = ANY(%%s)" % self.expr, self.params + (values,))

    def match(self, regex: str) -> Predicate:
        return Predicate("%s ~ %%s" % self.expr, self.params + (regex,))

    def starts_with(self, prefix: str) -> Predicate:
        """The LIKE form of a prefix scan. Under COLLATE "C" it returns exactly
        the same rows as between(prefix, prefix + '\\uffff',
        right_bound='closed'), and it is backed by the "_pfx"
        text_pattern_ops index."""
        return Predicate("%s LIKE %%s" % self.expr,
                         self.params + (escape_like(prefix) + "%",))

    def is_null(self) -> Predicate:
        return Predicate("%s IS NULL" % self.expr, self.params)


class P:
    @staticmethod
    def field(key: str) -> FieldRef:
        if "'" in key:
            raise StoreError("illegal doc key %r" % key)
        return FieldRef("doc->>'%s'" % key)


def _predicate_from(predicate) -> Predicate:
    if isinstance(predicate, Predicate):
        return predicate
    if isinstance(predicate, dict):
        out = None
        for key, value in predicate.items():
            if value is None:
                # A dict value of None means the key is JSON null, not absent.
                term = Predicate("doc -> '%s' = 'null'::jsonb" % key, ())
            elif isinstance(value, bool):
                term = Predicate("doc -> '%s' = %%s::jsonb" % key,
                                 ("true" if value else "false",))
            elif isinstance(value, (int, float)):
                term = Predicate("(doc -> '%s')::numeric = %%s" % key, (value,))
            else:
                term = P.field(key).eq(value)
            out = term if out is None else (out & term)
        return out if out is not None else Predicate("true", ())
    raise StoreError("filter needs a dict or a Predicate, got %r" % type(predicate))


def filter(table: str, predicate) -> Selection:      # noqa: A001 - ReQL's name
    frag, params = _predicate_from(predicate).to_sql()
    return Selection(table).where(frag, params)


def _bound_column(table: str, index: Optional[str]) -> str:
    if index in (None, "id"):
        return "id"
    return '"%s"' % index


def between(table: str, lo, hi, *, index: Optional[str] = None,
            left_bound: str = "closed", right_bound: str = "open") -> Selection:
    """NEVER SQL BETWEEN: ReQL is [lo, hi); SQL BETWEEN is [lo, hi].

    r.minval / r.maxval omit the bound entirely, which is how
    interactive_utils.py:5246's [instance, minval] -> [instance, maxval] scan
    on a compound index becomes a plain equality on the instance.
    """
    col = _bound_column(table, index)
    sel = Selection(table)
    if lo is not None and not isinstance(lo, _Sentinel):
        op = ">" if left_bound == "open" else ">="
        sel = sel.where("%s %s %%s" % (col, op),
                        (coerce_id(table, lo) if col == "id" else lo,))
    if hi is not None and not isinstance(hi, _Sentinel):
        op = "<=" if right_bound == "closed" else "<"
        sel = sel.where("%s %s %%s" % (col, op),
                        (coerce_id(table, hi) if col == "id" else hi,))
    return sel


_INDEX_ORDER = {
    "list_ts": "(coalesce(doc->>'timestamp',''))",
    "instance_ts": "(coalesce(doc->>'instance_id', doc->>'instance',''))",
}


def order_by(selection, *, index: Optional[str] = None,
             fields: Sequence = (), desc: bool = False) -> Selection:   # noqa: A002
    """ORDER BY over text always carries COLLATE "C"."""
    sel = _as_selection(selection)
    if index is None:
        return sel.ordered(tuple(fields))
    expr = _INDEX_ORDER.get(index)
    if expr is None:
        expr = "id" if index == "id" else '"%s"' % index
    direction = "DESC" if desc else "ASC"
    return sel.ordered(sel.orders + (_RawOrder("%s%s %s" % (expr, _C, direction)),))


class _ResultMixin:
    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def get(self, key, default=None):
        return getattr(self, key, default)


@dataclass(frozen=True)
class InsertResult(_ResultMixin):
    inserted: int = 0
    replaced: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0
    first_error: Optional[str] = None
    generated_keys: list = field(default_factory=list)


@dataclass(frozen=True)
class WriteResult(_ResultMixin):
    replaced: int = 0
    unchanged: int = 0
    deleted: int = 0
    skipped: int = 0
    errors: int = 0
    first_error: Optional[str] = None


def _row_id_for(table: str, doc: Doc) -> str:
    spec_ = dbschema.spec(table)
    if spec_.pk_field not in doc or doc[spec_.pk_field] is None:
        raise StoreError("%s: document is missing its primary key field %r"
                         % (table, spec_.pk_field))
    return coerce_id(table, doc[spec_.pk_field])


def insert(table: str, doc_or_docs, *, conflict: str = "error",
           durability: str = "hard") -> InsertResult:
    """``durability`` is accepted and ignored: Postgres is durable by default,
    and the parameter stays in the signature so backtest_replay's interface and
    its ~10 test doubles are unchanged.

    ReQL multi-insert is partial-success; a plain multi-row INSERT is
    all-or-nothing, so each row gets its own savepoint.
    """
    if conflict not in ("error", "replace", "update"):
        raise StoreError("conflict must be error|replace|update, got %r" % conflict)
    docs = [doc_or_docs] if isinstance(doc_or_docs, dict) else list(doc_or_docs)
    if not docs:
        return InsertResult()
    q = dbschema.quoted(table)
    if conflict == "replace":
        tail = (" ON CONFLICT (id) DO UPDATE SET doc = EXCLUDED.doc, "
                "updated_at = now() WHERE %s.doc IS DISTINCT FROM EXCLUDED.doc" % q)
    elif conflict == "update":
        # NOT `||` -- `||` is shallow and silently drops sibling keys.
        tail = (" ON CONFLICT (id) DO UPDATE SET "
                "doc = jsonb_deep_merge(%s.doc, EXCLUDED.doc), updated_at = now()" % q)
    else:
        tail = " ON CONFLICT (id) DO NOTHING"
    statement = ("INSERT INTO %s (id, doc) VALUES (%%s, %%s::jsonb)%s "
                 "RETURNING (xmax = 0) AS was_insert" % (q, tail))

    inserted = replaced = unchanged = errors = 0
    first_error = None
    for start in range(0, len(docs), WRITE_CHUNK):
        chunk = docs[start:start + WRITE_CHUNK]
        with dbpool.connection() as conn:
            with conn.cursor() as cur:
                for doc in chunk:
                    row_id = _row_id_for(table, doc)
                    payload = dbjson.dumps(doc)      # raises on NaN, at the client
                    cur.execute("SAVEPOINT s")
                    try:
                        cur.execute(statement, (row_id, payload))
                        row = cur.fetchone()
                        cur.execute("RELEASE SAVEPOINT s")
                    except Exception as exc:
                        cur.execute("ROLLBACK TO SAVEPOINT s")
                        errors += 1
                        first_error = first_error or str(exc)
                        continue
                    if row is None:
                        # DO NOTHING absorbed it (conflict='error'), or the
                        # replace guard filtered an identical write.
                        if conflict == "replace":
                            unchanged += 1
                        else:
                            errors += 1
                            first_error = first_error or _DUP_ERROR
                    elif row["was_insert"]:
                        inserted += 1
                    elif conflict in ("replace", "update"):
                        replaced += 1
                    else:
                        unchanged += 1
            conn.commit()
    return InsertResult(inserted=inserted, replaced=replaced, unchanged=unchanged,
                        errors=errors, first_error=first_error)


def _selector_to_selection(table: str, selector) -> Selection:
    if isinstance(selector, Selection):
        return selector
    return Selection(table).where("id = %s", (coerce_id(table, selector),))


def update(table: str, selector, patch: Doc) -> WriteResult:
    """UPDATE ... SET doc = jsonb_deep_merge(doc, patch). One statement, even
    over a Selection -- ReQL's .filter(...).update() is server-side too."""
    sel = _selector_to_selection(table, selector)
    where, params = sel.where_sql()
    payload = dbjson.dumps(encode_patch(patch))
    with dbpool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE %s SET doc = jsonb_deep_merge(doc, %%s::jsonb), "
                "updated_at = now()%s" % (dbschema.quoted(table), where),
                (payload,) + params)
            n = cur.rowcount
        conn.commit()
    if n == 0 and not isinstance(selector, Selection):
        return WriteResult(skipped=1)
    return WriteResult(replaced=n)


def replace(table: str, row_id, doc: Doc) -> WriteResult:
    payload = dbjson.dumps(doc)
    with dbpool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE %s SET doc = %%s::jsonb, updated_at = now() "
                        "WHERE id = %%s" % dbschema.quoted(table),
                        (payload, coerce_id(table, row_id)))
            n = cur.rowcount
        conn.commit()
    return WriteResult(replaced=n) if n else WriteResult(skipped=1)


def replace_if(table: str, row_id, *, when, doc: Doc,
               insert_if_absent: bool = False) -> Optional[Doc]:
    """Atomic compare-and-swap, backing the 5 ``.replace(lambda row:
    r.branch(...))`` sites. Returns the document on success and None when the
    predicate did not hold. A missing row is NOT conflated with a failed
    predicate: it raises unless insert_if_absent is set.
    """
    rid = coerce_id(table, row_id)
    q = dbschema.quoted(table)
    payload = dbjson.dumps(doc)
    frag, params = (when.to_sql() if when is not None else ("true", ()))
    with dbpool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM %s WHERE id = %%s FOR UPDATE" % q, (rid,))
            exists = cur.fetchone() is not None
            if not exists:
                if not insert_if_absent:
                    conn.rollback()
                    raise StoreError("%s: row %r does not exist" % (table, row_id))
                cur.execute("INSERT INTO %s (id, doc) VALUES (%%s, %%s::jsonb) "
                            "ON CONFLICT (id) DO NOTHING" % q, (rid, payload))
                conn.commit()
                return doc
            cur.execute("UPDATE %s SET doc = %%s::jsonb, updated_at = now() "
                        "WHERE id = %%s AND (%s)" % (q, frag),
                        (payload, rid) + params)
            n = cur.rowcount
        conn.commit()
    return doc if n else None


def delete(table: str, selector) -> WriteResult:
    """On a Selection this is ONE statement -- never fetch-then-delete."""
    sel = _selector_to_selection(table, selector)
    where, params = sel.where_sql()
    with dbpool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM %s%s" % (dbschema.quoted(table), where), params)
            n = cur.rowcount
        conn.commit()
    return WriteResult(deleted=n)
