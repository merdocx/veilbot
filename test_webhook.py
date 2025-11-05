#!/usr/bin/env python3
"""
Тестирование webhook CryptoBot
"""
import asyncio
import aiohttp
import json

async def test_webhook():
    """Тест webhook endpoint"""
    webhook_url = "https://veil-bot.ru/cryptobot/webhook"
    
    # Тестовый payload (как от CryptoBot)
    test_payload = {
        "update_id": 12345,
        "update_type": "invoice_paid",
        "request_date": "2024-01-01T12:00:00Z",
        "payload": {
            "invoice_id": 999999,  # Тестовый ID
            "hash": "test_hash",
            "asset": "USDT",
            "amount": "10.00",
            "paid_btn_name": "callback",
            "paid_btn_url": "https://t.me/veilbot_bot"
        }
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url,
                json=test_payload,
                timeout=aiohttp.ClientTimeout(total=10),
                ssl=False
            ) as response:
                status = response.status
                text = await response.text()
                
                print(f"📡 Тест webhook:")
                print(f"   URL: {webhook_url}")
                print(f"   Статус: {status}")
                print(f"   Ответ: {text[:200]}")
                
                if status == 200:
                    print("   ✅ Webhook endpoint доступен и отвечает")
                    try:
                        data = json.loads(text)
                        if data.get('status') == 'ok':
                            print("   ✅ Webhook обработал запрос корректно")
                        elif data.get('status') == 'error':
                            print(f"   ⚠️  Webhook вернул ошибку: {data.get('reason', 'unknown')}")
                            print("   (Это нормально для тестового запроса)")
                    except:
                        pass
                else:
                    print(f"   ⚠️  Неожиданный статус: {status}")
                
                return status == 200
                
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return False

if __name__ == "__main__":
    result = asyncio.run(test_webhook())
    exit(0 if result else 1)

