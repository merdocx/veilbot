#!/usr/bin/env python3
"""
Скрипт для синхронизации всех ключей V2Ray и Outline с серверами
Полностью переписан согласно ТЗ для оптимизации и упрощения логики
"""
import sys
import os
import asyncio
import logging
import time
import urllib.parse
from typing import List, Tuple, Dict, Any, Optional, Set

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.settings import settings
from app.infra.sqlite_utils import get_db_cursor
from app.infra.foreign_keys import safe_foreign_keys_off
from vpn_protocols import ProtocolFactory, normalize_vless_host, remove_fragment_from_vless
from bot.services.subscription_service import invalidate_subscription_cache

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def extract_v2ray_uuid(remote_entry: Dict[str, Any]) -> Optional[str]:
    """Извлечь UUID ключа V2Ray из ответа сервера"""
    uuid = remote_entry.get("uuid")
    if not uuid:
        key_info = remote_entry.get("key") or {}
        if isinstance(key_info, dict):
            uuid = key_info.get("uuid")
    if isinstance(uuid, str) and uuid.strip():
        return uuid.strip()
    return None


def extract_outline_key_id(remote_entry: Dict[str, Any]) -> Optional[str]:
    """Извлечь ID ключа Outline из ответа сервера"""
    key_id = remote_entry.get("id")
    if key_id is not None:
        return str(key_id).strip()
    return None


class ServerClientPool:
    """Пул клиентов для переиспользования соединений к серверам"""
    
    def __init__(self):
        self._clients: Dict[int, Any] = {}
        self._outline_keys_cache: Dict[int, List[Dict]] = {}
        self._v2ray_keys_cache: Dict[int, List[Dict]] = {}
    
    async def get_client(self, server: Dict[str, Any]) -> Optional[Any]:
        """Получить или создать клиент для сервера"""
        server_id = server["id"]
        if server_id not in self._clients:
            try:
                if server["protocol"] == "v2ray":
                    if not server.get("api_url") or not server.get("api_key"):
                        return None
                    self._clients[server_id] = ProtocolFactory.create_protocol("v2ray", {
                        "api_url": server["api_url"],
                        "api_key": server["api_key"],
                        "domain": server.get("domain"),
                    })
                elif server["protocol"] == "outline":
                    if not server.get("api_url"):
                        return None
                    self._clients[server_id] = ProtocolFactory.create_protocol("outline", {
                        "api_url": server["api_url"],
                        "cert_sha256": server.get("cert_sha256") or "",
                    })
                else:
                    return None
            except Exception as e:
                logger.warning(f"Не удалось создать клиент для сервера #{server_id}: {e}")
                return None
        return self._clients.get(server_id)
    
    async def get_all_keys_cached(self, server_id: int, protocol: str, client: Any) -> List[Dict]:
        """Получить все ключи с сервера с кэшированием"""
        cache_key = server_id
        if protocol == "outline":
            if cache_key not in self._outline_keys_cache:
                try:
                    self._outline_keys_cache[cache_key] = await asyncio.wait_for(
                        client.get_all_keys(),
                        timeout=30.0
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"Таймаут при получении ключей с сервера #{server_id}")
                    return []
                except Exception as e:
                    logger.warning(f"Ошибка получения ключей с сервера #{server_id}: {e}")
                    return []
            return self._outline_keys_cache.get(cache_key, [])
        elif protocol == "v2ray":
            if cache_key not in self._v2ray_keys_cache:
                try:
                    self._v2ray_keys_cache[cache_key] = await asyncio.wait_for(
                        client.get_all_keys(),
                        timeout=30.0
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"Таймаут при получении ключей с сервера #{server_id}")
                    return []
                except Exception as e:
                    logger.warning(f"Ошибка получения ключей с сервера #{server_id}: {e}")
                    return []
            return self._v2ray_keys_cache.get(cache_key, [])
        return []
    
    async def close_all(self):
        """Закрыть все клиенты"""
        for server_id, client in self._clients.items():
            try:
                await client.close()
            except Exception as e:
                logger.warning(f"Ошибка закрытия клиента для сервера #{server_id}: {e}")
        self._clients.clear()
        self._outline_keys_cache.clear()
        self._v2ray_keys_cache.clear()


async def check_server_availability(
    protocol: str,
    api_url: str,
    api_key: Optional[str] = None,
    cert_sha256: Optional[str] = None,
    timeout: float = 10.0
) -> bool:
    """Проверить доступность сервера через API"""
    try:
        if protocol == "v2ray":
            server_config = {"api_url": api_url, "api_key": api_key or ""}
            protocol_client = ProtocolFactory.create_protocol("v2ray", server_config)
        elif protocol == "outline":
            server_config = {"api_url": api_url, "cert_sha256": cert_sha256 or ""}
            protocol_client = ProtocolFactory.create_protocol("outline", server_config)
        else:
            return False
        
        try:
            # Пытаемся получить список ключей с таймаутом
            await asyncio.wait_for(protocol_client.get_all_keys(), timeout=timeout)
            return True
        finally:
            # Закрываем клиент только если у него есть метод close
            if hasattr(protocol_client, 'close'):
                try:
                    await protocol_client.close()
                except Exception:
                    pass
    except asyncio.TimeoutError:
        logger.warning(f"Таймаут при проверке доступности сервера {api_url}")
        return False
    except Exception as e:
        logger.warning(f"Сервер {api_url} недоступен: {e}")
        return False


async def sync_all_keys_with_servers(
    dry_run: bool = False,
    server_id: Optional[int] = None,
    create_missing: bool = True,
    delete_orphaned_on_servers: bool = True,
    delete_inactive_server_keys: bool = True,
    sync_configs: bool = True,
    include_v2ray: bool = True,
    include_outline: bool = True,
) -> dict:
    """
    Синхронизировать все ключи V2Ray и Outline с серверами согласно ТЗ.
    
    Алгоритм:
    1. Подготовка данных
    2. Удаление ключей для недоступных серверов (active = 0 или удалены) - если delete_inactive_server_keys=True
    3. Синхронизация V2Ray ключей (если include_v2ray=True):
       - Создание недостающих ключей (только для available_for_purchase = 1) - если create_missing=True
       - Удаление лишних ключей с серверов - если delete_orphaned_on_servers=True
       - Синхронизация VLESS ссылок (для всех активных серверов) - если sync_configs=True
    4. Синхронизация Outline ключей (если include_outline=True):
       - Создание недостающих ключей на сервере №8 (если available_for_purchase = 1) - если create_missing=True
       - Удаление лишних ключей - если delete_orphaned_on_servers=True
       - Синхронизация access_url (для всех активных серверов) - если sync_configs=True
    
    Args:
        dry_run: Если True, только показывает что будет обновлено, не изменяет БД
        server_id: Если указан, синхронизирует только ключи с этого сервера
        create_missing: Создавать недостающие ключи для подписок
        delete_orphaned_on_servers: Удалять лишние ключи с серверов (которых нет в БД)
        delete_inactive_server_keys: Удалять ключи для неактивных/удалённых серверов из БД
        sync_configs: Синхронизировать конфигурации (VLESS / access_url)
        include_v2ray: Включать синхронизацию V2Ray ключей
        include_outline: Включать синхронизацию Outline ключей
    
    Returns:
        dict: Словарь со статистикой синхронизации
    """
    start_time = time.time()
    stats = {
        "servers_processed": 0,
        "servers_unavailable": 0,
        "keys_deleted_from_db": 0,
        "keys_deleted_from_servers": 0,
        "v2ray_keys_created": 0,
        "v2ray_configs_updated": 0,
        "outline_keys_created": 0,
        "outline_configs_updated": 0,
        "outline_keys_removed": 0,
        "errors": 0,
        "errors_details": [],
        "duration_seconds": 0.0,
    }

    now = int(time.time())
    api_semaphore = asyncio.Semaphore(10)  # Rate limiting для API-запросов
    
    logger.info("=" * 60)
    logger.info("Начало синхронизации ключей")
    logger.info("=" * 60)
    
    # ========== ЭТАП 1: Подготовка данных ==========
    logger.info("\n[ЭТАП 1] Подготовка данных...")
    
    # Получаем серверы: только активные (active=1), чтобы не пытаться создавать ключи
    # или синхронизировать с удалёнными/неактивными серверами. Неактивные учитываются
    # отдельно для удаления ключей из БД (этап 2).
    with get_db_cursor() as cursor:
        if server_id:
            cursor.execute("""
                SELECT id, name, protocol, api_url, api_key, cert_sha256, domain,
                       active, COALESCE(access_level, 'all') as access_level,
                       COALESCE(available_for_purchase, 1) as available_for_purchase
                FROM servers
                WHERE id = ?
            """, (server_id,))
        else:
            cursor.execute("""
                SELECT id, name, protocol, api_url, api_key, cert_sha256, domain,
                       active, COALESCE(access_level, 'all') as access_level,
                       COALESCE(available_for_purchase, 1) as available_for_purchase
                FROM servers
                WHERE active = 1
            """)
        server_rows = cursor.fetchall()

    servers = []
    for row in server_rows:
        servers.append({
            "id": row[0],
            "name": row[1] or f"Server #{row[0]}",
            "protocol": row[2] or "outline",
            "api_url": row[3],
            "api_key": row[4],
            "cert_sha256": row[5],
            "domain": row[6],
            "active": bool(row[7]),
            "access_level": row[8] or 'all',
            "available_for_purchase": bool(row[9]) if len(row) > 9 else True,
        })
    
    # Получаем активные подписки с информацией о тарифе (для проверки платности)
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT s.id, s.user_id, s.subscription_token, s.expires_at, s.tariff_id,
                   COALESCE(t.price_rub, 0) as price_rub
            FROM subscriptions s
            LEFT JOIN tariffs t ON s.tariff_id = t.id
            WHERE s.is_active = 1 AND s.expires_at > ?
        """, (now,))
        subscription_rows = cursor.fetchall()

    subscriptions = []
    for row in subscription_rows:
        subscriptions.append({
            "id": row[0],
            "user_id": row[1],
            "subscription_token": row[2],
            "expires_at": row[3],
            "tariff_id": row[4],
            "price_rub": row[5] if len(row) > 5 else 0,
        })
    
    logger.info(f"  Серверов в БД: {len(servers)}")
    logger.info(f"  Активных подписок: {len(subscriptions)}")
    
    # ОПТИМИЗАЦИЯ: Создаем пул клиентов сразу для переиспользования
    client_pool = ServerClientPool()
    
    # ========== ЭТАП 2: Удаление ключей для недоступных серверов ==========
    active_servers = [s for s in servers if s["active"]]
    unavailable_by_api = []  # Серверы, недоступные по API, но активные в БД
    
    # ОПТИМИЗАЦИЯ: Проверяем доступность через пул клиентов (используя get_all_keys_cached)
    # Вместо отдельного запроса в check_server_availability используем тот же запрос, который будет использован позже
    async def check_server_availability_via_pool(server: Dict[str, Any]) -> bool:
        """Проверить доступность сервера через пул клиентов (оптимизированная версия)"""
        try:
            protocol_client = await client_pool.get_client(server)
            if not protocol_client:
                return False
            
            # Используем get_all_keys_cached - это и проверка доступности, и кэширование для будущего использования
            protocol = server.get("protocol", "outline")
            # Увеличиваем таймаут для медленных серверов до 30 секунд
            await asyncio.wait_for(
                client_pool.get_all_keys_cached(server["id"], protocol, protocol_client),
                timeout=30.0
            )
            return True
        except (asyncio.TimeoutError, Exception) as e:
            # Логируем ошибку для отладки, но не блокируем создание ключей
            logger.debug(f"Server {server.get('id')} availability check failed: {e}")
            return False
    
    # Список ID неактивных серверов (для удаления ключей из БД на этапе 2).
    # При полной синхронизации серверы с active=0 не загружаются в servers, поэтому
    # получаем их отдельным запросом.
    with get_db_cursor() as cursor:
        if server_id:
            unavailable_server_ids = [s["id"] for s in servers if not s["active"]]
        else:
            cursor.execute("SELECT id FROM servers WHERE active = 0")
            unavailable_server_ids = [row[0] for row in cursor.fetchall()]
    
    if delete_inactive_server_keys:
        logger.info("\n[ЭТАП 2] Удаление ключей для недоступных серверов...")
        for sid in unavailable_server_ids:
            logger.info(f"  Сервер #{sid} неактивен в БД (ключи будут удалены из БД)")
        
        # ОПТИМИЗАЦИЯ: Проверяем доступность через пул клиентов (один запрос вместо двух)
        if active_servers:
            server_availability_tasks = [
                check_server_availability_via_pool(server)
                for server in active_servers
            ]
            
            availability_results = await asyncio.gather(
                *server_availability_tasks,
                return_exceptions=True
            )
            
            for i, server in enumerate(active_servers):
                result = availability_results[i]
                if isinstance(result, Exception) or not result:
                    unavailable_by_api.append(server["id"])
                    logger.warning(f"  Сервер #{server['id']} ({server['name']}) временно недоступен по API (ключи сохранены)")
        
        # Удаляем ключи ТОЛЬКО для серверов с active = 0
        if unavailable_server_ids:
            placeholders = ','.join('?' * len(unavailable_server_ids))
            if not dry_run:
                with get_db_cursor(commit=True) as cursor:
                    with safe_foreign_keys_off(cursor):
                        # Удаляем V2Ray ключи
                        cursor.execute(f"""
                            DELETE FROM v2ray_keys
                            WHERE server_id IN ({placeholders})
                        """, unavailable_server_ids)
                        v2ray_deleted = cursor.rowcount
                        
                        # Удаляем Outline ключи
                        cursor.execute(f"""
                            DELETE FROM keys
                            WHERE server_id IN ({placeholders})
                        """, unavailable_server_ids)
                        outline_deleted = cursor.rowcount
                        
                        total_deleted = v2ray_deleted + outline_deleted
                        stats["keys_deleted_from_db"] = total_deleted
                        logger.info(f"  Удалено ключей из БД: {total_deleted} (V2Ray: {v2ray_deleted}, Outline: {outline_deleted})")
            else:
                logger.info(f"  [DRY RUN] Будет удалено ключей из БД для {len(unavailable_server_ids)} неактивных серверов")
        
        stats["servers_unavailable"] = len(unavailable_server_ids)
    else:
        logger.info("\n[ЭТАП 2] Пропущен (delete_inactive_server_keys=False)")
        # ОПТИМИЗАЦИЯ: Все равно проверяем доступность для фильтрации через пул клиентов
        if active_servers:
            server_availability_tasks = [
                check_server_availability_via_pool(server)
                for server in active_servers
            ]
            
            availability_results = await asyncio.gather(
                *server_availability_tasks,
                return_exceptions=True
            )
            
            for i, server in enumerate(active_servers):
                result = availability_results[i]
                if isinstance(result, Exception) or not result:
                    unavailable_by_api.append(server["id"])
                    logger.warning(f"  Сервер #{server['id']} ({server['name']}) временно недоступен по API (ключи сохранены)")
    
    # ========== ЭТАП 2.5: Удаление ключей для неактивных подписок ==========
    if delete_inactive_server_keys:
        logger.info("\n[ЭТАП 2.5] Удаление ключей для неактивных подписок...")
        
        active_subscription_ids = {sub["id"] for sub in subscriptions}
        
        # Удаляем V2Ray ключи для неактивных подписок
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT k.id, k.subscription_id, k.server_id, k.v2ray_uuid, s.api_url, s.api_key
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.subscription_id IS NOT NULL 
                AND k.subscription_id NOT IN (
                    SELECT id FROM subscriptions 
                    WHERE is_active = 1 AND expires_at > ?
                )
            """, (now,))
            inactive_v2ray_keys = cursor.fetchall()
        
        # Удаляем Outline ключи для неактивных подписок
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT k.id, k.subscription_id, k.server_id, k.key_id, s.api_url, s.cert_sha256
                FROM keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.protocol = 'outline'
                AND k.subscription_id IS NOT NULL 
                AND k.subscription_id NOT IN (
                    SELECT id FROM subscriptions 
                    WHERE is_active = 1 AND expires_at > ?
                )
            """, (now,))
            inactive_outline_keys = cursor.fetchall()
        
        total_inactive_keys = len(inactive_v2ray_keys) + len(inactive_outline_keys)
        
        if total_inactive_keys > 0:
            logger.info(f"  Найдено ключей для неактивных подписок: {total_inactive_keys} (V2Ray: {len(inactive_v2ray_keys)}, Outline: {len(inactive_outline_keys)})")
            
            if not dry_run:
                # ОПТИМИЗАЦИЯ: Используем пул клиентов и группируем удаления по серверам
                deleted_from_servers = 0
                
                # Создаем пул клиентов для переиспользования
                client_pool_inactive = ServerClientPool()
                
                # Группируем V2Ray ключи по серверам
                v2ray_keys_by_server: Dict[int, List[Tuple]] = {}
                for key_row in inactive_v2ray_keys:
                    key_id, sub_id, server_id, v2ray_uuid, api_url, api_key = key_row
                    if server_id not in v2ray_keys_by_server:
                        v2ray_keys_by_server[server_id] = []
                    v2ray_keys_by_server[server_id].append(key_row)
                
                # Группируем Outline ключи по серверам
                outline_keys_by_server: Dict[int, List[Tuple]] = {}
                for key_row in inactive_outline_keys:
                    key_id, sub_id, server_id, outline_key_id, api_url, cert_sha256 = key_row
                    if server_id not in outline_keys_by_server:
                        outline_keys_by_server[server_id] = []
                    outline_keys_by_server[server_id].append(key_row)
                
                # Получаем информацию о серверах для пула клиентов
                servers_dict = {s["id"]: s for s in servers}
                
                # Удаляем V2Ray ключи (группированно по серверам)
                async def delete_v2ray_keys_for_server(server_id: int, keys: List[Tuple]) -> int:
                    """Удалить V2Ray ключи для одного сервера"""
                    server_info = servers_dict.get(server_id)
                    if not server_info:
                        return 0
                    
                    deleted_count = 0
                    try:
                        protocol_client = await client_pool_inactive.get_client(server_info)
                        if not protocol_client:
                            return 0
                        
                        # Параллельно удаляем все ключи этого сервера
                        async def delete_single_key(key_row: Tuple) -> bool:
                            async with api_semaphore:
                                try:
                                    _, _, _, v2ray_uuid, _, _ = key_row
                                    if v2ray_uuid:
                                        deleted = await protocol_client.delete_user(v2ray_uuid)
                                        return bool(deleted)
                                except Exception as e:
                                    logger.warning(f"    Ошибка удаления V2Ray ключа {key_row[3][:8] if key_row[3] else 'N/A'}...: {e}")
                                return False
                        
                        delete_tasks = [delete_single_key(key_row) for key_row in keys]
                        results = await asyncio.gather(*delete_tasks, return_exceptions=True)
                        deleted_count = sum(1 for r in results if r is True)
                    except Exception as e:
                        logger.warning(f"    Ошибка при удалении V2Ray ключей для сервера #{server_id}: {e}")
                    
                    return deleted_count
                
                # Удаляем Outline ключи (группированно по серверам)
                async def delete_outline_keys_for_server(server_id: int, keys: List[Tuple]) -> int:
                    """Удалить Outline ключи для одного сервера"""
                    server_info = servers_dict.get(server_id)
                    if not server_info:
                        return 0
                    
                    deleted_count = 0
                    try:
                        protocol_client = await client_pool_inactive.get_client(server_info)
                        if not protocol_client:
                            return 0
                        
                        # Параллельно удаляем все ключи этого сервера
                        async def delete_single_key(key_row: Tuple) -> bool:
                            async with api_semaphore:
                                try:
                                    _, _, _, outline_key_id, _, _ = key_row
                                    if outline_key_id:
                                        deleted = await protocol_client.delete_user(outline_key_id)
                                        return bool(deleted)
                                except Exception as e:
                                    logger.warning(f"    Ошибка удаления Outline ключа {key_row[3] if key_row[3] else 'N/A'}: {e}")
                                return False
                        
                        delete_tasks = [delete_single_key(key_row) for key_row in keys]
                        results = await asyncio.gather(*delete_tasks, return_exceptions=True)
                        deleted_count = sum(1 for r in results if r is True)
                    except Exception as e:
                        logger.warning(f"    Ошибка при удалении Outline ключей для сервера #{server_id}: {e}")
                    
                    return deleted_count
                
                # Параллельно обрабатываем все серверы
                v2ray_delete_tasks = [
                    delete_v2ray_keys_for_server(server_id, keys)
                    for server_id, keys in v2ray_keys_by_server.items()
                ]
                outline_delete_tasks = [
                    delete_outline_keys_for_server(server_id, keys)
                    for server_id, keys in outline_keys_by_server.items()
                ]
                
                all_delete_results = await asyncio.gather(
                    *v2ray_delete_tasks + outline_delete_tasks,
                    return_exceptions=True
                )
                
                deleted_from_servers = sum(
                    r for r in all_delete_results
                    if isinstance(r, int)
                )
                
                # Закрываем пул клиентов
                await client_pool_inactive.close_all()
                
                # Удаляем из БД
                with get_db_cursor(commit=True) as cursor:
                    with safe_foreign_keys_off(cursor):
                        # Удаляем V2Ray ключи
                        if inactive_v2ray_keys:
                            v2ray_key_ids = [k[0] for k in inactive_v2ray_keys]
                            placeholders = ','.join('?' * len(v2ray_key_ids))
                            cursor.execute(f"""
                                DELETE FROM v2ray_keys
                                WHERE id IN ({placeholders})
                            """, v2ray_key_ids)
                            v2ray_deleted = cursor.rowcount
                        else:
                            v2ray_deleted = 0
                        
                        # Удаляем Outline ключи
                        if inactive_outline_keys:
                            outline_key_ids = [k[0] for k in inactive_outline_keys]
                            placeholders = ','.join('?' * len(outline_key_ids))
                            cursor.execute(f"""
                                DELETE FROM keys
                                WHERE id IN ({placeholders}) AND protocol = 'outline'
                            """, outline_key_ids)
                            outline_deleted = cursor.rowcount
                        else:
                            outline_deleted = 0
                        
                        total_deleted = v2ray_deleted + outline_deleted
                        stats["keys_deleted_from_db"] += total_deleted
                        stats["keys_deleted_from_servers"] += deleted_from_servers
                        logger.info(f"  Удалено ключей: {total_deleted} из БД, {deleted_from_servers} с серверов (V2Ray: {v2ray_deleted}, Outline: {outline_deleted})")
            else:
                logger.info(f"  [DRY RUN] Будет удалено {total_inactive_keys} ключей для неактивных подписок")
        else:
            logger.info("  Ключей для неактивных подписок не найдено")
    
    # Фильтруем серверы: оставляем только активные (независимо от доступности по API)
    available_servers = [s for s in servers if s["active"]]
    
    # Для создания ключей берём все активные серверы с available_for_purchase=1.
    # Не исключаем по unavailable_by_api: проверка доступности (get_all_keys, таймаут 30s) могла
    # не пройти из-за медленного ответа, но create_user может отработать — пробуем создавать ключи.
    servers_for_creation = [
        s for s in available_servers
        if s.get("available_for_purchase", True)
    ]
    
    # Разделяем серверы по протоколам
    v2ray_servers_all = []
    outline_servers_all = []
    outline_server_8 = None
    
    if include_v2ray:
        # Создание ключей: по настройкам (active + available_for_purchase), без исключения по API
        v2ray_servers_all = [
            s for s in servers_for_creation
            if s["protocol"] == "v2ray"
        ]
        logger.info(f"  V2Ray серверов для создания ключей (active + available_for_purchase): {len(v2ray_servers_all)}")
    
    if include_outline:
        outline_servers_all = [
            s for s in servers_for_creation
            if s["protocol"] == "outline"
        ]
        # Для синхронизации используем Outline-сервер с ID из настроек (по умолчанию 8), если он активен и available_for_purchase = 1
        outline_server_id_conf = settings.OUTLINE_SERVER_ID
        outline_server_8 = next(
            (s for s in outline_servers_all if s["id"] == outline_server_id_conf),
            None
        )
        logger.info(f"  Outline серверов для создания ключей (active + available_for_purchase): {len(outline_servers_all)}")
        logger.info(f"  Outline сервер (id={outline_server_id_conf}) в списке для создания: {outline_server_8 is not None}")
    
    if unavailable_by_api:
        logger.info(f"  ⚠️  Обнаружено {len(unavailable_by_api)} серверов с медленным API (проверка доступности не прошла)")
        logger.info(f"  ⚠️  Попытка создания ключей все равно будет выполнена (сервер может быть медленным, но работать)")
    
    # ОПТИМИЗАЦИЯ: Создаем словарь для быстрого доступа к серверам по ID
    servers_by_id = {s["id"]: s for s in servers}
    
    # ОПТИМИЗАЦИЯ: Используем пул клиентов, созданный ранее на ЭТАПЕ 2
    
    # ОПТИМИЗАЦИЯ: Semaphore для параллельной обработки серверов (до 5 одновременно)
    server_semaphore = asyncio.Semaphore(5)
    
    try:
        # ========== ЭТАП 3: Синхронизация V2Ray ключей ==========
        if include_v2ray:
            logger.info("\n[ЭТАП 3] Синхронизация V2Ray ключей...")
            
            # 3.1. Создание недостающих ключей (с проверкой access_level для каждого пользователя)
            if create_missing:
                logger.info("  [3.1] Создание недостающих ключей...")
                
                # Импортируем UserRepository для проверки доступности
                from app.repositories.user_repository import UserRepository
                user_repo = UserRepository()
                
                async def process_server_create_keys(server: Dict[str, Any]) -> None:
                    """Обработать один сервер: создать недостающие ключи"""
                    async with server_semaphore:
                        server_id = server["id"]
                        server_name = server["name"]
                        server_access_level = server.get("access_level", "all")
                        logger.info(f"    Сервер #{server_id} ({server_name}): access_level={server_access_level}")
                        
                        try:
                            # ОПТИМИЗАЦИЯ: Используем пул клиентов
                            protocol_client = await client_pool.get_client(server)
                            if not protocol_client:
                                reason = []
                                if server.get("protocol") == "v2ray":
                                    if not server.get("api_url"):
                                        reason.append("api_url пустой или отсутствует")
                                    if not server.get("api_key"):
                                        reason.append("api_key пустой или отсутствует")
                                if not reason:
                                    reason.append("не удалось создать клиент протокола")
                                logger.warning(
                                    f"    Сервер #{server_id} ({server_name}): не удалось создать клиент — %s. Проверьте настройки сервера в БД.",
                                    "; ".join(reason)
                                )
                                return

                            # Получаем существующие ключи для этого сервера
                            with get_db_cursor() as cursor:
                                cursor.execute("""
                                    SELECT subscription_id, v2ray_uuid
                                    FROM v2ray_keys
                                    WHERE server_id = ? AND subscription_id IS NOT NULL
                                """, (server_id,))
                                existing_keys = {row[0]: row[1] for row in cursor.fetchall()}
                            
                            # Находим подписки без ключей на этом сервере и фильтруем по access_level
                            missing_subscriptions = []
                            skipped_count = 0
                            skipped_reasons = {"access_level": 0, "no_vip": 0, "no_active_subscription": 0}
                            
                            total_subscriptions_without_keys = sum(1 for sub in subscriptions if sub["id"] not in existing_keys)
                            logger.debug(f"    Сервер #{server_id} ({server_name}): найдено {total_subscriptions_without_keys} подписок без ключей на этом сервере")
                            
                            for sub in subscriptions:
                                if sub["id"] not in existing_keys:
                                    # Проверяем доступность сервера для пользователя
                                    user_id = sub["user_id"]
                                    
                                    if server_access_level == 'all':
                                        missing_subscriptions.append(sub)
                                    elif server_access_level == 'vip':
                                        is_vip = user_repo.is_user_vip(user_id)
                                        if is_vip:
                                            missing_subscriptions.append(sub)
                                        else:
                                            skipped_count += 1
                                            skipped_reasons["no_vip"] += 1
                                    elif server_access_level == 'paid':
                                        # Для 'paid': доступны VIP пользователи И пользователи с платными подписками (price > 0)
                                        is_vip = user_repo.is_user_vip(user_id)
                                        if is_vip:
                                            missing_subscriptions.append(sub)
                                        else:
                                            # Проверяем, является ли текущая подписка платной (цена > 0)
                                            is_paid_subscription = sub.get("price_rub", 0) > 0
                                            if is_paid_subscription:
                                                missing_subscriptions.append(sub)
                                            else:
                                                skipped_count += 1
                                                skipped_reasons["no_active_subscription"] += 1
                            
                            if skipped_count > 0:
                                logger.info(
                                    f"    Сервер #{server_id} ({server_name}): пропущено {skipped_count} подписок (access_level={server_access_level}), "
                                    f"к созданию {len(missing_subscriptions)} ключей. "
                                    f"Причины: VIP={skipped_reasons['no_vip']}, нет активной подписки={skipped_reasons['no_active_subscription']}"
                                )
                            elif server_access_level in ("vip", "paid") and not missing_subscriptions:
                                logger.info(
                                    f"    Сервер #{server_id} ({server_name}): по access_level={server_access_level} подходящих подписок нет, создано 0 ключей"
                                )
                            elif total_subscriptions_without_keys > 0 and len(missing_subscriptions) == 0:
                                logger.warning(
                                    f"    Сервер #{server_id} ({server_name}): найдено {total_subscriptions_without_keys} подписок без ключей, "
                                    f"но ни одна не прошла фильтрацию по access_level={server_access_level}"
                                )
                            
                            if missing_subscriptions:
                                logger.info(f"    Сервер #{server_id} ({server_name}): создаем {len(missing_subscriptions)} ключей (access_level={server_access_level})")
                                
                                for subscription in missing_subscriptions:
                                    sub_id = subscription["id"]
                                    user_id = subscription["user_id"]
                                    token = subscription["subscription_token"]
                                    expires_at = subscription["expires_at"]
                                    tariff_id = subscription["tariff_id"]
                                    key_email = f"{user_id}_subscription_{sub_id}@veilbot.com"

                                    try:
                                        # Создаем ключ на сервере с таймаутом
                                        async with api_semaphore:
                                            # Увеличиваем таймаут до 60 секунд для медленных серверов
                                            user_data = await asyncio.wait_for(
                                                protocol_client.create_user(key_email, name=server_name),
                                                timeout=60.0  # Таймаут 60 секунд для создания ключа
                                            )
                                            if not user_data or not user_data.get("uuid"):
                                                raise RuntimeError("V2Ray сервер не вернул uuid при создании пользователя")

                                            created_uuid = user_data["uuid"]

                                            # Получаем client_config с таймаутом
                                            # Увеличиваем таймаут до 60 секунд для медленных серверов
                                            client_config = await asyncio.wait_for(
                                                protocol_client.get_user_config(
                                                    created_uuid,
                                                    {
                                                        # `app.settings.Settings` не имеет поля `domain`.
                                                        # Если домен не задан у сервера, используем основной домен проекта.
                                                        "domain": (server.get("domain") or "").strip() or "veil-bot.ru",
                                                        "port": 443,
                                                        "email": key_email,
                                                    },
                                                ),
                                                timeout=60.0  # Таймаут 60 секунд для получения конфигурации
                                            )

                                            # Извлекаем VLESS URL
                                            if "vless://" in client_config:
                                                for line in client_config.split("\n"):
                                                    candidate = line.strip()
                                                    if candidate.startswith("vless://"):
                                                        client_config = candidate
                                                        break

                                            # Нормализуем конфигурацию
                                            client_config = normalize_vless_host(
                                                client_config,
                                                server.get("domain"),
                                                server["api_url"] or "",
                                            )
                                            client_config = remove_fragment_from_vless(client_config)

                                            # Сохраняем в БД
                                            # ВАЖНО: Проверяем существование ключа ПЕРЕД вставкой для защиты от race conditions
                                            # Используем BEGIN IMMEDIATE для атомарной проверки и вставки
                                            if not dry_run:
                                                with get_db_cursor(commit=True) as cursor:
                                                    # Начинаем IMMEDIATE транзакцию для атомарной проверки и вставки
                                                    cursor.execute("BEGIN IMMEDIATE")
                                                    try:
                                                        # Проверяем существование ключа атомарно перед вставкой
                                                        cursor.execute("""
                                                            SELECT id FROM v2ray_keys
                                                            WHERE server_id = ? AND subscription_id = ?
                                                            LIMIT 1
                                                        """, (server_id, sub_id))
                                                        if cursor.fetchone():
                                                            # Ключ уже существует (race condition), удаляем созданный ключ с сервера
                                                            cursor.execute("ROLLBACK")
                                                            logger.warning(f"      Ключ для подписки {sub_id} на сервере {server_id} уже существует (race condition), удаляем дубликат с сервера")
                                                            try:
                                                                await protocol_client.delete_user(created_uuid)
                                                            except Exception as e:
                                                                logger.warning(f"      Не удалось удалить дубликат ключа {created_uuid[:8]}... с сервера: {e}")
                                                            continue
                                                        
                                                        # Вставляем ключ только если его еще нет (expiry берётся из subscriptions через JOIN)
                                                        with safe_foreign_keys_off(cursor):
                                                            cursor.execute("""
                                                                INSERT INTO v2ray_keys
                                                                (server_id, user_id, v2ray_uuid, email, created_at,
                                                                 tariff_id, client_config, subscription_id)
                                                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                                            """, (
                                                                server_id,
                                                                user_id,
                                                                created_uuid,
                                                                key_email,
                                                                now,
                                                                tariff_id,
                                                                client_config,
                                                                sub_id,
                                                            ))
                                                        cursor.execute("COMMIT")
                                                    except Exception as e:
                                                        cursor.execute("ROLLBACK")
                                                        raise

                                            invalidate_subscription_cache(token)
                                            
                                            stats["v2ray_keys_created"] += 1
                                            logger.debug(f"      Создан ключ для подписки {sub_id}")
                                            
                                    except asyncio.TimeoutError:
                                        stats["errors"] += 1
                                        error_msg = f"Таймаут создания ключа для подписки {sub_id} (сервер #{server_id} медленно отвечает)"
                                        stats["errors_details"].append({
                                            "type": "v2ray_create_timeout",
                                            "server_id": server_id,
                                            "subscription_id": sub_id,
                                            "error": error_msg,
                                        })
                                        logger.warning(f"      ⏱️  {error_msg}")
                                        if len(stats["errors_details"]) >= 50:
                                            break
                                    except Exception as e:
                                        stats["errors"] += 1
                                        error_msg = f"Ошибка создания ключа для подписки {sub_id}: {e}"
                                        stats["errors_details"].append({
                                            "type": "v2ray_create",
                                            "server_id": server_id,
                                            "subscription_id": sub_id,
                                            "error": error_msg,
                                        })
                                        logger.error(f"      ✗ {error_msg}")
                                        if len(stats["errors_details"]) >= 50:
                                            break
                            
                            stats["servers_processed"] += 1
                            
                        except Exception as e:
                            stats["errors"] += 1
                            error_msg = f"Ошибка обработки сервера #{server_id}: {e}"
                            stats["errors_details"].append({
                                "type": "v2ray_server",
                                "server_id": server_id,
                                "error": error_msg,
                            })
                            logger.error(f"    ✗ {error_msg}")
                
                # ОПТИМИЗАЦИЯ: Параллельная обработка серверов (до 5 одновременно)
                if v2ray_servers_all:
                    create_tasks = [process_server_create_keys(server) for server in v2ray_servers_all]
                    await asyncio.gather(*create_tasks, return_exceptions=True)
            else:
                logger.info("  [3.1] Пропущено (create_missing=False)")
            
            # 3.2. Удаление лишних ключей с серверов
            if delete_orphaned_on_servers:
                logger.info("  [3.2] Удаление лишних ключей с серверов...")
        
        async def process_server_delete_orphaned(server: Dict[str, Any]) -> None:
            """Обработать один сервер: удалить лишние ключи"""
            async with server_semaphore:
                server_id = server["id"]
                server_name = server["name"]
                server_access_level = server.get("access_level", "all")
                
                try:
                    # ОПТИМИЗАЦИЯ: Используем пул клиентов
                    protocol_client = await client_pool.get_client(server)
                    if not protocol_client:
                        return
                    
                    # Получаем ключи из БД с информацией о пользователях
                    with get_db_cursor() as cursor:
                        cursor.execute("""
                            SELECT k.v2ray_uuid, k.email, k.user_id, k.subscription_id
                            FROM v2ray_keys k
                            WHERE k.server_id = ?
                        """, (server_id,))
                        db_keys = cursor.fetchall()
                    
                    db_uuids: Set[str] = {row[0].strip() for row in db_keys if row[0]}
                    db_emails: Set[str] = {(row[1] or "").lower().strip() for row in db_keys if row[1]}
                    # Создаем словарь для быстрой проверки VIP статуса пользователей
                    db_keys_by_uuid: Dict[str, Tuple] = {row[0].strip(): row for row in db_keys if row[0]}
                    
                    # ОПТИМИЗАЦИЯ: Используем кэшированные ключи
                    remote_keys = await client_pool.get_all_keys_cached(server_id, "v2ray", protocol_client)
                    if not remote_keys:
                        return
                    
                    # Импортируем UserRepository для проверки VIP статуса
                    from app.repositories.user_repository import UserRepository
                    user_repo = UserRepository()
                    
                    # Находим ключи для удаления
                    keys_to_delete = []
                    keys_to_delete_from_db = []  # Ключи для удаления из БД
                    
                    for remote_entry in remote_keys:
                        remote_uuid = extract_v2ray_uuid(remote_entry)
                        if not remote_uuid:
                            continue
                        
                        remote_name = (remote_entry.get("name") or "").lower().strip()
                        remote_email = (remote_entry.get("email") or "").lower().strip()
                        
                        key_info = remote_entry.get("key") if isinstance(remote_entry.get("key"), dict) else None
                        if isinstance(key_info, dict):
                            remote_name = remote_name or (key_info.get("name") or "").lower().strip()
                            remote_email = remote_email or (key_info.get("email") or "").lower().strip()
                        
                        key_identifier = (
                            remote_entry.get("id")
                            or remote_entry.get("key_id")
                            or (key_info.get("id") if isinstance(key_info, dict) else None)
                            or (key_info.get("key_id") if isinstance(key_info, dict) else None)
                            or remote_uuid
                        )
                        
                        # Проверяем, есть ли ключ в БД
                        key_in_db = remote_uuid in db_uuids
                        key_email_match = remote_name in db_emails or remote_email in db_emails
                        
                        # Если ключ не в БД и не совпадает по email - удаляем как лишний
                        if not key_in_db and not key_email_match:
                            keys_to_delete.append({
                                "uuid": remote_uuid,
                                "id": key_identifier,
                            })
                            continue
                        
                        # Если ключ есть в БД, проверяем соответствие access_level
                        if key_in_db and server_access_level in ('vip', 'paid'):
                            db_key_info = db_keys_by_uuid.get(remote_uuid)
                            if db_key_info:
                                _, _, user_id, subscription_id = db_key_info
                                
                                # Проверяем VIP статус пользователя
                                is_vip = user_repo.is_user_vip(user_id)
                                
                                if server_access_level == 'vip':
                                    # Для VIP-only серверов удаляем ключи не-VIP пользователей
                                    if not is_vip:
                                        keys_to_delete.append({
                                            "uuid": remote_uuid,
                                            "id": key_identifier,
                                        })
                                        keys_to_delete_from_db.append(remote_uuid)
                                        logger.debug(f"      Сервер #{server_id}: удаляем ключ не-VIP пользователя {user_id} (access_level=vip)")
                                elif server_access_level == 'paid':
                                    # Для paid-only серверов удаляем ключи не-VIP пользователей с бесплатными подписками (price = 0)
                                    if not is_vip:
                                        # Проверяем, есть ли у пользователя платная подписка (price > 0)
                                        with get_db_cursor() as check_cursor:
                                            check_cursor.execute("""
                                                SELECT COUNT(*) FROM subscriptions s
                                                LEFT JOIN tariffs t ON s.tariff_id = t.id
                                                WHERE s.user_id = ? AND s.is_active = 1 AND s.expires_at > ?
                                                  AND COALESCE(t.price_rub, 0) > 0
                                            """, (user_id, now))
                                            has_paid_subscription = check_cursor.fetchone()[0] > 0
                                        
                                        if not has_paid_subscription:
                                            keys_to_delete.append({
                                                "uuid": remote_uuid,
                                                "id": key_identifier,
                                            })
                                            keys_to_delete_from_db.append(remote_uuid)
                                            logger.debug(f"      Сервер #{server_id}: удаляем ключ пользователя {user_id} без платной подписки (access_level=paid)")
                    
                    # Удаляем лишние ключи
                    if keys_to_delete:
                        logger.info(f"    Сервер #{server_id} ({server_name}): найдено {len(keys_to_delete)} лишних ключей (access_level={server_access_level})")
                        
                        if not dry_run:
                            async def delete_key(key_info: Dict[str, Any]) -> bool:
                                async with api_semaphore:
                                    try:
                                        deleted = await protocol_client.delete_user(str(key_info["id"]))
                                        if deleted:
                                            stats["keys_deleted_from_servers"] += 1
                                            return True
                                        return False
                                    except Exception as e:
                                        logger.warning(f"      Ошибка удаления ключа {key_info['uuid'][:8]}...: {e}")
                                        return False
                            
                            delete_tasks = [delete_key(key_info) for key_info in keys_to_delete]
                            await asyncio.gather(*delete_tasks, return_exceptions=True)
                            
                            # Удаляем ключи из БД, которые были удалены из-за несоответствия access_level
                            if keys_to_delete_from_db:
                                with get_db_cursor(commit=True) as cursor:
                                    with safe_foreign_keys_off(cursor):
                                        placeholders = ','.join('?' * len(keys_to_delete_from_db))
                                        cursor.execute(f"""
                                            DELETE FROM v2ray_keys
                                            WHERE server_id = ? AND v2ray_uuid IN ({placeholders})
                                        """, (server_id, *keys_to_delete_from_db))
                                        deleted_from_db = cursor.rowcount
                                        stats["keys_deleted_from_db"] += deleted_from_db
                                        logger.info(f"      Удалено {deleted_from_db} ключей из БД для сервера #{server_id} (не соответствуют access_level={server_access_level})")
                
                except Exception as e:
                    stats["errors"] += 1
                    error_msg = f"Ошибка удаления лишних ключей с сервера #{server_id}: {e}"
                    stats["errors_details"].append({
                        "type": "v2ray_delete_orphaned",
                        "server_id": server_id,
                        "error": error_msg,
                    })
                    logger.error(f"    ✗ {error_msg}")
        
        # ОПТИМИЗАЦИЯ: Параллельная обработка серверов (до 5 одновременно)
        if delete_orphaned_on_servers:
            if v2ray_servers_all:
                delete_tasks = [process_server_delete_orphaned(server) for server in v2ray_servers_all]
                await asyncio.gather(*delete_tasks, return_exceptions=True)
        else:
            logger.info("  [3.2] Пропущено (delete_orphaned_on_servers=False)")
        
        # 3.3. Синхронизация VLESS ссылок (для всех активных серверов)
        if sync_configs:
            logger.info("  [3.3] Синхронизация VLESS ссылок...")
            
            # ОПТИМИЗАЦИЯ: Удаляем ключи для несуществующих серверов и неактивных подписок
            if not dry_run:
                with get_db_cursor(commit=True) as cursor:
                    # Удаляем ключи для несуществующих серверов
                    cursor.execute("""
                        DELETE FROM v2ray_keys
                        WHERE server_id NOT IN (SELECT id FROM servers)
                    """)
                    deleted_orphaned_servers = cursor.rowcount
                    
                    # Удаляем ключи для неактивных подписок
                    cursor.execute("""
                        DELETE FROM v2ray_keys
                        WHERE subscription_id IS NOT NULL
                        AND subscription_id NOT IN (
                            SELECT id FROM subscriptions 
                            WHERE is_active = 1 AND expires_at > ?
                        )
                    """, (now,))
                    deleted_inactive_subs = cursor.rowcount
                    
                    if deleted_orphaned_servers > 0 or deleted_inactive_subs > 0:
                        logger.info(f"    Удалено ключей: {deleted_orphaned_servers} для несуществующих серверов, {deleted_inactive_subs} для неактивных подписок")
        else:
            logger.info("  [3.3] Пропущено (sync_configs=False)")
        
        # Получаем все V2Ray ключи для синхронизации (только для активных серверов и подписок)
        with get_db_cursor() as cursor:
            if server_id:
                cursor.execute("""
                    SELECT k.id, k.v2ray_uuid, k.client_config, k.server_id, k.user_id, k.email,
                           k.subscription_id, s.name, s.domain, s.api_url, s.api_key
                    FROM v2ray_keys k
                    JOIN servers s ON k.server_id = s.id
                    WHERE s.protocol = 'v2ray' AND s.active = 1 AND k.server_id = ?
                    AND (k.subscription_id IS NULL OR k.subscription_id IN (
                        SELECT id FROM subscriptions WHERE is_active = 1 AND expires_at > ?
                    ))
                """, (server_id, now))
            else:
                cursor.execute("""
                    SELECT k.id, k.v2ray_uuid, k.client_config, k.server_id, k.user_id, k.email,
                           k.subscription_id, s.name, s.domain, s.api_url, s.api_key
                    FROM v2ray_keys k
                    JOIN servers s ON k.server_id = s.id
                    WHERE s.protocol = 'v2ray' AND s.active = 1
                    AND (k.subscription_id IS NULL OR k.subscription_id IN (
                        SELECT id FROM subscriptions WHERE is_active = 1 AND expires_at > ?
                    ))
                """, (now,))
            v2ray_keys = cursor.fetchall()
        
        logger.info(f"    Найдено {len(v2ray_keys)} V2Ray ключей для синхронизации")
        
        # Группируем ключи по серверам
        keys_by_server: Dict[int, List[Tuple]] = {}
        for key_row in v2ray_keys:
            server_id_key = key_row[3]
            if server_id_key not in keys_by_server:
                keys_by_server[server_id_key] = []
            keys_by_server[server_id_key].append(key_row)
        
        # Получаем токены подписок для инвалидации кэша
        subscription_ids = {key[6] for key in v2ray_keys if key[6]}
        subscription_tokens_map: Dict[int, str] = {}
        if subscription_ids:
            placeholders = ','.join('?' * len(subscription_ids))
            with get_db_cursor() as cursor:
                cursor.execute(f"""
                    SELECT id, subscription_token
                    FROM subscriptions
                    WHERE id IN ({placeholders})
                """, list(subscription_ids))
                for sub_id, token in cursor.fetchall():
                    subscription_tokens_map[sub_id] = token
        
        # Синхронизируем конфигурации по серверам
        all_updates: List[Tuple[str, int]] = []  # (new_config, key_id)
        tokens_to_invalidate = set()
        
        async def process_server_sync_configs(server_id_key: int, server_keys: List[Tuple]) -> Tuple[List[Tuple[str, int]], Set[str]]:
            """Обработать один сервер: синхронизировать конфигурации ключей"""
            async with server_semaphore:
                server_updates = []
                server_tokens = set()
                
                # ОПТИМИЗАЦИЯ: Используем словарь для быстрого доступа
                server_info = servers_by_id.get(server_id_key)
                if not server_info:
                    return ([], set())
                
                try:
                    # ОПТИМИЗАЦИЯ: Используем пул клиентов
                    protocol_client = await client_pool.get_client(server_info)
                    if not protocol_client:
                        return ([], set())
                    
                    async def sync_single_v2ray_key(key_row: Tuple) -> Optional[Tuple[str, int]]:
                        """Синхронизировать один V2Ray ключ"""
                        async with api_semaphore:
                            try:
                                key_id, v2ray_uuid, old_config, _, user_id, email, sub_id, _, domain, _, _ = key_row
                                
                                # ОПТИМИЗАЦИЯ: Если конфигурация уже есть и не пустая, проверяем только если нужно
                                # Но мы все равно должны проверить, так как конфигурация на сервере могла измениться
                                # Уменьшаем таймаут для ускорения
                                fetched_config = await asyncio.wait_for(
                                    protocol_client.get_user_config(
                                        v2ray_uuid,
                                        {
                                            "domain": domain or "veil-bot.ru",
                                            "port": 443,
                                            "email": email or f"user_{user_id}@veilbot.com",
                                        },
                                    ),
                                    timeout=10.0  # Уменьшен таймаут с 30 до 10 секунд
                                )
                                
                                # Извлекаем VLESS URL
                                if "vless://" in fetched_config:
                                    for line in fetched_config.split("\n"):
                                        if line.strip().startswith("vless://"):
                                            fetched_config = line.strip()
                                            break
                                
                                # Нормализуем конфигурацию
                                new_config = normalize_vless_host(
                                    fetched_config,
                                    domain,
                                    server_info["api_url"] or "",
                                )
                                new_config = remove_fragment_from_vless(new_config)
                                
                                # Сравниваем с текущей конфигурацией
                                if old_config != new_config:
                                    return (new_config, key_id)
                                return None
                                
                            except asyncio.TimeoutError:
                                logger.warning(f"      ⏱️  Таймаут синхронизации ключа #{key_row[0]} (сервер медленно отвечает)")
                                return None
                            except Exception as e:
                                logger.warning(f"      Ошибка синхронизации ключа #{key_row[0]}: {e}")
                                return None
                    
                    # Параллельно синхронизируем ключи батчами (для БД операций)
                    batch_size = 50
                    for i in range(0, len(server_keys), batch_size):
                        batch = server_keys[i:i + batch_size]
                        sync_tasks = [sync_single_v2ray_key(key_row) for key_row in batch]
                        batch_results = await asyncio.gather(*sync_tasks, return_exceptions=True)
                        
                        for result in batch_results:
                            if isinstance(result, Exception):
                                continue
                            if result:
                                server_updates.append(result)
                                # Добавляем токен для инвалидации
                                key_row = next((k for k in batch if k[0] == result[1]), None)
                                if key_row and key_row[6]:  # subscription_id
                                    token = subscription_tokens_map.get(key_row[6])
                                    if token:
                                        server_tokens.add(token)
                
                except Exception as e:
                    stats["errors"] += 1
                    error_msg = f"Ошибка синхронизации конфигураций сервера #{server_id_key}: {e}"
                    stats["errors_details"].append({
                        "type": "v2ray_sync_configs",
                        "server_id": server_id_key,
                        "error": error_msg,
                    })
                    logger.error(f"    ✗ {error_msg}")
                
                return server_updates, server_tokens
        
        # ОПТИМИЗАЦИЯ: Параллельная обработка серверов (до 5 одновременно)
        if keys_by_server:
            sync_config_tasks = [
                process_server_sync_configs(server_id_key, server_keys)
                for server_id_key, server_keys in keys_by_server.items()
            ]
            sync_results = await asyncio.gather(*sync_config_tasks, return_exceptions=True)
            
            for result in sync_results:
                if isinstance(result, Exception):
                    continue
                if isinstance(result, tuple) and len(result) == 2:
                    updates, tokens = result
                    all_updates.extend(updates)
                    tokens_to_invalidate.update(tokens)
        
        # Обновляем конфигурации в БД батчем
        if all_updates and not dry_run:
            with get_db_cursor(commit=True) as cursor:
                cursor.executemany("""
                    UPDATE v2ray_keys
                    SET client_config = ?
                    WHERE id = ?
                """, all_updates)
                stats["v2ray_configs_updated"] = len(all_updates)
                logger.info(f"    Обновлено конфигураций: {len(all_updates)}")
        
        # Инвалидируем кэш подписок
        if not dry_run:
            for token in tokens_to_invalidate:
                invalidate_subscription_cache(token)

        # ========== ЭТАП 4: Синхронизация Outline ключей ==========
        if include_outline:
            logger.info("\n[ЭТАП 4] Синхронизация Outline ключей...")
        
            # 4.1. Создание недостающих ключей на сервере №8 (только если сервер №8 в списке — при server_id= только один сервер может не быть Outline)
            if outline_server_8:
                server_id = outline_server_8["id"]
                server_name = outline_server_8["name"]
                # Пропускаем сервер, если он недоступен по API
                # ИСПРАВЛЕНИЕ: Не пропускаем создание ключей даже если проверка доступности не прошла
                if server_id in unavailable_by_api:
                    logger.warning(f"  [4.1] Сервер №8 ({server_name}) показал медленный ответ при проверке доступности, но попытаемся создать ключи...")
                
                logger.info("  [4.1] Создание недостающих ключей на сервере №8...")
                
                try:
                    # ОПТИМИЗАЦИЯ: Используем пул клиентов
                    protocol_client = await client_pool.get_client(outline_server_8)
                    if not protocol_client:
                        logger.warning(f"    Сервер №8 ({server_name}): не удалось создать клиент")
                    else:
                        # Получаем существующие Outline ключи подписок на Outline-сервере (id из настроек)
                        with get_db_cursor() as cursor:
                            cursor.execute("""
                                SELECT subscription_id, key_id
                                FROM keys
                                WHERE server_id = ? AND subscription_id IS NOT NULL
                            """, (outline_server_id_conf,))
                            existing_keys = {row[0]: row[1] for row in cursor.fetchall()}
                        
                        # Находим подписки без ключей и фильтруем по access_level
                        server_access_level = outline_server_8.get("access_level", "all")
                        missing_subscriptions = []
                        skipped_count = 0
                        
                        # Импортируем UserRepository для проверки доступности
                        from app.repositories.user_repository import UserRepository
                        user_repo = UserRepository()
                        
                        for sub in subscriptions:
                            if sub["id"] not in existing_keys:
                                # Проверяем доступность сервера для пользователя
                                user_id = sub["user_id"]
                                
                                if server_access_level == 'all':
                                    missing_subscriptions.append(sub)
                                elif server_access_level == 'vip':
                                    is_vip = user_repo.is_user_vip(user_id)
                                    if is_vip:
                                        missing_subscriptions.append(sub)
                                    else:
                                        skipped_count += 1
                                elif server_access_level == 'paid':
                                    # Для 'paid': доступны VIP пользователи И пользователи с платными подписками (price > 0)
                                    is_vip = user_repo.is_user_vip(user_id)
                                    if is_vip:
                                        missing_subscriptions.append(sub)
                                    else:
                                        # Проверяем, является ли текущая подписка платной (цена > 0)
                                        is_paid_subscription = sub.get("price_rub", 0) > 0
                                        if is_paid_subscription:
                                            missing_subscriptions.append(sub)
                                        else:
                                            skipped_count += 1
                        
                        if skipped_count > 0:
                            logger.debug(f"    Сервер №8 ({server_name}): пропущено {skipped_count} подписок из-за access_level={server_access_level}")
                        
                        if missing_subscriptions:
                            logger.info(f"    Сервер №8 ({server_name}): создаем {len(missing_subscriptions)} ключей (access_level={server_access_level})")
                            
                            for subscription in missing_subscriptions:
                                sub_id = subscription["id"]
                                user_id = subscription["user_id"]
                                token = subscription["subscription_token"]
                                expires_at = subscription["expires_at"]
                                tariff_id = subscription["tariff_id"]
                                key_email = f"{user_id}_subscription_{sub_id}@veilbot.com"
                                
                                try:
                                    # Создаем ключ на сервере с таймаутом
                                    # Увеличиваем таймаут до 60 секунд для медленных серверов
                                    user_data = await asyncio.wait_for(
                                        protocol_client.create_user(key_email),
                                        timeout=60.0  # Таймаут 60 секунд для создания ключа
                                    )
                                    if not user_data or not user_data.get("id"):
                                        raise RuntimeError("Outline сервер не вернул id при создании ключа")
                                    
                                    key_id = str(user_data["id"])
                                    access_url = user_data.get("accessUrl") or user_data.get("access_url") or ""
                                    
                                    # Сохраняем в БД
                                    if not dry_run:
                                        with get_db_cursor(commit=True) as cursor:
                                            with safe_foreign_keys_off(cursor):
                                                cursor.execute("""
                                                    INSERT INTO keys
                                                    (server_id, user_id, key_id, access_url, email, created_at,
                                                     tariff_id, subscription_id, protocol)
                                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'outline')
                                                """, (
                                                    server_id,
                                                    user_id,
                                                    key_id,
                                                    access_url,
                                                    key_email,
                                                    now,
                                                    tariff_id,
                                                    sub_id,
                                                ))
                                        
                                        invalidate_subscription_cache(token)
                                    
                                    stats["outline_keys_created"] += 1
                                    logger.debug(f"      Создан ключ для подписки {sub_id}")
                                    
                                except asyncio.TimeoutError:
                                    stats["errors"] += 1
                                    error_msg = f"Таймаут создания Outline ключа для подписки {sub_id} (сервер #{server_id} медленно отвечает)"
                                    stats["errors_details"].append({
                                        "type": "outline_create_timeout",
                                        "server_id": server_id,
                                        "subscription_id": sub_id,
                                        "error": error_msg,
                                    })
                                    logger.warning(f"      ⏱️  {error_msg}")
                                    if len(stats["errors_details"]) >= 50:
                                        break
                                except Exception as e:
                                    stats["errors"] += 1
                                    error_msg = f"Ошибка создания Outline ключа для подписки {sub_id}: {e}"
                                    stats["errors_details"].append({
                                        "type": "outline_create",
                                        "server_id": server_id,
                                        "subscription_id": sub_id,
                                        "error": error_msg,
                                    })
                                    logger.error(f"      ✗ {error_msg}")
                                    if len(stats["errors_details"]) >= 50:
                                        break
                        
                        stats["servers_processed"] += 1
                    
                except Exception as e:
                    stats["errors"] += 1
                    error_msg = f"Ошибка обработки Outline-сервера #{server_id}: {e}"
                    stats["errors_details"].append({
                        "type": "outline_server_8",
                        "server_id": server_id,
                        "error": error_msg,
                    })
                    logger.error(f"    ✗ {error_msg}")
            else:
                logger.info(
                    "  [4.1] Нет активного Outline-сервера в списке (удалён, неактивен или Outline отключён), пропуск."
                )
        
        # 4.2. Удаление Outline ключей подписок с других серверов (не №8)
        logger.info("  [4.2] Удаление Outline ключей подписок с других серверов...")
        
        with get_db_cursor() as cursor:
            outline_id = settings.OUTLINE_SERVER_ID
            cursor.execute("""
                SELECT id, server_id, key_id, subscription_id
                FROM keys
                WHERE subscription_id IS NOT NULL AND server_id != ? AND protocol = 'outline'
            """, (outline_id,))
            keys_to_remove = cursor.fetchall()
        
        if keys_to_remove:
            logger.info(f"    Найдено {len(keys_to_remove)} Outline ключей подписок на серверах, отличных от Outline-сервера (id={outline_id})")
            
            for key_row in keys_to_remove:
                key_db_id, server_id, key_id, sub_id = key_row
                
                # ОПТИМИЗАЦИЯ: Используем словарь для быстрого доступа
                server_info = servers_by_id.get(server_id)
                if not server_info:
                    # Удаляем из БД, если сервер недоступен
                    if not dry_run:
                        with get_db_cursor(commit=True) as cursor:
                            with safe_foreign_keys_off(cursor):
                                cursor.execute("DELETE FROM keys WHERE id = ?", (key_db_id,))
                    stats["outline_keys_removed"] += 1
                    continue
                
                try:
                    # ОПТИМИЗАЦИЯ: Используем пул клиентов
                    protocol_client = await client_pool.get_client(server_info)
                    if not protocol_client:
                        # Удаляем из БД, если не удалось создать клиент
                        if not dry_run:
                            with get_db_cursor(commit=True) as cursor:
                                with safe_foreign_keys_off(cursor):
                                    cursor.execute("DELETE FROM keys WHERE id = ?", (key_db_id,))
                        stats["outline_keys_removed"] += 1
                        continue
                    
                    # Удаляем ключ с сервера
                    if not dry_run:
                        async with api_semaphore:
                            await protocol_client.delete_user(key_id)
                        
                        # Удаляем из БД
                        with get_db_cursor(commit=True) as cursor:
                            with safe_foreign_keys_off(cursor):
                                cursor.execute("DELETE FROM keys WHERE id = ?", (key_db_id,))
                    
                    stats["outline_keys_removed"] += 1
                    stats["keys_deleted_from_servers"] += 1
                    logger.debug(f"      Удален ключ подписки {sub_id} с сервера #{server_id}")
                    
                except Exception as e:
                    logger.warning(f"      Ошибка удаления ключа {key_id} с сервера #{server_id}: {e}")
                    # Удаляем из БД в любом случае
                    if not dry_run:
                        with get_db_cursor(commit=True) as cursor:
                            with safe_foreign_keys_off(cursor):
                                cursor.execute("DELETE FROM keys WHERE id = ?", (key_db_id,))
                    stats["outline_keys_removed"] += 1
        
            else:
                logger.info("  [4.2] Пропущено (delete_orphaned_on_servers=False)")
            
            # 4.3. Удаление лишних Outline ключей с серверов
            if delete_orphaned_on_servers:
                logger.info("  [4.3] Удаление лишних Outline ключей с серверов...")
        
        async def process_outline_server_delete_orphaned(server: Dict[str, Any]) -> None:
            """Обработать один Outline сервер: удалить лишние ключи"""
            async with server_semaphore:
                server_id = server["id"]
                server_name = server["name"]
                
                try:
                    # ОПТИМИЗАЦИЯ: Используем пул клиентов
                    protocol_client = await client_pool.get_client(server)
                    if not protocol_client:
                        return

                    # Получаем ключи из БД
                    with get_db_cursor() as cursor:
                        cursor.execute("""
                            SELECT key_id, email
                            FROM keys
                            WHERE server_id = ? AND (key_id IS NOT NULL AND key_id != '')
                        """, (server_id,))
                        db_keys = cursor.fetchall()
                    
                    db_key_ids: Set[str] = {str(row[0]).strip() for row in db_keys if row[0]}
                    db_emails: Set[str] = {(row[1] or "").lower().strip() for row in db_keys if row[1]}
                    
                    # ОПТИМИЗАЦИЯ: Используем кэшированные ключи
                    remote_keys = await client_pool.get_all_keys_cached(server_id, "outline", protocol_client)
                    if not remote_keys:
                        return

                    # Находим ключи для удаления
                    keys_to_delete = []
                    for remote_entry in remote_keys:
                        remote_key_id = extract_outline_key_id(remote_entry)
                        if not remote_key_id or remote_key_id in db_key_ids:
                            continue

                        remote_name = (remote_entry.get("name") or "").lower().strip()
                        if remote_name in db_emails:
                            continue

                        keys_to_delete.append({"key_id": remote_key_id})
                    
                    # Удаляем лишние ключи
                    if keys_to_delete:
                        logger.info(f"    Сервер #{server_id} ({server_name}): найдено {len(keys_to_delete)} лишних ключей")
                        
                        if not dry_run:
                            async def delete_outline_key(key_info: Dict[str, Any]) -> bool:
                                async with api_semaphore:
                                    try:
                                        deleted = await protocol_client.delete_user(key_info["key_id"])
                                        if deleted:
                                            stats["keys_deleted_from_servers"] += 1
                                            return True
                                        return False
                                    except Exception as e:
                                        logger.warning(f"      Ошибка удаления ключа {key_info['key_id']}: {e}")
                                        return False
                            
                            delete_tasks = [delete_outline_key(key_info) for key_info in keys_to_delete]
                            await asyncio.gather(*delete_tasks, return_exceptions=True)
                
                except Exception as e:
                    stats["errors"] += 1
                    error_msg = f"Ошибка удаления лишних Outline ключей с сервера #{server_id}: {e}"
                    stats["errors_details"].append({
                        "type": "outline_delete_orphaned",
                        "server_id": server_id,
                        "error": error_msg,
                    })
                    logger.error(f"    ✗ {error_msg}")
        
        # ОПТИМИЗАЦИЯ: Параллельная обработка серверов (до 5 одновременно)
        if outline_servers_all:
            outline_delete_tasks = [process_outline_server_delete_orphaned(server) for server in outline_servers_all]
            await asyncio.gather(*outline_delete_tasks, return_exceptions=True)
        
        # 4.4. Синхронизация Outline конфигураций (access_url)
        if sync_configs:
            logger.info("  [4.4] Синхронизация Outline конфигураций...")
            
            # Получаем все Outline ключи для синхронизации
            with get_db_cursor() as cursor:
                if server_id:
                    cursor.execute("""
                SELECT k.id, k.key_id, k.access_url, k.server_id, k.user_id, k.email,
                       k.subscription_id, s.name, s.api_url, s.cert_sha256
                FROM keys k
                JOIN servers s ON k.server_id = s.id
                WHERE s.protocol = 'outline' AND s.active = 1 AND k.server_id = ?
                  AND k.key_id IS NOT NULL AND k.key_id != ''
                    """, (server_id,))
                else:
                    cursor.execute("""
                        SELECT k.id, k.key_id, k.access_url, k.server_id, k.user_id, k.email,
                               k.subscription_id, s.name, s.api_url, s.cert_sha256
                        FROM keys k
                        JOIN servers s ON k.server_id = s.id
                        WHERE s.protocol = 'outline' AND s.active = 1
                          AND k.key_id IS NOT NULL AND k.key_id != ''
                    """)
                outline_keys = cursor.fetchall()
            
            logger.info(f"    Найдено {len(outline_keys)} Outline ключей для синхронизации")
            
            # Группируем ключи по серверам
            outline_keys_by_server: Dict[int, List[Tuple]] = {}
            for key_row in outline_keys:
                server_id_key = key_row[3]
                if server_id_key not in outline_keys_by_server:
                    outline_keys_by_server[server_id_key] = []
                outline_keys_by_server[server_id_key].append(key_row)
            
            # Получаем токены подписок для инвалидации кэша
            outline_subscription_ids = {key[6] for key in outline_keys if key[6]}
            outline_tokens_to_invalidate = set()
            if outline_subscription_ids:
                placeholders = ','.join('?' * len(outline_subscription_ids))
                with get_db_cursor() as cursor:
                    cursor.execute(f"""
                        SELECT id, subscription_token
                        FROM subscriptions
                        WHERE id IN ({placeholders})
                    """, list(outline_subscription_ids))
                    for sub_id, token in cursor.fetchall():
                        outline_tokens_to_invalidate.add(token)
            
            # Синхронизируем конфигурации
            outline_updates: List[Tuple[str, int]] = []  # (new_access_url, key_id)
            
            async def process_outline_server_sync_configs(server_id_key: int, server_keys: List[Tuple]) -> List[Tuple[str, int]]:
                """Обработать один Outline сервер: синхронизировать конфигурации ключей"""
                async with server_semaphore:
                    server_updates = []
                    
                    # ОПТИМИЗАЦИЯ: Используем словарь для быстрого доступа
                    server_info = servers_by_id.get(server_id_key)
                    if not server_info:
                        return []
                    
                    try:
                        # ОПТИМИЗАЦИЯ: Используем пул клиентов
                        protocol_client = await client_pool.get_client(server_info)
                        if not protocol_client:
                            return []
                        
                        # ОПТИМИЗАЦИЯ: Получаем все ключи один раз и кэшируем
                        remote_keys_all = await client_pool.get_all_keys_cached(server_id_key, "outline", protocol_client)
                        remote_keys_dict = {
                            str(extract_outline_key_id(k)): k
                            for k in remote_keys_all
                            if extract_outline_key_id(k)
                        }
                        
                        async def sync_single_outline_key(key_row: Tuple) -> Optional[Tuple[str, int]]:
                            """Синхронизировать один Outline ключ"""
                            async with api_semaphore:
                                try:
                                    key_id_db, key_id, old_access_url, _, _, _, sub_id, _, _, _ = key_row
                                    
                                    # ОПТИМИЗАЦИЯ: Используем кэшированные ключи вместо запроса к серверу
                                    remote_key = remote_keys_dict.get(str(key_id))
                                    
                                    if not remote_key:
                                        # Ключа нет на сервере - пропускаем (будет удален на другом этапе)
                                        return None
                                    
                                    # Получаем access_url из ответа сервера
                                    new_access_url = remote_key.get("accessUrl") or remote_key.get("access_url") or ""
                                    
                                    # Сравниваем с текущим access_url
                                    if old_access_url != new_access_url and new_access_url:
                                        return (new_access_url, key_id_db)
                                    return None
                                    
                                except Exception as e:
                                    logger.warning(f"      Ошибка синхронизации Outline ключа #{key_row[0]}: {e}")
                                    return None
                        
                        # Параллельно синхронизируем ключи батчами (для БД операций)
                        batch_size = 50
                        for i in range(0, len(server_keys), batch_size):
                            batch = server_keys[i:i + batch_size]
                            sync_tasks = [sync_single_outline_key(key_row) for key_row in batch]
                            batch_results = await asyncio.gather(*sync_tasks, return_exceptions=True)
                            
                            for result in batch_results:
                                if isinstance(result, Exception):
                                    continue
                                if result:
                                    server_updates.append(result)
                    
                    except Exception as e:
                        stats["errors"] += 1
                        error_msg = f"Ошибка синхронизации Outline конфигураций сервера #{server_id_key}: {e}"
                        stats["errors_details"].append({
                            "type": "outline_sync_configs",
                            "server_id": server_id_key,
                            "error": error_msg,
                        })
                        logger.error(f"    ✗ {error_msg}")
                    
                    return server_updates
            
            # ОПТИМИЗАЦИЯ: Параллельная обработка серверов (до 5 одновременно)
            if outline_keys_by_server:
                outline_sync_tasks = [
                    process_outline_server_sync_configs(server_id_key, server_keys)
                    for server_id_key, server_keys in outline_keys_by_server.items()
                ]
                outline_sync_results = await asyncio.gather(*outline_sync_tasks, return_exceptions=True)
                
                for result in outline_sync_results:
                    if isinstance(result, Exception):
                        continue
                    if isinstance(result, list):
                        outline_updates.extend(result)
            
            # Обновляем access_url в БД батчем
            if outline_updates and not dry_run:
                with get_db_cursor(commit=True) as cursor:
                    cursor.executemany("""
                        UPDATE keys
                        SET access_url = ?
                        WHERE id = ?
                    """, outline_updates)
                    stats["outline_configs_updated"] = len(outline_updates)
                    logger.info(f"    Обновлено конфигураций: {len(outline_updates)}")
            
            # Инвалидируем кэш подписок
            if not dry_run:
                for token in outline_tokens_to_invalidate:
                    invalidate_subscription_cache(token)
        else:
            logger.info("  [4.4] Пропущено (sync_configs=False)")
    
    finally:
        # ОПТИМИЗАЦИЯ: Закрываем все клиенты из пула (гарантированно)
        await client_pool.close_all()
    
    # ========== ИТОГОВАЯ СТАТИСТИКА ==========
    stats["duration_seconds"] = time.time() - start_time
    
    logger.info("\n" + "=" * 60)
    logger.info("ИТОГО:")
    logger.info(f"  Обработано серверов: {stats['servers_processed']}")
    logger.info(f"  Недоступных серверов: {stats['servers_unavailable']}")
    logger.info(f"  Удалено ключей из БД: {stats['keys_deleted_from_db']}")
    logger.info(f"  Удалено ключей с серверов: {stats['keys_deleted_from_servers']}")
    logger.info(f"  Создано V2Ray ключей: {stats['v2ray_keys_created']}")
    logger.info(f"  Обновлено V2Ray конфигураций: {stats['v2ray_configs_updated']}")
    logger.info(f"  Создано Outline ключей: {stats['outline_keys_created']}")
    logger.info(f"  Обновлено Outline конфигураций: {stats['outline_configs_updated']}")
    logger.info(f"  Удалено Outline ключей (не с Outline-сервера): {stats['outline_keys_removed']}")
    logger.info(f"  Ошибок: {stats['errors']}")
    logger.info(f"  Время выполнения: {stats['duration_seconds']:.2f} сек")
    logger.info("=" * 60)
    
    if dry_run:
        logger.info("\n⚠️  Это был DRY RUN - изменения не были применены")
    
    return stats


async def main():
    """Главная функция"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Синхронизировать все ключи V2Ray и Outline с серверами')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Показать что будет обновлено, не изменяя БД'
    )
    parser.add_argument(
        '--server-id',
        type=int,
        help='Синхронизировать только ключи с указанного сервера'
    )
    
    args = parser.parse_args()
    
    try:
        result = await sync_all_keys_with_servers(
            dry_run=args.dry_run,
            server_id=args.server_id,
        )
        if result:
            logger.info(f"\nРезультат синхронизации: {result}")
    except KeyboardInterrupt:
        logger.info("\n\nПрервано пользователем")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
