#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${MONGO_URI:-}" ]]; then
  echo "MONGO_URI is required"
  exit 1
fi

if [[ -z "${MONGO_DB_NAME:-}" ]]; then
  echo "MONGO_DB_NAME is required"
  exit 1
fi

BACKUP_DIR="${1:-./backups}"
TS="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="${BACKUP_DIR}/mongo-${MONGO_DB_NAME}-${TS}"

mkdir -p "${OUT_DIR}"
echo "Creating backup at ${OUT_DIR}"
mongodump --uri="${MONGO_URI}" --db="${MONGO_DB_NAME}" --out="${OUT_DIR}"
echo "Backup completed"
