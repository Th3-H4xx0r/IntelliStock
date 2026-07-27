#!/usr/bin/env bash
# IntelliStock — quick install + start
#
# What it does:
#   1. Verifies prerequisites (docker, docker compose, openssl).
#      Auto-installs any that are missing using the host's package
#      manager (apt / dnf / yum / pacman / zypper / apk / brew) and
#      the official get.docker.com convenience script for Linux Docker.
#   2. Creates .env with safe defaults + freshly-generated stable secrets,
#      and securely provisions missing control-plane keys during upgrades
#   3. Builds the backend image
#   4. Brings up the full stack (rethinkdb, neo4j, backend, api,
#      frontend, price-service, backtest-engine, credential-service)
#   5. Prints local URLs once the API health check passes
#
# Auto-install needs sudo on Linux and Homebrew on macOS. Re-run with
# the user already in the docker group to skip the group-fixup hint.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# ── Pretty print helpers ───────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; PURPLE='\033[38;5;141m'; NC='\033[0m'
info()  { printf "${CYAN}==>${NC} %s\n" "$*"; }
ok()    { printf "${GREEN} ✓${NC}  %s\n" "$*"; }
warn()  { printf "${YELLOW} !${NC}  %s\n" "$*"; }
err()   { printf "${RED} ✗${NC}  %s\n" "$*" >&2; }

banner() {
  printf "${PURPLE}"
  cat <<'BANNER'
██╗███╗   ██╗████████╗███████╗██╗     ██╗     ██╗
██║████╗  ██║╚══██╔══╝██╔════╝██║     ██║     ██║
██║██╔██╗ ██║   ██║   █████╗  ██║     ██║     ██║
██║██║╚██╗██║   ██║   ██╔══╝  ██║     ██║     ██║
██║██║ ╚████║   ██║   ███████╗███████╗███████╗██║
╚═╝╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚══════╝╚══════╝╚═╝
   ███████╗████████╗ ██████╗  ██████╗██╗  ██╗
   ██╔════╝╚══██╔══╝██╔═══██╗██╔════╝██║ ██╔╝
   ███████╗   ██║   ██║   ██║██║     █████╔╝
   ╚════██║   ██║   ██║   ██║██║     ██╔═██╗
   ███████║   ██║   ╚██████╔╝╚██████╗██║  ██╗
   ╚══════╝   ╚═╝    ╚═════╝  ╚═════╝╚═╝  ╚═╝
BANNER
  printf "${NC}\n"
}

banner

# ── Prerequisite checks ────────────────────────────────────────────
have() { command -v "$1" >/dev/null 2>&1; }

# Detect the host OS family. Returns one of: macos, ubuntu, debian,
# fedora, rhel, centos, rocky, almalinux, arch, manjaro, opensuse,
# alpine, or unknown. We key auto-install decisions off this.
detect_os() {
  if [[ "$(uname -s)" == "Darwin" ]]; then
    echo "macos"; return
  fi
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    echo "${ID:-unknown}"; return
  fi
  echo "unknown"
}

# sudo wrapper: if we're already root or sudo is absent, just exec.
_sudo() {
  if [[ $EUID -eq 0 ]] || ! have sudo; then
    "$@"
  else
    sudo "$@"
  fi
}

# Install one or more packages using the host's package manager. The
# first arg is the detected OS id; the rest are package names. Caller
# is responsible for choosing names that exist in that distro's repos.
pkg_install() {
  local os="$1"; shift
  case "$os" in
    ubuntu|debian|raspbian|linuxmint|pop)
      _sudo apt-get update -y
      _sudo apt-get install -y "$@" ;;
    fedora)
      _sudo dnf install -y "$@" ;;
    rhel|centos|rocky|almalinux)
      if have dnf; then _sudo dnf install -y "$@"; else _sudo yum install -y "$@"; fi ;;
    arch|manjaro|endeavouros)
      _sudo pacman -Sy --noconfirm --needed "$@" ;;
    opensuse-leap|opensuse-tumbleweed|opensuse|sles)
      _sudo zypper --non-interactive install "$@" ;;
    alpine)
      _sudo apk add --no-cache "$@" ;;
    macos)
      if ! have brew; then
        err "Homebrew is required to auto-install on macOS. Install from https://brew.sh and re-run."
        return 1
      fi
      brew install "$@" ;;
    *)
      err "Unsupported OS '$os' for auto-install. Install '$*' manually."
      return 1 ;;
  esac
}

ensure_curl() {
  if have curl; then return 0; fi
  local os; os="$(detect_os)"
  warn "curl missing — installing"
  pkg_install "$os" curl || { err "Install curl manually and re-run."; exit 1; }
}

ensure_openssl() {
  if have openssl; then return 0; fi
  local os; os="$(detect_os)"
  warn "openssl not found — installing"
  pkg_install "$os" openssl || { err "Install openssl manually and re-run."; exit 1; }
}

ensure_docker() {
  if have docker; then return 0; fi
  local os; os="$(detect_os)"
  warn "docker not found — installing for '$os'"
  case "$os" in
    macos)
      # Docker on macOS = Docker Desktop. Brew cask handles that and
      # the user still has to open it once to grant privileged-helper
      # permission, so we bail out with instructions afterward.
      if ! have brew; then
        err "Install Docker Desktop manually: https://docs.docker.com/desktop/install/mac-install/"
        exit 1
      fi
      brew install --cask docker
      err "Docker Desktop installed. Open it from Applications once to start the daemon (grants privileged helper), then re-run this script."
      exit 0
      ;;
    ubuntu|debian|raspbian|linuxmint|pop|fedora|rhel|centos|rocky|almalinux|arch|manjaro|endeavouros|opensuse*|sles)
      # The official convenience script handles repo setup across
      # every distro the Docker team supports. It's the same
      # one-liner the Docker docs recommend.
      ensure_curl
      info "Installing Docker via get.docker.com…"
      curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
      _sudo sh /tmp/get-docker.sh
      rm -f /tmp/get-docker.sh
      # Drop the invoking user into the docker group so subsequent
      # 'docker' calls don't need sudo. Only meaningful next session.
      if getent group docker >/dev/null 2>&1 && [[ -n "${SUDO_USER:-${USER:-}}" ]]; then
        _sudo usermod -aG docker "${SUDO_USER:-$USER}" || true
        warn "Added ${SUDO_USER:-$USER} to the 'docker' group — log out + back in for it to take effect (this run will still use sudo via the daemon socket where needed)."
      fi
      ;;
    alpine)
      pkg_install "$os" docker docker-cli-compose
      _sudo rc-update add docker default || true
      _sudo service docker start || true
      ;;
    *)
      err "Unsupported OS '$os'. Install Docker manually: https://docs.docker.com/get-docker/"
      exit 1
      ;;
  esac
  if ! have docker; then
    err "Docker install finished but the 'docker' command still isn't on PATH. Open a new shell and re-run."
    exit 1
  fi
  ok "Docker installed"
}

ensure_compose() {
  if docker compose version >/dev/null 2>&1; then return 0; fi
  local os; os="$(detect_os)"
  warn "'docker compose' subcommand missing — installing compose plugin"
  case "$os" in
    ubuntu|debian|raspbian|linuxmint|pop)
      pkg_install "$os" docker-compose-plugin ;;
    fedora|rhel|centos|rocky|almalinux)
      pkg_install "$os" docker-compose-plugin ;;
    macos)
      err "'docker compose' should ship with Docker Desktop. Update Docker Desktop and re-run."
      exit 1 ;;
    *)
      err "Couldn't auto-install the compose plugin for '$os'. Install 'docker-compose-plugin' (or equivalent) manually."
      exit 1 ;;
  esac
  if ! docker compose version >/dev/null 2>&1; then
    err "'docker compose' still unavailable after install. Reinstall Docker manually."
    exit 1
  fi
}

ensure_docker_running() {
  if docker info >/dev/null 2>&1; then return 0; fi
  local os; os="$(detect_os)"
  case "$os" in
    macos)
      warn "Docker daemon not running — opening Docker Desktop…"
      open -a Docker >/dev/null 2>&1 || true
      for _ in $(seq 1 60); do
        docker info >/dev/null 2>&1 && { ok "Docker daemon is up"; return 0; }
        sleep 2
      done
      err "Docker Desktop didn't come up within 2 minutes. Open it manually and re-run."
      exit 1 ;;
    *)
      warn "Docker daemon not running — starting it…"
      if have systemctl; then
        _sudo systemctl enable --now docker || true
      elif have service; then
        _sudo service docker start || true
      fi
      for _ in $(seq 1 15); do
        docker info >/dev/null 2>&1 && { ok "Docker daemon is up"; return 0; }
        sleep 2
      done
      err "Docker daemon still down. Check 'systemctl status docker' or 'journalctl -u docker' and re-run."
      exit 1 ;;
  esac
}

info "Checking prerequisites…"
ensure_docker
ensure_compose
ensure_openssl
ensure_docker_running
ok "docker, docker compose, openssl present and daemon is up"

# Add the mandatory socket-control master key to new and upgraded installs.
# Existing valid values are preserved because rotating this key invalidates
# in-flight supervisor/broker authentication tokens.
ensure_socket_control_master_key() {
  local key_line_count current_value socket_control_master_key
  key_line_count="$(grep -Ec '^SOCKET_CONTROL_MASTER_KEY=' "$ENV_FILE" || true)"

  if [[ "$key_line_count" -eq 0 ]]; then
    socket_control_master_key="$(openssl rand -hex 32)"
    printf '\n# Stable 32-byte socket-control HMAC master key (64 lowercase hex)\nSOCKET_CONTROL_MASTER_KEY=%s\n' \
      "$socket_control_master_key" >> "$ENV_FILE"
    unset socket_control_master_key
    ok "Provisioned the required socket-control master key in .env"
    return
  fi

  if [[ "$key_line_count" -ne 1 ]]; then
    err ".env must contain exactly one SOCKET_CONTROL_MASTER_KEY assignment"
    exit 1
  fi

  current_value="$(grep -E '^SOCKET_CONTROL_MASTER_KEY=' "$ENV_FILE" | cut -d= -f2-)"
  if ! [[ "$current_value" =~ ^[0-9a-f]{64}$ ]]; then
    err "SOCKET_CONTROL_MASTER_KEY must be exactly 64 lowercase hexadecimal characters"
    exit 1
  fi
}

# ── .env scaffold ──────────────────────────────────────────────────
ENV_FILE="$REPO_ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
  ok ".env already exists — preserving its configured values"
else
  info "Generating .env with safe defaults"
  # Fernet key = 32 random bytes, URL-safe base64 (44 chars incl. padding).
  # This MUST be stable for the lifetime of the install — losing it makes
  # encrypted brokerage credentials unreadable.
  CRED_KEY="$(openssl rand 32 | base64 | tr -d '\n' | tr '+/' '-_')"
  # Signup gate: any /auth/signup call must present this token. Holding it
  # private is what keeps the open invite from being a public registration.
  SECRET_AUTH_KEY="$(openssl rand 32 | base64 | tr -d '\n' | tr '+/' '-_')"
  # Admin password: 16 random URL-safe characters. Auto-provisioned on first
  # boot. Surface it at the end of install so the user can log in.
  # 16 input bytes -> 22 base64 chars (no padding stripped here), cut to 16.
  ADMIN_PASSWORD="$(openssl rand 16 | base64 | tr -d '\n=' | tr '+/' '-_' | cut -c1-16)"
  # JWT signing key: must be at least 32 bytes of high-entropy randomness.
  JWT_SECRET="$(openssl rand 32 | base64 | tr -d '\n' | tr '+/' '-_')"
  # Neo4j password — Neo4j only honours NEO4J_AUTH on first boot, so we
  # commit to a strong password here. If the operator wants a memorable
  # one, they edit .env BEFORE the first `docker compose up`.
  # 20 input bytes -> 28 base64 chars, cut to 20 to guarantee length.
  NEO4J_PASSWORD_GEN="$(openssl rand 20 | base64 | tr -d '\n=' | tr '+/' '-_' | cut -c1-20)"
  cat > "$ENV_FILE" <<EOF
# ── Auto-generated by install.sh — keep INTELLISTOCK_CRED_KEY stable ──
# Losing this key makes encrypted brokerage credentials unrecoverable.

# Fernet key for credential encryption (32 random bytes, urlsafe-b64)
INTELLISTOCK_CRED_KEY=${CRED_KEY}

# Signup gate token. /auth/signup rejects any request that doesn't carry
# this exact value. Keep it private — anyone with it can register users.
SECRET_AUTH_KEY=${SECRET_AUTH_KEY}

# Default admin account auto-provisioned on first backend boot. Change
# DEFAULT_ADMIN_PASSWORD here if you want a memorable one — the value
# below is the auto-generated random password printed at install time.
DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_PASSWORD=${ADMIN_PASSWORD}

# JWT signing key. The backend refuses to mint tokens if this is unset
# or weak; rotating it logs out every active session.
JWT_SECRET=${JWT_SECRET}

# CORS origins (comma-separated). Empty = no cross-origin allowed at all,
# which is correct for the default single-host deploy where nginx
# proxies the API on the same origin. Set this only when serving the
# frontend from a different host than the API.
CORS_ALLOW_ORIGINS=

# Set API_DOCS_PUBLIC=true to expose /docs and /openapi.json. They're
# off by default because the schema is an attack-surface map.
API_DOCS_PUBLIC=

# Service ports
API_PORT=8011
FRONTEND_PORT=3000
RETHINKDB_WEB_PORT=8080
DISCORD_BOT_HTTP_PORT=8050

# How the JS bundle reaches the API in the browser. For local dev
# the frontend container's nginx routes /api → http://intellistock-api,
# so leaving this blank is fine.
VITE_API_URL=
API_URL=http://intellistock-api:8011

# Preview mode: set to "true" to build a landing-only frontend (no
# portal, GitHub CTA in place of auth). Useful for hosting the
# marketing page on its own domain. Empty = full portal.
VITE_PREVIEW_MODE=

# RethinkDB host. Inside docker-compose this is the service name
# 'rethinkdb' (set in compose). Out-of-container CLI scripts default
# to localhost; override here if RethinkDB lives on another host.
RETHINKDB_HOST=localhost
RETHINKDB_PORT=28015

# Neo4j (used by Graph Nexus). Auto-generated random password here so
# we don't ship the well-known default. Neo4j only honours NEO4J_AUTH
# on first boot, so changes after the first 'docker compose up' require
# a manual reset via cypher-shell.
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=${NEO4J_PASSWORD_GEN}

# Optional: Discord bot. Leave DISCORD_BOT_TOKEN blank to skip.
DISCORD_BOT_TOKEN=
DISCORD_BOT_API_KEY=

# LLM provider credentials are NOT configured here. Add them through
# the Models tab in the web UI after onboarding — the Models table is
# the single source of truth for OpenAI / Azure / Gemini / DeepSeek /
# NVIDIA keys for the chatbot and strategies that use LLMs.

# Data-source keys for the Graph Nexus engine. Both are optional —
# the engine degrades to free fallbacks where it can.
BENZINGA_API_KEY=
POLYGON_API_KEY=

# Optional Neo4j heap tuning. Default is 4 GB; lower for the
# minimum hardware tier (see README → Hardware).
# NEO4J_HEAP_MAX_SIZE=4G
EOF
  ok "Wrote $ENV_FILE  (review and add any optional API keys you have)"
fi

ensure_socket_control_master_key

# ── Build & launch ─────────────────────────────────────────────────
info "Building backend image (this takes a few minutes the first time)…"
docker compose build backend
ok "Backend image built"

info "Starting the full stack in the background…"
docker compose up -d
ok "Containers up"

# ── Health wait ────────────────────────────────────────────────────
info "Waiting for the API to come online…"
API_PORT="$(grep -E '^API_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d '"')"
API_PORT="${API_PORT:-8011}"
HEALTH_URL="http://localhost:${API_PORT}/health"

ATTEMPTS=60
for i in $(seq 1 $ATTEMPTS); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    ok "API healthy at $HEALTH_URL"
    break
  fi
  if [[ $i -eq $ATTEMPTS ]]; then
    warn "API didn't respond at $HEALTH_URL after $ATTEMPTS attempts."
    warn "Tail logs with:  docker compose logs -f api"
  fi
  sleep 2
done

# ── Done ───────────────────────────────────────────────────────────
FRONTEND_PORT="$(grep -E '^FRONTEND_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d '"')"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
RDB_WEB_PORT="$(grep -E '^RETHINKDB_WEB_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d '"')"
RDB_WEB_PORT="${RDB_WEB_PORT:-8080}"

ADMIN_USER="$(grep -E '^DEFAULT_ADMIN_USERNAME=' "$ENV_FILE" | cut -d= -f2 | tr -d '"')"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="$(grep -E '^DEFAULT_ADMIN_PASSWORD=' "$ENV_FILE" | cut -d= -f2 | tr -d '"')"

cat <<EOF

${GREEN}IntelliStock is up.${NC}

  Frontend            http://localhost:${FRONTEND_PORT}
  API                 http://localhost:${API_PORT}
  RethinkDB admin     http://localhost:${RDB_WEB_PORT}
  Neo4j browser       http://localhost:7474   (user: neo4j / pass: see NEO4J_PASSWORD in .env)

  ${PURPLE}Default admin login${NC}
    Username          ${ADMIN_USER}
    Password          ${ADMIN_PASS}
    ${YELLOW}(also stored in .env as DEFAULT_ADMIN_PASSWORD — change there + restart to rotate)${NC}

  Logs                docker compose logs -f
  Stop                docker compose down
  Stop + wipe data    docker compose down -v   ${YELLOW}(deletes RethinkDB + Neo4j volumes)${NC}

Open the frontend, walk through onboarding, and you're trading.
EOF
