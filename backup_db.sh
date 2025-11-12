#!/usr/bin/env bash
# Скрипт для резервного копирования базы данных vpn.db c использованием sqlite3 .backup
#
# Для автоматизации создайте cron-задание (каждый час):
# 0 * * * * /bin/bash /root/veilbot/backup_db.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_FILE="${DATABASE_PATH:-$PROJECT_ROOT/vpn.db}"
BACKUP_DIR="${VEILBOT_BACKUP_DIR:-/var/backups/veilbot}"
LOG_DIR="${VEILBOT_LOG_DIR:-/var/log/veilbot}"
DATE="$(date +"%Y-%m-%d_%H-%M-%S")"
BACKUP_FILE="$BACKUP_DIR/vpn.db.$DATE.sqlite3"
LOG_FILE="$LOG_DIR/backup_db.log"

mkdir -p "$BACKUP_DIR" "$LOG_DIR"

exec >> "$LOG_FILE" 2>&1

echo "=== VeilBot SQLite Backup ==="
echo "Время: $(date)"
echo "Источник: $DB_FILE"
echo "Backup:   $BACKUP_FILE"

if [ ! -f "$DB_FILE" ]; then
  echo "❌ Файл базы данных $DB_FILE не найден!"
  exit 1
fi

# Проверяем целостность базы в read-only режиме до любых операций
echo "--- Проверка целостности ---"
INTEGRITY_RESULT="$(sqlite3 -readonly "$DB_FILE" "PRAGMA integrity_check;")" || INTEGRITY_RESULT="error"
if [ "$INTEGRITY_RESULT" != "ok" ]; then
  echo "❌ Проверка целостности не пройдена: $INTEGRITY_RESULT"
  exit 2
fi
echo "✅ integrity_check: ok"

# Выполняем контрольную точку журналов WAL перед копированием (может требовать запись)
sqlite3 "$DB_FILE" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null 2>&1 || echo "⚠️ Не удалось выполнить wal_checkpoint (вероятно, база занята)"

# Создаём резервную копию через встроенную команду .backup
if sqlite3 "$DB_FILE" ".backup '$BACKUP_FILE'"; then
  echo "✅ Бэкап создан: $BACKUP_FILE"
else
  echo "❌ Ошибка при создании бэкапа"
  exit 3
fi

# Периодическая дефрагментация базы данных
if sqlite3 "$DB_FILE" "VACUUM;" >/dev/null 2>&1; then
  echo "✅ VACUUM выполнен"
else
  echo "⚠️ Не удалось выполнить VACUUM (вероятно, база занята)"
fi

# Хранить только последние 48 резервных копий
find "$BACKUP_DIR" -maxdepth 1 -name 'vpn.db.*.sqlite3' -type f | sort -r | tail -n +49 | xargs -r rm --

echo "=== Завершено: $(date) ==="