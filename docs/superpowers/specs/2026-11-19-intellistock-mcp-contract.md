# IntelliStock MCP — interface contract

Companion to 2026-11-19-intellistock-mcp-server-design.md. This file pins the wire
contract between the three builds so they can be implemented in parallel:
(C) API gate in backend/api, (D) MCP server in backend/mcp_server, (E) iOS approvals.
If a build needs to change anything here, change this file in the same PR.

## 1. Route policy annotation (C defines, D reads from the schema)

Every route decorator in backend/api/main.py (and every APIRouter route) carries:

    operation_id="<snake_case, <= 64 chars, unique>"
    openapi_extra={"x-mcp": {...}}

x-mcp object:

| key    | type                                                | required |
|--------|-----------------------------------------------------|----------|
| class  | "read" \| "write" \| "money" \| "irreversible" \| "never" | yes |
| tool   | string, snake_case, <= 64 chars — direct MCP tool name | no   |
| target | {"instance": "<path-param-or-body-field>"} or {"brokerage": "<...>"} or {"strategy": "<...>"} | money routes: yes |
| cost   | true                                                | no       |

Python helper (C, backend/mcp_policy.py): mcp_policy(cls, *, tool=None, target=None,
cost=False) -> dict returns {"x-mcp": {...}} for openapi_extra, validating values.

The class of every route is in C's plan (docs/superpowers/plans/2026-11-19-mcp-api-gate.md,
Appendix A). One ruling there changes what D sees: POST /learning/control is
"irreversible" with cost: true, not "write". It can switch self-learning to autonomous
live trading and start LLM spend, so every MCP call to it waits for approval.

## 2. MCP listener and principal (C provides, D uses)

- The api process serves the same FastAPI app on two listeners:
  - :8011 (API_PORT) — public, behind Traefik, unchanged.
  - :8012 (MCP_API_PORT) — MCP only. proxy_headers=False.
- A request is the MCP principal only if ALL hold:
  - it arrived on the :8012 listener (scope["server"][1] == MCP_API_PORT);
  - its raw socket peer IP is inside MCP_ALLOWED_CIDR (default 172.31.250.0/24, the
    fixed subnet of the compose network mcp_net);
  - Authorization: Bearer <MCP_SERVICE_TOKEN> (hmac.compare_digest; token >= 32 bytes);
  - X-MCP-Acting-User names an existing user id;
  - MCP_ENABLED is true (default false — the kill switch is "unset it").
- Any request on :8012 that fails these → 401. The service token on :8011 → 401.
  On :8012 the body is {"detail": "mcp: unauthenticated"}; on :8011 the service token
  gets the same 401 as a bad JWT.
- The :8012 listener binds IPv4 only (0.0.0.0). The peer check reads the raw socket
  peer and refuses an IPv4-mapped IPv6 address (::ffff:172.31.250.7), which a
  dual-stack bind would hand it. The listener starts only when MCP_ENABLED is true,
  MCP_SERVICE_TOKEN is at least 32 bytes and MCP_ALLOWED_CIDR parses. MCP_API_PORT
  must differ from API_PORT: when they are equal, the API logs one ERROR naming both
  ports and starts the public listener alone, and the gate treats every request on
  that port as public (the service token there gets the bad-JWT 401). :8012 runs with lifespan off (startup hooks run once, on
  :8011), sends no Server header, and stops when the public listener stops.
- Principal dict returned by get_current_user for MCP requests:
  {"id": <user id>, "username": <username>, "via": "mcp", "client": <X-MCP-Client or "jarvis">}
  Human principals are unchanged ({"id", "username"}; no "via" key).
- Request headers D sends on every call:
  Authorization, X-MCP-Acting-User, X-MCP-Client: jarvis, X-MCP-Correlation-Id: <uuid4>,
  and Idempotency-Key: <1..128 chars [A-Za-z0-9._:-]> on every non-read call.

## 3. Gate responses (C produces, D consumes)

For the MCP principal only:

| condition | status | body |
|---|---|---|
| route class never, or no x-mcp | 403 | {"detail": "mcp: operation not available"} |
| needs approval | 202 | {"pending_approval": {"id": str, "summary": str, "money_state": "live"\|"unknown"\|"protected"\|"irreversible", "expires_at": ISO-8601 UTC}} |
| non-read call without Idempotency-Key | 428 | {"detail": "mcp: Idempotency-Key required"} |
| same Idempotency-Key, different request | 409 | {"detail": "mcp: Idempotency-Key reused with a different request"} |
| cap exceeded | 429 | {"detail": str, "retry_after_s": int} (also Retry-After header) |
| body over MCP_MAX_BODY_BYTES | 413 | {"detail": "mcp: request body too large"} |
| non-empty body not sent as application/json, or not JSON | 415 | {"detail": "mcp: JSON body required"} |
| JSON the gate cannot hash (a lone surrogate), or nested deeper than 64 | 415 | {"detail": "mcp: request body must be JSON the gate can process"} |
| same Idempotency-Key while the first call still runs | 409 | {"detail": "mcp: Idempotency-Key request still in progress"} |
| otherwise | route's own response | |

A repeated Idempotency-Key with the identical request returns the stored status and
body, with header X-MCP-Idempotent-Replay: true.

Additions from build C:
- Order of checks: service auth (401), route class (403), body (413, 415), then for
  non-read calls the Idempotency-Key (428, a replay, 409), then caps (429), then the
  approval hold (202). The route runs last.
- The 415 is stricter than FastAPI. On :8012 a missing Content-Type, or
  application/*+json, is refused even where FastAPI would parse the body, and so are
  NaN and Infinity. D always sends application/json.
- Every completed response is stored under its key: 4xx, 5xx and the 202 included. A
  call whose route raises releases the key, so a retry runs. A stored body over
  1048576 bytes is not kept; its replay is the stored status with
  {"detail": "mcp: original response too large to replay"} and the replay header.
- 429 details: "mcp: call rate cap reached", "mcp: write rate cap reached",
  "mcp: a backtest is already running (one at a time)", "mcp: daily cost-route cap
  reached" and "mcp: daily approval-request cap reached" (retry_after_s runs to the
  next UTC midnight). When the day's count cannot be read, the call is refused with
  "mcp: daily cost-route count unavailable" or "mcp: daily approval-request count
  unavailable" and retry_after_s 60.
- The backtest slot (MCP_CAP_CONCURRENT_BACKTESTS, create_backtest only) counts every
  backtest queued or running: BacktestProgress rows with status running, queued,
  pending or paused*, and BacktestInstances rows with status pending, starting or
  running. A granted start also holds the slot in memory until its call returns, for
  at most BACKTEST_HOLD_S = 300 s. A count that cannot be read is a 429.

## 4. Internal endpoints (C provides on :8012 only; MCP principal NOT required —
service token + CIDR + listener are; acting user header optional)

- GET /internal/mcp/openapi.json → 200 OpenAPI 3 JSON containing only operations whose
  x-mcp class != "never", each keeping its x-mcp object. Top-level
  "x-mcp-schema-hash": sha256 hex of json.dumps({"paths": paths, "components":
  components or {}}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
  .encode("utf-8") — paths AND components, so a body/response model change is seen.
- POST /internal/mcp/verify-password {"username": str, "password": str}
  → 200 {"user_id": str, "username": str} | 401 {"detail": "invalid credentials"} |
  429 {"detail": str, "retry_after_s": int}. Uses verify_password + the login limiter
  keyed (username, "mcp").
- POST /internal/mcp/audit-event {"kind": "auth", "event": str, "detail": object}
  → 204. event in: login_ok, login_failed, passkey_failed, totp_failed, lockout,
  refresh_reuse_revoked, session_revoked, enrolled, disabled, enabled.
  The API relays refresh_reuse_revoked, lockout and disabled events to Discord through
  its existing notification path; the MCP container has no webhook of its own.
- GET /health (public, existing) gains "mcp_schema_hash": <same hash> — D polls it every
  60 s and refetches the schema when it changes. On :8012, /health and /internal/mcp/*
  need only the service token + CIDR + listener (no X-MCP-Acting-User); every other
  route on :8012 needs the full MCP principal.
- With MCP_ENABLED off, every :8012 request, /health and /internal/mcp/* included,
  answers 401 {"detail": "mcp: unauthenticated"}. The /internal/mcp/* routes answer the
  same 401 on :8011.
- verify-password: the limiter is keyed (username, "mcp") only, with no per-IP
  bucket. Every MCP login arrives from the one MCP server, so a shared bucket would
  let a spray of made-up names lock the operator out. The 429 detail is
  "mcp: too many attempts", with a Retry-After header. A body without string username
  and password fields is FastAPI's 422.
- audit-event: 400 when kind is not "auth", the event is not in the list, or detail is
  not an object. session_revoked may carry {"count": int} and no session id. The
  Discord relay's summary is cut to 1900 characters (Discord refuses a message over
  2000); the McpAudit row keeps up to 2000.

## 5. Approvals (C provides; E is the human client; D reads status)

Public listener, human JWT (get_current_user, "via" absent):
- GET /mcp/approvals?status=pending|all&limit=50 → {"approvals": [Approval]}
- GET /mcp/approvals/{id} → Approval
- POST /mcp/approvals/{id}/decision
  body {"decision": "approve"|"deny", "body_sha256": str, "ts": int (unix seconds),
        "device_id": str, "signature": base64(DER ECDSA P-256 SHA-256)}
  → 200 Approval (after execution when approved) | 400 bad input | 403 bad signature /
  unknown or not-yet-active device / MCP principal | 409 not pending (already decided,
  expired, executed) | 410 expired.
  Signed message bytes: UTF-8 of f"{id}|{body_sha256}|{decision}|{ts}". |now - ts| <= 60.
  404 {"detail": "approval not found"} for an unknown, malformed or foreign id, on GET
  /mcp/approvals/{id} and on the decision alike: only the user an approval was created
  for can see or decide it. The 400 includes a body_sha256 that is not the approval's.
  409 {"detail": "mcp: disabled"} when MCP_ENABLED is off: an approve runs nothing, and
  the approval ends "failed" with result {"error": "mcp disabled"} and result_status
  null. A deny still works.
  Outcome of an approve: "executed" when the replayed route answers 2xx (swing's 202
  "uncertain" counts, and its body says so); "failed" for any other answer or a route
  that raises, with result_status recorded. Either is terminal; the call never runs
  again. An approval claimed for execution that has no result after
  MCP_APPROVAL_EXEC_STALE_S reads "failed", with result {"detail": "mcp: execution
  outcome unknown (the API restarted mid-execution); check orders"}.
  GET /mcp/approvals answers 400 for a status other than pending or all, and clamps
  limit to 1..200. DELETE /mcp/approval-devices/{device_id} answers 404
  {"detail": "device not found"} for an unknown, foreign or already revoked device.
- POST /mcp/approval-devices {"name": str, "public_key": base64(X9.62 uncompressed
  P-256 point, 65 bytes)} → 201 {"device_id": str, "active_at": ISO-8601 UTC (now+10 min)}
  Sends a Discord alert. GET /mcp/approval-devices → {"devices": [...]};
  DELETE /mcp/approval-devices/{device_id} → 204.
- Ids: approval ids and device ids match ^[A-Za-z0-9_-]{1,128}$ (uuid4().hex). Device
  object: {"device_id", "name", "created_at", "active_at"} (ISO-8601 UTC); GET returns
  {"devices": [Device]}.
- All /mcp/approvals/* and /mcp/approval-devices* routes are x-mcp class "never" except
  GET /mcp/approvals and GET /mcp/approvals/{id}, which are class "read" with tools
  list_pending_approvals and get_approval (MCP principal sees only approvals it created).

Approval object:
    {"id", "created_at", "expires_at", "status": "pending"|"approved"|"denied"|"expired"|
     "executed"|"failed", "summary", "money_state", "client", "method", "path",
     "query": object, "body": object|null, "body_sha256", "result_status": int|null,
     "result": object|null, "decided_at": str|null, "device_id": str|null}
"result" is stored raw (capped at 256 KB, with "truncated": true when cut); the API does
no redaction. D redacts every result — including approval results — before Jarvis sees
it. The human (E) sees results unredacted, as they would in the app.

body_sha256 = sha256 hex of canonical JSON of {"method", "path", "query", "body"}
(sort_keys, separators (",", ":"), ensure_ascii False).

Secrets at rest: when an approval reaches a terminal status (denied, expired, executed,
failed), its secret-named body fields become "[redacted]" in the stored row, and
body_sha256 is kept. A terminal Approval object therefore need not re-hash to its
body_sha256. A pending row is never altered, so while a decision is possible it always
re-hashes.

Push (C → APNs via apns_sender.send_to_user): category "MCP_APPROVAL",
title "Jarvis wants to act", body = summary, data {"approval_id": id}.

Expiry: 15 minutes (MCP_APPROVAL_TTL_S=900). A background sweep or lazy check marks
pending → expired.

## 6. Tables (C, backend/db/schema.py registry)

McpApprovals, McpApprovalDevices, McpIdempotency (TTL 24 h), McpAudit (retention 365 d).
Field lists live in C's plan (docs/superpowers/plans/2026-11-19-mcp-api-gate.md,
Task 3); D and E only see them through the endpoints above. Additions to those lists:
- McpApprovals carries the internal field exec_started_at. Approving sets
  exec_claimed_at in the same write as status "approved", and the replay's one
  execution is a compare-and-swap on exec_started_at being null. Neither field is in
  the Approval object.
- McpAudit.status 499 records a client that hung up before its body was complete; no
  response is sent. McpAudit.outcome "rejected" marks the second 415 (JSON the gate
  cannot process).

## 7. Environment

api service: MCP_ENABLED, MCP_SERVICE_TOKEN, MCP_API_PORT=8012,
MCP_ALLOWED_CIDR=172.31.250.0/24, MCP_PROTECTED_INSTANCES=f14-paper,swing-paper,
MCP_PROTECTED_ACCOUNTS=PA37JPXGU196, MCP_APPROVAL_TTL_S=900, cap knobs
(MCP_CAP_COST_PER_DAY=20, MCP_CAP_APPROVALS_PER_DAY=50, MCP_CAP_CALLS_PER_MIN=60,
MCP_CAP_WRITES_PER_MIN=10, MCP_CAP_CONCURRENT_BACKTESTS=1), MCP_MAX_BODY_BYTES=1048576 (the
413 limit), MCP_APPROVAL_EXEC_STALE_S=600 (when a claimed execution with no result reads
"failed"), RETAIN_MCP_AUDIT_DAYS=365 and RETAIN_MCP_IDEMPOTENCY_DAYS=1 (retention of
McpAudit and McpIdempotency). MCP_API_PORT must differ from API_PORT (§2).

mcp service: MCP_API_BASE=http://api:8012, MCP_SERVICE_TOKEN (same value),
MCP_PUBLIC_BASE=https://<server7 tailnet name>:8443, MCP_RP_ID=<server7 tailnet name>,
MCP_DATA_DIR=/data, MCP_JARVIS_CLIENT_ID, MCP_JARVIS_REDIRECT_URI
(http://127.0.0.1:<port>/callback), MCP_TOTP_ENC_KEY (32-byte base64). No Discord
variables: alerts go through POST /internal/mcp/audit-event and the API relays them.

compose: network mcp_net (ipam subnet 172.31.250.0/24); api joins it in addition to its
current networks; mcp joins ONLY mcp_net; mcp publishes "127.0.0.1:8090:8090".
