"""The operation catalog, built at runtime from the API's MCP-visible OpenAPI document (contract sections 1 and 4).

build_catalog() turns each operation into an Operation: its x-mcp class, tool
name and cost flag, and one merged input schema, {"type": "object",
"properties": ..., "required": ..., "additionalProperties": False}. Path and
query parameters sit at top level (path parameters always required), and the
application/json request body sits under "body". Header and cookie parameters
are not arguments.

An operation that cannot be offered safely is left out and named in
Catalog.skipped as "<METHOD> <path>: <reason>": an operationId that is missing,
malformed or shared (every copy is skipped), no x-mcp or a class outside
CLASSES (the API already drops "never"; this is the second check), a method
other than GET, POST, PUT, PATCH or DELETE, a $ref that points outside the
document or names nothing in it, or parameters that do not map onto one flat
argument object (a path template that disagrees with the path parameters, two
arguments with one name, or a non-JSON request body).

$refs into #/components are inlined, so each input schema stands alone. A
reference back into a schema that is already being expanded becomes a
plain-object placeholder, and expansion stops MAX_REF_DEPTH references deep.

schema_hash is canonical_schema_hash(doc["paths"], doc.get("components")), the
contract's x-mcp-schema-hash formula; the schema watcher compares the published
value with it. It covers components because the input schemas are inlined from
there: a changed request-body model changes no path.
"""
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

CLASSES = ("read", "write", "money", "irreversible")
METHODS = ("get", "post", "put", "patch", "delete")
PAGING_PARAM_RE = re.compile(
    r"(limit|offset|page|page_size|cursor|since|since_line|until|start|end|start_date|end_date|before|after|days"
    r"|lines|tail)")
OPERATION_ID_RE = re.compile(r"[A-Za-z0-9_]{1,64}")
MAX_REF_DEPTH = 32
MAX_SUMMARY_CHARS = 200
MAX_DESCRIPTION_CHARS = 2000

# The operation keys of an OpenAPI path item; its other keys (parameters, summary, servers, x-*) are not operations.
_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_PARAMETER_LOCATIONS = ("path", "query", "header", "cookie")
_COMPONENTS_PREFIX = "#/components/"
_PATH_TEMPLATE = re.compile(r"\{([^{}/]+)\}")
_SEARCH_TOKEN = re.compile(r"[a-z0-9]+")
_MAX_QUOTED_CHARS = 120


@dataclass(frozen=True)
class Operation:
    operation_id: str
    method: str  # upper case, as sent
    path: str  # the template, e.g. /instances/{instance_id}
    summary: str
    description: str
    cls: str
    tool: Optional[str]
    cost: bool
    path_params: tuple[str, ...]
    query_params: tuple[str, ...]
    has_body: bool
    input_schema: dict = field(repr=False)

    @property
    def approval_may_apply(self) -> bool:
        return self.cls in ("money", "irreversible")

    @property
    def needs_idempotency_key(self) -> bool:
        return self.cls != "read"

    @property
    def paging_params(self) -> tuple[str, ...]:
        return tuple(name for name in self.query_params if PAGING_PARAM_RE.fullmatch(name))


@dataclass(frozen=True)
class Catalog:
    operations: dict[str, Operation]
    schema_hash: str
    skipped: tuple[str, ...]

    def search(self, query: str, cls: Optional[str] = None, limit: int = 20) -> tuple[list[Operation], int]:
        """(the best matches, at most limit of them; how many matched). An empty query matches everything."""
        tokens = _SEARCH_TOKEN.findall(query.lower())
        scored = []
        for op in self.operations.values():
            if cls is not None and op.cls != cls:
                continue
            score = _score(op, tokens)
            if tokens and score == 0:
                continue
            scored.append((-score, op.operation_id, op))
        scored.sort(key=lambda entry: entry[:2])
        return [op for _, _, op in scored[:max(limit, 0)]], len(scored)


def _score(op: Operation, tokens: list[str]) -> int:
    """Per token: +3 among the operation_id's _-separated words, +2 in the path, +1 in the summary or description."""
    id_words = set(op.operation_id.lower().split("_"))
    path, summary, description = op.path.lower(), op.summary.lower(), op.description.lower()
    score = 0
    for token in tokens:
        score += 3 * (token in id_words) + 2 * (token in path) + (token in summary or token in description)
    return score


class _Skip(Exception):
    """This operation is left out of the catalog; the message is the reason."""


def canonical_schema_hash(paths: Any, components: Any) -> str:
    """The contract's x-mcp-schema-hash: sha256 hex of the canonical JSON of
    {"paths": paths, "components": components or {}}."""
    canonical = json.dumps({"paths": paths, "components": components or {}}, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _quoted(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= _MAX_QUOTED_CHARS else text[:_MAX_QUOTED_CHARS - 1] + "…"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _lookup(ref: str, components: Mapping[str, Any]) -> Any:
    """The value a #/components/... JSON pointer names; ValueError for any other $ref or one that names nothing."""
    if not ref.startswith(_COMPONENTS_PREFIX):
        raise ValueError(f"cannot resolve $ref {_quoted(ref)}: only #/components/... references are inlined")
    node: Any = components
    for part in ref[len(_COMPONENTS_PREFIX):].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, Mapping) or part not in node:
            raise ValueError(f"cannot resolve $ref {_quoted(ref)}: it names nothing in the document")
        node = node[part]
    return node


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")


def _resolve(node: Any, components: Mapping[str, Any], stack: tuple[str, ...]) -> Any:
    if isinstance(node, list):
        return [_resolve(item, components, stack) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if not isinstance(ref, str):  # a property that happens to be named "$ref" is not a reference
        return {key: _resolve(value, components, stack) for key, value in node.items()}
    siblings = {key: _resolve(value, components, stack) for key, value in node.items() if key != "$ref"}
    if ref in stack:
        name = _ref_name(ref)
        return {**siblings, "type": "object", "description": f"Recursive reference to {name}; send a JSON object."}
    if len(stack) >= MAX_REF_DEPTH:
        name = _ref_name(ref)
        return {**siblings,
                "description": f"Schema {name} not expanded: references nest more than {MAX_REF_DEPTH} deep."}
    target = _resolve(_lookup(ref, components), components, stack + (ref,))
    if not siblings:
        return target
    if isinstance(target, dict):
        return {**target, **siblings}  # the use site's own keywords (default, description) win
    return {**siblings, "allOf": [target]}


def resolve_refs(schema: Any, components: Mapping[str, Any]) -> Any:
    """A fresh copy of schema with every #/components/... $ref inlined.

    components is the document's "components" object. ValueError for a $ref
    that points anywhere else or names nothing.
    """
    return _resolve(schema, components, ())


def _nonlocal_ref(node: Any) -> Optional[str]:
    """The first $ref anywhere in node that points outside the document, or None."""
    if isinstance(node, list):
        for item in node:
            found = _nonlocal_ref(item)
            if found is not None:
                return found
    elif isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            return ref
        for value in node.values():
            found = _nonlocal_ref(value)
            if found is not None:
                return found
    return None


def _parameters(item: dict, operation: dict, components: Mapping[str, Any]) -> list[dict]:
    """Path-item parameters overlaid by the operation's own, keyed by (name, in), refs resolved."""
    merged: dict[tuple[str, str], dict] = {}
    for source in (item.get("parameters"), operation.get("parameters")):
        if source is None:
            continue
        if not isinstance(source, list):
            raise ValueError("parameters is not a list")
        for raw in source:
            param = resolve_refs(raw, components)
            if not (isinstance(param, dict) and isinstance(param.get("name"), str)
                    and param.get("in") in _PARAMETER_LOCATIONS and isinstance(param.get("schema", {}), dict)):
                raise ValueError(f"malformed parameter {_quoted(raw)}")
            merged[(param["name"], param["in"])] = param
    return list(merged.values())


def _input_schema(path: str, item: dict, operation: dict, cls: str,
                  components: Mapping[str, Any]) -> tuple[dict, tuple[str, ...], tuple[str, ...], bool]:
    """(input schema, path params, query params, has_body); ValueError when the arguments are not one flat object."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    path_params: list[str] = []
    query_params: list[str] = []
    for param in _parameters(item, operation, components):
        location, name = param["in"], param["name"]
        if location in ("header", "cookie"):
            continue
        if name in properties:
            raise ValueError(f"parameter {_quoted(name)} is both a path and a query parameter")
        prop = dict(param.get("schema", {}))
        description = param.get("description")
        if isinstance(description, str) and description and "description" not in prop:
            prop["description"] = description
        properties[name] = prop
        if location == "path":
            path_params.append(name)
            required.append(name)
        else:
            query_params.append(name)
            if param.get("required") is True:
                required.append(name)
    if set(_PATH_TEMPLATE.findall(path)) != set(path_params):
        raise ValueError("path parameters do not match the path template")
    if cls != "read" and "idempotency_key" in properties:
        raise ValueError(f"parameter {_quoted('idempotency_key')} collides with the tool's idempotency key")

    has_body = "requestBody" in operation
    if has_body:
        body = resolve_refs(operation["requestBody"], components)
        content = body.get("content") if isinstance(body, dict) else None
        media = content.get("application/json") if isinstance(content, dict) else None
        if not isinstance(media, dict):
            raise ValueError("request body is not application/json")
        if "body" in properties:
            raise ValueError(f"parameter {_quoted('body')} collides with the request body")
        properties["body"] = media.get("schema", {})
        if body.get("required") is True:
            required.append("body")
    schema = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
    return schema, tuple(path_params), tuple(query_params), has_body


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _build_operation(method: str, path: str, item: dict, operation: Any, components: Mapping[str, Any],
                     id_counts: Counter) -> Operation:
    if method not in METHODS:
        raise _Skip(f"method {method.upper()} is not supported")
    if not isinstance(operation, dict):
        raise _Skip("operation is not an object")
    operation_id = operation.get("operationId")
    if not (isinstance(operation_id, str) and OPERATION_ID_RE.fullmatch(operation_id)):
        raise _Skip("operationId is missing or not 1-64 characters from A-Z a-z 0-9 _")
    if id_counts[operation_id] > 1:
        raise _Skip(f"operationId {_quoted(operation_id)} is not unique")
    if "x-mcp" not in operation:
        raise _Skip("no x-mcp annotation")
    policy = operation["x-mcp"]
    if not isinstance(policy, dict):
        raise _Skip("x-mcp is not an object")
    cls = policy.get("class")
    if not (isinstance(cls, str) and cls in CLASSES):
        raise _Skip(f"x-mcp class {_quoted(cls)} is not offered over MCP")
    ref = _nonlocal_ref([item.get("parameters"), operation])
    if ref is not None:
        raise _Skip(f"non-local $ref {_quoted(ref)}")
    if not path.startswith("/") or path.startswith("//"):
        raise _Skip("path is not an absolute path on the API")
    try:
        input_schema, path_params, query_params, has_body = _input_schema(path, item, operation, cls, components)
    except ValueError as exc:
        raise _Skip(str(exc)) from None

    summary, description = _text(operation.get("summary")), _text(operation.get("description"))
    if not summary and description:
        summary = description.splitlines()[0].strip()
    tool = policy.get("tool")
    return Operation(
        operation_id=operation_id,
        method=method.upper(),
        path=path,
        summary=_clip(summary, MAX_SUMMARY_CHARS),
        description=_clip(description, MAX_DESCRIPTION_CHARS),
        cls=cls,
        tool=tool if isinstance(tool, str) else None,
        cost=policy.get("cost") is True,
        path_params=path_params,
        query_params=query_params,
        has_body=has_body,
        input_schema=input_schema,
    )


def build_catalog(doc: dict) -> Catalog:
    """The catalog of doc's offerable operations; ValueError when doc is not an OpenAPI document at all."""
    if not isinstance(doc, dict) or not isinstance(doc.get("paths"), dict):
        raise ValueError("not an OpenAPI document: 'paths' is missing or not an object")
    components = doc.get("components", {})
    if not isinstance(components, dict):
        raise ValueError("not an OpenAPI document: 'components' is not an object")
    paths = doc["paths"]

    entries = [(method, path, item, operation)
               for path, item in paths.items() if isinstance(item, dict)
               for method, operation in item.items() if method in _HTTP_METHODS]
    id_counts = Counter(operation.get("operationId") for method, _, _, operation in entries
                        if method in METHODS and isinstance(operation, dict)
                        and isinstance(operation.get("operationId"), str))
    operations: dict[str, Operation] = {}
    skipped: list[str] = []
    for method, path, item, operation in entries:
        try:
            op = _build_operation(method, path, item, operation, components, id_counts)
        except _Skip as exc:
            skipped.append(f"{method.upper()} {path}: {exc}")
            continue
        operations[op.operation_id] = op
    return Catalog(operations=operations, schema_hash=canonical_schema_hash(paths, components),
                   skipped=tuple(skipped))
