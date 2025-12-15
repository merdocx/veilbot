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
    
    # Получаем все серверы
    with get_db_cursor() as cursor:
        if server_id:
            cursor.execute("""
                SELECT id, name, protocol, api_url, api_key, cert_sha256, domain, 
                       active, available_for_purchase
            FROM servers
                WHERE id = ?
            """, (server_id,))
        else:
            cursor.execute("""
                SELECT id, name, protocol, api_url, api_key, cert_sha256, domain,
                       active, available_for_purchase
                FROM servers
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
            "available_for_purchase": bool(row[8]),
        })
    
    # Получаем активные подписки
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT id, user_id, subscription_token, expires_at, tariff_id
            FROM subscriptions
            WHERE is_active = 1 AND expires_at > ?
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
        })
    
    logger.info(f"  Серверов в БД: {len(servers)}")
    logger.info(f"  Активных подписок: {len(subscriptions)}")
    
    # ========== ЭТАП 2: Удаление ключей для недоступных серверов ==========
    active_servers = [s for s in servers if s["active"]]
    unavailable_by_api = []  # Серверы, недоступные по API, но активные в БД
    
    if delete_inactive_server_keys:
        logger.info("\n[ЭТАП 2] Удаление ключей для недоступных серверов...")
        
        # ВАЖНО: Удаляем ключи ТОЛЬКО для серверов с active = 0 или отсутствующих в БД
        # Серверы с active = 1, но временно недоступные по API, НЕ должны терять ключи
        unavailable_server_ids = []
        for server in servers:
            if not server["active"]:
                unavailable_server_ids.append(server["id"])
                logger.info(f"  Сервер #{server['id']} ({server['name']}) неактивен в БД")
        
        # Проверяем доступность активных серверов по API (только для логирования и фильтрации)
        # НО НЕ удаляем ключи для временно недоступных серверов!
        server_availability_tasks = []
        for server in active_servers:
            task = check_server_availability(
                server["protocol"],
                server["api_url"],
                server.get("api_key"),
                server.get("cert_sha256"),
            )
            server_availability_tasks.append((server["id"], task))
        
        if server_availability_tasks:
            availability_results = await asyncio.gather(
                *[task for _, task in server_availability_tasks],
                return_exceptions=True
            )
            
            for i, (server_id, _) in enumerate(server_availability_tasks):
                result = availability_results[i]
                if isinstance(result, Exception) or not result:
                    unavailable_by_api.append(server_id)
                    server_name = next((s["name"] for s in servers if s["id"] == server_id), f"Server #{server_id}")
                    logger.warning(f"  Сервер #{server_id} ({server_name}) временно недоступен по API (ключи сохранены)")
        
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
        # Все равно проверяем доступность для фильтрации
        server_availability_tasks = []
        for server in active_servers:
            task = check_server_availability(
                server["protocol"],
                server["api_url"],
                server.get("api_key"),
                server.get("cert_sha256"),
            )
            server_availability_tasks.append((server["id"], task))
        
        if server_availability_tasks:
            availability_results = await asyncio.gather(
                *[task for _, task in server_availability_tasks],
                return_exceptions=True
            )
            
            for i, (server_id, _) in enumerate(server_availability_tasks):
                result = availability_results[i]
                if isinstance(result, Exception) or not result:
                    unavailable_by_api.append(server_id)
                    server_name = next((s["name"] for s in servers if s["id"] == server_id), f"Server #{server_id}")
                    logger.warning(f"  Сервер #{server_id} ({server_name}) временно недоступен по API (ключи сохранены)")
    
    # Фильтруем серверы: оставляем только активные (независимо от доступности по API)
    # Серверы, недоступные по API, будут пропущены на этапах синхронизации, но ключи останутся в БД
    available_servers = [s for s in servers if s["active"]]
    
    # Исключаем недоступные по API серверы из списка для создания ключей
    # (чтобы не тратить время на таймауты), но оставляем их для синхронизации конфигураций
    available_for_creation = [
        s for s in available_servers
        if s["id"] not in unavailable_by_api
    ]
    
    # Разделяем серверы по протоколам и доступности к покупке
    v2ray_servers_for_purchase = []
    v2ray_servers_all = []
    outline_servers_all = []
    outline_server_8 = None
    
    if include_v2ray:
        v2ray_servers_for_purchase = [
            s for s in available_for_creation
            if s["protocol"] == "v2ray" and s["available_for_purchase"]
        ]
        v2ray_servers_all = [
            s for s in available_servers
            if s["protocol"] == "v2ray"
        ]
        logger.info(f"  V2Ray серверов доступных к покупке: {len(v2ray_servers_for_purchase)}")
        logger.info(f"  V2Ray серверов активных (всего): {len(v2ray_servers_all)}")
    
    if include_outline:
        outline_servers_all = [
            s for s in available_servers
            if s["protocol"] == "outline"
        ]
        outline_server_8 = next(
            (s for s in outline_servers_all if s["id"] == 8 and s["available_for_purchase"]),
            None
        )
        logger.info(f"  Outline серверов активных: {len(outline_servers_all)}")
        logger.info(f"  Outline сервер №8 доступен: {outline_server_8 is not None}")
    
    if unavailable_by_api:
        logger.info(f"  ⚠️  Пропущено недоступных по API серверов: {len(unavailable_by_api)} (ключи не будут созданы, но останутся в БД)")
    
    # ОПТИМИЗАЦИЯ: Создаем словарь для быстрого доступа к серверам по ID
    servers_by_id = {s["id"]: s for s in servers}
    
    # ОПТИМИЗАЦИЯ: Создаем пул клиентов для переиспользования
    client_pool = ServerClientPool()
    
    # ОПТИМИЗАЦИЯ: Semaphore для параллельной обработки серверов (до 5 одновременно)
    server_semaphore = asyncio.Semaphore(5)
    
    try:
        # ========== ЭТАП 3: Синхронизация V2Ray ключей ==========
        if include_v2ray:
            logger.info("\n[ЭТАП 3] Синхронизация V2Ray ключей...")
            
            # 3.1. Создание недостающих ключей (только для серверов с available_for_purchase = 1)
            if create_missing:
                logger.info("  [3.1] Создание недостающих ключей...")
                
                async def process_server_create_keys(server: Dict[str, Any]) -> None:
                    """Обработать один сервер: создать недостающие ключи"""
                    async with server_semaphore:
                        server_id = server["id"]
                        server_name = server["name"]
                        
                        try:
                            # ОПТИМИЗАЦИЯ: Используем пул клиентов
                            protocol_client = await client_pool.get_client(server)
                            if not protocol_client:
                                logger.warning(f"    Сервер #{server_id} ({server_name}): не удалось создать клиент")
                                return

                            # Получаем существующие ключи для этого сервера
                            with get_db_cursor() as cursor:
                                cursor.execute("""
                                    SELECT subscription_id, v2ray_uuid
                                    FROM v2ray_keys
                                    WHERE server_id = ? AND subscription_id IS NOT NULL
                                """, (server_id,))
                                existing_keys = {row[0]: row[1] for row in cursor.fetchall()}
                            
                            # Находим подписки без ключей на этом сервере
                            missing_subscriptions = [
                                sub for sub in subscriptions
                                if sub["id"] not in existing_keys
                            ]
                            
                            if missing_subscriptions:
                                logger.info(f"    Сервер #{server_id} ({server_name}): создаем {len(missing_subscriptions)} ключей")
                                
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
                                            user_data = await asyncio.wait_for(
                                                protocol_client.create_user(key_email, name=server_name),
                                                timeout=15.0  # Таймаут 15 секунд для создания ключа
                                            )
                                            if not user_data or not user_data.get("uuid"):
                                                raise RuntimeError("V2Ray сервер не вернул uuid при создании пользователя")

                                            created_uuid = user_data["uuid"]

                                            # Получаем client_config с таймаутом
                                            client_config = await asyncio.wait_for(
                                                protocol_client.get_user_config(
                                                    created_uuid,
                                                    {
                                                        "domain": server.get("domain") or settings.domain or "veil-bot.ru",
                                                        "port": 443,
                                                        "email": key_email,
                                                    },
                                                ),
                                                timeout=15.0  # Таймаут 15 секунд для получения конфигурации
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
                                            if not dry_run:
                                                with get_db_cursor(commit=True) as cursor:
                                                    with safe_foreign_keys_off(cursor):
                                                        cursor.execute("""
                                                            INSERT INTO v2ray_keys
                                                            (server_id, user_id, v2ray_uuid, email, created_at, expiry_at,
                                                             tariff_id, client_config, subscription_id)
                                                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                                        """, (
                                                            server_id,
                                                            user_id,
                                                            created_uuid,
                                                            key_email,
                                                            now,
                                                            expires_at,
                                                            tariff_id,
                                                            client_config,
                                                            sub_id,
                                                        ))

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
                if v2ray_servers_for_purchase:
                    create_tasks = [process_server_create_keys(server) for server in v2ray_servers_for_purchase]
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
                
                try:
                    # ОПТИМИЗАЦИЯ: Используем пул клиентов
                    protocol_client = await client_pool.get_client(server)
                    if not protocol_client:
                        return
                    
                    # Получаем ключи из БД
                    with get_db_cursor() as cursor:
                        cursor.execute("""
                            SELECT v2ray_uuid, email
                            FROM v2ray_keys
                            WHERE server_id = ?
                        """, (server_id,))
                        db_keys = cursor.fetchall()
                    
                    db_uuids: Set[str] = {row[0].strip() for row in db_keys if row[0]}
                    db_emails: Set[str] = {(row[1] or "").lower().strip() for row in db_keys if row[1]}
                    
                    # ОПТИМИЗАЦИЯ: Используем кэшированные ключи
                    remote_keys = await client_pool.get_all_keys_cached(server_id, "v2ray", protocol_client)
                    if not remote_keys:
                        return
                    
                    # Находим ключи для удаления
                    keys_to_delete = []
                    for remote_entry in remote_keys:
                        remote_uuid = extract_v2ray_uuid(remote_entry)
                        if not remote_uuid or remote_uuid in db_uuids:
                            continue
                        
                        remote_name = (remote_entry.get("name") or "").lower().strip()
                        remote_email = (remote_entry.get("email") or "").lower().strip()
                        
                        key_info = remote_entry.get("key") if isinstance(remote_entry.get("key"), dict) else None
                        if isinstance(key_info, dict):
                            remote_name = remote_name or (key_info.get("name") or "").lower().strip()
                            remote_email = remote_email or (key_info.get("email") or "").lower().strip()
                        
                        if remote_name in db_emails or remote_email in db_emails:
                            continue
                        
                        key_identifier = (
                            remote_entry.get("id")
                            or remote_entry.get("key_id")
                            or (key_info.get("id") if isinstance(key_info, dict) else None)
                            or (key_info.get("key_id") if isinstance(key_info, dict) else None)
                            or remote_uuid
                        )
                        
                        keys_to_delete.append({
                            "uuid": remote_uuid,
                            "id": key_identifier,
                        })
                    
                    # Удаляем лишние ключи
                    if keys_to_delete:
                        logger.info(f"    Сервер #{server_id} ({server_name}): найдено {len(keys_to_delete)} лишних ключей")
                        
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
        if v2ray_servers_all:
            delete_tasks = [process_server_delete_orphaned(server) for server in v2ray_servers_all]
            await asyncio.gather(*delete_tasks, return_exceptions=True)
        
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
        
            # 4.1. Создание недостающих ключей на сервере №8
            if outline_server_8:
                server_id = outline_server_8["id"]
                server_name = outline_server_8["name"]
            
            # Пропускаем сервер, если он недоступен по API
            if server_id in unavailable_by_api:
                logger.warning(f"  [4.1] Сервер №8 ({server_name}) недоступен по API - пропускаем создание ключей (ключи сохранены в БД)")
            else:
                logger.info("  [4.1] Создание недостающих ключей на сервере №8...")
                
                try:
                    # ОПТИМИЗАЦИЯ: Используем пул клиентов
                    protocol_client = await client_pool.get_client(outline_server_8)
                    if not protocol_client:
                        logger.warning(f"    Сервер №8 ({server_name}): не удалось создать клиент")
                    else:
                        # Получаем существующие Outline ключи подписок на сервере №8
                        with get_db_cursor() as cursor:
                            cursor.execute("""
                                SELECT subscription_id, key_id
                                FROM keys
                                WHERE server_id = 8 AND subscription_id IS NOT NULL
                            """)
                            existing_keys = {row[0]: row[1] for row in cursor.fetchall()}
                        
                        # Находим подписки без ключей
                        missing_subscriptions = [
                            sub for sub in subscriptions
                            if sub["id"] not in existing_keys
                        ]
                        
                        if missing_subscriptions:
                            logger.info(f"    Сервер №8 ({server_name}): создаем {len(missing_subscriptions)} ключей")
                            
                            for subscription in missing_subscriptions:
                                sub_id = subscription["id"]
                                user_id = subscription["user_id"]
                                token = subscription["subscription_token"]
                                expires_at = subscription["expires_at"]
                                tariff_id = subscription["tariff_id"]
                                key_email = f"{user_id}_subscription_{sub_id}@veilbot.com"
                                
                                try:
                                    # Создаем ключ на сервере с таймаутом
                                    user_data = await asyncio.wait_for(
                                        protocol_client.create_user(key_email),
                                        timeout=15.0  # Таймаут 15 секунд для создания ключа
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
                                                    (server_id, user_id, key_id, access_url, email, created_at, expiry_at,
                                                     tariff_id, subscription_id, protocol)
                                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'outline')
                                                """, (
                                                    8,
                                                    user_id,
                                                    key_id,
                                                    access_url,
                                                    key_email,
                                                    now,
                                                    expires_at,
                                                    tariff_id,
                                                    sub_id,
                                                ))
                                        
                                        invalidate_subscription_cache(token)
                                    
                                    stats["outline_keys_created"] += 1
                                    logger.debug(f"      Создан ключ для подписки {sub_id}")
                                    
                                except asyncio.TimeoutError:
                                    stats["errors"] += 1
                                    error_msg = f"Таймаут создания Outline ключа для подписки {sub_id} (сервер №8 медленно отвечает)"
                                    stats["errors_details"].append({
                                        "type": "outline_create_timeout",
                                        "server_id": 8,
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
                                        "server_id": 8,
                                        "subscription_id": sub_id,
                                        "error": error_msg,
                                    })
                                    logger.error(f"      ✗ {error_msg}")
                                    if len(stats["errors_details"]) >= 50:
                                        break
                        
                        stats["servers_processed"] += 1
                
                except Exception as e:
                    stats["errors"] += 1
                    error_msg = f"Ошибка обработки сервера №8: {e}"
                    stats["errors_details"].append({
                        "type": "outline_server_8",
                        "server_id": 8,
                        "error": error_msg,
                    })
                    logger.error(f"    ✗ {error_msg}")
        
        # 4.2. Удаление Outline ключей подписок с других серверов (не №8)
        logger.info("  [4.2] Удаление Outline ключей подписок с других серверов...")
        
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT id, server_id, key_id, subscription_id
                FROM keys
                WHERE subscription_id IS NOT NULL AND server_id != 8 AND protocol = 'outline'
            """)
            keys_to_remove = cursor.fetchall()
        
        if keys_to_remove:
            logger.info(f"    Найдено {len(keys_to_remove)} Outline ключей подписок на серверах, отличных от №8")
            
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
    logger.info(f"  Удалено Outline ключей (не с сервера №8): {stats['outline_keys_removed']}")
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
