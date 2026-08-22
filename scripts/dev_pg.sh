#!/usr/bin/env bash
# Throwaway PostgreSQL 17 cluster for local tests. No root, no Docker.
#
#   ./scripts/dev_pg.sh up     initdb into .devpg/, start on a free port,
#                              create the DB, print PG_TEST_DSN
#   ./scripts/dev_pg.sh dsn    echo PG_TEST_DSN
#   ./scripts/dev_pg.sh psql   open a shell on it
#   ./scripts/dev_pg.sh down   stop the cluster
#   ./scripts/dev_pg.sh nuke   stop and delete .devpg/
#
# Usage:  export PG_TEST_DSN="$(./scripts/dev_pg.sh dsn)"
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGROOT="$REPO_ROOT/.devpg"
PGDATA="$PGROOT/data"
PGLOG="$PGROOT/postgres.log"
PORTFILE="$PGROOT/port"
DBNAME="intellistock_test"

find_bindir() {
  # Homebrew postgresql@17 first; it is binaries-only and we run our own
  # cluster, never Homebrew's service.
  for cand in \
      /opt/homebrew/opt/postgresql@17/bin \
      /usr/local/opt/postgresql@17/bin \
      /usr/lib/postgresql/17/bin; do
    if [ -x "$cand/initdb" ]; then echo "$cand"; return 0; fi
  done
  if command -v initdb >/dev/null 2>&1; then dirname "$(command -v initdb)"; return 0; fi
  # Fallback: pgserver vendors a Postgres binary inside a wheel.
  local pgs
  pgs="$(python3 -c 'import pgserver,os;print(os.path.join(os.path.dirname(pgserver.__file__),"pginstall","bin"))' 2>/dev/null || true)"
  if [ -n "$pgs" ] && [ -x "$pgs/initdb" ]; then echo "$pgs"; return 0; fi
  echo "ERROR: no PostgreSQL 17 binaries found." >&2
  echo "  brew install postgresql@17     (preferred)" >&2
  echo "  pip install pgserver           (fallback)" >&2
  return 1
}

free_port() {
  python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

BINDIR="$(find_bindir)"
export PATH="$BINDIR:$PATH"

cmd_up() {
  mkdir -p "$PGROOT"
  if [ ! -f "$PGDATA/PG_VERSION" ]; then
    echo "initdb -> $PGDATA (using $BINDIR)"
    initdb -D "$PGDATA" -U postgres --encoding=UTF8 \
           --locale=C --lc-collate=C --lc-ctype=C >/dev/null
  fi
  if pg_ctl -D "$PGDATA" status >/dev/null 2>&1; then
    echo "already running on port $(cat "$PORTFILE")"
  else
    local port; port="$(free_port)"; echo "$port" > "$PORTFILE"
    pg_ctl -D "$PGDATA" -l "$PGLOG" \
      -o "-p $port -k $PGROOT -c listen_addresses=127.0.0.1 -c timezone=UTC" \
      -w start
    createdb -h 127.0.0.1 -p "$port" -U postgres "$DBNAME" 2>/dev/null || true
  fi
  local port; port="$(cat "$PORTFILE")"
  # Risk #4 in the spec: lz4 availability is unverified. Report, never assume.
  local comp
  comp="$(psql -h 127.0.0.1 -p "$port" -U postgres -d "$DBNAME" -tAc \
          'SHOW default_toast_compression' 2>/dev/null || echo unknown)"
  echo "default_toast_compression = $comp  (lz4 wanted; pglz costs disk, not correctness)"
  echo "PG_TEST_DSN=$(cmd_dsn)"
}

cmd_dsn() {
  [ -f "$PORTFILE" ] || { echo "not started; run: $0 up" >&2; return 1; }
  echo "postgresql://postgres@127.0.0.1:$(cat "$PORTFILE")/$DBNAME"
}

cmd_psql() { psql "$(cmd_dsn)"; }
cmd_down() { pg_ctl -D "$PGDATA" -m fast -w stop || true; }
cmd_nuke() { cmd_down; rm -rf "$PGROOT"; echo "removed $PGROOT"; }

case "${1:-}" in
  up) cmd_up ;;
  dsn) cmd_dsn ;;
  psql) cmd_psql ;;
  down) cmd_down ;;
  nuke) cmd_nuke ;;
  *) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 2 ;;
esac
