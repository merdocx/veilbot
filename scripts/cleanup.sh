#!/bin/bash
# Скрипт для автоматической очистки старых бэкапов БД и логов

DB_BACKUP_DIR="/root/veilbot/db_backups"
LOG_DIR="/root/veilbot/logs"
ROOT_LOGS_DIR="/root/veilbot" # Директория для логов в корне проекта

# Удаляем бэкапы БД старше 30 дней
echo "Удаляем бэкапы БД старше 30 дней из $DB_BACKUP_DIR"
find "$DB_BACKUP_DIR" -name "*.sqlite3" -type f -mtime +30 -delete

# Удаляем архивы логов старше 30 дней (если они не были удалены rotate_logs.sh)
echo "Удаляем архивы логов старше 30 дней из $LOG_DIR"
find "$LOG_DIR" -name "*.gz" -type f -mtime +30 -delete

# Удаляем старые файлы логов в корне проекта (старше 7 дней)
echo "Удаляем старые файлы логов в корне старше 7 дней из $ROOT_LOGS_DIR"
find "$ROOT_LOGS_DIR" -maxdepth 1 -type f \( -name "*.log" -o -name "*.out" \) -mtime +7 -delete

echo "Автоочистка завершена: $(date)"
echo "=========================================="

