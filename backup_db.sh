#!/usr/bin/env bash
# Скрипт для резервного копирования базы данных vpn.db c использованием sqlite3 .backup
#
# Для автоматизации создайте cron-задание (каждый час):
# 0 * * * * /bin/bash /root/veilbot/backup_db.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_FILE="${DATABASE_PATH:-$PROJECT_ROOT/vpn.db}"
BACKUP_DIR="${VEILBOT_BACKUP_DIR:-/var/backups/veilbot}"
DATE="$(date +"%Y-%m-%d_%H-%M-%S")"
BACKUP_FILE="$BACKUP_DIR/vpn.db.$DATE.sqlite3"

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB_FILE" ]; then
  echo "❌ Файл базы данных $DB_FILE не найден!"
  exit 1
fi

echo "=== VeilBot SQLite Backup ==="
echo "Источник: $DB_FILE"
echo "Backup:   $BACKUP_FILE"

# Выполняем контрольную точку журналов WAL перед копированием
sqlite3 "$DB_FILE" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null 2>&1 || true

# Создаём резервную копию через встроенную команду .backup
if sqlite3 "$DB_FILE" ".backup '$BACKUP_FILE'"; then
  echo "✅ Бэкап создан: $BACKUP_FILE"
else
  echo "❌ Ошибка при создании бэкапа"
  exit 2
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