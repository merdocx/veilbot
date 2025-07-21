#!/bin/bash
# Скрипт для резервного копирования базы данных vpn.db
#
# Для автоматизации добавьте в crontab строку (каждый час):
# 0 * * * * /bin/bash /root/veilbot/backup_db.sh

set -e

DB_FILE="vpn.db"
BACKUP_DIR="backups"
DATE=$(date +"%Y-%m-%d_%H-%M-%S")
BACKUP_FILE="$BACKUP_DIR/vpn.db.$DATE.bak"

# Создать папку для бэкапов, если не существует
mkdir -p "$BACKUP_DIR"

# Проверить, существует ли база данных
if [ ! -f "$DB_FILE" ]; then
  echo "❌ Файл базы данных $DB_FILE не найден!"
  exit 1
fi

# Копировать базу данных
cp "$DB_FILE" "$BACKUP_FILE"

if [ $? -eq 0 ]; then
  echo "✅ Бэкап создан: $BACKUP_FILE"
else
  echo "❌ Ошибка при создании бэкапа!"
  exit 2
fi

# Удалять старые бэкапы, оставляя только 48 последних
ls -1t $BACKUP_DIR/vpn.db.*.bak | tail -n +49 | xargs -r rm -- 