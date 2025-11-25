#!/usr/bin/env python3
"""
Тестовый скрипт для проверки VPN API версии 2.3.7
Тестирует: создание ключа, получение информации, трафика и удаление
"""
import asyncio
import sys
import os
import logging
from typing import Optional, Dict, Any

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vpn_protocols import V2RayProtocol

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_create_key(protocol: V2RayProtocol) -> Optional[Dict[str, Any]]:
    """Тест 1: Создание ключа"""
    logger.info("=" * 60)
    logger.info("ТЕСТ 1: Создание ключа")
    logger.info("=" * 60)
    
    try:
        test_email = f"test_user_{int(asyncio.get_event_loop().time())}@test.com"
        logger.info(f"Создаю ключ с email: {test_email}")
        
        user_data = await protocol.create_user(test_email)
        
        if not user_data:
            logger.error("❌ ОШИБКА: Ключ не был создан (user_data пустой)")
            return None
        
        if not user_data.get('id'):
            logger.error("❌ ОШИБКА: Ключ не содержит ID")
            return None
        
        if not user_data.get('uuid'):
            logger.error("❌ ОШИБКА: Ключ не содержит UUID")
            return None
        
        logger.info(f"✅ Ключ успешно создан!")
        logger.info(f"   ID: {user_data.get('id')}")
        logger.info(f"   UUID: {user_data.get('uuid')}")
        logger.info(f"   Port: {user_data.get('port', 'N/A')}")
        logger.info(f"   Short ID: {user_data.get('short_id', 'N/A')}")
        logger.info(f"   SNI: {user_data.get('sni', 'N/A')}")
        logger.info(f"   Is Active: {user_data.get('is_active', 'N/A')}")
        logger.info(f"   Created At: {user_data.get('created_at', 'N/A')}")
        
        # Проверяем наличие новых полей API 2.3.7
        if user_data.get('short_id'):
            logger.info("✅ Поле short_id присутствует (API 2.3.7)")
        else:
            logger.warning("⚠️  Поле short_id отсутствует (может быть старая версия API)")
        
        if user_data.get('sni'):
            logger.info("✅ Поле sni присутствует (API 2.3.7)")
        else:
            logger.warning("⚠️  Поле sni отсутствует (может быть старая версия API)")
        
        return user_data
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА при создании ключа: {e}", exc_info=True)
        return None


async def test_get_key_info(protocol: V2RayProtocol, key_id: str) -> bool:
    """Тест 2: Получение информации о ключе"""
    logger.info("=" * 60)
    logger.info("ТЕСТ 2: Получение информации о ключе")
    logger.info("=" * 60)
    
    try:
        logger.info(f"Получаю информацию о ключе: {key_id}")
        
        key_info = await protocol.get_key_info(key_id)
        
        if not key_info:
            logger.error("❌ ОШИБКА: Не удалось получить информацию о ключе")
            return False
        
        if not key_info.get('id'):
            logger.error("❌ ОШИБКА: Информация о ключе не содержит ID")
            return False
        
        logger.info("✅ Информация о ключе успешно получена!")
        logger.info(f"   ID: {key_info.get('id')}")
        logger.info(f"   Name: {key_info.get('name', 'N/A')}")
        logger.info(f"   UUID: {key_info.get('uuid', 'N/A')}")
        logger.info(f"   Port: {key_info.get('port', 'N/A')}")
        logger.info(f"   Short ID: {key_info.get('short_id', 'N/A')}")
        logger.info(f"   SNI: {key_info.get('sni', 'N/A')}")
        logger.info(f"   Is Active: {key_info.get('is_active', 'N/A')}")
        logger.info(f"   Created At: {key_info.get('created_at', 'N/A')}")
        
        # Проверяем наличие новых полей API 2.3.7
        if key_info.get('short_id'):
            logger.info("✅ Поле short_id присутствует (API 2.3.7)")
        else:
            logger.warning("⚠️  Поле short_id отсутствует (может быть старая версия API)")
        
        if key_info.get('sni'):
            logger.info("✅ Поле sni присутствует (API 2.3.7)")
        else:
            logger.warning("⚠️  Поле sni отсутствует (может быть старая версия API)")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА при получении информации о ключе: {e}", exc_info=True)
        return False


async def test_get_key_traffic(protocol: V2RayProtocol, key_id: str) -> bool:
    """Тест 3: Получение трафика ключа"""
    logger.info("=" * 60)
    logger.info("ТЕСТ 3: Получение трафика ключа")
    logger.info("=" * 60)
    
    try:
        logger.info(f"Получаю трафик для ключа: {key_id}")
        
        traffic_stats = await protocol.get_key_traffic_stats(key_id)
        
        if not traffic_stats:
            logger.error("❌ ОШИБКА: Не удалось получить трафик ключа")
            return False
        
        logger.info("✅ Трафик ключа успешно получен!")
        logger.info(f"   Key UUID: {traffic_stats.get('uuid', 'N/A')}")
        logger.info(f"   Total Bytes: {traffic_stats.get('total_bytes', 0)}")
        logger.info(f"   Total Formatted: {traffic_stats.get('total_formatted', 'N/A')}")
        logger.info(f"   Total MB: {traffic_stats.get('total_mb', 0):.2f}")
        logger.info(f"   Timestamp: {traffic_stats.get('timestamp', 'N/A')}")
        logger.info(f"   Status: {traffic_stats.get('status', 'N/A')}")
        
        # Проверяем формат ответа API 2.3.7
        if traffic_stats.get('status') == 'success':
            logger.info("✅ Формат ответа соответствует API 2.3.7 (status: success)")
        else:
            logger.warning("⚠️  Формат ответа может отличаться от API 2.3.7")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА при получении трафика ключа: {e}", exc_info=True)
        return False


async def test_delete_key(protocol: V2RayProtocol, key_id: str) -> bool:
    """Тест 4: Удаление ключа"""
    logger.info("=" * 60)
    logger.info("ТЕСТ 4: Удаление ключа")
    logger.info("=" * 60)
    
    try:
        logger.info(f"Удаляю ключ: {key_id}")
        
        result = await protocol.delete_user(key_id)
        
        if not result:
            logger.error("❌ ОШИБКА: Не удалось удалить ключ")
            return False
        
        logger.info("✅ Ключ успешно удален!")
        return True
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА при удалении ключа: {e}", exc_info=True)
        return False


async def main():
    """Основная функция тестирования"""
    logger.info("=" * 60)
    logger.info("ТЕСТИРОВАНИЕ VPN API версии 2.3.7")
    logger.info("=" * 60)
    
    # Получаем параметры из переменных окружения или аргументов командной строки
    api_url = os.getenv('VPN_API_URL')
    api_key = os.getenv('VPN_API_KEY')
    
    if len(sys.argv) >= 2:
        api_url = sys.argv[1]
    if len(sys.argv) >= 3:
        api_key = sys.argv[2]
    
    if not api_url:
        logger.error("❌ ОШИБКА: Не указан API URL")
        logger.info("Использование: python test_vpn_api.py <api_url> [api_key]")
        logger.info("Или установите переменные окружения: VPN_API_URL и VPN_API_KEY")
        sys.exit(1)
    
    if not api_key:
        logger.warning("⚠️  API ключ не указан, запросы могут не работать")
    
    logger.info(f"API URL: {api_url}")
    logger.info(f"API Key: {'*' * 10 if api_key else 'не указан'}")
    logger.info("")
    
    # Создаем протокол
    protocol = None
    try:
        protocol = V2RayProtocol(api_url, api_key)
        logger.info("✅ Протокол V2Ray инициализирован")
    except Exception as e:
        logger.error(f"❌ ОШИБКА при инициализации протокола: {e}")
        sys.exit(1)
    
    # Запускаем тесты
    test_results = {
        'create': False,
        'get_info': False,
        'get_traffic': False,
        'delete': False
    }
    
    created_key_id = None
    
    try:
        # Тест 1: Создание ключа
        user_data = await test_create_key(protocol)
        if user_data:
            test_results['create'] = True
            created_key_id = user_data.get('id') or user_data.get('uuid')
        
        if not created_key_id:
            logger.error("❌ Не удалось создать ключ, остальные тесты пропущены")
            await protocol.close()
            sys.exit(1)
        
        # Тест 2: Получение информации о ключе
        test_results['get_info'] = await test_get_key_info(protocol, created_key_id)
        
        # Тест 3: Получение трафика
        test_results['get_traffic'] = await test_get_key_traffic(protocol, created_key_id)
        
        # Тест 4: Удаление ключа
        test_results['delete'] = await test_delete_key(protocol, created_key_id)
        
    finally:
        # Закрываем соединение
        if protocol:
            try:
                await protocol.close()
            except Exception as e:
                logger.warning(f"Ошибка при закрытии протокола: {e}")
    
    # Выводим итоги
    logger.info("")
    logger.info("=" * 60)
    logger.info("ИТОГИ ТЕСТИРОВАНИЯ")
    logger.info("=" * 60)
    
    total_tests = len(test_results)
    passed_tests = sum(1 for result in test_results.values() if result)
    
    logger.info(f"Всего тестов: {total_tests}")
    logger.info(f"Пройдено: {passed_tests}")
    logger.info(f"Провалено: {total_tests - passed_tests}")
    logger.info("")
    
    for test_name, result in test_results.items():
        status = "✅ ПРОЙДЕН" if result else "❌ ПРОВАЛЕН"
        logger.info(f"  {test_name.upper()}: {status}")
    
    logger.info("")
    
    if passed_tests == total_tests:
        logger.info("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
        sys.exit(0)
    else:
        logger.error("❌ НЕКОТОРЫЕ ТЕСТЫ ПРОВАЛЕНЫ")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())





