# IntelliStock Backend
FROM python:3.11-slim

WORKDIR /app

# Install Python deps. ``claude`` CLI itself is installed via Node below;
# enable/disable that with the INSTALL_CLAUDE_CLI build arg (default on).
ARG INSTALL_CLAUDE_CLI=1

# Install the Claude Code CLI (used by the "claude-cli" LLM provider when
# the operator wires up their Pro/Max subscription via the locally-mounted
# ``~/.claude/`` auth state). Skip with ``--build-arg INSTALL_CLAUDE_CLI=0``
# if you don't plan to use this provider.
RUN if [ "$INSTALL_CLAUDE_CLI" = "1" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
        && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
        && apt-get install -y --no-install-recommends nodejs \
        && npm install -g @anthropic-ai/claude-code \
        && claude --version \
        && apt-get clean \
        && rm -rf /var/lib/apt/lists/*; \
    fi

# Non-root user that ``claude`` subprocess invocations run as. The CLI
# hard-refuses ``--dangerously-skip-permissions`` (and any equivalent
# permission-mode) when running as root, so the chatbot's claude
# subprocesses drop privileges via Popen(user=...) at spawn time. The
# Python servers themselves keep running as root (the rest of the
# codebase assumes that). UID 1000 is the conventional first non-system
# user on Debian-based images.
RUN if [ "$INSTALL_CLAUDE_CLI" = "1" ]; then \
        useradd -m -u 1000 -s /bin/bash claudeuser 2>/dev/null || true; \
    fi

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
