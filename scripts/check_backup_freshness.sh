#!/usr/bin/env bash
# Скрипт для проверки свежести резервных копий БД
# Отправляет уведомление, если последний бэкап старше 2 часов
#
# Для автоматизации создайте cron-задание (каждые 30 минут):
# */30 * * * * /bin/bash /root/veilbot/scripts/check_backup_freshness.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${VEILBOT_BACKUP_DIR:-/var/backups/veilbot}"
LOG_DIR="${VEILBOT_LOG_DIR:-/var/log/veilbot}"
TIMESTAMP_FILE="$BACKUP_DIR/.last_backup_timestamp"
MAX_AGE_SECONDS=7200  # 2 часа
ALERT_COOLDOWN_FILE="$LOG_DIR/.backup_freshness_alert_cooldown"
ALERT_COOLDOWN_SECONDS=3600  # 1 час между уведомлениями

mkdir -p "$BACKUP_DIR" "$LOG_DIR"

# Функция для отправки уведомлений в Telegram
send_telegram_notification() {
    local message="$1"
    
    local bot_token
    local admin_id
    
    bot_token=$(python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
try:
    from app.settings import settings
    print(settings.TELEGRAM_BOT_TOKEN or '')
except Exception:
    pass
" 2>/dev/null || echo "")
    
    admin_id=$(python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
try:
    from app.settings import settings
    print(settings.ADMIN_ID or '46701395')
except Exception:
    print('46701395')
" 2>/dev/null || echo "46701395")
    
    if [ -z "$bot_token" ] || [ "$bot_token" = "None" ]; then
        return 0
    fi
    
    curl -s -X POST "https://api.telegram.org/bot${bot_token}/sendMessage" \
        -d "chat_id=${admin_id}" \
        -d "text=${message}" \
        -d "parse_mode=Markdown" \
        >/dev/null 2>&1 || true
}

# Проверяем наличие файла метки времени
if [ ! -f "$TIMESTAMP_FILE" ]; then
    # Если файла нет, проверяем наличие любого бэкапа
    LATEST_BACKUP=$(find "$BACKUP_DIR" -maxdepth 1 -name 'vpn.db.*.sqlite3' -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
    
    if [ -z "$LATEST_BACKUP" ]; then
        notification="⚠️ *Предупреждение: резервные копии не найдены*\n\n"
        notification+="*Директория:* \`$BACKUP_DIR\`\n"
        notification+="*Время:* $(date '+%Y-%m-%d %H:%M:%S')\n\n"
        notification+="Резервные копии БД не создавались или были удалены."
        
        send_telegram_notification "$notification"
        exit 1
    fi
    
    # Используем время модификации файла
    LAST_BACKUP_TIME=$(stat -f%Y "$LATEST_BACKUP" 2>/dev/null || stat -c%Y "$LATEST_BACKUP" 2>/dev/null || echo "0")
else
    LAST_BACKUP_TIME=$(cat "$TIMESTAMP_FILE" 2>/dev/null || echo "0")
fi

if [ "$LAST_BACKUP_TIME" = "0" ]; then
    notification="⚠️ *Ошибка проверки свежести бэкапов*\n\n"
    notification+="Не удалось определить время последнего бэкапа."
    send_telegram_notification "$notification"
    exit 1
fi

CURRENT_TIME=$(date +%s)
AGE_SECONDS=$((CURRENT_TIME - LAST_BACKUP_TIME))

# Проверяем cooldown для уведомлений
SHOULD_ALERT=true
if [ -f "$ALERT_COOLDOWN_FILE" ]; then
    LAST_ALERT_TIME=$(cat "$ALERT_COOLDOWN_FILE" 2>/dev/null || echo "0")
    TIME_SINCE_ALERT=$((CURRENT_TIME - LAST_ALERT_TIME))
    
    if [ "$TIME_SINCE_ALERT" -lt "$ALERT_COOLDOWN_SECONDS" ]; then
        SHOULD_ALERT=false
    fi
fi

# Проверяем возраст последнего бэкапа
if [ "$AGE_SECONDS" -gt "$MAX_AGE_SECONDS" ]; then
    AGE_HOURS=$((AGE_SECONDS / 3600))
    AGE_MINUTES=$(((AGE_SECONDS % 3600) / 60))
    
    if [ "$SHOULD_ALERT" = "true" ]; then
        notification="🔴 *ВНИМАНИЕ: Старый резервный бэкап*\n\n"
        notification+="*Возраст последнего бэкапа:* ${AGE_HOURS}ч ${AGE_MINUTES}м\n"
        notification+="*Максимальный возраст:* 2 часа\n"
        notification+="*Время последнего бэкапа:* $(date -d "@$LAST_BACKUP_TIME" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -r "$LAST_BACKUP_TIME" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "неизвестно")\n"
        notification+="*Текущее время:* $(date '+%Y-%m-%d %H:%M:%S')\n\n"
        notification+="Рекомендуется проверить работу скрипта резервного копирования."
        
        send_telegram_notification "$notification"
        echo "$CURRENT_TIME" > "$ALERT_COOLDOWN_FILE"
        
        exit 1
    fi
else
    # Бэкап свежий, сбрасываем cooldown если он был установлен
    if [ -f "$ALERT_COOLDOWN_FILE" ]; then
        rm -f "$ALERT_COOLDOWN_FILE"
    fi
fi

exit 0

