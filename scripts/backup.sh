#!/usr/bin/env bash
# Postgres backup for the harness (#75).
#
# A backup nobody has restored is a hope, not a backup — scripts/restore.sh is the other half, and
# docs/operations.md records the date of the last verified restore. Run this from cron/a scheduled
# job; it writes one compressed custom-format dump per run and prunes older ones.
#
#   ./scripts/backup.sh                      # uses .env.<APP_ENV> (default development)
#   BACKUP_DIR=/var/backups ./scripts/backup.sh
#
# Custom format (-Fc) on purpose: it restores selectively with pg_restore (one table, one schema)
# and compresses, unlike a plain SQL dump.

set -euo pipefail

APP_ENV="${APP_ENV:-development}"
ENV_FILE="${ENV_FILE:-.env.${APP_ENV}}"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a && . "$ENV_FILE" && set +a
fi

POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-mydb}"
POSTGRES_USER="${POSTGRES_USER:-myuser}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-14}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="${BACKUP_DIR}/${POSTGRES_DB}-${STAMP}.dump"

echo "==> dumping ${POSTGRES_DB} from ${POSTGRES_HOST}:${POSTGRES_PORT} to ${TARGET}"
PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump \
  --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --format=custom --compress=6 --file "$TARGET"

# Fail loudly on an empty/truncated dump instead of "succeeding" with an unusable file.
SIZE=$(wc -c < "$TARGET")
if [ "$SIZE" -lt 1024 ]; then
  echo "!! dump is only ${SIZE} bytes — treating as failure" >&2
  exit 1
fi

echo "==> pruning dumps older than ${RETAIN_DAYS} days"
find "$BACKUP_DIR" -name "${POSTGRES_DB}-*.dump" -type f -mtime "+${RETAIN_DAYS}" -delete

echo "==> ok: ${TARGET} ($(numfmt --to=iec "$SIZE" 2>/dev/null || echo "${SIZE}B"))"
echo "    artifacts live under ARTIFACT_STORAGE_ROOT and are NOT in this dump — back that volume up too."
