#!/usr/bin/env bash
# Restore a Postgres dump produced by scripts/backup.sh (#75).
#
#   ./scripts/restore.sh backups/mydb-20260731T190000Z.dump                 # into $POSTGRES_DB
#   ./scripts/restore.sh backups/....dump --into mydb_restore_check         # into a scratch DB
#
# Restoring into a SCRATCH database is how a restore gets *verified* without touching production —
# that is the drill docs/operations.md asks you to run quarterly, and the reason this script exists
# separately from the backup one.

set -euo pipefail

DUMP="${1:-}"
if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
  echo "usage: $0 <dump-file> [--into <database>]" >&2
  exit 2
fi
shift

APP_ENV="${APP_ENV:-development}"
ENV_FILE="${ENV_FILE:-.env.${APP_ENV}}"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a && . "$ENV_FILE" && set +a
fi

POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-myuser}"
TARGET_DB="${POSTGRES_DB:-mydb}"

if [ "${1:-}" = "--into" ]; then
  TARGET_DB="${2:?--into needs a database name}"
  echo "==> creating scratch database ${TARGET_DB} (dropped first if present)"
  PGPASSWORD="${POSTGRES_PASSWORD:-}" psql --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" \
    --username "$POSTGRES_USER" --dbname postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS ${TARGET_DB};" -c "CREATE DATABASE ${TARGET_DB};"
fi

echo "==> restoring ${DUMP} into ${TARGET_DB}"
# --clean --if-exists so restoring over an existing database is idempotent; exit code is checked by
# `set -e`, so a partial restore fails the run instead of leaving a half-populated database.
PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_restore \
  --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" \
  --username "$POSTGRES_USER" --dbname "$TARGET_DB" \
  --clean --if-exists --no-owner --no-privileges "$DUMP"

echo "==> restored. Sanity-check row counts before declaring success, e.g.:"
echo "    psql -h $POSTGRES_HOST -U $POSTGRES_USER -d $TARGET_DB -c 'select count(*) from \"user\";'"
