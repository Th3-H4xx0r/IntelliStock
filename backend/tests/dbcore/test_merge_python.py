import pytest
from db.errors import StoreError
from db.merge import LITERAL_KEY, Literal, deep_merge, encode_patch


def test_objects_merge_recursively():
    assert deep_merge({"a": {"b": 1, "c": 2}, "d": 3}, {"a": {"c": 9, "e": 5}}) == \
        {"a": {"b": 1, "c": 9, "e": 5}, "d": 3}


def test_arrays_replace_wholesale():
    assert deep_merge({"a": [1, 2, 3]}, {"a": [9]}) == {"a": [9]}


def test_scalar_replaces_object():
    assert deep_merge({"a": {"b": 1}}, {"a": 5}) == {"a": 5}


def test_none_sets_json_null_it_does_not_delete():
    assert deep_merge({"a": 1}, {"a": None}) == {"a": None}


def test_missing_intermediates_are_created():
    assert deep_merge({}, {"a": {"b": {"c": 1}}}) == {"a": {"b": {"c": 1}}}


def test_literal_sets_shallow_without_merging():
    assert deep_merge({"cfg": {"keep": 1, "drop": 2}},
                      {"cfg": Literal({"only": 3})}) == {"cfg": {"only": 3}}


def test_literal_empty_dict_blanks_a_subtree():
    assert deep_merge({"s": {"k": "secret"}}, {"s": Literal({})}) == {"s": {}}


def test_literal_at_the_root_replaces_the_document():
    assert deep_merge({"a": 1}, Literal({"b": 2})) == {"b": 2}


def test_base_non_object_is_replaced_by_patch_object():
    assert deep_merge(5, {"a": 1}) == {"a": 1}


def test_base_none_yields_the_patch():
    assert deep_merge(None, {"a": 1}) == {"a": 1}


def test_deep_merge_does_not_mutate_its_inputs():
    base, patch = {"a": {"b": 1}}, {"a": {"c": 2}}
    deep_merge(base, patch)
    assert base == {"a": {"b": 1}} and patch == {"a": {"c": 2}}


def test_encode_patch_rewrites_literal_to_the_wire_sentinel():
    assert encode_patch({"a": Literal({"b": 1})}) == {"a": {LITERAL_KEY: {"b": 1}}}


def test_encode_patch_recurses_into_nested_objects_and_arrays():
    assert encode_patch({"a": {"b": [Literal(1), 2]}}) == \
        {"a": {"b": [{LITERAL_KEY: 1}, 2]}}


def test_encode_patch_rejects_a_real_key_named_like_the_sentinel():
    with pytest.raises(StoreError):
        encode_patch({LITERAL_KEY: 1})


def test_deep_merge_unwraps_the_wire_sentinel_too():
    assert deep_merge({"a": {"b": 1}}, {"a": {LITERAL_KEY: {"c": 2}}}) == {"a": {"c": 2}}
