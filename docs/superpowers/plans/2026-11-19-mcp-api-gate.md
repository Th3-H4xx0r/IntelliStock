# IntelliStock MCP — API Gate (build C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every IntelliStock API route declare its MCP policy on its own decorator, and add a second, MCP-only listener (:8012). Its gate authenticates the MCP service and refuses `never` routes. It holds live-money and irreversible calls until a Face ID-signed approval arrives, then executes each approved call exactly once, in-process. It enforces idempotency, caps and audit, and nothing a human JWT caller on :8011 sees changes.

**Architecture:** One FastAPI app is served by two uvicorn servers in one process. A pure ASGI middleware (`McpGateMiddleware`) passes public-listener traffic through untouched. On :8012 it is the whole gate: service auth (listener + CIDR + token + kill switch), MCP principal, route policy from `openapi_extra["x-mcp"]`, idempotency, caps, money-state resolution, approvals and audit. `get_current_user` gains one additive branch, which returns the principal the middleware placed in the ASGI scope. Approvals are decided on the public listener with an ECDSA P-256 signature from the phone's Secure Enclave key. The approved request is then replayed through the app in-process, under a scope marker that only in-process code can set.

**Tech Stack:** Python 3.11, FastAPI / Starlette / uvicorn (unpinned; local 0.136 / 1.0 / 0.47), anyio, cryptography (ECDSA P-256), Postgres 17 + JSONB through `backend/db` (FakeStore in tests), pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-intellistock-mcp-server-design.md` (§5.1, §6; §2 anti-goals) and the binding interface contract `docs/superpowers/specs/2026-11-19-intellistock-mcp-contract.md` (§1–§7). Executors read both.

**Branch / base:** `feat/mcp-api-gate` in worktree `/Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock/.worktrees/mcp-api-gate`, stacked on `fix/api-hardening-mcp-prereqs` (plan `docs/superpowers/plans/2026-11-19-mcp-api-hardening.md`), which is itself cut from origin/main 4420fea6 (wheel-regrade PR #188 merged: 255 `@app` routes + 6 `app_updates` router routes = 261; 159 tables).

## Global Constraints

- Python floor 3.11 (CI runs 3.11, local is 3.14): no PEP 695 syntax (`type X = ...`, `def f[T]`), no `itertools.batched`, and no 3.12 f-strings that reuse the outer quote. Every new module starts with `from __future__ import annotations`. Syntax gate: `~/.local/bin/python3.11 -m compileall -q <files>` (the path the hardening plan uses).
- Test command, from the repo root: `python -m pytest -q -p no:cacheprovider backend/tests`. Tests never touch the network, production, brokers, APNs, Discord or an LLM. They run on the `store` fixture (FakeStore unless `PG_TEST_DSN` is set) and patch `apns_sender.send_to_user` and `notifications.notify`.
- Datastore (CLAUDE.md): all DB access goes through `from db import store` (`get / run / filter / insert / update / replace_if / delete / count / sql`), and no module opens a connection. DDL lives only in `backend/db/schema.py`: no `CREATE INDEX` at a call site, and never SQL `BETWEEN`. Timestamps are fixed-width ISO strings `%Y-%m-%dT%H:%M:%S.%fZ`, so bytewise `COLLATE "C"` order is chronological. Every new module binds `store` at module level, so a test can `monkeypatch.setattr(module, "store", store)`.
- Human (JWT) callers see no behaviour change except the two the contract mandates: `GET /health` gains `"mcp_schema_hash"`, and a request bearing `MCP_SERVICE_TOKEN` on the public listener gets 401. Real money trades through this API (instance `alpaca-main`).
- The contract is binding. Task 17 writes this plan's additive changes back into the contract file, in the same PR.
- Defaults (contract §7): `MCP_ENABLED` false; `MCP_API_PORT` 8012; `MCP_ALLOWED_CIDR` 172.31.250.0/24; `MCP_SERVICE_TOKEN` at least 32 bytes; `MCP_PROTECTED_INSTANCES` f14-paper,swing-paper; `MCP_PROTECTED_ACCOUNTS` PA37JPXGU196; `MCP_APPROVAL_TTL_S` 900; `MCP_CAP_COST_PER_DAY` 20, `MCP_CAP_APPROVALS_PER_DAY` 50, `MCP_CAP_CALLS_PER_MIN` 60, `MCP_CAP_WRITES_PER_MIN` 10, `MCP_CAP_CONCURRENT_BACKTESTS` 1.
- Fixed values (spec/contract): device activation delay 600 s; decision `ts` skew 60 s; idempotency TTL 86400 s; approval result cap 262144 bytes. Idempotency-Key must match `^[A-Za-z0-9._:-]{1,128}$`. Every hash in this plan (body_sha256, args, schema) is the sha256 hex of `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`.
- No new dependencies. `cryptography>=41` is already in `backend/requirements.txt`, and anyio comes with Starlette. Do not edit `docker-compose.yml`: build D owns `mcp_net` and the mcp service, and the api reads `MCP_*` from `.env` through its existing `env_file`.
- Secrets: the service token, Authorization headers and secret-named request fields never reach a log line or an McpAudit row.
- Never import `api.main` from a new module. `python api/main.py` runs it as `__main__`, so an import would build a second app. Routers take the user dependency as an argument (the `app_updates.build_router` pattern), and the replay reaches the app through `request.app`.
- GitNexus (CLAUDE.md):
  - Before editing an existing function, run `mcp__gitnexus__impact({target: "<name>", direction: "upstream"})` and report its direct callers, affected processes and risk.
  - When it returns UNKNOWN (usual for FastAPI-wired handlers), grep the callers instead: `/usr/bin/grep -rn "<name>(" backend`.
  - On HIGH or CRITICAL, write the warning into the task report, then proceed only under the tests that task names. The operator is asleep and pre-approved autonomous work.
  - Before every commit, run `mcp__gitnexus__detect_changes()` and confirm that only the task's symbols changed.
- Commits: conventional style; the message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push; the operator merges.

## Review Focus

1. **Routes that never call `get_current_user`.** Requests on :8012 to `/help`, `/health`, `/kalshi/img`, `/auth/login` or `/ota/*` must still be authenticated (401) and classified (never → 403). Pinned by Task 11 `test_gate_covers_routes_without_get_current_user`.
2. **Missing or unknown money target.** A money call whose target field is absent, or names an unknown id, fails closed: 202 with `money_state` "unknown", and the handler does not run. Pinned by Task 2's parametrized cases and Task 11 `test_money_with_unknown_target_waits_for_approval`.
3. **Duplicate Idempotency-Key in flight.** The same key, retried while the first call is still running, gets 409 "still in progress", and the route runs once. Pinned by Task 14 `test_concurrent_duplicate_key_is_409_and_runs_once`.
4. **Failed replay.** When an approved request's replay fails (the route raises, or answers 4xx/5xx), the approval ends as `failed` with `result_status` recorded, and it can never execute again. Pinned by Task 13 `test_failed_replay_is_terminal`.
5. **Percent-encoded path params.** A path param that needs percent-encoding (instance id `my bot`) survives storage and replays to the same route with the same value. Pinned by Task 13 `test_replay_preserves_percent_encoded_path`.

---

## Decisions made without the operator (review these)

1. **The gate is a pure ASGI middleware, not a FastAPI dependency.** Only a middleware covers routes that never resolve `get_current_user`, and only a middleware can buffer the body and capture the response for idempotency. On the public listener it calls the inner app with the identical scope, receive and send objects.
2. **Money state is computed in `mcp_policy`, not by calling `interactive_utils.action_learning_targets`.** That function has two latent bugs that make it unusable as a gate:
   - (a) Its local `from self_learning import store` shadows the module-level `store`, and `self_learning.store` has no `run`. So `store.run(BROKERAGE_ACCOUNTS_TABLE)` raises an AttributeError that a bare `except` swallows. The brokerage map is therefore always empty, and every linked equity instance reads "unknown" (verified: `hasattr(self_learning.store, "run")` is False).
   - (b) Its Kalshi branch reads `kalshi_environment / kalshi_live_enabled / kalshi_paper_mode`, which `action_instances` never returns. Every Kalshi instance therefore reads "paper", which fails open.

   The plan reuses the function's rules verbatim, over direct store reads: alpaca_paper's three states, "none" when unlinked, a strategy as risky as its riskiest instance, and `kalshi.mode.is_real_mode`. The function itself stays untouched, as instructed. Both bugs mislabel the Learning tab today and deserve a separate fix.
3. **A Kalshi demo environment counts as paper.** `is_real_mode` calls demo "real" because its orders execute, but they execute on fake money.
4. **Escalators.** On any money route, either of these raises the money state:
   - a body `brokerage_id` that names a live or unknown brokerage, which closes "repoint a paper instance at the live account";
   - a body `paper_mode: false`, which closes "flip a Kalshi bot to real orders".
5. **Class `irreversible` (always needs approval) where the contract's target kinds cannot express the risk:**
   - `POST /models`, `PUT /models/{id}` and `DELETE /models/{id}`: lead ruling. `PUT /models` keeps the saved key when only the base URL changes, so an agent could re-point a saved LLM key, and a live strategy's trade-deciding LLM, at any host.
   - `POST /config/start-broker` and `POST /config/terminate-price`: the global price/broker engine has no target.
   - `POST /trends/{id}/end`: no instance in the path or body.
   - `POST /nexus/rebuild`: the destructive flag is per body, so the whole route is gated.
   - `PUT /notification-preferences`: muting alerts would hide agent activity.
   - `POST /brokerages/ensure-ai-alpaca`: it creates a brokerage.
   - Every DELETE, per spec, including cancel-order and remove-stock.

   `PUT /strategies/{id}` is `money` with target `{"strategy": "strategy_id"}` (lead ruling), so documents used by live or protected instances need approval.
6. **Edges of the never class.** `GET /nexus/cache` is `never` (docker exec; inventory tier A). `GET /app-updates/latest` and `GET /app-updates/install-status` are `read`. `/ota/*`, the session-less installer links, are `never`.
7. **The three wheel-regrade routes,** classified as the lead gave them: regrade = write, POST wheel/scan = write, GET wheel/scan = read. I added `cost: true` to the two writes because each runs the wheel's LLM scoring.
8. **Execution outcome.**
   - `executed` when the replayed route answers 2xx. Swing's 202 "uncertain" counts as executed, and its body says uncertain.
   - `failed` for any other answer.
   - An approval whose execution was claimed but has no result after `MCP_APPROVAL_EXEC_STALE_S` (600 s) reads `failed`, with `{"detail": "mcp: execution outcome unknown (the API restarted mid-execution); check orders"}`.
9. **Secrets at rest.** On every terminal transition (denied, expired, executed, failed), secret-named body fields in the McpApprovals row become `"[redacted]"`; `body_sha256` is kept. A pending row is never altered, so while a decision is possible its Approval object always re-hashes to `body_sha256`. The iOS client recomputes the hash before signing.
10. **Idempotency stores every completed response,** including 4xx, 5xx and the 202 pending-approval body. A route that raises releases the key, so a retry can run.
11. **Audit.** One row is written for every principal call, including refusals and 401s, and for verify-password and audit-event. The MCP server's 60-second `/health` and `openapi.json` polls are not audited. Audit writes are best-effort: a failure is logged and never fails the call.
12. **Backtest concurrency cap.** It counts BacktestProgress rows whose status is running, queued, pending or paused*. It applies to `create_backtest` only, and it fails closed: a store error gives 429.
13. **Who decides.** Only the user an approval was created for can decide it; anyone else gets 404. The replay runs as that user's MCP principal.
14. **400 vs 422.** Decision and device bodies are taken as JSON objects and validated in code, so bad values return 400, as the contract says. A body that is not a JSON object still gets FastAPI's 422.
15. **The :8012 listener.** It starts only when `MCP_ENABLED` is true and the token is at least 32 bytes. It never starts on the public port: that misconfiguration logs an ERROR and starts the public listener only. The second server runs with `lifespan="off"`, so startup hooks run once. On :8012, `GET /health` and `/internal/mcp/*` need the service token but no acting user (contract §4).
16. **Additive contract changes** (written back in Task 17):
    - 413 body too large;
    - 415 non-JSON body;
    - 409 "still in progress";
    - 404 for an unknown or foreign approval id;
    - 400 for a `body_sha256` that does not match;
    - 401 from the internal endpoints when MCP is disabled;
    - env knobs `MCP_MAX_BODY_BYTES` (1048576) and `MCP_APPROVAL_EXEC_STALE_S` (600).
17. **Direct tools.** There are 23: the 21 the spec lists, plus `list_pending_approvals` and `get_approval`. A route's tool name equals its operation_id. Every other operation_id is the handler name minus its `api_` prefix; all are unique, and the longest is 33 characters.
18. **The daily cost cap is global** across all MCP clients ("20 cost-route calls per day in total"). The per-minute windows live in memory, because the api is one process. The daily counts re-seed from McpAudit and McpApprovals at the first call of each UTC day, so a restart does not reset them.

## Execution preconditions (before Task 1)

1. **Base.** In the worktree, run `git fetch origin`, then check out the base:
   - If `fix/api-hardening-mcp-prereqs` exists: `git checkout -b feat/mcp-api-gate fix/api-hardening-mcp-prereqs`.
   - Otherwise: branch from `origin/main` (4420fea6) and rebase onto the prereq branch once it lands. Only Task 17 needs it.
2. **Prereq interface.** Confirm that `backend/api/server_config.py` provides `build_uvicorn_config(app, *, host, port, proxy_headers) -> uvicorn.Config` and `run_server(config) -> None`, as specified in the hardening plan's Task 6.
3. **Baseline.** Run the test command once and record any pre-existing failures, so they are not blamed on this branch.
4. **Index.** Read `gitnexus://repo/IntelliStock/context`. If it reports a stale index, run `GITNEXUS_MAX_FILE_SIZE=2048 npx gitnexus analyze`.

## File structure

| File | Responsibility |
|---|---|
| `backend/mcp_policy.py` (new) | `mcp_policy()` helper and validation; `McpConfig`; canonical hashing; UTC helpers; money-state resolution; `approval_reason()` |
| `backend/mcp_audit.py` (new) | McpAudit writer, secret redaction, argument summary |
| `backend/mcp_approvals.py` (new) | Approval rows (create, list, get, lazy expiry, CAS transitions, result); summaries; push; devices; signature check; `decide()` |
| `backend/mcp_idempotency.py` (new) | McpIdempotency lookup, claim, finalize, release |
| `backend/mcp_caps.py` (new) | Per-minute windows, daily cost and approval caps, backtest slot |
| `backend/api/mcp_gate.py` (new) | `McpGateMiddleware`, scope keys, route-policy lookup, service auth, `principal_from_scope`, `require_mcp_service` |
| `backend/api/mcp_replay.py` (new) | `replay_approved_request()`, the in-process ASGI replay |
| `backend/api/mcp_routes.py` (new) | `build_router(current_user_dependency)`: approvals, devices, decision, internal endpoints, internal OpenAPI and schema hash |
| `backend/api/server_config.py` (prereq, modified) | `build_mcp_uvicorn_config`, `serve()` for both listeners |
| `backend/api/main.py` (modified) | x-mcp and operation_id on all 255 routes; docstrings on exposed routes; `get_current_user` branch; middleware; router include; /health hash; `__main__` |
| `backend/api/app_updates.py` (modified) | x-mcp and operation_id on its 6 routes |
| `backend/db/schema.py` (modified) | McpApprovals, McpApprovalDevices, McpIdempotency, McpAudit |
| `backend/tests/dbcore/test_schema_ensure.py` (modified) | Table count 147 → 151 (+12 lazily created = 163) |
| `backend/tests/mcp_helpers.py` (new) | Shared harness: env, store patching, clients, probe app, keys |
| `backend/tests/test_mcp_*.py` (new) | One file per task deliverable |
| contract (modified, Task 17) | Additive entries (Decision 16) |


---

### Task 1: Policy helper, config and canonical hashing

**Files:**
- Create: `backend/mcp_policy.py`
- Create: `backend/tests/mcp_helpers.py` (env helper only, for now)
- Test: `backend/tests/test_mcp_policy.py`

**Interfaces:**
- Consumes: nothing.
- Produces (`mcp_policy`):
  - `POLICY_CLASSES = ("read", "write", "money", "irreversible", "never")`, `NON_READ_CLASSES = frozenset({"write", "money", "irreversible"})`, `TARGET_KINDS = ("instance", "brokerage", "strategy")`, and `NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")`.
  - `mcp_policy(cls: str, *, tool: str | None = None, target: dict | None = None, cost: bool = False) -> dict` returns `{"x-mcp": {...}}` containing only the keys that are set. It raises ValueError on any violation of `validate_policy`'s rules.
  - `validate_policy(raw: object) -> dict | None` returns a normalized copy, or None when invalid. The rules:
    - only the keys class, tool, target and cost;
    - class is in POLICY_CLASSES;
    - tool matches NAME_RE;
    - target is a one-key dict with its key in TARGET_KINDS and a NAME_RE-shaped value, present if and only if class is "money";
    - cost is exactly True, and only on write, money or irreversible.
  - `@dataclass(frozen=True) class McpConfig`, with these fields:
    - enabled: bool, service_token: str | None, port: int;
    - allowed_cidr: ipaddress network | None;
    - protected_instances: frozenset[str], protected_accounts: frozenset[str];
    - approval_ttl_s, cap_cost_per_day, cap_approvals_per_day, cap_calls_per_min, cap_writes_per_min, cap_concurrent_backtests, max_body_bytes, approval_exec_stale_s: int.

    It also has a property `usable -> bool` (enabled, a token, and a CIDR) and a classmethod `from_env(environ: Mapping[str, str] | None = None) -> McpConfig`.
  - `canonical_request(method: str, path: str, query: Mapping[str, object], body: object) -> dict` returns `{"method": method.upper(), "path", "query": dict(query), "body"}`. `canonical_json(obj) -> bytes` applies the Global Constraints formula, and `canonical_request_sha256(method, path, query, body) -> str` hashes the canonical request.
  - `utc_now() -> datetime` (tz-aware), `iso_utc(dt: datetime) -> str` (`%Y-%m-%dT%H:%M:%S.%fZ`), `parse_iso_utc(s: str) -> datetime`.
- Produces (`backend/tests/mcp_helpers.py`): `MCP_TOKEN = "t" * 40`, `mcp_env(monkeypatch, **overrides) -> None`. It sets `MCP_ENABLED=true`, `MCP_SERVICE_TOKEN=MCP_TOKEN` and `MCP_API_PORT=8012`, plus overrides given as `MCP_X=value` keyword arguments.

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_mcp_policy.py`)

~~~python
def test_mcp_policy_builds_openapi_extra():
    assert mcp_policy("money", tool="live_command", target={"instance": "instance_id"}) == {
        "x-mcp": {"class": "money", "tool": "live_command", "target": {"instance": "instance_id"}}}
    assert mcp_policy("read") == {"x-mcp": {"class": "read"}}
    assert mcp_policy("write", cost=True) == {"x-mcp": {"class": "write", "cost": True}}

@pytest.mark.parametrize("args,kwargs", [
    (("delete",), {}),
    (("money",), {}),                                              # money needs a target
    (("money",), {"target": {"instance": "a", "brokerage": "b"}}),
    (("money",), {"target": {"model": "model_id"}}),
    (("read",), {"target": {"instance": "instance_id"}}),          # target only on money
    (("read",), {"tool": "Bad-Name"}),
    (("read",), {"tool": "x" * 65}),
    (("read",), {"cost": True}),
    (("never",), {"cost": True}),
])
def test_mcp_policy_rejects_invalid(args, kwargs):
    with pytest.raises(ValueError):
        mcp_policy(*args, **kwargs)

def test_validate_policy():
    assert validate_policy(None) is None
    assert validate_policy({"class": "nope"}) is None
    assert validate_policy({"class": "read", "extra": 1}) is None
    assert validate_policy({"class": "write", "cost": False}) is None
    assert validate_policy({"class": "read"}) == {"class": "read"}

def test_config_defaults():
    cfg = McpConfig.from_env({})
    assert cfg.enabled is False and cfg.service_token is None and cfg.port == 8012
    assert str(cfg.allowed_cidr) == "172.31.250.0/24"
    assert cfg.protected_instances == frozenset({"f14-paper", "swing-paper"})
    assert cfg.protected_accounts == frozenset({"PA37JPXGU196"})
    assert (cfg.approval_ttl_s, cfg.cap_cost_per_day, cfg.cap_approvals_per_day, cfg.cap_calls_per_min,
            cfg.cap_writes_per_min, cfg.cap_concurrent_backtests) == (900, 20, 50, 60, 10, 1)
    assert (cfg.max_body_bytes, cfg.approval_exec_stale_s) == (1048576, 600)
    assert cfg.usable is False

@pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("YES", True),
                                          ("false", False), ("", False), ("on", False)])
def test_config_enabled_parsing(raw, expected):
    assert McpConfig.from_env({"MCP_ENABLED": raw}).enabled is expected

def test_short_token_is_unset():
    assert McpConfig.from_env({"MCP_ENABLED": "true", "MCP_SERVICE_TOKEN": "x" * 31}).usable is False
    assert McpConfig.from_env({"MCP_ENABLED": "true", "MCP_SERVICE_TOKEN": "x" * 32}).usable is True

def test_bad_cidr_disables():
    env = {"MCP_ENABLED": "true", "MCP_SERVICE_TOKEN": "x" * 32, "MCP_ALLOWED_CIDR": "nonsense"}
    assert McpConfig.from_env(env).usable is False

def test_bad_ints_fall_back_and_zero_is_allowed():
    assert McpConfig.from_env({"MCP_CAP_COST_PER_DAY": "abc"}).cap_cost_per_day == 20
    assert McpConfig.from_env({"MCP_CAP_COST_PER_DAY": "-1"}).cap_cost_per_day == 20
    assert McpConfig.from_env({"MCP_CAP_COST_PER_DAY": "0"}).cap_cost_per_day == 0

def test_protected_accounts_are_normalised():
    assert McpConfig.from_env({"MCP_PROTECTED_ACCOUNTS": " pa37jpxgu196 , PA3X ,"}).protected_accounts == \
        frozenset({"PA37JPXGU196", "PA3X"})

def test_canonical_request_sha256_follows_the_contract():
    body = {"b": "é", "a": 1}
    expected = hashlib.sha256(json.dumps(
        {"method": "POST", "path": "/x", "query": {"q": "1"}, "body": body},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    assert canonical_request_sha256("post", "/x", {"q": "1"}, body) == expected

def test_iso_utc_round_trip():
    dt = datetime(2026, 11, 19, 7, 0, tzinfo=timezone.utc)
    assert iso_utc(dt) == "2026-11-19T07:00:00.000000Z"
    assert parse_iso_utc(iso_utc(dt)) == dt
~~~

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_mcp_policy.py`
Expected: collection error, `ModuleNotFoundError: No module named 'mcp_policy'`.

- [ ] **Step 3: Implement `backend/mcp_policy.py` per Interfaces.** `from_env` defaults to `os.environ`. MCP_ENABLED is true for `1/true/yes` (case-insensitive). Ints use a `_env_int(env, name, default)` helper that accepts integers ≥ 0 and otherwise returns the default. The CIDR is `ipaddress.ip_network(value, strict=False)`, and None on ValueError. `mcp_policy` builds the dict, then raises `ValueError(f"invalid x-mcp: {raw!r}")` when `validate_policy` returns None.

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `python -m pytest -q -p no:cacheprovider backend/tests/test_mcp_policy.py` → all pass.
Run: `~/.local/bin/python3.11 -m compileall -q backend/mcp_policy.py` → exit 0.

- [ ] **Step 5: Commit** (new files only; run `mcp__gitnexus__detect_changes()` first)

~~~bash
git add backend/mcp_policy.py backend/tests/mcp_helpers.py backend/tests/test_mcp_policy.py
git commit -m "feat(mcp): x-mcp policy helper, config and canonical hashing" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
~~~

---

### Task 2: Money-state resolution

The rules are those of `action_learning_targets` (Decision 2), read straight from the store.

**Files:**
- Modify: `backend/mcp_policy.py`
- Modify: `backend/tests/mcp_helpers.py` (`use_store`, `seed_money_fixture`)
- Test: `backend/tests/test_mcp_money_state.py`

**Interfaces:**
- Consumes: Task 1 (`McpConfig`, `validate_policy` output shape).
- Produces (`mcp_policy`):
  - `brokerage_flag_money(row: dict | None) -> str`:
    - `alpaca_paper` True → "paper", False → "live";
    - else `kalshi_environment` "demo" → "paper", "live"/"prod" → "live";
    - else (or a missing row) → "unknown".
  - `instance_money(inst: dict, brokerages: Mapping[str, dict | None]) -> str`:
    - no `brokerage_id` → "none";
    - else the flag money of its brokerage, when that is not "unknown" and the instance is not Kalshi;
    - for `kind == "kalshi"`: env demo → "paper", and env live/prod → `"live" if kalshi.mode.is_real_mode(env, cfg.live_enabled, cfg.paper_mode) else "paper"`, where `cfg = inst.get("kalshi_config") or {}`;
    - otherwise → "unknown".
  - `resolve_money_state(policy: dict, path_params: Mapping[str, str], body: object, cfg: McpConfig) -> str` returns one of "live", "unknown", "protected", "paper" or "none". Combination order for the monetary part: live > unknown > paper > none. "protected" replaces paper or none only.
  - `approval_reason(policy: dict, money_state: str | None) -> str | None`:
    - irreversible → "irreversible";
    - money → money_state, when it is in {"live", "unknown", "protected"};
    - else None.
- Produces (`mcp_helpers`):
  - `MCP_STORE_MODULES` (tuple of module names: mcp_policy, mcp_audit, mcp_approvals, mcp_idempotency, mcp_caps, auth_utils, interactive_utils; later tasks append nothing).
  - `use_store(monkeypatch, store) -> None` patches `store` on every listed module that is importable.
  - `seed_money_fixture(store) -> None` (rows below).

`resolve_money_state` algorithm (the order matters):

~~~text
kind, field = the single target item; value = path_params.get(field) or (body.get(field) if body is a dict)
instances = store.run("Instances")   (one read); brokerage rows are fetched on demand with store.get, then cached
missing value          -> base = "none" if kind == "brokerage" else "unknown"
instance               -> row with str(id) == value ? instance_money(row) : "unknown"
brokerage              -> worst(brokerage_flag_money(row), instance_money of every instance whose brokerage_id == value)
strategy               -> store.get("Strategies", value) is None ? "unknown"
                          : worst(instance_money of instances whose str(strategy_id) == value), "none" if unused
escalate: body.brokerage_id (if present, and not the primary target field) -> worst(base, brokerage resolution of it)
          body.paper_mode is False -> worst(base, "live")
protected if: a touched instance id is in cfg.protected_instances, or a touched brokerage's normalized
              alpaca_account_number (strip/upper) is in cfg.protected_accounts
              (touched = the target instance, instances under a target brokerage or strategy, the escalating brokerage)
result: base if base in ("live", "unknown") else ("protected" if protected else base)
any exception anywhere -> "unknown"
~~~

- [ ] **Step 1: Write the failing tests.** `seed_money_fixture` inserts these rows.

  BrokerageAccounts:

  | id | fields |
  |---|---|
  | b-live | `alpaca_paper: False`, `alpaca_account_number: "LIVE123"` |
  | b-paper | `alpaca_paper: True`, `alpaca_account_number: "PA3IBY5S84PG"` |
  | b-f14 | `alpaca_paper: True`, `alpaca_account_number: "PA3TRLBV6ZNZ"` |
  | b-swing | `alpaca_paper: True`, `alpaca_account_number: " pa37jpxgu196 "` |
  | b-kalshi-live | `kalshi_environment: "live"` |
  | b-kalshi-demo | `kalshi_environment: "demo"` |
  | b-odd | no flags |

  Instances:

  | id | brokerage | strategy | other fields |
  |---|---|---|---|
  | alpaca-main | b-live | 200 | |
  | paper-1 | b-paper | 201 | |
  | f14-paper | b-f14 | 207 | |
  | swing-paper | b-swing | 202 | |
  | other-on-pa37 | b-swing | 204 | |
  | no-broker | none | 205 | |
  | kalshi-real | b-kalshi-live | | kalshi, `kalshi_config {"live_enabled": True, "paper_mode": False}` |
  | kalshi-dry | b-kalshi-live | | kalshi, `{"live_enabled": False, "paper_mode": True}` |
  | kalshi-demo | b-kalshi-demo | | kalshi |
  | odd-1 | b-odd | | |

  Strategies: ids 200, 201, 202, 204, 205, 207 and 299 (299 unused).

~~~python
P_INST = {"class": "money", "target": {"instance": "instance_id"}}
P_BROK = {"class": "money", "target": {"brokerage": "brokerage_id"}}
P_STRAT = {"class": "money", "target": {"strategy": "strategy_id"}}
CFG = McpConfig.from_env({})

@pytest.mark.parametrize("iid,expected", [
    ("alpaca-main", "live"), ("paper-1", "paper"), ("no-broker", "none"), ("missing", "unknown"),
    ("f14-paper", "protected"), ("swing-paper", "protected"), ("other-on-pa37", "protected"),
    ("kalshi-real", "live"), ("kalshi-dry", "paper"), ("kalshi-demo", "paper"), ("odd-1", "unknown")])
def test_instance_target(store, monkeypatch, iid, expected):
    use_store(monkeypatch, store); seed_money_fixture(store)
    assert resolve_money_state(P_INST, {"instance_id": iid}, None, CFG) == expected

@pytest.mark.parametrize("bid,expected", [
    ("b-live", "live"), ("b-paper", "paper"), ("b-f14", "protected"), ("b-swing", "protected"),
    ("b-kalshi-live", "live"), ("b-kalshi-demo", "paper"), ("b-odd", "unknown"), ("missing", "unknown")])
def test_brokerage_target(store, monkeypatch, bid, expected): ...   # P_BROK, {"brokerage_id": bid}

@pytest.mark.parametrize("sid,expected", [
    ("200", "live"), ("201", "paper"), ("207", "protected"), ("205", "none"), ("299", "none"), ("999", "unknown")])
def test_strategy_target(store, monkeypatch, sid, expected): ...    # P_STRAT, {"strategy_id": sid}

def test_body_field_target(store, monkeypatch):     # POST /instances shape
    p = {"class": "money", "target": {"brokerage": "brokerage_id"}}
    assert resolve_money_state(p, {}, {}, CFG) == "none"
    assert resolve_money_state(p, {}, {"brokerage_id": "b-live"}, CFG) == "live"
    assert resolve_money_state(p, {}, {"brokerage_id": "nope"}, CFG) == "unknown"

def test_missing_instance_field_fails_closed(store, monkeypatch):
    assert resolve_money_state(P_INST, {}, {}, CFG) == "unknown"

def test_escalators(store, monkeypatch):
    assert resolve_money_state(P_INST, {"instance_id": "paper-1"}, {"brokerage_id": "b-live"}, CFG) == "live"
    assert resolve_money_state(P_INST, {"instance_id": "kalshi-dry"}, {"paper_mode": False}, CFG) == "live"
    assert resolve_money_state(P_INST, {"instance_id": "kalshi-dry"}, {"paper_mode": True}, CFG) == "paper"

def test_protection_follows_config(store, monkeypatch):
    cfg = McpConfig.from_env({"MCP_PROTECTED_INSTANCES": "", "MCP_PROTECTED_ACCOUNTS": ""})
    assert resolve_money_state(P_INST, {"instance_id": "f14-paper"}, None, cfg) == "paper"

def test_store_failure_is_unknown(store, monkeypatch):
    monkeypatch.setattr(mcp_policy.store, "run", Mock(side_effect=RuntimeError("db down")))
    assert resolve_money_state(P_INST, {"instance_id": "paper-1"}, None, CFG) == "unknown"

@pytest.mark.parametrize("policy,state,expected", [
    ({"class": "irreversible"}, None, "irreversible"), (P_INST, "live", "live"),
    (P_INST, "unknown", "unknown"), (P_INST, "protected", "protected"),
    (P_INST, "paper", None), (P_INST, "none", None), ({"class": "write"}, None, None),
    ({"class": "read"}, None, None)])
def test_approval_reason(policy, state, expected):
    assert approval_reason(policy, state) == expected
~~~

- [ ] **Step 2: Run them to confirm they fail.** Run `python -m pytest -q -p no:cacheprovider backend/tests/test_mcp_money_state.py`. Expected: ImportError on `resolve_money_state`.
- [ ] **Step 3: Implement** per Interfaces and the algorithm. `store.get("Strategies", value)` needs an int id (Strategies is int-keyed), so a non-numeric value counts as missing ("unknown"). Wrap the whole resolution in `try/except Exception: return "unknown"`.
- [ ] **Step 4: Run them to confirm they pass.** Same command → all pass.
- [ ] **Step 5: Commit** (run detect_changes first): `feat(mcp): fail-closed money-state resolution with protected targets`, with the Co-Authored-By line.

---

### Task 3: MCP tables

**Files:**
- Modify: `backend/db/schema.py` (`ALL_TABLES` and `_SPECS`)
- Modify: `backend/tests/dbcore/test_schema_ensure.py`
- Test: `backend/tests/test_mcp_tables.py`

**Interfaces:**
- Produces the tables below (all ids are `uuid4().hex` unless stated; all timestamps use `iso_utc`). They are reached only through the modules of Tasks 8–15.

| table | TableSpec | document fields |
|---|---|---|
| McpApprovals | `indexed_fields=("status", "user_id", "created_at")` | id, created_at, expires_at, status, user_id, username, client, operation_id, method, path, query, body, body_sha256, summary, money_state, idempotency_key, correlation_id, result_status, result, decided_at, device_id, exec_claimed_at, executed_at |
| McpApprovalDevices | `indexed_fields=("user_id",)` | id (= device_id), user_id, name, public_key (base64, 65-byte point), created_at, active_at, revoked_at |
| McpIdempotency | `notify=False, indexed_fields=("created_at",), retention=RetentionSpec(field="created_at", days_env="RETAIN_MCP_IDEMPOTENCY_DAYS", default_days=1)` | id = sha256(client + "\x00" + key) hex, client, key, method, path, request_sha256, state ("in_progress" or "done"), status, content_type, body_text, too_large, created_at, completed_at |
| McpAudit | `notify=False, indexed_fields=("ts", "user_id", "operation_id"), retention=RetentionSpec(field="ts", days_env="RETAIN_MCP_AUDIT_DAYS", default_days=365)` | id, ts, client, user_id, username, operation_id, method, path, peer, args_sha256, args_summary, idempotency_key, outcome, status, latency_ms, approval_id, correlation_id, money_state, cap_bucket |

- [ ] **Step 1: Impact.** No function is edited: `ALL_TABLES` and `_SPECS` are module data. Grep the consumers of `len(schema.ALL_TABLES)`; only `tests/dbcore/test_schema_ensure.py` asserts a count.
- [ ] **Step 2: Write the failing tests** (`backend/tests/test_mcp_tables.py`)

~~~python
NAMES = ("McpApprovals", "McpApprovalDevices", "McpIdempotency", "McpAudit")

def test_mcp_tables_are_registered():
    for n in NAMES:
        assert n in schema.ALL_TABLES and n in schema.TABLES

def test_mcp_retention_and_indexes():
    a, i = schema.spec("McpAudit"), schema.spec("McpIdempotency")
    assert (a.retention.days_env, a.retention.default_days, a.retention.field) == ("RETAIN_MCP_AUDIT_DAYS", 365, "ts")
    assert (i.retention.days_env, i.retention.default_days, i.retention.field) == ("RETAIN_MCP_IDEMPOTENCY_DAYS", 1, "created_at")
    assert a.notify is False and i.notify is False
    assert schema.spec("McpApprovals").indexed_fields == ("status", "user_id", "created_at")
    assert "ts" in a.indexed_fields and "created_at" in i.indexed_fields

@requires_pg
def test_approval_status_cas_has_one_winner(store):
    store.insert("McpApprovals", {"id": "a1", "status": "pending"})
    when = P.field("status").eq("pending")
    results = run_in_two_threads(lambda: store.replace_if("McpApprovals", "a1", when=when,
                                                          doc={"id": "a1", "status": "approved"}))
    assert sum(r is not None for r in results) == 1
~~~

  In `test_schema_ensure.py`, change the count literal to `assert len(schema.ALL_TABLES) == 151 + len(_LAZILY_CREATED)` (147 → 151; total 163), and add a comment line: `# 2026-11-19: +4 for the MCP gate (McpApprovals, McpApprovalDevices, McpIdempotency, McpAudit).` `run_in_two_threads` is a small local helper: a `threading.Barrier(2)`, two threads, return both results.
- [ ] **Step 3: Run them to confirm they fail.** Run `python -m pytest -q -p no:cacheprovider backend/tests/test_mcp_tables.py backend/tests/dbcore/test_schema_ensure.py`. Expected: assertion failures (names missing, count 159).
- [ ] **Step 4: Implement.** Add the four names to `ALL_TABLES` between `"LiveState"` and `"MlNewsLLMPromptCache"` (bytewise order). Add the four `TableSpec`s, with a comment citing the contract §6, after the `AppSettings` / `LiveBootAudit` block.
- [ ] **Step 5: Run them to confirm they pass**, plus `backend/tests/test_pg_retention.py` (`configured_tables` now includes both MCP tables) → pass.
- [ ] **Step 6: Commit** (run detect_changes first): `feat(db): MCP approvals, devices, idempotency and audit tables`, with the Co-Authored-By line.


---

### Annotation tasks (4–7): the shared recipe

Each batch task annotates every route listed for it in Appendix A, following the same recipe:

- Add `operation_id="<operation_id>"` and `openapi_extra=mcp_policy(<class>, tool=..., target=..., cost=True)` to the decorator, setting only the kwargs the row fills. Keep `response_class=JSONResponse` and every other existing kwarg. Import once at the top of `main.py` and `app_updates.py`: `from mcp_policy import mcp_policy` (`backend/` is on sys.path in production and tests).
- Rows marked **+doc** are exposed routes without a docstring. Add a one-line docstring of at most 100 characters that says what the route returns or does; for money and irreversible routes, also name the hazard (for example "Can place a LIVE order."). Never edit route bodies.
- The batch's money and irreversible handlers need impact analysis first. Decorator-only edits do not change call semantics. Expect UNKNOWN or LOW, and grep callers. The only handler in the app with an in-code caller is `api_brokerage_positions` (called by `api_move_analysis_portfolio`; batch 4).
- The test module `backend/tests/test_mcp_route_policy.py` grows with each batch through its `ANNOTATED_PREFIXES` tuple; Task 7 replaces the prefix filter with "every route".

---

### Task 4: Annotate batch 1 + the route-policy test module

**Files:**
- Modify: `backend/api/main.py` (batch-1 decorators: `/health` through `/history`, `/codex/*`, `/claude/*`, `/admin/*`, `/ollama|bedrock|openrouter/*`, `/llm/*`, `/llm-usage/*`, `/kalshi/img`)
- Modify: `backend/api/app_updates.py` (the 6 decorators inside `build_router`)
- Test: `backend/tests/test_mcp_route_policy.py`

**Interfaces:**
- Consumes: `mcp_policy`, `validate_policy`, `NAME_RE` (Task 1).
- Produces (test module): the helpers `_api_routes() -> list[APIRoute]` (from `main.app.routes`), `_policy(route) -> dict | None`, and the constant `EXPECTED_DIRECT_TOOLS` (frozenset of the 21 spec tools; Task 10 adds 2).

- [ ] **Step 1: Impact.** Run impact (upstream) on `api_chatbot_delete`, `api_chatbot_clear`, `api_put_notification_prefs`, `api_remove_ticker` and on `build_router` (app_updates); grep their callers. Report the results.
- [ ] **Step 2: Write the failing tests**

~~~python
ANNOTATED_PREFIXES = ("/health", "/help", "/auth/", "/onboarding/", "/chatbot/", "/status", "/llm/",
    "/llm-usage/", "/benzinga/", "/notification-preferences", "/push/", "/notifications/", "/tickers",
    "/prices", "/history", "/app-updates/", "/ota/", "/codex/", "/claude/", "/admin/", "/ollama/",
    "/bedrock/", "/openrouter/", "/kalshi/img")
PENDING = ()          # path prefixes inside an annotated prefix that a later batch owns

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

def test_money_targets_resolve():
    for r in _annotated():
        p = _policy(r)
        if p["class"] != "money":
            continue
        (field,) = p["target"].values()
        body_model = getattr(getattr(r, "body_field", None), "type_", None)
        body_fields = set(getattr(body_model, "model_fields", {}) or {})
        assert field in {x.name for x in r.dependant.path_params} | body_fields, r.path
~~~

  `EXPECTED_DIRECT_TOOLS` holds the 21 names: list_instances, get_instance, get_live_state, get_instance_portfolio_history, get_account_summary, get_positions, get_orders, list_backtests, get_backtest_status, get_backtest_summary, create_backtest, stop_backtest, list_strategies, get_strategy, get_symbol_info, get_symbol_historicals, get_market_news, list_swing_signals, start_instance, stop_instance and live_command.
- [ ] **Step 3: Run them to confirm they fail.** Run `python -m pytest -q -p no:cacheprovider backend/tests/test_mcp_route_policy.py`. Expected: `test_annotated_routes_have_valid_x_mcp` fails, listing the batch-1 routes.
- [ ] **Step 4: Annotate** the 72 batch-1 rows of Appendix A, with 19 docstrings.
- [ ] **Step 5: Run them to confirm they pass**, plus the regression checks `backend/tests/test_api_authorization.py backend/tests/test_app_updates.py backend/tests/test_users_api.py` → pass.
- [ ] **Step 6: Commit** (run detect_changes first): `feat(mcp): x-mcp annotations for platform, auth and LLM routes`, with the Co-Authored-By line.

---

### Task 5: Annotate batch 2 (instances, live control, swing/wheel, config, strategies, models)

**Files:** Modify `backend/api/main.py` (the 54 batch-2 rows); Modify `backend/tests/test_mcp_route_policy.py`.

**Interfaces:** Consumes Task 4's test helpers. Produces nothing new.

- [ ] **Step 1: Impact.** Run impact (upstream) on `api_create_instance`, `api_edit_instance`, `api_delete_instance`, `api_add_stock`, `api_remove_stock`, `api_start_instance`, `api_stop_instance`, `api_clear_instance_state`, `api_readiness_waiver`, `api_revoke_readiness_waiver`, `api_terminate_price`, `api_start_broker`, `api_edit_strategy`, `api_delete_strategy`, `api_link_strategy`, `api_unlink_strategy`, `api_link_brokerage_to_instance`, `api_link_data_brokerage_to_instance`, `api_edit_model`, `api_delete_model`, `api_submit_live_command`, `api_swing_decide_signal`, `api_swing_resend_signal`, `api_nexus_config_set` and grep their callers. Most are wired only through FastAPI. Report the results.
- [ ] **Step 2: Extend the tests.** Append `"/instances", "/live-commands/", "/config/", "/strategies", "/models", "/widget/", "/nexus/config/"` to `ANNOTATED_PREFIXES` and set `PENDING = ("/instances/{instance_id}/kalshi/",)`. Add:

~~~python
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
~~~

- [ ] **Step 3: Run them to confirm they fail.** Run `python -m pytest -q -p no:cacheprovider backend/tests/test_mcp_route_policy.py` → fails, listing the batch-2 routes.
- [ ] **Step 4: Annotate** the 54 batch-2 rows (31 docstrings), including the three wheel-regrade routes as Appendix A gives them.
- [ ] **Step 5: Run them to confirm they pass**, plus `test_instance_*.py test_swing_api.py test_swing_resend_roundtrip.py test_readiness_waiver_route.py test_instance_edit_baseline.py test_models_api_*.py` → pass.
- [ ] **Step 6: Commit** (run detect_changes first): `feat(mcp): x-mcp annotations for instance, live-control, strategy and model routes`, with the Co-Authored-By line.

---

### Task 6: Annotate batch 3 (backtests, research jobs, nexus, agent, learning, digest, discover, trends)

**Files:** Modify `backend/api/main.py` (the 80 batch-3 rows); Modify `backend/tests/test_mcp_route_policy.py`.

- [ ] **Step 1: Impact.** Run impact (upstream) on `api_delete_backtest`, `api_stop_all_backtests`, `api_learning_purge`, `api_learning_decide_approval`, `api_nexus_rebuild`, `api_nexus_delete_edges`, `api_end_trend`, `api_delete_trend`, `api_remove_discovered` and grep their callers. Report the results.
- [ ] **Step 2: Extend the tests.** Append `"/backtests", "/backtest-evidence/", "/trend-research/", "/nexus-graph-builds", "/agent/", "/learning/", "/digest/", "/discover/", "/nexus/", "/trends", "/discovered"` to `ANNOTATED_PREFIXES`. Add:

~~~python
def test_cost_routes_are_exactly_the_paid_work():
    cost = {(m, r.path) for r in _annotated() for m in r.methods if _policy(r).get("cost")}
    assert ("POST", "/backtests") in cost and ("POST", "/trend-research/jobs") in cost
    assert ("POST", "/backtests/{backtest_id}/stop") not in cost
~~~

- [ ] **Step 3: Run them to confirm they fail**, then **Step 4: Annotate** the 80 rows (13 docstrings). `GET /nexus/cache` is `never`.
- [ ] **Step 5: Run them to confirm they pass**, plus `test_backtest_*.py test_learning_*.py test_nexus_*.py` (whichever exist) → pass.
- [ ] **Step 6: Commit** (run detect_changes first): `feat(mcp): x-mcp annotations for backtest, research, nexus and learning routes`, with the Co-Authored-By line.

---

### Task 7: Annotate batch 4 and make the coverage test global

**Files:** Modify `backend/api/main.py` (the 55 batch-4 rows); Modify `backend/tests/test_mcp_route_policy.py`.

- [ ] **Step 1: Impact.** Run impact (upstream) on `api_link_brokerage`, `api_update_brokerage`, `api_delete_brokerage`, `api_ensure_ai_alpaca`, `api_brokerage_place_order`, `api_brokerage_cancel_order`, `api_kalshi_kill`, `api_kalshi_create_instance`, `api_kalshi_update_instance`, `api_kalshi_backtest_delete` and on `api_brokerage_positions` (direct caller: `api_move_analysis_portfolio`; its decorator-only change leaves the call untouched). Report the results.
- [ ] **Step 2: Make the tests global.** Replace `_annotated()`'s filter with every `APIRoute` in `main.app.routes`, and delete `ANNOTATED_PREFIXES` and `PENDING`. Rename `test_annotated_routes_have_valid_x_mcp` to `test_every_route_has_a_valid_x_mcp`; this is the CI coverage gate, and its failure message names each offending `METHOD path`. Add:

~~~python
def test_direct_tools_are_exactly_the_expected_set():
    tools = {p["tool"] for p in map(_policy, _api_routes()) if "tool" in p}
    assert tools == EXPECTED_DIRECT_TOOLS
~~~

  Do not add a route-count or per-route golden test. The spec's anti-goal is a second hand-maintained list, and `test_every_route_has_a_valid_x_mcp` already forces the decision for every new route at its own decorator.
- [ ] **Step 3: Run them to confirm they fail**, then **Step 4: Annotate** the 55 rows (12 docstrings).
- [ ] **Step 5: Run the full suite** (the Global Constraints test command) → no failure beyond the baseline.
- [ ] **Step 6: Commit** (run detect_changes first): `feat(mcp): x-mcp annotations for brokerage, Kalshi and market routes; global coverage test`, with the Co-Authored-By line.


---

### Task 8: Audit module

**Files:**
- Create: `backend/mcp_audit.py`
- Test: `backend/tests/test_mcp_audit.py`

**Interfaces:**
- Consumes: `iso_utc`, `utc_now`, `canonical_json` (Task 1); table McpAudit (Task 3).
- Produces (`mcp_audit`):
  - `AUDIT_TABLE = "McpAudit"`.
  - `SECRET_KEY_NAMES = frozenset({"api_key", "secret", "secret_key", "key", "token", "access_token", "refresh_token", "oauth_token", "password", "password_hash", "passphrase", "private_key", "privatekey", "dsn", "odds_api_key", "oddspapi_api_key"})` and `SECRET_KEY_SUFFIXES = ("_api_key", "_secret", "_token", "_private_key", "_auth_key")` (`_private_key` covers `kalshi_private_key`, the PEM key in `POST /brokerages`; `_auth_key` covers `secret_auth_key` in `POST /auth/signup`).
  - `is_secret_key(name: str) -> bool`: normalize the name first (camelCase → snake_case, hyphens → underscores, lower-case, so `kalshiPrivateKey`, `kalshi-private-key`, `apiKey` and `X-Api-Key` are caught), then an exact name or a suffix.
  - `redact_secrets(value: object) -> object`: a deep copy in which every dict value under a secret key becomes `"[redacted]"`.
  - `summarize_args(query: Mapping, body: object, limit: int = 500) -> str`: the canonical JSON of `{"query", "body"}` after redaction, decoded, cut to `limit` characters, with `"…"` appended when cut.
  - `OUTCOMES = frozenset({"ok", "error", "unauthenticated", "forbidden", "approval_required", "idempotency_required", "idempotency_conflict", "idempotent_replay", "capped", "too_large", "unsupported_media", "executed_approved", "replay_refused", "auth_event", "verify_password_ok", "verify_password_failed", "verify_password_limited"})`.
  - `write_audit(row: dict) -> None`: fills `id` (uuid4 hex) and `ts` when absent, asserts `row["outcome"] in OUTCOMES`, drops any key named `authorization`, and calls `store.insert(AUDIT_TABLE, row)`. It never raises: a failure goes to `logging.getLogger("uvicorn.error").warning("mcp audit write failed: %s", type(e).__name__)`.

- [ ] **Step 1: Write the failing tests**

~~~python
def test_redact_secrets_is_deep_and_case_insensitive():
    got = redact_secrets({"Key": "AK1", "nested": [{"odds_api_key": "x", "symbol": "TQQQ"}], "my_token": "t"})
    assert got == {"Key": "[redacted]", "nested": [{"odds_api_key": "[redacted]", "symbol": "TQQQ"}], "my_token": "[redacted]"}

def test_summarize_args_redacts_and_truncates():
    s = summarize_args({"q": "1"}, {"password": "hunter2", "text": "x" * 900})   # filler sorts after "password"
    assert "hunter2" not in s and "[redacted]" in s and len(s) == 501 and s.endswith("…")

def test_write_audit_fills_id_and_ts(store, monkeypatch):
    use_store(monkeypatch, store)
    write_audit({"outcome": "ok", "status": 200, "operation_id": "list_instances"})
    (row,) = store.run("McpAudit")
    assert len(row["id"]) == 32 and row["ts"].endswith("Z") and row["operation_id"] == "list_instances"

def test_write_audit_never_raises(monkeypatch):
    monkeypatch.setattr(mcp_audit.store, "insert", Mock(side_effect=RuntimeError("down")))
    write_audit({"outcome": "ok"})          # no exception
~~~

- [ ] **Step 2: Run them to confirm they fail.** Run `python -m pytest -q -p no:cacheprovider backend/tests/test_mcp_audit.py` → ModuleNotFoundError.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run them to confirm they pass.**
- [ ] **Step 5: Commit** (run detect_changes first): `feat(mcp): audit writer with secret redaction`, with the Co-Authored-By line.

---

### Task 9: MCP principal and the gate skeleton

**Files:**
- Create: `backend/api/mcp_gate.py`
- Modify: `backend/api/main.py` (`get_current_user`: an additive `request` parameter and branch; register the middleware right after the CORS block)
- Modify: `backend/tests/mcp_helpers.py` (clients, probe app, users, human token)
- Test: `backend/tests/test_mcp_principal.py`

**Interfaces:**
- Consumes: `McpConfig`, `validate_policy` (Task 1); `write_audit`, `summarize_args` (Task 8); `auth_utils.get_user_by_id(conn, user_id)`.
- Produces (`api.mcp_gate`):
  - Scope keys, set only in-process: `PRINCIPAL_SCOPE_KEY = "intellistock.mcp_principal"`, `SERVICE_SCOPE_KEY = "intellistock.mcp_service"` and `REPLAY_SCOPE_KEY = "intellistock.mcp_replay"` (used in Task 13).
  - `SERVICE_LEVEL_ROUTES = frozenset({("GET", "/health"), ("GET", "/internal/mcp/openapi.json"), ("POST", "/internal/mcp/verify-password"), ("POST", "/internal/mcp/audit-event")})`.
  - `principal_from_scope(scope: Mapping) -> dict | None` returns a copy of `scope[PRINCIPAL_SCOPE_KEY]` when its `via == "mcp"`.
  - `match_route(app, scope) -> tuple[BaseRoute | None, dict]`: the first route whose `matches(scope)` gives `Match.FULL`, with its `path_params`.
  - `route_policy(route) -> dict | None`: `validate_policy((getattr(route, "openapi_extra", None) or {}).get("x-mcp"))`.
  - `service_auth_failure(scope, cfg) -> str | None` returns None when the request passes. Otherwise it returns the first failing check, in this order: "disabled" (not cfg.enabled), "no_token", "no_cidr", "peer" (`scope["client"][0]` not an IP inside cfg.allowed_cidr), "token" (Authorization is not exactly one header equal to `"Bearer " + token` under `hmac.compare_digest` on bytes).
  - `class McpGateMiddleware: __init__(self, app, *, config_loader: Callable[[], McpConfig] = McpConfig.from_env)` and `async __call__(scope, receive, send)`.
  - `require_mcp_service(request: Request) -> None` (FastAPI dependency) raises `HTTPException(401, "mcp: unauthenticated")` unless `request.scope.get(SERVICE_SCOPE_KEY)`.
- Produces (`main.get_current_user`): the signature gains `request: Request = None` as its last parameter. The first statement is `principal = mcp_gate.principal_from_scope(request.scope) if request is not None else None`, then `if principal is not None: return principal`. Everything after it is byte-for-byte unchanged.
- Produces (`mcp_helpers`):
  - `PEER_IN = "172.31.250.7"`, `PEER_OUT = "10.9.9.9"`.
  - `seed_operator(store) -> None` inserts Users `{"id": "u-op", "username": "operator", "password_hash": "x", "token_version": 0}`.
  - `human_token(monkeypatch, user_id="u-op", username="operator") -> str` sets `JWT_SECRET` (40 chars) and returns `auth_utils.create_access_token(user_id, username, 0)`.
  - `mcp_client(app, *, peer=PEER_IN, acting_user="u-op", client="jarvis", token=MCP_TOKEN) -> TestClient`: base_url `http://api:8012`, `client=(peer, 40000)`, default headers Authorization / X-MCP-Acting-User / X-MCP-Client / `X-MCP-Correlation-Id` (uuid4); a header whose argument is None is omitted.
  - `public_client(app) -> TestClient` with base_url `http://api:8011`.
  - `human_headers(monkeypatch) -> dict` = `{"Authorization": f"Bearer {human_token(monkeypatch)}"}`; tests bind it to `HUMAN`.
  - `IDEM() -> dict` = `{"Idempotency-Key": uuid4().hex}`.
  - `T0 = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)`, `CFG = McpConfig.from_env({})`, `PRINCIPAL = {"id": "u-op", "username": "operator", "via": "mcp", "client": "jarvis"}`. These are shared constants; time-dependent tests monkeypatch `mcp_policy.utc_now` (and the modules that import it) to return `T0 + delta`.
  - `make_probe_app() -> tuple[FastAPI, dict]`: a FastAPI app with `McpGateMiddleware` whose routes depend on `main.get_current_user`, plus a `calls` dict (counts, last bodies, last principal). Task 9 routes: `GET /whoami` (read, returns current_user), `GET /probe/read` (read), `POST /probe/never` (never), `GET /probe/unannotated` (no x-mcp). Later tasks add routes to the same factory.

Middleware flow at the end of this task (Tasks 11, 13, 14 and 15 extend it):

~~~text
scope["type"] != "http"                         -> await app(scope, receive, send)
cfg = config_loader()
REPLAY_SCOPE_KEY in scope                       -> (Task 13) replay branch
scope["server"][1] != cfg.port   (public)       -> if cfg.service_token and the bearer equals it (compare_digest):
                                                     401 {"detail": "Invalid or expired token"}   # same body as a bad JWT
                                                   else await app(scope, receive, send)   # identical objects, nothing added
service_auth_failure(scope, cfg)                -> audit outcome "unauthenticated"; 401 {"detail": "mcp: unauthenticated"},
                                                   header WWW-Authenticate: Bearer
(method, path) in SERVICE_LEVEL_ROUTES          -> scope[SERVICE_SCOPE_KEY] = True; await app(...)    # no acting user, no audit here
X-MCP-Client present and not ^[A-Za-z0-9._-]{1,32}$ -> 401 (audit unauthenticated)
X-MCP-Acting-User: exactly one header, user exists (get_user_by_id in threadpool) else 401 (audit unauthenticated)
principal = {"id", "username", "via": "mcp", "client": header or "jarvis"}
route, path_params = match_route(scope["app"], scope); policy = route_policy(route)
policy None or class "never"                    -> audit "forbidden"; 403 {"detail": "mcp: operation not available"}
class != "read"                                 -> 403 same body            # TEMPORARY: replaced by Task 11
scope[PRINCIPAL_SCOPE_KEY] = principal; await app(scope, receive, capturing_send)
audit: outcome "ok" if status < 400 else "error"; status; latency_ms; operation_id; user; client; peer;
       correlation_id; args_summary = summarize_args(query, None)
~~~

  Run every store call from the middleware through `fastapi.concurrency.run_in_threadpool`. Build responses with `JSONResponse(...)(scope, receive, send)`.

- [ ] **Step 1: Impact.** Run `mcp__gitnexus__impact({target: "get_current_user", direction: "upstream"})` and expect HIGH or CRITICAL: every authenticated route depends on it. Record the warning in the task report. The edit is additive and guarded by a scope key that only in-process code can set; the tests below pin the human path.

  Grep its direct callers: `get_current_user_optional` (main) and `tests/test_auth_hardening.py` (three calls using keywords `response=, credentials=, conn=`). Both keep working because `request` defaults to None.
- [ ] **Step 2: Write the failing tests** (`backend/tests/test_mcp_principal.py`; autouse: `use_store`, `seed_operator`, `mcp_env`)

~~~python
def test_public_listener_is_a_pure_passthrough():
    seen = {}
    async def inner(scope, receive, send): seen.update(scope=scope, receive=receive, send=send)
    mw = McpGateMiddleware(inner, config_loader=lambda: McpConfig.from_env({"MCP_ENABLED": "true", "MCP_SERVICE_TOKEN": MCP_TOKEN}))
    scope = {"type": "http", "method": "GET", "path": "/instances", "server": ("10.0.0.2", 8011),
             "client": (PEER_IN, 1), "headers": [(b"x-mcp-acting-user", b"u-op")]}
    recv, snd = AsyncMock(), AsyncMock()
    anyio.run(mw, scope, recv, snd)
    assert seen["scope"] is scope and seen["receive"] is recv and seen["send"] is snd
    assert PRINCIPAL_SCOPE_KEY not in scope and SERVICE_SCOPE_KEY not in scope

def test_service_token_on_public_listener_is_401_even_with_forged_headers():
    app, _ = make_probe_app()
    res = public_client(app).get("/probe/read", headers={"Authorization": f"Bearer {MCP_TOKEN}",
        "X-Forwarded-For": PEER_IN, "X-Forwarded-Port": "8012", "Host": "api:8012", "X-MCP-Acting-User": "u-op"})
    assert res.status_code == 401 and res.json() == {"detail": "Invalid or expired token"}

def test_mcp_headers_are_ignored_on_public_listener(monkeypatch):
    app, _ = make_probe_app()
    res = public_client(app).get("/whoami", headers={"Authorization": f"Bearer {human_token(monkeypatch)}",
                                                     "X-MCP-Acting-User": "u-op", "X-MCP-Client": "jarvis"})
    assert res.status_code == 200 and res.json() == {"id": "u-op", "username": "operator"}

def test_principal_shape():
    app, _ = make_probe_app()
    assert mcp_client(app).get("/whoami").json() == {"id": "u-op", "username": "operator", "via": "mcp", "client": "jarvis"}
    assert mcp_client(app, client=None).get("/whoami").json()["client"] == "jarvis"

@pytest.mark.parametrize("kwargs,env", [
    ({}, {"MCP_ENABLED": "false"}),                       # kill switch
    ({"peer": PEER_OUT}, {}),
    ({"token": "wrong" * 8}, {}), ({"token": None}, {}),
    ({"acting_user": "nobody"}, {}), ({"acting_user": None}, {}),
    ({"client": "bad client!"}, {}),
    ({}, {"MCP_SERVICE_TOKEN": "short"}),
])
def test_mcp_listener_refuses(monkeypatch, kwargs, env):
    for k, v in env.items(): monkeypatch.setenv(k, v)
    app, calls = make_probe_app()
    res = mcp_client(app, **kwargs).get("/probe/read")
    assert res.status_code == 401 and calls["count"].get("probe_read", 0) == 0

def test_human_jwt_is_refused_on_mcp_listener(monkeypatch):
    app, _ = make_probe_app()
    assert mcp_client(app, token=human_token(monkeypatch)).get("/whoami").status_code == 401

def test_never_and_unannotated_are_403():
    app, _ = make_probe_app()
    c = mcp_client(app)
    for res in (c.post("/probe/never", json={}), c.get("/probe/unannotated")):
        assert res.status_code == 403 and res.json() == {"detail": "mcp: operation not available"}

def test_get_current_user_human_path_unchanged(monkeypatch, store):
    from api import main
    token = human_token(monkeypatch)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    plain = Request({"type": "http", "headers": []})
    a = main.get_current_user(response=Response(), credentials=creds, conn=None)
    b = main.get_current_user(response=Response(), credentials=creds, conn=None, request=plain)
    assert a == b == {"id": "u-op", "username": "operator"}

def test_audit_rows_for_calls_and_refusals(store):
    app, _ = make_probe_app()
    mcp_client(app).get("/probe/read"); mcp_client(app, peer=PEER_OUT).get("/probe/read")
    outcomes = sorted((r["outcome"], r["status"]) for r in store.run("McpAudit"))
    assert outcomes == [("ok", 200), ("unauthenticated", 401)]
    assert all("Bearer" not in json.dumps(r) and MCP_TOKEN not in json.dumps(r) for r in store.run("McpAudit"))
~~~

- [ ] **Step 3: Run them to confirm they fail.** Run `python -m pytest -q -p no:cacheprovider backend/tests/test_mcp_principal.py` → ImportError (`api.mcp_gate`).
- [ ] **Step 4: Implement** `api/mcp_gate.py` and the two `main.py` edits:
  - add `app.add_middleware(McpGateMiddleware)` right after the CORS `if` block;
  - make the `get_current_user` branch the first statement of its body.
- [ ] **Step 5: Run them to confirm they pass,** plus `test_auth_hardening.py test_api_authorization.py test_users_api.py test_app_updates.py` → pass. Run the full suite → no failure beyond the baseline. Run the Python 3.11 compileall on the touched files → exit 0.
- [ ] **Step 6: Commit** (run detect_changes first; expected symbols: `get_current_user` plus the new module): `feat(mcp): MCP principal and listener-scoped gate skeleton`, with the Co-Authored-By line.

---

### Task 10: Approvals — create, list and get

**Files:**
- Create: `backend/mcp_approvals.py`
- Create: `backend/api/mcp_routes.py`
- Modify: `backend/api/main.py` (`app.include_router(mcp_routes.build_router(get_current_user))` right after the `app_updates` include)
- Modify: `backend/tests/test_mcp_route_policy.py` (`EXPECTED_DIRECT_TOOLS` gains `list_pending_approvals` and `get_approval`)
- Modify: `backend/tests/mcp_helpers.py` (`make_probe_app` includes `mcp_routes.build_router(main.get_current_user)`)
- Test: `backend/tests/test_mcp_approvals.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 8.
- Produces (`mcp_approvals`):
  - `ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")` (contract §5). Every approval and device id is `uuid4().hex`, and a path or body id that fails ID_RE is treated as not found.
  - `APPROVALS_TABLE = "McpApprovals"` and `PUBLIC_FIELDS = ("id", "created_at", "expires_at", "status", "summary", "money_state", "client", "method", "path", "query", "body", "body_sha256", "result_status", "result", "decided_at", "device_id")`.
  - `build_summary(operation_id: str, method: str, path: str, path_params: Mapping[str, str], body: object, money_state: str) -> str` (formats below; at most 200 characters, truncating the core with "…" and always keeping the suffix).
  - `create_approval(*, principal: dict, operation_id: str, method: str, path: str, query: dict, body: object, money_state: str, summary: str, idempotency_key: str | None, correlation_id: str | None, cfg: McpConfig, now: datetime | None = None) -> dict`: inserts and returns the row (the Task 3 fields, status "pending", expires_at = now + cfg.approval_ttl_s, body_sha256 = `canonical_request_sha256(method, path, query, body)`).
  - `send_approval_push(row: dict) -> None` calls `apns_sender.send_to_user(row["user_id"], title="Jarvis wants to act", body=row["summary"], category="MCP_APPROVAL", data={"approval_id": row["id"]})` and swallows exceptions. `notify_approval_async(row: dict) -> None` runs it in a daemon thread.
  - `approval_public(row: dict) -> dict` returns exactly `PUBLIC_FIELDS`.
  - `terminal_scrub(row: dict) -> dict` returns a copy with `body = mcp_audit.redact_secrets(body)`.
  - `refresh_lazily(row: dict, *, cfg: McpConfig, now: datetime | None = None) -> dict`:
    - pending past expires_at → CAS to "expired" (with `terminal_scrub`);
    - approved, with exec_claimed_at older than `cfg.approval_exec_stale_s` and no result_status → CAS to "failed" with the Decision 8 detail;
    - returns the current row.
  - `get_visible(approval_id: str, *, viewer: dict, cfg, now=None) -> dict | None`: None when the row is missing, when `row["user_id"] != viewer["id"]`, or (for an MCP viewer, `via == "mcp"`) when `row["client"] != viewer["client"]`.
  - `list_visible(*, viewer: dict, status: str, limit: int, cfg, now=None) -> list[dict]`: newest first.
  - `cas_status(approval_id: str, *, expect: str, patch: dict) -> dict | None`: read the row, merge the patch, then `store.replace_if(APPROVALS_TABLE, id, when=P.field("status").eq(expect), doc=new)`.
- Produces (`api.mcp_routes`): `build_router(current_user_dependency) -> APIRouter` with:
  - `GET /mcp/approvals`: `operation_id="list_pending_approvals"`, `openapi_extra=mcp_policy("read", tool="list_pending_approvals")`, query `status: str = "pending"` (must be `pending` or `all`, else 400) and `limit: int = 50` (clamped to 1..200). Returns `{"approvals": [approval_public(...)]}`. Docstring: "List MCP approval requests (pending by default)."
  - `GET /mcp/approvals/{approval_id}`: `operation_id="get_approval"`, read, tool `get_approval`. Returns `approval_public`, or 404 `{"detail": "approval not found"}`. Docstring: "One MCP approval request and, once executed, its result."

Summary formats (the core, then a suffix chosen by money_state):

| operation_id | core |
|---|---|
| live_command, type submit_order | `"submit_order {SIDE} {qty} {symbol} on {instance_id}"` |
| live_command, other types | `"{type} {symbol?} on {instance_id}"` |
| brokerage_place_order | `"{SIDE} {qty or "$" + notional} {symbol} {type} on brokerage {brokerage_id}"` |
| start_instance / stop_instance | `"start instance {id}"` / `"stop instance {id}"` |
| swing_decide_signal | `"{decision} swing signal {signal_id} on {instance_id}"` |
| anything else | `"{operation_id}: {METHOD} {path}"` |

Suffixes: live `" — LIVE"`, unknown `" — MONEY STATE UNKNOWN"`, protected `" — PROTECTED PAPER"`, irreversible `" — IRREVERSIBLE"`.

- [ ] **Step 1: Write the failing tests**

~~~python
def test_summary_formats():
    assert build_summary("live_command", "POST", "/instances/alpaca-main/live-command", {"instance_id": "alpaca-main"},
        {"type": "submit_order", "payload": {"symbol": "TQQQ", "side": "buy", "qty": 10}}, "live") == \
        "submit_order BUY 10 TQQQ on alpaca-main — LIVE"
    assert build_summary("delete_instance", "DELETE", "/instances/x", {"instance_id": "x"}, None, "irreversible") == \
        "delete_instance: DELETE /instances/x — IRREVERSIBLE"
    assert len(build_summary("edit_strategy", "PUT", "/strategies/1" + "9" * 300, {}, None, "protected")) <= 200

def test_create_approval_row(store, monkeypatch):
    row = create_approval(principal=PRINCIPAL, operation_id="live_command", method="POST", path="/p", query={},
                          body={"a": 1}, money_state="live", summary="s", idempotency_key="k1",
                          correlation_id="c1", cfg=CFG, now=T0)
    assert row["status"] == "pending" and row["expires_at"] == iso_utc(T0 + timedelta(seconds=900))
    assert row["body_sha256"] == canonical_request_sha256("POST", "/p", {}, {"a": 1})
    assert set(approval_public(row)) == set(PUBLIC_FIELDS)

def test_pending_past_expiry_reads_expired_and_is_scrubbed(store, monkeypatch): ...
    # body {"secret": "s"} created at T0, listed at T0 + 901 s -> status "expired", body {"secret": "[redacted]"}

def test_stale_execution_reads_failed(store, monkeypatch): ...
    # status "approved", exec_claimed_at T0, no result; read at T0 + 601 s -> "failed", result.detail contains "outcome unknown"

def test_human_sees_only_own_approvals(store, monkeypatch): ...       # GET /mcp/approvals/{id} for another user's row -> 404
def test_mcp_principal_sees_only_its_client(store, monkeypatch): ...  # on :8012, client "jarvis" vs a row with client "other" -> 404; list excludes it
def test_list_params(store, monkeypatch): ...                         # status=bogus -> 400; limit=0 -> 1 row max; limit=999 -> clamps to 200

def test_approval_object_rehashes_to_body_sha256(store, monkeypatch):   # iOS recomputes it (contract §5)
    a = approval_public(create_approval(principal=PRINCIPAL, operation_id="x", method="POST", path="/instances/my bot/act",
        query={"q": ["1", "2"]}, body={"note": "é", "n": 1.5}, money_state="live", summary="s",
        idempotency_key=None, correlation_id=None, cfg=CFG, now=T0))
    canon = json.dumps({"method": a["method"], "path": a["path"], "query": a["query"], "body": a["body"]},
                       sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert hashlib.sha256(canon).hexdigest() == a["body_sha256"]

def test_malformed_id_is_404(): ...                                   # GET /mcp/approvals/bad%20id!! -> 404

def test_push_payload(monkeypatch):
    sent = Mock(); monkeypatch.setattr(apns_sender, "send_to_user", sent)
    send_approval_push({"id": "a1", "user_id": "u-op", "summary": "s"})
    sent.assert_called_once_with("u-op", title="Jarvis wants to act", body="s", category="MCP_APPROVAL", data={"approval_id": "a1"})
~~~

- [ ] **Step 2: Run them to confirm they fail.** Run `python -m pytest -q -p no:cacheprovider backend/tests/test_mcp_approvals.py backend/tests/test_mcp_route_policy.py` → ImportError, plus the direct-tool set mismatch.
- [ ] **Step 3: Implement** per Interfaces. The routes are plain `def` (threadpool), and the viewer is the resolved `current_user`.
- [ ] **Step 4: Run them to confirm they pass,** plus `test_api_authorization.py` (both new routes are GETs).
- [ ] **Step 5: Commit** (run detect_changes first; main.py symbol touched: module-level include only): `feat(mcp): approval records, summaries, push and read routes`, with the Co-Authored-By line.


---

### Task 11: Gate — body buffering, money resolution, 202 approvals, dispatch

**Files:**
- Modify: `backend/api/mcp_gate.py`
- Modify: `backend/tests/mcp_helpers.py` (probe routes below)
- Test: `backend/tests/test_mcp_gate.py`

**Interfaces:**
- Consumes: `resolve_money_state`, `approval_reason`, `canonical_request_sha256` (Tasks 1–2); `create_approval`, `build_summary`, `notify_approval_async` (Task 10); `summarize_args`, `write_audit` (Task 8).
- Produces: the middleware's full principal path. Tasks 14 and 15 insert their steps at the marked points. Also these probe routes in `make_probe_app`, all depending on `main.get_current_user`:
  - `POST /probe/write` (write) echoes its JSON body;
  - `POST /probe/instances/{instance_id}/act` (money, target instance);
  - `POST /probe/brokerages/{brokerage_id}/order` (money, target brokerage);
  - `POST /probe/cost` (write, cost);
  - `DELETE /probe/things/{thing_id}` (irreversible);
  - `POST /probe/boom` (write; raises RuntimeError);
  - `POST /probe/refuse` (write; raises `HTTPException(400, "nope")`);
  - `POST /probe/backtests` with `operation_id="create_backtest"` (write, cost), for Task 15;
  - for Task 13, `POST /probe/instances/{instance_id}/boom` (money, raises RuntimeError) and `POST /probe/instances/{instance_id}/refuse` (money, raises `HTTPException(400, "nope")`).

  Their operation_ids are `probe_write, probe_act, probe_order, probe_cost, probe_delete, probe_boom, probe_refuse, create_backtest, probe_act_boom, probe_act_refuse`; Task 9's are `whoami, probe_read, probe_never, probe_unannotated`. Each route counts its calls in `calls["count"][operation_id]`, and records the body it received (`calls["body"]`) and the principal (`calls["principal"]`).

Principal path after this task (it replaces the TEMPORARY line of Task 9):

~~~text
1  route/policy lookup -> never / missing -> 403                                     (Task 9)
2  read the whole body from receive(); > cfg.max_body_bytes -> 413 {"detail": "mcp: request body too large"} (audit too_large)
   non-empty body whose content-type is not application/json, or invalid JSON -> 415 {"detail": "mcp: JSON body required"}
   body_obj = parsed JSON or None; query = dict(parse_qsl(query_string, keep_blank_values=True)), repeated keys -> list
   req_sha = canonical_request_sha256(method, scope["path"], query, body_obj)
3  [Task 14: Idempotency-Key check, lookup, claim]
4  [Task 15: rate caps, cost cap, backtest slot]
5  money_state = resolve_money_state(policy, path_params, body_obj, cfg) if class == "money" else None   (threadpool)
   reason = approval_reason(policy, money_state)
6  reason -> [Task 15: approvals cap] -> row = create_approval(...) (threadpool); notify_approval_async(row)
            respond 202 {"pending_approval": {"id", "summary", "money_state": reason, "expires_at"}}
            audit outcome "approval_required", approval_id
7  else   -> scope[PRINCIPAL_SCOPE_KEY] = principal
            await app(scope, buffered_receive, capturing_send)   # buffered_receive: one http.request with the
                                                                  # whole body, then defers to the real receive
            on exception: audit "error" status 500, then re-raise
8  [Task 14: finalize or release the idempotency record]
9  audit: outcome "ok" (< 400) or "error"; status; latency; args_sha256 = req_sha; args_summary; money_state
~~~

- [ ] **Step 1: Impact.** None: no existing function is edited (`mcp_gate` is new code from Task 9).
- [ ] **Step 2: Write the failing tests.** Autouse: `use_store`, `seed_operator`, `seed_money_fixture`, `mcp_env`, and `notify_approval_async` patched to a Mock. Every non-read request sends `headers=IDEM()`, so the tests keep passing after Task 14.

~~~python
def test_read_runs(): ...                      # GET /probe/read -> 200, count 1
def test_write_runs_and_sees_the_exact_body(): # POST /probe/write {"x": "é"} -> 200; handler got {"x": "é"}; principal via "mcp"
def test_money_on_paper_runs():                # /probe/instances/paper-1/act -> 200, count 1
def test_money_on_none_runs():                 # /probe/instances/no-broker/act -> 200

def test_money_on_live_waits_for_approval(store):
    res = c.post("/probe/instances/alpaca-main/act", json={"qty": 1}, headers=IDEM())
    assert res.status_code == 202 and calls["count"].get("probe_act", 0) == 0
    pa = res.json()["pending_approval"]
    assert set(pa) == {"id", "summary", "money_state", "expires_at"} and pa["money_state"] == "live"
    row = store.get("McpApprovals", pa["id"])
    assert row["status"] == "pending" and row["body"] == {"qty": 1} and row["client"] == "jarvis"
    assert row["body_sha256"] == canonical_request_sha256("POST", "/probe/instances/alpaca-main/act", {}, {"qty": 1})
    notify_mock.assert_called_once()

def test_money_with_unknown_target_waits_for_approval():   # instance "ghost" -> 202 money_state "unknown"
def test_protected_paper_waits():                          # f14-paper -> 202 "protected"
def test_escalation_by_body_brokerage():                   # paper-1 with {"brokerage_id": "b-live"} -> 202 "live"
def test_irreversible_always_waits():                      # DELETE /probe/things/1 -> 202 "irreversible"
def test_body_too_large_is_413(monkeypatch):               # MCP_MAX_BODY_BYTES=10, 11-byte body -> 413, handler not run
def test_non_json_body_is_415():                           # content=b"a=1", form content-type -> 415
def test_route_exception_is_audited_and_raised():          # /probe/boom -> 500 (TestClient raise_server_exceptions=False); audit "error" 500
def test_route_4xx_passes_through():                       # /probe/refuse -> 400 {"detail": "nope"}

def test_gate_covers_routes_without_get_current_user(monkeypatch):
    from api import main
    c = mcp_client(main.app)
    assert c.get("/help").status_code == 200                     # read, no get_current_user
    for m, p in (("GET", "/kalshi/img"), ("POST", "/auth/login"), ("GET", "/ota/m/x.plist")):
        assert c.request(m, p, json={}).json() == {"detail": "mcp: operation not available"}
    assert mcp_client(main.app, token=None).get("/help").status_code == 401

def test_replay_marker_header_is_ignored():       # header "intellistock.mcp_replay: 1" on a live call -> still 202
~~~

- [ ] **Step 3: Run them to confirm they fail.** Run `python -m pytest -q -p no:cacheprovider backend/tests/test_mcp_gate.py` → the money, write and irreversible cases fail (403 from the TEMPORARY line).
- [ ] **Step 4: Implement** the flow. Delete the TEMPORARY line. `capturing_send` forwards every message unchanged and records `status` (and, for Task 14, the body chunks).
- [ ] **Step 5: Run them to confirm they pass,** plus `test_mcp_principal.py` → pass.
- [ ] **Step 6: Commit** (run detect_changes first): `feat(mcp): gate holds live-money and irreversible calls for approval`, with the Co-Authored-By line.

---

### Task 12: Approval devices

**Files:**
- Modify: `backend/mcp_approvals.py` (devices)
- Modify: `backend/api/mcp_routes.py` (3 routes)
- Modify: `backend/tests/mcp_helpers.py` (`p256_keypair`, `sign_decision`)
- Test: `backend/tests/test_mcp_devices.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 10.
- Produces (`mcp_approvals`):
  - `DEVICES_TABLE = "McpApprovalDevices"` and `DEVICE_ACTIVATION_DELAY_S = 600`.
  - `decode_public_key(b64: str) -> ec.EllipticCurvePublicKey`: base64 → exactly 65 bytes beginning `0x04` → `ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)`. Raises ValueError otherwise.
  - `register_device(*, user: dict, name: str, public_key_b64: str, now=None) -> dict` (row; active_at = now + 600 s).
  - `list_devices(user_id: str) -> list[dict]` (not revoked).
  - `revoke_device(*, user_id: str, device_id: str, now=None) -> bool` (sets revoked_at; False when not found or not the user's).
  - `active_device(*, user_id: str, device_id: str, now=None) -> dict | None`: the row when it is the user's, not revoked, and has `active_at <= now`.
  - `alert_device_registered(row: dict, username: str) -> None` calls `notifications.notify(category="other", instance_id="mcp", title="MCP approval device registered", body=..., discord_channel="notifications")`; best-effort. The body is `f"MCP APPROVAL DEVICE registered: '{name}' for {username}; it can approve from {active_at}. If this was not you, revoke it in Settings now."`.
- Produces (routes; all `mcp_policy("never")`, all `def`):
  - `POST /mcp/approval-devices`: `operation_id="register_mcp_approval_device"`, `status_code=201`, body `payload: dict = Body(...)` with `name` (1–64 chars after strip) and `public_key`. Returns `{"device_id", "active_at"}`; bad values → 400.
  - `GET /mcp/approval-devices`: `operation_id="list_mcp_approval_devices"` → `{"devices": [{"device_id", "name", "created_at", "active_at"}]}`.
  - `DELETE /mcp/approval-devices/{device_id}`: `operation_id="revoke_mcp_approval_device"` → 204, or 404 (also for an id failing ID_RE).
  - Device ids are `uuid4().hex`. The device object is exactly `{"device_id", "name", "created_at", "active_at"}` (contract §5).
  - All three answer `403 {"detail": "mcp: operation not available"}` when `current_user.get("via") == "mcp"` (defense in depth; the gate already refuses never routes).
- Produces (`mcp_helpers`): `p256_keypair() -> tuple[ec.EllipticCurvePrivateKey, str]` (the private key, plus base64 of `public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)`), and `sign_decision(private_key, approval_id, body_sha256, decision, ts) -> str` (base64 DER of `sign(message, ec.ECDSA(hashes.SHA256()))`).

- [ ] **Step 1: Write the failing tests**

~~~python
def test_register_returns_id_and_activation_ten_minutes_out(store, monkeypatch):   # freeze utc_now at T0
    _, pub = p256_keypair()
    res = public_client(app).post("/mcp/approval-devices", json={"name": "iPhone", "public_key": pub}, headers=HUMAN)
    assert res.status_code == 201 and res.json()["active_at"] == iso_utc(T0 + timedelta(seconds=600))

@pytest.mark.parametrize("payload", [{"name": "", "public_key": "PUB"}, {"name": "x" * 65, "public_key": "PUB"},
    {"name": "p", "public_key": "!!notb64"}, {"name": "p", "public_key": base64.b64encode(b"\x04" + b"\0" * 64).decode()},
    {"name": "p", "public_key": base64.b64encode(b"\x04" * 33).decode()}])
def test_register_rejects_bad_input(payload): ...                 # 400 ("PUB" is replaced by a valid key)

def test_register_sends_discord_alert(monkeypatch): ...           # notify called once; body contains "iPhone" and "operator"
def test_mcp_principal_cannot_register(): ...                     # :8012 -> 403; route called with a principal override -> 403
def test_list_and_revoke(): ...                                   # list shows 1; DELETE -> 204; list empty; DELETE again -> 404
def test_active_device_respects_delay_and_revocation(store): ...  # T0 + 599 s -> None; T0 + 600 s -> row; revoked -> None
def test_another_users_device_is_invisible(store): ...            # revoke_device(user_id="u-2", ...) -> False
~~~

- [ ] **Step 2: Run them to confirm they fail.** Run `python -m pytest -q -p no:cacheprovider backend/tests/test_mcp_devices.py` → 404 / ImportError.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them to confirm they pass,** plus `test_api_authorization.py` (new POST and DELETE refuse a bad token with 401) and `test_mcp_route_policy.py` → pass.
- [ ] **Step 5: Commit** (run detect_changes first): `feat(mcp): approval device registration with delayed activation and alert`, with the Co-Authored-By line.

---

### Task 13: Signed decision, CAS, and in-process replay

**Files:**
- Modify: `backend/mcp_approvals.py` (decision)
- Create: `backend/api/mcp_replay.py`
- Modify: `backend/api/mcp_gate.py` (the replay branch)
- Modify: `backend/api/mcp_routes.py` (the decision route)
- Test: `backend/tests/test_mcp_decision.py`

**Interfaces:**
- Consumes: Tasks 10–12.
- Produces (`mcp_approvals`):
  - `SIGNATURE_MAX_SKEW_S = 60` and `class DecisionError(Exception)` with attributes `status: int` and `detail: str`.
  - `signed_message(approval_id: str, body_sha256: str, decision: str, ts: int) -> bytes` = `f"{approval_id}|{body_sha256}|{decision}|{ts}".encode("utf-8")`.
  - `verify_signature(public_key_b64: str, message: bytes, signature_b64: str) -> bool`: False on any decode error or `InvalidSignature`.
  - `decide(approval_id: str, payload: dict, *, user: dict, cfg: McpConfig, now: datetime | None = None) -> dict` returns the row after the CAS, or raises `DecisionError`. Checks, in order:
    1. Payload shape: decision in approve/deny, body_sha256 64 lowercase hex, ts an int, device_id matching `ID_RE`, signature base64 of at most 200 chars; else 400.
    2. `user.get("via") == "mcp"` → 403.
    3. `approval_id` fails ID_RE, or the row is missing or belongs to another user → 404.
    4. Status is not pending → 409.
    5. Past expires_at → `cas_status(expect="pending", patch={"status": "expired"} + scrub)`, then 410.
    6. body_sha256 differs from the row's → 400.
    7. `abs(now - ts) > 60` → 403.
    8. `active_device` is None → 403.
    9. The signature fails → 403.
    10. `cas_status(expect="pending", patch={"status": "approved"|"denied", "decided_at", "device_id"})` returns None → 409.
    11. A deny is scrubbed (terminal).
  - `claim_execution(approval_id: str, *, now=None) -> dict | None`: CAS with `when = status == "approved" & exec_claimed_at IS NULL` (`P.field("exec_claimed_at").is_null()`), setting exec_claimed_at.
  - `record_result(approval_id: str, *, status_code: int, result: object, now=None) -> dict | None`: CAS approved → `"executed"` when `200 <= status_code < 300`, else `"failed"`. It sets result_status, result and executed_at, and applies `terminal_scrub`.
- Produces (`api.mcp_replay`):
  - `RESULT_CAP_BYTES = 262144` and `@dataclass class ReplayResult: status: int; body: object`.
  - `async def replay_approved_request(app, approval: dict, principal: dict) -> ReplayResult` builds the scope:
    - `{"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method", "scheme": "http"`;
    - `"path": approval["path"], "raw_path": quote(path).encode(), "root_path": "", "query_string": urlencode(query, doseq=True).encode()`;
    - `"headers"`: host `mcp-replay`, content-type json, content-length, `authorization: Bearer mcp-replay` (a dummy, so HTTPBearer passes; it is never a valid JWT), and x-mcp-client;
    - `"client": ("mcp-replay", 0)` (deliberately not loopback), `"server": ("mcp-replay", 0)`, `"state": {}`;
    - `REPLAY_SCOPE_KEY: {"approval_id": id, "principal": principal}}`.

    receive yields the JSON body once, then waits for the response to complete before returning `http.disconnect`. send collects the status and body. An exception from `app(...)` is caught, and the result is the captured status or 500. The body is parsed JSON, else `{"text": ...}`. When the serialized result exceeds the cap, it becomes `{"truncated": True, "partial": <first 262144 chars of the raw text>}`.
- Produces (gate replay branch, checked before the listener test):

~~~text
marker = scope[REPLAY_SCOPE_KEY]
row = claim_execution(marker["approval_id"])  (threadpool)
row is None, or row.method != scope.method, or row.path != scope.path
    -> audit "replay_refused"; 403 {"detail": "mcp: approval is not executable"}
route/policy lookup; missing / never -> 403 (defense)
scope[PRINCIPAL_SCOPE_KEY] = marker["principal"]; await app(scope, receive, capturing_send)
audit "executed_approved", approval_id, status, latency                  # no idempotency, no caps
~~~

- Produces (route): `POST /mcp/approvals/{approval_id}/decision` with `operation_id="decide_mcp_approval"`, `mcp_policy("never")`, written as `async def api_mcp_approval_decision(approval_id: str, request: Request, payload: dict = Body(...), current_user: dict = Depends(current_user_dependency))`:

~~~text
row = await run_in_threadpool(decide, approval_id, payload, user=current_user, cfg=McpConfig.from_env())
      DecisionError(e) -> HTTPException(e.status, e.detail)
if row["status"] == "approved":
    principal = {"id": current_user["id"], "username": current_user["username"], "via": "mcp", "client": row["client"]}
    result = await replay_approved_request(request.app, row, principal)
    row = await run_in_threadpool(record_result, row["id"], status_code=result.status, result=result.body)
return approval_public(row)
~~~

  The route is `async` so that it can await the replay on the event loop. Every store call goes through `run_in_threadpool`, and the replayed sync routes run in the threadpool as usual.

- [ ] **Step 1: Write the failing tests.** Fixture: the probe app plus a registered device whose `active_at` is backdated to T0 − 1 s. A live approval is created through the gate (`/probe/instances/alpaca-main/act`), and `decide_json(...)` builds a signed payload.

~~~python
def test_approve_executes_exactly_once(store):
    res = public_client(app).post(f"/mcp/approvals/{aid}/decision", json=decide_json("approve"), headers=HUMAN)
    assert res.status_code == 200 and res.json()["status"] == "executed" and res.json()["result_status"] == 200
    assert calls["count"]["probe_act"] == 1
    again = public_client(app).post(f"/mcp/approvals/{aid}/decision", json=decide_json("approve"), headers=HUMAN)
    assert again.status_code == 409 and calls["count"]["probe_act"] == 1

def test_replay_runs_under_the_approvers_mcp_principal(): # calls["principal"] == {"id": "u-op", "username": "operator", "via": "mcp", "client": "jarvis"}
def test_deny_does_not_execute():                         # 200, status "denied", count 0
def test_bad_signature_is_403():                          # signed with another key
def test_unknown_device_is_403(): ...; def test_device_not_yet_active_is_403(): ...   # active_at = now + 60 s
def test_revoked_device_is_403(): ...
def test_stale_ts_is_403():                               # ts = now - 61
def test_body_sha_mismatch_is_400(): ...
def test_bad_input_is_400():                              # decision "maybe"; ts "x"; missing signature
def test_expired_pending_is_410_then_409():               # decided at created + 901 s -> 410; row status "expired"; again -> 409
def test_other_user_gets_404(): ...
def test_mcp_principal_cannot_decide():                   # :8012 -> 403 (never); route with principal override -> 403

def test_failed_replay_is_terminal(store):                # money route that raises, and one answering 400
    # raising route -> 200 response, approval status "failed", result_status 500; decide again -> 409; count stays 1
    # 400 route    -> status "failed", result_status 400, result == {"detail": "nope"}

def test_replay_marker_is_one_shot(store):
    first = anyio.run(replay_approved_request, app, row, principal)
    second = anyio.run(replay_approved_request, app, row, principal)
    assert first.status == 200 and second.status == 403 and calls["count"]["probe_act"] == 1

def test_replay_preserves_percent_encoded_path(store):     # instance id "my bot" seeded as live
    # approval path "/probe/instances/my bot/act"; after approve, the handler saw instance_id == "my bot"

def test_executed_approval_scrubs_secret_body_keys(store): # body {"key": "AK", "qty": 1} -> stored body {"key": "[redacted]", "qty": 1}
def test_replay_is_audited_with_the_approval_id(store):    # McpAudit row with outcome "executed_approved", approval_id == aid
~~~

- [ ] **Step 2: Run them to confirm they fail.** Run `python -m pytest -q -p no:cacheprovider backend/tests/test_mcp_decision.py` → 404 / ImportError.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run them to confirm they pass,** plus `test_mcp_gate.py test_mcp_approvals.py test_api_authorization.py` → pass. Run the full suite → no failure beyond the baseline.
- [ ] **Step 5: Commit** (run detect_changes first): `feat(mcp): signed approval decisions with exactly-once in-process execution`, with the Co-Authored-By line.


---

### Task 14: Idempotency

**Files:**
- Create: `backend/mcp_idempotency.py`
- Modify: `backend/api/mcp_gate.py` (steps 3 and 8)
- Test: `backend/tests/test_mcp_idempotency.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 11.
- Produces (`mcp_idempotency`):
  - `IDEMPOTENCY_TABLE = "McpIdempotency"`, `KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")`, `TTL_S = 86400`, `RESPONSE_CAP_BYTES = 1048576`.
  - `record_id(client: str, key: str) -> str` = sha256 hex of `f"{client}\x00{key}"`.
  - `@dataclass class IdemLookup: kind: str; record: dict | None`, where kind is one of "absent", "replay", "conflict" or "in_progress".
  - `lookup(client, key, request_sha256, *, now=None) -> IdemLookup`: a row older than TTL_S counts as absent.
  - `claim(client, key, *, method, path, request_sha256, now=None) -> bool`: deletes an expired row first. It is `store.insert(..., conflict="error")` with state "in_progress", and it is claimed iff `result["inserted"] == 1`.
  - `finalize(client, key, *, status: int, content_type: str, body: bytes, now=None) -> None` sets state "done" and stores `body_text` (UTF-8, `errors="replace"`). A body over RESPONSE_CAP_BYTES stores `too_large: True` and no body.
  - `release(client, key) -> None` deletes the row.
- Gate step 3, non-read classes only:
  - The header is missing, duplicated, or fails KEY_RE → 428 `{"detail": "mcp: Idempotency-Key required"}` (audit idempotency_required).
  - lookup returns "replay" → respond with the stored status and body, content-type, and `X-MCP-Idempotent-Replay: true`. A `too_large` row replays as its status with `{"detail": "mcp: original response too large to replay"}`. Audit idempotent_replay.
  - "conflict" → 409 `{"detail": "mcp: Idempotency-Key reused with a different request"}`.
  - "in_progress" → 409 `{"detail": "mcp: Idempotency-Key request still in progress"}`.
  - After caps (step 4), claim. When the claim is lost, re-run lookup and answer as above.
- Gate step 8:
  - finalize with the captured status, content-type and body, for both the 202 approval response and dispatched responses;
  - an exception releases the key before re-raising.

- [ ] **Step 1: Write the failing tests**

~~~python
def test_non_read_without_key_is_428(): ...          # also a 129-char key and one containing a space
def test_read_without_key_runs(): ...
def test_replay_returns_stored_response(store):
    a = c.post("/probe/write", json={"n": 1}, headers={"Idempotency-Key": "k-1"})
    b = c.post("/probe/write", json={"n": 1}, headers={"Idempotency-Key": "k-1"})
    assert (b.status_code, b.json()) == (a.status_code, a.json()) and b.headers["X-MCP-Idempotent-Replay"] == "true"
    assert calls["count"]["probe_write"] == 1
def test_same_key_different_body_is_409(): ...       # exact contract detail
def test_same_key_different_path_is_409(): ...
def test_concurrent_duplicate_key_is_409_and_runs_once(store):
    # pre-insert the claim row for (jarvis, "k-2") with state "in_progress" and the same request_sha256
    # -> 409 "mcp: Idempotency-Key request still in progress"; probe_write count 0
def test_expired_record_is_ignored(store): ...       # done row created 25 h ago -> executes again
def test_pending_approval_is_replayed_not_duplicated(store):   # live money twice with one key -> same approval id; 1 McpApprovals row
def test_route_exception_releases_the_key(store): ...          # /probe/boom raises -> no row left; a retry with the same key runs again
def test_keys_are_scoped_per_client(): ...                     # same key, X-MCP-Client "other" -> runs independently
~~~

- [ ] **Step 2: Run them to confirm they fail**, then **Step 3: Implement**. Update the Task 11 tests' helper only if a test breaks: they already send keys.
- [ ] **Step 4: Run them to confirm they pass,** plus `test_mcp_gate.py test_mcp_decision.py`.
- [ ] **Step 5: Commit** (run detect_changes first): `feat(mcp): idempotency keys for MCP writes`, with the Co-Authored-By line.

---

### Task 15: Caps

**Files:**
- Create: `backend/mcp_caps.py`
- Modify: `backend/api/mcp_gate.py` (step 4 and the approvals cap in step 6)
- Test: `backend/tests/test_mcp_caps.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 8, 10, 11, 14.
- Produces (`mcp_caps`):
  - `@dataclass(frozen=True) class CapRefusal: detail: str; retry_after_s: int`.
  - `BACKTEST_START_OPERATIONS = frozenset({"create_backtest"})`.
  - `running_backtest_count() -> int`: `store.sql('SELECT count(*) AS n FROM "BacktestProgress" WHERE status IN (%s, %s, %s) OR status LIKE %s', ("running", "queued", "pending", "paused%"))`.
  - `class McpCaps`, with `__init__(self, *, clock: Callable[[], float] = time.time)`; one `threading.Lock` guards all state. Each check reserves on success:
    - `check_rate(self, *, non_read: bool, cfg) -> CapRefusal | None`: 60-second sliding deques. Calls use `cfg.cap_calls_per_min`; non-read calls also use `cfg.cap_writes_per_min`. retry_after_s = `max(1, ceil(oldest + 60 - now))`. Detail: "mcp: call rate cap reached" or "mcp: write rate cap reached".
    - `check_cost(self, cfg) -> CapRefusal | None`: the daily count is seeded once per UTC date from `store.count(store.filter("McpAudit", P.field("ts").ge(midnight_iso) & P.field("cap_bucket").eq("cost")))`, then incremented in memory. retry_after_s = seconds to the next UTC midnight. Detail: "mcp: daily cost-route cap reached".
    - `check_approvals(self, cfg) -> CapRefusal | None`: the same scheme, seeded from McpApprovals `created_at >= midnight`. Detail: "mcp: daily approval-request cap reached".
    - `check_backtest_slot(self, cfg) -> CapRefusal | None`: when `running_backtest_count() >= cfg.cap_concurrent_backtests`, or the count raises, returns `CapRefusal("mcp: a backtest is already running (one at a time)", 60)`. It reserves nothing.
  - `caps = McpCaps()`, the module singleton the gate uses.
- Gate step 4 (after the idempotency lookup, before the claim):
  - `check_rate`;
  - when `policy.get("cost")`: `check_cost`, and the audit row gets `cap_bucket = "cost"`;
  - when the operation_id is in BACKTEST_START_OPERATIONS: `check_backtest_slot`.
- Gate step 6: `check_approvals` before `create_approval`.
- Any refusal → 429 `{"detail", "retry_after_s"}` plus the `Retry-After` header (same integer), and audit "capped". Replays (Task 13), idempotent replays and service-level routes never touch caps.

- [ ] **Step 1: Write the failing tests** (each test builds a fresh `McpCaps` and monkeypatches `mcp_caps.caps`; the clock is injected)

~~~python
def test_call_rate_cap(monkeypatch):                # MCP_CAP_CALLS_PER_MIN=2 -> third GET is 429
    assert res.status_code == 429 and res.headers["Retry-After"] == str(res.json()["retry_after_s"])
    assert 1 <= res.json()["retry_after_s"] <= 60
def test_write_rate_cap(monkeypatch): ...           # MCP_CAP_WRITES_PER_MIN=1 -> second write 429, reads still fine
def test_cost_cap_resets_at_utc_midnight(monkeypatch):   # MCP_CAP_COST_PER_DAY=1, clock 2026-11-19T23:00:00Z
    assert second.json()["retry_after_s"] == 3600
def test_cost_cap_survives_restart(store, monkeypatch):  # seed an McpAudit row (ts today, cap_bucket "cost"), cap 1 -> first call 429
def test_approvals_cap(monkeypatch): ...            # MCP_CAP_APPROVALS_PER_DAY=1 -> second live call 429, no second row
def test_one_backtest_at_a_time(monkeypatch):
    monkeypatch.setattr(mcp_caps, "running_backtest_count", lambda: 1)
    res = c.post("/probe/backtests", json={}, headers=IDEM())
    assert res.status_code == 429 and "one at a time" in res.json()["detail"]
def test_backtest_count_failure_fails_closed(monkeypatch): ...   # count raises -> 429
def test_idempotent_replay_does_not_consume_caps(monkeypatch): ...
def test_public_listener_is_never_capped(monkeypatch): ...       # cap 1, three human calls on :8011 -> all 200
~~~

- [ ] **Step 2: Run them to confirm they fail**, then **Step 3: Implement**.
- [ ] **Step 4: Run them to confirm they pass,** plus every `backend/tests/test_mcp_*.py`.
- [ ] **Step 5: Commit** (run detect_changes first): `feat(mcp): rate, daily-cost, approval and backtest caps`, with the Co-Authored-By line.

---

### Task 16: Internal endpoints and the /health schema hash

**Files:**
- Modify: `backend/api/mcp_routes.py` (3 internal routes; `build_internal_openapi`, `schema_hash`)
- Modify: `backend/api/main.py` (`api_health` payload)
- Test: `backend/tests/test_mcp_internal.py`

**Interfaces:**
- Consumes: `require_mcp_service`, `SERVICE_SCOPE_KEY` (Task 9); `validate_policy`, `canonical_json` (Task 1); `write_audit`, `redact_secrets` (Task 8); auth_utils `get_user_by_username`, `verify_password`, `login_is_rate_limited`, `record_failed_login`, `clear_login_attempts`, `login_rate_limit_config`.
- Produces (`api.mcp_routes`):
  - `build_internal_openapi(app) -> dict` is cached on `app.state.mcp_internal_openapi`. Steps:
    1. Deep-copy `app.openapi()`.
    2. Keep only the operations whose `validate_policy(op.get("x-mcp"))` is not None and whose class is not "never"; drop paths left empty.
    3. Prune `components.schemas` to the transitive closure of the `$ref`s reachable from the kept paths.
    4. Set `"x-mcp-schema-hash" = hashlib.sha256(canonical_json(doc["paths"])).hexdigest()` (contract §4).
  - `schema_hash(app) -> str` returns that same value, from the same function.
  - `AUTH_EVENTS = frozenset({"login_ok", "login_failed", "passkey_failed", "totp_failed", "lockout", "refresh_reuse_revoked", "session_revoked", "enrolled", "disabled", "enabled"})` and `DISCORD_RELAYED_EVENTS = frozenset({"refresh_reuse_revoked", "lockout", "disabled"})`.
  - Routes, all `mcp_policy("never")`, `include_in_schema=False`, `dependencies=[Depends(require_mcp_service)]` (so 401 fires before body validation):
    - `GET /internal/mcp/openapi.json` (`operation_id="mcp_internal_openapi"`) → `build_internal_openapi(request.app)`.
    - `POST /internal/mcp/verify-password` (`operation_id="mcp_internal_verify_password"`), body `{"username": str, "password": str}`. Lowercase and strip the username first.
      - When `login_is_rate_limited(username, "mcp")`: 429 `{"detail": "mcp: too many attempts", "retry_after_s": login_rate_limit_config()[1]}` (plus Retry-After), audit verify_password_limited.
      - When the user is unknown, still run `verify_password(password, _DUMMY_BCRYPT_HASH)` (a module-level `hash_password` of a random string, computed lazily once) so that timing does not reveal whether the user exists.
      - On failure: `record_failed_login(username, "mcp")`, then 401 `{"detail": "invalid credentials"}`, audit verify_password_failed.
      - On success: `clear_login_attempts(username, "mcp")`, then 200 `{"user_id", "username"}`, audit verify_password_ok. Audit rows never contain the password.
    - `POST /internal/mcp/audit-event` (`operation_id="mcp_internal_audit_event"`), body `payload: dict = Body(...)`. `kind != "auth"`, an event outside AUTH_EVENTS, or a non-object `detail` → 400.
      - Every accepted event writes an McpAudit row: outcome "auth_event", `operation_id = "auth_event:" + event`, `args_summary = canonical_json(redact_secrets(detail))` decoded and cut to 2000 chars, client from X-MCP-Client.
      - Events in DISCORD_RELAYED_EVENTS are also relayed (contract §4) through `notifications.notify(category="other", instance_id="mcp", title=f"MCP security: {event}", body=f"MCP SECURITY [{event}] {summary}", discord_channel="notifications")`, best-effort. That is the API's existing outbox path: `live_alerts._safe_enqueue` → DiscordOutbox.
      - Returns 204.
- Produces (`main.api_health`): `"mcp_schema_hash": _mcp_schema_hash()`, where the module-level helper returns `mcp_routes.schema_hash(app)` or None on any exception. It goes into both the 200 and the 503 payloads. No other key changes.

- [ ] **Step 1: Impact.** Run impact (upstream) on `api_health`. Callers are FastAPI only; `tests/test_code_fingerprint.py` reads its `code` key. Report the results.
- [ ] **Step 2: Write the failing tests**

~~~python
def test_internal_routes_refuse_the_public_listener(): ...    # public GET openapi.json -> 401; POST verify-password -> 401
def test_internal_routes_need_no_acting_user():
    assert mcp_client(main.app, acting_user=None).get("/internal/mcp/openapi.json").status_code == 200
def test_kill_switch_closes_internal_routes(monkeypatch): ... # MCP_ENABLED=false -> 401

def test_internal_openapi_excludes_never_and_keeps_x_mcp():
    doc = mcp_client(main.app, acting_user=None).get("/internal/mcp/openapi.json").json()
    assert "/auth/login" not in doc["paths"] and "/app-updates/upload" not in doc["paths"] and "/internal/mcp/openapi.json" not in doc["paths"]
    assert doc["paths"]["/instances/{instance_id}/live-command"]["post"]["x-mcp"] == {
        "class": "money", "tool": "live_command", "target": {"instance": "instance_id"}}
    assert "LoginBody" not in doc.get("components", {}).get("schemas", {})
    expected = hashlib.sha256(json.dumps(doc["paths"], sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False).encode("utf-8")).hexdigest()
    assert doc["x-mcp-schema-hash"] == expected

def test_health_carries_the_same_hash(monkeypatch):   # db_pool.health patched ok; public GET /health
    body = public_client(main.app).get("/health").json()
    assert set(body) == {"status", "postgres", "host", "code", "mcp_schema_hash"}
    assert body["mcp_schema_hash"] == build_internal_openapi(main.app)["x-mcp-schema-hash"]
def test_health_on_mcp_listener_needs_no_acting_user(): ...

def test_verify_password_ok_401_429(monkeypatch, store): ...  # real auth_utils over FakeStore; the limiter key is (username, "mcp"); 429 has retry_after_s
def test_verify_password_unknown_user_still_hashes(monkeypatch): ...  # verify_password called once for an unknown user
def test_audit_event_stores_every_event(store): ...           # "login_ok" -> 204; McpAudit row outcome "auth_event"; no Discord
def test_audit_event_relays_security_events(monkeypatch):     # each of refresh_reuse_revoked, lockout, disabled -> notify called with category "other"
def test_audit_event_rejects_bad_input(): ...                 # kind "x"; event "nope"; detail [] -> 400
~~~

- [ ] **Step 3: Run them to confirm they fail**, then **Step 4: Implement**.
- [ ] **Step 5: Run them to confirm they pass,** plus `test_api_authorization.py test_code_fingerprint.py test_mcp_route_policy.py`.
- [ ] **Step 6: Commit** (run detect_changes first; expected symbols: `api_health` plus the new code): `feat(mcp): internal schema, password check, auth-event relay and health hash`, with the Co-Authored-By line.

---

### Task 17: The :8012 listener, contract write-back, branch verification

**Files:**
- Modify: `backend/api/server_config.py` (prereq module)
- Modify: `backend/api/main.py` (the `if __name__ == "__main__":` block)
- Modify: `docs/superpowers/specs/2026-11-19-intellistock-mcp-contract.md`
- Test: `backend/tests/test_mcp_server_config.py`

**Interfaces:**
- Consumes: prereq `build_uvicorn_config(app, *, host, port, proxy_headers) -> uvicorn.Config` and `run_server(config) -> None`; `McpConfig` (Task 1).
- Produces (`api.server_config`):
  - `build_mcp_uvicorn_config(app, *, port: int) -> uvicorn.Config` = `build_uvicorn_config(app, host="0.0.0.0", port=port, proxy_headers=False)`, then `config.lifespan = "off"` and `config.server_header = False` before returning.
  - `class _NoSignalServer(uvicorn.Server)` overrides `capture_signals` with a `contextlib.contextmanager` that yields without installing handlers; the public server owns the signals.
  - `async def _serve_both(public: uvicorn.Server, mcp: uvicorn.Server) -> None` starts `mcp.serve()` as a task, awaits `public.serve()`, then in `finally` sets `mcp.should_exit = True` and awaits the task.
  - `serve(app, public_config: uvicorn.Config, *, environ: Mapping[str, str] | None = None) -> None`:
    - `cfg = McpConfig.from_env(environ)`.
    - Not `cfg.usable` → log INFO "MCP listener off" and `run_server(public_config)`.
    - `cfg.port == public_config.port` → log ERROR "MCP_API_PORT must differ from API_PORT; MCP listener off" and `run_server(public_config)`.
    - Otherwise `public = uvicorn.Server(public_config)`; `asyncio.run(_serve_both(public, _NoSignalServer(build_mcp_uvicorn_config(app, port=cfg.port))))`; then `raise SystemExit(3)` if `not public.started`.
- `main.py` tail becomes `serve(app, build_uvicorn_config(app, host=API_HOST, port=API_PORT, proxy_headers=True))` (import `serve` alongside the prereq imports). The prereq's `test_main_entrypoint_uses_the_builder` still holds: the tail contains `build_uvicorn_config(` and `proxy_headers=True`, and no `uvicorn.run(`.

- [ ] **Step 1: Impact.** The `__main__` block is module code. Its only caller is compose `api.command ["python", "api/main.py"]`; confirm with grep. If `run_server` or `build_uvicorn_config` differ from the prereq interface quoted above, adapt only the calls inside `serve` and `build_mcp_uvicorn_config`, and note it in the report.
- [ ] **Step 2: Write the failing tests**

~~~python
def test_mcp_config_has_proxy_headers_off_and_lifespan_off():
    c = build_mcp_uvicorn_config(tiny_app, port=8012)
    assert (c.host, c.port, c.proxy_headers, c.lifespan) == ("0.0.0.0", 8012, False, "off")

def test_serve_runs_public_only_when_mcp_is_off(monkeypatch):     # environ {} -> run_server called once with the public config
def test_serve_refuses_mcp_on_the_public_port(monkeypatch):       # MCP_API_PORT == public port -> run_server(public) only; ERROR logged
def test_serve_runs_both_when_enabled(monkeypatch):               # patch _serve_both; mcp server config.port == 8012 and is a _NoSignalServer
def test_serve_both_stops_mcp_when_public_exits():                # fake servers: public returns -> mcp.should_exit True and awaited
def test_no_signal_server_leaves_handlers_alone():
    before = signal.getsignal(signal.SIGINT)
    with _NoSignalServer(build_mcp_uvicorn_config(tiny_app, port=8012)).capture_signals():
        assert signal.getsignal(signal.SIGINT) is before
def test_main_tail_calls_serve():                                 # source tail contains "serve(app, build_uvicorn_config(" and "proxy_headers=True"
~~~

- [ ] **Step 3: Run them to confirm they fail**, then **Step 4: Implement**.
- [ ] **Step 5: Contract write-back.** Make additive edits to the contract file:
  - §3 table rows: 413 `{"detail": "mcp: request body too large"}`; 415 `{"detail": "mcp: JSON body required"}`; 409 `{"detail": "mcp: Idempotency-Key request still in progress"}`.
  - §5: 404 `{"detail": "approval not found"}` for an unknown or foreign id on GET and decision; decision 400 includes a `body_sha256` mismatch; executed vs failed per Decision 8.
  - §4: internal endpoints answer 401 when `MCP_ENABLED` is off.
  - §7 api env: `MCP_MAX_BODY_BYTES=1048576`, `MCP_APPROVAL_EXEC_STALE_S=600`, `RETAIN_MCP_AUDIT_DAYS` (365), `RETAIN_MCP_IDEMPOTENCY_DAYS` (1).
  - §6: "Field lists: docs/superpowers/plans/2026-11-19-mcp-api-gate.md, Task 3."
- [ ] **Step 6: Verify the branch.**
  - Full suite (test command) → no failure beyond the baseline.
  - `~/.local/bin/python3.11 -m compileall -q backend/mcp_policy.py backend/mcp_audit.py backend/mcp_approvals.py backend/mcp_idempotency.py backend/mcp_caps.py backend/api/mcp_gate.py backend/api/mcp_replay.py backend/api/mcp_routes.py backend/api/server_config.py backend/api/main.py backend/api/app_updates.py backend/db/schema.py` → exit 0.
  - `mcp__gitnexus__detect_changes({scope: "compare", base_ref: "fix/api-hardening-mcp-prereqs"})` and `git diff --stat fix/api-hardening-mcp-prereqs` → only the File structure files. Changed existing symbols: `get_current_user`, `api_health`, decorators and docstrings of route handlers, `build_router` (app_updates), and the schema data.
  - Sanity check: with `MCP_ENABLED` unset, `TestClient(main.app)` on a human JWT gives the same JSON as before for `GET /instances` and `GET /health` (minus the new hash key).
- [ ] **Step 7: Commit** (run detect_changes first): `feat(mcp): MCP-only :8012 listener; contract write-back`, with the Co-Authored-By line. Then hand off via superpowers:finishing-a-development-branch: push and open a PR, **never merge**. The PR body carries the Decisions list and the deploy note below.

**Deploy note (PR body):** merging redeploys alpaca-main, so the operator merges outside market hours. Nothing changes until `.env` sets `MCP_ENABLED=true` and a ≥32-byte `MCP_SERVICE_TOKEN`, and build D's compose change puts the api on `mcp_net`. Rollback = unset `MCP_ENABLED`.

---

## Spec / contract coverage map

| Requirement | Task |
|---|---|
| Contract §1 annotation, helper, operation_id, CI coverage test | 1, 4–7 |
| Spec §6 classification, direct tools (23) | 4–7, 10, Appendix A |
| Contract §2 listener, principal, kill switch, token on :8011 → 401 | 9, 17 |
| Gate on all :8012 routes incl. ones without get_current_user | 9, 11 |
| Contract §3 never/202/428/409/429, replay header | 9, 11, 14, 15 |
| Money state, protected targets, fail closed | 2, 11 |
| Contract §5 approvals (list/get/decision/devices), push, expiry | 10, 12, 13 |
| Signed decision (ECDSA P-256, exact message, ±60 s), CAS, exactly-once in-process replay | 13 |
| Contract §6 tables, count 159 → 163 | 3 |
| Caps incl. one concurrent backtest and daily cost budget | 15 |
| McpAudit for every MCP call, 365-day retention | 3, 8, 9, 11, 16 |
| Contract §4 internal endpoints, auth-event Discord relay, /health hash | 16 |
| Contract §7 env, defaults | 1, 17 |

## Appendix A — route classification (origin/main 4420fea6: 255 `@app` routes + 6 `app_updates` routes)

Columns: `target` is the `mcp_policy(target=...)` argument; `cost` true means `cost=True`; **+doc** means "add a one-line docstring". The operation_id is the tool name when there is one, otherwise the handler name without `api_`. Totals: read 130, write 28, money 18, irreversible 31, never 54.

Routes added by this plan (Tasks 10, 12, 13, 16):

| operation_id | method path | class | tool |
|---|---|---|---|
| list_pending_approvals | GET /mcp/approvals | read | list_pending_approvals |
| get_approval | GET /mcp/approvals/{approval_id} | read | get_approval |
| decide_mcp_approval | POST /mcp/approvals/{approval_id}/decision | never | |
| register_mcp_approval_device | POST /mcp/approval-devices | never | |
| list_mcp_approval_devices | GET /mcp/approval-devices | never | |
| revoke_mcp_approval_device | DELETE /mcp/approval-devices/{device_id} | never | |
| mcp_internal_openapi | GET /internal/mcp/openapi.json | never | |
| mcp_internal_verify_password | POST /internal/mcp/verify-password | never | |
| mcp_internal_audit_event | POST /internal/mcp/audit-event | never | |


#### Batch 1 (Task 4): platform, auth, chatbot, LLM plumbing, notifications, tickers, app updates

| operation_id | method path | class | tool | target | cost | doc |
|---|---|---|---|---|---|---|
| health | GET /health | read |  |  |  |  |
| help | GET /help | read |  |  |  |  |
| signup | POST /auth/signup | never |  |  |  |  |
| login | POST /auth/login | never |  |  |  |  |
| me | GET /auth/me | never |  |  |  |  |
| list_auth_users | GET /auth/users | never |  |  |  |  |
| create_auth_user | POST /auth/users | never |  |  |  |  |
| get_auth_user | GET /auth/users/{user_id} | never |  |  |  |  |
| update_auth_user | PUT /auth/users/{user_id} | never |  |  |  |  |
| delete_auth_user | DELETE /auth/users/{user_id} | never |  |  |  |  |
| onboarding_state | GET /onboarding/state | read |  |  |  |  |
| onboarding_complete | POST /onboarding/complete | write |  |  |  |  |
| onboarding_reset | POST /onboarding/reset | never |  |  |  |  |
| chatbot_list | GET /chatbot/conversations | read |  |  |  |  |
| chatbot_create | POST /chatbot/conversations | write |  |  |  |  |
| chatbot_get | GET /chatbot/conversations/{conv_id} | read |  |  |  | +doc |
| chatbot_update | PATCH /chatbot/conversations/{conv_id} | write |  |  |  | +doc |
| chatbot_delete | DELETE /chatbot/conversations/{conv_id} | irreversible |  |  |  | +doc |
| chatbot_clear | POST /chatbot/conversations/{conv_id}/clear | irreversible |  |  |  | +doc |
| chatbot_turn | POST /chatbot/conversations/{conv_id}/turn | never |  |  |  |  |
| chatbot_confirm_tool | POST /chatbot/conversations/{conv_id}/confirm-tool | never |  |  |  |  |
| chatbot_tools | GET /chatbot/tools | read |  |  |  |  |
| mcp_tools_list | POST /chatbot/internal/mcp-tools-list | never |  |  |  |  |
| mcp_tool_call | POST /chatbot/internal/mcp-tool-call | never |  |  |  |  |
| mcp_confirm | POST /chatbot/conversations/{conv_id}/mcp-confirm | never |  |  |  |  |
| status | GET /status | read |  |  |  | +doc |
| test_llm_config | POST /llm/test | never |  |  |  |  |
| test_benzinga_sources | POST /benzinga/test | never |  |  |  |  |
| get_notification_prefs | GET /notification-preferences | read |  |  |  | +doc |
| put_notification_prefs | PUT /notification-preferences | irreversible |  |  |  | +doc |
| list_push_devices | GET /push/devices | never |  |  |  |  |
| register_push_device | POST /push/devices | never |  |  |  |  |
| delete_push_device | DELETE /push/devices/{token} | never |  |  |  |  |
| test_notification | POST /notifications/test | never |  |  |  |  |
| tickers | GET /tickers | read |  |  |  | +doc |
| add_ticker | POST /tickers | write |  |  |  | +doc |
| remove_ticker | DELETE /tickers/{symbol} | irreversible |  |  |  | +doc |
| prices | GET /prices | read |  |  |  | +doc |
| history | GET /history | read |  |  |  | +doc |
| ollama_list_models | POST /ollama/list-models | never |  |  |  |  |
| bedrock_list_models | POST /bedrock/list-models | never |  |  |  |  |
| openrouter_list_models | POST /openrouter/list-models | never |  |  |  |  |
| codex_status | GET /codex/status | never |  |  |  |  |
| codex_install_start | POST /codex/install | never |  |  |  |  |
| codex_install_status | GET /codex/install/{job_id} | never |  |  |  |  |
| codex_login_start | POST /codex/login/start | never |  |  |  |  |
| codex_login_status | GET /codex/login/{job_id}/status | never |  |  |  |  |
| codex_login_cancel | POST /codex/login/{job_id}/cancel | never |  |  |  |  |
| codex_logout | POST /codex/logout | never |  |  |  |  |
| claude_auth_status | GET /claude/auth/status | never |  |  |  |  |
| claude_login_start | POST /claude/login/start | never |  |  |  |  |
| claude_login_submit | POST /claude/login/{job_id}/submit | never |  |  |  |  |
| claude_login_status | GET /claude/login/{job_id}/status | never |  |  |  |  |
| claude_login_cancel | POST /claude/login/{job_id}/cancel | never |  |  |  |  |
| claude_models | GET /claude/models | never |  |  |  |  |
| claude_logout | POST /claude/logout | never |  |  |  |  |
| migrate_credentials | POST /admin/credentials/migrate | never |  |  |  |  |
| kalshi_img_proxy | GET /kalshi/img | never |  |  |  |  |
| llm_output_save | POST /llm/outputs | never |  |  |  |  |
| llm_output_get | GET /llm/outputs/{output_id} | read |  |  |  |  |
| llm_usage_summary | GET /llm-usage/summary | read |  |  |  | +doc |
| llm_usage_timeseries | GET /llm-usage/timeseries | read |  |  |  | +doc |
| llm_usage_top_spenders | GET /llm-usage/top-spenders | read |  |  |  | +doc |
| llm_usage_by_backtest | GET /llm-usage/by-backtest | read |  |  |  |  |
| llm_usage_calls | GET /llm-usage/calls | read |  |  |  | +doc |
| llm_usage_health | GET /llm-usage/health | read |  |  |  | +doc |
| app_updates_latest | GET /app-updates/latest (app_updates.py) | read |  |  |  | +doc |
| app_updates_install_status | GET /app-updates/install-status (app_updates.py) | read |  |  |  | +doc |
| app_updates_upload | POST /app-updates/upload (app_updates.py) | never |  |  |  |  |
| app_updates_rollback | POST /app-updates/rollback (app_updates.py) | never |  |  |  |  |
| ota_manifest | GET /ota/m/{token}.plist (app_updates.py) | never |  |  |  |  |
| ota_download | GET /ota/d/{token}/{name} (app_updates.py) | never |  |  |  |  |

#### Batch 2 (Task 5): instances, live control, swing and wheel, config, strategies, models

| operation_id | method path | class | tool | target | cost | doc |
|---|---|---|---|---|---|---|
| list_instances | GET /instances | read | list_instances |  |  | +doc |
| create_instance | POST /instances | money |  | `{"brokerage":"brokerage_id"}` |  | +doc |
| get_instance | GET /instances/{instance_id} | read | get_instance |  |  | +doc |
| edit_instance | PATCH /instances/{instance_id} | money |  | `{"instance":"instance_id"}` |  | +doc |
| delete_instance | DELETE /instances/{instance_id} | irreversible |  |  |  | +doc |
| add_stock | POST /instances/{instance_id}/stocks | money |  | `{"instance":"instance_id"}` |  | +doc |
| remove_stock | DELETE /instances/{instance_id}/stocks/{symbol} | irreversible |  |  |  | +doc |
| start_instance | POST /instances/{instance_id}/start | money | start_instance | `{"instance":"instance_id"}` |  | +doc |
| stop_instance | POST /instances/{instance_id}/stop | money | stop_instance | `{"instance":"instance_id"}` |  | +doc |
| clear_instance_state | POST /instances/{instance_id}/clear-state | irreversible |  |  |  |  |
| readiness_waiver | POST /instances/{instance_id}/readiness-waiver | irreversible |  |  |  |  |
| revoke_readiness_waiver | DELETE /instances/{instance_id}/readiness-waiver | irreversible |  |  |  |  |
| terminate_price | POST /config/terminate-price | irreversible |  |  |  | +doc |
| terminate_discover | POST /config/terminate-discover | write |  |  |  | +doc |
| start_broker | POST /config/start-broker | irreversible |  |  |  | +doc |
| available_strategies | GET /strategies/available | read |  |  |  |  |
| list_strategies | GET /strategies | read | list_strategies |  |  | +doc |
| get_strategy | GET /strategies/{strategy_id} | read | get_strategy |  |  | +doc |
| create_strategy | POST /strategies | write |  |  |  | +doc |
| edit_strategy | PUT /strategies/{strategy_id} | money |  | `{"strategy":"strategy_id"}` |  | +doc |
| preview_strategy_config_change | POST /strategies/{strategy_id}/config-change-preview | read |  |  |  |  |
| delete_strategy | DELETE /strategies/{strategy_id} | irreversible |  |  |  | +doc |
| link_strategy | POST /instances/{instance_id}/link-strategy | money |  | `{"instance":"instance_id"}` |  | +doc |
| unlink_strategy | POST /instances/{instance_id}/unlink-strategy | money |  | `{"instance":"instance_id"}` |  | +doc |
| link_brokerage_to_instance | POST /instances/{instance_id}/link-brokerage | money |  | `{"instance":"instance_id"}` |  |  |
| link_data_brokerage_to_instance | POST /instances/{instance_id}/link-data-brokerage | money |  | `{"instance":"instance_id"}` |  |  |
| list_models | GET /models | read |  |  |  | +doc |
| get_model | GET /models/{model_id} | read |  |  |  | +doc |
| create_model | POST /models | irreversible |  |  |  |  |
| edit_model | PUT /models/{model_id} | irreversible |  |  |  |  |
| test_claude_cli | POST /models/{model_id}/test-cli | never |  |  |  |  |
| delete_model | DELETE /models/{model_id} | irreversible |  |  |  | +doc |
| model_strategies | GET /models/{model_id}/strategies | read |  |  |  | +doc |
| list_instance_backtests | GET /instances/{instance_id}/backtests | read |  |  |  | +doc |
| get_live_state | GET /instances/{instance_id}/live-state | read | get_live_state |  |  |  |
| alpha_predictions | GET /instances/{instance_id}/alpha/predictions | read |  |  |  | +doc |
| alpha_allocations | GET /instances/{instance_id}/alpha/allocations | read |  |  |  | +doc |
| alpha_performance | GET /instances/{instance_id}/alpha/performance | read |  |  |  | +doc |
| alpha_readiness | GET /instances/{instance_id}/alpha/readiness | read |  |  |  | +doc |
| get_account_summary | GET /widget/accounts | read | get_account_summary |  |  |  |
| get_live_trading_logs | GET /instances/{instance_id}/live-logs | read |  |  |  |  |
| live_command | POST /instances/{instance_id}/live-command | money | live_command | `{"instance":"instance_id"}` |  |  |
| get_live_command | GET /live-commands/{command_id} | read |  |  |  |  |
| list_swing_signals | GET /instances/{instance_id}/swing/signals | read | list_swing_signals |  |  | +doc |
| swing_decide_signal | POST /instances/{instance_id}/swing/signals/{signal_id}/decision | money |  | `{"instance":"instance_id"}` |  |  |
| swing_resend_signal | POST /instances/{instance_id}/swing/signals/{signal_id}/resend | money |  | `{"instance":"instance_id"}` |  |  |
| wheel_overview | GET /instances/{instance_id}/wheel | read |  |  |  | +doc |
| swing_regrade_signal | POST /instances/{instance_id}/swing/signals/{signal_id}/regrade | write |  |  | true |  |
| wheel_manual_scan | POST /instances/{instance_id}/wheel/scan | write |  |  | true |  |
| wheel_manual_scan_status | GET /instances/{instance_id}/wheel/scan | read |  |  |  |  |
| swing_calibration | GET /instances/{instance_id}/swing/calibration | read |  |  |  | +doc |
| nexus_config_get | GET /nexus/config/{instance_id} | read |  |  |  |  |
| nexus_config_set | PATCH /nexus/config/{instance_id} | money |  | `{"instance":"instance_id"}` |  |  |
| get_instance_portfolio_history | GET /instances/{instance_id}/portfolio-history | read | get_instance_portfolio_history |  |  |  |

#### Batch 3 (Task 6): backtests, research jobs, nexus, agent, learning, digest, discover, trends

| operation_id | method path | class | tool | target | cost | doc |
|---|---|---|---|---|---|---|
| list_backtests | GET /backtests | read | list_backtests |  |  | +doc |
| create_backtest | POST /backtests | write | create_backtest |  | true | +doc |
| evidence_source_identity | GET /backtest-evidence/source-identity | read |  |  |  |  |
| publish_evidence_matrix | POST /backtest-evidence/matrices | write |  |  |  |  |
| delete_backtest | DELETE /backtests/{backtest_id} | irreversible |  |  |  | +doc |
| stop_all_backtests | POST /backtests/stop-all | irreversible |  |  |  |  |
| stop_backtest | POST /backtests/{backtest_id}/stop | write | stop_backtest |  |  | +doc |
| pause_backtest | POST /backtests/{backtest_id}/pause | write |  |  |  | +doc |
| resume_backtest | POST /backtests/{backtest_id}/resume | write |  |  | true | +doc |
| get_backtest_status | GET /backtests/{backtest_id}/status | read | get_backtest_status |  |  |  |
| get_backtest_summary | GET /backtests/{backtest_id}/summary | read | get_backtest_summary |  |  | +doc |
| backtest_logs | GET /backtests/{backtest_id}/logs | read |  |  |  | +doc |
| tr_job_submit | POST /trend-research/jobs | write |  |  | true | +doc |
| tr_job_status | GET /trend-research/jobs/{job_id} | read |  |  |  | +doc |
| tr_job_stop | POST /trend-research/jobs/{job_id}/stop | write |  |  |  | +doc |
| list_nexus_graph_builds | GET /nexus-graph-builds | read |  |  |  |  |
| get_latest_nexus_graph_build | GET /nexus-graph-builds/latest | read |  |  |  |  |
| get_latest_nexus_graph_build_logs | GET /nexus-graph-builds/latest/logs | read |  |  |  |  |
| get_nexus_graph_build | GET /nexus-graph-builds/{build_id} | read |  |  |  |  |
| get_nexus_graph_build_logs | GET /nexus-graph-builds/{build_id}/logs | read |  |  |  |  |
| graph_backtest_data | GET /backtests/{backtest_id}/graph-data | read |  |  |  | +doc |
| backtest_playback_data | GET /backtests/{backtest_id}/playback-data | read |  |  |  |  |
| backtest_best_per_strategy | GET /backtests/best-per-strategy | read |  |  |  | +doc |
| agent_control_get | GET /agent/control | read |  |  |  |  |
| agent_control_set | POST /agent/control | write |  |  | true |  |
| resume_timer | POST /agent/resume-timer | write |  |  | true |  |
| agent_restart | POST /agent/restart | write |  |  | true |  |
| agent_increment_count | POST /agent/increment-count | never |  |  |  |  |
| agent_results | GET /agent/results | read |  |  |  |  |
| agent_results_post | POST /agent/results | never |  |  |  |  |
| agent_best | GET /agent/best | read |  |  |  |  |
| agent_set_best_post | POST /agent/best | never |  |  |  |  |
| agent_top5_get | GET /agent/top5 | read |  |  |  |  |
| agent_top5_update | POST /agent/top5 | never |  |  |  |  |
| agent_cycle_log_create | POST /agent/cycle-log | never |  |  |  |  |
| agent_cycle_log_update | POST /agent/cycle-log/{log_id}/update | never |  |  |  |  |
| agent_runs | GET /agent/runs | read |  |  |  |  |
| learning_overview | GET /learning/overview | read |  |  |  |  |
| learning_findings | GET /learning/findings | read |  |  |  |  |
| learning_funnels | GET /learning/funnels | read |  |  |  |  |
| learning_observations | GET /learning/observations/{run_id} | read |  |  |  |  |
| learning_purge | POST /learning/purge | irreversible |  |  |  |  |
| learning_engine_status | GET /learning/engine-status | read |  |  |  |  |
| learning_activity | GET /learning/activity | read |  |  |  |  |
| learning_intents | GET /learning/intents | read |  |  |  |  |
| learning_llm_usage | GET /learning/llm-usage | read |  |  |  |  |
| learning_budget | GET /learning/budget | read |  |  |  |  |
| learning_targets | GET /learning/targets | read |  |  |  |  |
| learning_permissions | GET /learning/permissions | read |  |  |  |  |
| learning_hypotheses | GET /learning/hypotheses | read |  |  |  |  |
| learning_approvals | GET /learning/approvals | read |  |  |  |  |
| learning_decide_approval | POST /learning/approvals/{approval_id} | irreversible |  |  |  |  |
| learning_roles | GET /learning/roles | read |  |  |  |  |
| learning_reports | GET /learning/reports | read |  |  |  |  |
| learning_noise_floors | GET /learning/noise-floors | read |  |  |  |  |
| learning_experiments | GET /learning/experiments | read |  |  |  |  |
| learning_outcomes | GET /learning/outcomes/{run_id} | read |  |  |  |  |
| learning_levers | GET /learning/levers | read |  |  |  |  |
| learning_acknowledge | POST /learning/findings/{finding_id}/status | write |  |  |  |  |
| learning_get_control | GET /learning/control | read |  |  |  |  |
| learning_set_control | POST /learning/control | irreversible |  |  | true |  |
| agent_run_force_stop | POST /agent/runs/{log_id}/force-stop | write |  |  |  |  |
| digest_control_get | GET /digest/control | read |  |  |  |  |
| digest_control_set | POST /digest/control | write |  |  | true |  |
| digest_send_now | POST /digest/send-now | write |  |  | true |  |
| discover_control_get | GET /discover/control | read |  |  |  |  |
| discover_control_set | POST /discover/control | write |  |  | true |  |
| nexus_control_get | GET /nexus/control | read |  |  |  |  |
| nexus_control_set | POST /nexus/control | write |  |  | true |  |
| nexus_status | GET /nexus/status | read |  |  |  |  |
| nexus_cache | GET /nexus/cache | never |  |  |  |  |
| nexus_rebuild | POST /nexus/rebuild | irreversible |  |  | true |  |
| nexus_delete_edges | POST /nexus/delete-edges | irreversible |  |  |  |  |
| list_trends | GET /trends | read |  |  |  |  |
| get_trend | GET /trends/{trend_id} | read |  |  |  |  |
| end_trend | POST /trends/{trend_id}/end | irreversible |  |  |  |  |
| delete_trend | DELETE /trends/{trend_id} | irreversible |  |  |  |  |
| list_discovered | GET /discovered | read |  |  |  |  |
| remove_discovered | DELETE /discovered/{instance_id}/{ticker} | irreversible |  |  |  |  |
| backtest_llm_cost | GET /backtests/{backtest_id}/llm-cost | read |  |  |  |  |

#### Batch 4 (Task 7): brokerages, orders, Kalshi, market data, symbols, analysis

| operation_id | method path | class | tool | target | cost | doc |
|---|---|---|---|---|---|---|
| list_brokerages | GET /brokerages | read |  |  |  |  |
| link_brokerage | POST /brokerages | irreversible |  |  |  |  |
| update_brokerage | PUT /brokerages/{brokerage_id} | irreversible |  |  |  |  |
| delete_brokerage | DELETE /brokerages/{brokerage_id} | irreversible |  |  |  |  |
| test_alpaca_brokerage | POST /brokerages/test-alpaca | never |  |  |  |  |
| ensure_ai_alpaca | POST /brokerages/ensure-ai-alpaca | irreversible |  |  |  |  |
| portfolio_history | GET /brokerages/{brokerage_id}/portfolio-history | read |  |  |  |  |
| get_positions | GET /brokerages/{brokerage_id}/positions | read | get_positions |  |  |  |
| brokerage_holding_opens | GET /brokerages/{brokerage_id}/holding-opens | read |  |  |  |  |
| get_orders | GET /brokerages/{brokerage_id}/orders | read | get_orders |  |  |  |
| brokerage_order_book | GET /brokerages/{brokerage_id}/order-book | read |  |  |  |  |
| brokerage_place_order | POST /brokerages/{brokerage_id}/orders | money |  | `{"brokerage":"brokerage_id"}` |  |  |
| brokerage_cancel_order | DELETE /brokerages/{brokerage_id}/orders/{order_id} | irreversible |  |  |  |  |
| brokerage_bot_activity | GET /brokerages/{brokerage_id}/bot-activity | read |  |  |  |  |
| brokerage_discovered | GET /brokerages/{brokerage_id}/discovered | read |  |  |  |  |
| brokerage_trends | GET /brokerages/{brokerage_id}/trends | read |  |  |  |  |
| kalshi_portfolio | GET /brokerages/{brokerage_id}/kalshi/portfolio | read |  |  |  |  |
| kalshi_positions | GET /brokerages/{brokerage_id}/kalshi/positions | read |  |  |  | +doc |
| kalshi_edges | GET /brokerages/{brokerage_id}/kalshi/edges | read |  |  |  | +doc |
| kalshi_clv | GET /brokerages/{brokerage_id}/kalshi/clv | read |  |  |  | +doc |
| kalshi_settlement | GET /brokerages/{brokerage_id}/kalshi/settlement | read |  |  |  | +doc |
| kalshi_fills | GET /brokerages/{brokerage_id}/kalshi/fills | read |  |  |  | +doc |
| kalshi_scan_budget | GET /brokerages/{brokerage_id}/kalshi/scan-budget | read |  |  |  | +doc |
| kalshi_instance_model | GET /brokerages/{brokerage_id}/kalshi/instances/{instance_id}/model | read |  |  |  |  |
| kalshi_kill | POST /brokerages/{brokerage_id}/kalshi/kill | money |  | `{"brokerage":"brokerage_id"}` |  |  |
| kalshi_list_instances | GET /brokerages/{brokerage_id}/kalshi/instances | read |  |  |  |  |
| kalshi_create_instance | POST /brokerages/{brokerage_id}/kalshi/instances | money |  | `{"brokerage":"brokerage_id"}` |  |  |
| kalshi_update_instance | PATCH /instances/{instance_id}/kalshi/config | money |  | `{"instance":"instance_id"}` |  |  |
| kalshi_create_backtest | POST /brokerages/{brokerage_id}/kalshi/backtests | write |  |  | true |  |
| kalshi_list_backtests | GET /brokerages/{brokerage_id}/kalshi/backtests | read |  |  |  | +doc |
| kalshi_backtest_status | GET /kalshi/backtests/{backtest_id}/status | read |  |  |  | +doc |
| kalshi_backtest_results | GET /kalshi/backtests/{backtest_id}/results | read |  |  |  | +doc |
| kalshi_backtest_stop | POST /kalshi/backtests/{backtest_id}/stop | write |  |  |  | +doc |
| kalshi_backtest_delete | DELETE /kalshi/backtests/{backtest_id} | irreversible |  |  |  | +doc |
| kalshi_instance_detail | GET /instances/{instance_id}/kalshi/detail | read |  |  |  |  |
| kalshi_instance_decisions | GET /instances/{instance_id}/kalshi/decisions | read |  |  |  |  |
| kalshi_instance_live | GET /instances/{instance_id}/kalshi/live | read |  |  |  |  |
| kalshi_instance_orders | GET /instances/{instance_id}/kalshi/orders | read |  |  |  |  |
| kalshi_instance_equity | GET /instances/{instance_id}/kalshi/equity | read |  |  |  |  |
| test_kalshi_brokerage | POST /brokerages/test-kalshi | never |  |  |  |  |
| get_market_news | GET /market/news | read | get_market_news |  |  |  |
| brokerage_movers | GET /brokerages/{brokerage_id}/movers | read |  |  |  |  |
| brokerage_nexus_momentum | GET /brokerages/{brokerage_id}/nexus-momentum | read |  |  |  |  |
| brokerage_backfill_queue | GET /brokerages/{brokerage_id}/backfill-queue | read |  |  |  |  |
| brokerage_momentum_watchlist | GET /brokerages/{brokerage_id}/momentum-watchlist | read |  |  |  |  |
| brokerage_trade_contexts | GET /brokerages/{brokerage_id}/trade-contexts | read |  |  |  |  |
| brokerage_nexus_outcomes | GET /brokerages/{brokerage_id}/nexus-outcomes | read |  |  |  |  |
| symbol_search | GET /symbols/search | read |  |  |  |  |
| get_symbol_info | GET /symbols/{symbol}/info | read | get_symbol_info |  |  |  |
| get_symbol_historicals | GET /symbol-historicals | read | get_symbol_historicals |  |  |  |
| move_analysis_settings | GET /analysis/settings | read |  |  |  | +doc |
| move_analysis_put_settings | PUT /analysis/settings | write |  |  |  |  |
| move_analysis_moves | GET /analysis/moves | read |  |  |  |  |
| move_analysis_explain | POST /analysis/moves/explain | write |  |  | true |  |
| move_analysis_portfolio | GET /analysis/portfolio | read |  |  |  |  |
