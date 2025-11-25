#!/usr/bin/env python3
"""
Скрипт для синхронизации всех ключей V2Ray и Outline с серверами
Обновляет client_config в БД актуальными данными с серверов
Удаляет ключи с серверов, которых нет в базе данных
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


def extract_sid_sni(config: str) -> Tuple[Optional[str], Optional[str]]:
    """Извлечь short id и SNI из конфигурации"""
    if not config or '?' not in config:
        return None, None
    
    try:
        params_str = config.split('?')[1].split('#')[0]
        params = urllib.parse.parse_qs(params_str)
        sid = params.get('sid', [None])[0]
        sni = params.get('sni', [None])[0]
        return sid, sni
    except Exception:
        return None, None


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


async def create_missing_keys_for_available_servers(server_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Создать недостающие ключи V2Ray на серверах, доступных для покупки.
    Рассматриваются только активные подписки.
    """
    stats = {
        "missing_pairs_total": 0,
        "missing_keys_created": 0,
        "missing_keys_failed": 0,
        "servers_checked": 0,
        "servers_with_missing_keys": 0,
    }

    now = int(time.time())

    with get_db_cursor() as cursor:
        query = """
            SELECT id, name, api_url, api_key, domain, available_for_purchase
            FROM servers
            WHERE protocol = 'v2ray'
              AND active = 1
              AND available_for_purchase = 1
        """
        params: List[Any] = []
        if server_id is not None:
            query += " AND id = ?"
            params.append(server_id)

        cursor.execute(query, params)
        server_rows = cursor.fetchall()

    if not server_rows:
        return stats

    servers = [dict(row) for row in server_rows]
    stats["servers_checked"] = len(servers)

    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, user_id, subscription_token, expires_at, tariff_id
            FROM subscriptions
            WHERE is_active = 1
              AND expires_at > ?
            """,
            (now,),
        )
        subscription_rows = cursor.fetchall()

    if not subscription_rows:
        return stats

    subscriptions = [dict(row) for row in subscription_rows]

    server_ids = [server["id"] for server in servers]
    existing_pairs = set()
    if server_ids:
        placeholders = ",".join("?" for _ in server_ids)
        with get_db_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT server_id, subscription_id
                FROM v2ray_keys
                WHERE subscription_id IS NOT NULL
                  AND server_id IN ({placeholders})
                """,
                server_ids,
            )
            for row in cursor.fetchall():
                existing_pairs.add((row["server_id"], row["subscription_id"]))

    missing_pairs_by_server: Dict[int, List[Dict[str, Any]]] = {}
    for server in servers:
        server_missing: List[Dict[str, Any]] = []
        for subscription in subscriptions:
            sub_id = subscription["id"]
            if sub_id is None or (server["id"], sub_id) in existing_pairs:
                continue
            server_missing.append(subscription)
        if server_missing:
            missing_pairs_by_server[server["id"]] = server_missing
            stats["missing_pairs_total"] += len(server_missing)

    if stats["missing_pairs_total"] == 0:
        return stats

    for server in servers:
        pending_subscriptions = missing_pairs_by_server.get(server["id"])
        if not pending_subscriptions:
            continue

        api_url = server.get("api_url")
        api_key = server.get("api_key")
        domain = server.get("domain")
        server_name = server.get("name") or f"Server #{server['id']}"

        if not api_url or not api_key:
            logger.warning(
                "Пропускаем сервер %s (id=%s) — отсутствуют api_url или api_key",
                server_name,
                server["id"],
            )
            stats["missing_keys_failed"] += len(pending_subscriptions)
            continue

        protocol_client = None
        try:
            server_config = {
                "api_url": api_url,
                "api_key": api_key,
                "domain": domain,
            }
            protocol_client = ProtocolFactory.create_protocol("v2ray", server_config)
        except Exception as client_error:
            logger.error(
                "Не удалось создать клиента V2Ray для сервера %s (id=%s): %s",
                server_name,
                server["id"],
                client_error,
            )
            stats["missing_keys_failed"] += len(pending_subscriptions)
            continue

        stats["servers_with_missing_keys"] += 1

        for subscription in pending_subscriptions:
            sub_id = subscription["id"]
            user_id = subscription["user_id"]
            token = subscription["subscription_token"]
            expires_at = subscription["expires_at"]
            tariff_id = subscription["tariff_id"]
            key_email = f"{user_id}_subscription_{sub_id}@veilbot.com"

            # Перепроверяем в БД перед созданием (мог появиться конкурентно)
            with get_db_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id FROM v2ray_keys
                    WHERE server_id = ? AND subscription_id = ?
                    """,
                    (server["id"], sub_id),
                )
                if cursor.fetchone():
                    existing_pairs.add((server["id"], sub_id))
                    continue

            created_uuid: Optional[str] = None
            try:
                user_data = await protocol_client.create_user(key_email, name=server_name)
                if not user_data or not user_data.get("uuid"):
                    raise RuntimeError("V2Ray сервер не вернул uuid при создании пользователя")

                created_uuid = user_data["uuid"]

                client_config = await protocol_client.get_user_config(
                    created_uuid,
                    {
                        "domain": domain or settings.domain or "veil-bot.ru",
                        "port": 443,
                        "email": key_email,
                    },
                )

                if "vless://" in client_config:
                    for line in client_config.split("\n"):
                        candidate = line.strip()
                        if candidate.startswith("vless://"):
                            client_config = candidate
                            break

                client_config = normalize_vless_host(
                    client_config,
                    domain,
                    api_url or "",
                )
                client_config = remove_fragment_from_vless(client_config)

                with get_db_cursor(commit=True) as cursor:
                    with safe_foreign_keys_off(cursor):
                        cursor.execute(
                            """
                            INSERT INTO v2ray_keys
                            (server_id, user_id, v2ray_uuid, email, created_at, expiry_at,
                             tariff_id, client_config, subscription_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                server["id"],
                                user_id,
                                created_uuid,
                                key_email,
                                int(time.time()),
                                expires_at,
                                tariff_id,
                                client_config,
                                sub_id,
                            ),
                        )

                invalidate_subscription_cache(token)
                existing_pairs.add((server["id"], sub_id))
                stats["missing_keys_created"] += 1
            except Exception as creation_error:
                stats["missing_keys_failed"] += 1
                logger.error(
                    "Не удалось создать недостающий ключ для подписки %s на сервере %s (id=%s): %s",
                    sub_id,
                    server_name,
                    server["id"],
                    creation_error,
                    exc_info=True,
                )
                if protocol_client and created_uuid:
                    try:
                        await protocol_client.delete_user(created_uuid)
                    except Exception as cleanup_error:
                        logger.warning(
                            "Не удалось удалить незафиксированный ключ %s на сервере %s: %s",
                            created_uuid,
                            server["id"],
                            cleanup_error,
                        )

        if protocol_client:
            try:
                await protocol_client.close()
            except Exception:
                pass

    return stats


async def sync_outline_server_keys(
    server_id: int,
    server_name: str,
    api_url: str,
    cert_sha256: Optional[str],
    dry_run: bool,
    delete_orphaned: bool,
    orphan_stats: Dict[str, Any],
) -> Dict[str, Any]:
    """Синхронизировать Outline ключи для одного сервера (только удаление лишних)"""
    result = {
        "protocol": "outline",
        "updated": 0,
        "unchanged": 0,
        "failed": 0,
    }
    
    if not delete_orphaned:
        return result
    
    try:
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
        
        # Получаем ключи с сервера
        server_config = {
            'api_url': api_url,
            'cert_sha256': cert_sha256 or '',
        }
        protocol_client = ProtocolFactory.create_protocol('outline', server_config)
        
        try:
            remote_keys = await protocol_client.get_all_keys()
        except Exception as remote_error:
            logger.error(f"    ✗ Не удалось получить список ключей с сервера {server_name}: {remote_error}")
            orphan_stats["orphaned_remote_delete_errors"] += 1
            return result
        
        keys_to_delete: List[Dict[str, Any]] = []
        for remote_entry in remote_keys or []:
            remote_key_id = extract_outline_key_id(remote_entry)
            if not remote_key_id or remote_key_id in db_key_ids:
                continue
            
            remote_name = (remote_entry.get("name") or "").lower().strip()
            if remote_name in db_emails:
                continue
            
            keys_to_delete.append({
                "key_id": remote_key_id,
                "name": remote_entry.get("name"),
            })
        
        if keys_to_delete:
            orphan_stats["servers_with_orphaned_keys"] += 1
            orphan_stats["orphaned_remote_detected"] += len(keys_to_delete)
            logger.info(f"    Обнаружено {len(keys_to_delete)} ключей на сервере без соответствия в БД")
            
            if dry_run:
                logger.info("    [DRY RUN] Ключи не удалялись")
            else:
                for key_info in keys_to_delete:
                    key_id = key_info["key_id"]
                    try:
                        deleted = await protocol_client.delete_user(key_id)
                        if deleted:
                            orphan_stats["orphaned_remote_deleted"] += 1
                            logger.info(f"      ✓ Удален лишний ключ {key_id}")
                        else:
                            orphan_stats["orphaned_remote_delete_errors"] += 1
                            logger.warning(f"      ✗ Не удалось удалить лишний ключ {key_id}")
                    except Exception as delete_error:
                        orphan_stats["orphaned_remote_delete_errors"] += 1
                        logger.error(
                            f"      ✗ Ошибка при удалении ключа {key_id}: {delete_error}",
                            exc_info=True,
                        )
        else:
            logger.info("    Лишних ключей на сервере не найдено")
        
        try:
            await protocol_client.close()
        except Exception:
            pass
            
    except Exception as e:
        logger.error(f"    ✗ Ошибка обработки Outline сервера {server_name}: {e}", exc_info=True)
        result["failed"] = 1
    
    return result


async def sync_all_keys_with_servers(
    dry_run: bool = False,
    server_id: Optional[int] = None,
    ensure_missing_keys: bool = False,
    delete_orphaned_server_keys: bool = False,
) -> dict:
    """
    Синхронизировать все ключи V2Ray и Outline с серверами
    
    Args:
        dry_run: Если True, только показывает что будет обновлено, не изменяет БД и не удаляет ключи
        server_id: Если указан, синхронизирует только ключи с этого сервера
        ensure_missing_keys: Если True, создает недостающие V2Ray ключи на серверах
        delete_orphaned_server_keys: Если True, удаляет на серверах ключи, которых нет в БД
    
    Returns:
        dict: Словарь со статистикой синхронизации
    """
    missing_stats = {
        "missing_pairs_total": 0,
        "missing_keys_created": 0,
        "missing_keys_failed": 0,
        "servers_checked": 0,
        "servers_with_missing_keys": 0,
    }

    orphan_stats = {
        "orphaned_remote_detected": 0,
        "orphaned_remote_deleted": 0,
        "orphaned_remote_delete_errors": 0,
        "servers_with_orphaned_keys": 0,
    }

    if ensure_missing_keys and not dry_run:
        missing_stats = await create_missing_keys_for_available_servers(server_id=server_id)

    # Получаем все активные ключи V2Ray
    with get_db_cursor() as cursor:
        if server_id:
            cursor.execute("""
                SELECT 
                    k.id,
                    k.v2ray_uuid,
                    k.client_config,
                    k.server_id,
                    k.user_id,
                    k.email,
                    k.subscription_id,
                    s.name as server_name,
                    s.domain,
                    s.api_url,
                    s.api_key,
                    s.active
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE s.protocol = 'v2ray'
                  AND s.active = 1
                  AND k.server_id = ?
                ORDER BY k.server_id, k.id
            """, (server_id,))
        else:
            cursor.execute("""
                SELECT 
                    k.id,
                    k.v2ray_uuid,
                    k.client_config,
                    k.server_id,
                    k.user_id,
                    k.email,
                    k.subscription_id,
                    s.name as server_name,
                    s.domain,
                    s.api_url,
                    s.api_key,
                    s.active
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE s.protocol = 'v2ray'
                  AND s.active = 1
                ORDER BY k.server_id, k.id
            """)
        keys = cursor.fetchall()
    
    logger.info(f"Найдено {len(keys)} ключей V2Ray для синхронизации")
    
    total_updated = 0
    total_failed = 0
    total_skipped = 0
    total_unchanged = 0
    
    # Группируем ключи по серверам для эффективной обработки
    keys_by_server = {}
    for key_data in keys:
        server_id_key = key_data[3]  # server_id
        if server_id_key not in keys_by_server:
            keys_by_server[server_id_key] = []
        keys_by_server[server_id_key].append(key_data)
    
    logger.info(f"Ключи распределены по {len(keys_by_server)} серверам")
    
    for server_id_key, server_keys in keys_by_server.items():
        server_name = server_keys[0][6]  # server_name
        domain = server_keys[0][7]  # domain
        api_url = server_keys[0][8]  # api_url
        api_key = server_keys[0][9]  # api_key
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Сервер #{server_id_key}: {server_name}")
        logger.info(f"Ключей на сервере: {len(server_keys)}")
        
        if not api_url or not api_key:
            logger.warning(f"  ⚠️  Нет API URL или ключа для сервера #{server_id_key}")
            total_failed += len(server_keys)
            continue
        
        db_uuids: Set[str] = {
            (key_row[1] or "").strip()
            for key_row in server_keys
            if key_row[1]
        }
        db_emails: Set[str] = {
            (key_row[5] or "").lower().strip()
            for key_row in server_keys
            if key_row[5]
        }
        
        server_config = {
            'api_url': api_url,
            'api_key': api_key,
            'domain': domain,
        }
        
        protocol_client = None
        try:
            protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
        except Exception as e:
            logger.error(f"  ✗ Ошибка создания клиента для сервера #{server_id_key}: {e}")
            total_failed += len(server_keys)
            continue
        
        server_updated = 0
        server_failed = 0
        server_skipped = 0
        server_unchanged = 0
        
        for key_data in server_keys:
            (
                key_id,
                v2ray_uuid,
                old_client_config,
                server_id_db,
                user_id,
                email,
                subscription_id,
                server_name_db,
                domain_db,
                api_url_db,
                api_key_db,
                active
            ) = key_data
            
            logger.debug(f"  Ключ #{key_id} (UUID: {v2ray_uuid[:8]}...)")
            
            try:
                # Получаем актуальную конфигурацию с сервера
                fetched_config = await protocol_client.get_user_config(
                    v2ray_uuid,
                    {
                        'domain': domain,
                        'port': 443,
                        'email': f'user_{user_id}@veilbot.com',
                    },
                )
                
                # Извлекаем VLESS URL из конфигурации
                if 'vless://' in fetched_config:
                    lines = fetched_config.split('\n')
                    for line in lines:
                        if line.strip().startswith('vless://'):
                            fetched_config = line.strip()
                            break
                
                # Нормализуем конфигурацию
                new_client_config = normalize_vless_host(
                    fetched_config,
                    domain,
                    api_url or ''
                )
                
                # Удаляем фрагмент (email) из конфигурации
                new_client_config = remove_fragment_from_vless(new_client_config)
                
                # Извлекаем short id и SNI для сравнения
                old_sid, old_sni = extract_sid_sni(old_client_config) if old_client_config else (None, None)
                new_sid, new_sni = extract_sid_sni(new_client_config)
                
                # Проверяем, изменилась ли конфигурация
                if old_client_config == new_client_config:
                    logger.debug(f"    ✓ Конфигурация не изменилась")
                    server_unchanged += 1
                    total_unchanged += 1
                    continue
                
                # Логируем изменения
                if old_sid != new_sid:
                    logger.info(f"    🔄 Short ID изменился: {old_sid[:8] if old_sid else 'N/A'}... -> {new_sid[:8] if new_sid else 'N/A'}...")
                if old_sni != new_sni:
                    logger.info(f"    🔄 SNI изменился: {old_sni or 'N/A'} -> {new_sni or 'N/A'}")
                
                if not dry_run:
                    # Обновляем конфигурацию в БД
                    with get_db_cursor(commit=True) as update_cursor:
                        update_cursor.execute("""
                            UPDATE v2ray_keys
                            SET client_config = ?
                            WHERE id = ?
                        """, (new_client_config, key_id))
                    
                    logger.info(f"    ✓ Ключ #{key_id} обновлен (sid={new_sid[:8] if new_sid else 'N/A'}..., sni={new_sni or 'N/A'})")
                    
                    # Инвалидируем кэш подписки, если ключ в подписке
                    if subscription_id:
                        with get_db_cursor() as sub_cursor:
                            sub_cursor.execute(
                                'SELECT subscription_token FROM subscriptions WHERE id = ?',
                                (subscription_id,)
                            )
                            token_row = sub_cursor.fetchone()
                            if token_row:
                                invalidate_subscription_cache(token_row[0])
                                logger.debug(f"      Кэш подписки #{subscription_id} инвалидирован")
                    
                    server_updated += 1
                    total_updated += 1
                else:
                    logger.info(f"    [DRY RUN] Ключ #{key_id} будет обновлен")
                    server_updated += 1
                    total_updated += 1
                
            except Exception as e:
                logger.error(f"    ✗ Ошибка при синхронизации ключа #{key_id}: {e}")
                server_failed += 1
                total_failed += 1
                continue

        if delete_orphaned_server_keys:
            remote_fetch_failed = False
            try:
                remote_keys = await protocol_client.get_all_keys()
            except Exception as remote_error:
                logger.error(
                    f"    ✗ Не удалось получить список ключей с сервера {server_name}: {remote_error}"
                )
                orphan_stats["orphaned_remote_delete_errors"] += 1
                remote_keys = []
                remote_fetch_failed = True

            keys_to_delete: List[Dict[str, Any]] = []
            for remote_entry in remote_keys or []:
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

                keys_to_delete.append(
                    {
                        "uuid": remote_uuid,
                        "id": key_identifier,
                        "name": remote_entry.get("name") or (key_info.get("name") if isinstance(key_info, dict) else None),
                    }
                )

            if keys_to_delete:
                orphan_stats["servers_with_orphaned_keys"] += 1
                orphan_stats["orphaned_remote_detected"] += len(keys_to_delete)
                logger.info(
                    f"    Обнаружено {len(keys_to_delete)} ключей на сервере без соответствия в БД"
                )
                if dry_run:
                    logger.info("    [DRY RUN] Ключи не удалялись")
                else:
                    for key_info in keys_to_delete:
                        remote_uuid = key_info["uuid"]
                        key_identifier = key_info["id"] or remote_uuid
                        try:
                            deleted = await protocol_client.delete_user(str(key_identifier))
                            if deleted:
                                orphan_stats["orphaned_remote_deleted"] += 1
                                logger.info(f"      ✓ Удален лишний ключ {remote_uuid[:8]}...")
                            else:
                                orphan_stats["orphaned_remote_delete_errors"] += 1
                                logger.warning(f"      ✗ Не удалось удалить лишний ключ {remote_uuid[:8]}...")
                        except Exception as delete_error:
                            orphan_stats["orphaned_remote_delete_errors"] += 1
                            logger.error(
                                f"      ✗ Ошибка при удалении ключа {remote_uuid[:8]}...: {delete_error}",
                                exc_info=True,
                            )
            elif not remote_fetch_failed:
                logger.info("    Лишних ключей на сервере не найдено")
        
        logger.info(f"\n  Итого для сервера #{server_id_key}:")
        logger.info(f"    Обновлено: {server_updated}")
        logger.info(f"    Не изменилось: {server_unchanged}")
        logger.info(f"    Ошибок: {server_failed}")
        
        if protocol_client:
            try:
                await protocol_client.close()
            except Exception:
                pass
    
    # Обрабатываем Outline серверы (только удаление лишних ключей)
    if delete_orphaned_server_keys:
        logger.info(f"\n{'='*60}")
        logger.info("Обработка Outline серверов (удаление лишних ключей)")
        logger.info(f"{'='*60}")
        
        with get_db_cursor() as cursor:
            if server_id:
                cursor.execute("""
                    SELECT id, name, api_url, cert_sha256
                    FROM servers
                    WHERE protocol = 'outline' AND active = 1 AND id = ?
                    ORDER BY id
                """, (server_id,))
            else:
                cursor.execute("""
                    SELECT id, name, api_url, cert_sha256
                    FROM servers
                    WHERE protocol = 'outline' AND active = 1
                    ORDER BY id
                """)
            outline_servers = cursor.fetchall()
        
        if outline_servers:
            logger.info(f"Найдено {len(outline_servers)} активных Outline серверов")
            
            for server_row in outline_servers:
                server_id_outline, server_name_outline, api_url_outline, cert_sha256_outline = server_row
                
                logger.info(f"\n{'='*60}")
                logger.info(f"Outline сервер #{server_id_outline}: {server_name_outline}")
                
                if not api_url_outline:
                    logger.warning(f"  ⚠️  Нет API URL для сервера #{server_id_outline}")
                    continue
                
                await sync_outline_server_keys(
                    server_id_outline,
                    server_name_outline,
                    api_url_outline,
                    cert_sha256_outline,
                    dry_run,
                    delete_orphaned_server_keys,
                    orphan_stats,
                )
        else:
            logger.info("Активных Outline серверов не найдено")
    
    logger.info(f"\n{'='*60}")
    logger.info(f"ИТОГО:")
    logger.info(f"  Обновлено ключей V2Ray: {total_updated}")
    logger.info(f"  Не изменилось V2Ray: {total_unchanged}")
    logger.info(f"  Ошибок V2Ray: {total_failed}")

    if delete_orphaned_server_keys:
        logger.info(f"  Найдено лишних ключей: {orphan_stats['orphaned_remote_detected']}")
        logger.info(f"  Удалено лишних ключей: {orphan_stats['orphaned_remote_deleted']}")
        logger.info(f"  Ошибок при удалении: {orphan_stats['orphaned_remote_delete_errors']}")
    
    if dry_run:
        logger.info(f"\n⚠️  Это был DRY RUN - изменения не были применены")
        logger.info(f"Запустите скрипт без --dry-run для применения изменений")
    
    return {
        "total_keys": len(keys),
        "updated": total_updated,
        "unchanged": total_unchanged,
        "failed": total_failed,
        "servers_processed": len(keys_by_server),
        "dry_run": dry_run,
        "missing_pairs_total": missing_stats.get("missing_pairs_total", 0),
        "missing_keys_created": missing_stats.get("missing_keys_created", 0),
        "missing_keys_failed": missing_stats.get("missing_keys_failed", 0),
        "missing_keys_servers": missing_stats.get("servers_with_missing_keys", 0),
        "orphaned_remote_detected": orphan_stats.get("orphaned_remote_detected", 0),
        "orphaned_remote_deleted": orphan_stats.get("orphaned_remote_deleted", 0),
        "orphaned_remote_delete_errors": orphan_stats.get("orphaned_remote_delete_errors", 0),
        "orphaned_remote_servers": orphan_stats.get("servers_with_orphaned_keys", 0),
    }


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
    parser.add_argument(
        '--ensure-missing-keys',
        action='store_true',
        help='Создавать недостающие ключи на серверах, доступных для покупки, перед синхронизацией'
    )
    parser.add_argument(
        '--delete-orphaned',
        action='store_true',
        help='Удалять с серверов ключи, которых нет в базе данных'
    )
    
    args = parser.parse_args()
    
    try:
        result = await sync_all_keys_with_servers(
            dry_run=args.dry_run,
            server_id=args.server_id,
            ensure_missing_keys=args.ensure_missing_keys,
            delete_orphaned_server_keys=args.delete_orphaned,
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

