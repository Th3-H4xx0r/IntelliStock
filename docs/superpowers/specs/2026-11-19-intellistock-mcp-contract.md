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
| otherwise | route's own response | |

A repeated Idempotency-Key with the identical request returns the stored status and
body, with header X-MCP-Idempotent-Replay: true.

## 4. Internal endpoints (C provides on :8012 only; MCP principal NOT required —
service token + CIDR + listener are; acting user header optional)

- GET /internal/mcp/openapi.json → 200 OpenAPI 3 JSON containing only operations whose
  x-mcp class != "never", each keeping its x-mcp object. Top-level
  "x-mcp-schema-hash": sha256 hex of json.dumps(paths, sort_keys=True,
  separators=(",", ":"), ensure_ascii=False).encode("utf-8") of the "paths" object.
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

Push (C → APNs via apns_sender.send_to_user): category "MCP_APPROVAL",
title "Jarvis wants to act", body = summary, data {"approval_id": id}.

Expiry: 15 minutes (MCP_APPROVAL_TTL_S=900). A background sweep or lazy check marks
pending → expired.

## 6. Tables (C, backend/db/schema.py registry)

McpApprovals, McpApprovalDevices, McpIdempotency (TTL 24 h), McpAudit (retention 365 d).
Field lists live in C's plan; D and E only see them through the endpoints above.

## 7. Environment

api service: MCP_ENABLED, MCP_SERVICE_TOKEN, MCP_API_PORT=8012,
MCP_ALLOWED_CIDR=172.31.250.0/24, MCP_PROTECTED_INSTANCES=f14-paper,swing-paper,
MCP_PROTECTED_ACCOUNTS=PA37JPXGU196, MCP_APPROVAL_TTL_S=900, cap knobs
(MCP_CAP_COST_PER_DAY=20, MCP_CAP_APPROVALS_PER_DAY=50, MCP_CAP_CALLS_PER_MIN=60,
MCP_CAP_WRITES_PER_MIN=10, MCP_CAP_CONCURRENT_BACKTESTS=1).

mcp service: MCP_API_BASE=http://api:8012, MCP_SERVICE_TOKEN (same value),
MCP_PUBLIC_BASE=https://<server7 tailnet name>:8443, MCP_RP_ID=<server7 tailnet name>,
MCP_DATA_DIR=/data, MCP_JARVIS_CLIENT_ID, MCP_JARVIS_REDIRECT_URI
(http://127.0.0.1:<port>/callback), MCP_TOTP_ENC_KEY (32-byte base64). No Discord
variables: alerts go through POST /internal/mcp/audit-event and the API relays them.

compose: network mcp_net (ipam subnet 172.31.250.0/24); api joins it in addition to its
current networks; mcp joins ONLY mcp_net; mcp publishes "127.0.0.1:8090:8090".
