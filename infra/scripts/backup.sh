#!/usr/bin/env sh
# Daily PostgreSQL backup with rotation.
#
# Usage (from repo root):  sh infra/scripts/backup.sh
# Cron example (03:30 every day):
#   30 3 * * * cd /opt/qr-reviews && sh infra/scripts/backup.sh >> /var/log/qr-backup.log 2>&1
#
# Env: BACKUP_DIR (default ./backups), BACKUP_KEEP_DAYS (default 14),
#      COMPOSE_FILE (default docker-compose.prod.yml).
set -eu

# shellcheck disable=SC1091
[ -f .env ] && . ./.env
: "${POSTGRES_USER:?}"
: "${POSTGRES_DB:?}"

BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="$BACKUP_DIR/${POSTGRES_DB}-${STAMP}.dump"

echo "[$(date -Iseconds)] Backing up $POSTGRES_DB -> $TARGET"
docker compose -f "$COMPOSE_FILE" exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --compress=9 > "$TARGET.tmp"
mv "$TARGET.tmp" "$TARGET"

find "$BACKUP_DIR" -name "${POSTGRES_DB}-*.dump" -type f -mtime "+$BACKUP_KEEP_DAYS" -delete
echo "[$(date -Iseconds)] Done ($(du -h "$TARGET" | cut -f1))"
# Restore: docker compose -f docker-compose.prod.yml exec -T postgres \
#   pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists < backup.dump
