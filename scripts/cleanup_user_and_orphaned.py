#!/usr/bin/env python3
"""
Скрипт для:
1. Удаления подписок, платежей и ключей пользователя с сохранением флага о бесплатном ключе
2. Удаления всех подписок и ключей с серверов, отсутствующих в базе данных
3. Перезапуска сервиса
"""
import asyncio
import sys
import logging
import time
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional

# Добавляем корневую директорию в путь
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from app.repositories.subscription_repository import SubscriptionRepository
from bot.services.subscription_service import invalidate_subscription_cache
from utils import get_db_cursor
from app.infra.foreign_keys import safe_foreign_keys_off
from vpn_protocols import V2RayProtocol, OutlineProtocol
from outline import delete_key as outline_delete_key

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def extract_v2ray_uuid(remote_entry: Dict[str, Any]) -> Optional[str]:
    """Извлечь UUID из записи V2Ray"""
    uuid = remote_entry.get("uuid")
    if not uuid:
        key_info = remote_entry.get("key") or {}
        uuid = key_info.get("uuid")
    if not uuid:
        uuid = remote_entry.get("id")
    if isinstance(uuid, str) and uuid.strip():
        return uuid.strip()
    return None


async def cleanup_user_data_with_free_key_flag(user_id: int) -> Dict[str, int]:
    """
    Удалить все подписки, платежи и ключи пользователя,
    но сохранить флаг о том, что ему ранее был выдан бесплатный ключ
    """
    results = {
        'subscriptions': 0,
        'subscription_keys_v2ray': 0,
        'v2ray_keys': 0,
        'outline_keys': 0,
        'payments': 0,
        'free_key_flag_added': False,
        'errors': []
    }
    
    logger.info(f"Начинаю очистку данных пользователя {user_id}...")
    
    # Проверяем, есть ли уже запись о бесплатном ключе
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) FROM free_key_usage 
            WHERE user_id = ?
            """,
            (user_id,)
        )
        has_free_key_flag = cursor.fetchone()[0] > 0
    
    # Если нет записи, добавляем флаг о бесплатном ключе
    if not has_free_key_flag:
        logger.info(f"Добавляю флаг о бесплатном ключе для пользователя {user_id}...")
        with get_db_cursor(commit=True) as cursor:
            try:
                now = int(time.time())
                cursor.execute(
                    """
                    INSERT INTO free_key_usage (user_id, protocol, country, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, 'v2ray', None, now)
                )
                results['free_key_flag_added'] = True
                logger.info(f"✓ Флаг о бесплатном ключе добавлен")
            except Exception as e:
                logger.warning(f"Не удалось добавить флаг о бесплатном ключе: {e}")
                results['errors'].append(f"Ошибка добавления флага: {e}")
    else:
        logger.info(f"Флаг о бесплатном ключе уже существует для пользователя {user_id}")
        results['free_key_flag_added'] = True
    
    # 1. Удаление всех подписок пользователя
    logger.info(f"Удаление всех подписок пользователя {user_id}...")
    repo = SubscriptionRepository()
    
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, subscription_token
            FROM subscriptions
            WHERE user_id = ?
            """,
            (user_id,),
        )
        all_subscriptions = cursor.fetchall()
    
    for subscription_row in all_subscriptions:
        subscription_id = subscription_row["id"]
        token = subscription_row["subscription_token"]
        
        try:
            # Получить все ключи подписки
            subscription_keys = repo.get_subscription_keys_for_deletion(subscription_id)
            
            # Удалить ключи через V2Ray API
            for v2ray_uuid, api_url, api_key in subscription_keys:
                if v2ray_uuid and api_url and api_key:
                    protocol_client = None
                    try:
                        logger.info(f"Удаление V2Ray ключа подписки {v2ray_uuid} с сервера {api_url}")
                        protocol_client = V2RayProtocol(api_url, api_key)
                        result = await protocol_client.delete_user(v2ray_uuid)
                        if result:
                            results['subscription_keys_v2ray'] += 1
                            logger.info(f"✓ Успешно удален V2Ray ключ {v2ray_uuid}")
                    except Exception as exc:
                        error_msg = f"Ошибка при удалении V2Ray ключа {v2ray_uuid}: {exc}"
                        logger.error(error_msg, exc_info=True)
                        results['errors'].append(error_msg)
                    finally:
                        if protocol_client:
                            try:
                                await protocol_client.close()
                            except Exception:
                                pass
            
            # Удалить ключи из БД
            with get_db_cursor(commit=True) as cursor:
                with safe_foreign_keys_off(cursor):
                    cursor.execute(
                        "DELETE FROM v2ray_keys WHERE subscription_id = ?",
                        (subscription_id,),
                    )
            
            # Инвалидировать кэш перед удалением
            invalidate_subscription_cache(token)
            
            # Удалить подписку из БД
            with get_db_cursor(commit=True) as cursor:
                with safe_foreign_keys_off(cursor):
                    cursor.execute(
                        "DELETE FROM subscriptions WHERE id = ?",
                        (subscription_id,),
                    )
            
            results['subscriptions'] += 1
            logger.info(f"✓ Подписка {subscription_id} удалена")
        
        except Exception as exc:
            error_msg = f"Ошибка при удалении подписки {subscription_id}: {exc}"
            logger.error(error_msg, exc_info=True)
            results['errors'].append(error_msg)
    
    # 2. Удаление всех V2Ray ключей пользователя
    logger.info(f"Удаление всех V2Ray ключей пользователя {user_id}...")
    
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT k.v2ray_uuid, s.api_url, s.api_key, k.id
            FROM v2ray_keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.v2ray_uuid IS NOT NULL AND k.v2ray_uuid != ''
            """,
            (user_id,),
        )
        v2ray_keys = cursor.fetchall()
    
    for key_row in v2ray_keys:
        v2ray_uuid = key_row["v2ray_uuid"]
        api_url = key_row["api_url"]
        api_key = key_row["api_key"]
        db_id = key_row["id"]
        
        if v2ray_uuid and api_url and api_key:
            try:
                logger.info(f"Удаление V2Ray ключа {v2ray_uuid} с сервера {api_url}")
                protocol_client = V2RayProtocol(api_url, api_key)
                result = await protocol_client.delete_user(v2ray_uuid)
                if result:
                    results['v2ray_keys'] += 1
                    logger.info(f"✓ Успешно удален V2Ray ключ {v2ray_uuid}")
                await protocol_client.close()
            except Exception as exc:
                error_msg = f"Ошибка при удалении V2Ray ключа {v2ray_uuid}: {exc}"
                logger.error(error_msg, exc_info=True)
                results['errors'].append(error_msg)
    
    # Удалить все V2Ray ключи из БД
    with get_db_cursor(commit=True) as cursor:
        with safe_foreign_keys_off(cursor):
            cursor.execute(
                "DELETE FROM v2ray_keys WHERE user_id = ?",
                (user_id,),
            )
            deleted_count = cursor.rowcount
            logger.info(f"✓ Удалено {deleted_count} V2Ray ключей из БД")
    
    # 3. Удаление всех Outline ключей пользователя
    logger.info(f"Удаление всех Outline ключей пользователя {user_id}...")
    
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT k.key_id, s.api_url, s.cert_sha256, k.id
            FROM keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.key_id IS NOT NULL AND k.key_id != ''
            """,
            (user_id,),
        )
        outline_keys = cursor.fetchall()
    
    for key_row in outline_keys:
        key_id = key_row["key_id"]
        api_url = key_row["api_url"]
        cert_sha256 = key_row["cert_sha256"]
        db_id = key_row["id"]
        
        if key_id and api_url and cert_sha256:
            try:
                logger.info(f"Удаление Outline ключа {key_id} с сервера {api_url}")
                result = await asyncio.get_event_loop().run_in_executor(
                    None, outline_delete_key, api_url, cert_sha256, key_id
                )
                if result:
                    results['outline_keys'] += 1
                    logger.info(f"✓ Успешно удален Outline ключ {key_id}")
            except Exception as exc:
                error_msg = f"Ошибка при удалении Outline ключа {key_id}: {exc}"
                logger.error(error_msg, exc_info=True)
                results['errors'].append(error_msg)
    
    # Удалить все Outline ключи из БД
    with get_db_cursor(commit=True) as cursor:
        with safe_foreign_keys_off(cursor):
            cursor.execute(
                "DELETE FROM keys WHERE user_id = ?",
                (user_id,),
            )
            deleted_count = cursor.rowcount
            logger.info(f"✓ Удалено {deleted_count} Outline ключей из БД")
    
    # 4. Удаление всех платежей пользователя
    logger.info(f"Удаление всех платежей пользователя {user_id}...")
    
    with get_db_cursor(commit=True) as cursor:
        with safe_foreign_keys_off(cursor):
            cursor.execute(
                "DELETE FROM payments WHERE user_id = ?",
                (user_id,),
            )
            results['payments'] = cursor.rowcount
            logger.info(f"✓ Удалено {results['payments']} платежей из БД")
    
    return results


async def cleanup_orphaned_keys_and_subscriptions() -> Dict[str, Any]:
    """
    Удалить все подписки и ключи с серверов, отсутствующие в базе данных
    """
    results = {
        'servers_checked': 0,
        'orphaned_keys_found': 0,
        'orphaned_keys_deleted': 0,
        'orphaned_subscriptions_found': 0,
        'orphaned_subscriptions_deleted': 0,
        'errors': []
    }
    
    logger.info("Начинаю проверку серверов на наличие orphaned ключей и подписок...")
    
    # Получить все активные серверы
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, name, protocol, api_url, cert_sha256, api_key, country, domain
            FROM servers
            WHERE active = 1
            """
        )
        servers = cursor.fetchall()
    
    results['servers_checked'] = len(servers)
    logger.info(f"Найдено {len(servers)} активных серверов")
    
    # Получить все UUID и key_id из базы данных
    with get_db_cursor() as cursor:
        # V2Ray ключи
        cursor.execute(
            """
            SELECT DISTINCT v2ray_uuid, server_id, email
            FROM v2ray_keys
            WHERE v2ray_uuid IS NOT NULL AND v2ray_uuid != ''
            """
        )
        db_v2ray_keys = cursor.fetchall()
        
        # Outline ключи
        cursor.execute(
            """
            SELECT DISTINCT key_id, server_id, email
            FROM keys
            WHERE key_id IS NOT NULL AND key_id != ''
            """
        )
        db_outline_keys = cursor.fetchall()
    
    # Создать множества для быстрого поиска
    db_v2ray_uuids = set()
    db_v2ray_emails = set()
    for row in db_v2ray_keys:
        uuid = (row[0] or "").strip() if row[0] else ""
        email = (row[2] or "").lower() if row[2] else ""
        if uuid:
            db_v2ray_uuids.add(uuid)
        if email:
            db_v2ray_emails.add(email)
    
    db_outline_key_ids = set()
    db_outline_emails = set()
    for row in db_outline_keys:
        key_id = str(row[0]) if row[0] else ""
        email = (row[2] or "").lower() if row[2] else ""
        if key_id:
            db_outline_key_ids.add(key_id)
        if email:
            db_outline_emails.add(email)
    
    logger.info(f"В БД найдено {len(db_v2ray_uuids)} V2Ray UUID и {len(db_outline_key_ids)} Outline key_id")
    
    # Проверить каждый сервер
    for server_row in servers:
        server_id = server_row["id"]
        server_name = server_row["name"]
        protocol = (server_row["protocol"] or "outline").lower()
        api_url = server_row["api_url"]
        cert_sha256 = server_row["cert_sha256"]
        api_key = server_row["api_key"]
        
        if not api_url:
            logger.warning(f"Сервер {server_name} не имеет api_url, пропускаем")
            continue
        
        try:
            if protocol == "v2ray":
                if not api_key:
                    logger.warning(f"Сервер {server_name} не имеет api_key, пропускаем")
                    continue
                
                logger.info(f"Проверка V2Ray сервера {server_name}...")
                protocol_client = V2RayProtocol(api_url, api_key)
                remote_keys = await protocol_client.get_all_keys()
                await protocol_client.close()
                
                if not remote_keys:
                    logger.info(f"На сервере {server_name} нет ключей")
                    continue
                
                logger.info(f"На сервере {server_name} найдено {len(remote_keys)} ключей")
                
                # Найти orphaned ключи
                for remote_entry in remote_keys:
                    uuid = extract_v2ray_uuid(remote_entry)
                    name = (remote_entry.get("name") or "").lower()
                    email = (remote_entry.get("email") or "").lower()
                    
                    if not uuid:
                        continue
                    
                    # Проверить, есть ли этот ключ в БД
                    if uuid not in db_v2ray_uuids and name not in db_v2ray_emails and email not in db_v2ray_emails:
                        results['orphaned_keys_found'] += 1
                        logger.info(f"Найден orphaned V2Ray ключ на сервере {server_name}: UUID={uuid}")
                        
                        # Удалить с сервера
                        try:
                            protocol_client = V2RayProtocol(api_url, api_key)
                            result = await protocol_client.delete_user(uuid)
                            await protocol_client.close()
                            
                            if result:
                                results['orphaned_keys_deleted'] += 1
                                logger.info(f"✓ Успешно удален orphaned ключ {uuid}")
                            else:
                                results['errors'].append(f"Не удалось удалить ключ {uuid} с сервера {server_name}")
                        except Exception as exc:
                            error_msg = f"Ошибка при удалении ключа {uuid} с сервера {server_name}: {exc}"
                            logger.error(error_msg, exc_info=True)
                            results['errors'].append(error_msg)
            
            elif protocol == "outline":
                if not cert_sha256:
                    logger.warning(f"Сервер {server_name} не имеет cert_sha256, пропускаем")
                    continue
                
                logger.info(f"Проверка Outline сервера {server_name}...")
                protocol_client = OutlineProtocol(api_url, cert_sha256)
                remote_keys = await protocol_client.get_all_keys()
                
                if not remote_keys:
                    logger.info(f"На сервере {server_name} нет ключей")
                    continue
                
                logger.info(f"На сервере {server_name} найдено {len(remote_keys)} ключей")
                
                # Найти orphaned ключи
                for remote_key in remote_keys:
                    key_id = str(remote_key.get("id", ""))
                    name = (remote_key.get("name") or "").lower()
                    
                    if not key_id:
                        continue
                    
                    # Проверить, есть ли этот ключ в БД
                    if key_id not in db_outline_key_ids and name not in db_outline_emails:
                        results['orphaned_keys_found'] += 1
                        logger.info(f"Найден orphaned Outline ключ на сервере {server_name}: key_id={key_id}")
                        
                        # Удалить с сервера
                        try:
                            result = await asyncio.get_event_loop().run_in_executor(
                                None, outline_delete_key, api_url, cert_sha256, key_id
                            )
                            if result:
                                results['orphaned_keys_deleted'] += 1
                                logger.info(f"✓ Успешно удален orphaned ключ {key_id}")
                            else:
                                results['errors'].append(f"Не удалось удалить ключ {key_id} с сервера {server_name}")
                        except Exception as exc:
                            error_msg = f"Ошибка при удалении ключа {key_id} с сервера {server_name}: {exc}"
                            logger.error(error_msg, exc_info=True)
                            results['errors'].append(error_msg)
        
        except Exception as exc:
            error_msg = f"Ошибка при проверке сервера {server_name}: {exc}"
            logger.error(error_msg, exc_info=True)
            results['errors'].append(error_msg)
    
    # Удалить подписки без активных ключей
    logger.info("Проверка подписок без активных ключей...")
    with get_db_cursor(commit=True) as cursor:
        cursor.execute("""
            SELECT s.id, s.user_id, s.subscription_token, s.expires_at
            FROM subscriptions s
            LEFT JOIN v2ray_keys k ON s.id = k.subscription_id
            WHERE s.is_active = 1
            GROUP BY s.id
            HAVING COUNT(k.id) = 0
        """)
        orphaned_subscriptions = cursor.fetchall()
        
        results['orphaned_subscriptions_found'] = len(orphaned_subscriptions)
        
        for sub_id, user_id, token, expires_at in orphaned_subscriptions:
            logger.info(f"Удаление orphaned подписки {sub_id} (token: {token[:20]}..., user: {user_id})")
            with safe_foreign_keys_off(cursor):
                cursor.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
            results['orphaned_subscriptions_deleted'] += 1
    
    return results


def restart_service() -> bool:
    """Перезапустить сервис veilbot"""
    logger.info("Перезапуск сервиса veilbot...")
    
    try:
        # Остановить сервис
        result = subprocess.run(
            ['systemctl', 'stop', 'veilbot'],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            logger.warning(f"Предупреждение при остановке сервиса: {result.stderr}")
        
        # Подождать
        import time
        time.sleep(3)
        
        # Запустить сервис
        result = subprocess.run(
            ['systemctl', 'start', 'veilbot'],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            logger.error(f"Ошибка при запуске сервиса: {result.stderr}")
            return False
        
        # Подождать и проверить статус
        time.sleep(5)
        
        result = subprocess.run(
            ['systemctl', 'is-active', 'veilbot'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0 and result.stdout.strip() == 'active':
            logger.info("✓ Сервис успешно перезапущен")
            return True
        else:
            logger.error("Сервис не активен после перезапуска")
            return False
    
    except subprocess.TimeoutExpired:
        logger.error("Таймаут при перезапуске сервиса")
        return False
    except Exception as exc:
        logger.error(f"Ошибка при перезапуске сервиса: {exc}", exc_info=True)
        return False


async def main():
    user_id = 6358556135
    
    print(f"\n{'='*80}")
    print(f"ОЧИСТКА ДАННЫХ ПОЛЬЗОВАТЕЛЯ И ORPHANED КЛЮЧЕЙ")
    print(f"{'='*80}\n")
    
    # Задача 1: Удалить данные пользователя с сохранением флага о бесплатном ключе
    print(f"1. Удаление данных пользователя {user_id}...")
    print(f"   (подписки, платежи, ключи будут удалены, флаг о бесплатном ключе сохранен)\n")
    
    try:
        user_results = await cleanup_user_data_with_free_key_flag(user_id)
        
        print(f"\n✓ Задача 1 завершена:")
        print(f"  - Подписок удалено: {user_results['subscriptions']}")
        print(f"  - V2Ray ключей подписок удалено с серверов: {user_results['subscription_keys_v2ray']}")
        print(f"  - V2Ray ключей удалено с серверов: {user_results['v2ray_keys']}")
        print(f"  - Outline ключей удалено с серверов: {user_results['outline_keys']}")
        print(f"  - Платежей удалено: {user_results['payments']}")
        print(f"  - Флаг о бесплатном ключе: {'добавлен' if user_results['free_key_flag_added'] else 'уже существовал'}")
        
        if user_results['errors']:
            print(f"  ⚠️  Ошибок: {len(user_results['errors'])}")
            for error in user_results['errors']:
                print(f"    - {error}")
    
    except Exception as exc:
        logger.error(f"Ошибка при очистке данных пользователя: {exc}", exc_info=True)
        print(f"\n❌ Ошибка при очистке данных пользователя: {exc}")
        return
    
    # Задача 2: Удалить orphaned ключи и подписки
    print(f"\n{'='*80}")
    print(f"2. Удаление orphaned ключей и подписок с серверов...")
    print(f"{'='*80}\n")
    
    try:
        orphaned_results = await cleanup_orphaned_keys_and_subscriptions()
        
        print(f"\n✓ Задача 2 завершена:")
        print(f"  - Серверов проверено: {orphaned_results['servers_checked']}")
        print(f"  - Orphaned ключей найдено: {orphaned_results['orphaned_keys_found']}")
        print(f"  - Orphaned ключей удалено: {orphaned_results['orphaned_keys_deleted']}")
        print(f"  - Orphaned подписок найдено: {orphaned_results['orphaned_subscriptions_found']}")
        print(f"  - Orphaned подписок удалено: {orphaned_results['orphaned_subscriptions_deleted']}")
        
        if orphaned_results['errors']:
            print(f"  ⚠️  Ошибок: {len(orphaned_results['errors'])}")
            for error in orphaned_results['errors'][:10]:  # Показываем первые 10 ошибок
                print(f"    - {error}")
            if len(orphaned_results['errors']) > 10:
                print(f"    ... и еще {len(orphaned_results['errors']) - 10} ошибок")
    
    except Exception as exc:
        logger.error(f"Ошибка при очистке orphaned ключей: {exc}", exc_info=True)
        print(f"\n❌ Ошибка при очистке orphaned ключей: {exc}")
        return
    
    # Задача 3: Перезапустить сервис
    print(f"\n{'='*80}")
    print(f"3. Перезапуск сервиса...")
    print(f"{'='*80}\n")
    
    try:
        restart_success = restart_service()
        
        if restart_success:
            print(f"\n✓ Сервис успешно перезапущен")
        else:
            print(f"\n⚠️  Проблемы при перезапуске сервиса. Проверьте логи: journalctl -u veilbot -n 20")
    
    except Exception as exc:
        logger.error(f"Ошибка при перезапуске сервиса: {exc}", exc_info=True)
        print(f"\n❌ Ошибка при перезапуске сервиса: {exc}")
        return
    
    print(f"\n{'='*80}")
    print(f"✅ ВСЕ ЗАДАЧИ ВЫПОЛНЕНЫ")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    asyncio.run(main())










