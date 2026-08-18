#!/usr/bin/env bash
# Start/stop local Postgres + Redis WITHOUT Docker.
#
# docker-compose.yml is the intended way to run these services, but Docker
# Desktop needs an administrator install. This script provisions the same two
# services from a user-local conda prefix so the stack can be developed and
# tested without admin rights. Ports are deliberately non-default so they cannot
# collide with a real local Postgres/Redis.
set -euo pipefail

SERVICES="${SERVICES:-$HOME/.local/rs-services}"
PGDATA="${PGDATA:-$HOME/.local/rs-data/pg}"
PGPORT="${PGPORT:-55432}"
REDIS_PORT="${REDIS_PORT:-56379}"

export RETAILSCOUT_PG_URL="postgresql+psycopg://retailscout@/retailscout?host=/tmp&port=${PGPORT}"
export RETAILSCOUT_REDIS_URL="redis://localhost:${REDIS_PORT}/0"

case "${1:-start}" in
  start)
    "$SERVICES/bin/pg_ctl" -D "$PGDATA" -o "-p $PGPORT -k /tmp" -l /tmp/pg.log start || true
    "$SERVICES/bin/redis-server" --port "$REDIS_PORT" --daemonize yes --save '' --appendonly no || true
    sleep 2
    "$SERVICES/bin/pg_isready" -h /tmp -p "$PGPORT" -U retailscout
    "$SERVICES/bin/redis-cli" -p "$REDIS_PORT" ping
    ;;
  stop)
    "$SERVICES/bin/pg_ctl" -D "$PGDATA" stop || true
    "$SERVICES/bin/redis-cli" -p "$REDIS_PORT" shutdown nosave 2>/dev/null || true
    ;;
  *) echo "usage: $0 {start|stop}" >&2; exit 2 ;;
esac
