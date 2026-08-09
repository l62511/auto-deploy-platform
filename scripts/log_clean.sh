#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/logs}"
COMPRESS_AFTER_DAYS="${COMPRESS_AFTER_DAYS:-1}"
DELETE_AFTER_DAYS="${DELETE_AFTER_DAYS:-30}"

if [[ ! -d "$LOG_DIR" ]]; then
  printf 'Log directory does not exist: %s\n' "$LOG_DIR" >&2
  exit 1
fi
if ! [[ "$COMPRESS_AFTER_DAYS" =~ ^[0-9]+$ && "$DELETE_AFTER_DAYS" =~ ^[0-9]+$ ]]; then
  printf 'Retention values must be non-negative integers.\n' >&2
  exit 1
fi

find "$LOG_DIR" -type f -name '*.log.*' ! -name '*.gz' -mtime "+$COMPRESS_AFTER_DAYS" -print0 |
  xargs -0 --no-run-if-empty gzip --
find "$LOG_DIR" -type f -name '*.gz' -mtime "+$DELETE_AFTER_DAYS" -delete

printf 'Log rotation complete: %s\n' "$LOG_DIR"

