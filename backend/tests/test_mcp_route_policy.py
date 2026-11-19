"""Every route carries an x-mcp policy and an operation_id (contract §1).

The policy on each decorator is the only list of what an MCP caller may do: the
gate reads `openapi_extra["x-mcp"]` back through `validate_policy`, and the MCP
server builds its tools from the operation_ids. These tests hold the annotations
to the rules the gate relies on. They cover the route prefixes annotated so far
(ANNOTATED_PREFIXES); a later batch widens the tuple, and the last one replaces
the filter with every route.
"""
from __future__ import annotations

import os
import sys

from fastapi.routing import APIRoute
from pydantic import BaseModel

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from mcp_policy import NAME_RE, mcp_policy, validate_policy  # noqa: E402

# The spec's direct tools: an x-mcp "tool" must name one of these.
EXPECTED_DIRECT_TOOLS = frozenset({
    "list_instances", "get_instance", "get_live_state", "get_instance_portfolio_history",
    "get_account_summary", "get_positions", "get_orders", "list_backtests",
    "get_backtest_status", "get_backtest_summary", "create_backtest", "stop_backtest",
    "list_strategies", "get_strategy", "get_symbol_info", "get_symbol_historicals",
    "get_market_news", "list_swing_signals", "start_instance", "stop_instance", "live_command",
})


def _api_routes() -> list[APIRoute]:
    from api import main

    return [r for r in main.app.routes if isinstance(r, APIRoute)]


def _policy(route) -> dict | None:
    return validate_policy((getattr(route, "openapi_extra", None) or {}).get("x-mcp"))


ANNOTATED_PREFIXES = ("/health", "/help", "/auth/", "/onboarding/", "/chatbot/", "/status", "/llm/",
    "/llm-usage/", "/benzinga/", "/notification-preferences", "/push/", "/notifications/", "/tickers",
    "/prices", "/history", "/app-updates/", "/ota/", "/codex/", "/claude/", "/admin/", "/ollama/",
    "/bedrock/", "/openrouter/", "/kalshi/img", "/instances", "/live-commands/", "/config/", "/strategies",
    "/models", "/widget/", "/nexus/config/")
PENDING = ("/instances/{instance_id}/kalshi/",)   # path prefixes inside an annotated prefix that a later batch owns


def _annotated():
    return [r for r in _api_routes() if r.path.startswith(ANNOTATED_PREFIXES) and not r.path.startswith(PENDING or ("\0",))]


def test_annotated_routes_have_valid_x_mcp():
    bad = [f"{sorted(r.methods)} {r.path}" for r in _annotated() if _policy(r) is None]
    assert not bad, f"missing/invalid x-mcp (add openapi_extra=mcp_policy(...)): {bad}"


def test_operation_ids_are_unique_snake_case_and_short():
    ids = [r.operation_id for r in _annotated()]
    assert all(i and NAME_RE.match(i) for i in ids) and len(ids) == len(set(ids))


def test_tools_are_unique_and_known():
    tools = [p["tool"] for p in map(_policy, _annotated()) if p and "tool" in p]
    assert len(tools) == len(set(tools)) and set(tools) <= EXPECTED_DIRECT_TOOLS


def test_exposed_routes_have_a_description():
    bare = [r.path for r in _annotated() if _policy(r)["class"] != "never" and not (r.description or "").strip()]
    assert not bare


def test_every_delete_is_irreversible_or_never():
    assert all(_policy(r)["class"] in ("irreversible", "never") for r in _annotated() if "DELETE" in r.methods)


def test_get_routes_are_read_or_never():
    assert all(_policy(r)["class"] in ("read", "never") for r in _annotated() if r.methods == {"GET"})


NEVER_PREFIXES = ("/auth/", "/codex/", "/claude/", "/admin/", "/ota/", "/chatbot/internal/", "/push/",
                  "/app-updates/upload", "/app-updates/rollback", "/llm/test", "/llm/outputs", "/kalshi/img",
                  "/ollama/", "/bedrock/", "/openrouter/", "/notifications/test", "/benzinga/test")


def test_sensitive_routes_are_never():
    assert all(_policy(r)["class"] == "never" for r in _annotated()
               if r.path.startswith(NEVER_PREFIXES) and not (r.path == "/llm/outputs/{output_id}" and r.methods == {"GET"}))


def _body_model(route):
    """The type behind a route's request body, or None when it has no body.

    Current FastAPI keeps it on `body_field.field_info.annotation` (its ModelField has
    no `type_`); `type_` is the fallback for an older FastAPI that had it.
    """
    body_field = getattr(route, "body_field", None)
    if body_field is None:
        return None
    model = getattr(getattr(body_field, "field_info", None), "annotation", None)
    return model if model is not None else getattr(body_field, "type_", None)


def _target_resolves(route) -> bool:
    """True when a money route's target field is one of its path params or body fields."""
    (field,) = _policy(route)["target"].values()
    body_fields = set(getattr(_body_model(route), "model_fields", {}) or {})
    return field in {x.name for x in route.dependant.path_params} | body_fields


def test_money_targets_resolve():
    for r in _annotated():
        if _policy(r)["class"] == "money":
            assert _target_resolves(r), r.path


SPEC_LIVE_ROUTES = {   # spec §6 "money" list, as routes; each must stay gated
    ("POST", "/instances/{instance_id}/start"), ("POST", "/instances/{instance_id}/stop"),
    ("POST", "/instances/{instance_id}/live-command"), ("POST", "/config/start-broker"),
    ("POST", "/instances/{instance_id}/swing/signals/{signal_id}/decision"),
    ("POST", "/instances/{instance_id}/swing/signals/{signal_id}/resend"),
    ("POST", "/instances/{instance_id}/link-brokerage"), ("POST", "/instances/{instance_id}/link-strategy"),
    ("PATCH", "/instances/{instance_id}"), ("PUT", "/strategies/{strategy_id}"), ("PUT", "/models/{model_id}"),
}


def test_spec_named_live_routes_are_gated():
    by_key = {(m, r.path): _policy(r) for r in _api_routes() for m in r.methods}
    for key in SPEC_LIVE_ROUTES:
        if key[1].startswith(ANNOTATED_PREFIXES):
            assert by_key[key]["class"] in ("money", "irreversible"), key


def test_model_writes_always_need_approval():          # lead ruling
    for m, path in (("POST", "/models"), ("PUT", "/models/{model_id}"), ("DELETE", "/models/{model_id}")):
        assert [_policy(r)["class"] for r in _api_routes() if r.path == path and m in r.methods] == ["irreversible"]


class _ProbeBody(BaseModel):
    brokerage_id: str
    note: str = ""


def _probe_endpoint(instance_id: str, body: _ProbeBody) -> dict:
    return {}


def _probe_route(target: dict) -> APIRoute:
    return APIRoute("/probe/{instance_id}", _probe_endpoint, methods=["POST"],
                    openapi_extra=mcp_policy("money", target=target))


def test_target_check_reads_body_and_path_fields():
    assert _target_resolves(_probe_route({"brokerage": "brokerage_id"}))      # a body-model field
    assert _target_resolves(_probe_route({"instance": "instance_id"}))        # a path param
    assert not _target_resolves(_probe_route({"strategy": "strategy_id"}))    # neither
