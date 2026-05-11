# Troubleshooting

If something is wrong on a fresh install, the answer is probably here.
Skim by symptom.

## Install / first boot

### `./install.sh` exits with "Docker daemon is not running"

The Docker engine isn't started yet. On macOS / Windows, open Docker
Desktop and wait for the whale icon to stop animating, then re-run the
script. On Linux: `sudo systemctl start docker` (or whatever your
distro uses).

### `Bind for 0.0.0.0:3000 failed: port is already allocated`

Another process is using the frontend port. Find it:

```bash
# macOS / Linux
lsof -i :3000

# Windows
netstat -ano | findstr :3000
```

Stop the offending process, or override `FRONTEND_PORT=3001` in
`.env` and re-run `./install.sh`. Same trick works for `API_PORT`,
`RETHINKDB_WEB_PORT`, and `DISCORD_BOT_HTTP_PORT`.

### Install hangs at "Waiting for the API to come online"

The API container is up but `/health` isn't responding. Tail the API
logs:

```bash
docker compose logs -f api
```

Most common causes:

- **Backend image build still running.** First-boot image build on a
  slow connection takes 5–10 min. Be patient.
- **RethinkDB or Neo4j not ready.** API depends on both. `docker
  compose ps` should show both as `healthy`. If Neo4j is `starting`,
  wait — Neo4j takes 30–90s to initialise.
- **Stale Neo4j auth.** If you ran `down -v` before re-running install
  but the Neo4j volume name didn't get wiped, the new password from
  `.env` won't take effect. Run `docker compose down -v` again,
  confirm the `neo4j_data` volume is gone (`docker volume ls`), then
  re-install.

### `Neo.ClientError.Security.Unauthorized` on first boot

You changed `NEO4J_PASSWORD` in `.env` *after* the first `docker
compose up`. Neo4j only honours `NEO4J_AUTH` on the very first
container boot. Either:

- Reset to the original password and restart, or
- `docker compose down -v` and reinstall (destructive — wipes the
  graph, will need to rebuild).

## Login / auth

### "Invalid credentials" with the install-printed admin password

Three things to check:

1. The install script prints the password once. If you missed it,
   it's also in `.env` as `DEFAULT_ADMIN_PASSWORD`.
2. The default admin user is created on first backend boot. If the
   backend container hadn't started yet when you tried to log in,
   wait 30 seconds and retry.
3. If you've rotated the password by editing `.env`, you need to
   `docker compose restart backend` for the new value to take effect.

### Signup fails with "Invalid signup key"

`/auth/signup` requires `SECRET_AUTH_KEY` to be passed in the request.
The install script generates one and stores it in `.env`. Either:

- Pass it in your signup payload (the UI does this automatically; if
  you're using `curl` directly, you need to include it), or
- Use the auto-provisioned admin account and create users from there.

### JWT expired errors after a long session

JWT tokens have a fixed lifetime. The frontend refreshes them
automatically; if you see this error in the UI, log out and back in.

## Runtime

### Instance starts, then immediately stops

Most common: bad strategy config or missing brokerage credentials.
Check the per-instance log:

```bash
docker compose logs -f backend
# look for: "instance <id> exited with code <N>"
```

Then drill into the instance log volume:

```bash
docker run --rm -v live_trading_logs:/logs alpine cat /logs/instance-<id>.log
```

Typical causes: `keyError` for a config field you didn't set, or a
401 from Alpaca because the brokerage row is encrypted with a stale
`INTELLISTOCK_CRED_KEY`.

### Backtest stuck at 0% forever

The backtest engine spawns ephemeral containers via the Docker
socket. Check:

1. `docker compose ps backtest-engine` — is it running?
2. `docker ps -a | grep backtest-` — did it spawn the ephemeral
   container? If not, the engine couldn't reach the Docker socket
   (selinux / podman / restricted CI environments).
3. `docker compose logs -f backtest-engine` — look for OHLCV preflight
   failures. Polygon rate limits, missing API keys, or symbols not in
   Polygon's universe will all stall the preflight.

### Chatbot says "No model configured"

You haven't added an LLM to the Models tab yet. Even though the
install script sets up the platform, LLM credentials are managed
through the UI (Models tab) or the CLI:

```bash
docker compose exec backend python cli.py model add
```

Pick a provider, paste an API key. The chatbot picks up the model on
the next message.

### Graph Nexus build stalls on phase X

Each phase has its own failure modes:

| Phase | Likely cause                                | Fix                                                        |
| ----- | ------------------------------------------- | ---------------------------------------------------------- |
| 1     | Polygon rate limit / missing API key        | Set `POLYGON_API_KEY` in `.env`; pause and retry           |
| 3     | SEC EDGAR rate limit (10/sec hard ceiling)  | Engine handles backoff; just wait                          |
| 7     | 13F-HR XML parsing error                    | Single bad filing; engine should skip and continue         |
| 8     | USASpending 429 burst                       | Backoff cooldown will resume after 1–3 min                 |
| 9     | Wikidata SPARQL timeout                     | Increase `WIKIDATA_TIMEOUT_S` in `.env`                    |
| 10    | PatentsView 403 (no API key)                | Set `PATENTSVIEW_API_KEY` or skip Phase 10 in run scope    |
| 11    | LLM validator returning garbage             | Check the LLM provider's status; lower the validator pass  |

Re-run a single phase:

```bash
docker compose exec backend python cli.py nexus run --phases 8
```

### Neo4j running out of memory during a build

The default 4 GB heap is enough for a `us`-scope build (~3,500
companies). For `global` scope (~10,000), bump it:

```env
NEO4J_HEAP_MAX_SIZE=8G
```

Restart Neo4j (`docker compose restart neo4j`). For systems below 16
GB total RAM, you may need to drop to a smaller scope or run phases
in batches via `GRAPH_NEXUS_PHASE_START` / `GRAPH_NEXUS_PHASE_END`.

### Discord bot doesn't respond

`DISCORD_BOT_TOKEN` is empty in `.env`. The bot service starts
unconditionally but no-ops without a token. Either:

- Add a token via the [Discord Developer Portal](https://discord.com/developers)
  and restart `docker compose restart discord-bot`, or
- Don't run the bot — it's optional. `docker compose stop discord-bot`
  to keep it down.

## Database

### Lost the `INTELLISTOCK_CRED_KEY`

There's no recovery path. Encrypted brokerage credentials are
unreadable without the key — that's the point. You'll need to:

1. Generate a new key in `.env`.
2. Restart the backend.
3. Manually re-link every brokerage account (the UI flow handles this
   transparently — the old encrypted blob will fail to decrypt and
   the user will be re-prompted for the credentials).

The user data, instances, strategies, and backtests all survive.

### RethinkDB query returns "Cannot connect to server"

The RethinkDB container is down. `docker compose ps rethinkdb` —
likely OOMKilled if you've run several heavy backtests. Bump the
memory limit in `docker-compose.yml`:

```yaml
rethinkdb:
  deploy:
    resources:
      limits:
        memory: 6G  # was 3G
```

### Neo4j browser shows "Connection refused"

Port 7474 (HTTP) is the browser. Port 7687 (Bolt) is the driver. If
the browser works but the strategy says "connection refused", you've
got a port-binding issue inside the Docker network. Inside compose,
use `bolt://neo4j:7687`, not `bolt://localhost:7687`.

## Where the logs live

| Want to see                | Run                                           |
| -------------------------- | --------------------------------------------- |
| API HTTP errors            | `docker compose logs -f api`                  |
| Instance-spawn failures    | `docker compose logs -f backend`              |
| Backtest preflight + harness errors | `docker compose logs -f backtest-engine` |
| Live-trading per-instance log | `live_trading_logs` named volume → `instance-<id>.log` |
| Backtest per-run log       | `backtest_logs` named volume → `backtest-<id>.log`     |
| Graph Nexus per-build log  | `nexus_graph_logs` named volume → `nexus-<build_id>.log` |
| Discord bot                | `docker compose logs -f discord-bot`          |
| RethinkDB internals        | `docker compose logs -f rethinkdb`            |
| Neo4j internals            | `docker compose logs -f neo4j`                |

To read a named volume from outside the container:

```bash
docker run --rm -v live_trading_logs:/logs alpine cat /logs/instance-42.log
```

## When all else fails

1. **Kill it with fire**: `docker compose down -v && ./install.sh`.
   Wipes the database, reinstalls. You lose user data but get a clean
   slate.
2. **Open an issue** with: your `docker compose ps` output, the
   relevant `docker compose logs --tail 200 <service>`, and your
   `.env` *with secrets redacted*. Don't paste real API keys.
