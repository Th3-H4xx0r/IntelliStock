# Claude Code CLI Provider — Operator Setup Guide

This guide walks you through enabling the **Claude Code CLI** provider in IntelliStock so the application can talk to Anthropic's Claude models through your existing Claude Pro or Max subscription, without provisioning an API key.

---

## 1. What this is

The Claude Code CLI provider is a built-in IntelliStock LLM provider that delegates chat completions to the locally-installed `claude` binary on this server. Each call (or each conversation, in the chatbot case) is serviced by a `claude` subprocess running in pure text-only mode: no Bash, no Read/Write, no MCP, no slash commands, no session persistence. From IntelliStock's point of view it behaves like any other LLM provider — input prompt in, completion out — but the cost is paid by your Claude Pro/Max subscription rather than by API credits.

Use this provider when:

- You already have a Claude Pro or Max subscription and want IntelliStock to consume that quota instead of paying for API tokens separately.
- You don't want to provision and rotate an Anthropic API key.
- You are running IntelliStock for yourself (or a small trusted group) — one CC subscription is shared across **all users and all calls on this server**.

Known limits:

- One shared subscription per server. There is no per-IntelliStock-user account isolation.
- No token streaming to the UI. Responses arrive whole.
- No MCP tool bridging. IntelliStock's tools (e.g. `fetch_quote`) are not exposed to Claude through this provider.
- No filesystem/Bash/web tools. They are explicitly disabled at spawn time and cannot be re-enabled via configuration.
- A small startup tax per subprocess spawn. See **Performance notes** below.

---

## 2. Prerequisites

- The `claude` CLI, version **2.1.x or later**.
- **Node.js 18 or later** on the server (needed to `npm i -g` the CLI).
- An **active Claude Pro or Max subscription** on the Anthropic account you will be logging the CLI into.
- Shell access to the server as the same OS user that runs the IntelliStock process. The CLI stores its login credentials under `~/.claude/`, so the login must be performed as that user.

---

## 3. One-time setup on the server

Perform these steps once, as the OS user that runs IntelliStock.

### 3.1 Install the CLI

```bash
npm i -g @anthropic-ai/claude-code
```

If you use `nvm`, make sure you install into the same Node version that the IntelliStock service will resolve `claude` from. If `claude` is not on PATH after installation, locate it with `npm root -g` and either add that directory to PATH or set the model's `cli_path` to the absolute path (see section 4).

### 3.2 Verify the install

```bash
claude --version
```

Expected output (the patch version may differ):

```
2.1.139 (Claude Code)
```

If the version is below 2.1.0, upgrade:

```bash
npm i -g @anthropic-ai/claude-code@latest
```

### 3.3 Log in

The CLI uses an OAuth/device-code flow tied to your Claude.ai account. Run it interactively once:

```bash
claude
```

Follow the prompts. It will print a URL and a one-time code, you complete the login in a browser, and the CLI writes the resulting credentials under `~/.claude/`. You can exit the interactive session (`Ctrl+D` or `/exit`) once you see the prompt — IntelliStock does not need an interactive session, it only needs the credentials to be on disk.

### 3.4 Confirm IntelliStock can use it

Run a non-interactive smoke test as the same OS user that runs the IntelliStock service:

```bash
echo ok | claude -p "say ok" --output-format json
```

A healthy response looks roughly like this (fields elided):

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "result": "ok",
  ...
}
```

The two things to verify:

- `"is_error": true` is **not** present.
- The `result` field does **not** contain the string `"Not logged in"`.

If either check fails, see section 8 (Troubleshooting) before proceeding.

### 3.5 Docker setup (default for IntelliStock)

IntelliStock ships as a `docker-compose` stack — the `api`, `backend`, and `backtest-engine` services all need to invoke `claude`. Two things must happen:

1. **The `claude` binary must be inside the container image.** The provided `backend/Dockerfile` now installs it automatically. To opt out (for a smaller image, if you'll never use this provider) build with `--build-arg INSTALL_CLAUDE_CLI=0` or set `INSTALL_CLAUDE_CLI=0` in your `.env` before `docker compose build`.

2. **The host's `~/.claude/` directory must be bind-mounted into the container** so the in-container CLI sees the OAuth credentials you wrote in step 3.3. The compose file bind-mounts `${CLAUDE_HOST_HOME:-~/.claude}` to `/root/.claude` on the `backend`, `api`, and `backtest-engine` services.

If your `~/.claude/` is at the default location on the host that runs Docker, no further configuration is required. Otherwise (e.g. a sudo install, an OS where `~` does not point to the user that owns the credentials, or Dockploy / similar where the host filesystem differs from your login shell), add an absolute path to `.env`:

```
CLAUDE_HOST_HOME=/home/your-user/.claude
```

Then rebuild and restart so the bind-mount picks up:

```bash
docker compose build backend
docker compose up -d --force-recreate backend api backtest-engine
```

Verify the mount inside a container:

```bash
docker compose exec api ls /root/.claude
docker compose exec api claude --version
docker compose exec api sh -c 'echo ok | claude -p "say ok" --output-format json'
```

The last command should return `"is_error": false` and no `"Not logged in"` text — same as the host check.

**For backtests:** the `backtest-engine` service spawns ephemeral broker containers (one per backtest run). For those to inherit the same claude login, `CLAUDE_HOST_HOME` must be set in `.env`. With it set, the engine forwards the host path when launching each broker container; without it set, child containers cannot reach the login state. The `backend` service does the same for the AI backtesting agent / daily digest / discover engine / Graph Nexus containers it spawns.

**Important caveat:** the `claude` CLI inside the container needs outbound HTTPS to `api.anthropic.com`. If you have an egress firewall, allow that destination.

---

## 4. Adding the model in IntelliStock

Once the CLI is installed and logged in, register a model record so IntelliStock's strategies and chatbot can route to it.

1. Open the IntelliStock UI and navigate to **Settings → Models**.
2. Click **Add Model**.
3. **Provider:** select `Claude Code CLI (subscription)`.
4. **Model:** pick one of the supported model IDs:

   | Model ID | When to pick it |
   |---|---|
   | `claude-sonnet-4-6` | Balanced — sensible default for most workloads. |
   | `claude-opus-4-7` | Best reasoning quality. Slowest. Highest subscription-quota cost per call. |
   | `claude-haiku-4-5` | Fast and cheap. Good for high-volume strategy calls where latency matters more than maximum quality. |

5. **`cli_path`:** leave as `claude`. Only override this if the binary is not on the IntelliStock service's PATH (rare — typically only happens when running under `systemd` with a minimal `PATH=` set in the unit file). If you do override, use an absolute path such as `/usr/local/bin/claude`.
6. **`extra_args`:** optional. See section 5 for the allowlist.
7. Click **Save**.
8. Click **Test connection** on the new row. A healthy result reads:

   ```
   ✓ Logged in, response: ok
   ```

   Any other message — `Claude binary not found`, `Not logged in`, `Subscription quota exceeded`, a timeout — should be resolved before you start routing real traffic to this model. See section 8.

You can register more than one model record pointing at the same provider (e.g. one `claude-sonnet-4-6` for the chatbot and one `claude-haiku-4-5` for a particular strategy). They will all share the same `~/.claude/` login and the same subscription quota.

---

## 5. `extra_args` reference

`extra_args` lets you pass a small, vetted set of `claude` CLI flags through to every subprocess spawned for this model. The provider enforces a **strict allowlist**: any flag not in the list below causes the model save (and any test connection) to fail with HTTP 400.

Supported flags:

- **`--fallback-model <name>`** — When Anthropic's overload signal trips, the CLI automatically retries the call against `<name>`. Typical value: `claude-haiku-4-5`. Recommended for production-like setups so a spike doesn't fail user-facing chatbot turns. The value is constrained to model-alias characters only (alphanumeric plus `._-`).
- **`--effort <low|medium|high|xhigh|max>`** — Thinking-budget control. Higher values let the model reason longer before answering. `medium` is a reasonable default for chatbot turns; strategies that benefit from deeper analysis (e.g. `graph_nexus`) may want `high` or `xhigh`. Values outside the enum are rejected with HTTP 400.
- **`--max-budget-usd <amount>`** — Per-call cost cap, denominated in USD. Pro/Max subscriptions only. If a call would exceed the cap it terminates with an error rather than draining quota. Must be a positive finite number ≤ 1000.

> **Removed:** `--append-system-prompt` is no longer accepted. Allowing arbitrary additions to the system prompt let any user with model-write access bend the assistant's behaviour outside IntelliStock's policy. If you need to customise the assistant's tone or instructions for a specific surface, do it in IntelliStock's system-prompt builder instead.

### Examples

Fallback-only, sensible for chatbot:

```
--fallback-model claude-haiku-4-5
```

Deeper reasoning plus fallback, sensible for `graph_nexus`:

```
--effort xhigh --fallback-model claude-haiku-4-5
```

`=`-style is also accepted:

```
--effort=xhigh --fallback-model=claude-haiku-4-5
```

### Why the allowlist?

The allowlist exists to preserve the provider's safety posture. The provider spawns `claude` with `--tools "" --strict-mcp-config --no-session-persistence --disable-slash-commands` so that the subprocess has **no** filesystem, Bash, MCP, or slash-command capability — it is a pure text-in/text-out function. Allowing arbitrary flags would let an operator (or an attacker who could write to the model config) re-enable any of those by passing, e.g., `--allowed-tools Read`. Every flag in the list above is one that affects routing, budget, or system-prompt content — none of them broaden the subprocess's capability surface.

If you want a capability that isn't on the list, file a request rather than working around the validator.

---

## 6. Performance notes

What to expect, latency-wise:

- **Chatbot, first turn in a conversation:** roughly **+2-4 s** of subprocess startup latency on top of the model's normal inference time. The provider spawns a fresh `claude` process for the conversation, lets it complete its own bootstrap, and then sends the first prompt.
- **Chatbot, subsequent turns:** **no startup overhead.** The provider keeps the per-conversation `claude` process resident and reuses it. Latency is dominated by inference time only.
- **Strategy calls (anything other than the chatbot):** spawn-per-call. Each LLM call pays the **2-4 s startup tax**. For one-shot strategies this is negligible. For strategies that issue many sequential LLM calls inside one run — `graph_nexus`'s daily pass is the headline example — expect an extra **10-30 s** total spread across the run, on top of the minutes the strategy already takes.
- **Idle session eviction:** a chatbot's persistent `claude` process is reaped after **1 hour of idle time** (configurable via `CLAUDE_CLI_IDLE_TTL_SEC`). The next turn in that conversation re-spawns and pays the startup tax again.
- **Shared quota:** all concurrent calls — chatbot turns, strategy calls, background jobs — draw on the **same** Claude Pro/Max subscription. Anthropic enforces the per-account rate limits. If the server runs hot enough to bump those limits, configure `--fallback-model claude-haiku-4-5` on your primary models so peaks fail over instead of failing outright.

Latency is fairly insensitive to model choice for the startup phase — it's CLI bootstrap, not model inference. Switching `claude-opus-4-7 → claude-haiku-4-5` shaves time off the inference phase, not the spawn phase.

---

## 7. Environment variables

All optional, all read once at IntelliStock startup. Defaults are tuned for a typical single-server deployment and most operators will not need to change them.

| Variable | Default | What it controls |
|---|---|---|
| `CLAUDE_CLI_MAX_CONCURRENT` | `10` | Maximum number of `claude` subprocesses the provider will keep alive simultaneously across the whole IntelliStock process. Includes both chatbot conversations and in-flight strategy calls. |
| `CLAUDE_CLI_IDLE_TTL_SEC` | `3600` | How long a persistent chatbot subprocess may sit idle before the sweeper reaps it. Lower this if memory is tight; raise it if your users come back to old conversations often. |
| `CLAUDE_CLI_SPAWN_TIMEOUT_SEC` | `30` | Maximum time the provider will wait for a fresh `claude` subprocess to reach the "ready to accept a prompt" state. If exceeded, the spawn is aborted and the call fails. |
| `CLAUDE_CLI_TURN_TIMEOUT_SEC` | `120` | Maximum wall-clock time for a single completion (prompt-in to result-out). Includes any thinking time `--effort` requests. Raise this if you run long `--effort xhigh`/`max` calls. |
| `CLAUDE_CLI_SWEEPER_INTERVAL_SEC` | `300` | How often the idle-session sweeper wakes up to look for processes past their TTL. You almost never need to change this. |

Set them in the IntelliStock service environment (e.g. systemd `Environment=`, your process manager, or a shell `export` before launching). They take effect on next start.

---

## 8. Troubleshooting

### `Claude binary not found`

The IntelliStock process cannot resolve `cli_path`.

- Confirm `which claude` (or `where.exe claude` on Windows) succeeds **as the OS user that runs IntelliStock**, not as your interactive login user.
- If `claude` is in a directory not on the service's PATH (very common under `systemd`), either add it to the unit's `Environment=PATH=...`, or set the model's `cli_path` to the absolute path.
- After fixing, click **Test connection** again on the model row.

### `Not logged in` / `result` contains `"Not logged in"`

The CLI has no valid credentials on disk for the OS user running the service.

- SSH in as that OS user.
- Run `claude` interactively and complete the OAuth flow.
- Re-run the smoke test from section 3.4.
- Common gotcha: you logged in as your personal user but the service runs as e.g. `intellistock` or `www-data`. Each OS user has its own `~/.claude/` directory.

### `Subscription quota exceeded`

Your Claude Pro/Max account has hit its rolling rate limit.

- Wait. The CLI will report when the limit resets.
- For ongoing relief, set `--fallback-model claude-haiku-4-5` in `extra_args` on your high-volume models so spikes overflow to Haiku rather than failing.
- If this is happening routinely, consider downgrading the model on the noisiest strategy (`claude-opus-4-7 → claude-sonnet-4-6`, or `claude-sonnet-4-6 → claude-haiku-4-5`).

### Subprocess timeouts (turn)

A call exceeded `CLAUDE_CLI_TURN_TIMEOUT_SEC` (default 120 s).

- If you use `--effort xhigh` or `--effort max` on long strategy calls, raise the timeout: `CLAUDE_CLI_TURN_TIMEOUT_SEC=300`.
- If chatbot turns are timing out at the default 120 s, that is unusual — check whether the server is under heavy load, or whether Anthropic is degraded.

### Spawn timeouts

A new subprocess could not reach the ready state within `CLAUDE_CLI_SPAWN_TIMEOUT_SEC` (default 30 s).

- The most common cause is **global concurrency saturation**. If `CLAUDE_CLI_MAX_CONCURRENT` (default 10) processes are already alive, new spawns queue behind them. Check IntelliStock's running-process count for the provider; raise the cap if your hardware can support it; or lower idle TTL so unused chatbot processes are reclaimed faster.
- Less common: the CLI itself is unusually slow to bootstrap on the host (see next item).

### First turn very slow on every conversation

Each new chatbot conversation pays significantly more than the documented 2-4 s spawn tax.

- The most likely cause is **SessionStart hooks** in `~/.claude/` (or `~/.claude/settings.json`) running synchronously during every spawn. Each new subprocess executes them.
- If you don't need those hooks for IntelliStock's calls, run the IntelliStock service with a **stripped `HOME`** dir — a dedicated directory containing only the `claude` credentials file and no `settings.json` / no hook scripts. Symlink the credentials in from your real `~/.claude/` or copy them.
- Less invasive: audit `~/.claude/settings.json` for `SessionStart` hooks and remove the slow ones.

---

## 9. Security notes

A few things worth being explicit about before you put this in front of users.

- The `claude` subprocess runs **as the IntelliStock server's OS user**, with that user's filesystem permissions. It is a normal child process, not a sandbox. If you run IntelliStock as `root` (please don't), the subprocess runs as `root` too.
- The provider spawns the CLI with **all tools disabled**: `--tools ""`, `--strict-mcp-config`, `--no-session-persistence`, `--disable-slash-commands`. The subprocess has no Bash/Read/Write/MCP/web/edit/slash-command surface to invoke. In practice this means model output is the **only** channel by which Claude can affect the world — there is no path for it to read files, run commands, or call IntelliStock's MCP tools.
- The threat model is therefore the **standard LLM threat model** — Claude can return adversarial text, hallucinated facts, or prompt-injection-influenced output. It is **not** the "LLM can read my filesystem" threat model that a tools-enabled Claude Code would warrant, because no such tools are loaded.
- The `extra_args` allowlist (section 5) is **the** mechanism that prevents an operator-side mistake — or an attacker who gains write access to the model config table — from re-enabling tools. Do not work around it. If you find yourself wanting to, that is a signal to file a feature request or use a different provider.
- The `~/.claude/` credentials are a long-lived OAuth token for your Anthropic account. Treat that directory's permissions as you would any other secret on the box (`chmod 700 ~/.claude`, restrict backups, etc.). If the host is compromised the attacker can use your Claude subscription; revoke the session at claude.ai if that happens.

---

## 10. What's NOT supported (yet)

These are deferred — they may arrive in future versions of the provider, but are not available today:

- **Token streaming to the UI.** Responses are returned whole. Chatbot users will see the full answer appear at once rather than being typed out token by token.
- **MCP tool bridging.** Claude in this provider cannot call IntelliStock's tools (`fetch_quote`, etc.). If a strategy needs the model to invoke a tool, it must continue to use an API-key-based provider, or the strategy must do the tool call itself and feed the result back into the prompt.
- **Multi-user isolated CC accounts.** The single `~/.claude/` login is shared by every IntelliStock user. There is no facility for, e.g., billing each user to their own Claude subscription.

If you need any of these today, use the standard Anthropic API-key provider instead — it supports streaming and MCP tool use.

---

## See also

- [`docs/superpowers/specs/2026-05-12-claude-code-cli-provider-design.md`](superpowers/specs/2026-05-12-claude-code-cli-provider-design.md) — full architectural design and rationale for the Claude Code CLI provider, including subprocess lifecycle, session management, and validator internals.
