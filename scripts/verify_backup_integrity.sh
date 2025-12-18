#!/usr/bin/env bash
# Скрипт для проверки целостности резервных копий БД
# Проверяет последние бэкапы и уведомляет о проблемах
#
# Для автоматизации создайте cron-задание (раз в неделю):
# 0 2 * * 0 /bin/bash /root/veilbot/scripts/verify_backup_integrity.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${VEILBOT_BACKUP_DIR:-/var/backups/veilbot}"
LOG_DIR="${VEILBOT_LOG_DIR:-/var/log/veilbot}"
VERIFY_LOG="$LOG_DIR/backup_verify.log"
BACKUPS_TO_CHECK=5  # Проверяем последние 5 бэкапов

mkdir -p "$BACKUP_DIR" "$LOG_DIR"

exec >> "$VERIFY_LOG" 2>&1

echo "=== Проверка целостности резервных копий ==="
echo "Время: $(date)"
echo ""

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

# Находим последние бэкапы
LATEST_BACKUPS=$(find "$BACKUP_DIR" -maxdepth 1 -name 'vpn.db.*.sqlite3' -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -n "$BACKUPS_TO_CHECK" | cut -d' ' -f2-)

if [ -z "$LATEST_BACKUPS" ]; then
    echo "❌ Резервные копии не найдены!"
    notification="❌ *Проверка целостности бэкапов*\n\nРезервные копии не найдены в директории \`$BACKUP_DIR\`"
    send_telegram_notification "$notification"
    exit 1
fi

CHECKED_COUNT=0
FAILED_COUNT=0
FAILED_BACKUPS=()

while IFS= read -r backup_file; do
    if [ -z "$backup_file" ]; then
        continue
    fi
    
    CHECKED_COUNT=$((CHECKED_COUNT + 1))
    backup_name=$(basename "$backup_file")
    
    echo "--- Проверка: $backup_name ---"
    
    # Проверяем целостность
    INTEGRITY_RESULT=$(sqlite3 -readonly "$backup_file" "PRAGMA integrity_check;" 2>&1)
    
    if [ "$INTEGRITY_RESULT" = "ok" ]; then
        echo "✅ Целостность: OK"
        
        # Дополнительная проверка: пытаемся открыть базу и выполнить простой запрос
        QUERY_RESULT=$(sqlite3 -readonly "$backup_file" "SELECT COUNT(*) FROM sqlite_master;" 2>&1)
        if [ $? -eq 0 ]; then
            echo "✅ База данных читаема"
        else
            echo "⚠️ Предупреждение: база данных не читаема ($QUERY_RESULT)"
            FAILED_COUNT=$((FAILED_COUNT + 1))
            FAILED_BACKUPS+=("$backup_name (не читаема)")
        fi
    else
        echo "❌ Целостность: FAILED - $INTEGRITY_RESULT"
        FAILED_COUNT=$((FAILED_COUNT + 1))
        FAILED_BACKUPS+=("$backup_name (целостность нарушена)")
    fi
    
    echo ""
done <<< "$LATEST_BACKUPS"

# Формируем отчет
echo "=== Результаты проверки ==="
echo "Проверено бэкапов: $CHECKED_COUNT"
echo "Успешных: $((CHECKED_COUNT - FAILED_COUNT))"
echo "С ошибками: $FAILED_COUNT"
echo ""

if [ "$FAILED_COUNT" -gt 0 ]; then
    FAILED_LIST=$(IFS=$'\n'; echo "${FAILED_BACKUPS[*]}")
    
    notification="🔴 *Проверка целостности бэкапов: ОШИБКИ*\n\n"
    notification+="*Проверено бэкапов:* $CHECKED_COUNT\n"
    notification+="*С ошибками:* $FAILED_COUNT\n\n"
    notification+="*Проблемные бэкапы:*\n\`\`\`\n$FAILED_LIST\n\`\`\`\n\n"
    notification+="*Время проверки:* $(date '+%Y-%m-%d %H:%M:%S')\n\n"
    notification+="Рекомендуется проверить систему резервного копирования."
    
    send_telegram_notification "$notification"
    exit 1
else
    notification="✅ *Проверка целостности бэкапов: УСПЕШНО*\n\n"
    notification+="*Проверено бэкапов:* $CHECKED_COUNT\n"
    notification+="*Все бэкапы целостны и читаемы* ✅\n\n"
    notification+="*Время проверки:* $(date '+%Y-%m-%d %H:%M:%S')"
    
    send_telegram_notification "$notification"
    echo "✅ Все проверенные бэкапы целостны"
    exit 0
fi

