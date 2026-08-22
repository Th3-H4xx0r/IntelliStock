"""An in-process store over Python dicts, API-identical to db.store.

It exists so the ~475 existing tests keep running on a laptop with no
database. It is NOT a validation target: only real Postgres can prove
collation, jsonb_deep_merge, and LISTEN/NOTIFY, and every test that proves
those skips without PG_TEST_DSN.

The merge is db.merge.deep_merge -- shared code, not a re-implementation --
so FakeStore cannot drift from the SQL twin on the one semantic that has 149
call sites.

Predicates are not re-derived either: the query builders (filter, between,
order_by, limit, slice) are the real ones, so a FakeStore query carries the
identical Selection. What this module adds is a small interpreter for the
bounded SQL grammar db.store's FieldRef emits, evaluated over a Python dict
with Postgres' three-valued logic. Anything outside that grammar raises
StoreError rather than silently returning the wrong rows.
"""
from __future__ import annotations

import re
from typing import Any, Iterator, Optional, Sequence

from . import json as dbjson
from . import schema as dbschema
from . import store as _s
from .errors import StoreError
from .merge import deep_merge


class FakeStore:
    """The db.store module surface, backed by ``{table: {id: doc}}``."""

    # The query builders are the real ones -- a FakeStore query carries the
    # identical Selection, so nothing about the predicate grammar is
    # re-derived here. Exposed as attributes so a call site that holds the
    # store as one object (``store.filter(...)`` on a monkeypatched module
    # attribute) works against the fake exactly as it does against db.store.
    filter = staticmethod(_s.filter)
    between = staticmethod(_s.between)
    order_by = staticmethod(_s.order_by)
    limit = staticmethod(_s.limit)
    slice = staticmethod(_s.slice)
    asc = staticmethod(_s.asc)
    desc = staticmethod(_s.desc)
    coerce_id = staticmethod(_s.coerce_id)
    Selection = _s.Selection

    def __init__(self) -> None:
        self._tables: dict = {}

    # -- helpers ----------------------------------------------------------
    def clear(self) -> None:
        self._tables.clear()

    def _rows(self, table: str) -> dict:
        return self._tables.setdefault(table, {})

    def _match(self, table: str, row: dict, sel) -> bool:
        for frag, params in getattr(sel, "terms", ()):
            # A WHERE clause keeps a row only on TRUE; NULL is not TRUE.
            if _eval_fragment(frag, params, row, table) is not True:
                return False
        return True

    def _select(self, sel) -> list:
        sel = sel if isinstance(sel, _s.Selection) else _s.Selection(str(sel))
        rows = [r for r in self._rows(sel.table).values()
                if self._match(sel.table, r, sel)]
        for order in reversed(sel.orders):
            keyfn, descending = _order_key(sel.table, order)
            rows.sort(key=keyfn, reverse=descending)
        if sel.offset_n:
            rows = rows[sel.offset_n:]
        if sel.limit_n is not None:
            rows = rows[:sel.limit_n]
        return [dict(r) for r in rows]

    # -- reads ------------------------------------------------------------
    def get(self, table: str, row_id: Any):
        row = self._rows(table).get(_s.coerce_id(table, row_id))
        return dict(row) if row is not None else None

    def get_all(self, table: str, *keys: Any, index: Optional[str] = None) -> list:
        out = []
        for key in keys:                          # no dedupe, matching ReQL
            if index in (None, "id"):
                row = self._rows(table).get(_s.coerce_id(table, key))
                if row is not None:
                    out.append(dict(row))
            else:
                for row in self._rows(table).values():
                    value = row.get(index)
                    if value is not None and _scalar_str(value) == _text(key):
                        out.append(dict(row))
        return out

    def run(self, selection) -> list:
        rows = self._select(selection)
        limit_n = getattr(selection, "limit_n", None)
        if limit_n is None and len(rows) > _s.PG_MAX_ROWS:
            table = getattr(selection, "table", selection)
            raise StoreError(
                "%s returned %d rows, above PG_MAX_ROWS=%d; use store.iter()"
                % (table, len(rows), _s.PG_MAX_ROWS))
        return rows

    def iter(self, selection, *, batch: int = 1000) -> Iterator:   # noqa: A003
        for row in self._select(selection):
            yield row

    def count(self, table_or_selection) -> int:
        sel = table_or_selection
        if isinstance(sel, _s.Selection):
            # LIMIT/OFFSET are honoured (ReQL's .limit(n).count() is
            # min(n, total)); ORDER BY cannot change a count.
            sel = sel.ordered(())
        return len(self._select(sel))

    def pluck(self, rows_or_selection, *fields):
        rows = (self._select(rows_or_selection)
                if isinstance(rows_or_selection, _s.Selection)
                else rows_or_selection)
        return _s.pluck(rows, *fields)

    def sql(self, query: str, params: Sequence = ()) -> list:
        raise NotImplementedError(
            "FakeStore has no SQL engine; this test needs PG_TEST_DSN")

    def table_list(self) -> list:
        return sorted(self._tables)

    def table_create(self, name: str, *, primary_key: str = "id") -> bool:
        if name in self._tables:
            return False
        if primary_key != "id" and dbschema.spec(name).pk_field != primary_key:
            raise StoreError(
                "%s: primary_key=%r contradicts the registry (%r); "
                "update db/schema.py"
                % (name, primary_key, dbschema.spec(name).pk_field))
        self._tables[name] = {}
        return True

    def index_list(self, table: str) -> list:
        spec_ = dbschema.spec(table)
        return sorted(set(spec_.indexed_fields)
                      | set(spec_.compound_indexes)
                      | set(spec_.generated))

    # -- query builders (the real ones: identical Selection objects) -------
    filter = staticmethod(_s.filter)
    between = staticmethod(_s.between)
    order_by = staticmethod(_s.order_by)
    limit = staticmethod(_s.limit)
    slice = staticmethod(_s.slice)
    asc = staticmethod(_s.asc)
    desc = staticmethod(_s.desc)
    escape_like = staticmethod(_s.escape_like)
    coerce_id = staticmethod(_s.coerce_id)
    Selection = _s.Selection
    Predicate = _s.Predicate
    P = _s.P
    InsertResult = _s.InsertResult
    WriteResult = _s.WriteResult
    MINVAL = _s.MINVAL
    MAXVAL = _s.MAXVAL
    PG_MAX_ROWS = _s.PG_MAX_ROWS
    # A ported call site writing store.Literal({}) must not raise
    # AttributeError only on the laptops the fake exists to serve.
    Literal = _s.Literal
    deep_merge = staticmethod(_s.deep_merge)
    encode_patch = staticmethod(_s.encode_patch)
    Order = _s.Order
    WRITE_CHUNK = _s.WRITE_CHUNK

    # -- writes -----------------------------------------------------------
    def insert(self, table: str, doc_or_docs, *, conflict: str = "error",
               durability: str = "hard") -> _s.InsertResult:
        if conflict not in ("error", "replace", "update"):
            raise StoreError(
                "conflict must be error|replace|update, got %r" % conflict)
        docs = [doc_or_docs] if isinstance(doc_or_docs, dict) else list(doc_or_docs)
        if not docs:
            return _s.InsertResult()
        rows = self._rows(table)
        inserted = replaced = unchanged = errors = 0
        first_error = None
        generated_keys = []
        # The whole batch is validated before anything is written, exactly as
        # db.store does it: a NaN or a bad primary key is a client-side
        # rejection, so no row of the batch may land.
        encoded = []
        for doc in docs:
            rid, doc, generated = _s._row_id_or_generate(table, doc)
            dbjson.dumps(doc)                     # NaN rejection, at the client
            _s._extra_column_values(table, dbschema.spec(table), doc)
            encoded.append((rid, doc, generated))
        for rid, doc, generated in encoded:
            if generated is not None:
                generated_keys.append(generated)
            if rid not in rows:
                rows[rid] = dict(doc)
                inserted += 1
            elif conflict == "replace":
                if rows[rid] == doc:
                    unchanged += 1
                else:
                    rows[rid] = dict(doc)
                    replaced += 1
            elif conflict == "update":
                rows[rid] = deep_merge(rows[rid], doc)
                replaced += 1
            else:
                errors += 1
                first_error = first_error or _s._DUP_ERROR
        return _s.InsertResult(inserted=inserted, replaced=replaced,
                               unchanged=unchanged, errors=errors,
                               first_error=first_error,
                               generated_keys=generated_keys)

    def update(self, table: str, selector, patch) -> _s.WriteResult:
        rows = self._rows(table)
        if isinstance(selector, _s.Selection):
            targets = [(k, r) for k, r in list(rows.items())
                       if self._match(table, r, selector)]
        else:
            rid = _s.coerce_id(table, selector)
            row = rows.get(rid)
            if row is None:
                return _s.WriteResult(skipped=1)
            targets = [(rid, row)]
        replaced = 0
        for rid, row in targets:
            merged = deep_merge(row, patch)
            if merged != row:
                replaced += 1
            rows[rid] = merged
        return _s.WriteResult(replaced=replaced,
                              unchanged=len(targets) - replaced)

    def replace(self, table: str, row_id, doc) -> _s.WriteResult:
        rid = _s.coerce_id(table, row_id)
        rows = self._rows(table)
        if rid not in rows:
            return _s.WriteResult(skipped=1)
        if rows[rid] == doc:
            return _s.WriteResult(unchanged=1)
        rows[rid] = dict(doc)
        return _s.WriteResult(replaced=1)

    def replace_if(self, table: str, row_id, *, when, doc,
                   insert_if_absent: bool = False):
        rid = _s.coerce_id(table, row_id)
        rows = self._rows(table)
        if rid not in rows:
            if not insert_if_absent:
                raise StoreError("%s: row %r does not exist" % (table, row_id))
            rows[rid] = dict(doc)
            return doc
        if when is not None:
            frag, params = when.to_sql()
            if _eval_fragment(frag, params, rows[rid], table) is not True:
                return None
        rows[rid] = dict(doc)
        return doc

    def delete(self, table: str, selector) -> _s.WriteResult:
        rows = self._rows(table)
        if isinstance(selector, _s.Selection):
            doomed = [k for k, r in rows.items() if self._match(table, r, selector)]
        else:
            rid = _s.coerce_id(table, selector)
            if rid not in rows:
                return _s.WriteResult(skipped=1)
            doomed = [rid]
        for key in doomed:
            del rows[key]
        return _s.WriteResult(deleted=len(doomed))


# -- text coercion --------------------------------------------------------
#
# ``doc->>'k'`` and every generated column render the jsonb value as text.
# Match that rendering, so a bytewise comparison here is the comparison
# Postgres makes under COLLATE "C".
def _scalar_str(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return dbjson.dumps(value)
    return str(value)


def _text(value):
    return None if value is None else _scalar_str(value)


# -- ORDER BY -------------------------------------------------------------
#
# Postgres sorts NULLs LAST on ASC and FIRST on DESC. A (is_null, value) key
# gives exactly that once list.sort(reverse=True) flips the whole tuple.
_RAW_ORDER_RE = re.compile(r'^(?P<expr>.*?)(?: COLLATE "C")? (?P<dir>ASC|DESC)$')


def _order_key(table: str, order):
    if isinstance(order, _s._RawOrder):
        m = _RAW_ORDER_RE.match(order.sql_text.strip())
        if not m:
            raise StoreError("FakeStore cannot evaluate ORDER BY %r"
                             % order.sql_text)
        expr = m.group("expr").strip()
        descending = m.group("dir") == "DESC"

        def keyfn(row, expr=expr):
            value = _eval_order_expr(expr, row, table)
            return (1, b"") if value is None else (0, value.encode("utf-8"))

        return keyfn, descending

    field_name = order.field
    if order.numeric:
        def keyfn(row, f=field_name):
            value = row.get(f)
            try:
                return (0, float(value))
            except (TypeError, ValueError):
                return (1, 0.0)
    else:
        def keyfn(row, f=field_name):
            value = _text(row.get(f))
            return (1, b"") if value is None else (0, value.encode("utf-8"))
    return keyfn, order.desc


_COALESCE_KEYS_RE = re.compile(r"^\(coalesce\((?P<args>.+),\s*''\)\)$")


def _eval_order_expr(expr: str, row: dict, table: str):
    """The ORDER BY expressions store._INDEX_ORDER and order_by() can emit."""
    m = _COALESCE_KEYS_RE.match(expr)
    if m:
        for part in m.group("args").split(","):
            key = re.match(r"^\s*doc->>'([^']+)'\s*$", part)
            if key is None:
                raise StoreError("FakeStore cannot evaluate ORDER BY %r" % expr)
            value = _text(row.get(key.group(1)))
            if value is not None:
                return value
        return ""
    if expr == "id":
        return _row_key(table, row)
    m = re.match(r'^"([^"]+)"$', expr)
    if m:
        return _text(row.get(m.group(1)))
    raise StoreError("FakeStore cannot evaluate ORDER BY %r" % expr)


def _row_key(table: str, row: dict):
    """The value of the ``id`` COLUMN, which is coerce_id(doc[pk_field])."""
    try:
        return _s._row_id_for(table, row)
    except StoreError:
        return None


# -- the predicate-fragment interpreter -----------------------------------
#
# db.store's FieldRef/Predicate emit a small, bounded grammar. This evaluates
# exactly that grammar over a Python dict, with Postgres' three-valued logic:
# True / False / None (SQL NULL). WHERE keeps a row only on True.
#
# Parameter consumption is positional and left-to-right: each sub-expression
# eats its own placeholders, so a fragment is split on the number of "%s" its
# left-hand side contains.
_FRAG_RE = re.compile(
    r"^(?P<expr>.+?)\s*(?P<op>= ANY|IS NOT NULL|IS NULL|LIKE|~|<=|>=|<>|=|<|>)"
    r"\s*(?P<rhs>\(%s\)|%s)?$")

_JSON_NULL_RE = re.compile(r"^doc -> '(?P<key>[^']+)' = 'null'::jsonb$")
_JSON_BOOL_RE = re.compile(r"^doc -> '(?P<key>[^']+)' = %s::jsonb$")
_JSON_NUM_RE = re.compile(
    r"^\(CASE WHEN jsonb_typeof\(doc -> '(?P<key>[^']+)'\) = 'number' "
    r"THEN \(doc -> '(?P=key)'\)::numeric = %s ELSE false END\)$")

_NOT_HEAD = "COALESCE(NOT ("
_NOT_TAIL = "), false)"


def _eval_fragment(fragment: str, params, row: dict, table: str):
    """Returns True, False, or None (SQL NULL)."""
    frag = fragment.strip()
    if frag == "true":
        return True
    if frag == "false":
        return False
    if frag.startswith(_NOT_HEAD) and frag.endswith(_NOT_TAIL):
        inner = frag[len(_NOT_HEAD):-len(_NOT_TAIL)]
        value = _eval_fragment(inner, params, row, table)
        return False if value is None else (not value)

    if frag.startswith("(") and frag.endswith(")"):
        for joiner in (") AND (", ") OR ("):
            left, right = _split_top(frag, joiner)
            if left is None:
                continue
            n_left = left.count("%s")
            lv = _eval_fragment(left, params[:n_left], row, table)
            rv = _eval_fragment(right, params[n_left:], row, table)
            if joiner == ") AND (":
                if lv is False or rv is False:
                    return False
                return None if (lv is None or rv is None) else True
            if lv is True or rv is True:
                return True
            return None if (lv is None or rv is None) else False

    m = _JSON_NULL_RE.match(frag)
    if m:
        # A dict filter value of None: the key is present and JSON null.
        # FakeStore cannot tell absent from null (Python has one None), so
        # this is the one place it is deliberately coarser than jsonb.
        return row.get(m.group("key"), _MISSING) is None
    m = _JSON_BOOL_RE.match(frag)
    if m:
        value = row.get(m.group("key"), _MISSING)
        if value is _MISSING:
            return None
        want = params[0] == "true"
        return isinstance(value, bool) and value is want
    m = _JSON_NUM_RE.match(frag)
    if m:
        # ELSE false: a missing key or a non-number is not a match, and it
        # is not NULL either -- the CASE the real store emits returns false.
        value = row.get(m.group("key"))
        if value is None or isinstance(value, bool):
            return False
        if not isinstance(value, (int, float)):
            return False
        return float(value) == float(params[0])

    m = _FRAG_RE.match(frag)
    if not m:
        raise StoreError("FakeStore cannot evaluate %r" % fragment)
    value, consumed = _eval_expr(m.group("expr"), params, row, table)
    op = m.group("op")
    if op == "IS NULL":
        return value is None
    if op == "IS NOT NULL":
        return value is not None
    rhs = params[consumed]
    if op == "= ANY":
        if value is None:
            return None
        return value in [_text(v) for v in rhs]
    if value is None or rhs is None:
        return None
    if op == "LIKE":
        return _like(value, str(rhs))
    if op == "~":
        return re.search(str(rhs), value) is not None
    left = value.encode("utf-8")
    right = _scalar_str(rhs).encode("utf-8")
    return {"=": left == right, "<>": left != right, "<": left < right,
            "<=": left <= right, ">": left > right, ">=": left >= right}[op]


class _Missing:
    def __repr__(self) -> str:
        return "<missing>"


_MISSING = _Missing()

_DOC_KEY_RE = re.compile(r"^doc->>'([^']+)'$")
_COALESCE_RE = re.compile(r"^coalesce\((.+), %s\)$")
_LOWER_RE = re.compile(r"^lower\((.+)\)$")
_SPLIT_RE = re.compile(r"^split_part\((.+), %s, %s\)$")
_COL_RE = re.compile(r'^"([^"]+)"$')


def _eval_expr(expr: str, params, row: dict, table: str):
    """Returns (text-or-None, n_params_consumed) for one FieldRef expression."""
    expr = expr.strip()
    if expr.endswith('COLLATE "C"'):
        # The four ordering operators pin the collation; this interpreter
        # already compares UTF-8 bytes, which IS collation C.
        expr = expr[:-len('COLLATE "C"')].strip()
    m = _DOC_KEY_RE.match(expr)
    if m:
        return _text(row.get(m.group(1))), 0
    m = _COALESCE_RE.match(expr)
    if m:
        inner, used = _eval_expr(m.group(1), params, row, table)
        return (inner if inner is not None else _text(params[used])), used + 1
    m = _LOWER_RE.match(expr)
    if m:
        inner, used = _eval_expr(m.group(1), params, row, table)
        return (None if inner is None else inner.lower()), used
    m = _SPLIT_RE.match(expr)
    if m:
        inner, used = _eval_expr(m.group(1), params, row, table)
        sep, idx = params[used], int(params[used + 1])
        parts = ("" if inner is None else inner).split(sep)
        return (parts[idx - 1] if 0 < idx <= len(parts) else ""), used + 2
    if expr == "id":
        return _row_key(table, row), 0
    m = _COL_RE.match(expr)                       # a generated column
    if m:
        return _text(row.get(m.group(1))), 0
    raise StoreError("FakeStore cannot evaluate expression %r" % expr)


def _like(value: str, pattern: str) -> bool:
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "%" else "." if ch == "_" else re.escape(ch))
        i += 1
    return re.match("^" + "".join(out) + "$", value, re.S) is not None


def _split_top(frag: str, joiner: str):
    """Split ``(L) <joiner> (R)`` at the top-level joiner, or (None, None).

    Predicate.__and__/__or__ wrap BOTH sides, so the outer parentheses are two
    separate pairs -- the split point is where depth returns to 0.
    """
    depth = 0
    for i, ch in enumerate(frag):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and frag[i:i + len(joiner)] == joiner:
                return frag[1:i], frag[i + len(joiner):-1]
    return None, None
