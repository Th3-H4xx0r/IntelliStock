"""FakeStore must answer the same way the real store does.

Every test here runs twice when PG_TEST_DSN is set: once against FakeStore and
once against the real store. Divergence here is a bug in FakeStore, never a
reason to weaken the real store.
"""
import pytest

from db import schema
from db.errors import StoreError
from db.fake import FakeStore
from db.merge import Literal
from db.store import P

from .conftest import PG_TEST_DSN

_TABLES = ["Instances", "Strategies", "DiscordOutbox", "kalshi_markets"]


@pytest.fixture
def pg_schema_or_skip(request):
    """Only the 'real' parametrisation needs a database schema."""
    if request.node.callspec.params.get("s") == "real":
        yield request.getfixturevalue("pg_schema")
    else:
        yield None


@pytest.fixture(params=["fake", "real"])
def s(request, pg_schema_or_skip):
    if request.param == "fake":
        return FakeStore()
    if not PG_TEST_DSN:
        pytest.skip("PG_TEST_DSN not set")
    from db import store as real
    schema.ensure_schema(tables=_TABLES)
    return real


def test_get_missing_is_none(s):
    assert s.get("Instances", 12345) is None


def test_insert_and_get(s):
    s.insert("Instances", {"id": 1, "name": "main"})
    assert s.get("Instances", 1) == {"id": 1, "name": "main"}


def test_int_ids_coerce_both_ways(s):
    s.insert("Instances", {"id": 1})
    assert s.get("Instances", "1") == s.get("Instances", 1)


def test_non_integer_id_on_an_int_table_raises(s):
    with pytest.raises(StoreError):
        s.get("Strategies", "not-a-number")


def test_a_text_table_keeps_a_numeric_looking_string_key(s):
    """Instances is text-keyed and holds '10' as a string. Fake and real must
    both return it under the string, never under a parsed int."""
    s.insert("Instances", {"id": "10", "name": "ten"})
    s.insert("Instances", {"id": "alpaca-main", "name": "live"})
    assert s.get("Instances", "10") == {"id": "10", "name": "ten"}
    assert s.get("Instances", "alpaca-main") == {"id": "alpaca-main",
                                                 "name": "live"}


def test_update_deep_merges(s):
    s.insert("Instances", {"id": 1, "cfg": {"a": 1, "b": 2}})
    s.update("Instances", 1, {"cfg": {"b": 9}})
    assert s.get("Instances", 1)["cfg"] == {"a": 1, "b": 9}


def test_update_literal_blanks(s):
    s.insert("Instances", {"id": 1, "cfg": {"a": 1}})
    s.update("Instances", 1, {"cfg": Literal({})})
    assert s.get("Instances", 1)["cfg"] == {}


def test_update_of_a_missing_row_is_skipped(s):
    res = s.update("Instances", 999, {"a": 1})
    assert (res.replaced, res.skipped) == (0, 1)


def test_get_all_does_not_dedupe(s):
    for rid in ("a", "b"):
        s.insert("DiscordOutbox", {"id": rid})
    assert [r["id"] for r in s.get_all("DiscordOutbox", "a", "a", "b")] == \
        ["a", "a", "b"]


def test_get_all_preserves_key_order(s):
    for rid in ("a", "b", "c"):
        s.insert("DiscordOutbox", {"id": rid})
    assert [r["id"] for r in s.get_all("DiscordOutbox", "c", "a", "b")] == \
        ["c", "a", "b"]


def test_get_all_skips_absent_keys(s):
    s.insert("DiscordOutbox", {"id": "a"})
    assert [r["id"] for r in s.get_all("DiscordOutbox", "a", "zz")] == ["a"]


def test_conflict_error_counts_without_aborting(s):
    s.insert("DiscordOutbox", {"id": "a", "n": 1})
    res = s.insert("DiscordOutbox", [{"id": "a", "n": 2}, {"id": "b"}])
    assert (res.inserted, res.errors) == (1, 1)
    assert "Duplicate primary key" in res.first_error
    assert s.get("DiscordOutbox", "a")["n"] == 1


def test_conflict_update_deep_merges(s):
    s.insert("DiscordOutbox", {"id": "a", "cfg": {"x": 1, "y": 2}})
    s.insert("DiscordOutbox", {"id": "a", "cfg": {"y": 9}}, conflict="update")
    assert s.get("DiscordOutbox", "a")["cfg"] == {"x": 1, "y": 9}


def test_conflict_replace_drops_keys(s):
    s.insert("DiscordOutbox", {"id": "a", "x": 1, "y": 2})
    res = s.insert("DiscordOutbox", {"id": "a", "y": 3}, conflict="replace")
    assert s.get("DiscordOutbox", "a") == {"id": "a", "y": 3}
    assert res.replaced == 1


def test_conflict_replace_identical_is_unchanged(s):
    s.insert("DiscordOutbox", {"id": "a", "x": 1})
    res = s.insert("DiscordOutbox", {"id": "a", "x": 1}, conflict="replace")
    assert (res.replaced, res.unchanged) == (0, 1)


def test_nan_raises_at_the_client(s):
    with pytest.raises(ValueError):
        s.insert("DiscordOutbox", {"id": "a", "x": float("nan")})


def test_ordering_is_bytewise(s):
    for rid in ("alpaca-main|z", "Alpaca-Main|a", "alpaca_main|a"):
        s.insert("DiscordOutbox", {"id": rid})
    got = [r["id"] for r in s.run(s.order_by(s.filter("DiscordOutbox", {}),
                                             fields=(s.asc("id"),)))]
    assert got == sorted(got, key=lambda v: v.encode("utf-8"))


def test_ordering_desc_is_bytewise(s):
    for rid in ("a|z", "A|a", "a_a"):
        s.insert("DiscordOutbox", {"id": rid})
    got = [r["id"] for r in s.run(s.order_by(s.filter("DiscordOutbox", {}),
                                             fields=(s.desc("id"),)))]
    assert got == sorted(got, key=lambda v: v.encode("utf-8"), reverse=True)


def test_between_is_half_open(s):
    for rid in ("a", "b", "c"):
        s.insert("DiscordOutbox", {"id": rid})
    assert sorted(r["id"] for r in s.run(s.between("DiscordOutbox", "a", "c"))) == \
        ["a", "b"]


def test_between_open_left_and_closed_right(s):
    for rid in ("a", "b", "c"):
        s.insert("DiscordOutbox", {"id": rid})
    sel = s.between("DiscordOutbox", "a", "c",
                    left_bound="open", right_bound="closed")
    assert sorted(r["id"] for r in s.run(sel)) == ["b", "c"]


def test_between_minval_omits_the_bound(s):
    for rid in ("a", "b", "c"):
        s.insert("DiscordOutbox", {"id": rid})
    sel = s.between("DiscordOutbox", s.MINVAL, "b")
    assert sorted(r["id"] for r in s.run(sel)) == ["a"]


def test_pluck_omits_absent_keys(s):
    assert s.pluck([{"id": 1}], "id", "status") == [{"id": 1}]


def test_pluck_recurses_into_a_nested_spec(s):
    rows = [{"id": 1, "cfg": {"a": 1, "b": 2}, "junk": 9}]
    assert s.pluck(rows, "id", {"cfg": ["a", "zz"]}) == [{"id": 1, "cfg": {"a": 1}}]


def test_pluck_over_a_selection(s):
    s.insert("DiscordOutbox", {"id": "a", "kind": "x", "junk": 1})
    assert s.pluck(s.filter("DiscordOutbox", {"kind": "x"}), "id") == [{"id": "a"}]


def test_delete_over_a_selection(s):
    for rid, kind in (("a", "x"), ("b", "x"), ("c", "y")):
        s.insert("DiscordOutbox", {"id": rid, "kind": kind})
    assert s.delete("DiscordOutbox",
                    s.filter("DiscordOutbox", {"kind": "x"})).deleted == 2
    assert s.count("DiscordOutbox") == 1


def test_delete_by_id(s):
    s.insert("DiscordOutbox", {"id": "a"})
    assert s.delete("DiscordOutbox", "a").deleted == 1
    assert s.delete("DiscordOutbox", "a").deleted == 0


def test_update_over_a_selection(s):
    for rid, kind in (("a", "x"), ("b", "y")):
        s.insert("DiscordOutbox", {"id": rid, "kind": kind})
    res = s.update("DiscordOutbox", s.filter("DiscordOutbox", {"kind": "x"}),
                   {"seen": True})
    assert res.replaced == 1
    assert s.get("DiscordOutbox", "a")["seen"] is True
    assert "seen" not in s.get("DiscordOutbox", "b")


def test_predicate_default_and_ne(s):
    s.insert("DiscordOutbox", {"id": "a", "origin": "backtest"})
    s.insert("DiscordOutbox", {"id": "b", "origin": "live"})
    s.insert("DiscordOutbox", {"id": "c"})
    pred = P.field("origin").default("").ne("backtest")
    assert {r["id"] for r in s.run(s.filter("DiscordOutbox", pred))} == {"b", "c"}


def test_predicate_ne_without_default_drops_the_missing_key(s):
    """A NULL comparison is NULL, and WHERE keeps only TRUE -- so the row
    without the key is NOT returned, unlike a naive Python ``!=``."""
    s.insert("DiscordOutbox", {"id": "a", "origin": "backtest"})
    s.insert("DiscordOutbox", {"id": "b", "origin": "live"})
    s.insert("DiscordOutbox", {"id": "c"})
    pred = P.field("origin").ne("backtest")
    assert {r["id"] for r in s.run(s.filter("DiscordOutbox", pred))} == {"b"}


def test_negation_of_a_null_comparison_stays_false(s):
    s.insert("DiscordOutbox", {"id": "a", "origin": "backtest"})
    s.insert("DiscordOutbox", {"id": "c"})
    pred = ~P.field("origin").eq("backtest")
    assert [r["id"] for r in s.run(s.filter("DiscordOutbox", pred))] == []


def test_predicate_and_or_split_params_left_to_right(s):
    s.insert("DiscordOutbox", {"id": "a", "kind": "x", "origin": "live"})
    s.insert("DiscordOutbox", {"id": "b", "kind": "y", "origin": "live"})
    s.insert("DiscordOutbox", {"id": "c", "kind": "x", "origin": "bt"})
    pred = ((P.field("kind").eq("x") & P.field("origin").eq("live"))
            | P.field("id").eq("b"))
    assert {r["id"] for r in s.run(s.filter("DiscordOutbox", pred))} == {"a", "b"}


def test_predicate_is_in_and_empty_is_in(s):
    for rid in ("a", "b", "c"):
        s.insert("DiscordOutbox", {"id": rid})
    got = s.run(s.filter("DiscordOutbox", P.field("id").is_in(["a", "c"])))
    assert {r["id"] for r in got} == {"a", "c"}
    assert s.run(s.filter("DiscordOutbox", P.field("id").is_in([]))) == []


def test_predicate_is_null(s):
    s.insert("DiscordOutbox", {"id": "a", "origin": "live"})
    s.insert("DiscordOutbox", {"id": "b"})
    got = s.run(s.filter("DiscordOutbox", P.field("origin").is_null()))
    assert [r["id"] for r in got] == ["b"]


def test_predicate_downcase_and_match(s):
    s.insert("DiscordOutbox", {"id": "a", "sym": "AAPL"})
    s.insert("DiscordOutbox", {"id": "b", "sym": "msft"})
    got = s.run(s.filter("DiscordOutbox", P.field("sym").downcase().eq("aapl")))
    assert [r["id"] for r in got] == ["a"]
    got = s.run(s.filter("DiscordOutbox", P.field("sym").match("^msft$")))
    assert [r["id"] for r in got] == ["b"]


def test_predicate_split_nth(s):
    s.insert("DiscordOutbox", {"id": "a", "scope": "alpaca-main|deadbeef"})
    s.insert("DiscordOutbox", {"id": "b", "scope": "other|deadbeef"})
    got = s.run(s.filter("DiscordOutbox",
                         P.field("scope").split_nth("|", 0).eq("alpaca-main")))
    assert [r["id"] for r in got] == ["a"]


def test_starts_with_prefix(s):
    for rid in ("main|h|x", "mainly", "other"):
        s.insert("DiscordOutbox", {"id": rid})
    got = s.run(s.filter("DiscordOutbox", P.field("id").starts_with("main|")))
    assert [r["id"] for r in got] == ["main|h|x"]


def test_starts_with_escapes_like_metacharacters(s):
    for rid in ("a_b", "axb"):
        s.insert("DiscordOutbox", {"id": rid})
    got = s.run(s.filter("DiscordOutbox", P.field("id").starts_with("a_")))
    assert [r["id"] for r in got] == ["a_b"]


def test_dict_filter_on_bool_and_number(s):
    s.insert("DiscordOutbox", {"id": "a", "sent": True, "n": 5})
    s.insert("DiscordOutbox", {"id": "b", "sent": False, "n": 6})
    s.insert("DiscordOutbox", {"id": "c"})
    assert [r["id"] for r in s.run(s.filter("DiscordOutbox", {"sent": True}))] == ["a"]
    assert [r["id"] for r in s.run(s.filter("DiscordOutbox", {"n": 5}))] == ["a"]


def test_limit_and_slice(s):
    for rid in ("a", "b", "c", "d"):
        s.insert("DiscordOutbox", {"id": rid})
    ordered = s.order_by(s.filter("DiscordOutbox", {}), fields=(s.asc("id"),))
    assert [r["id"] for r in s.run(s.limit(ordered, 2))] == ["a", "b"]
    assert [r["id"] for r in s.run(s.slice(ordered, 1, 3))] == ["b", "c"]


def test_count_honours_limit(s):
    """ReQL's .limit(n).count() is min(n, total)."""
    for rid in ("a", "b", "c"):
        s.insert("DiscordOutbox", {"id": rid})
    assert s.count(s.limit(s.filter("DiscordOutbox", {}), 1)) == 1
    assert s.count(s.filter("DiscordOutbox", {})) == 3


def test_iter_yields_every_row(s):
    for rid in ("a", "b", "c"):
        s.insert("DiscordOutbox", {"id": rid})
    assert sorted(r["id"] for r in s.iter("DiscordOutbox")) == ["a", "b", "c"]


def test_replace_drops_keys_and_skips_a_missing_row(s):
    s.insert("DiscordOutbox", {"id": "a", "x": 1, "y": 2})
    assert s.replace("DiscordOutbox", "a", {"id": "a", "y": 3}).replaced == 1
    assert s.get("DiscordOutbox", "a") == {"id": "a", "y": 3}
    assert s.replace("DiscordOutbox", "zz", {"id": "zz"}).skipped == 1


def test_replace_if_honours_the_predicate(s):
    s.insert("DiscordOutbox", {"id": "a", "state": "idle"})
    assert s.replace_if("DiscordOutbox", "a", when=P.field("state").eq("busy"),
                        doc={"id": "a", "state": "won"}) is None
    assert s.get("DiscordOutbox", "a")["state"] == "idle"
    assert s.replace_if("DiscordOutbox", "a", when=P.field("state").eq("idle"),
                        doc={"id": "a", "state": "won"}) is not None
    assert s.get("DiscordOutbox", "a")["state"] == "won"


def test_replace_if_missing_row_raises_unless_allowed(s):
    with pytest.raises(StoreError):
        s.replace_if("DiscordOutbox", "zz", when=None, doc={"id": "zz"})
    assert s.replace_if("DiscordOutbox", "zz", when=None, doc={"id": "zz"},
                        insert_if_absent=True) == {"id": "zz"}


def test_missing_id_is_generated(s):
    """RethinkDB generated a uuid for a missing ``id`` and returned it in
    generated_keys; priceBroker.py:186 has always relied on that."""
    res = s.insert("DiscordOutbox", {"body": "hi"})
    assert res.inserted == 1 and len(res.generated_keys) == 1
    assert s.get("DiscordOutbox", res.generated_keys[0])["id"] == \
        res.generated_keys[0]


def test_missing_custom_pk_field_raises_in_both_backends(s):
    """ReQL minted only for ``id``; a document missing a CUSTOM primary key
    raised. FakeStore must refuse it too, or a unit test would green-light a
    write production rejects."""
    with pytest.raises(StoreError):
        s.insert("kalshi_markets", {"yes_bid": 1})
    assert s.count("kalshi_markets") == 0


def test_bad_conflict_mode_raises(s):
    with pytest.raises(StoreError):
        s.insert("DiscordOutbox", {"id": "a"}, conflict="nope")


def test_fake_sql_is_not_implemented():
    with pytest.raises(NotImplementedError):
        FakeStore().sql("SELECT 1")


def test_fake_rejects_a_fragment_outside_the_grammar():
    from db import store as st
    fake = FakeStore()
    fake.insert("DiscordOutbox", {"id": "a"})
    sel = st.Selection("DiscordOutbox").where("doc @> %s::jsonb", ("{}",))
    with pytest.raises(StoreError):
        fake.run(sel)
