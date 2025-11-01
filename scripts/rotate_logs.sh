#!/bin/bash
# Скрипт для ротации логов VeilBot

LOG_DIR="/root/veilbot/logs"
APP_LOGS=(
    "/root/veilbot/bot.log"
    "/root/veilbot/admin.log"
    "/root/veilbot/veilbot_security.log"
    "/root/veilbot/admin/admin_audit.log"
    "/root/veilbot/admin/nohup.out"
)

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

echo "=== Ротация логов VeilBot ==="
echo "Время запуска: $(date)"

mkdir -p "$LOG_DIR"

for LOG_FILE in "${APP_LOGS[@]}"; do
    if [ -f "$LOG_FILE" ]; then
        BASENAME=$(basename "$LOG_FILE")
        DIRNAME=$(dirname "$LOG_FILE")
        
        echo "Ротируем $LOG_FILE..."
        gzip -c "$LOG_FILE" > "$LOG_DIR/${BASENAME}.${TIMESTAMP}.gz"
        echo "" > "$LOG_FILE" # Очищаем оригинальный файл
    else
        echo "Файл лога не найден: $LOG_FILE"
    fi
done

# Удаляем старые архивы логов (старше 30 дней)
find "$LOG_DIR" -name "*.gz" -type f -mtime +30 -delete

echo "Ротация логов завершена: $(date)"
echo "=============================="

