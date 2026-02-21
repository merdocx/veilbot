#!/usr/bin/env bash
# Автоматизированное восстановление базы данных SQLite через .recover

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${DATABASE_PATH:-$PROJECT_ROOT/vpn.db}"
BACKUP_DIR="${VEILBOT_BACKUP_DIR:-/var/backups/veilbot}"
LOG_DIR="${VEILBOT_LOG_DIR:-/var/log/veilbot}"
TIMESTAMP="$(date +"%Y-%m-%d_%H-%M-%S")"

mkdir -p "$BACKUP_DIR" "$LOG_DIR"
LOG_FILE="$LOG_DIR/db_recovery.log"
exec >> "$LOG_FILE" 2>&1

echo "=== SQLite recovery started: $TIMESTAMP ==="
echo "DB_PATH: $DB_PATH"

if [ ! -f "$DB_PATH" ]; then
  echo "❌ Файл базы данных не найден: $DB_PATH"
  exit 1
fi

BACKUP_PATH="$BACKUP_DIR/vpn.db.before_recover.$TIMESTAMP.sqlite3"
echo "--- Создание резервной копии $BACKUP_PATH ---"
sqlite3 "$DB_PATH" ".backup '$BACKUP_PATH'"

TMP_SQL="$(mktemp /tmp/vpn_recover_${TIMESTAMP}_XXXX.sql)"
TMP_DB="$(mktemp /tmp/vpn_recovered_${TIMESTAMP}_XXXX.db)"

cleanup() {
  rm -f "$TMP_SQL" "$TMP_DB".-journal "$TMP_DB"-wal "$TMP_DB"-shm 2>/dev/null || true
}
trap cleanup EXIT

echo "--- Выгрузка данных через .recover ---"
sqlite3 "$DB_PATH" ".mode insert" ".output $TMP_SQL" ".recover"

echo "--- Построение новой базы ---"
sqlite3 "$TMP_DB" ".read $TMP_SQL"

echo "--- Замена оригинального файла ---"
mv "$DB_PATH" "$DB_PATH.corrupted.$TIMESTAMP"
mv "$TMP_DB" "$DB_PATH"
rm -f "${DB_PATH}.wal" "${DB_PATH}.shm" 2>/dev/null || true

echo "--- Проверка целостности новой базы ---"
INTEGRITY_RESULT="$(sqlite3 -readonly "$DB_PATH" "PRAGMA integrity_check;")" || INTEGRITY_RESULT="error"
if [ "$INTEGRITY_RESULT" != "ok" ]; then
  echo "❌ Integrity check не пройден: $INTEGRITY_RESULT"
  exit 2
fi

echo "✅ Восстановление успешно завершено."
echo "Резервная копия перед восстановлением: $BACKUP_PATH"
echo "Старый файл перемещён в: $DB_PATH.corrupted.$TIMESTAMP"
echo "=== SQLite recovery finished: $(date) ==="






