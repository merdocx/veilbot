#!/bin/bash

echo "🚀 Запуск VeilBot..."

# Проверка, не запущен ли уже бот
if pgrep -f "python3 bot.py" > /dev/null; then
    echo "⚠️  Бот уже запущен. Останавливаю предыдущий процесс..."
    pkill -f "python3 bot.py"
    sleep 2
fi

# Проверка файлов
if [ ! -f "bot.py" ]; then
    echo "❌ Файл bot.py не найден!"
    exit 1
fi

if [ ! -f ".env" ]; then
    echo "❌ Файл .env не найден!"
    exit 1
fi

# Проверка токена
if ! grep -q "TELEGRAM_BOT_TOKEN" .env; then
    echo "❌ TELEGRAM_BOT_TOKEN не найден в .env!"
    exit 1
fi

# Создание директории для логов
mkdir -p logs

# Запуск бота с логированием
echo "✅ Запускаю бота..."
nohup python3 bot.py > logs/bot_$(date +%Y%m%d_%H%M%S).log 2>&1 &

# Получение PID
BOT_PID=$!
echo "✅ Бот запущен с PID: $BOT_PID"

# Проверка, что бот запустился
sleep 3
if ps -p $BOT_PID > /dev/null; then
    echo "✅ Бот успешно запущен и работает"
    echo "📋 PID: $BOT_PID"
    echo "📁 Логи: logs/bot_*.log"
    echo "🔍 Для просмотра логов: tail -f logs/bot_*.log"
else
    echo "❌ Ошибка запуска бота"
    echo "📋 Проверьте логи: tail -f logs/bot_*.log"
    exit 1
fi

