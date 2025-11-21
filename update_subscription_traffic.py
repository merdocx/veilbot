#!/usr/bin/env python3
"""
Скрипт для принудительного обновления трафика подписки
"""
import sys
import asyncio
from app.repositories.subscription_repository import SubscriptionRepository
from vpn_protocols import ProtocolFactory
from collections import defaultdict
from utils import get_db_cursor

def format_bytes(bytes_value):
    """Форматировать байты в читаемый формат"""
    if bytes_value is None or bytes_value == 0:
        return "0 B"
    
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} PB"

async def fetch_traffic_from_api(server_id, api_url, api_key, keys):
    """Получить трафик из V2Ray API для ключей на сервере"""
    if not keys:
        return {}
    
    try:
        config = {"api_url": api_url, "api_key": api_key}
        protocol = ProtocolFactory.create_protocol('v2ray', config)
        
        results = {}
        try:
            # Метод 1: Пробуем получить через get_traffic_history (быстрее для всех ключей)
            try:
                history = await protocol.get_traffic_history()
                traffic_map = {}
                if isinstance(history, dict):
                    data = history.get('data') or {}
                    items = data.get('keys') or []
                    for item in items:
                        uuid_val = item.get('key_uuid') or item.get('uuid')
                        total = item.get('total_traffic') or {}
                        total_bytes = total.get('total_bytes')
                        if uuid_val and isinstance(total_bytes, (int, float)):
                            traffic_map[uuid_val] = int(total_bytes)
                
                for key_data in keys:
                    uuid = key_data.get('v2ray_uuid')
                    key_id = key_data.get('id')
                    if uuid and key_id:
                        results[key_id] = traffic_map.get(uuid)
            except Exception as e:
                print(f"    Метод 1 (get_traffic_history) не сработал: {e}")
            
            # Метод 2: Пробуем для каждого ключа индивидуально через get_key_usage_bytes
            if not results:
                print(f"    Пробуем метод 2: индивидуальный запрос для каждого ключа...")
                for key_data in keys:
                    uuid = key_data.get('v2ray_uuid')
                    key_id = key_data.get('id')
                    if uuid and key_id and key_id not in results:
                        try:
                            usage_bytes = await protocol.get_key_usage_bytes(uuid)
                            if usage_bytes is not None:
                                results[key_id] = usage_bytes
                        except Exception as key_error:
                            print(f"    Ошибка получения трафика для ключа {uuid[:8]}...: {key_error}")
            
            # Метод 3: Пробуем через get_key_info (может содержать информацию о трафике)
            if not results:
                print(f"    Пробуем метод 3: через get_key_info...")
                for key_data in keys:
                    uuid = key_data.get('v2ray_uuid')
                    key_id = key_data.get('id')
                    if uuid and key_id and key_id not in results:
                        try:
                            key_info = await protocol.get_key_info(uuid)
                            if key_info:
                                # Проверяем различные возможные поля с трафиком
                                total_bytes = None
                                if 'total_traffic' in key_info:
                                    total_bytes = key_info['total_traffic'].get('total_bytes') if isinstance(key_info['total_traffic'], dict) else None
                                elif 'traffic' in key_info:
                                    total_bytes = key_info['traffic'].get('total_bytes') if isinstance(key_info['traffic'], dict) else None
                                elif 'interface_traffic' in key_info:
                                    if_traffic = key_info['interface_traffic']
                                    if isinstance(if_traffic, dict):
                                        total_bytes = if_traffic.get('total_bytes')
                                
                                if total_bytes is not None and isinstance(total_bytes, (int, float)):
                                    results[key_id] = int(total_bytes)
                        except Exception as info_error:
                            print(f"    Ошибка получения info для ключа {uuid[:8]}...: {info_error}")
            
            # Метод 4: Пробуем через get_traffic_stats
            if not results:
                print(f"    Пробуем метод 4: через get_traffic_stats...")
                try:
                    stats = await protocol.get_traffic_stats()
                    if isinstance(stats, list):
                        for stat in stats:
                            uuid_val = stat.get('key_uuid') or stat.get('uuid')
                            if uuid_val:
                                total_bytes = stat.get('total_bytes', 0)
                                if total_bytes > 0:
                                    # Найти соответствующий key_id
                                    for key_data in keys:
                                        if key_data.get('v2ray_uuid') == uuid_val:
                                            results[key_data.get('id')] = int(total_bytes)
                                            break
                except Exception as stats_error:
                    print(f"    Ошибка получения stats: {stats_error}")
                    
        finally:
            await protocol.close()
        
        return results
    except Exception as e:
        print(f"  Ошибка получения трафика с сервера {server_id}: {e}")
        import traceback
        traceback.print_exc()
        return {}

async def update_subscription_traffic(subscription_id: int):
    """Обновить трафик подписки из V2Ray API"""
    repo = SubscriptionRepository()
    
    # Получить информацию о подписке
    sub_info = repo.get_subscription_by_id(subscription_id)
    if not sub_info:
        print(f"❌ Подписка #{subscription_id} не найдена")
        return
    
    (sub_id, user_id, token, created_at, expires_at, tariff_id, is_active, 
     last_updated_at, notified, tariff_name, keys_count, traffic_limit_mb) = sub_info
    
    print(f"\n📋 Подписка #{subscription_id}")
    print(f"   Пользователь: {user_id}")
    print(f"   Тариф: {tariff_name or 'N/A'}")
    print(f"   Активна: {'Да' if is_active else 'Нет'}")
    print(f"   Ключей: {keys_count}")
    
    # Получить ключи с информацией о серверах
    keys_with_server = repo.get_subscription_keys_with_server_info(subscription_id)
    
    if not keys_with_server:
        print(f"\n⚠️  У подписки нет активных ключей")
        return
    
    print(f"\n🔄 Обновление трафика для {len(keys_with_server)} ключей...")
    
    # Сгруппировать по серверам
    server_keys_map = defaultdict(list)
    server_configs = {}
    
    for key_id, v2ray_uuid, server_id, api_url, api_key in keys_with_server:
        if not api_url or not api_key:
            print(f"   ⚠️  Ключ #{key_id}: отсутствуют API данные сервера")
            continue
        
        config = {"api_url": api_url, "api_key": api_key}
        server_configs[server_id] = config
        
        server_keys_map[server_id].append({
            "id": key_id,
            "v2ray_uuid": v2ray_uuid
        })
    
    # Получить трафик с каждого сервера
    usage_map = {}
    if server_keys_map:
        tasks = [
            fetch_traffic_from_api(server_id, server_configs[server_id]["api_url"], 
                                  server_configs[server_id]["api_key"], keys)
            for server_id, keys in server_keys_map.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, dict):
                usage_map.update(result)
            else:
                print(f"   ❌ Ошибка в задаче получения трафика: {result}")
    
    # Обновить traffic_usage_bytes в БД для всех ключей
    key_updates = []
    for key_id, usage_bytes in usage_map.items():
        if usage_bytes is not None:
            key_updates.append((usage_bytes, key_id))
            print(f"   ✅ Ключ #{key_id}: {format_bytes(usage_bytes)}")
        else:
            print(f"   ⚠️  Ключ #{key_id}: трафик недоступен")
    
    if key_updates:
        with get_db_cursor(commit=True) as cursor:
            cursor.executemany(
                "UPDATE v2ray_keys SET traffic_usage_bytes = ? WHERE id = ?",
                key_updates
            )
        print(f"\n💾 Обновлено {len(key_updates)} ключей в БД")
    else:
        print(f"\n⚠️  Не удалось получить трафик ни для одного ключа")
        return
    
    # Обновить суммарный трафик подписки
    total_usage = repo.get_subscription_traffic_sum(subscription_id)
    repo.update_subscription_traffic(subscription_id, total_usage)
    
    # Получить лимит
    limit_bytes = repo.get_subscription_traffic_limit(subscription_id)
    
    # Показать итоговую информацию
    print(f"\n📊 ИТОГОВАЯ СТАТИСТИКА:")
    print(f"   Суммарный трафик: {format_bytes(total_usage)}")
    
    if limit_bytes > 0:
        limit_mb = limit_bytes / (1024 * 1024)
        usage_percent = (total_usage / limit_bytes) * 100
        remaining = max(0, limit_bytes - total_usage)
        print(f"   Лимит: {format_bytes(limit_bytes)} ({limit_mb:.0f} MB)")
        print(f"   Использовано: {usage_percent:.2f}%")
        print(f"   Осталось: {format_bytes(remaining)}")
        
        if total_usage > limit_bytes:
            print(f"   ⚠️  ПРЕВЫШЕН ЛИМИТ!")
    else:
        print(f"   Лимит: не задан")
    
    # Показать детали по ключам
    print(f"\n🔑 Детали по ключам:")
    keys_list = repo.get_subscription_keys_list(subscription_id)
    for key_row in keys_list:
        (key_id, v2ray_uuid, email, created_at, expires_at, 
         server_name, country, traffic_limit_mb, traffic_usage_bytes) = key_row
        
        api_traffic = usage_map.get(key_id)
        status = "✅" if api_traffic is not None and api_traffic > 0 else "⚠️" if api_traffic == 0 else "❌"
        
        print(f"   {status} Ключ #{key_id} ({v2ray_uuid[:8]}...)")
        print(f"      Сервер: {server_name} ({country})")
        print(f"      Трафик: {format_bytes(traffic_usage_bytes or 0)}")
        if api_traffic is not None and api_traffic != (traffic_usage_bytes or 0):
            print(f"      (API: {format_bytes(api_traffic)})")

if __name__ == "__main__":
    subscription_id = 43
    if len(sys.argv) > 1:
        try:
            subscription_id = int(sys.argv[1])
        except ValueError:
            print(f"Ошибка: неверный ID подписки: {sys.argv[1]}")
            sys.exit(1)
    
    asyncio.run(update_subscription_traffic(subscription_id))

