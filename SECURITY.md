# Security

This document is the threat model and known-trade-offs reference for
IntelliStock. Read this *before* exposing the platform to the public
internet or running it against a real broker account.

## Reporting a vulnerability

Please open a private security advisory on GitHub
(`Security → Report a vulnerability`) rather than a public issue.
For non-critical findings, a regular issue is fine.

## Threat model

IntelliStock is designed for **single-tenant, single-host, operator-equals-user**
deployment. Every security choice in the codebase optimises for that
shape. If you deploy outside of it (multi-tenant, public signup,
shared host with other workloads), several assumptions become
load-bearing — call them out explicitly when you do.

**No privilege separation between authenticated users.** Every account
created with the signup key is fully trusted: it can manage users (create,
delete, reset any password), brokerages, instances, and live trading. There
is no admin/non-admin tier inside the app. Only provision accounts for people
you would trust with full operator access, and never expose signup to the
public internet.

In scope:
- Brokerage credential confidentiality at rest.
- Order-execution authorisation (no order fires without a strategy
  vote or a confirmed UI action).
- API authentication and JWT integrity.
- Chatbot tool authorisation (no cross-conversation data bleed within
  one install).
- Frontend XSS / supply-chain hygiene.
- Container isolation between instance / backtest workers and the
  host.

Out of scope (explicitly):
- Broker outages, market crashes, exchange-side anomalies.
- Bugs in upstream providers (OpenAI, Alpaca, Kalshi, Binance.US, Polygon).
- Side-channel attacks against the host kernel.
- Operator-side OPSEC (an operator who pastes their `.env` into
  Discord is on their own).

## Upgrading an existing install

If you set up IntelliStock before this security pass and your `.env`
predates `JWT_SECRET` / `NEO4J_PASSWORD` being required, the backend
and `docker compose` will refuse to start. Three values are now
mandatory in `.env`:

- `JWT_SECRET` — 32 random bytes, urlsafe-b64. Generate with
  `openssl rand 32 | base64 | tr '+/' '-_'`. Rotating it logs out every
  active session.
- `NEO4J_PASSWORD` — Neo4j only honours `NEO4J_AUTH` on first boot, so
  if you already have a Neo4j volume, set this to whatever password the
  existing graph uses. New installs: pick any value (the install
  scripts auto-generate one).
- `DEFAULT_ADMIN_PASSWORD` — at least 12 characters. The backend
  refuses to provision the default admin if missing or shorter.

Easiest path: stop the stack, delete `.env`, re-run `./install.sh` (or
`install.ps1`), and copy any provider keys (Benzinga, Polygon, Discord)
back over from your old file. The Fernet `INTELLISTOCK_CRED_KEY` MUST
be carried over verbatim — losing it makes encrypted brokerage
credentials unrecoverable.

## Hardening checklist for production

If you're running this against real money on a real broker, do all of
these *before* the first live trade:

- [ ] Run `./install.sh` (or `install.ps1`) — it auto-generates
      `INTELLISTOCK_CRED_KEY`, `SECRET_AUTH_KEY`, `JWT_SECRET`,
      `DEFAULT_ADMIN_PASSWORD`, and `NEO4J_PASSWORD`. Never commit
      `.env`.
- [ ] Confirm `DEFAULT_ADMIN_PASSWORD` is **not** the literal string
      `changeme` (the backend now refuses to start if it is).
- [ ] Confirm `NEO4J_PASSWORD` is the auto-generated value, not the
      legacy `intellistock` default (compose now refuses to render
      without an explicit value).
- [ ] Set `CORS_ALLOW_ORIGINS` *only* if you serve the frontend on a
      different host than the API. Default empty = no cross-origin
      requests at all.
- [ ] Leave `API_DOCS_PUBLIC=` empty in production — `/docs` and
      `/openapi.json` enumerate every endpoint and parameter.
- [ ] Deploy behind a TLS-terminating reverse proxy (Caddy, nginx,
      Cloudflare). The bundled `nginx.conf.template` in the
      frontend container is HTTP-only.
- [ ] Bind RethinkDB and Neo4j ports to `127.0.0.1` (or the internal
      Docker network only) — never expose 28015, 7474, 7687 to the
      public internet.
- [ ] Use **paper-mode brokerage credentials** for at least one full
      market cycle before pointing the platform at a real
      live-trading account.
- [ ] Read every strategy module in `backend/strategies/` you intend
      to run, and audit any third-party strategies before linking.

## Known trade-offs (deliberate, not bugs)

These are choices the platform makes intentionally; flagged so you
know what you're buying into.

### `INTELLISTOCK_CRED_KEY` propagated to instance / backtest containers

Per-instance broker containers and per-backtest containers receive
`INTELLISTOCK_CRED_KEY` in their environment so they can decrypt their
linked brokerage's Fernet-encrypted credentials. This means **any
strategy module that runs inside an instance or backtest container can
read that env var and decrypt every brokerage row in the database** —
not just its own.

Why we accept it (for now): the architectural fix is to have the
per-instance container fetch its specific decrypted blob via a
token-authenticated callback to the api service, removing the need
for the cred key to leave the orchestrator. That refactor is on the
roadmap; the implementation requires touching the instance startup
path, the credential-service refresh path, and the broker adapter
load path simultaneously, and we'd rather ship it as one coherent
change than partial.

What this means for you:
- Audit every strategy module before linking. Strategy code is
  full-trust today.
- Don't link untrusted third-party strategies to a brokerage with
  real money.
- This is the single biggest reason the platform is positioned as
  *single-tenant operator-equals-user* — public multi-tenant is unsafe
  with this trade-off in place.

### Docker socket exposure

`server.py` and `backtest-engine` mount `/var/run/docker.sock` so
they can spawn instance / backtest containers. Anyone with code
execution in those containers gets host-level container orchestration
rights, which is an escape primitive.

Mitigation today: the backend runs trusted first-party code; only
strategy modules are user-supplied, and they execute in the spawned
*instance* container, not the orchestrator. The orchestrator never
imports strategy modules.

Roadmap: replace the direct socket mount with a small socket-proxy
sidecar that allows only `containers.run` against a fixed image with
a fixed mount/env profile.

### Resource-level IDOR on `{id}` endpoints

Endpoints like `/instances/{id}`, `/backtests/{id}`, `/strategies/{id}`,
`/models/{id}` currently fetch the resource by ID without verifying
`doc.user_id == current_user.id`. In the single-tenant default
deployment this is harmless (one user owns everything). If you create
a second user via `/auth/users` and intend them to be a tenant, **the
isolation isn't there yet** — they can read each other's resources.

Roadmap: centralised `assert_owns(doc, user)` helper applied across
every resource-scoped handler. Until then, treat additional non-admin
users as a CLI/UI convenience, not a security boundary.

### Chatbot tool authorisation

Chatbot tools execute against `current_user`'s connection but most
tools accept resource IDs (e.g. `brokerage_id`) and pass them directly
to the action layer. The action layer doesn't yet verify the resource
belongs to the user. Same single-tenant caveat as above.

The destructive-tool *confirmation* gate is real: destructive tools
pause the orchestration loop server-side and surface a confirmation
prompt. Don't bypass it. Don't write a UI that auto-confirms.

### Discord bot has no UI confirmation gate

If you wire up the optional Discord bot, it accepts the same command
surface as the CLI without UI-side confirmation. Restrict the bot's
guild to your own account only, and set `DISCORD_BOT_API_KEY` for
defence-in-depth.

## What's already in place

These are baseline protections you can rely on:

- **Fernet-encrypted brokerage credentials** at rest.
  `INTELLISTOCK_CRED_KEY` is generated cryptographically and never
  logged; the redaction layer scrubs the key pattern from every log
  line.
- **Bcrypt password hashing** with adequate cost.
- **JWT-bearer auth** on every API call. Tokens are signed with a
  generated `JWT_SECRET`; the backend refuses to mint tokens if the
  secret is missing.
- **Constant-time comparison** for `SECRET_AUTH_KEY` (signup gate)
  via `hmac.compare_digest`.
- **Default admin password fail-fast.** The backend refuses to start
  if `DEFAULT_ADMIN_PASSWORD` is unset or shorter than 12 characters
  on first-time provisioning.
- **CORS closed by default.** `allow_origins` is empty unless you
  explicitly opt in via `CORS_ALLOW_ORIGINS`.
- **OpenAPI / Swagger UI off by default.** Set `API_DOCS_PUBLIC=true`
  to re-enable in development.
- **`require_admin` enforced** on system-control endpoints
  (`/config/*`, `/agent/control` POST, `/nexus/control` POST,
  `/nexus/rebuild`, `/nexus/delete-edges`).
- **Open-redirect guard** on the post-login redirect parameter (only
  same-origin path-style redirects accepted).
- **Strict CSP / security headers** on the bundled nginx
  (Content-Security-Policy, X-Frame-Options, X-Content-Type-Options,
  Referrer-Policy, Permissions-Policy, HSTS).
- **Production source maps off** (`vite.config.js build.sourcemap`).
- **DOMPurify-sanitised chatbot output** with a strict allowlist;
  `target="_blank" rel="noopener noreferrer"` on every link.
- **No `eval` / `exec` / `pickle.load` / `yaml.load` on user input.**
- **Strategy module names sanitised** before `importlib` resolution.
- **Constant-time** Fernet ciphertext / JWT / Discord token / AWS key
  / connection-string redaction in the logger.

## Versions

This document is current as of the commit that ships `SECURITY.md`.
Re-read after major changes — security choices drift.
