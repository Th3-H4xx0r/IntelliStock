"""The typed store API. Every call site in the repo goes through this module.

Selection is a lazy, immutable query builder (table + WHERE terms + ORDER +
LIMIT/OFFSET). It is never executed until run/iter/count/delete/update touches
it -- matching ReQL, where ``.filter(...).delete()`` is one server-side
statement, not a fetch-then-delete.
"""
from __future__ import annotations

import datetime as _dt
import itertools as _itertools
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

# id() is reused after GC, so it never made a unique cursor name.
_iter_seq = _itertools.count(1)

_DUP_ERROR = "Duplicate primary key `id`"
_C = ' COLLATE "C"'


@dataclass(frozen=True)
class Order:
    field: str
    desc: bool = False
    numeric: bool = False

    def to_sql(self) -> str:
        if self.numeric:
            # The cast is evaluated for EVERY scanned row, so one row holding
            # a string under this key answered "cannot cast jsonb string to
            # type numeric" and poisoned the whole query. A CASE guarantees
            # the untaken branch is never evaluated; a non-number sorts as
            # NULL, which is where ReQL put it too.
            expr = ("(CASE WHEN jsonb_typeof(doc->'%s') = 'number' "
                    "THEN (doc->>'%s')::numeric END)" % (self.field, self.field))
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

    A TEXT table is the opposite: the string is the key, and it is returned
    UNTOUCHED. That is load-bearing for a numeric-looking string key --
    Instances holds the key '10' as a string, and any "normalise it if it
    parses as an int" step would be a no-op at best and, on '007' or '1.0',
    would silently look up a row that does not exist.
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
        with conn.cursor(name="store_iter_%d" % next(_iter_seq)) as cur:
            cur.itersize = int(batch)
            cur.execute(sql_, params)
            for row in cur:
                yield _decode_times(sel.table, row["doc"])


def count(table_or_selection) -> int:
    """ReQL's ``.limit(n).count()`` returns min(n, total), so LIMIT/OFFSET are
    honoured. ORDER BY is dropped: it cannot change a count."""
    sel = _as_selection(table_or_selection)
    where, params = sel.where_sql()
    body = "SELECT 1 FROM %s%s" % (dbschema.quoted(sel.table), where)
    if sel.limit_n is not None:
        body += " LIMIT %d" % int(sel.limit_n)
    if sel.offset_n:
        body += " OFFSET %d" % int(sel.offset_n)
    if sel.limit_n is None and not sel.offset_n:
        body = "SELECT count(*) AS n FROM %s%s" % (dbschema.quoted(sel.table),
                                                   where)
    else:
        body = "SELECT count(*) AS n FROM (%s) AS _c" % body
    with dbpool.cursor() as cur:
        cur.execute(body, params)
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

    # doc->> carries collation "default", not "C": on a prod cluster initdb'd
    # with a locale (stock postgres:17), .lt/.le/.gt/.ge ordered under that
    # locale instead of bytewise. Equality, IS NULL and = ANY are
    # collation-independent, so only the four ordering operators are pinned.
    _ORDERING_OPS = ("<", "<=", ">", ">=")

    def _cmp(self, op: str, value: Any) -> Predicate:
        expr = self.expr + (_C if op in self._ORDERING_OPS else "")
        return Predicate("%s %s %%s" % (expr, op), self.params + (value,))

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

    def is_not_null(self) -> Predicate:
        # NOT the ``~`` of is_null(): that wraps in COALESCE(NOT (...), false),
        # which is opaque to the planner. ``IS NOT NULL`` is indexable.
        return Predicate("%s IS NOT NULL" % self.expr, self.params)


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
                # Guarded: BacktestResults carries instance_id as a NUMBER on
                # 592 rows and a STRING on 833, and an unguarded ::numeric
                # cast on the string rows raises for the whole query where
                # ReQL simply did not match them.
                term = Predicate(
                    "(CASE WHEN jsonb_typeof(doc -> '%s') = 'number' "
                    "THEN (doc -> '%s')::numeric = %%s ELSE false END)"
                    % (key, key), (value,))
            else:
                term = P.field(key).eq(value)
            out = term if out is None else (out & term)
        return out if out is not None else Predicate("true", ())
    raise StoreError("filter needs a dict or a Predicate, got %r" % type(predicate))


def predicate(spec) -> Predicate:
    """A dict-or-Predicate as a Predicate. The dict form is the one that
    carries jsonb-typed equality (the guarded ``::numeric`` compare), so a CAS
    on a numeric ``version`` field goes through here rather than through
    ``P.field('version').eq(str(n))``, which would miss a 3.0 stored for 3."""
    return _predicate_from(spec)


def filter(table: str, predicate) -> Selection:      # noqa: A001 - ReQL's name
    frag, params = _predicate_from(predicate).to_sql()
    return Selection(table).where(frag, params)


def _bound_column(table: str, index: Optional[str]) -> str:
    if index in (None, "id"):
        return "id"
    return '"%s"' % index


# The doc keys each named index is built from. Missing from this map means the
# index is a plain generated column, whose name IS the doc key.
#
# An inner tuple is a coalesce chain (present when ANY of its keys is present);
# the outer tuple is a compound index (present when EVERY component is).
_INDEX_KEYS = {
    "list_ts": (("timestamp",),),
    "instance_ts": (("instance_id", "instance"), ("timestamp",)),
    "instance_or_instance_id": (("instance_id", "instance"),),
    "instance_base": (("instance_id",),),
}


def index_presence(index: Optional[str]) -> Optional[Predicate]:
    """"This row is IN the ``index``" -- or None when every row always is.

    RethinkDB builds a secondary index by evaluating the index function over
    each document and SKIPPING the document when the result is missing or
    null. An index-driven read therefore never sees those rows, in either sort
    direction. Postgres keeps them (the generated column is simply NULL), and
    on a DESC scan it puts them FIRST, so a ported ``order_by(index=...)``
    silently led with rows ReQL would not have returned at all.

    ``doc ->> 'k'`` is NULL for a missing key and for an explicit JSON null
    alike, which is exactly the conflation ReQL makes: an index function that
    returns null omits its document.

    A plain field index is tested on the generated COLUMN -- identical truth
    value to the doc key, and it is what the B-tree is built on, so the
    planner still uses the index.
    """
    if index in (None, "id"):
        return None
    chains = _INDEX_KEYS.get(index)
    if chains is None:
        return Predicate('"%s" IS NOT NULL' % index)
    out = None
    for chain in chains:
        term = None
        for key in chain:
            present = P.field(key).is_not_null()
            term = present if term is None else (term | present)
        out = term if out is None else (out & term)
    return out


def _with_index_presence(sel: Selection, index: Optional[str]) -> Selection:
    pred = index_presence(index)
    if pred is None:
        return sel
    return sel.where(*pred.to_sql())


def between(table: str, lo, hi, *, index: Optional[str] = None,
            left_bound: str = "closed", right_bound: str = "open") -> Selection:
    """NEVER SQL BETWEEN: ReQL is [lo, hi); SQL BETWEEN is [lo, hi].

    r.minval / r.maxval omit the bound entirely, which is how
    interactive_utils.py:5246's [instance, minval] -> [instance, maxval] scan
    on a compound index becomes a plain equality on the instance.
    """
    col = _bound_column(table, index)
    # A row with no value for the index is not IN the index, so between()
    # never returns it -- including the r.minval..r.maxval scan, whose bounds
    # are both omitted and which therefore emitted no WHERE clause at all.
    sel = _with_index_presence(Selection(table), index)
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
    # Ordering THROUGH an index cannot surface a row the index does not hold.
    sel = _with_index_presence(sel, index)
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


def _row_id_or_generate(table: str, doc: Doc):
    """(row_id, doc, generated_key).

    RethinkDB generated a uuid4 for a document with no primary key and
    returned it in ``generated_keys`` -- priceBroker.py:186 writes
    PriceHistory rows that way and has never supplied an id. The generated
    value is written INTO the document, as ReQL did, so a later read sees it.

    Two kinds of table are the exception, and both still raise:

    * a CUSTOM primary key (``pk_field`` is not ``id`` -- the 9 kalshi tables
      key on fixture_id / market_ticker / client_order_id / window /
      instance_id / fixture_key). ReQL minted a uuid only for ``id``; a
      document missing a custom key raised "Primary key `fixture_key` not
      found in document". Minting there writes the row under a key nothing
      ever looks up -- silent data loss where RethinkDB was loud.
    * an ``id_type="int"`` table: a uuid there is exactly the shadow row
      coerce_id exists to forbid.

    PriceHistory keeps the mint: its ``pk_field`` IS ``id`` (only its
    compound ``pk`` names the partition columns), which is why
    priceBroker.py:186 may still write a document with no id at all.
    """
    spec_ = dbschema.spec(table)
    if spec_.pk_field in doc and doc[spec_.pk_field] is not None:
        return coerce_id(table, doc[spec_.pk_field]), doc, None
    if spec_.pk_field != "id" or spec_.id_type == "int":
        raise StoreError("%s: document is missing its primary key field %r"
                         % (table, spec_.pk_field))
    import uuid as _uuid
    key = str(_uuid.uuid4())
    doc = dict(doc)
    doc[spec_.pk_field] = key
    return key, doc, key


def _parse_timestamp(table: str, key: str, value):
    """doc[key] -> an aware datetime, or StoreError. Never a silent drop."""
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    if isinstance(value, str):
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = _dt.datetime.fromisoformat(text)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(
                tzinfo=_dt.timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _dt.datetime.fromtimestamp(float(value), _dt.timezone.utc)
    raise StoreError("%s: document timestamp %r=%r will not parse"
                     % (table, key, value))


def _extra_column_values(table: str, spec_, doc: Doc) -> list:
    """The real (non-generated) columns store.insert must populate itself."""
    out = []
    for col in sorted(spec_.column_sources):
        doc_key, kind = spec_.column_sources[col]
        value = doc.get(doc_key)
        if kind == "timestamp":
            out.append(_parse_timestamp(table, doc_key, value))
        elif value is None:
            raise StoreError("%s: document is missing %r, which column %r "
                             "requires" % (table, doc_key, col))
        else:
            out.append(value if isinstance(value, str) else str(value))
    return out


def _ensure_partitions_for(table: str, spec_, docs) -> None:
    """Create the range partitions this batch needs before writing it.

    pg_partman keeps the rolling window ahead in production, but nothing
    covers a historical row (the migration copy) or a month partman has not
    premade yet, and PriceHistory has NO default partition by design -- the
    insert would fail with "no partition of relation found for row".
    schema.ensure_partitions memoises, so a steady-state write costs nothing.
    """
    doc_key = spec_.column_sources.get(spec_.partitioned.by, (None, None))[0]
    if doc_key is None:
        return
    stamps = []
    for doc in docs:
        try:
            stamps.append(_parse_timestamp(table, doc_key, doc.get(doc_key)))
        except StoreError:
            continue          # the per-row savepoint reports it as errors:1
    if not stamps:
        return
    dbschema.ensure_partitions(table, lo=min(stamps).astimezone(_dt.timezone.utc),
                               hi=max(stamps).astimezone(_dt.timezone.utc))


def insert(table: str, doc_or_docs, *, conflict: str = "error",
           durability: str = "hard") -> InsertResult:
    """``durability`` is accepted and ignored: Postgres is durable by default,
    and the parameter stays in the signature so backtest_replay's interface and
    its ~10 test doubles are unchanged.

    ReQL multi-insert is partial-success; a plain multi-row INSERT is
    all-or-nothing, so each row gets its own savepoint.

    Every document is encoded BEFORE the first row is written. A NaN, a NUL,
    a non-integer id on an int-keyed table or an unparseable PriceHistory
    timestamp is a client-side rejection in RethinkDB too: it raises there
    before anything reaches the server, so nothing must be written here
    either. Encoding inside the write loop meant a bad document in chunk 3
    left chunks 1 and 2 committed and rolled back every good row in its own
    chunk -- a half-written batch that neither store ever produced.
    """
    if conflict not in ("error", "replace", "update"):
        raise StoreError("conflict must be error|replace|update, got %r" % conflict)
    docs = [doc_or_docs] if isinstance(doc_or_docs, dict) else list(doc_or_docs)
    if not docs:
        return InsertResult()
    q = dbschema.quoted(table)
    spec_ = dbschema.spec(table)
    # The conflict target is the table's real primary key, not always (id):
    # PriceHistory's is (ticker, ts, id), and ON CONFLICT (id) there has no
    # matching unique index at all.
    target = ", ".join('"%s"' % c for c in spec_.pk)
    extra_cols = sorted(spec_.column_sources)
    if conflict == "replace":
        tail = (" ON CONFLICT (%s) DO UPDATE SET doc = EXCLUDED.doc, "
                "updated_at = now() WHERE %s.doc IS DISTINCT FROM EXCLUDED.doc"
                % (target, q))
    elif conflict == "update":
        # NOT `||` -- `||` is shallow and silently drops sibling keys.
        tail = (" ON CONFLICT (%s) DO UPDATE SET "
                "doc = jsonb_deep_merge(%s.doc, EXCLUDED.doc), updated_at = now()"
                % (target, q))
    else:
        tail = " ON CONFLICT (%s) DO NOTHING" % target
    columns = ", ".join(['"id"', '"doc"'] + ['"%s"' % c for c in extra_cols])
    placeholders = ", ".join(["%s", "%s::jsonb"] + ["%s"] * len(extra_cols))
    # xmax is a system column, and PG answers "cannot retrieve a system column
    # in this context" when the INSERT target is a PARTITIONED table. Under
    # DO NOTHING a returned row can only be a fresh insert, so the constant
    # carries the same information there; the probe below covers the two
    # upsert modes, which no partitioned-table call site uses today.
    probe_pk = spec_.partitioned is not None and conflict != "error"
    returning = "true" if spec_.partitioned is not None else "(xmax = 0)"
    statement = ("INSERT INTO %s (%s) VALUES (%s)%s "
                 "RETURNING %s AS was_insert"
                 % (q, columns, placeholders, tail, returning))
    probe = ("SELECT 1 FROM %s WHERE %s" %
             (q, " AND ".join('"%s" = %%s' % c for c in spec_.pk)))

    # Client-side validation of the WHOLE batch, before any write.
    encoded = []
    for doc in docs:
        row_id, doc, generated = _row_id_or_generate(table, doc)
        values = [row_id, dbjson.dumps(doc)]     # dumps raises on NaN/NUL
        values.extend(_extra_column_values(table, spec_, doc))
        encoded.append((values, generated,
                        dict(zip(["id"] + extra_cols,
                                 [values[0]] + values[2:]))))

    if spec_.partitioned is not None:
        _ensure_partitions_for(table, spec_, docs)

    inserted = replaced = unchanged = errors = 0
    generated_keys: list = []
    first_error = None
    for start in range(0, len(encoded), WRITE_CHUNK):
        chunk = encoded[start:start + WRITE_CHUNK]
        with dbpool.connection() as conn:
            with conn.cursor() as cur:
                for values, generated, by_column in chunk:
                    # ReQL multi-insert is partial-success: a server-side
                    # rejection of one row is errors:1 and the rest still land.
                    cur.execute("SAVEPOINT s")
                    try:
                        if probe_pk:
                            cur.execute(probe,
                                        tuple(by_column[c] for c in spec_.pk))
                            existed = cur.fetchone() is not None
                        cur.execute(statement, tuple(values))
                        row = cur.fetchone()
                        if probe_pk and row is not None and existed:
                            row = {"was_insert": False}
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
                        if generated is not None:
                            generated_keys.append(generated)
                    elif conflict in ("replace", "update"):
                        replaced += 1
                    else:
                        unchanged += 1
            conn.commit()
    return InsertResult(inserted=inserted, replaced=replaced, unchanged=unchanged,
                        errors=errors, first_error=first_error,
                        generated_keys=generated_keys)


def _selector_to_selection(table: str, selector) -> Selection:
    if isinstance(selector, Selection):
        return selector
    return Selection(table).where("id = %s", (coerce_id(table, selector),))


def update(table: str, selector, patch: Doc) -> WriteResult:
    """UPDATE ... SET doc = jsonb_deep_merge(doc, patch). One statement, even
    over a Selection -- ReQL's .filter(...).update() is server-side too."""
    sel = _selector_to_selection(table, selector)
    where, params = sel.where_sql()
    q = dbschema.quoted(table)
    payload = dbjson.dumps(encode_patch(patch))
    with dbpool.connection() as conn:
        with conn.cursor() as cur:
            # ReQL counted a patch that changed nothing as unchanged, not
            # replaced. The guard makes the UPDATE skip those rows (which also
            # spares the WAL a full doc rewrite); matched-minus-replaced is
            # what ReQL called unchanged. Both statements share one
            # transaction, so no row can move between them.
            cur.execute("SELECT count(*) AS n FROM %s%s" % (q, where), params)
            matched = int(cur.fetchone()["n"])
            replaced_n = 0
            if matched:
                cur.execute(
                    "UPDATE %s SET doc = jsonb_deep_merge(doc, %%s::jsonb), "
                    "updated_at = now()%s%s doc IS DISTINCT FROM "
                    "jsonb_deep_merge(doc, %%s::jsonb)"
                    % (q, where, " AND" if where else " WHERE"),
                    (payload,) + params + (payload,))
                replaced_n = cur.rowcount
        conn.commit()
    if matched == 0 and not isinstance(selector, Selection):
        return WriteResult(skipped=1)
    return WriteResult(replaced=replaced_n, unchanged=matched - replaced_n)


def replace(table: str, row_id, doc: Doc) -> WriteResult:
    """A replace that writes the identical document is ReQL's ``unchanged``;
    a replace of a row that is not there is ReQL's ``skipped``."""
    payload = dbjson.dumps(doc)
    q = dbschema.quoted(table)
    rid = coerce_id(table, row_id)
    with dbpool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE %s SET doc = %%s::jsonb, updated_at = now() "
                        "WHERE id = %%s AND doc IS DISTINCT FROM %%s::jsonb"
                        % q, (payload, rid, payload))
            n = cur.rowcount
            existed = True
            if not n:            # only the no-op path pays for this
                cur.execute("SELECT 1 FROM %s WHERE id = %%s" % q, (rid,))
                existed = cur.fetchone() is not None
        conn.commit()
    if n:
        return WriteResult(replaced=n)
    return WriteResult(unchanged=1) if existed else WriteResult(skipped=1)


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
    if n == 0 and not isinstance(selector, Selection):
        # ReQL's delete(missing_id) reported skipped:1, not deleted:0. A
        # Selection that matches nothing stays deleted:0, as it did there.
        return WriteResult(skipped=1)
    return WriteResult(deleted=n)
