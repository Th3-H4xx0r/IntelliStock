# IntelliStock MCP server (python -m mcp_server, port 8090). The build context is
# backend/mcp_server alone, so nothing else from backend/ can reach the image.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# Every package, transitive ones included, is pinned with its sha256 in the lock;
# --no-deps means pip installs exactly that list and resolves nothing itself.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes --no-deps -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# The server and the admin CLI (docker exec) both run as uid 10001, the owner of
# the auth store. A fresh named volume mounted on /data inherits this ownership.
RUN groupadd --gid 10001 mcp \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin mcp \
    && mkdir /data && chown 10001:10001 /data && chmod 700 /data

# Root-owned and read-only to the service user.
COPY . /app/mcp_server
WORKDIR /app
USER 10001:10001

EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=4)"]

# One uvicorn worker (see __main__.py): sign-in's throttle locks live in this process.
CMD ["python", "-m", "mcp_server"]
