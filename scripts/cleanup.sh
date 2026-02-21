#!/bin/bash
# Скрипт для автоматической очистки старых бэкапов БД и логов

DB_BACKUP_DIR="${VEILBOT_BACKUP_DIR:-/var/backups/veilbot}"
LOG_ARCHIVE_DIR="${VEILBOT_LOG_ARCHIVE_DIR:-/var/log/veilbot/archive}"

# Удаляем бэкапы БД старше 30 дней
echo "Удаляем бэкапы БД старше 30 дней из $DB_BACKUP_DIR"
find "$DB_BACKUP_DIR" -name "*.sqlite3" -type f -mtime +30 -delete

# Удаляем архивы логов старше 30 дней (если они не были удалены rotate_logs.sh)
echo "Удаляем архивы логов старше 30 дней из $LOG_ARCHIVE_DIR"
find "$LOG_ARCHIVE_DIR" -name "*.gz" -type f -mtime +30 -delete

echo "Автоочистка завершена: $(date)"
echo "=========================================="

