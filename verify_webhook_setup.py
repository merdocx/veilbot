#!/usr/bin/env python3
"""
Проверка настройки webhook CryptoBot
"""
import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

async def check_webhook_status():
    """Проверка статуса webhook в CryptoBot"""
    api_token = os.getenv('CRYPTOBOT_API_TOKEN')
    if not api_token:
        print("❌ CRYPTOBOT_API_TOKEN не найден")
        return
    
    print("🔍 Проверка настройки webhook в CryptoBot...\n")
    
    try:
        async with aiohttp.ClientSession() as session:
            # Проверяем информацию о webhook
            async with session.get(
                'https://pay.crypt.bot/api/getWebhookInfo',
                headers={'Crypto-Pay-API-Token': api_token},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('ok'):
                        result = data.get('result', {})
                        webhook_url = result.get('url', 'не настроен')
                        
                        print("=" * 60)
                        print("📋 Статус webhook в CryptoBot:")
                        print("=" * 60)
                        print(f"URL: {webhook_url}")
                        print(f"Ожидающих обновлений: {result.get('pending_update_count', 0)}")
                        
                        last_error = result.get('last_error_message')
                        if last_error:
                            print(f"⚠️  Последняя ошибка: {last_error}")
                        else:
                            print("✅ Ошибок нет")
                        
                        last_error_date = result.get('last_error_date')
                        if last_error_date:
                            print(f"Дата последней ошибки: {last_error_date}")
                        
                        max_connections = result.get('max_connections', 'N/A')
                        print(f"Макс. соединений: {max_connections}")
                        
                        print("=" * 60)
                        
                        if webhook_url and webhook_url == "https://veil-bot.ru/cryptobot/webhook":
                            print("✅ Webhook настроен правильно!")
                            print("✅ URL соответствует ожидаемому")
                            
                            if result.get('pending_update_count', 0) > 0:
                                print(f"⚠️  Есть {result.get('pending_update_count')} необработанных обновлений")
                                print("   Это может означать, что webhook не отвечает или отвечает с ошибкой")
                            
                            if last_error:
                                print(f"\n⚠️  ВНИМАНИЕ: Есть ошибки webhook!")
                                print(f"   Ошибка: {last_error}")
                                print("\n💡 Рекомендации:")
                                print("   1. Проверьте, что админ-панель запущена")
                                print("   2. Проверьте логи nginx")
                                print("   3. Проверьте доступность URL из интернета")
                            else:
                                print("\n✅ Webhook работает без ошибок!")
                        else:
                            print("⚠️  Webhook не настроен или настроен на другой URL")
                    else:
                        error = data.get('error', {})
                        print(f"❌ API вернул ошибку: {error.get('name', 'unknown')}")
                        print(f"   Код: {error.get('code', 'unknown')}")
                elif response.status == 405:
                    print("⚠️  Метод getWebhookInfo не поддерживается этой версией API")
                    print("   Это нормально - webhook может быть настроен через бота")
                else:
                    print(f"⚠️  HTTP статус: {response.status}")
                    
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(check_webhook_status())

