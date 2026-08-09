#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
MYSQL_CONTAINER="${MYSQL_CONTAINER:-auto-deploy-dev-mysql-1}"
MYSQL_DATABASE="${MYSQL_DATABASE:-demo}"
CONFIG_PATH="${CONFIG_PATH:-$PROJECT_ROOT/config}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="$BACKUP_DIR/$timestamp"

if [[ -z "$MYSQL_ROOT_PASSWORD" ]]; then
  printf 'MYSQL_ROOT_PASSWORD is required.\n' >&2
  exit 1
fi
if ! [[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]]; then
  printf 'RETENTION_DAYS must be a non-negative integer.\n' >&2
  exit 1
fi
if ! docker inspect "$MYSQL_CONTAINER" >/dev/null 2>&1; then
  printf 'MySQL container not found: %s\n' "$MYSQL_CONTAINER" >&2
  exit 1
fi

mkdir -p "$destination"
exec 9>"$BACKUP_DIR/.backup.lock"
if ! flock -n 9; then
  printf 'Another backup is already running.\n' >&2
  exit 1
fi

sql_file="$destination/${MYSQL_DATABASE}.sql"
docker exec -e "MYSQL_PWD=$MYSQL_ROOT_PASSWORD" "$MYSQL_CONTAINER" \
  mysqldump --single-transaction --quick --lock-tables=false -uroot "$MYSQL_DATABASE" > "$sql_file"
gzip "$sql_file"

if [[ -e "$CONFIG_PATH" ]]; then
  tar -czf "$destination/config.tar.gz" -C "$(dirname "$CONFIG_PATH")" "$(basename "$CONFIG_PATH")"
fi
sha256sum "$destination"/* > "$destination/SHA256SUMS"

find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+$RETENTION_DAYS" -exec rm -rf -- {} +
printf 'Backup created: %s\n' "$destination"

