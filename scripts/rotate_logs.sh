#!/bin/bash
# Скрипт для ротации логов VeilBot

LOG_ROOT="${VEILBOT_LOG_DIR:-/var/log/veilbot}"
ARCHIVE_DIR="$LOG_ROOT/archive"
APP_LOGS=(
    "$LOG_ROOT/bot.log"
    "$LOG_ROOT/admin_audit.log"
    "$LOG_ROOT/veilbot_security.log"
)

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

echo "=== Ротация логов VeilBot ==="
echo "Время запуска: $(date)"

mkdir -p "$ARCHIVE_DIR"

for LOG_FILE in "${APP_LOGS[@]}"; do
    if [ -f "$LOG_FILE" ]; then
        BASENAME=$(basename "$LOG_FILE")
        DIRNAME=$(dirname "$LOG_FILE")
        
        echo "Ротируем $LOG_FILE..."
        gzip -c "$LOG_FILE" > "$ARCHIVE_DIR/${BASENAME}.${TIMESTAMP}.gz"
        echo "" > "$LOG_FILE" # Очищаем оригинальный файл
    else
        echo "Файл лога не найден: $LOG_FILE"
    fi
done

# Удаляем старые архивы логов (старше 30 дней)
find "$ARCHIVE_DIR" -name "*.gz" -type f -mtime +30 -delete

echo "Ротация логов завершена: $(date)"
echo "=============================="

