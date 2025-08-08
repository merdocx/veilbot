#!/bin/bash

# Скрипт для безопасного перезапуска VeilBot

echo "🔄 Перезапуск VeilBot..."

# Останавливаем systemd сервис
echo "⏹ Останавливаем сервис..."
systemctl stop veilbot

# Ждем завершения
sleep 3

# Запускаем сервис заново
echo "▶️ Запускаем сервис..."
systemctl start veilbot

# Ждем запуска
sleep 5

# Проверяем статус
if systemctl is-active --quiet veilbot; then
    echo "✅ Бот успешно запущен!"
    echo "📊 Статус: $(systemctl is-active veilbot)"
    echo "📋 Логи: journalctl -u veilbot -f"
    echo "📋 Статус: systemctl status veilbot"
else
    echo "❌ Ошибка запуска бота!"
    echo "📋 Проверьте логи: journalctl -u veilbot -n 20"
    exit 1
fi
