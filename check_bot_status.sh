#!/bin/bash

echo "🔍 Проверка состояния VeilBot..."

# Поиск процессов бота
BOT_PROCESSES=$(ps aux | grep -E "(bot\.py|python.*bot)" | grep -v grep)

if [ -z "$BOT_PROCESSES" ]; then
    echo "❌ Бот не запущен"
    echo "🚀 Для запуска используйте: ./start_bot.sh"
    exit 1
fi

echo "✅ Бот запущен:"
echo "$BOT_PROCESSES"

# Получение PID
BOT_PID=$(echo "$BOT_PROCESSES" | awk '{print $2}' | head -1)
echo ""
echo "📋 PID: $BOT_PID"

# Проверка использования памяти
MEMORY_INFO=$(ps -o pid,ppid,cmd,%mem,%cpu --no-headers -p $BOT_PID)
echo "💾 Использование ресурсов:"
echo "$MEMORY_INFO"

# Проверка последних логов
echo ""
echo "📋 Последние логи:"
if [ -d "logs" ]; then
    LATEST_LOG=$(ls -t logs/bot_*.log 2>/dev/null | head -1)
    if [ -n "$LATEST_LOG" ]; then
        echo "📁 Файл: $LATEST_LOG"
        echo "--- Последние 10 строк ---"
        tail -10 "$LATEST_LOG"
    else
        echo "❌ Логи не найдены"
    fi
else
    echo "❌ Директория logs не найдена"
fi

# Проверка базы данных
echo ""
echo "🗄️  Проверка базы данных..."
if [ -f "vpn.db" ]; then
    DB_SIZE=$(du -h vpn.db | cut -f1)
    echo "✅ База данных найдена, размер: $DB_SIZE"
elif [ -f "veilbot.db" ]; then
    DB_SIZE=$(du -h veilbot.db | cut -f1)
    echo "✅ База данных найдена, размер: $DB_SIZE"
else
    echo "❌ База данных не найдена"
    echo "🔍 Ищем файлы БД:"
    ls -la *.db 2>/dev/null || echo "Файлы .db не найдены"
fi

# Проверка конфигурации
echo ""
echo "⚙️  Проверка конфигурации..."
if [ -f ".env" ]; then
    if grep -q "TELEGRAM_BOT_TOKEN" .env; then
        echo "✅ TELEGRAM_BOT_TOKEN настроен"
    else
        echo "❌ TELEGRAM_BOT_TOKEN не настроен"
    fi
    
    if grep -q "ADMIN_ID" .env; then
        echo "✅ ADMIN_ID настроен"
    else
        echo "❌ ADMIN_ID не настроен"
    fi
else
    echo "❌ Файл .env не найден"
fi
