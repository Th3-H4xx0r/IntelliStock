"""The catalog built from the API's MCP-visible OpenAPI document (contract sections 1 and 4)."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from fake_api import schema_hash
from mcp_server.catalog import (
    CLASSES,
    MAX_REF_DEPTH,
    PAGING_PARAM_RE,
    build_catalog,
    canonical_schema_hash,
    resolve_refs,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "openapi_sample.json"

EXPECTED_OPERATIONS = {
    "get_instance", "list_backtests", "create_backtest", "get_backtest_logs", "live_command", "delete_strategy",
    "strategy_tree", "learning_control", "list_brokerages", "list_models", "search_routes", "list_mcp_approvals",
    "get_mcp_approval",
}
READ_OPERATIONS = sorted(["get_instance", "list_backtests", "get_backtest_logs", "strategy_tree", "list_brokerages",
                          "list_models", "search_routes", "list_mcp_approvals", "get_mcp_approval"])


def _sample():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def sample():
    return _sample()


@pytest.fixture
def catalog(sample):
    return build_catalog(sample)


def _refs(value):
    """Every $ref string anywhere in value."""
    if isinstance(value, dict):
        found = [value["$ref"]] if isinstance(value.get("$ref"), str) else []
        return found + [ref for item in value.values() for ref in _refs(item)]
    if isinstance(value, list):
        return [ref for item in value for ref in _refs(item)]
    return []


def _doc(paths, schemas=None):
    """A minimal OpenAPI document around paths."""
    doc = {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": paths}
    if schemas is not None:
        doc["components"] = {"schemas": schemas}
    return doc


def _op(operation_id, cls="read", **extra):
    """A minimal operation with an x-mcp object."""
    return {"operationId": operation_id, "responses": {"200": {"description": "ok"}}, "x-mcp": {"class": cls}, **extra}


def test_builds_operations_and_skips_unsafe(catalog):
    assert set(catalog.operations) == EXPECTED_OPERATIONS
    assert sorted(catalog.skipped) == [
        "GET /internal/debug/state: no x-mcp annotation",
        "HEAD /instances/{instance_id}: method HEAD is not supported",
        "POST /mcp/approvals/{id}/decision: x-mcp class 'never' is not offered over MCP",
    ]

    get_instance = catalog.operations["get_instance"]
    assert (get_instance.operation_id, get_instance.method, get_instance.path) == (
        "get_instance", "GET", "/instances/{instance_id}")
    assert get_instance.summary == "Get one instance — config, status and linked accounts"
    assert (get_instance.cls, get_instance.tool, get_instance.cost) == ("read", "get_instance", False)
    assert (get_instance.path_params, get_instance.query_params, get_instance.has_body) == (("instance_id",), (), False)

    create = catalog.operations["create_backtest"]
    assert (create.method, create.cls, create.tool, create.cost, create.has_body) == (
        "POST", "write", "create_backtest", True, True)
    assert create.description.startswith("Queues a backtest for an instance.")

    live = catalog.operations["live_command"]
    assert (live.method, live.path, live.cls, live.path_params) == (
        "POST", "/instances/{instance_id}/live-command", "money", ("instance_id",))
    assert catalog.operations["delete_strategy"].method == "DELETE"

    # cost is read for every class, not only write (the API marks POST /learning/control irreversible + cost).
    learning = catalog.operations["learning_control"]
    assert (learning.cls, learning.cost, learning.tool) == ("irreversible", True, None)

    # Tool-name collisions are kept here; the tool registry decides which direct tools exist.
    assert [catalog.operations[name].tool for name in ("list_brokerages", "list_models", "search_routes")] == [
        "dup_tool", "dup_tool", "find_endpoints"]
    assert catalog.operations["list_mcp_approvals"].tool == "list_pending_approvals"
    assert catalog.operations["get_mcp_approval"].tool == "get_approval"

    # Approval and idempotency flags follow the class.
    by_class = {"get_instance": (False, False), "create_backtest": (False, True), "live_command": (True, True),
                "delete_strategy": (True, True)}
    for name, flags in by_class.items():
        op = catalog.operations[name]
        assert (op.approval_may_apply, op.needs_idempotency_key) == flags, name
    assert {op.cls for op in catalog.operations.values()} == set(CLASSES)


def test_merged_input_schema_shape(catalog):
    for op in catalog.operations.values():
        Draft202012Validator.check_schema(op.input_schema)
        assert op.input_schema["type"] == "object"
        assert op.input_schema["additionalProperties"] is False
        assert _refs(op.input_schema) == [], op.operation_id

    get_instance = catalog.operations["get_instance"].input_schema
    assert get_instance == {
        "type": "object",
        "properties": {"instance_id": {"type": "string", "title": "Instance Id"}},
        "required": ["instance_id"],
        "additionalProperties": False,
    }

    listing = catalog.operations["list_backtests"].input_schema
    assert list(listing["properties"]) == ["instance_id", "status", "limit", "offset"]
    assert listing["required"] == []
    assert listing["properties"]["limit"] == {"type": "integer", "maximum": 200, "minimum": 1, "default": 20,
                                              "title": "Limit"}
    # A parameter's description moves onto its property, where a client can see it.
    assert listing["properties"]["instance_id"]["description"] == "Only this instance's backtests."

    create = catalog.operations["create_backtest"].input_schema
    assert list(create["properties"]) == ["body"]  # the x-trace-id header parameter is not an argument
    assert create["required"] == ["body"]
    body = create["properties"]["body"]
    assert body["required"] == ["instance_id", "window"]
    assert body["properties"]["window"]["properties"]["start"] == {"type": "string", "format": "date",
                                                                    "title": "Start"}
    assert body["properties"]["window"]["required"] == ["start", "end"]
    # A $ref with sibling keywords keeps both the target and the siblings.
    assert body["properties"]["granularity"] == {"type": "string", "enum": ["60", "300", "900", "3600"],
                                                 "title": "Granularity", "description": "Bar size in seconds.",
                                                 "default": "900"}

    validator = Draft202012Validator(create)
    good = {"body": {"instance_id": "i-1", "window": {"start": "2026-01-02", "end": "2026-03-02"}}}
    validator.validate(good)
    for bad in ({}, {**good, "extra": 1}, {"body": {"instance_id": "i-1"}},
                {"body": {**good["body"], "granularity": "7"}}):
        with pytest.raises(ValidationError):
            validator.validate(bad)

    live = catalog.operations["live_command"].input_schema
    assert list(live["properties"]) == ["instance_id", "body"]
    assert live["required"] == ["instance_id", "body"]
    assert live["properties"]["body"]["properties"]["type"]["enum"] == ["halt", "close_position", "submit_order"]

    logs = catalog.operations["get_backtest_logs"].input_schema
    assert logs["properties"]["backtest_id"]["type"] == "integer"
    assert logs["required"] == ["backtest_id"]


def test_recursive_ref_terminates(catalog):
    tree = catalog.operations["strategy_tree"].input_schema
    node = tree["properties"]["body"]
    assert node["required"] == ["name"]
    assert node["properties"]["children"]["items"] == {
        "type": "object", "description": "Recursive reference to StrategyNode; send a JSON object."}
    assert _refs(tree) == []
    Draft202012Validator(tree).validate(
        {"body": {"name": "root", "children": [{"name": "a", "children": [{"name": "b", "rule": "x > 1"}]}]}})

    # Mutual recursion stops at the first reference back into a schema being expanded.
    mutual = {"schemas": {
        "A": {"type": "object", "properties": {"b": {"$ref": "#/components/schemas/B"}}},
        "B": {"type": "object", "properties": {"a": {"$ref": "#/components/schemas/A"}}},
    }}
    resolved = resolve_refs({"$ref": "#/components/schemas/A"}, mutual)
    assert resolved["properties"]["b"]["properties"]["a"] == {
        "type": "object", "description": "Recursive reference to A; send a JSON object."}

    # A chain of distinct references is expanded MAX_REF_DEPTH deep, then left as a placeholder.
    chain = {f"S{i}": {"type": "object", "properties": {"next": {"$ref": f"#/components/schemas/S{i + 1}"}}}
             for i in range(40)}
    chain["S40"] = {"type": "string"}
    node, expanded = resolve_refs({"$ref": "#/components/schemas/S0"}, {"schemas": chain}), 0
    while "properties" in node:
        node, expanded = node["properties"]["next"], expanded + 1
    assert MAX_REF_DEPTH == 32
    assert expanded == MAX_REF_DEPTH
    assert "$ref" not in node and "S32" in node["description"]


def test_resolve_refs_copies_and_rejects_what_it_cannot_resolve():
    components = {"schemas": {"Window": {"type": "object", "properties": {"start": {"type": "string"}}}}}
    before = copy.deepcopy(components)
    first = resolve_refs({"$ref": "#/components/schemas/Window"}, components)
    second = resolve_refs({"$ref": "#/components/schemas/Window"}, components)
    first["properties"]["start"]["type"] = "integer"
    assert components == before and second["properties"]["start"]["type"] == "string"

    for ref in ("#/components/schemas/Missing", "https://example.test/schema.json", "#/definitions/Window"):
        with pytest.raises(ValueError):
            resolve_refs({"type": "object", "properties": {"x": {"$ref": ref}}}, components)


def test_operation_id_rules():
    paths = {
        "/a": {"get": _op("ok_one"), "post": _op("dup", "write")},
        "/b": {"get": _op("dup")},
        "/c": {"get": _op("has-dash")},
        "/d": {"get": _op("x" * 65)},
        "/e": {"get": {k: v for k, v in _op("unused").items() if k != "operationId"}},
        "/f": {"get": _op("y" * 64)},
    }
    catalog = build_catalog(_doc(paths))
    assert set(catalog.operations) == {"ok_one", "y" * 64}
    assert sorted(entry.split(":")[0] for entry in catalog.skipped) == [
        "GET /b", "GET /c", "GET /d", "GET /e", "POST /a"]
    assert sum("'dup' is not unique" in entry for entry in catalog.skipped) == 2


def test_policy_and_method_rules():
    paths = {
        "/a": {
            "get": _op("bad_class", "admin"),
            "put": _op("put_ok", "write", **{"x-mcp": {"class": "write", "tool": 7}}),
            "patch": _op("patch_ok", "write"),
            "options": _op("options_op"),
            "trace": _op("trace_op"),
            "parameters": [],
            "summary": "path-item keys that are not operations are not skipped",
            "x-internal": True,
        },
        "/b": {"get": {**_op("no_object"), "x-mcp": "read"}},
    }
    catalog = build_catalog(_doc(paths))
    assert set(catalog.operations) == {"put_ok", "patch_ok"}
    assert catalog.operations["put_ok"].tool is None  # a tool that is not a string is no tool
    assert sorted(catalog.skipped) == [
        "GET /a: x-mcp class 'admin' is not offered over MCP",
        "GET /b: x-mcp is not an object",
        "OPTIONS /a: method OPTIONS is not supported",
        "TRACE /a: method TRACE is not supported",
    ]


def test_non_local_and_unresolvable_refs_skip_the_operation():
    external = {"$ref": "https://evil.test/schema.json"}
    paths = {
        "/param": {"get": _op("ext_param", parameters=[{"name": "q", "in": "query", "schema": external}])},
        "/body": {"post": _op("ext_body", "write", requestBody={
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Outside"}}}})},
        "/response": {"get": _op("ext_response", responses={
            "200": {"description": "ok", "content": {"application/json": {"schema": external}}}})},
        "/dangling": {"get": _op("dangling", parameters=[
            {"name": "q", "in": "query", "schema": {"$ref": "#/components/schemas/Missing"}}])},
        "/fine": {"get": _op("fine")},
    }
    catalog = build_catalog(_doc(paths, schemas={"Outside": {"$ref": "other.json#/Thing"}}))
    assert set(catalog.operations) == {"fine"}
    reasons = dict(entry.split(": ", 1) for entry in catalog.skipped)
    assert reasons["GET /param"] == "non-local $ref 'https://evil.test/schema.json'"
    assert reasons["GET /response"] == "non-local $ref 'https://evil.test/schema.json'"
    assert "other.json#/Thing" in reasons["POST /body"]  # reached through a local component
    assert "#/components/schemas/Missing" in reasons["GET /dangling"]


def test_parameter_rules():
    shared = [{"name": "instance_id", "in": "path", "required": True, "schema": {"type": "string"}},
              {"name": "verbose", "in": "query", "schema": {"type": "boolean"}}]
    paths = {
        "/instances/{instance_id}/x": {
            "parameters": shared,
            "get": _op("merged", parameters=[
                {"name": "verbose", "in": "query", "required": True, "schema": {"type": "boolean"}},
                {"name": "session", "in": "cookie", "schema": {"type": "string"}},
                {"name": "x-req", "in": "header", "required": True, "schema": {"type": "string"}},
            ]),
        },
        "/p/{a}": {"get": _op("path_not_required", parameters=[
            {"name": "a", "in": "path", "required": False, "schema": {"type": "string"}}])},
        "/q/{a}": {"get": _op("template_mismatch", parameters=[
            {"name": "b", "in": "path", "required": True, "schema": {"type": "string"}}])},
        "/r/{a}": {"get": _op("name_clash", parameters=[
            {"name": "a", "in": "path", "required": True, "schema": {"type": "string"}},
            {"name": "a", "in": "query", "schema": {"type": "string"}}])},
        "/s": {"post": _op("body_clash", "write", parameters=[{"name": "body", "in": "query", "schema": {}}],
                           requestBody={"content": {"application/json": {"schema": {"type": "object"}}}})},
        "/t": {"post": _op("key_clash", "write", parameters=[
            {"name": "idempotency_key", "in": "query", "schema": {"type": "string"}}])},
        "/u": {"post": _op("form_body", "write", requestBody={
            "required": True, "content": {"multipart/form-data": {"schema": {"type": "object"}}}})},
        "/v": {"post": _op("optional_body", "write", requestBody={
            "content": {"application/json": {"schema": {"type": "object"}}}})},
        "/w": {"get": _op("read_key", parameters=[
            {"name": "idempotency_key", "in": "query", "schema": {"type": "string"}}])},
    }
    catalog = build_catalog(_doc(paths))
    assert set(catalog.operations) == {"merged", "path_not_required", "optional_body", "read_key"}

    merged = catalog.operations["merged"]
    assert (merged.path_params, merged.query_params) == (("instance_id",), ("verbose",))
    assert merged.input_schema["required"] == ["instance_id", "verbose"]  # the operation's own entry wins
    assert list(merged.input_schema["properties"]) == ["instance_id", "verbose"]
    assert catalog.operations["path_not_required"].input_schema["required"] == ["a"]
    optional = catalog.operations["optional_body"]
    assert optional.has_body and optional.input_schema["required"] == []

    reasons = dict(entry.split(": ", 1) for entry in catalog.skipped)
    assert set(reasons) == {"GET /q/{a}", "GET /r/{a}", "POST /s", "POST /t", "POST /u"}
    assert "path template" in reasons["GET /q/{a}"]
    assert "'a'" in reasons["GET /r/{a}"]
    assert "'body'" in reasons["POST /s"]
    assert "'idempotency_key'" in reasons["POST /t"]
    assert "application/json" in reasons["POST /u"]


def test_summary_and_description_rules():
    long_line = "word " * 100
    paths = {
        "/a": {"get": _op("from_description", description="First line.\nSecond line.")},
        "/b": {"get": _op("clipped", summary=long_line, description="d" * 2500)},
        "/c": {"get": _op("blank")},
    }
    catalog = build_catalog(_doc(paths))
    assert catalog.operations["from_description"].summary == "First line."
    assert catalog.operations["from_description"].description == "First line.\nSecond line."
    clipped = catalog.operations["clipped"]
    assert len(clipped.summary) == 200 and clipped.summary.endswith("…")
    assert len(clipped.description) == 2000 and clipped.description.endswith("…")
    assert (catalog.operations["blank"].summary, catalog.operations["blank"].description) == ("", "")


def test_search_ranks_operation_id_matches_first(catalog):
    # get_instance: operation_id 3 + path 2 + summary 1; live_command: path 2; create_backtest: description 1.
    results, total = catalog.search("instance")
    assert [op.operation_id for op in results] == ["get_instance", "live_command", "create_backtest"]
    assert total == 3
    assert [op.operation_id for op in catalog.search("INSTANCE!")[0]] == [op.operation_id for op in results]

    # Equal scores fall back to operation_id order; "backtests" is not the token "backtest".
    results, total = catalog.search("backtest")
    assert [op.operation_id for op in results] == ["create_backtest", "get_backtest_logs", "list_backtests"]
    assert total == 3

    # Scores add up across tokens.
    results, _ = catalog.search("list models")
    assert results[0].operation_id == "list_models"

    assert catalog.search("zzzqqq") == ([], 0)


def test_search_class_filter_and_limit(catalog):
    everything, total = catalog.search("")
    assert [op.operation_id for op in everything] == sorted(EXPECTED_OPERATIONS)[:20]
    assert total == len(EXPECTED_OPERATIONS)

    reads, total = catalog.search("", cls="read", limit=50)
    assert [op.operation_id for op in reads] == READ_OPERATIONS
    assert total == len(READ_OPERATIONS)

    assert [op.operation_id for op in catalog.search("", cls="money")[0]] == ["live_command"]
    assert [op.operation_id for op in catalog.search("backtest", cls="write")[0]] == ["create_backtest"]
    assert catalog.search("backtest", cls="money") == ([], 0)

    first_two, total = catalog.search("", limit=2)
    assert [op.operation_id for op in first_two] == sorted(EXPECTED_OPERATIONS)[:2]
    assert total == len(EXPECTED_OPERATIONS)
    results, total = catalog.search("backtest", limit=1)
    assert [op.operation_id for op in results] == ["create_backtest"] and total == 3


def test_paging_params_detected(catalog):
    assert PAGING_PARAM_RE.pattern == (
        "(limit|offset|page|page_size|cursor|since|since_line|until|start|end|start_date|end_date|before|after|days"
        "|lines|tail)")
    assert catalog.operations["list_backtests"].paging_params == ("limit", "offset")
    assert catalog.operations["get_backtest_logs"].paging_params == ("since_line", "lines")
    assert catalog.operations["list_mcp_approvals"].paging_params == ("limit",)
    assert catalog.operations["get_instance"].paging_params == ()
    assert catalog.operations["search_routes"].paging_params == ()

    for name in ("limit", "page_size", "cursor", "start_date", "tail", "days", "since"):
        assert PAGING_PARAM_RE.fullmatch(name), name
    for name in ("status", "starting", "page_count", "instance_id", "sort_by"):
        assert not PAGING_PARAM_RE.fullmatch(name), name


def test_canonical_hash_matches_contract_formula(sample):
    paths, components = sample["paths"], sample["components"]
    canonical = {"paths": paths, "components": components}
    expected = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                              .encode("utf-8")).hexdigest()
    assert "—" in json.dumps(paths, ensure_ascii=False)  # the fixture carries non-ASCII text...
    ascii_escaped = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"))
                                   .encode("utf-8")).hexdigest()
    assert ascii_escaped != expected  # ...so the hash proves ensure_ascii=False
    assert canonical_schema_hash(paths, components) == expected
    assert build_catalog(sample).schema_hash == expected
    assert schema_hash(paths, components) == expected  # the fake API publishes the same value

    reordered = {key: paths[key] for key in reversed(list(paths))}
    assert canonical_schema_hash(reordered, components) == expected  # key order does not matter


def test_components_only_change_changes_hash(sample):
    # Input schemas are inlined from #/components, so a changed body model changes no path.
    changed = copy.deepcopy(sample)
    changed["components"]["schemas"]["LiveCommandBody"]["properties"]["type"]["enum"].append("liquidate_all")
    assert changed["paths"] == sample["paths"]

    before, after = build_catalog(sample), build_catalog(changed)
    assert after.schema_hash != before.schema_hash
    assert after.schema_hash == canonical_schema_hash(changed["paths"], changed["components"])
    body = after.operations["live_command"].input_schema["properties"]["body"]
    assert "liquidate_all" in body["properties"]["type"]["enum"]

    # The formula's `components or {}`: no components hashes as empty components.
    paths = sample["paths"]
    assert canonical_schema_hash(paths, None) == canonical_schema_hash(paths, {})
    assert canonical_schema_hash(paths, {}) != canonical_schema_hash(paths, sample["components"])
    assert build_catalog({"paths": {}}).schema_hash == canonical_schema_hash({}, None) == schema_hash({})


def test_invalid_document_raises():
    for doc in ([], {"openapi": "3.1.0"}, {"paths": []}, {"paths": {}, "components": []}):
        with pytest.raises(ValueError):
            build_catalog(doc)
    empty = build_catalog({"paths": {}})
    assert (empty.operations, empty.skipped) == ({}, ())
