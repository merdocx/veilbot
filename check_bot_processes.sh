#!/bin/bash

echo "🔍 Проверка запущенных процессов VeilBot..."

# Поиск всех процессов Python, связанных с bot.py
BOT_PROCESSES=$(ps aux | grep -E "(bot\.py|python.*bot)" | grep -v grep)

if [ -z "$BOT_PROCESSES" ]; then
    echo "✅ Процессы VeilBot не найдены"
else
    echo "📋 Найдены процессы VeilBot:"
    echo "$BOT_PROCESSES"
    
    echo ""
    echo "🛑 Остановка всех процессов VeilBot..."
    
    # Остановка процессов по PID
    ps aux | grep -E "(bot\.py|python.*bot)" | grep -v grep | awk '{print $2}' | while read pid; do
        echo "Останавливаю процесс PID: $pid"
        kill -TERM $pid 2>/dev/null || kill -KILL $pid 2>/dev/null
    done
    
    # Ждем завершения процессов
    sleep 2
    
    # Проверяем, остались ли процессы
    REMAINING_PROCESSES=$(ps aux | grep -E "(bot\.py|python.*bot)" | grep -v grep)
    
    if [ -z "$REMAINING_PROCESSES" ]; then
        echo "✅ Все процессы VeilBot остановлены"
    else
        echo "⚠️ Некоторые процессы все еще работают:"
        echo "$REMAINING_PROCESSES"
        echo "Попытка принудительной остановки..."
        
        ps aux | grep -E "(bot\.py|python.*bot)" | grep -v grep | awk '{print $2}' | while read pid; do
            echo "Принудительно останавливаю процесс PID: $pid"
            kill -KILL $pid 2>/dev/null
        done
        
        sleep 1
        
        FINAL_CHECK=$(ps aux | grep -E "(bot\.py|python.*bot)" | grep -v grep)
        if [ -z "$FINAL_CHECK" ]; then
            echo "✅ Все процессы VeilBot принудительно остановлены"
        else
            echo "❌ Не удалось остановить все процессы:"
            echo "$FINAL_CHECK"
        fi
    fi
fi

echo ""
echo "🧹 Очистка временных файлов..."
rm -f /tmp/veilbot_*.pid 2>/dev/null
rm -f /tmp/bot_*.lock 2>/dev/null

echo "✅ Проверка завершена"

