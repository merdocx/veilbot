#!/usr/bin/env python3
"""
Тестовый скрипт для проверки работы monitor_subscription_traffic_limits
"""
import asyncio
import sys
import logging
from app.repositories.subscription_repository import SubscriptionRepository
from vpn_protocols import ProtocolFactory
from collections import defaultdict
from utils import get_db_cursor
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_monitor_subscription_traffic():
    """Тестовая функция для проверки работы monitor_subscription_traffic_limits"""
    now = int(time.time())
    repo = SubscriptionRepository()
    
    logger.info("=" * 60)
    logger.info("ТЕСТ: monitor_subscription_traffic_limits")
    logger.info("=" * 60)
    
    # Получить активные подписки с лимитами
    subscriptions = repo.get_subscriptions_with_traffic_limits(now)
    logger.info(f"Найдено подписок с лимитами: {len(subscriptions)}")
    
    if not subscriptions:
        logger.warning("Нет активных подписок с лимитами трафика!")
        return
    
    for sub in subscriptions:
        subscription_id, user_id, stored_usage, over_limit_at, notified_flags, expires_at, tariff_id, limit_mb, tariff_name = sub
        logger.info(f"\nПодписка #{subscription_id}:")
        logger.info(f"  Пользователь: {user_id}")
        logger.info(f"  Тариф: {tariff_name}")
        logger.info(f"  Лимит: {limit_mb} MB")
        logger.info(f"  Текущий трафик в БД: {stored_usage or 0} bytes")
    
    # Собрать все ключи подписок, сгруппированные по серверам
    server_configs = {}
    server_keys_map = defaultdict(list)
    
    logger.info("\n" + "=" * 60)
    logger.info("Сбор ключей подписок...")
    
    for sub in subscriptions:
        subscription_id = sub[0]
        keys = repo.get_subscription_keys_with_server_info(subscription_id)
        logger.info(f"Подписка #{subscription_id}: найдено {len(keys)} ключей")
        
        for key_id, v2ray_uuid, server_id, api_url, api_key in keys:
            if not api_url or not api_key:
                logger.warning(f"  Ключ #{key_id}: отсутствуют API данные сервера")
                continue
            
            config = {"api_url": api_url, "api_key": api_key}
            server_configs[server_id] = config
            
            key_data = {
                "id": key_id,
                "v2ray_uuid": v2ray_uuid,
                "subscription_id": subscription_id
            }
            server_keys_map[server_id].append(key_data)
            logger.info(f"  Ключ #{key_id} ({v2ray_uuid[:8]}...) добавлен для сервера #{server_id}")
    
    logger.info(f"\nВсего серверов: {len(server_keys_map)}")
    
    # Функция получения трафика
    async def _fetch_usage_for_subscription_keys(server_id: int, config: dict, keys: list) -> dict:
        """Получить трафик для ключей подписки с одного сервера из V2Ray API"""
        if not keys:
            return {}
        try:
            protocol = ProtocolFactory.create_protocol('v2ray', config)
        except Exception as e:
            logger.error(f"[Сервер #{server_id}] Ошибка инициализации протокола: {e}")
            return {}

        results = {}
        try:
            logger.info(f"[Сервер #{server_id}] Получение истории трафика...")
            history = await protocol.get_traffic_history()
            traffic_map = {}
            if isinstance(history, dict):
                data = history.get('data') or {}
                items = data.get('keys') or []
                logger.info(f"[Сервер #{server_id}] Получено {len(items)} ключей из истории")
                for item in items:
                    uuid_val = item.get('key_uuid') or item.get('uuid')
                    total = item.get('total_traffic') or {}
                    total_bytes = total.get('total_bytes')
                    if uuid_val and isinstance(total_bytes, (int, float)):
                        traffic_map[uuid_val] = int(total_bytes)
                        logger.info(f"[Сервер #{server_id}] Ключ {uuid_val[:8]}...: {total_bytes} bytes")
            
            for key_row in keys:
                uuid = key_row.get('v2ray_uuid')
                key_pk = key_row.get('id')
                if uuid is None or key_pk is None:
                    continue
                traffic = traffic_map.get(uuid)
                results[key_pk] = traffic
                if traffic is not None:
                    logger.info(f"[Сервер #{server_id}] Ключ #{key_pk}: {traffic} bytes")
                else:
                    logger.warning(f"[Сервер #{server_id}] Ключ #{key_pk}: трафик не найден в истории")
        except Exception as e:
            logger.error(f"[Сервер #{server_id}] Ошибка получения трафика: {e}", exc_info=True)
        finally:
            try:
                await protocol.close()
            except Exception as close_error:
                logger.warning(f"[Сервер #{server_id}] Ошибка закрытия протокола: {close_error}")
        return results
    
    # Получить трафик из V2Ray API для всех ключей
    logger.info("\n" + "=" * 60)
    logger.info("Получение трафика из V2Ray API...")
    usage_map = {}
    if server_keys_map:
        tasks = [
            _fetch_usage_for_subscription_keys(server_id, server_configs[server_id], keys)
            for server_id, keys in server_keys_map.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, dict):
                usage_map.update(result)
                logger.info(f"Обновлено {len(result)} ключей из результата")
            else:
                logger.error(f"Ошибка в задаче получения трафика: {result}", exc_info=True)
    
    logger.info(f"\nВсего получено трафика для {len(usage_map)} ключей")
    
    # Обновить traffic_usage_bytes в БД для всех ключей подписок
    key_updates = []
    for key_id, usage_bytes in usage_map.items():
        if usage_bytes is not None:
            key_updates.append((usage_bytes, key_id))
    
    logger.info("\n" + "=" * 60)
    logger.info(f"Обновление БД: {len(key_updates)} ключей")
    
    if key_updates:
        with get_db_cursor(commit=True) as cursor:
            cursor.executemany(
                "UPDATE v2ray_keys SET traffic_usage_bytes = ? WHERE id = ?",
                key_updates
            )
        logger.info(f"✅ Обновлено {len(key_updates)} ключей в БД")
    else:
        logger.warning("⚠️  Нет ключей для обновления!")
    
    # Обновить суммарный трафик подписок
    logger.info("\n" + "=" * 60)
    logger.info("Обновление суммарного трафика подписок...")
    
    for sub in subscriptions:
        subscription_id, user_id, stored_usage, over_limit_at, notified_flags, expires_at, tariff_id, limit_mb, tariff_name = sub
        
        # Агрегировать трафик всех ключей подписки
        total_usage = repo.get_subscription_traffic_sum(subscription_id)
        logger.info(f"Подписка #{subscription_id}: суммарный трафик = {total_usage} bytes")
        
        # Обновить traffic_usage_bytes в подписке
        repo.update_subscription_traffic(subscription_id, total_usage)
        logger.info(f"✅ Подписка #{subscription_id} обновлена")
    
    logger.info("\n" + "=" * 60)
    logger.info("ТЕСТ ЗАВЕРШЕН")
    logger.info("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_monitor_subscription_traffic())





