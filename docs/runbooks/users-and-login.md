# Users and login

Every account has full access. There is no administrator tier, and no
account is provisioned from the environment — `DEFAULT_ADMIN_USERNAME` and
`DEFAULT_ADMIN_PASSWORD` are gone (2026-09-11).

## First run

A deployment with an empty `Users` table has one open door, and only one:
`POST /auth/users` answers without a session. The instant a single row
exists it refuses again.

In the browser: open the login page, submit anything, and it switches to
**Create the first account** — the backend's 401 carries
`detail.code == "no_users"`, which is the only thing that turns that mode
on. Choose a username and a password of at least 8 characters.

Without a browser:

```bash
curl -sX POST "$INTELLISTOCK_API_URL/auth/users" \
  -H 'Content-Type: application/json' \
  -d '{"username": "...", "password": "..."}'
```

On an existing deployment both come back `401` — nothing to do, you
already have accounts.

## Creating and deleting users

**Users** in the sidebar. Create takes a username and a password (8
characters minimum, entered twice). Delete asks for confirmation.

Two deletes are refused, by the server and greyed out in the UI:

- **your own account** — you would be logged out of a session you are using
- **the last remaining account** — the table would be empty, and the
  bootstrap door would reopen for whoever reaches the login page next

To hand a deployment over, create the new account first, sign in as it,
then delete the old one.

Changing a password (`PUT /auth/users/{id}`) bumps that row's
`token_version`, so every token minted against the old password stops
working immediately — including your own, if you change your own.

## API client credentials

Scripts under `scripts/` authenticate in this order:

| Key | Notes |
|-----|-------|
| `INTELLISTOCK_API_TOKEN` | A bearer token. Skips login entirely. |
| `INTELLISTOCK_API_USERNAME` / `INTELLISTOCK_API_PASSWORD` | An ordinary account, created in the Users tab. |

The backtest engine container uses `AGENT_API_USERNAME` /
`AGENT_API_PASSWORD` first and falls back to the `INTELLISTOCK_API_*`
pair. Give it its own account so revoking it does not log you out.

### The one surviving legacy fallback

`scripts/_api.py` still accepts `DEFAULT_ADMIN_USERNAME` /
`DEFAULT_ADMIN_PASSWORD`, with a deprecation line on stderr. It exists so
a checkout running against a pre-change `.env` keeps working across the
deploy; nothing else in the repo reads those names. Delete the fallback
once every `.env` in use has been renamed.

## After deploying this change

1. **Add to `.env`** (the account must already exist — create it in the
   Users tab first):

   ```
   INTELLISTOCK_API_USERNAME=<an account username>
   INTELLISTOCK_API_PASSWORD=<its password>
   ```

2. **Delete from Dokploy** (and from every `.env`), once step 1 is done
   and a script run has been verified:

   ```
   DEFAULT_ADMIN_USERNAME
   DEFAULT_ADMIN_PASSWORD
   ```

   Nothing reads them any more except the `scripts/_api.py` fallback
   above, so removing them is safe as soon as the new pair is in place.

3. **Existing accounts are untouched.** Passwords, sessions, and the
   legacy `role` field on old rows all survive the deploy; the field is
   simply never read and never written again. The first-run path cannot
   fire on a deployment that has users.

## Login limits behind the proxy

The API counts failed attempts in memory, per process:

| Limit | Default | Keys |
|-------|---------|------|
| Failed logins per username and client IP | 10 per 15 minutes | `LOGIN_RATE_LIMIT_MAX_ATTEMPTS`, `LOGIN_RATE_LIMIT_WINDOW_SECONDS` |
| Failed logins per client IP, across usernames | 30 per 15 minutes, **only once `FORWARDED_ALLOW_IPS` names the proxy** | `LOGIN_RATE_LIMIT_PER_IP_MAX_ATTEMPTS` |
| Signups per client IP | 5 per hour | `SIGNUP_RATE_LIMIT_MAX_ATTEMPTS`, `SIGNUP_RATE_LIMIT_WINDOW_SECONDS` |

A correct password clears that username and IP's count.

### Why `FORWARDED_ALLOW_IPS` must name Traefik

Behind Traefik, every request reaches the API from Traefik's container
address. Uvicorn replaces that address with the real client from
`X-Forwarded-For`, but only for requests arriving from an address listed in
`FORWARDED_ALLOW_IPS`. The default is `127.0.0.1`, which Traefik is not, so
until it is set every client shares one address: anyone's ten wrong guesses
lock the operator out of that username, and the per-IP limit stays off,
because it would lock everyone out at once. The API logs a warning at startup
while the variable is unset.

A request sent straight to the host-published port (8011) does not come from a
listed address, so its `X-Forwarded-For` is ignored. `*`, `0.0.0.0/0`, `::/0`
and hostnames are ignored with a warning: trusting every peer would let such a
caller choose its own address.

Find Traefik's address on the server (both commands are read-only):

```bash
docker inspect dokploy-traefik --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}'
docker network inspect dokploy-network --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
```

Set `FORWARDED_ALLOW_IPS` in the stack environment to Traefik's IP if it is
stable, otherwise to the subnet (comma-separated IPs and CIDRs are accepted).
The subnet is looser: every container on `dokploy-network` can then claim any
client address. After the redeploy the API's startup log reads
`Proxy headers: trusting X-Forwarded-For from <your value>`, and its access
log shows client addresses instead of Traefik's.

### Unlocking

The counts live in the API process. Restarting the api container
(`intellistock-api`) clears every one of them.

## Troubleshooting

**The login page offers to create the first account on a deployment that
has users.** It only does that on the server's `no_users` signal, so the
API is talking to an empty or wrong database. Check `PG_DSN` before
creating anything.

**A script 401s after the deploy.** It is logging in as an account that no
longer exists, or with the old keys and no fallback. Set the
`INTELLISTOCK_API_*` pair to a real account.

**Nobody can log in and the table has rows.** There is no reset path from
the environment by design. Reset a password directly against Postgres with
a bcrypt hash, or delete the rows and let the bootstrap path run again.
