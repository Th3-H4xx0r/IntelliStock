"""Order authority and user administration are admin-only.

Before this, every authenticated user was an administrator in all but name:
any account could rewrite another account's password and role, mint a new
admin, start or stop a live instance, relink its brokerage, edit the strategy
it trades, or submit an order through /live-command. On a real-money Alpaca
instance behind a public API that is the whole security model.

These tests pin two things: the dependency each mutating route is wired to,
and the behaviour of the dependency itself. They also pin that the operator
cannot lock themselves out -- the DEFAULT_ADMIN_USERNAME account is created
with role admin and passes require_admin.
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest
from fastapi import HTTPException

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# Every mutating route that can move money, change what trades, or change who
# may do either.
ADMIN_ONLY_ROUTES = [
    ("POST", "/instances"),
    ("PATCH", "/instances/{instance_id}"),
    ("DELETE", "/instances/{instance_id}"),
    ("POST", "/instances/{instance_id}/stocks"),
    ("DELETE", "/instances/{instance_id}/stocks/{symbol}"),
    ("POST", "/instances/{instance_id}/start"),
    ("POST", "/instances/{instance_id}/stop"),
    ("POST", "/instances/{instance_id}/clear-state"),
    # Writes the readiness report a real-money launcher trusts. If anything on
    # this list has to be admin-only, it is the route that can authorize a
    # funded start.
    ("POST", "/instances/{instance_id}/readiness-waiver"),
    ("POST", "/instances/{instance_id}/link-strategy"),
    ("POST", "/instances/{instance_id}/unlink-strategy"),
    ("POST", "/instances/{instance_id}/link-brokerage"),
    ("POST", "/instances/{instance_id}/link-data-brokerage"),
    ("POST", "/instances/{instance_id}/live-command"),
    ("PATCH", "/instances/{instance_id}/kalshi/config"),
    ("POST", "/strategies"),
    ("PUT", "/strategies/{strategy_id}"),
    ("DELETE", "/strategies/{strategy_id}"),
    ("POST", "/brokerages"),
    ("PUT", "/brokerages/{brokerage_id}"),
    ("DELETE", "/brokerages/{brokerage_id}"),
    ("POST", "/brokerages/ensure-ai-alpaca"),
    ("POST", "/brokerages/{brokerage_id}/kalshi/kill"),
    ("POST", "/brokerages/{brokerage_id}/kalshi/instances"),
    ("POST", "/brokerages/{brokerage_id}/kalshi/backtests"),
    ("POST", "/auth/users"),
    ("DELETE", "/auth/users/{user_id}"),
    # Round 2 (whole-branch review I3).
    ("PATCH", "/nexus/config/{instance_id}"),
    ("POST", "/config/start-broker"),
    ("POST", "/admin/credentials/migrate"),
]

# Read-only routes stay open to any authenticated user.
READ_ONLY_ROUTES = [
    ("GET", "/instances"),
    ("GET", "/instances/{instance_id}"),
    ("GET", "/strategies"),
    ("GET", "/brokerages"),
    ("GET", "/auth/users"),
    ("GET", "/auth/me"),
]


def _endpoint(method: str, path: str):
    from api import main

    for route in main.app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", ()):
            return route.endpoint
    raise AssertionError(f"no route for {method} {path}")


def _guard_of(method: str, path: str):
    """The dependency behind the endpoint's ``current_user`` parameter."""
    param = inspect.signature(_endpoint(method, path)).parameters.get("current_user")
    assert param is not None, f"{method} {path} has no current_user parameter"
    return getattr(param.default, "dependency", None)


# --- the dependency -------------------------------------------------------


def test_require_admin_accepts_an_admin():
    from api.main import require_admin

    user = {"id": "u1", "username": "root", "role": "admin"}
    assert require_admin(user) is user


def test_require_admin_is_case_and_whitespace_insensitive():
    from api.main import require_admin

    assert require_admin({"id": "u1", "username": "root", "role": " Admin "})


@pytest.mark.parametrize("role", ["user", "", None, "administrator", "Admin ", "superuser"])
def test_require_admin_rejects_everything_that_is_not_the_admin_role(role):
    from api.main import require_admin

    if role == "Admin ":
        pytest.skip("covered by the case-insensitivity test")
    with pytest.raises(HTTPException) as ei:
        require_admin({"id": "u2", "username": "bob", "role": role})
    assert ei.value.status_code == 403


def test_require_admin_rejects_a_user_with_no_role_key_at_all():
    from api.main import require_admin

    with pytest.raises(HTTPException) as ei:
        require_admin({"id": "u2", "username": "bob"})
    assert ei.value.status_code == 403


# --- route wiring ---------------------------------------------------------


@pytest.mark.parametrize("method,path", ADMIN_ONLY_ROUTES)
def test_mutating_routes_require_admin(method, path):
    from api.main import require_admin

    assert _guard_of(method, path) is require_admin, (
        f"{method} {path} is not behind require_admin"
    )


@pytest.mark.parametrize("method,path", READ_ONLY_ROUTES)
def test_read_only_routes_stay_open_to_any_authenticated_user(method, path):
    from api.main import get_current_user

    assert _guard_of(method, path) is get_current_user


def test_put_auth_user_stays_self_service_at_the_dependency_level():
    """It is the ONE mutating auth route that a non-admin may call -- to
    change their own password or email. The role check is inside the body."""
    from api.main import get_current_user

    assert _guard_of("PUT", "/auth/users/{user_id}") is get_current_user


# --- PUT /auth/users/{id} ------------------------------------------------


def _put(monkeypatch, *, target, actor, **body_kwargs):
    from api import main

    calls = {}

    def _fake_update(conn, user_id, password=None, role=None, email=None):
        calls.update(user_id=user_id, password=password, role=role, email=email)
        return {"id": user_id, "username": "target", "role": role or "user"}

    monkeypatch.setattr(main, "update_user", _fake_update)
    body = main.UpdateUserBody(**body_kwargs)
    res = main.api_update_auth_user(
        user_id=target, body=body, conn=None, current_user=actor)
    return res, calls


ADMIN = {"id": "admin-1", "username": "root", "role": "admin"}
BOB = {"id": "bob-1", "username": "bob", "role": "user"}


def test_a_user_may_change_their_own_password(monkeypatch):
    _res, calls = _put(monkeypatch, target="bob-1", actor=BOB, password="newpassword")
    assert calls["user_id"] == "bob-1" and calls["password"] == "newpassword"


def test_a_user_may_not_change_someone_elses_password(monkeypatch):
    with pytest.raises(HTTPException) as ei:
        _put(monkeypatch, target="admin-1", actor=BOB, password="pwned-password")
    assert ei.value.status_code == 403


def test_a_user_may_not_grant_themselves_the_admin_role(monkeypatch):
    with pytest.raises(HTTPException) as ei:
        _put(monkeypatch, target="bob-1", actor=BOB, role="admin")
    assert ei.value.status_code == 403


def test_a_user_may_not_change_their_own_role_to_anything(monkeypatch):
    with pytest.raises(HTTPException) as ei:
        _put(monkeypatch, target="bob-1", actor=BOB, role="user")
    assert ei.value.status_code == 403


def test_an_admin_may_change_any_users_password_and_role(monkeypatch):
    _res, calls = _put(monkeypatch, target="bob-1", actor=ADMIN,
                       password="resetpassword", role="admin")
    assert calls["user_id"] == "bob-1"
    assert calls["role"] == "admin"


def test_a_user_may_change_their_own_email(monkeypatch):
    _res, calls = _put(monkeypatch, target="bob-1", actor=BOB, email="bob@example.com")
    assert calls["email"] == "bob@example.com"


# --- the operator is not locked out --------------------------------------


def test_the_default_admin_is_created_with_the_admin_role_and_passes_require_admin(
    store, monkeypatch
):
    """DEFAULT_ADMIN_USERNAME must still hold admin authority after this
    change, or the operator cannot administer their own deployment."""
    import auth_utils
    from api.main import require_admin

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", "operator")
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "a-sufficiently-long-password")

    auth_utils.ensure_default_admin(None)
    row = auth_utils.get_user_by_username(None, "operator")
    assert row is not None and row["role"] == "admin"

    # Exactly the dict get_current_user hands the dependency.
    current_user = {
        "id": row.get("id"),
        "username": row.get("username"),
        "role": row.get("role", "user"),
    }
    assert require_admin(current_user) is current_user


def test_the_default_admin_can_still_reach_every_admin_only_route(store, monkeypatch):
    """One assertion per route, using the real dependency, so a future route
    added to the admin set cannot silently exclude the operator."""
    import auth_utils
    from api.main import require_admin

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", "operator")
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "a-sufficiently-long-password")
    auth_utils.ensure_default_admin(None)
    row = auth_utils.get_user_by_username(None, "operator")
    operator = {"id": row["id"], "username": row["username"], "role": row["role"]}

    for method, path in ADMIN_ONLY_ROUTES:
        guard = _guard_of(method, path)
        assert guard(operator) is operator, f"operator refused by {method} {path}"


def test_create_user_route_is_admin_only_and_still_mints_admins_for_an_admin(monkeypatch):
    from api import main

    monkeypatch.setattr(
        main, "create_user",
        lambda conn, username, password, role="user", email=None: {
            "id": "new", "username": username, "role": role},
    )
    body = main.CreateUserBody(username="second-admin", password="hunter22", role="admin")
    got = main.api_create_auth_user(body=body, conn=None, current_user=ADMIN)
    assert got["role"] == "admin"


# ---------------------------------------------------------------------------
# The inverse test (whole-branch review I3).
#
# Listing the routes that ARE gated can only ever prove what it lists. This
# goes the other way: every non-GET route in the app must be either
# require_admin-gated or named here with a reason. A new mutating route is a
# test failure until someone decides which it is.
#
# Measured 2026-09-11: 104 mutating routes, 26 gated before this round, 29
# after. The 75 below are NOT a clean bill of health -- several say
# "candidate" and mean it. They are written down so the next round has a list
# instead of a search.
# ---------------------------------------------------------------------------

NOT_ADMIN_GATED = {
    # -- public by design -------------------------------------------------
    ("POST", "/auth/login"):
        "public: this IS the authentication, and it is rate limited",
    ("POST", "/auth/signup"):
        "public: gated by SECRET_AUTH_KEY, not by a session",

    # -- self-service: the caller's own account or device ------------------
    ("PUT", "/auth/users/{user_id}"):
        "self-service password/email; role change and other users are admin-only in the body",
    ("POST", "/onboarding/complete"):
        "self-service: sets a flag on the calling user's own row",
    ("POST", "/onboarding/reset"):
        "self-service: clears a flag on the calling user's own row",
    ("PUT", "/notification-preferences"):
        "self-service: the calling user's own notification routing",
    ("POST", "/push/devices"):
        "self-service: registers the caller's own push token",
    ("DELETE", "/push/devices/{token}"):
        "self-service: removes the caller's own push token",
    ("POST", "/notifications/test"):
        "self-service: sends a test notification to the caller",

    # -- the caller's own chatbot conversations ----------------------------
    ("POST", "/chatbot/conversations"):
        "self-service: conversations are scoped to the calling user",
    ("PATCH", "/chatbot/conversations/{conv_id}"):
        "self-service: scoped to the calling user's conversation",
    ("DELETE", "/chatbot/conversations/{conv_id}"):
        "self-service: scoped to the calling user's conversation",
    ("POST", "/chatbot/conversations/{conv_id}/clear"):
        "self-service: scoped to the calling user's conversation",
    ("POST", "/chatbot/conversations/{conv_id}/turn"):
        "self-service: scoped to the calling user's conversation",
    ("POST", "/chatbot/conversations/{conv_id}/confirm-tool"):
        "self-service: confirms a tool the caller's own turn proposed",
    ("POST", "/chatbot/conversations/{conv_id}/mcp-confirm"):
        "self-service: confirms a tool the caller's own turn proposed",

    # -- not session-authenticated at all ----------------------------------
    ("POST", "/llm/outputs"):
        "loopback-only (_require_loopback): llm_utils posts to it in-process",
    ("POST", "/chatbot/internal/mcp-tools-list"):
        "authenticated by the MCP session token, not a user session",
    ("POST", "/chatbot/internal/mcp-tool-call"):
        "authenticated by the MCP session token, not a user session",

    # -- POST that reads: a probe or a preview, writing nothing -----------
    ("POST", "/llm/test"):
        "read-only probe: tests an LLM config, persists nothing",
    ("POST", "/bedrock/list-models"):
        "read-only probe: lists a provider's catalog",
    ("POST", "/ollama/list-models"):
        "read-only probe: lists a provider's catalog",
    ("POST", "/openrouter/list-models"):
        "read-only probe: lists a provider's catalog",
    ("POST", "/benzinga/test"):
        "read-only probe: tests a news source, persists nothing",
    ("POST", "/brokerages/test-alpaca"):
        "read-only probe: validates credentials, persists nothing",
    ("POST", "/brokerages/test-kalshi"):
        "read-only probe: validates credentials, persists nothing",
    ("POST", "/models/{model_id}/test-cli"):
        "read-only probe: tests a CLI provider, persists nothing",
    ("POST", "/strategies/{strategy_id}/config-change-preview"):
        "read-only: computes the diff a PUT would make, writes nothing",

    # -- CANDIDATES for admin gating: model + provider credentials ---------
    ("POST", "/models"):
        "candidate: stores an LLM provider API key; not gated this round",
    ("PUT", "/models/{model_id}"):
        "candidate: stores an LLM provider API key; not gated this round",
    ("DELETE", "/models/{model_id}"):
        "candidate: deletes a model any instance may be using; not gated this round",
    ("POST", "/claude/login/start"):
        "candidate: starts an interactive provider login; not gated this round",
    ("POST", "/claude/login/{job_id}/submit"):
        "candidate: submits a provider login code; not gated this round",
    ("POST", "/claude/login/{job_id}/cancel"):
        "candidate: cancels a provider login job; not gated this round",
    ("POST", "/claude/logout"):
        "candidate: drops the shared Claude CLI session; not gated this round",
    ("POST", "/codex/install"):
        "candidate: installs the Codex CLI on the host; not gated this round",
    ("POST", "/codex/login/start"):
        "candidate: starts an interactive provider login; not gated this round",
    ("POST", "/codex/login/{job_id}/cancel"):
        "candidate: cancels a provider login job; not gated this round",
    ("POST", "/codex/logout"):
        "candidate: drops the shared Codex CLI session; not gated this round",

    # -- CANDIDATES for admin gating: backtests and the research agent -----
    ("POST", "/backtests"):
        "candidate: spends LLM budget on a backtest container; not gated this round",
    ("POST", "/backtests/stop-all"):
        "candidate: stops every running backtest; not gated this round",
    ("DELETE", "/backtests/{backtest_id}"):
        "candidate: deletes a backtest and its results; not gated this round",
    ("POST", "/backtests/{backtest_id}/stop"):
        "candidate: stops a running backtest; not gated this round",
    ("POST", "/backtests/{backtest_id}/pause"):
        "candidate: pauses a running backtest; not gated this round",
    ("POST", "/backtests/{backtest_id}/resume"):
        "candidate: resumes a backtest; not gated this round",
    ("POST", "/backtest-evidence/matrices"):
        "candidate: publishes an evidence matrix; not gated this round",
    ("DELETE", "/kalshi/backtests/{backtest_id}"):
        "candidate: deletes a Kalshi backtest; not gated this round",
    ("POST", "/kalshi/backtests/{backtest_id}/stop"):
        "candidate: stops a Kalshi backtest; not gated this round",
    ("POST", "/agent/control"):
        "candidate: starts/stops the research agent loop; not gated this round",
    ("POST", "/agent/restart"):
        "candidate: restarts the research agent; not gated this round",
    ("POST", "/agent/best"):
        "candidate: rewrites the agent's best-result record; not gated this round",
    ("POST", "/agent/results"):
        "candidate: appends to the agent's result set; not gated this round",
    ("POST", "/agent/top5"):
        "candidate: rewrites the agent's top-5 record; not gated this round",
    ("POST", "/agent/cycle-log"):
        "candidate: appends to the agent cycle log; not gated this round",
    ("POST", "/agent/cycle-log/{log_id}/update"):
        "candidate: updates an agent cycle log row; not gated this round",
    ("POST", "/agent/increment-count"):
        "candidate: bumps the agent's run counter; not gated this round",
    ("POST", "/agent/resume-timer"):
        "candidate: resumes the agent timer; not gated this round",
    ("POST", "/agent/runs/{log_id}/force-stop"):
        "candidate: force-stops an agent run; not gated this round",

    # -- CANDIDATES for admin gating: Nexus, discovery, digests, learning --
    ("POST", "/nexus/control"):
        "candidate: starts/stops the Graph Nexus service; not gated this round",
    ("POST", "/nexus/rebuild"):
        "candidate: queues a destructive Nexus rebuild; not gated this round",
    ("POST", "/nexus/delete-edges"):
        "candidate: deletes graph edges; not gated this round",
    ("POST", "/discover/control"):
        "candidate: starts/stops stock discovery; not gated this round",
    ("DELETE", "/discovered/{instance_id}/{ticker}"):
        "candidate: drops a discovered ticker from an instance; not gated this round",
    ("POST", "/config/terminate-discover"):
        "candidate: kills the discovery process; not gated this round",
    ("POST", "/config/terminate-price"):
        "candidate: kills the price process; not gated this round",
    ("POST", "/digest/control"):
        "candidate: configures the scheduled digest; not gated this round",
    ("POST", "/digest/send-now"):
        "candidate: sends a digest to every configured channel; not gated this round",
    ("POST", "/learning/control"):
        "candidate: starts/stops the self-learning subsystem; not gated this round",
    ("POST", "/learning/purge"):
        "candidate: purges self-learning state; not gated this round",
    ("POST", "/learning/approvals/{approval_id}"):
        "candidate: approves or rejects a learned change; not gated this round",
    ("POST", "/learning/findings/{finding_id}/status"):
        "candidate: acknowledges a finding; not gated this round",
    ("POST", "/tickers"):
        "candidate: adds a globally tracked ticker; not gated this round",
    ("DELETE", "/tickers/{symbol}"):
        "candidate: removes a globally tracked ticker; not gated this round",
    ("DELETE", "/trends/{trend_id}"):
        "candidate: deletes a trend row; not gated this round",
    ("POST", "/trends/{trend_id}/end"):
        "candidate: ends a trend early; not gated this round",
}


def _mutating_routes():
    """(method, path, guard) for every non-GET route in the app."""
    from api import main

    out = []
    for route in main.app.routes:
        methods = set(getattr(route, "methods", ()) or ())
        for method in sorted(methods - {"GET", "HEAD", "OPTIONS"}):
            endpoint = getattr(route, "endpoint", None)
            guard = None
            if endpoint is not None:
                param = inspect.signature(endpoint).parameters.get("current_user")
                guard = getattr(getattr(param, "default", None), "dependency", None)
            out.append((method, route.path, guard))
    return out


def test_every_mutating_route_is_admin_gated_or_named_with_a_reason():
    """The inverse of the allowlist above: nothing may be unclassified."""
    from api.main import require_admin

    unclassified = [
        f"{m} {p}" for m, p, guard in _mutating_routes()
        if guard is not require_admin and (m, p) not in NOT_ADMIN_GATED
    ]
    assert not unclassified, (
        "these mutating routes are neither require_admin-gated nor listed in "
        "NOT_ADMIN_GATED with a reason:\n  " + "\n  ".join(sorted(unclassified))
    )


def test_the_exemption_list_has_no_dead_entries():
    """An entry that no longer names a live, ungated route is stale — it hides
    the next route that needs classifying."""
    from api.main import require_admin

    live = {(m, p) for m, p, guard in _mutating_routes() if guard is not require_admin}
    stale = sorted(NOT_ADMIN_GATED.keys() - live)
    assert not stale, f"exemptions that are gated or gone: {stale}"


def test_every_exemption_carries_a_real_reason():
    thin = {k: v for k, v in NOT_ADMIN_GATED.items()
            if not isinstance(v, str) or len(v.strip()) < 20}
    assert not thin, f"exemptions without a usable reason: {sorted(thin)}"


def test_the_three_routes_from_the_branch_review_are_now_gated():
    """I3: a live instance's runtime config, the broker start, and the
    credential migration were open to any authenticated user."""
    from api.main import require_admin

    for method, path in [
        ("PATCH", "/nexus/config/{instance_id}"),
        ("POST", "/config/start-broker"),
        ("POST", "/admin/credentials/migrate"),
    ]:
        assert _guard_of(method, path) is require_admin, f"{method} {path}"


def test_the_gated_and_exempt_sets_partition_every_mutating_route():
    from api.main import require_admin

    routes = _mutating_routes()
    gated = [r for r in routes if r[2] is require_admin]
    assert len(gated) + len(NOT_ADMIN_GATED) == len(routes)
    # Sanity on the measurement in the comment above: this round added three.
    assert len(gated) >= 29


# ---------------------------------------------------------------------------
# The operator-lockout proof, end to end (whole-branch review I4).
#
# The tests above call the guard with a hand-built dict. That proves the guard
# accepts an admin; it does NOT prove the operator can get a token that
# survives login -> token_version claim -> get_current_user -> require_admin.
# Those are the four places round 1 changed, so the proof has to run through
# all four.
# ---------------------------------------------------------------------------

OPERATOR = "operator"
OPERATOR_PASSWORD = "a-sufficiently-long-password"
ADMIN_ROUTE = "/instances/eb/stop"


@pytest.fixture
def live_api(store, monkeypatch):
    """The real app over the test store, with the DB-touching bits caged.

    Only auth is exercised: the admin-gated action is stubbed so the assertion
    is about the status code the AUTHORIZATION produced, not about whether an
    instance exists.
    """
    from fastapi.testclient import TestClient
    import auth_utils
    from api import main

    monkeypatch.setenv("JWT_SECRET", "test-signing-secret-for-the-round-trip")
    monkeypatch.delenv("JWT_EXPIRE_HOURS", raising=False)
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", OPERATOR)
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", OPERATOR_PASSWORD)
    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setattr(main, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setattr(main, "action_stop_instance",
                        lambda conn, instance_id: {"stopped": True, "id": instance_id})
    auth_utils.reset_login_rate_limit()

    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        with TestClient(main.app) as client:
            yield client, auth_utils
    finally:
        main.app.dependency_overrides.pop(main.conn_dependency, None)
        auth_utils.reset_login_rate_limit()


def _login(client, username, password):
    return client.post("/auth/login", json={"username": username, "password": password})


def test_the_operator_can_log_in_and_use_an_admin_route_end_to_end(live_api):
    """login -> token (with token_version) -> get_current_user -> require_admin."""
    client, auth_utils = live_api
    auth_utils.ensure_default_admin(None)

    res = _login(client, OPERATOR, OPERATOR_PASSWORD)
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    assert res.json()["user"]["role"] == "admin"

    got = client.post(ADMIN_ROUTE, headers={"Authorization": f"Bearer {token}"})
    assert got.status_code not in (401, 403), got.text
    assert got.status_code == 200


def test_a_non_admin_token_is_refused_by_the_same_route(live_api):
    client, auth_utils = live_api
    auth_utils.ensure_default_admin(None)
    auth_utils.create_user(None, "bob", "bobs-long-password", role="user")

    res = _login(client, "bob", "bobs-long-password")
    assert res.status_code == 200, res.text
    got = client.post(ADMIN_ROUTE,
                      headers={"Authorization": f"Bearer {res.json()['access_token']}"})
    assert got.status_code == 403


def test_a_password_change_kills_the_old_token_and_a_fresh_login_works(live_api):
    """The revocation from round 3, proven through the HTTP surface."""
    client, auth_utils = live_api
    auth_utils.ensure_default_admin(None)

    old_token = _login(client, OPERATOR, OPERATOR_PASSWORD).json()["access_token"]
    old_auth = {"Authorization": f"Bearer {old_token}"}
    assert client.post(ADMIN_ROUTE, headers=old_auth).status_code == 200

    row = auth_utils.get_user_by_username(None, OPERATOR)
    changed = client.put(
        f"/auth/users/{row['id']}",
        headers=old_auth,
        json={"password": "an-even-longer-new-password"},
    )
    assert changed.status_code == 200, changed.text

    # The token minted against the old password is dead...
    assert client.post(ADMIN_ROUTE, headers=old_auth).status_code == 401
    # ...and the operator is not locked out: the new password works.
    again = _login(client, OPERATOR, "an-even-longer-new-password")
    assert again.status_code == 200, again.text
    fresh = {"Authorization": f"Bearer {again.json()['access_token']}"}
    assert client.post(ADMIN_ROUTE, headers=fresh).status_code == 200


def test_the_old_password_no_longer_logs_in(live_api):
    client, auth_utils = live_api
    auth_utils.ensure_default_admin(None)
    row = auth_utils.get_user_by_username(None, OPERATOR)
    auth_utils.update_user(None, row["id"], password="an-even-longer-new-password")

    assert _login(client, OPERATOR, OPERATOR_PASSWORD).status_code == 401


# --- in-band recovery: the operator row that lost its role ----------------


def test_ensure_default_admin_promotes_the_operator_back_to_admin(store, monkeypatch, capsys):
    """Round 1 made role load-bearing. If the DEFAULT_ADMIN_USERNAME row is
    role 'user' — demoted by the old flat-authorization free-for-all, or
    created as a plain user before it was named the default admin —
    ensure_default_admin used to return early and leave it that way, and with
    every admin route now gated there was no in-band way back in."""
    import auth_utils

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", OPERATOR)
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", OPERATOR_PASSWORD)

    auth_utils.create_user(None, OPERATOR, OPERATOR_PASSWORD, role="user")
    assert auth_utils.get_user_by_username(None, OPERATOR)["role"] == "user"

    auth_utils.ensure_default_admin(None)

    assert auth_utils.get_user_by_username(None, OPERATOR)["role"] == "admin"
    logged = capsys.readouterr()
    assert OPERATOR in (logged.err + logged.out)
    assert "admin" in (logged.err + logged.out)


def test_the_promotion_does_not_touch_the_password_or_the_token_version(store, monkeypatch):
    """Repair the role, nothing else: a promotion is not a password reset, and
    it must not log the operator's other sessions out."""
    import auth_utils

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", OPERATOR)
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "a-completely-different-password")

    auth_utils.create_user(None, OPERATOR, OPERATOR_PASSWORD, role="user")
    before = auth_utils.get_user_by_username(None, OPERATOR)

    auth_utils.ensure_default_admin(None)

    after = auth_utils.get_user_by_username(None, OPERATOR)
    assert after["password_hash"] == before["password_hash"]
    assert auth_utils.token_version_of(after) == auth_utils.token_version_of(before)
    assert auth_utils.verify_password(OPERATOR_PASSWORD, after["password_hash"])


def test_an_admin_operator_row_is_left_completely_alone(store, monkeypatch, capsys):
    import auth_utils

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", OPERATOR)
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", OPERATOR_PASSWORD)
    auth_utils.create_user(None, OPERATOR, OPERATOR_PASSWORD, role="admin")
    before = auth_utils.get_user_by_username(None, OPERATOR)
    capsys.readouterr()

    auth_utils.ensure_default_admin(None)

    assert auth_utils.get_user_by_username(None, OPERATOR) == before
    assert "promot" not in (capsys.readouterr().err.lower())


def test_a_non_operator_user_is_never_promoted(store, monkeypatch):
    """Only the DEFAULT_ADMIN_USERNAME row is repairable this way."""
    import auth_utils

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", OPERATOR)
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", OPERATOR_PASSWORD)
    auth_utils.create_user(None, OPERATOR, OPERATOR_PASSWORD, role="admin")
    auth_utils.create_user(None, "bob", "bobs-long-password", role="user")

    auth_utils.ensure_default_admin(None)

    assert auth_utils.get_user_by_username(None, "bob")["role"] == "user"


def test_the_promotion_takes_effect_on_a_token_the_user_already_holds(live_api):
    """The role is read from the ROW on every request, not from the token, so
    recovery does not require the operator to log in again."""
    client, auth_utils = live_api
    # The app's startup provisioned the operator; demote it the way the old
    # flat-authorization API allowed any account to.
    row = auth_utils.get_user_by_username(None, OPERATOR)
    auth_utils.update_user(None, row["id"], role="user")

    token = _login(client, OPERATOR, OPERATOR_PASSWORD).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    assert client.post(ADMIN_ROUTE, headers=auth).status_code == 403

    auth_utils.ensure_default_admin(None)

    assert client.post(ADMIN_ROUTE, headers=auth).status_code == 200
