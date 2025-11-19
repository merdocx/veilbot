#!/bin/bash
# Скрипт для мониторинга покупки подписки пользователем 6358556135

USER_ID="6358556135"
LOG_DIR="${VEILBOT_LOG_DIR:-/var/log/veilbot}"

echo "=========================================="
echo "Мониторинг покупки подписки"
echo "Пользователь: $USER_ID"
echo "Время начала: $(date)"
echo "=========================================="
echo ""

# Функция для форматирования вывода
format_log() {
    while IFS= read -r line; do
        timestamp=$(date '+%H:%M:%S')
        echo "[$timestamp] $line"
    done
}

# Мониторинг всех логов с фильтрацией
tail -f \
    "${LOG_DIR}/bot.log" \
    "${LOG_DIR}/admin_audit.log" \
    "${LOG_DIR}/veilbot_security.log" \
    "./bot.log" \
    "./admin.log" \
    2>/dev/null | \
    grep --line-buffered -i -E "($USER_ID|subscription|подписк|payment.*$USER_ID|webhook.*$USER_ID|yookassa.*$USER_ID|create.*subscription|process.*payment|Payment.*$USER_ID)" | \
    format_log

