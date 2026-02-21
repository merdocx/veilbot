#!/usr/bin/env bash
# Скрипт для автоматической синхронизации ключей с серверами
# Запускает полную синхронизацию: обновление конфигураций и удаление лишних ключей
#
# Для автоматизации создайте cron-задание (например, раз в день):
# 0 4 * * * /bin/bash /root/veilbot/scripts/sync_keys_cron.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${VEILBOT_LOG_DIR:-/var/log/veilbot}"
DATE="$(date +"%Y-%m-%d_%H-%M-%S")"
LOG_FILE="$LOG_DIR/sync_keys_${DATE}.log"

mkdir -p "$LOG_DIR"

exec >> "$LOG_FILE" 2>&1

echo "=== VeilBot Sync Keys ==="
echo "Время: $(date)"
echo "Лог: $LOG_FILE"
echo ""

cd "$PROJECT_ROOT"

# Запускаем синхронизацию (используем python3.11 — в нём установлены зависимости)
python3.11 "$PROJECT_ROOT/scripts/sync_all_keys_with_servers.py" \
    2>&1 | tee -a "$LOG_FILE"

SYNC_EXIT_CODE=${PIPESTATUS[0]}

if [ $SYNC_EXIT_CODE -eq 0 ]; then
    echo ""
    echo "✅ Синхронизация завершена успешно"
else
    echo ""
    echo "❌ Ошибка синхронизации (код: $SYNC_EXIT_CODE)"
fi

# Оставляем только последние 30 файлов логов
find "$LOG_DIR" -name "sync_keys_*.log" -type f -mtime +30 -delete 2>/dev/null || true

exit $SYNC_EXIT_CODE


