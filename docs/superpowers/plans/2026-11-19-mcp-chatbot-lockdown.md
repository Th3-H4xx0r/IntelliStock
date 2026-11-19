# Chatbot CLI Lockdown and Exactly-Once Approvals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The in-app chatbot's spawned Claude Code can reach no built-in tool and no server secret, and a chatbot tool approval runs its tool exactly once however many times, and from however many devices, it is approved.

**Architecture:** Phase 0 items 7.2 and 7.5 of the MCP design, on branch `fix/chatbot-cli-lockdown` in worktree `/Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock/.worktrees/chatbot-lockdown` (origin/main 26cd7546). 7.2 stays inside `backend/chatbot/claude_cli_provider.py`: the MCP-mode argv gains the `--tools ""` flag pure-text mode already passes, a session whose `system:init` event lists a non-MCP tool is killed, and every `claude` spawn gets an environment built from an allowlist. 7.5 adds a compare-and-swap over the conversation document using `store.replace_if` (the primitive behind the swing decision CAS, `swing_trader.signals_store.cas_signal`), and both approval paths claim `pending_confirmation → executing` through it before the tool runs.

**Tech Stack:** Python 3.11, `db.store` / `db.fake.FakeStore`, pytest, Claude Code CLI (stream-json).

**Spec:** `docs/superpowers/specs/2026-11-19-intellistock-mcp-server-design.md` (§7 items 2 and 5) and the binding contract `docs/superpowers/specs/2026-11-19-intellistock-mcp-contract.md`, both in the `.worktrees/mcp-api-gate` worktree. This plan touches none of the contract's wire surface.

## Global Constraints

- Backend code runs on Python 3.11 (CI). Local default is 3.14: no 3.12+ syntax or stdlib. `~/.local/bin/python3.11` exists for a compile check.
- Tests live in `backend/tests`; CI runs `python -m pytest -q -p no:cacheprovider backend/tests` from the repo root. No test touches the network, production, a broker, an LLM or a real `claude` binary. FakeStore is the default (PG_TEST_DSN unset); PG-only tests skip without it.
- Do not edit `backend/api/main.py`, `backend/interactive_utils.py`, `backend/db/schema.py`, `backend/broker.py` or `ios/*` (another session owns them). Nothing here needs them, and no other `backend/db/` file changes either.
- All DB access goes through `from db import store` (plus the predicate builder `from db import P`). No new SQL, no DDL, no `CREATE INDEX`.
- Human (JWT) callers see no behaviour change except: a lost approval race returns the 400 an already-resolved approval returns today; a message reads `"status": "executing"` while its tool runs; conversation docs gain a `rev` key. Status codes and response shapes are unchanged. Real money trades through this API (alpaca-main) and the chatbot can call `submit_live_command`.
- Before editing an existing function: run `mcp__gitnexus__impact({target, direction: "upstream", repo: "IntelliStock"})` and report direct callers, affected processes and risk. GitNexus returns UNKNOWN for this FastAPI-wired code (pre-checked 2026-11-19), so each task also gives the grep that finds the callers. Warn on HIGH/CRITICAL.
- Before each commit: `mcp__gitnexus__detect_changes({scope: "staged", worktree: "/Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock/.worktrees/chatbot-lockdown", repo: "IntelliStock"})`; only the task's own symbols may appear.
- Shell: `grep` may be aliased to ugrep in interactive zsh; use `/usr/bin/grep` in scripts and quote `--include='*.py'` globs.
- Commit messages are conventional and end with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commit only; the operator merges, outside market hours, because a merge redeploys alpaca-main.

## Review Focus

1. **Approve and Decline tapped at once (web and phone).** Exactly one applies: either the tool ran once and no decline was recorded, or it never ran. Pinned in Tasks 5 and 6 (`*_approve_and_decline_apply_exactly_one`).
2. **A chat turn appends while an approval is in flight.** The appended messages survive; today `replace_message` rewrites the whole array and can drop them. Pinned in Task 4 (`test_cas_retries_past_an_interleaved_write_without_losing_it`, PG `test_concurrent_append_survives_a_transition`).
3. **The API dies between claim and finish.** The message stays `executing`, can never be approved again, and the next LLM turn still builds valid history. Pinned in Tasks 5 and 6 (`*_executing_message_never_runs`, `test_llm_history_tolerates_a_stranded_executing_message`).
4. **A newer claude-code (the image installs it unpinned) exposes an always-on tool such as ToolSearch.** The session is refused with a clear error and never runs; pure-text sessions (empty tool list) still work. Pinned in Task 2.
5. **Variables something genuinely needs are not on the allowlist.** Proxy/CA vars, `CLAUDE_CONFIG_DIR`, `LC_*`, the OAuth token file, and an operator-set `INTELLISTOCK_MCP_CONFIRM_WAIT_SEC` still reach their consumer; no secret-shaped name ever does. Pinned in Task 3.

## Facts this plan rests on (verified 2026-11-19)

1. Local `claude` 2.1.328 with today's MCP-mode flags (`--strict-mcp-config --no-session-persistence --disable-slash-commands --dangerously-skip-permissions --allowedTools mcp__*`) listed 24 built-ins in `system:init`, including Bash, Read, Write, Edit, NotebookEdit, WebSearch, Task, CronCreate and ToolSearch. `--allowedTools` only pre-approves; skip-permissions approves everything. The comment at `claude_cli_provider.py:436` ("built-in Bash / Read / Edit / etc. stay disabled") is false.
2. Adding `--tools ""` left only the MCP tool in `system:init` and the model called it; with 61 large MCP tools all 61 were listed and callable (no ToolSearch dependency). The assertion in `test_chat_argv_with_mcp_config_switches_to_allowedtools` that `--tools` "would silently override --allowedTools" is wrong for this CLI.
3. `claude --help`: `--tools <tools...>  Built-in tools to make available: "" for none`. `--tools`, `--mcp-config` and `--allowedTools` are variadic: a following token that does not start with `-` is eaten as another value. `--tools ""` must be followed by a `--flag`.
4. Claude Code hands stdio MCP servers its whole environment overlaid with the `--mcp-config` env block (a canary in claude's env reached the MCP child; a key set to "" in the block overrode the inherited value). So the IntelliStock MCP server inherits JWT_SECRET etc. today too; allowlisting claude's env protects both.
5. Claude authenticated and ran MCP tools with only PATH, HOME, USER, LOGNAME, SHELL, LANG, TERM and TMPDIR set. On server7 auth comes from `CLAUDE_CODE_OAUTH_TOKEN` (env, or `~/.claude/oauth_token` via `_strip_api_key_env`) plus the copied runtime HOME.
6. `intellistock_mcp_server.py` is stdlib-only. It reads `INTELLISTOCK_MCP_URL / _TOKEN / _CONVERSATION_ID / _USER_ID` from the config env block and `INTELLISTOCK_MCP_CONFIRM_WAIT_SEC` by inheritance only (nothing in the repo sets it).
7. Spawn sites building `_strip_api_key_env(os.environ.copy())`: `_spawn` (1674), `call_claude_cli_structured` (2420), `test_claude_cli` (2979), `claude_auth_status` (3142), `claude_logout` (3181), `ClaudeCliLogin.start` (3334). The two `claude --version` runs (2935, 3133) pass no `env` and inherit everything. There are 8 `subprocess.run/Popen` calls in the module.
8. `call_claude_cli_structured` is the strategy LLM path (`llm_utils.py` 1923, 2581): an env regression there breaks LLM-driven strategy decisions, not just the chatbot.
9. Both confirm endpoints (`main.py` 1768 `confirm-tool`, 1874 `mcp-confirm`) are sync `def` (thread pool, so two taps really run in parallel) and map ValueError to 400.
10. `ChatbotConversations` has no `time_fields`, so `doc.updated_at` stays an ISO string and `P.field("updated_at")` compares it. `append_messages` (pre-existing SQL in its owning module) rewrites `updated_at`. `FakeStore.sql` raises, so FakeStore tests stub `append_messages`. Clients only special-case `status == "pending_confirmation"` (web `useChatbot.js:53`, iOS `ChatModels.swift:82`).
11. Baseline: `python3 -m pytest -q -p no:cacheprovider backend/tests/test_claude_cli_provider.py backend/tests/test_claude_cli_login.py backend/tests/test_api_auth_chat_store.py` → 142 passed, 1 skipped.

## File Structure

- `backend/chatbot/claude_cli_provider.py` — argv flag (Task 1), init guard (Task 2), env allowlist + MCP config env block (Task 3).
- `backend/chatbot/conversations.py` — status constants, CAS primitive, `transition_message` (Task 4).
- `backend/chatbot/mcp_bridge.py` — `resolve_mcp_pending` claims before executing (Task 5).
- `backend/chatbot/orchestration.py` — `confirm_pending_tool` claims before executing (Task 6).
- `backend/tests/test_claude_cli_provider.py` — 7.2 tests, beside the `fake_popen` / `manager` fixtures they reuse.
- `backend/tests/test_chatbot_approval_cas.py` — new; all 7.5 tests.

---

### Task 1: MCP-mode spawn gets no built-in tools

**Files:**
- Modify: `backend/chatbot/claude_cli_provider.py:436-467` (comment block + `_MCP_FLAGS_TEMPLATE`), `:1085-1121` (`_build_chat_argv` docstring only)
- Test: `backend/tests/test_claude_cli_provider.py` (class `TestArgvBuilders`; rewrite `test_chat_argv_with_mcp_config_switches_to_allowedtools` at 268-296)

**Interfaces:**
- Produces: `_MCP_FLAGS_TEMPLATE = ["--tools", "", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands", "--dangerously-skip-permissions", "--allowedTools", "mcp__*"]` (exact order; `--tools ""` first so a flag follows it).

- [ ] **Step 1: Impact analysis.** `mcp__gitnexus__impact` on `_build_chat_argv`; then `grep -rn "_MCP_FLAGS_TEMPLATE\|_build_chat_argv" backend --include='*.py'`. Expected callers: `ClaudeCliSessionManager._spawn` (1660) and tests only. Report the blast radius (chatbot claude-cli sessions in MCP mode).

- [ ] **Step 2: Write the failing tests** (in `TestArgvBuilders`; replace the old MCP test with the first one)

```python
def test_chat_argv_with_mcp_config_disables_builtins_and_allows_mcp(self):
    argv = _build_chat_argv(cli_path="claude", model="x", system_prompt="hi",
                            extra_args=[], mcp_config_path="/tmp/test-mcp.json")
    assert argv[argv.index("--mcp-config") + 1] == "/tmp/test-mcp.json"
    assert argv.count("--tools") == 1
    i = argv.index("--tools")
    assert argv[i + 1] == ""
    assert argv[i + 2].startswith("--")          # variadic flag: nothing else may be eaten
    assert argv[argv.index("--allowedTools") + 1] == "mcp__*"
    for flag in ("--strict-mcp-config", "--no-session-persistence",
                 "--disable-slash-commands", "--dangerously-skip-permissions"):
        assert flag in argv
    assert "--permission-mode" not in argv

@pytest.mark.parametrize("mcp_path", [None, "/tmp/test-mcp.json"])
def test_every_chat_mode_disables_builtin_tools(self, mcp_path):
    argv = _build_chat_argv(cli_path="claude", model="x", system_prompt="hi",
                            extra_args=["--effort", "high"], mcp_config_path=mcp_path)
    assert argv[argv.index("--tools") + 1] == ""
```

- [ ] **Step 3: Run to verify failure.** `python3 -m pytest -q -p no:cacheprovider backend/tests/test_claude_cli_provider.py -k "disables"` → the MCP cases fail (`ValueError: '--tools' is not in list`); the pure-text case already passes.

- [ ] **Step 4: Implement.** Prepend `"--tools", ""` to `_MCP_FLAGS_TEMPLATE`. Rewrite the comment above it: built-ins are removed by `--tools ""` (lab note: 2026-11-19, claude 2.1.328 — without it, 24 built-ins incl. Bash/Read/Write/Edit/WebSearch/Task were live under skip-permissions; with it only `mcp__*` tools remain and MCP calls succeed, 61 tools tested); `--allowedTools mcp__*` is kept because it is the lab-verified way MCP calls go through without a permission mode; keep the existing permission-mode notes. Make the `_build_chat_argv` docstring say the same.

- [ ] **Step 5: Run the file.** `python3 -m pytest -q -p no:cacheprovider backend/tests/test_claude_cli_provider.py backend/tests/test_claude_cli_login.py` → all pass.

- [ ] **Step 6: detect_changes, then commit.**

```bash
git add backend/chatbot/claude_cli_provider.py backend/tests/test_claude_cli_provider.py
git commit -F - <<'EOF'
fix(chatbot): give MCP-mode claude-cli no built-in tools

--dangerously-skip-permissions with --allowedTools mcp__* left Bash, Read,
Write, Edit, WebSearch and Task live in every chatbot session. --tools ""
removes the built-ins and keeps the MCP tools (verified on claude 2.1.328).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: Refuse a session whose init event lists a built-in tool

**Files:**
- Modify: `backend/chatbot/claude_cli_provider.py:132-158` (new exception), `:1906-1930` (`ClaudeCliSessionManager._handle_event`)
- Test: `backend/tests/test_claude_cli_provider.py` (new class `TestBuiltinToolGuard`)

**Interfaces:**
- Consumes: `_MCP_FLAGS_TEMPLATE` (Task 1).
- Produces: `class ClaudeCliToolPolicyError(ClaudeCliError)`; `_builtin_tools_in_init(event: Dict[str, Any]) -> List[str]` — sorted names in `event["tools"]` that do not start with `"mcp__"`; `[]` when `tools` is missing or not a list.

- [ ] **Step 1: Impact analysis.** `mcp__gitnexus__impact` on `_handle_event` (file `backend/chatbot/claude_cli_provider.py`); `grep -n "_handle_event" backend/chatbot/claude_cli_provider.py` → only `_stdout_reader_loop`. Report.

- [ ] **Step 2: Write the failing tests.** Two module-level helpers: `_feed_after_write(fake_popen, events)` starts a daemon thread that polls (up to 2 s) until `fake_popen` is non-empty AND `fake_popen[-1].stdin.getvalue()` is non-empty (the turn slot is registered before the stdin write), then calls `feed_event` for each event; `_send(manager)` is `manager.send_turn(conversation_id="conv-guard", messages=[{"role": "user", "content": "hi"}], system_prompt="sys", model="claude-haiku-4-5", cli_path="claude", extra_args=[])`.

```python
def _init(tools):
    return {"type": "system", "subtype": "init", "tools": tools}

def test_init_with_a_builtin_tool_refuses_the_session(self, fake_popen, manager):
    _feed_after_write(fake_popen, [_init(["Bash", "mcp__intellistock__list_instances"])])
    with pytest.raises(ClaudeCliToolPolicyError, match="Bash"):
        _send(manager)
    assert fake_popen[-1]._killed is True

def test_init_with_only_toolsearch_extra_is_refused(self, fake_popen, manager):
    _feed_after_write(fake_popen, [_init(["ToolSearch", "mcp__intellistock__list_instances"])])
    with pytest.raises(ClaudeCliToolPolicyError, match="ToolSearch"):
        _send(manager)

@pytest.mark.parametrize("tools", [["mcp__intellistock__list_instances"], [], None])
def test_init_with_mcp_only_empty_or_missing_tools_is_accepted(self, fake_popen, manager, tools):
    init = _init(tools) if tools is not None else {"type": "system", "subtype": "init"}
    _feed_after_write(fake_popen, [init, _make_result("ok")])
    assert _send(manager)["content"] == "ok"
    assert fake_popen[-1]._killed is False

def test_builtin_tools_in_init_helper(self):
    assert provider._builtin_tools_in_init(_init(["Read", "mcp__a__b", "Bash"])) == ["Bash", "Read"]
    assert provider._builtin_tools_in_init({"type": "system", "subtype": "init", "tools": "Bash"}) == []
```

- [ ] **Step 3: Run to verify failure.** `python3 -m pytest -q -p no:cacheprovider backend/tests/test_claude_cli_provider.py -k "Guard"` → ImportError / AttributeError on `ClaudeCliToolPolicyError`.

- [ ] **Step 4: Implement.** In `_handle_event`, before the fall-through comment: when `etype == "system"` and `event.get("subtype") == "init"`, when `tools` is a list, log `f"[{conv[:8]}] CC init tools: {sorted(tools)}"` (cyan, names only; the post-deploy check greps `CC init tools`). If `_builtin_tools_in_init(event)` is non-empty: log red, `self._fail_pending(sess, ClaudeCliToolPolicyError(f"claude-cli exposed built-in tools {bad}; session refused (only mcp__* tools are allowed)"))`, set `sess.state = SessionState.CRASHED`, `self._kill_process(sess)`. `_fail_pending` sets the error first, so the later EOF crash error does not overwrite it, and `send_turn` does not retry (not a `ClaudeCliCrashError`).

- [ ] **Step 5: Run.** Same command as Step 3, then the whole file → all pass.

- [ ] **Step 6: detect_changes, then commit** (`git add` the two files) with message `fix(chatbot): refuse a claude-cli session that reports a built-in tool` and a body: the image installs claude-code unpinned, so the flag alone could regress on a CLI upgrade; the guard fails closed. End with the Co-Authored-By line.

---

### Task 3: Build every claude child environment from an allowlist

**Files:**
- Modify: `backend/chatbot/claude_cli_provider.py` — comment at 355-371 and `_strip_api_key_env` docstring (398-409; no `os.environ.copy()` text may remain), new constants + `_claude_child_env` right after `_strip_api_key_env`, `_write_session_mcp_config` (993-1038), and the spawn sites in Fact 7 (1674, 2420, 2935, 2979, 3133, 3142, 3181, 3334)
- Test: `backend/tests/test_claude_cli_provider.py` (new class `TestChildEnvAllowlist`)

**Interfaces:**
- Consumes: `_strip_api_key_env(env) -> Dict[str, str]` (unchanged; still adds the token-file token).
- Produces:
  - `_CHILD_ENV_KEYS: frozenset` = PATH, HOME, USER, LOGNAME, SHELL, TERM, TZ, LANG, LANGUAGE, TMPDIR, TMP, TEMP, XDG_CONFIG_HOME, XDG_CACHE_HOME, XDG_DATA_HOME, XDG_STATE_HOME, XDG_RUNTIME_DIR, CLAUDE_CODE_OAUTH_TOKEN, CLAUDE_CONFIG_DIR, HTTP_PROXY, HTTPS_PROXY, NO_PROXY, http_proxy, https_proxy, no_proxy, SSL_CERT_FILE, SSL_CERT_DIR, NODE_EXTRA_CA_CERTS, PYTHONIOENCODING, PYTHONUTF8, SYSTEMROOT, WINDIR, COMSPEC, PATHEXT, USERPROFILE, APPDATA, LOCALAPPDATA.
  - `_CHILD_ENV_PREFIXES = ("LC_",)`
  - `_claude_child_env(home: Optional[str] = None) -> Dict[str, str]` — allowlisted subset of `os.environ`, passed through `_strip_api_key_env` (token file read from the process HOME before any override), then `HOME = home` when `home` is truthy.
  - MCP config env block = the four `INTELLISTOCK_*` keys as today, plus `"CLAUDE_CODE_OAUTH_TOKEN": ""` (blanks the inherited subscription token for the stdio child, Fact 4), plus `INTELLISTOCK_MCP_CONFIRM_WAIT_SEC` copied from `os.environ` only when its stripped value is all digits.

- [ ] **Step 1: Impact analysis.** `mcp__gitnexus__impact` on `_strip_api_key_env`, `_write_session_mcp_config`, `_spawn`, `call_claude_cli_structured`, `test_claude_cli`, `claude_auth_status`, `claude_logout`, `ClaudeCliLogin.start`. Then `grep -rn "call_claude_cli_structured\|test_claude_cli\|claude_auth_status\|claude_logout\|ClaudeCliLogin" backend --include='*.py' | grep -v /tests/`. Expected: `llm_utils.py` 1923/2581 (strategy LLM path), `main.py` 3088/3350/3375-3437/3467. **Report as elevated:** the structured path serves LLM strategy decisions, so a missing variable breaks trading-side LLM calls as well as the chatbot (mitigated by Fact 5 and the post-deploy check in Task 7).

- [ ] **Step 2: Write the failing tests**

```python
_SECRETS = ("JWT_SECRET", "POSTGRES_PASSWORD", "INTELLISTOCK_CRED_KEY", "SECRET_AUTH_KEY",
            "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "OPENROUTER_API_KEY", "PG_DSN",
            "MCP_SERVICE_TOKEN", "ANTHROPIC_API_KEY", "DISCORD_WEBHOOK_URL")

def test_child_env_drops_every_secret(self, monkeypatch):
    for k in _SECRETS:
        monkeypatch.setenv(k, "canary")
    env = provider._claude_child_env("/tmp/cc-home-x")
    assert not set(_SECRETS) & set(env)
    assert env["HOME"] == "/tmp/cc-home-x"

def test_child_env_keeps_what_claude_needs(self, monkeypatch):
    keep = {"PATH": "/usr/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "LC_CTYPE": "C.UTF-8",
            "TZ": "UTC", "TERM": "xterm", "TMPDIR": "/tmp", "CLAUDE_CONFIG_DIR": "/cfg",
            "HTTPS_PROXY": "http://proxy:3128", "NO_PROXY": "127.0.0.1",
            "NODE_EXTRA_CA_CERTS": "/ca.pem", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-x"}
    for k, v in keep.items():
        monkeypatch.setenv(k, v)
    env = provider._claude_child_env()
    assert {k: env.get(k) for k in keep} == keep
    assert env["HOME"] == os.environ["HOME"]

def test_child_env_supplies_the_token_file(self, tmp_path, monkeypatch):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "oauth_token").write_text("sk-ant-oat01-file\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    env = provider._claude_child_env("/tmp/cc-home-y")
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-file"
    assert env["HOME"] == "/tmp/cc-home-y"

def test_allowlist_holds_no_secret_shaped_name(self):
    keys = provider._CHILD_ENV_KEYS
    assert not [k for k in keys if any(s in k for s in ("SECRET", "PASSWORD", "DSN", "API_KEY", "CRED"))]
    assert [k for k in keys if "TOKEN" in k] == ["CLAUDE_CODE_OAUTH_TOKEN"]

def test_every_subprocess_call_passes_an_explicit_env(self):
    tree = ast.parse(inspect.getsource(provider))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr in ("run", "Popen")
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"]
    assert len(calls) >= 8
    assert [n.lineno for n in calls if not any(k.arg == "env" for k in n.keywords)] == []
    assert "os.environ.copy()" not in inspect.getsource(provider)
```

Plus three behavioural tests (write them in the file's existing style):
- `test_spawn_hands_popen_the_allowlisted_env`: `monkeypatch.setenv("JWT_SECRET", "canary")`, `CLAUDE_CLI_MCP_DIR=tmp_path`, `_prepare_runtime_home` patched to return `str(tmp_path / "cc-home")`, `_cleanup_runtime_home` a no-op, a Popen factory that records `kwargs` and returns `FakeProcess`; drive one `manager.send_turn` with a fed `_make_result("ok")`. Assert `"JWT_SECRET" not in kwargs["env"]` and `kwargs["env"]["HOME"] == str(tmp_path / "cc-home")`.
- `test_structured_call_hands_run_the_allowlisted_env`: same canary; `subprocess.run` patched to record `kwargs["env"]` and return the happy envelope from `TestCallClaudeCliStructured.test_happy_path`; assert the canary is absent.
- `test_mcp_config_env_block` (parametrize `wait` over `"300"`, `None`, `"abc"`): `CLAUDE_CLI_MCP_DIR=tmp_path`; `sess = provider._Session(conversation_id="conv-cfg-1", model="m", cli_path="claude", system_prompt="s", user_id="u1")`; read the JSON at `provider._write_session_mcp_config(sess)`; `env = cfg["mcpServers"]["intellistock"]["env"]`. Assert `env["CLAUDE_CODE_OAUTH_TOKEN"] == ""`, the four `INTELLISTOCK_*` keys present with `INTELLISTOCK_MCP_TOKEN == sess.mcp_token`, and `env.get("INTELLISTOCK_MCP_CONFIRM_WAIT_SEC") == ("300" if wait == "300" else None)`.

- [ ] **Step 3: Run to verify failure.** `python3 -m pytest -q -p no:cacheprovider backend/tests/test_claude_cli_provider.py -k "ChildEnv"` → AttributeError on `_claude_child_env`.

- [ ] **Step 4: Implement.** Add the constants and `_claude_child_env`. Replace each site: `_spawn` → `_claude_child_env(sess.runtime_home or None)` (drop the separate HOME line); `call_claude_cli_structured` and `test_claude_cli` → `_claude_child_env(runtime_home or None)`; `claude_auth_status`, `claude_logout`, `ClaudeCliLogin.start` → `_claude_child_env(_operator_home())` (login keeps its TERM/BROWSER/NO_BROWSER overrides after); both `--version` runs gain `env=_claude_child_env()`. Extend the MCP config env block. Rewrite the comment at 355-371 to say the child env is now an allowlist and the three conflicting keys are still popped as belt and braces.

- [ ] **Step 5: Run.** `python3 -m pytest -q -p no:cacheprovider backend/tests/test_claude_cli_provider.py backend/tests/test_claude_cli_login.py backend/tests/test_strategy_claude_cli_dispatch.py backend/tests/test_llm_web_search.py` → all pass (the existing `_strip_api_key_env` tests are untouched and still pass).

- [ ] **Step 6 (optional, local CLI): Live check.** Only if `claude` is installed and logged in; it makes one small Haiku call on the operator's subscription. Save as `<scratch>/verify_lockdown.py` (not in the repo) and run from `backend/` with `python3 <scratch>/verify_lockdown.py`. Use `shutil.which`, not `_resolve_cli_path`: a native install resolves to a version-named binary that the allowlist rejects.

```python
import json, os, shutil, subprocess, sys, tempfile
sys.path.insert(0, ".")
os.environ["JWT_SECRET"] = "canary"                      # must reach neither claude nor the MCP child
from chatbot import claude_cli_provider as p
STUB = r'''
import json, os, sys
for raw in sys.stdin:
    if not raw.strip(): continue
    m = json.loads(raw); mid = m.get("id"); meth = m.get("method")
    if meth == "initialize": r = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "stub", "version": "0"}, "capabilities": {"tools": {}}}
    elif meth == "tools/list": r = {"tools": [{"name": "echo", "description": "Report env.", "inputSchema": {"type": "object", "properties": {}}}]}
    elif meth == "tools/call": r = {"content": [{"type": "text", "text": json.dumps({"jwt": "JWT_SECRET" in os.environ, "oauth": bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))})}]}
    elif mid is None: continue
    else: r = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": r}) + "\n"); sys.stdout.flush()
'''
d = tempfile.mkdtemp(); cfg = os.path.join(d, "mcp.json")
open(cfg, "w").write(json.dumps({"mcpServers": {"stub": {"command": sys.executable, "args": ["-c", STUB], "env": {"CLAUDE_CODE_OAUTH_TOKEN": ""}}}}))
argv = p._build_chat_argv(cli_path=shutil.which("claude"), model="haiku", system_prompt="You are a test harness.", extra_args=[], mcp_config_path=cfg)
msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "List the names of every tool you can call, then call echo and print its result verbatim."}]}}
out = subprocess.run(argv, input=json.dumps(msg) + "\n", capture_output=True, text=True, timeout=180, env=p._claude_child_env(), cwd=d)
ev = [json.loads(l) for l in out.stdout.splitlines() if l.startswith("{")]
print("init tools:", next(e for e in ev if e.get("subtype") == "init")["tools"])
print("result:", next(e for e in ev if e.get("type") == "result").get("result"))
```

Expected: `init tools: ['mcp__stub__echo']` and a result containing `"jwt": false, "oauth": false`. Any other name in `init tools` is a failure.

- [ ] **Step 7: detect_changes, then commit** with message `fix(chatbot): build claude-cli child env from an allowlist`; body: every claude spawn (chat, structured, test, auth, login, --version) inherited JWT_SECRET, POSTGRES_PASSWORD, INTELLISTOCK_CRED_KEY and broker keys, and Claude Code passes its env to the MCP stdio child; Co-Authored-By line.

---

### Task 4: Compare-and-swap for conversation messages

**Files:**
- Modify: `backend/chatbot/conversations.py` — imports (8-17), new constants/exception/helpers, `replace_message` (270-296)
- Create: `backend/tests/test_chatbot_approval_cas.py`

**Interfaces:**
- Produces (in `conversations.py`):
  - `STATUS_PENDING = "pending_confirmation"`, `STATUS_EXECUTING = "executing"`, `STATUS_COMPLETE = "complete"`, `CAS_ATTEMPTS = 5`
  - `class ConversationBusyError(ValueError)` — message `"conversation is busy; try again"`
  - `_cas_conversation(user_id: str, conv_id: str, mutate: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]) -> Optional[Dict[str, Any]]`
  - `transition_message(conn, user_id: str, conv_id: str, message_id: str, *, expect_status: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]` — returns a copy of the message as it stood just before the transition; `None` when the conversation (owner-scoped) or message is missing, the message's status is not `expect_status`, or the row vanished mid-swap. The new message is `{**old, **changes, "id": message_id}`.
  - `replace_message(...)` — signature and return (the written doc, or `None`) unchanged, now built on `_cas_conversation`.
- Produces (test helpers in `test_chatbot_approval_cas.py`, used by Tasks 5-6): fixture `conv(store, monkeypatch)` (patches `chatbot.conversations.store` with the conftest `store` fixture and returns the module); `_seed(conv, store, *, status="pending_confirmation", source="claude-cli-mcp") -> tuple[str, str]` (conversation of user `"u1"` holding one assistant message with `tool_calls=[{"id": "tc-1", "name": "fake_write", "arguments": {"qty": 1}}]` and `pending_tool={"tool_call_id": "tc-1", "name": "fake_write", "arguments": {"qty": 1}, "safety": "write", "description": "", "source": source}`, written with `store.update`; returns `(conv_id, message_id)`); `_StoreProxy(inner, *, before_first_replace_if=None, always_lose=False)` (delegates every attribute, counts `replace_if_calls`, runs the hook once before the first delegated `replace_if`); `_RacingStore(inner)` (each thread's FIRST `replace_if` waits at a shared `threading.Barrier(2, timeout=5)`; every delegated `replace_if` runs under one `threading.Lock`, standing in for Postgres' row lock since FakeStore is not thread-safe).

CAS algorithm (the tests do not pin all of it):

```python
for _ in range(CAS_ATTEMPTS):
    doc = get_conversation(None, user_id, conv_id)          # owner check
    if doc is None:
        return None
    new = mutate(copy.deepcopy(doc))
    if new is None:
        return None
    new["updated_at"], new["rev"] = _now(), uuid.uuid4().hex
    when = _token(P.field("updated_at"), doc.get("updated_at")) & _token(P.field("rev"), doc.get("rev"))
    try:
        saved = store.replace_if(TABLE, conv_id, when=when, doc=new)
    except StoreError:                                       # row deleted under us
        return None
    if saved is not None:
        return saved
raise ConversationBusyError("conversation is busy; try again")
# _token(ref, v) = ref.eq(str(v)) if v is not None else ref.is_null()
```

`replace_if` replaces the whole document, so the token must catch every intervening write: `updated_at` catches `append_messages` / `update_conversation` / `clear_messages`; `rev` (rewritten only by CAS writes) makes approver-against-approver exact even when two writes share a microsecond.

- [ ] **Step 1: Impact analysis.** `mcp__gitnexus__impact` on `replace_message`; `grep -rn "replace_message" backend scripts --include='*.py'` → `mcp_bridge.py` 237/270 and `orchestration.py` 590/619 (both rewritten in Tasks 5-6). Report.

- [ ] **Step 2: Write the failing tests**

```python
def test_transition_moves_only_from_the_expected_status(conv, store):
    cid, mid = _seed(conv, store)
    before = conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation",
                                     changes={"status": "executing"})
    assert before["status"] == "pending_confirmation" and before["pending_tool"]["name"] == "fake_write"
    assert conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation",
                                   changes={"status": "executing"}) is None
    msg = conv.find_message(conv.get_conversation(None, "u1", cid), mid)
    assert msg["status"] == "executing" and msg["id"] == mid

def test_transition_refuses_unknown_message_owner_or_conversation(conv, store):
    cid, mid = _seed(conv, store)
    kw = dict(expect_status="pending_confirmation", changes={"status": "executing"})
    assert conv.transition_message(None, "u1", cid, "nope", **kw) is None
    assert conv.transition_message(None, "u2", cid, mid, **kw) is None
    assert conv.transition_message(None, "u1", "missing-conv", mid, **kw) is None
    assert conv.find_message(conv.get_conversation(None, "u1", cid), mid)["status"] == "pending_confirmation"

def test_every_cas_write_rewrites_rev(conv, store):
    cid, mid = _seed(conv, store)
    conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation", changes={"status": "executing"})
    rev1 = conv.get_conversation(None, "u1", cid)["rev"]
    conv.transition_message(None, "u1", cid, mid, expect_status="executing", changes={"status": "complete"})
    assert conv.get_conversation(None, "u1", cid)["rev"] not in (None, rev1)

def test_cas_retries_past_an_interleaved_write_without_losing_it(conv, store, monkeypatch):
    cid, mid = _seed(conv, store)
    proxy = _StoreProxy(store, before_first_replace_if=lambda: store.update(
        conv.TABLE, cid, {"title": "renamed", "updated_at": "2099-01-01T00:00:00Z"}))
    monkeypatch.setattr(conv, "store", proxy)
    assert conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation",
                                   changes={"status": "executing"}) is not None
    doc = conv.get_conversation(None, "u1", cid)
    assert doc["title"] == "renamed" and conv.find_message(doc, mid)["status"] == "executing"
    assert proxy.replace_if_calls == 2

def test_cas_gives_up_after_cas_attempts(conv, store, monkeypatch):
    cid, mid = _seed(conv, store)
    proxy = _StoreProxy(store, always_lose=True)
    monkeypatch.setattr(conv, "store", proxy)
    with pytest.raises(conv.ConversationBusyError):
        conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation", changes={"status": "executing"})
    assert proxy.replace_if_calls == conv.CAS_ATTEMPTS == 5
    assert issubclass(conv.ConversationBusyError, ValueError)

def test_transition_on_a_conversation_deleted_mid_swap_returns_none(conv, store, monkeypatch):
    cid, mid = _seed(conv, store)
    monkeypatch.setattr(conv, "store", _StoreProxy(store, before_first_replace_if=lambda: store.delete(conv.TABLE, cid)))
    assert conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation", changes={"status": "executing"}) is None

def test_replace_message_keeps_the_id_and_rewrites_rev(conv, store):
    cid, mid = _seed(conv, store)
    doc = conv.replace_message(None, "u1", cid, mid, {"id": "other", "role": "assistant", "content": "x", "status": "complete"})
    assert conv.find_message(doc, mid)["content"] == "x" and doc["rev"]
    assert conv.replace_message(None, "u1", cid, "nope", {"content": "y"}) is None

def test_concurrent_append_survives_a_transition(conv, store, monkeypatch):
    from db.fake import FakeStore
    if isinstance(store, FakeStore):
        pytest.skip("append_messages is SQL; needs PG_TEST_DSN")
    cid, mid = _seed(conv, store)
    monkeypatch.setattr(conv, "store", _StoreProxy(store, before_first_replace_if=lambda: conv.append_messages(
        None, "u1", cid, [conv.new_message("assistant", content="late reply")])))
    conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation", changes={"status": "executing"})
    doc = conv.get_conversation(None, "u1", cid)
    assert "late reply" in [m.get("content") for m in doc["messages"]]
    assert conv.find_message(doc, mid)["status"] == "executing"
```

(Inside the PG hook, `conv.append_messages` reaches the real store through the proxy, which only intercepts `replace_if`.)

- [ ] **Step 3: Run to verify failure.** `python3 -m pytest -q -p no:cacheprovider backend/tests/test_chatbot_approval_cas.py` → AttributeError on `transition_message` (the PG test skips).

- [ ] **Step 4: Implement** in `conversations.py`: imports `copy`, `Callable`, `from db import P, StoreError` (FakeStore has no `P` attribute, so import it from `db`, as `trend_research/jobs.py` does); constants, exception, `_cas_conversation`, `transition_message`; rebuild `replace_message` on `_cas_conversation`. Add one line to the module docstring: `rev` is an opaque token rewritten by every compare-and-swap write.

- [ ] **Step 5: Run.** The new file plus `backend/tests/test_api_auth_chat_store.py` → pass (PG-only tests skip).

- [ ] **Step 6: detect_changes, then commit** with message `fix(chatbot): add a compare-and-swap for conversation messages`; body: `store.replace_if` guarded by `updated_at` and a new `rev` token, the mechanism behind `swing_trader.signals_store.cas_signal`; `replace_message` no longer drops concurrent appends; Co-Authored-By line.

---

### Task 5: MCP approvals claim before they execute

**Files:**
- Modify: `backend/chatbot/mcp_bridge.py:192-276` (`resolve_mcp_pending`)
- Test: `backend/tests/test_chatbot_approval_cas.py`

**Interfaces:**
- Consumes: `transition_message`, the `STATUS_*` constants, `_seed`, `_RacingStore`, fixture `conv` (Task 4).
- Produces: `resolve_mcp_pending` with its signature, return value and ValueError texts unchanged; test helper `_race(fn_a, fn_b) -> list[tuple[str, object]]` (two threads; each entry `("ok", value)` or `("err", exception)`), used again in Task 6.

New flow:
1. Read the conversation and message; keep today's three ValueErrors (not found / not pending / wrong `source`), all checked before any write.
2. Decline: `transition_message(expect_status=STATUS_PENDING, changes={"status": STATUS_COMPLETE, "pending_tool": None})`; `None` → `ValueError(f"no pending tool found on message {message_id!r}")`; then append the decline message as today.
3. Approve: `claimed = transition_message(expect_status=STATUS_PENDING, changes={"status": STATUS_EXECUTING})`; `None` → the same ValueError and the tool does not run. Take `name` / `arguments` / `tool_call_id` from `claimed["pending_tool"]`, execute, then `transition_message(expect_status=STATUS_EXECUTING, changes={"status": STATUS_COMPLETE, "pending_tool": None})`. If that returns `None`, log yellow and carry on: the tool ran, so its result is still appended.

- [ ] **Step 1: Impact analysis.** `mcp__gitnexus__impact` on `resolve_mcp_pending`; `grep -rn "resolve_mcp_pending" backend --include='*.py'` → `main.py` 1885 (`api_mcp_confirm`, ValueError → 400). Report: chatbot MCP confirm only; endpoint contract unchanged.

- [ ] **Step 2: Write the failing tests.** Common setup: `racing = _RacingStore(store)` patched over `conv.store`; `calls = []`; `tool = SimpleNamespace(name="fake_write", safety="write", description="", executor=lambda ctx, args: calls.append(dict(args)) or {"done": True})`; `monkeypatch.setattr(mcp_bridge, "get_tool", lambda n: tool if n == "fake_write" else None)`; `appended = []` and `monkeypatch.setattr(conv, "append_messages", lambda c, u, cid_, msgs: appended.extend(msgs))`; `approve` / `decline` call `mcp_bridge.resolve_mcp_pending(conn=None, user={"id": "u1", "username": "op"}, conversation_id=cid, message_id=mid, approved=True/False)`.

```python
def test_two_concurrent_mcp_approvals_execute_once(...):
    outcomes = _race(approve, approve)
    assert calls == [{"qty": 1}]
    assert sorted(k for k, _ in outcomes) == ["err", "ok"]
    err = next(v for k, v in outcomes if k == "err")
    assert isinstance(err, ValueError) and "no pending tool" in str(err)
    final = conv.find_message(conv.get_conversation(None, "u1", cid), mid)
    assert final["status"] == "complete" and final["pending_tool"] is None
    assert [m["role"] for m in appended] == ["tool"]

def test_concurrent_mcp_approve_and_decline_apply_exactly_one(...):
    outcomes = _race(approve, decline)
    assert [k for k, _ in outcomes].count("ok") == 1
    declined = any("user_declined" in (m.get("content") or "") for m in appended)
    assert len(calls) + int(declined) == 1 and len(appended) == 1

def test_mcp_approval_of_an_executing_message_never_runs(...):   # seed status="executing"
    with pytest.raises(ValueError, match="no pending tool"):
        approve()
    assert calls == []

def test_mcp_sequential_double_approve_runs_once(...):
    approve()
    with pytest.raises(ValueError, match="no pending tool"):
        approve()
    assert len(calls) == 1

def test_mcp_wrong_source_is_rejected_before_any_claim(...):     # seed source="orchestration"
    with pytest.raises(ValueError, match="wasn't produced by claude-cli"):
        approve()
    assert conv.find_message(conv.get_conversation(None, "u1", cid), mid)["status"] == "pending_confirmation"
    assert calls == []
```

The three single-thread tests use plain `store` (no `_RacingStore`, whose barrier needs two threads).

- [ ] **Step 3: Run to verify failure.** `python3 -m pytest -q -p no:cacheprovider backend/tests/test_chatbot_approval_cas.py -k mcp` → the concurrent tests fail with `calls` of length 2 (or a barrier timeout, since today's code never calls `replace_if`); `executing_message_never_runs` and `wrong_source` may already pass.

- [ ] **Step 4: Implement** the new flow.

- [ ] **Step 5: Run.** `python3 -m pytest -q -p no:cacheprovider backend/tests/test_chatbot_approval_cas.py` → all pass. Re-run it 20 times (`for i in $(seq 20); do python3 -m pytest -q -p no:cacheprovider backend/tests/test_chatbot_approval_cas.py -k concurrent || break; done`) → never fails.

- [ ] **Step 6: detect_changes, then commit** with message `fix(chatbot): claim MCP approvals with a compare-and-swap`; body: two near-simultaneous approvals both read pending and both ran the tool, which can be submit_live_command; Co-Authored-By line.

---

### Task 6: Confirm-tool approvals claim before they execute

**Files:**
- Modify: `backend/chatbot/orchestration.py:557-621` (`confirm_pending_tool`)
- Test: `backend/tests/test_chatbot_approval_cas.py`

**Interfaces:**
- Consumes: `transition_message`, `STATUS_*`, `_seed`, `_RacingStore` (Task 4); `_race` (Task 5).
- Produces: `confirm_pending_tool(conn, user, conv_id, pending_message_id, approved)` with signature, return and ValueError texts unchanged.

Same three-step flow as Task 5, with this path's own payloads: the not-pending error stays `"no pending tool found on that message"`; decline `changes = {"status": STATUS_COMPLETE, "pending_tool": None, "tool_calls": <today's tool_calls fallback>}`; approve claims with `{"status": STATUS_EXECUTING}`, executes from `claimed["pending_tool"]`, finishes with the decline-shaped `changes`, then calls `_resume_after_tool_results` as today. No `source` check (this path never had one).

- [ ] **Step 1: Impact analysis.** `mcp__gitnexus__impact` on `confirm_pending_tool`; `grep -rn "confirm_pending_tool" backend --include='*.py'` → `main.py` 1776 (`api_chatbot_confirm_tool`, ValueError → 400). Report.

- [ ] **Step 2: Write the failing tests.** Setup as in Task 5, but patch `orchestration.get_tool` and `monkeypatch.setattr(orchestration, "_resume_after_tool_results", lambda conn, user, conv_id, appended: list(appended))` (no LLM); `approve` = `orchestration.confirm_pending_tool(None, {"id": "u1", "username": "op"}, cid, mid, True)`. Seed with `source="orchestration"`.

```python
def test_two_concurrent_confirm_tool_approvals_execute_once(...):
    outcomes = _race(approve, approve)
    assert calls == [{"qty": 1}]
    err = next(v for k, v in outcomes if k == "err")
    assert "no pending tool found on that message" in str(err)

def test_concurrent_confirm_tool_approve_and_decline_apply_exactly_one(...):
    outcomes = _race(approve, decline)
    winner = next(v for k, v in outcomes if k == "ok")
    declined = any("user_declined" in (m.get("content") or "") for m in winner)
    assert [k for k, _ in outcomes].count("ok") == 1 and len(calls) + int(declined) == 1

def test_confirm_tool_on_an_executing_message_never_runs(...):   # seed status="executing"
    with pytest.raises(ValueError):
        approve()
    assert calls == []

def test_confirm_tool_final_message_keeps_its_tool_calls(...):
    approve()
    final = conv.find_message(conv.get_conversation(None, "u1", cid), mid)
    assert final["status"] == "complete" and final["pending_tool"] is None
    assert final["tool_calls"] == [{"id": "tc-1", "name": "fake_write", "arguments": {"qty": 1}}]

def test_llm_history_tolerates_a_stranded_executing_message():
    stranded = {"id": "a1", "role": "assistant", "content": "needs approval", "status": "executing",
                "tool_calls": [{"id": "tc-1", "name": "fake_write", "arguments": {}}]}
    out = orchestration._llm_messages_for_call([{"id": "u", "role": "user", "content": "hi"}, stranded], "sys")
    assert out == [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"},
                   {"role": "assistant", "content": "needs approval"}]
```

- [ ] **Step 3: Run to verify failure.** `python3 -m pytest -q -p no:cacheprovider backend/tests/test_chatbot_approval_cas.py -k "confirm_tool or stranded"` → the concurrent tests fail; the stranded-history test should already pass (it pins current behaviour).

- [ ] **Step 4: Implement** the flow.

- [ ] **Step 5: Run** the file (all pass) and the 20-run loop from Task 5 Step 5 with `-k concurrent` → never fails.

- [ ] **Step 6: detect_changes, then commit** with message `fix(chatbot): claim confirm-tool approvals with a compare-and-swap` and the Co-Authored-By line.

---

### Task 7: Whole-branch verification

**Files:** none unless a check fails (then fix in the owning task's file and commit as `fix(chatbot): ...`).

- [ ] **Step 1: Full suite.** `python3 -m pytest -q -p no:cacheprovider backend/tests` from the worktree root → no failure that does not also fail on a clean origin/main 26cd7546 checkout (check any failure there before calling it pre-existing).
- [ ] **Step 2: Python 3.11.** `~/.local/bin/python3.11 -m py_compile backend/chatbot/claude_cli_provider.py backend/chatbot/conversations.py backend/chatbot/mcp_bridge.py backend/chatbot/orchestration.py backend/tests/test_claude_cli_provider.py backend/tests/test_chatbot_approval_cas.py` → no output.
- [ ] **Step 3: Postgres (if Docker is available).** `scripts/dev_pg.sh up`, export the printed `PG_TEST_DSN`, run `backend/tests/test_chatbot_approval_cas.py backend/tests/test_api_auth_chat_store.py` → all pass and none skip, including `test_concurrent_append_survives_a_transition` and the real-row-lock concurrency tests. Without Docker, say so in the PR body.
- [ ] **Step 4: Scope.** `git diff --stat origin/main...HEAD` lists exactly the six files in File Structure; `mcp__gitnexus__detect_changes({scope: "compare", base_ref: "origin/main", worktree: "/Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock/.worktrees/chatbot-lockdown", repo: "IntelliStock"})` shows only the symbols named in Tasks 1-6.
- [ ] **Step 5: Optional live check.** Task 3 Step 6, if not run yet.
- [ ] **Step 6: Post-deploy checklist for the PR body** (the operator runs it after merging outside market hours):
  1. Models page → claude-cli model → Test connection → ok (proves the allowlisted env authenticates; same env as the strategy LLM path).
  2. Open a chatbot conversation on the claude-cli model, ask "what tools do you have?", then `docker logs <api> 2>&1 | grep "CC init tools"` → only `mcp__intellistock__*` names.
  3. With that conversation still open, list env NAMES (never values) of the live children:
     ```
     docker exec -i <api> python - <<'EOF'
     import os
     for pid in filter(str.isdigit, os.listdir("/proc")):
         try:
             cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ")
             if (b"claude" in cmd and b" -p " in cmd) or b"intellistock_mcp_server" in cmd:
                 env = open(f"/proc/{pid}/environ", "rb").read().split(b"\0")
                 print(pid, cmd[:60], sorted(e.split(b"=", 1)[0].decode() for e in env if e))
         except OSError:
             pass
     EOF
     ```
     → no JWT_SECRET, POSTGRES_PASSWORD, INTELLISTOCK_CRED_KEY, SECRET_AUTH_KEY, ALPACA_*, PG_DSN, *_API_KEY.
  4. Ask the chatbot for a reversible write on a paper target (never alpaca-main, f14-paper or swing-paper), double-tap Approve → one execution; the second tap shows "no pending tool found". Undo the write afterwards.

---

## Decisions made without the operator (review these)

1. **One PR for 7.2 and 7.5.** Spec §8 ships 7.2 alone and 7.5 with the Phase 0 remainder. Both are small, chatbot-only and need no `main.py` change, and every merge redeploys alpaca-main, so one window instead of two.
2. **Runtime guard on top of the flag (Task 2).** The image installs claude-code unpinned. If a future CLI exposes any non-`mcp__` tool, chatbot sessions fail closed with a clear error. An init event without a `tools` key is accepted, so a format change cannot brick the chatbot while the flag still holds.
3. **Allowlist contents (Task 3).** Kept proxy and CA variables (precedent: `forward_mirror._FROZEN_ENV_KEYS`), `LC_*`, XDG and Windows basics. Dropped everything else, including Claude Code tuning knobs nothing in the repo sets (DISABLE_AUTOUPDATER, MCP_TIMEOUT). No operator pass-through knob: adding a variable is a one-line code change. The allowlist also covers the structured (strategy LLM), test, auth, login and `--version` spawns, because they run the same binary with the same auth needs (one rule, one test).
4. **MCP config env block** now blanks `CLAUDE_CODE_OAUTH_TOKEN` for the stdio child and forwards `INTELLISTOCK_MCP_CONFIRM_WAIT_SEC` when it is set to digits, which keeps today's inherited behaviour for that one variable.
5. **CAS token = `updated_at` + new top-level `rev`.** No `backend/db` change and no new table (a claim table would need `schema.py`, which is off-limits). Conversation documents now carry `rev`, and a message reads `executing` while its tool runs; both clients treat any non-pending status as a plain message.
6. **At-most-once on crash.** A crash between claim and finish leaves the message `executing` for good; it is never re-run, and the user asks again. No sweeper.
7. **`replace_message` kept, rebuilt on the CAS.** After Tasks 5-6 it has no production callers. It stays as a safe helper rather than a read-modify-write trap.
8. **Lost race → 400 with today's "no pending tool found…" text**, the same response an already-resolved approval gets now. Exhausted retries → 400 "conversation is busy; try again".
9. **Seen, not in scope:** `backend/chatbot/codex_cli_provider.py` builds its child env with `os.environ.copy()` (lines 1145, 2292), the same leak for the Codex provider; the trim step in `append_messages` is a non-atomic read-modify-write.
