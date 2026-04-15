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

if [[ $# -lt 1 ]]; then
  echo "Usage: ./scripts/restore_mongo.sh <backup_folder>"
  exit 1
fi

BACKUP_FOLDER="$1"
TARGET_PATH="${BACKUP_FOLDER}/${MONGO_DB_NAME}"
if [[ ! -d "${TARGET_PATH}" ]]; then
  echo "Backup folder does not contain expected path: ${TARGET_PATH}"
  exit 1
fi

echo "Restoring database ${MONGO_DB_NAME} from ${TARGET_PATH}"
mongorestore --uri="${MONGO_URI}" --drop --db="${MONGO_DB_NAME}" "${TARGET_PATH}"
echo "Restore completed"
