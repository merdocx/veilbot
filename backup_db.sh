#!/usr/bin/env bash
# Скрипт для резервного копирования базы данных vpn.db c использованием sqlite3 .backup
# Улучшенная версия с уведомлениями, проверкой целостности и улучшенной политикой хранения
#
# Для автоматизации создайте cron-задание (каждый час):
# 0 * * * * /bin/bash /root/veilbot/backup_db.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_FILE="${DATABASE_PATH:-$PROJECT_ROOT/vpn.db}"
BACKUP_DIR="${VEILBOT_BACKUP_DIR:-/var/backups/veilbot}"
LOG_DIR="${VEILBOT_LOG_DIR:-/var/log/veilbot}"
DATE="$(date +"%Y-%m-%d_%H-%M-%S")"
BACKUP_FILE="$BACKUP_DIR/vpn.db.$DATE.sqlite3"
LOG_FILE="$LOG_DIR/backup_db.log"
FRESHNESS_CHECK_FILE="$LOG_DIR/backup_freshness_check.log"

mkdir -p "$BACKUP_DIR" "$LOG_DIR"

# Функция для отправки уведомлений в Telegram
send_telegram_notification() {
    local message="$1"
    local parse_mode="${2:-Markdown}"
    
    # Получаем токен и ID админа из Python настроек
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
        echo "⚠️ Telegram токен не найден, уведомление не отправлено"
        return 0
    fi
    
    # Экранируем специальные символы для URL
    local encoded_message
    encoded_message=$(echo "$message" | sed "s/ /%20/g; s/&/%26/g; s/#/%23/g; s/+/%2B/g; s/=/%3D/g; s/?/%3F/g; s/@/%40/g")
    
    curl -s -X POST "https://api.telegram.org/bot${bot_token}/sendMessage" \
        -d "chat_id=${admin_id}" \
        -d "text=${message}" \
        -d "parse_mode=${parse_mode}" \
        >/dev/null 2>&1 || echo "⚠️ Не удалось отправить уведомление в Telegram"
}

# Функция для обработки ошибок
handle_error() {
    local error_code=$1
    local error_message="$2"
    local context="$3"
    
    echo "❌ Ошибка: $error_message (код: $error_code)"
    
    local notification="🔴 *Ошибка резервного копирования*\n\n"
    notification+="*Контекст:* $context\n"
    notification+="*Ошибка:* $error_message\n"
    notification+="*Время:* $(date '+%Y-%m-%d %H:%M:%S')\n"
    notification+="*Файл БД:* $DB_FILE\n"
    
    send_telegram_notification "$notification"
    exit "$error_code"
}

# Перенаправляем вывод в лог
exec >> "$LOG_FILE" 2>&1

echo "=== VeilBot SQLite Backup ==="
echo "Время: $(date)"
echo "Источник: $DB_FILE"
echo "Backup:   $BACKUP_FILE"

# Проверка существования файла БД
if [ ! -f "$DB_FILE" ]; then
    handle_error 1 "Файл базы данных $DB_FILE не найден!" "Проверка существования БД"
fi

# Проверяем целостность базы в read-only режиме до любых операций
echo "--- Проверка целостности исходной БД ---"
INTEGRITY_RESULT="$(sqlite3 -readonly "$DB_FILE" "PRAGMA integrity_check;" 2>&1)" || INTEGRITY_RESULT="error"
if [ "$INTEGRITY_RESULT" != "ok" ]; then
    handle_error 2 "Проверка целостности не пройдена: $INTEGRITY_RESULT" "Проверка целостности исходной БД"
fi
echo "✅ integrity_check: ok"

# Выполняем контрольную точку журналов WAL перед копированием
echo "--- Контрольная точка WAL ---"
if sqlite3 "$DB_FILE" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null 2>&1; then
    echo "✅ wal_checkpoint выполнен"
else
    echo "⚠️ Не удалось выполнить wal_checkpoint (вероятно, база занята)"
fi

# Создаём резервную копию через встроенную команду .backup
echo "--- Создание резервной копии ---"
if sqlite3 "$DB_FILE" ".backup '$BACKUP_FILE'"; then
    echo "✅ Бэкап создан: $BACKUP_FILE"
    
    # Проверяем размер созданного бэкапа
    BACKUP_SIZE=$(stat -f%z "$BACKUP_FILE" 2>/dev/null || stat -c%s "$BACKUP_FILE" 2>/dev/null || echo "0")
    if [ "$BACKUP_SIZE" -lt 1000 ]; then
        handle_error 3 "Размер бэкапа подозрительно мал: $BACKUP_SIZE байт" "Проверка размера бэкапа"
    fi
    echo "✅ Размер бэкапа: $BACKUP_SIZE байт"
else
    handle_error 3 "Ошибка при создании бэкапа" "Создание резервной копии"
fi

# Проверяем целостность созданного бэкапа
echo "--- Проверка целостности бэкапа ---"
BACKUP_INTEGRITY="$(sqlite3 -readonly "$BACKUP_FILE" "PRAGMA integrity_check;" 2>&1)" || BACKUP_INTEGRITY="error"
if [ "$BACKUP_INTEGRITY" != "ok" ]; then
    handle_error 4 "Проверка целостности бэкапа не пройдена: $BACKUP_INTEGRITY" "Проверка целостности бэкапа"
fi
echo "✅ Целостность бэкапа проверена: ok"

# Периодическая дефрагментация базы данных (только если база не занята)
echo "--- Дефрагментация БД ---"
if sqlite3 "$DB_FILE" "VACUUM;" >/dev/null 2>&1; then
    echo "✅ VACUUM выполнен"
else
    echo "⚠️ Не удалось выполнить VACUUM (вероятно, база занята) - это нормально"
fi

# Улучшенная политика хранения бэкапов
echo "--- Ротация старых бэкапов ---"

# Создаем подкаталоги для разных типов бэкапов
DAILY_DIR="$BACKUP_DIR/daily"
WEEKLY_DIR="$BACKUP_DIR/weekly"
mkdir -p "$DAILY_DIR" "$WEEKLY_DIR"

# Определяем тип бэкапа по времени
HOUR=$(date +%H)
DAY_OF_WEEK=$(date +%u)  # 1-7, где 1 = понедельник

# Копируем бэкап в соответствующий каталог
if [ "$HOUR" = "00" ]; then
    # Ежедневный бэкап в полночь
    DAILY_BACKUP="$DAILY_DIR/vpn.db.$(date +%Y-%m-%d).sqlite3"
    cp "$BACKUP_FILE" "$DAILY_BACKUP"
    echo "✅ Создан ежедневный бэкап: $DAILY_BACKUP"
    
    # Еженедельный бэкап в понедельник в полночь
    if [ "$DAY_OF_WEEK" = "1" ]; then
        WEEKLY_BACKUP="$WEEKLY_DIR/vpn.db.$(date +%Y-%m-%d).sqlite3"
        cp "$BACKUP_FILE" "$WEEKLY_BACKUP"
        echo "✅ Создан еженедельный бэкап: $WEEKLY_BACKUP"
    fi
fi

# Удаляем старые бэкапы по политике:
# - Последние 48 часовых бэкапов (2 дня)
# - Последние 7 ежедневных бэкапов (неделя)
# - Последние 4 еженедельных бэкапа (месяц)

# Удаляем старые часовые бэкапы (оставляем последние 48)
HOURLY_COUNT=$(find "$BACKUP_DIR" -maxdepth 1 -name 'vpn.db.*.sqlite3' -type f | wc -l)
if [ "$HOURLY_COUNT" -gt 48 ]; then
    find "$BACKUP_DIR" -maxdepth 1 -name 'vpn.db.*.sqlite3' -type f | sort -r | tail -n +49 | xargs -r rm --
    echo "✅ Удалены старые часовые бэкапы (оставлено 48)"
fi

# Удаляем старые ежедневные бэкапы (оставляем последние 7)
DAILY_COUNT=$(find "$DAILY_DIR" -name 'vpn.db.*.sqlite3' -type f | wc -l)
if [ "$DAILY_COUNT" -gt 7 ]; then
    find "$DAILY_DIR" -name 'vpn.db.*.sqlite3' -type f | sort -r | tail -n +8 | xargs -r rm --
    echo "✅ Удалены старые ежедневные бэкапы (оставлено 7)"
fi

# Удаляем старые еженедельные бэкапы (оставляем последние 4)
WEEKLY_COUNT=$(find "$WEEKLY_DIR" -name 'vpn.db.*.sqlite3' -type f | wc -l)
if [ "$WEEKLY_COUNT" -gt 4 ]; then
    find "$WEEKLY_DIR" -name 'vpn.db.*.sqlite3' -type f | sort -r | tail -n +5 | xargs -r rm --
    echo "✅ Удалены старые еженедельные бэкапы (оставлено 4)"
fi

# Сохраняем метку времени последнего успешного бэкапа
echo "$(date +%s)" > "$BACKUP_DIR/.last_backup_timestamp"
echo "$BACKUP_FILE" > "$BACKUP_DIR/.last_backup_file"

echo "=== Завершено: $(date) ==="
echo ""

# Отправляем успешное уведомление только при создании ежедневного бэкапа
if [ "$HOUR" = "00" ]; then
    notification="✅ *Резервное копирование завершено*\n\n"
    notification+="*Тип:* Ежедневный бэкап\n"
    notification+="*Файл:* \`$(basename "$BACKUP_FILE")\`\n"
    notification+="*Размер:* $(du -h "$BACKUP_FILE" | cut -f1)\n"
    notification+="*Время:* $(date '+%Y-%m-%d %H:%M:%S')\n"
    notification+="*Целостность:* Проверена ✅"
    
    send_telegram_notification "$notification"
fi

exit 0
