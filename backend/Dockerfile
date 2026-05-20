# IntelliStock Backend
FROM python:3.11-slim

WORKDIR /app

# ``claude`` and ``codex`` are both installed via npm. We install Node.js
# once if either CLI is requested, then add each provider on its own
# build arg so a deployment that uses only one doesn't pay the disk cost
# for the other.
ARG INSTALL_CLAUDE_CLI=1
ARG INSTALL_CODEX_CLI=1

# Install Node.js (npm) once when either CLI is requested. The
# NodeSource setup script handles apt key + repo. Tools land in
# /usr/bin/npm — under the system-binary allowlist the LLM providers
# enforce, so the in-app installer endpoint can re-run ``npm install -g``
# on a live container if the operator wants to upgrade codex.
RUN if [ "$INSTALL_CLAUDE_CLI" = "1" ] || [ "$INSTALL_CODEX_CLI" = "1" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
        && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
        && apt-get install -y --no-install-recommends nodejs \
        && apt-get clean \
        && rm -rf /var/lib/apt/lists/*; \
    fi

# Install the Claude Code CLI (used by the "claude-cli" LLM provider when
# the operator wires up their Pro/Max subscription via the locally-mounted
# ``~/.claude/`` auth state). Skip with ``--build-arg INSTALL_CLAUDE_CLI=0``.
RUN if [ "$INSTALL_CLAUDE_CLI" = "1" ]; then \
        npm install -g @anthropic-ai/claude-code \
        && claude --version; \
    fi

# Install the OpenAI Codex CLI (used by the "codex-cli" LLM provider).
# Operator completes the device-code OAuth from the Models UI; the auth
# state lands in ``/root/.codex`` which compose mounts to a named volume
# so it persists across restarts. Skip with ``--build-arg INSTALL_CODEX_CLI=0``.
#
# IMPORTANT: do NOT pre-populate /root/.codex in the image (no mkdir, no
# ``codex --version`` probe). When Docker first mounts the named volume
# ``codex_auth`` on backend/api/backtest-engine simultaneously, it tries
# to seed the empty volume from whatever's at the image's /root/.codex.
# Any content there (even ``codex --version``'s scratch ``tmp`` dir)
# races across the three containers and one of them hits "mkdir … : file
# exists" because two seeders touch the same subpath. Leaving the dir
# absent means Docker creates an empty volume and codex creates its own
# files at runtime — no race.
RUN if [ "$INSTALL_CODEX_CLI" = "1" ]; then \
        npm install -g @openai/codex \
        && rm -rf /root/.codex; \
    fi

# Non-root user that ``claude`` subprocess invocations run as. The CLI
# hard-refuses ``--dangerously-skip-permissions`` (and any equivalent
# permission-mode) when running as root, so the chatbot's claude
# subprocesses drop privileges via ``runuser`` at spawn time. The
# Python servers themselves keep running as root (the rest of the
# codebase assumes that). UID 1000 is the conventional first non-system
# user on Debian-based images.
RUN if [ "$INSTALL_CLAUDE_CLI" = "1" ]; then \
        useradd -m -u 1000 -s /bin/bash claudeuser 2>/dev/null || true; \
    fi

# Bake the runtime-user env into the image so it doesn't depend on
# compose ``${VAR:-default}`` substitution reaching the container —
# Dockploy and some other orchestrators don't propagate compose-file
# defaults the way ``docker compose up`` does, so the variable would
# arrive as ``None`` and the privilege drop would silently no-op.
# Compose / operator can still override it via the service ``environment``
# block when needed.
ENV CLAUDE_CLI_RUNTIME_USER=claudeuser

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m spacy download en_core_web_lg

# Copy backend application
COPY . .

# Pre-train the CandlesStrategy model so backtests load weights from disk
# instead of retraining (~30s, 99% CPU) on every run.
RUN python strategies/pretrain_candles.py

# Expose Socket.IO / WSGI server port (backend) and API port (api service)
EXPOSE 5000 8000

# Run the main server
CMD ["python", "server.py"]
