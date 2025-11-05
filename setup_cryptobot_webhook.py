#!/usr/bin/env python3
"""
Скрипт для настройки webhook CryptoBot через API
"""
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

API_TOKEN = os.getenv('CRYPTOBOT_API_TOKEN')
WEBHOOK_URL = "https://veil-bot.ru/cryptobot/webhook"

if not API_TOKEN:
    print("❌ Ошибка: CRYPTOBOT_API_TOKEN не найден в .env")
    sys.exit(1)

print(f"🔗 Настройка webhook для CryptoBot...")
print(f"URL: {WEBHOOK_URL}")
print(f"API Token: {API_TOKEN[:15]}...")

# Метод 1: Попытка установить webhook через API
try:
    response = requests.post(
        "https://pay.crypt.bot/api/setWebhook",
        headers={
            "Crypto-Pay-API-Token": API_TOKEN,
            "Content-Type": "application/json"
        },
        json={
            "url": WEBHOOK_URL
        },
        timeout=10
    )
    
    result = response.json()
    
    if result.get('ok'):
        print("✅ Webhook успешно настроен через API!")
        print(f"Результат: {result.get('result', {})}")
    else:
        error = result.get('error', {})
        error_code = error.get('code', 'unknown')
        error_name = error.get('name', 'unknown')
        
        print(f"⚠️  API вернул ошибку: {error_code} - {error_name}")
        
        if error_code == 'SSL_ERROR' or 'certificate' in error_name.lower():
            print("\n📋 Проблема: CryptoBot требует проверку SSL сертификата")
            print("\n💡 Решения:")
            print("1. Использовать валидный SSL сертификат (Let's Encrypt)")
            print("2. Загрузить самоподписанный сертификат через бота @CryptoBot")
            print("3. Использовать polling (автоматическая проверка каждые 10 секунд)")
            print("\n⚠️  ВАЖНО: Webhook не обязателен! Система работает через polling.")
            print("   Пользователи получат ключ через 10-30 секунд после оплаты.")
        else:
            print(f"   Полная ошибка: {result}")
    
except requests.exceptions.RequestException as e:
    print(f"❌ Ошибка подключения: {e}")
    print("\n💡 Проверьте:")
    print("   - Доступность URL из интернета")
    print("   - Правильность токена")
    print("   - Интернет соединение")

print("\n" + "="*60)
print("ℹ️  Информация:")
print("="*60)
print("Webhook ускоряет выдачу ключа (мгновенно), но не обязателен.")
print("Система автоматически проверяет статус инвойсов каждые 10 секунд.")
print("Пользователи получат ключ в течение 10-30 секунд после оплаты.")

