# IntelliStock MCP server for Jarvis — design

Date: 2026-11-19 · Branch: feat/mcp-server · Status: draft for approval

## 1. Intent

The operator wants their Jarvis agent (jarvis-copilot, a Hermes-agent fork running on
the same Tailscale network as server7) to do anything the operator can do in
IntelliStock, on the operator's behalf, through MCP.

Two properties matter more than any feature:

- **One source of truth.** A route is written once, in backend/api/main.py. The MCP
  surface is derived from the API at runtime. Adding or changing an endpoint never
  means editing a second list.
- **Nothing gets in, nothing gets corrupted.** No one but the operator's Jarvis can
  call it, and no agent mistake or planted instruction can move real money or destroy
  data without the operator's tap on their phone.

## 2. Goals, non-goals, anti-goals

Goals
- Jarvis reaches every operator-level capability of the API, except the short
  never-list in section 6.
- Strong built-in sign-in (OAuth 2.1 + PKCE; password + passkey, authenticator-code
  backup), built to the MCP authorization spec so it can go public later unchanged.
- Live-money and irreversible actions wait for a Face ID-signed approval in the
  IntelliStock iOS app; everything else runs at once.
- Tailscale-only deployment on server7.

Non-goals (this round)
- Public-internet exposure (the sign-in is ready for it; the route is not added).
- Any client other than Jarvis; multi-user roles; dynamic client registration.
- MCP resources, prompts, sampling or elicitation — tools only.
- Replacing the in-app chatbot's 33-tool registry (backend/chatbot/tools.py).
- Approvals on the web frontend.

Anti-goals (failure even if it "works")
- Any path where an agent alone can place a live order, start a live instance, change
  broker keys, waive a readiness gate or delete data.
- Any MCP credential that is accepted by the public API at intellistock-api.<domain>.
- A second hand-maintained endpoint list.
- Secrets (broker keys, LLM keys, tokens, hashes) in any tool result.

## 3. Facts this design rests on

- The API is one FastAPI app (backend/api/main.py), 245 routes, already public behind
  Dokploy's Traefik. Auth is a bcrypt-backed HS256 JWT (get_current_user); there is one
  class of user and no roles, so any token can do everything.
- All 245 request bodies are Pydantic models (16 contain untyped dict fields). No route
  sets operation_id, tags or response_model; 76 have no docstring; 49 default
  operationIds exceed 64 characters. OpenAPI is off in production (API_DOCS_PUBLIC).
- Risk tiers from the route inventory: R0 3, R1 123, R2 7, W1 18, W2 39, X 29, A 26.
- jarvis-copilot's MCP client supports Streamable HTTP with static headers and full
  OAuth 2.1 (discovery, pre-registered client_id, PKCE, refresh, on-disk token store,
  loopback redirect http://127.0.0.1:<port>/callback). It does not support elicitation.
- The iOS app already has Face ID (ios/IntelliStock/Core/Lock/BiometricService.swift),
  swing-signal approval screens, and APNs push (backend/apns_sender.py send_to_user).
- interactive_utils.action_learning_targets already computes a three-state money flag
  per instance (live / paper / unknown) from BrokerageAccounts.alpaca_paper and the
  Kalshi mode.
- Merging to main redeploys the whole stack, including the real-money alpaca-main
  container, so merges go outside market hours. The operator merges PRs themselves.

## 4. Approaches considered

**What Jarvis sees as tools** (operator picked: hybrid)
1. One tool per endpoint (~190). Literal, but ~25-30k tokens of tool definitions ride on
   every Jarvis turn, OpenAI requests have been capped at 128 tools, and auto-converted
   servers this large are known to degrade tool selection. Rejected.
2. Catalog only: find / describe / call. Flat, tiny context; one extra lookup per new
   endpoint.
3. **Hybrid (chosen):** the catalog plus ~20 direct tools for daily work. A route gets a
   direct tool by one annotation on its own decorator — still one source of truth.

**Where the server runs** (my call)
- Mounted inside the api container: least code, but the api container holds the Docker
  socket, the host's Claude login and every secret, so any MCP bug becomes host root.
  Rejected.
- **Separate container (chosen)** that calls the API over a private Docker network with
  its own service credential and has no database, Docker socket or host mounts.

**Library** (my call)
- FastMCP 3.x from_fastapi generates one tool per route — the rejected shape — and
  adds a dependency.
- fastapi-mcp forwards the caller's Authorization header by design (token passthrough,
  forbidden by the MCP spec) and its compatibility with MCP SDK v2 is unclear. Rejected.
- **Official MCP Python SDK v2 (chosen)**, hash-pinned. It provides Streamable HTTP
  (stateless mode), the resource-server pieces (TokenVerifier, protected-resource
  metadata) and authorization-server hooks. Its bearer middleware does not check token
  audience; we check it ourselves.

**Where the approval gate lives** (my call)
- In the MCP server: a compromised MCP container could skip it. Rejected.
- **In the API (chosen):** the API recognises MCP-originated requests and refuses to run
  gated ones without a consumed, signed approval.

## 5. Architecture

    Jarvis (tailnet) ──HTTPS──▶ tailscale serve :8443 on server7
                                   │ 127.0.0.1:8090
                                   ▼
                           intellistock-mcp container
                           ├─ OAuth 2.1 authorization server (login, passkey, TOTP)
                           ├─ MCP resource server  /mcp  (Streamable HTTP, stateless)
                           ├─ catalog built from the API's OpenAPI schema
                           └─ own SQLite volume (auth state only)
                                   │ mcp_net (private Docker network)
                                   ▼  service token + acting-user header
                           intellistock-api, second listener :8012 (mcp_net only)
                           ├─ MCP principal  → approval gate → idempotency → route
                           ├─ approvals, audit, idempotency tables (Postgres via db.store)
                           └─ push to iOS on approval-required
                                   ▲
                       iOS app: Approvals screen, Face ID-signed decision (public :8011)

### 5.1 The API side (backend/api, Postgres via backend/db)

**Route policy annotation — the single source of truth.** Every route declares its MCP
policy on its own decorator, e.g.

    @app.post("/instances/{instance_id}/live-command", operation_id="live_command",
              openapi_extra={"x-mcp": {"class": "money", "tool": "live_command"}})

- class: read | write | money | irreversible | never
  - read, write: run at once for Jarvis.
  - money: needs approval when the target's money state is live or unknown
    (fail closed); runs at once on paper or none.
  - irreversible: always needs approval.
  - never: not in the catalog, and the API rejects the MCP principal on it.
- tool (optional): exposes the route as a direct MCP tool under that name.
- cost (optional, true): the route starts paid work (LLM calls, containers, data pulls) —
  backtests, trend-research jobs, nexus rebuilds, discover/digest/learning/agent
  controls. Cost routes from the MCP principal draw on a daily budget (section caps).
- target (optional): how to find the instance/brokerage whose money state decides a
  money route (path param name, or a body field).
- A CI test fails if any route lacks x-mcp, so every new route forces the decision once,
  at the place it is written. Missing annotation at runtime = treated as never.
- Exposed routes get a stable operation_id (<= 64 chars) and a one-line docstring; these
  become tool names and descriptions. The 76 missing docstrings are filled only for
  exposed routes.

**Internal schema.** The API serves its OpenAPI schema (with x-mcp) at
GET /internal/mcp/openapi.json, on the MCP listener only. The MCP server fetches it at
start and whenever GET /health reports a new schema hash, so a redeployed API is picked
up without touching the MCP code.

**MCP principal.** A second uvicorn listener in the api process binds :8012 on mcp_net
only, with proxy-header rewriting off. A request on that listener carrying the shared
MCP_SERVICE_TOKEN (constant-time compare) and X-MCP-Acting-User becomes
{user, via: "mcp", client: "jarvis"}. The token is rejected on :8011 (the Traefik-facing
listener) regardless of headers, and acceptance is decided by the listener the socket
arrived on, never by forwarded headers. MCP_ENABLED=false in the api env rejects the
principal everywhere (kill switch that works even if the MCP container is compromised).

**Approval gate.** For the MCP principal, a dependency reads the route's x-mcp:
- never → 403.
- money (live/unknown target) or irreversible → create an approval and return
  202 {approval_id, summary, expires_at}; the route body does not run.
- Otherwise run the route.

Approval row (table McpApprovals): id, created_at, expires_at (15 min), user, client,
method, path, query, body, body_sha256, human summary ("BUY 10 TQQQ on alpaca-main —
LIVE"), money_state, status (pending | approved | denied | expired | executed | failed),
result (redacted), decided_at, device_id.

Decision: POST /mcp/approvals/{id}/decision on the public listener, human JWT only (the
MCP principal is refused). The body carries {decision, body_sha256, ts, device_id,
signature}. The signature is ECDSA P-256 from a Secure Enclave key on the operator's
phone, created with biometry-current-set access control, over
(approval_id || body_sha256 || decision || ts). The server verifies against the device's
registered public key, checks ts within 60 s, then compare-and-swaps pending → approved
exactly once and executes the stored request in-process (same route, same body) with a
one-shot "approved:<id>" marker the gate accepts once. Double taps, replays and a body
that changed after the request was made all fail.

Device registration: POST /mcp/approval-devices from the iOS app, with a valid human
JWT plus a fresh Face ID-gated key; registering a new device sends a Discord alert and
takes effect after 10 minutes (so a stolen JWT cannot silently add a device and
approve at once).

Push: apns_sender.send_to_user(category "MCP_APPROVAL") with the summary; tapping opens
the approval.

**Idempotency.** Write, money and irreversible calls from the MCP principal must carry
Idempotency-Key. Table McpIdempotency stores (client, key, method, path, body_sha256) →
response for 24 h; a repeat returns the stored response; same key with a different body
→ 409.

**Caps (MCP principal only).** One backtest running at a time (the operator's standing
rule); at most 20 cost-route calls per day in total (x-mcp cost: true) and 50 approval
requests per day; 60 calls/min, 10 writes/min. Over a cap → 429 with the reset time.
Numbers are config, not code.

**Protected paper targets.** Some paper targets must not change without the operator:
f14-paper runs a frozen forward test, and Alpaca account PA37JPXGU196 belongs to the
swing-paper instance only. A config list (MCP_PROTECTED_INSTANCES,
MCP_PROTECTED_ACCOUNTS — account numbers, not names) makes money-class routes on those
targets, and edits to strategy documents they use, require approval exactly as if
they were live. Defaults: instances f14-paper and swing-paper; account PA37JPXGU196.

**Audit.** Table McpAudit, one row per MCP call, allowed or not: ts, client, user,
operation_id, method, path, args sha256 + redacted summary, idempotency key, outcome,
status, latency, approval_id. Retained one year.

### 5.2 The MCP server (new package backend/mcp_server/, own slim image)

**Transport.** Streamable HTTP at /mcp, stateless JSON responses (no sessions).
TransportSecuritySettings set explicitly: allowed_hosts and allowed_origins = the
server7 tailnet HTTPS origin. Container port published as 127.0.0.1:8090 only;
tailscale serve --https=8443 fronts it with the tailnet certificate. No Traefik labels.
Networks: mcp_net only (it cannot reach Postgres, Neo4j or RethinkDB).

**Authorization server (built in).**
- Metadata: /.well-known/oauth-authorization-server and
  /.well-known/oauth-protected-resource/mcp (RFC 9728); 401s carry
  WWW-Authenticate with resource_metadata.
- Clients: dynamic registration off. Jarvis is pre-registered as a public client with
  one exact redirect URI (http://127.0.0.1:<fixed port>/callback, matching Jarvis's
  oauth.redirect_port). PKCE S256 required; resource indicator (RFC 8707) required and
  must equal the canonical /mcp URL.
- Login: IntelliStock username + password, verified by the API over the MCP listener
  (POST /internal/mcp/verify-password, existing bcrypt + limiter), then a passkey
  assertion (WebAuthn, user verification required, RP ID = server7's tailnet name), or
  a TOTP code as the backup (±1 step, a used step is never accepted twice).
- Enrollment: no open bootstrap. An admin CLI inside the container
  (python -m mcp_server.admin enroll <username>) prints a single-use enrollment link
  valid 10 minutes; the page asks for the password, registers a passkey and shows the
  TOTP QR once.
- Tokens: access tokens are Ed25519-signed JWTs (key on the MCP volume, never shared
  with the API), aud = canonical /mcp URL, iss = MCP base URL, 15-minute expiry,
  sub = user id, client_id = jarvis. Refresh tokens are opaque, stored hashed, rotate on
  every use; reuse of a spent refresh token revokes the whole family and alerts Discord.
  Reuse grace: Jarvis runs several processes (gateway, cron, CLI) that share one token
  file, so a spent refresh token presented again within 120 s of its rotation returns
  the same successor pair (kept encrypted for that window) instead of revoking.
  Absolute session lifetime 30 days.
- Login throttling: per-username and global limits, lockout with backoff, alert on
  bursts.
- Auth state lives in SQLite on the MCP's own named volume (clients, passkey
  credentials, encrypted TOTP secrets, refresh-token families, auth events). Losing the
  volume means re-enrolling; it holds nothing the API needs.

**Resource server.** Every /mcp request: verify signature, iss, aud, exp, client_id; no
session is ever treated as authentication. The verified sub becomes
X-MCP-Acting-User on the API call. The caller's token is never forwarded.

**Tools.**
- Catalog (always present):
  - find_endpoints(query, class?) → operation_id, one-line summary, class, whether
    approval may apply.
  - describe_endpoint(operation_id) → description, input JSON schema
    (path + query + body), class.
  - call_endpoint(operation_id, arguments, idempotency_key?) → validates arguments
    against the schema, calls the API, returns the redacted result, or
    {pending_approval: {id, summary, expires_at}}.
- Approvals: get_approval(id), list_pending_approvals().
- Direct tools (x-mcp tool annotations, ~20, finalised in the plan): list_instances,
  get_instance, get_live_state, get_instance_portfolio_history, get_account_summary
  (widget/accounts), get_positions, get_orders, list_backtests, get_backtest_status,
  get_backtest_summary, create_backtest, stop_backtest, list_strategies, get_strategy,
  get_symbol_info, get_symbol_historicals, get_market_news, list_swing_signals,
  start_instance, stop_instance, live_command.
- Tool annotations (readOnlyHint, destructiveHint, idempotentHint) are set from the
  class for client UX only; enforcement is all server-side.
- Write-class tools require idempotency_key.

**Results.**
- Redaction by exact key name (case-insensitive) — api_key, secret, secret_key, key,
  token, access_token, refresh_token, oauth_token, password, password_hash, passphrase,
  private_key, dsn, odds_api_key, oddspapi_api_key, and any name ending _api_key,
  _secret, _token — plus value patterns (JWTs, sk-…, AKIA…, PEM blocks). Fixtures come
  from real producer output, not assumed shapes.
- Free text (news, LLM output, logs, names) is returned inside a data envelope labelled
  as untrusted third-party content; control characters stripped.
- Size cap 100 KB per result with an explicit truncation note and the parameters to
  page further.
- Errors: 4xx detail passed through, truncated; 5xx replaced with a generic message
  plus a correlation id that appears in the audit row.

**Admin CLI** (docker exec only): enroll, list-sessions, revoke <session>, revoke-all,
disable / enable (all tools return 503), rotate-signing-key.

### 5.3 iOS

- Approvals screen: pending list and detail (summary, money state, exact parameters,
  expiry), Approve / Deny. Each decision is signed by the Secure Enclave key, which
  requires Face ID.
- Push category MCP_APPROVAL opens the detail.
- One-time device registration in Settings.

## 6. Exposure policy

Classes are assigned per route against the code as it stands after rebasing onto
origin/main (252 @app routes plus the app_updates router at the time of writing; the
agent inventory of 245 routes was taken from an older branch). Mapping from the
inventory tiers: R0/R1 → read; R2 → read with redaction; W1 → write; W2 → write or
money (money when the target can be live); X → money or irreversible; A → never.
The CI coverage test is what guarantees no route is missed:

- read: R0/R1 routes, plus R2 log and detail routes with redaction applied.
- write: W1/W2 routes whose effect is reversible and not on a live account.
- money: anything whose target can be a live account — POST /brokerages/{id}/orders,
  /config/start-broker, start/stop instance,
  live-command, swing decision/resend, link-brokerage / link-strategy / PATCH instance,
  strategy edits used by a live instance, model edits used by a live instance, Kalshi
  config and kill.
- irreversible: every DELETE, clear-state, readiness waiver (POST and DELETE),
  brokerage create/update/delete, nexus delete-edges and destructive rebuild,
  learning purge, backtests stop-all, learning approvals.
- never (agent capability is pure risk, or is plumbing):
  - /app-updates/upload and /app-updates/rollback — replacing the iOS build that holds
    the approval key would undercut the approval gate;
  - /auth/* (signup, login, every /auth/users route) — accounts and passwords;
  - /codex/*, /claude/*, /admin/credentials/migrate, onboarding reset — host CLI logins
    and credential migration;
  - /llm/test, /benzinga/test, /ollama|bedrock|openrouter/list-models,
    /brokerages/test-alpaca, /brokerages/test-kalshi — take caller-supplied URLs or keys;
  - /chatbot/internal/*, confirm-tool, mcp-confirm, /chatbot/conversations/*/turn —
    the chatbot's own plumbing and self-approval;
  - /llm/outputs (loopback), /agent/* internal writers, /kalshi/img, push device and
    notification-test routes;
  - the new /mcp/approvals/*/decision and /mcp/approval-devices routes.

## 7. Phase 0 — API fixes that come first

The MCP layer would inherit these; they are also live risks on the public API today.

1. /llm/test and the list-models routes: never pair a saved or server key with a
   caller-supplied base URL; allowlist base URLs per provider.
2. In-app chatbot (backend/chatbot/claude_cli_provider.py): verify live whether the
   spawned Claude Code has Bash/Read/WebFetch under --dangerously-skip-permissions;
   restrict to MCP tools only and build the child environment from an allowlist (today
   it is os.environ minus three keys, so it carries JWT_SECRET, POSTGRES_PASSWORD,
   INTELLISTOCK_CRED_KEY and broker keys). Shipped first, as its own small PR.
3. Login limiter keyed on (username, Traefik's IP): trust forwarded headers only from
   Traefik's address and add a per-IP limit; add a limiter to /auth/signup.
4. /chatbot/internal/* reachable through Traefik: restrict to loopback.
5. Chatbot approvals can execute twice (read-check-write without a version): make the
   pending → approved transition a compare-and-swap.

Noted, not in scope: sliding JWT renewal with no absolute lifetime; no iss/aud on API
JWTs; /health discloses hostnames and file hashes; port 8011 is also published on the
host; the 6-character signup minimum.

## 8. Delivery

Separate PRs, each merged by the operator outside market hours:
1. Phase 0 chatbot lockdown (item 7.2) — first, on its own.
2. Phase 0 remainder (7.1, 7.3–7.5).
3. API side: x-mcp annotations + CI coverage test, MCP listener and principal, gate,
   approvals, idempotency, caps, audit, internal schema route.
4. MCP server package, image, compose service, mcp_net, admin CLI.
5. iOS approvals screen, device registration, signed decisions, push category.
6. Deploy: tailscale serve on server7, Tailscale ACL (the Jarvis node and the
   operator's own devices may reach server7:8443), enroll, add the server to Jarvis
   config.yaml with auth: oauth and a pinned client_id / redirect_port.

## 9. Verification

- Unit: classification coverage; gate (never → 403, money on paper runs, money on live
  → 202, irreversible always 202); approval replay, double approve, changed body,
  expired, bad signature, unregistered device; idempotency replay and 409; caps.
- Auth: wrong audience, wrong issuer, expired, unknown client, wrong redirect URI, no
  PKCE, refresh reuse revokes the family, DCR endpoint absent, TOTP step reuse, service
  token rejected on :8011 even with forged forwarded headers.
- Redaction: fixtures captured from real responses of every R2 route.
- End to end on a local stack (FakeStore or scripts/dev_pg.sh): Jarvis-equivalent MCP
  SDK client signs in, lists tools, reads, starts a paper backtest, requests a live
  action, a test device approves, the action executes once.
- On server7 after deploy: a live-gated action from real Jarvis that the operator
  denies, then one they approve on a paper account.

## 10. Known friction

- Jarvis runs headless and only starts an OAuth flow from an interactive terminal. The
  sign-in (about once every 30 days) is: ssh into the Jarvis host with a TTY, run its
  MCP login, open the printed URL on the operator's Mac or iPhone (which load the
  sign-in page straight from server7's tailnet origin, so the passkey RP ID matches),
  and carry only the final redirect back with the ssh -L port forward Jarvis prints.
  The Tailscale ACL therefore admits the Jarvis node AND the operator's own devices to
  server7:8443 — nothing else.
- Passkeys are bound to the tailnet hostname; going public later means enrolling a
  passkey once more for the new hostname.

## 11. Calls made without the operator (builder check, 2026-11-19)

The operator went autonomous mid-design; these were decided by the designer and are
listed for review: sign-in from the operator's own devices (ACL admits them); 120 s
refresh-reuse grace for Jarvis's multi-process token file; protected paper targets
(f14-paper, swing-paper, PA37JPXGU196) gated like live; app-update upload/rollback
never exposed; a cost flag with a daily budget for paid jobs; signed (Secure Enclave)
approvals rather than UI-only Face ID.

## 12. Left to the builder

SQLite schema, exact redaction list beyond the minimum above, final direct-tool list,
cap numbers, iOS layout, how the schema-hash appears in /health, admin CLI ergonomics.
