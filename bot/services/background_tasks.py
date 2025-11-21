"""
Модуль для фоновых задач бота
Вынесен из bot.py для улучшения поддерживаемости
"""
import asyncio
import time
import logging
from collections import defaultdict
from typing import Optional, Callable, Awaitable, Dict, Any
from datetime import datetime

from app.infra.sqlite_utils import get_db_cursor
from outline import delete_key
from vpn_protocols import format_duration, ProtocolFactory
from bot.utils import format_key_message, format_key_message_unified, safe_send_message
from bot.keyboards import get_main_menu
from bot.core import get_bot_instance
from bot.services.key_creation import select_available_server_by_protocol
from app.infra.foreign_keys import safe_foreign_keys_off
from memory_optimizer import optimize_memory, log_memory_usage
from config import ADMIN_ID
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.tariff_repository import TariffRepository
from bot.services.subscription_service import invalidate_subscription_cache

logger = logging.getLogger(__name__)

# Глобальная переменная для отслеживания уведомлений о низком количестве ключей
low_key_notified: bool = False

_TASK_ERROR_COOLDOWN_SECONDS = 1800
_task_last_error: Dict[str, float] = {}

TRAFFIC_NOTIFY_WARNING = 1
TRAFFIC_NOTIFY_DISABLED = 2
TRAFFIC_DISABLE_GRACE = 86400  # 24 часа


async def _notify_task_error(task_name: str, error: Exception) -> None:
    """Отправляет администратору уведомление об ошибке фоновой задачи с троттлингом."""
    now = time.time()
    last_notified = _task_last_error.get(task_name, 0.0)
    if now - last_notified < _TASK_ERROR_COOLDOWN_SECONDS:
        return

    bot = get_bot_instance()
    if not bot:
        return

    _task_last_error[task_name] = now

    await safe_send_message(
        bot,
        ADMIN_ID,
        (
            f"⚠️ Фоновая задача `{task_name}` завершилась с ошибкой:\n"
            f"`{type(error).__name__}: {error}`\n"
            "Повторная попытка будет выполнена автоматически."
        ),
        parse_mode="Markdown",
        mark_blocked=False,
    )


async def _run_periodic(
    task_name: str,
    interval_seconds: int,
    job: Callable[[], Awaitable[None]],
    *,
    max_backoff: Optional[int] = None,
    backoff_multiplier: int = 2,
) -> None:
    """Запускает корутину периодически с экспоненциальным backoff при ошибках."""

    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    backoff = interval_seconds
    max_backoff = max_backoff or interval_seconds * 8

    while True:
        started_at = time.time()
        try:
            await job()
            backoff = interval_seconds
        except Exception as error:  # noqa: BLE001
            logging.error("Error in %s: %s", task_name, error, exc_info=True)
            await _notify_task_error(task_name, error)
            backoff = min(backoff * backoff_multiplier, max_backoff)

        elapsed = time.time() - started_at
        sleep_for = max(backoff - elapsed, 0)
        await asyncio.sleep(sleep_for)


async def auto_delete_expired_keys() -> None:
    """Автоматическое удаление истекших ключей с grace period 24 часа."""

    GRACE_PERIOD = 86400  # 24 часа в секундах

    async def job() -> None:
        with get_db_cursor(commit=True) as cursor:
            now = int(time.time())
            grace_threshold = now - GRACE_PERIOD

            cursor.execute(
                """
                SELECT k.id, k.key_id, s.api_url, s.cert_sha256
                FROM keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.expiry_at <= ?
                """,
                (grace_threshold,),
            )
            expired_outline_keys = cursor.fetchall()

            outline_deleted = 0
            for _, key_id_outline, api_url, cert_sha256 in expired_outline_keys:
                if key_id_outline:
                    success = await asyncio.get_event_loop().run_in_executor(  # noqa: RUF006 - run_in_executor допустим
                        None, delete_key, api_url, cert_sha256, key_id_outline
                    )
                    if not success:
                        logging.warning("Failed to delete Outline key %s from server", key_id_outline)

            with safe_foreign_keys_off(cursor):
                cursor.execute("DELETE FROM keys WHERE expiry_at <= ?", (grace_threshold,))
                outline_deleted = cursor.rowcount

            cursor.execute(
                """
                SELECT k.id, k.v2ray_uuid, s.api_url, s.api_key
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.expiry_at <= ?
                """,
                (grace_threshold,),
            )
            expired_v2ray_keys = cursor.fetchall()

            v2ray_deleted = 0
            for _, v2ray_uuid, api_url, api_key in expired_v2ray_keys:
                if v2ray_uuid and api_url and api_key:
                    try:
                        from vpn_protocols import V2RayProtocol

                        protocol_client = V2RayProtocol(api_url, api_key)
                        await protocol_client.delete_user(v2ray_uuid)
                    except Exception as exc:  # noqa: BLE001
                        logging.warning(
                            "Failed to delete V2Ray key %s from server: %s", v2ray_uuid, exc
                        )

            try:
                with safe_foreign_keys_off(cursor):
                    cursor.execute("DELETE FROM v2ray_keys WHERE expiry_at <= ?", (grace_threshold,))
                    v2ray_deleted = cursor.rowcount
            except Exception as exc:  # noqa: BLE001
                logging.warning("Error deleting expired V2Ray keys: %s", exc)
                v2ray_deleted = 0

            if outline_deleted > 0 or v2ray_deleted > 0:
                logging.info(
                    "Deleted expired keys (grace period 24h): %s Outline, %s V2Ray",
                    outline_deleted,
                    v2ray_deleted,
                )

        try:
            optimize_memory()
            log_memory_usage()
        except Exception as exc:  # noqa: BLE001
            logging.error("Ошибка при оптимизации памяти: %s", exc)

    await _run_periodic(
        "auto_delete_expired_keys",
        interval_seconds=600,
        job=job,
        max_backoff=3600,
    )


async def notify_expiring_keys() -> None:
    """Уведомление пользователей об истекающих ключах."""

    async def job() -> None:
        bot = get_bot_instance()
        if not bot:
            logging.debug("Bot instance is not available for notify_expiring_keys")
            return

        outline_updates = []
        v2ray_updates = []
        notifications_to_send = []

        with get_db_cursor() as cursor:
            now = int(time.time())
            one_day = 86400
            one_hour = 3600
            ten_minutes = 600

            cursor.execute(
                """
                SELECT k.id, k.user_id, k.access_url, k.expiry_at,
                       k.created_at, COALESCE(k.notified, 0) as notified
                FROM keys k
                WHERE k.expiry_at > ?
                """,
                (now,),
            )
            outline_rows = cursor.fetchall()

            for key_id_db, user_id, access_url, expiry, created_at, notified in outline_rows:
                remaining_time = expiry - now
                if created_at is None:
                    logging.warning("Skipping Outline key %s - created_at is None", key_id_db)
                    continue

                # Проверяем наличие активной подписки у пользователя
                cursor.execute("""
                    SELECT id FROM subscriptions
                    WHERE user_id = ? AND is_active = 1 AND expires_at > ?
                    LIMIT 1
                """, (user_id, now))
                has_active_subscription = cursor.fetchone() is not None
                
                # Если у пользователя есть активная подписка, пропускаем уведомление о продлении ключа
                if has_active_subscription:
                    continue

                original_duration = expiry - created_at
                ten_percent_threshold = int(original_duration * 0.1)
                message = None
                new_notified = notified

                if (
                    original_duration > one_day
                    and one_hour < remaining_time <= one_day
                    and (notified & 4) == 0
                ):
                    time_str = format_duration(remaining_time)
                    message = f"⏳ Ваш ключ истечет через {time_str}:\n`{access_url}`\nПродлите доступ:"
                    new_notified = notified | 4
                elif (
                    original_duration > one_hour
                    and ten_minutes < remaining_time <= (one_hour + 60)
                    and (notified & 2) == 0
                ):
                    time_str = format_duration(remaining_time)
                    message = f"⏳ Ваш ключ истечет через {time_str}:\n`{access_url}`\nПродлите доступ:"
                    new_notified = notified | 2
                elif remaining_time > 0 and remaining_time <= ten_minutes and (notified & 8) == 0:
                    time_str = format_duration(remaining_time)
                    message = f"⏳ Ваш ключ истечет через {time_str}:\n`{access_url}`\nПродлите доступ:"
                    new_notified = notified | 8
                elif remaining_time > 0 and remaining_time <= ten_percent_threshold and (notified & 1) == 0:
                    time_str = format_duration(remaining_time)
                    message = f"⏳ Ваш ключ истечет через {time_str}:\n`{access_url}`\nПродлите доступ:"
                    new_notified = notified | 1

                if message:
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

                    keyboard = InlineKeyboardMarkup()
                    keyboard.add(InlineKeyboardButton("🔁 Продлить", callback_data="buy"))
                    notifications_to_send.append((user_id, message, keyboard))
                    outline_updates.append((new_notified, key_id_db))

            cursor.execute(
                """
                SELECT k.id, k.user_id, k.client_config, k.expiry_at,
                       k.created_at, COALESCE(k.notified, 0) as notified
                FROM v2ray_keys k
                WHERE k.expiry_at > ?
                """,
                (now,),
            )
            v2ray_rows = cursor.fetchall()

            for key_id_db, user_id, client_config, expiry, created_at, notified in v2ray_rows:
                remaining_time = expiry - now
                if created_at is None:
                    logging.warning("Skipping V2Ray key %s - created_at is None", key_id_db)
                    continue

                # Проверяем наличие активной подписки у пользователя
                cursor.execute("""
                    SELECT id FROM subscriptions
                    WHERE user_id = ? AND is_active = 1 AND expires_at > ?
                    LIMIT 1
                """, (user_id, now))
                has_active_subscription = cursor.fetchone() is not None
                
                # Если у пользователя есть активная подписка, пропускаем уведомление о продлении ключа
                if has_active_subscription:
                    continue

                original_duration = expiry - created_at
                ten_percent_threshold = int(original_duration * 0.1)
                message = None
                new_notified = notified
                key_display = client_config or "V2Ray ключ"

                if (
                    original_duration > one_day
                    and one_hour < remaining_time <= one_day
                    and (notified & 4) == 0
                ):
                    time_str = format_duration(remaining_time)
                    message = f"⏳ Ваш ключ истечет через {time_str}:\n`{key_display}`\nПродлите доступ:"
                    new_notified = notified | 4
                elif (
                    original_duration > one_hour
                    and ten_minutes < remaining_time <= (one_hour + 60)
                    and (notified & 2) == 0
                ):
                    time_str = format_duration(remaining_time)
                    message = f"⏳ Ваш ключ истечет через {time_str}:\n`{key_display}`\nПродлите доступ:"
                    new_notified = notified | 2
                elif remaining_time > 0 and remaining_time <= ten_minutes and (notified & 8) == 0:
                    time_str = format_duration(remaining_time)
                    message = f"⏳ Ваш ключ истечет через {time_str}:\n`{key_display}`\nПродлите доступ:"
                    new_notified = notified | 8
                elif remaining_time > 0 and remaining_time <= ten_percent_threshold and (notified & 1) == 0:
                    time_str = format_duration(remaining_time)
                    message = f"⏳ Ваш ключ истечет через {time_str}:\n`{key_display}`\nПродлите доступ:"
                    new_notified = notified | 1

                if message:
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

                    keyboard = InlineKeyboardMarkup()
                    keyboard.add(InlineKeyboardButton("🔁 Продлить", callback_data="buy"))
                    notifications_to_send.append((user_id, message, keyboard))
                    v2ray_updates.append((new_notified, key_id_db))

        # Отправляем уведомления (safe_send_message теперь имеет встроенный retry механизм)
        for user_id, message, keyboard in notifications_to_send:
            result = await safe_send_message(
                bot,
                user_id,
                message,
                reply_markup=keyboard,
                disable_web_page_preview=True,
                parse_mode="Markdown",
            )
            if result:
                logging.info("Sent expiry notification to user %s", user_id)
            else:
                logging.warning("Failed to deliver expiry notification to user %s after retries", user_id)

        # Обновляем флаги уведомлений в БД
        # safe_send_message теперь имеет встроенный retry механизм (до 3 попыток),
        # поэтому большинство временных ошибок будут обработаны автоматически
        if outline_updates:
            with get_db_cursor(commit=True) as cursor:
                cursor.executemany("UPDATE keys SET notified = ? WHERE id = ?", outline_updates)
                logging.info("Updated %s Outline keys with expiry notifications", len(outline_updates))

        if v2ray_updates:
            with get_db_cursor(commit=True) as cursor:
                cursor.executemany(
                    "UPDATE v2ray_keys SET notified = ? WHERE id = ?",
                    v2ray_updates,
                )
                logging.info("Updated %s V2Ray keys with expiry notifications", len(v2ray_updates))

    await _run_periodic(
        "notify_expiring_keys",
        interval_seconds=60,
        job=job,
        max_backoff=600,
    )


def _format_bytes_short(num_bytes: Optional[float]) -> str:
    if not num_bytes or num_bytes <= 0:
        return "0 Б"
    value = float(num_bytes)
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ"]
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    if idx == 0:
        return f"{int(value)} {units[idx]}"
    return f"{value:.2f} {units[idx]}"




async def check_key_availability() -> None:
    """Проверка количества доступных ключей и уведомление администратора."""

    async def job() -> None:
        global low_key_notified

        bot = get_bot_instance()
        if not bot:
            logging.debug("Bot instance is not available for check_key_availability")
            return

        with get_db_cursor() as cursor:
            cursor.execute("SELECT SUM(max_keys) FROM servers WHERE active = 1")
            total_capacity = cursor.fetchone()[0] or 0

            now = int(time.time())
            cursor.execute("SELECT COUNT(*) FROM keys WHERE expiry_at > ?", (now,))
            active_keys = cursor.fetchone()[0] or 0

            free_keys = total_capacity - active_keys

        if free_keys < 6:
            if not low_key_notified:
                await safe_send_message(
                    bot,
                    ADMIN_ID,
                    f"⚠️ **Внимание:** Осталось мало свободных ключей: *{free_keys}*.",
                    parse_mode="Markdown",
                    mark_blocked=False,
                )
                low_key_notified = True
        else:
            if low_key_notified:
                await safe_send_message(
                    bot,
                    ADMIN_ID,
                    f"✅ **Статус:** Количество свободных ключей восстановлено: *{free_keys}*.",
                    parse_mode="Markdown",
                    mark_blocked=False,
                )
            low_key_notified = False

    await _run_periodic(
        "check_key_availability",
        interval_seconds=300,
        job=job,
        max_backoff=1800,
    )


async def process_pending_paid_payments() -> None:
    """
    Обработка оплаченных платежей без созданных ключей
    
    Фоновая задача, которая проверяет платежи со статусом 'paid', для которых
    еще не были созданы VPN ключи, и создает их автоматически.
    
    Использует новый платежный модуль, если он доступен, с fallback на старый код.
    """
    from outline import create_key  # Lazy import сохранен ниже для совместимости
    from bot.utils import format_key_message

    async def _process_with_new_module() -> bool:
        try:
            from memory_optimizer import get_payment_service

            payment_service = get_payment_service()
            if not payment_service:
                return False

            from payments.adapters.legacy_adapter import process_pending_paid_payments_legacy

            await process_pending_paid_payments_legacy()
            return True
        except Exception as exc:  # noqa: BLE001
            logging.warning("Ошибка в новом платежном модуле, используем старый: %s", exc)
            return False

    async def _process_fallback() -> None:
        bot = get_bot_instance()
        if not bot:
            logging.debug("Bot instance is not available for process_pending_paid_payments")
            return

        main_menu = get_main_menu()

        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                '''
                    SELECT p.id, p.user_id, p.tariff_id, p.email, p.protocol, p.country
                    FROM payments p
                    WHERE p.status="paid" AND p.revoked = 0
                    AND p.user_id NOT IN (
                        SELECT user_id FROM keys WHERE expiry_at > ?
                        UNION
                        SELECT user_id FROM v2ray_keys WHERE expiry_at > ?
                        UNION
                        SELECT user_id FROM subscriptions WHERE expires_at > ? AND is_active = 1
                    )
                ''',
                (int(time.time()), int(time.time()), int(time.time())),
            )
            payments = cursor.fetchall()

            for payment_db_id, user_id, tariff_id, email, protocol, country in payments:
                cursor.execute("SELECT status, payment_id, metadata FROM payments WHERE id = ?", (payment_db_id,))
                status_row = cursor.fetchone()
                if not status_row:
                    logging.warning("[AUTO-ISSUE] Payment id=%s not found, skipping", payment_db_id)
                    continue
                
                payment_status = (status_row[0] or "").lower()
                payment_uuid = status_row[1]
                payment_metadata_str = status_row[2] if len(status_row) > 2 else None
                
                if payment_status == "completed":
                    logging.info(
                        "[AUTO-ISSUE] Payment %s already completed, skipping key issuance", payment_uuid
                    )
                    continue
                
                if payment_status != "paid":
                    cursor.execute("UPDATE payments SET status = 'paid' WHERE id = ?", (payment_db_id,))
                
                # Парсим метаданные
                import json
                payment_metadata = {}
                if payment_metadata_str:
                    try:
                        payment_metadata = json.loads(payment_metadata_str) if isinstance(payment_metadata_str, str) else payment_metadata_str
                    except (json.JSONDecodeError, TypeError):
                        payment_metadata = {}
                
                # Получаем тариф сначала (нужен для всех типов платежей)
                cursor.execute("SELECT name, duration_sec, price_rub, traffic_limit_mb FROM tariffs WHERE id=?", (tariff_id,))
                tariff_row = cursor.fetchone()
                if not tariff_row:
                    logging.error("[AUTO-ISSUE] Не найден тариф id=%s для user_id=%s", tariff_id, user_id)
                    continue

                tariff = {
                    "id": tariff_id,
                    "name": tariff_row[0],
                    "duration_sec": tariff_row[1],
                    "price_rub": tariff_row[2],
                    "traffic_limit_mb": tariff_row[3],
                }

                protocol = protocol or "outline"
                
                # Проверяем, нужно ли создавать подписку
                key_type = payment_metadata.get('key_type') if payment_metadata else None
                is_subscription = key_type == 'subscription' and protocol == 'v2ray'
                
                # Если это подписка, используем новый сервис для обработки
                if is_subscription:
                    try:
                        from payments.services.subscription_purchase_service import SubscriptionPurchaseService
                        
                        subscription_service = SubscriptionPurchaseService()
                        success, error_msg = await subscription_service.process_subscription_purchase(payment_uuid)
                        
                        if success:
                            logging.info(
                                f"[AUTO-ISSUE] Subscription purchase processed successfully for payment {payment_uuid}, "
                                f"user {user_id}, notification sent"
                            )
                            # Платеж уже помечен как completed в process_subscription_purchase после успешной отправки уведомления
                            # Не нужно обновлять статус здесь
                        else:
                            logging.error(
                                f"[AUTO-ISSUE] Failed to process subscription purchase for payment {payment_uuid}, "
                                f"user {user_id}: {error_msg}. Will retry."
                            )
                            # НЕ помечаем платеж как completed при ошибке, чтобы повторить попытку
                            # Это особенно важно, если уведомление не было отправлено
                    except Exception as exc:
                        logging.error(
                            f"[AUTO-ISSUE] Error processing subscription purchase for payment {payment_uuid}, "
                            f"user {user_id}: {exc}",
                            exc_info=True
                        )
                        # НЕ помечаем платеж как completed при ошибке, чтобы повторить попытку
                    # ВАЖНО: continue здесь гарантирует, что код не продолжит создание обычного ключа
                    continue
                # Старый код обработки подписок удален - теперь используется SubscriptionPurchaseService
                # Продолжаем обработку обычных ключей для не-подписок
                server = select_available_server_by_protocol(cursor, country, protocol)
                if not server:
                    logging.error(
                        "[AUTO-ISSUE] Нет доступных серверов %s для user_id=%s, тариф=%s, страна=%s",
                        protocol,
                        user_id,
                        tariff,
                        country,
                    )
                    continue

                server_dict = {
                    "id": server[0] if len(server) > 0 else None,
                    "name": server[1] if len(server) > 1 else None,
                    "api_url": server[2] if len(server) > 2 else None,
                    "cert_sha256": server[3] if len(server) > 3 else None,
                    "domain": server[4] if len(server) > 4 else None,
                    "api_key": server[5] if len(server) > 5 else None,
                    "v2ray_path": server[6] if len(server) > 6 else None,
                }

                if protocol == "outline":
                    try:
                        key = await asyncio.get_event_loop().run_in_executor(
                            None, create_key, server_dict["api_url"], server_dict["cert_sha256"]
                        )
                    except Exception as exc:  # noqa: BLE001
                        logging.error(
                            "[AUTO-ISSUE] Ошибка при создании Outline ключа для user_id=%s: %s",
                            user_id,
                            exc,
                        )
                        continue
                    if not key:
                        logging.error(
                            "[AUTO-ISSUE] Не удалось создать Outline ключ для user_id=%s, тариф=%s",
                            user_id,
                            tariff,
                        )
                        continue
                    
                    # Проверяем, что key - это словарь, а не кортеж
                    if not isinstance(key, dict):
                        logging.error(
                            "[AUTO-ISSUE] Invalid key format for user_id=%s: expected dict, got %s",
                            user_id,
                            type(key).__name__,
                        )
                        continue
                    
                    # Проверяем наличие необходимых полей
                    if "accessUrl" not in key or "id" not in key:
                        logging.error(
                            "[AUTO-ISSUE] Missing required fields in key for user_id=%s: %s",
                            user_id,
                            list(key.keys()) if isinstance(key, dict) else "not a dict",
                        )
                        continue

                    now = int(time.time())
                    expiry = now + tariff["duration_sec"]
                    traffic_limit_mb = 0
                    try:
                        traffic_limit_mb = int(tariff.get("traffic_limit_mb") or 0)
                    except (TypeError, ValueError):
                        traffic_limit_mb = 0
                    cursor.execute(
                        "INSERT INTO keys (server_id, user_id, access_url, expiry_at, traffic_limit_mb, notified, key_id, created_at, email, tariff_id, protocol) "
                        "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
                        (
                            server_dict["id"],
                            user_id,
                            key["accessUrl"],
                            expiry,
                            traffic_limit_mb,
                            key["id"],
                            now,
                            email,
                            tariff_id,
                            protocol,
                        ),
                    )

                    if tariff["price_rub"] == 0:
                        import importlib

                        bot_module = importlib.import_module("bot")
                        record_free_key_usage = getattr(bot_module, "record_free_key_usage", None)
                        if record_free_key_usage:
                            record_free_key_usage(cursor, user_id, protocol, country)

                    result = await safe_send_message(
                        bot,
                        user_id,
                        format_key_message(key["accessUrl"]),
                        reply_markup=main_menu,
                        disable_web_page_preview=True,
                        parse_mode="Markdown",
                    )
                    if not result:
                        logging.warning(
                            "[AUTO-ISSUE] Не удалось отправить Outline ключ user_id=%s",
                            user_id,
                        )

                elif protocol == "v2ray":
                    try:
                        server_config = {
                            "api_url": server_dict["api_url"],
                            "api_key": server_dict.get("api_key"),
                        }
                        protocol_client = ProtocolFactory.create_protocol(protocol, server_config)

                        user_data = await protocol_client.create_user(email or f"user_{user_id}@veilbot.com")
                        if not isinstance(user_data, dict) or not user_data.get("uuid"):
                            raise ValueError("Invalid response from V2Ray server")

                        config = None
                        if user_data.get("client_config"):
                            config = user_data["client_config"]
                            if "vless://" in config:
                                lines = config.split("\n")
                                for line in lines:
                                    if line.strip().startswith("vless://"):
                                        config = line.strip()
                                        break
                        else:
                            config = await protocol_client.get_user_config(
                                user_data["uuid"],
                                {
                                    "domain": server_dict.get("domain") or "veil-bot.ru",
                                    "port": 443,
                                    "path": server_dict.get("v2ray_path") or "/v2ray",
                                    "email": email or f"user_{user_id}@veilbot.com",
                                },
                            )
                            if "vless://" in config:
                                lines = config.split("\n")
                                for line in lines:
                                    if line.strip().startswith("vless://"):
                                        config = line.strip()
                                        break

                        cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
                        if not cursor.fetchone():
                            logging.info(
                                "[AUTO-ISSUE] User %s not found in users table, creating...",
                                user_id,
                            )
                            with safe_foreign_keys_off(cursor):
                                cursor.execute(
                                    """
                                        INSERT OR REPLACE INTO users
                                        (user_id, username, first_name, last_name, created_at, last_active_at, blocked)
                                        VALUES (?, ?, ?, ?, ?, ?, 0)
                                    """,
                                    (user_id, None, None, None, int(time.time()), int(time.time())),
                                )

                        now = int(time.time())
                        expiry = now + tariff["duration_sec"]
                        with safe_foreign_keys_off(cursor):
                            traffic_limit_mb = 0
                            try:
                                traffic_limit_mb = int(tariff.get("traffic_limit_mb") or 0)
                            except (TypeError, ValueError):
                                traffic_limit_mb = 0
                            cursor.execute(
                                """
                                INSERT INTO v2ray_keys (
                                    server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id, client_config,
                                    traffic_limit_mb, traffic_usage_bytes
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                                """,
                                (
                                    server_dict["id"],
                                    user_id,
                                    user_data["uuid"],
                                    email or f"user_{user_id}@veilbot.com",
                                    now,
                                    expiry,
                                    tariff_id,
                                    config,
                                    traffic_limit_mb,
                                ),
                            )
                            new_key_id = cursor.lastrowid
                            if (traffic_limit_mb or 0) <= 0 and tariff_id:
                                try:
                                    cursor.execute(
                                        """
                                        UPDATE v2ray_keys
                                        SET traffic_limit_mb = COALESCE(
                                            (SELECT traffic_limit_mb FROM tariffs WHERE id = ?),
                                            0
                                        )
                                        WHERE id = ?
                                        """,
                                        (tariff_id, new_key_id),
                                    )
                                except Exception as update_error:
                                    logging.warning("[AUTO-ISSUE] Failed to backfill traffic_limit_mb for key %s: %s", new_key_id, update_error)
                                else:
                                    cursor.execute("SELECT traffic_limit_mb FROM v2ray_keys WHERE id = ?", (new_key_id,))
                                    row_limit = cursor.fetchone()
                                    if not row_limit or (row_limit[0] or 0) <= 0:
                                        logging.warning("[AUTO-ISSUE] traffic_limit_mb remains zero for key %s after backfill", new_key_id)

                        if tariff["price_rub"] == 0:
                            import importlib

                            bot_module = importlib.import_module("bot")
                            record_free_key_usage = getattr(bot_module, "record_free_key_usage", None)
                            if record_free_key_usage:
                                record_free_key_usage(cursor, user_id, protocol, country)

                        result = await safe_send_message(
                            bot,
                            user_id,
                            format_key_message_unified(config, protocol, tariff),
                            reply_markup=main_menu,
                            disable_web_page_preview=True,
                            parse_mode="Markdown",
                        )
                        if not result:
                            logging.warning(
                                "[AUTO-ISSUE] Не удалось отправить V2Ray ключ user_id=%s",
                                user_id,
                            )

                    except Exception as exc:  # noqa: BLE001
                        logging.error(
                            "[AUTO-ISSUE] Ошибка при создании V2Ray ключа для user_id=%s: %s",
                            user_id,
                            exc,
                        )
                        try:
                            if "user_data" in locals() and isinstance(user_data, dict) and user_data.get("uuid"):
                                await protocol_client.delete_user(user_data["uuid"])
                                logging.info(
                                    "[AUTO-ISSUE] Deleted V2Ray user %s from server due to error",
                                    user_data["uuid"],
                                )
                        except Exception as cleanup_error:  # noqa: BLE001
                            logging.error(
                                "[AUTO-ISSUE] Failed to cleanup V2Ray user after error: %s",
                                cleanup_error,
                            )
                        continue

                logging.info(
                    "[AUTO-ISSUE] Успешно создан ключ %s для user_id=%s, payment_id=%s",
                    protocol,
                    user_id,
                    payment_uuid,
                )

    async def job() -> None:
        if await _process_with_new_module():
            return
        await _process_fallback()

    await _run_periodic(
        "process_pending_paid_payments",
        interval_seconds=300,
        job=job,
        max_backoff=1800,
    )



async def auto_delete_expired_subscriptions() -> None:
    """Автоматическое удаление истекших подписок с grace period 24 часа."""
    
    GRACE_PERIOD = 86400  # 24 часа в секундах
    
    async def job() -> None:
        repo = SubscriptionRepository()
        with get_db_cursor(commit=True) as cursor:
            now = int(time.time())
            grace_threshold = now - GRACE_PERIOD
            
            # Получить истекшие подписки
            expired_subscriptions = repo.get_expired_subscriptions(grace_threshold)
            
            deleted_count = 0
            
            for subscription_id, user_id, token in expired_subscriptions:
                try:
                    # Получить все ключи подписки
                    subscription_keys = repo.get_subscription_keys_for_deletion(subscription_id)
                    
                    # Удалить ключи через V2Ray API
                    for v2ray_uuid, api_url, api_key in subscription_keys:
                        if v2ray_uuid and api_url and api_key:
                            protocol_client = None
                            try:
                                from vpn_protocols import V2RayProtocol
                                protocol_client = V2RayProtocol(api_url, api_key)
                                await protocol_client.delete_user(v2ray_uuid)
                                logging.info(
                                    "Successfully deleted V2Ray key %s for subscription %s",
                                    v2ray_uuid, subscription_id
                                )
                            except Exception as exc:
                                logging.warning(
                                    "Failed to delete V2Ray key %s for subscription %s: %s",
                                    v2ray_uuid, subscription_id, exc
                                )
                            finally:
                                if protocol_client:
                                    try:
                                        await protocol_client.close()
                                    except Exception:
                                        pass
                    
                    # Удалить ключи из БД напрямую через cursor (как для обычных ключей)
                    # Используем safe_foreign_keys_off для обхода проблем с foreign keys
                    try:
                        with safe_foreign_keys_off(cursor):
                            cursor.execute("DELETE FROM v2ray_keys WHERE subscription_id = ?", (subscription_id,))
                            deleted_keys_count = cursor.rowcount
                    except Exception as exc:
                        logging.warning("Error deleting subscription keys: %s", exc)
                        deleted_keys_count = 0
                    
                    # Удалить подписку из БД (аналогично удалению ключей)
                    # Используем safe_foreign_keys_off для обхода проблем с foreign keys
                    try:
                        with safe_foreign_keys_off(cursor):
                            cursor.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))
                    except Exception as exc:
                        logging.warning("Error deleting subscription from DB: %s", exc)
                        # Если не удалось удалить, хотя бы деактивируем
                        cursor.execute("UPDATE subscriptions SET is_active = 0 WHERE id = ?", (subscription_id,))
                    
                    # Инвалидировать кэш
                    invalidate_subscription_cache(token)
                    
                    deleted_count += 1
                    logging.info(
                        "Deleted expired subscription %s (user_id=%s, keys_count=%s)",
                        subscription_id, user_id, len(subscription_keys)
                    )
                except Exception as exc:
                    logging.error(
                        "Error deleting expired subscription %s: %s",
                        subscription_id, exc, exc_info=True
                    )
            
            if deleted_count > 0:
                logging.info("Deleted %s expired subscriptions (grace period 24h)", deleted_count)
        
        try:
            optimize_memory()
            log_memory_usage()
        except Exception as exc:
            logging.error("Ошибка при оптимизации памяти: %s", exc)
    
    await _run_periodic(
        "auto_delete_expired_subscriptions",
        interval_seconds=600,
        job=job,
        max_backoff=3600,
    )


async def monitor_subscription_traffic_limits() -> None:
    """Контроль превышения трафиковых лимитов для подписок V2Ray.
    
    Каждые 10 минут:
    1. Запрашивает трафик для всех активных ключей через GET /api/keys/{key_id}/traffic
    2. Перезаписывает traffic_usage_bytes абсолютным значением total_bytes
    3. Рассчитывает трафик подписок как сумму трафика всех ключей подписки
    4. Проверяет превышение лимитов только для подписок
    """
    
    TRAFFIC_NOTIFY_WARNING = 1
    TRAFFIC_NOTIFY_DISABLED = 2
    TRAFFIC_DISABLE_GRACE = 86400  # 24 часа
    
    async def _fetch_traffic_for_key(key_id: int, v2ray_uuid: str, server_id: int, api_url: str, api_key: str) -> Optional[int]:
        """Получить трафик для одного ключа через GET /api/keys/{key_id}/traffic"""
        try:
            config = {"api_url": api_url, "api_key": api_key}
            protocol = ProtocolFactory.create_protocol('v2ray', config)
            
            try:
                # Получаем информацию о ключе для получения API key_id
                key_info = await protocol.get_key_info(v2ray_uuid)
                api_key_id = key_info.get('id') or key_info.get('uuid')
                
                if not api_key_id:
                    logging.warning(f"[TRAFFIC] Cannot resolve API key_id for UUID {v2ray_uuid}")
                    return None
                
                # Получаем трафик через новый эндпоинт GET /api/keys/{key_id}/traffic
                stats = await protocol.get_key_traffic_stats(str(api_key_id))
                if not stats:
                    return None
                
                total_bytes = stats.get('total_bytes')
                if isinstance(total_bytes, (int, float)) and total_bytes >= 0:
                    return int(total_bytes)
                
                return None
            finally:
                await protocol.close()
        except Exception as e:
            logging.error(f"[TRAFFIC] Error fetching traffic for key {key_id} (UUID: {v2ray_uuid}): {e}", exc_info=True)
            return None
    
    async def _disable_subscription_keys(subscription_id: int) -> bool:
        """Отключить все ключи подписки через V2Ray API"""
        repo = SubscriptionRepository()
        keys = repo.get_subscription_keys_for_deletion(subscription_id)
        
        success_count = 0
        for v2ray_uuid, api_url, api_key in keys:
            if v2ray_uuid and api_url and api_key:
                protocol_client = None
                try:
                    from vpn_protocols import V2RayProtocol
                    protocol_client = V2RayProtocol(api_url, api_key)
                    result = await protocol_client.delete_user(v2ray_uuid)
                    if result:
                        success_count += 1
                        logging.info(
                            "[SUBSCRIPTION TRAFFIC] Successfully disabled V2Ray key %s for subscription %s",
                            v2ray_uuid, subscription_id
                        )
                except Exception as exc:
                    logging.warning(
                        "[SUBSCRIPTION TRAFFIC] Failed to disable V2Ray key %s for subscription %s: %s",
                        v2ray_uuid, subscription_id, exc
                    )
                finally:
                    if protocol_client:
                        try:
                            await protocol_client.close()
                        except Exception:
                            pass
        
        return success_count > 0
    
    async def job() -> None:
        now = int(time.time())
        repo = SubscriptionRepository()
        
        # Получить все активные (не истекшие) ключи V2Ray
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT 
                    k.id,
                    k.v2ray_uuid,
                    k.server_id,
                    k.subscription_id,
                    IFNULL(s.api_url, '') AS api_url,
                    IFNULL(s.api_key, '') AS api_key
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.expiry_at > ?
                  AND s.protocol = 'v2ray'
                  AND s.api_url IS NOT NULL
                  AND s.api_key IS NOT NULL
            """, (now,))
            active_keys = cursor.fetchall()
        
        if not active_keys:
            logging.debug("[TRAFFIC] No active V2Ray keys found")
            return
        
        logging.info(f"[TRAFFIC] Found {len(active_keys)} active V2Ray keys to update")
        
        # Запросить трафик для каждого ключа индивидуально через GET /api/keys/{key_id}/traffic
        # Создаем список задач с привязкой к key_id
        tasks_with_keys: list[tuple[int, asyncio.Task]] = []
        
        for key_row in active_keys:
            key_id, v2ray_uuid, server_id, subscription_id, api_url, api_key = key_row
            if not api_url or not api_key:
                logging.warning(
                    "[TRAFFIC] Missing API credentials for server %s, skipping key %s",
                    server_id, v2ray_uuid
                )
                continue
            
            task = _fetch_traffic_for_key(key_id, v2ray_uuid, server_id, api_url, api_key)
            tasks_with_keys.append((key_id, task))
        
        # Выполняем все запросы параллельно
        usage_map: Dict[int, Optional[int]] = {}
        if tasks_with_keys:
            logging.info(f"[TRAFFIC] Fetching traffic for {len(tasks_with_keys)} keys in parallel")
            tasks = [task for _, task in tasks_with_keys]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for i, result in enumerate(results):
                key_id = tasks_with_keys[i][0]
                if isinstance(result, Exception):
                    logging.error(f"[TRAFFIC] Error fetching traffic for key {key_id}: {result}", exc_info=True)
                    continue
                if result is not None:
                    usage_map[key_id] = result
        
        # Обновить traffic_usage_bytes в БД для всех ключей (перезаписать абсолютным значением)
        key_updates = []
        for key_id, usage_bytes in usage_map.items():
            if usage_bytes is not None:
                key_updates.append((usage_bytes, key_id))
        
        logging.info(f"[TRAFFIC] Updating traffic for {len(key_updates)} keys")
        if key_updates:
            with get_db_cursor(commit=True) as cursor:
                cursor.executemany(
                    "UPDATE v2ray_keys SET traffic_usage_bytes = ? WHERE id = ?",
                    key_updates
                )
                logging.info(f"[TRAFFIC] Updated traffic_usage_bytes for {len(key_updates)} keys")
        
        # Получить активные подписки с лимитами
        subscriptions = repo.get_subscriptions_with_traffic_limits(now)
        
        if not subscriptions:
            logging.debug("[TRAFFIC] No active subscriptions with traffic limits found")
            return
        
        # Проверить превышение лимитов только для подписок
        warn_notifications = []
        disable_notifications = []
        updates = []
        
        for sub in subscriptions:
            subscription_id, user_id, stored_usage, over_limit_at, notified_flags, expires_at, tariff_id, limit_mb, tariff_name = sub
            
            # Агрегировать трафик всех ключей подписки
            total_usage = repo.get_subscription_traffic_sum(subscription_id)
            
            # Обновить traffic_usage_bytes в подписке
            repo.update_subscription_traffic(subscription_id, total_usage)
            
            # Получить лимит из тарифа или индивидуального лимита подписки
            limit_bytes = repo.get_subscription_traffic_limit(subscription_id)
            
            # Проверить превышение лимита
            over_limit = limit_bytes > 0 and total_usage > limit_bytes
            
            new_over_limit_at = over_limit_at
            new_notified_flags = notified_flags or 0
            
            if over_limit:
                if not new_over_limit_at:
                    new_over_limit_at = now
                
                # Отправить предупреждение
                if not (new_notified_flags & TRAFFIC_NOTIFY_WARNING):
                    limit_display = _format_bytes_short(limit_bytes)
                    usage_display = _format_bytes_short(total_usage)
                    deadline_ts = (new_over_limit_at or now) + TRAFFIC_DISABLE_GRACE
                    remaining = max(0, deadline_ts - now)
                    
                    message = (
                        "⚠️ Превышен лимит трафика для вашей подписки V2Ray.\n"
                        f"Тариф: {tariff_name or 'V2Ray'}\n"
                        f"Израсходовано: {usage_display} из {limit_display}.\n"
                        f"Подписка будет отключена через {format_duration(remaining)}.\n"
                        "Продлите доступ, чтобы сбросить лимит."
                    )
                    warn_notifications.append((user_id, message))
                    new_notified_flags |= TRAFFIC_NOTIFY_WARNING
                
                # Отключить подписку после grace period
                should_disable = False
                if new_over_limit_at:
                    disable_deadline = new_over_limit_at + TRAFFIC_DISABLE_GRACE
                    should_disable = (
                        now >= disable_deadline
                        and not (new_notified_flags & TRAFFIC_NOTIFY_DISABLED)
                    )
                
                if should_disable:
                    disable_success = await _disable_subscription_keys(subscription_id)
                    if disable_success:
                        # Деактивировать подписку
                        repo.deactivate_subscription(subscription_id)
                        
                        limit_display = _format_bytes_short(limit_bytes)
                        usage_display = _format_bytes_short(total_usage)
                        message = (
                            "❌ Ваша подписка V2Ray отключена из-за превышения лимита трафика.\n"
                            f"Тариф: {tariff_name or 'V2Ray'}\n"
                            f"Израсходовано: {usage_display} из {limit_display}.\n"
                            "Продлите доступ, чтобы восстановить подписку."
                        )
                        disable_notifications.append((user_id, message))
                        new_notified_flags |= TRAFFIC_NOTIFY_DISABLED
            else:
                # Если лимит не превышен, сбрасываем флаги
                new_over_limit_at = None
                new_notified_flags = 0
            
            # Сохранить обновления
            updates.append((
                new_over_limit_at,
                new_notified_flags,
                subscription_id
            ))
        
        # Обновить БД
        if updates:
            with get_db_cursor(commit=True) as cursor:
                cursor.executemany("""
                    UPDATE subscriptions
                    SET traffic_over_limit_at = ?,
                        traffic_over_limit_notified = ?
                    WHERE id = ?
                """, updates)
        
        # Отправить уведомления
        bot = get_bot_instance()
        if bot:
            for user_id, message in warn_notifications + disable_notifications:
                await safe_send_message(
                    bot,
                    user_id,
                    message,
                    reply_markup=get_main_menu(user_id),
                    parse_mode="Markdown",
                )
        
        if warn_notifications or disable_notifications:
            logging.info(
                "[TRAFFIC] Sent %s warning and %s disable notifications",
                len(warn_notifications), len(disable_notifications)
            )
    
    await _run_periodic(
        "monitor_subscription_traffic_limits",
        interval_seconds=600,  # 10 минут
        job=job,
        max_backoff=3600,
    )


async def notify_expiring_subscriptions() -> None:
    """Уведомление пользователей об истекающих подписках."""
    
    async def job() -> None:
        bot = get_bot_instance()
        if not bot:
            logging.debug("Bot instance is not available for notify_expiring_subscriptions")
            return
        
        repo = SubscriptionRepository()
        updates = []
        notifications_to_send = []
        
        with get_db_cursor() as cursor:
            now = int(time.time())
            one_day = 86400
            one_hour = 3600
            ten_minutes = 600
            
            subscriptions = repo.get_expiring_subscriptions(now)
            
            for sub_id, user_id, token, expiry, created_at, notified in subscriptions:
                remaining_time = expiry - now
                if created_at is None:
                    logging.warning("Skipping subscription %s - created_at is None", sub_id)
                    continue
                
                original_duration = expiry - created_at
                ten_percent_threshold = int(original_duration * 0.1)
                message = None
                new_notified = notified
                
                if (
                    original_duration > one_day
                    and one_hour < remaining_time <= one_day
                    and (notified & 4) == 0
                ):
                    time_str = format_duration(remaining_time)
                    subscription_url = f"https://veil-bot.ru/api/subscription/{token}"
                    message = (
                        f"⏳ Ваша подписка V2Ray истечет через {time_str}\n\n"
                        f"🔗 `{subscription_url}`\n\n"
                        f"Продлите доступ:"
                    )
                    new_notified = notified | 4
                elif (
                    original_duration > one_hour
                    and ten_minutes < remaining_time <= (one_hour + 60)
                    and (notified & 2) == 0
                ):
                    time_str = format_duration(remaining_time)
                    subscription_url = f"https://veil-bot.ru/api/subscription/{token}"
                    message = (
                        f"⏳ Ваша подписка V2Ray истечет через {time_str}\n\n"
                        f"🔗 `{subscription_url}`\n\n"
                        f"Продлите доступ:"
                    )
                    new_notified = notified | 2
                elif remaining_time > 0 and remaining_time <= ten_minutes and (notified & 8) == 0:
                    time_str = format_duration(remaining_time)
                    subscription_url = f"https://veil-bot.ru/api/subscription/{token}"
                    message = (
                        f"⏳ Ваша подписка V2Ray истечет через {time_str}\n\n"
                        f"🔗 `{subscription_url}`\n\n"
                        f"Продлите доступ:"
                    )
                    new_notified = notified | 8
                elif remaining_time > 0 and remaining_time <= ten_percent_threshold and (notified & 1) == 0:
                    time_str = format_duration(remaining_time)
                    subscription_url = f"https://veil-bot.ru/api/subscription/{token}"
                    message = (
                        f"⏳ Ваша подписка V2Ray истечет через {time_str}\n\n"
                        f"🔗 `{subscription_url}`\n\n"
                        f"Продлите доступ:"
                    )
                    new_notified = notified | 1
                
                if message:
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    keyboard = InlineKeyboardMarkup()
                    keyboard.add(InlineKeyboardButton("🔁 Продлить подписку", callback_data="renew_subscription"))
                    notifications_to_send.append((user_id, message, keyboard))
                    updates.append((sub_id, new_notified))
        
        # Отправка уведомлений
        for user_id, message, keyboard in notifications_to_send:
            result = await safe_send_message(
                bot,
                user_id,
                message,
                reply_markup=keyboard,
                disable_web_page_preview=True,
                parse_mode="Markdown",
            )
            if result:
                logging.info("Sent expiry notification for subscription to user %s", user_id)
            else:
                logging.warning("Failed to deliver expiry notification to user %s", user_id)
        
        # Обновление БД
        if updates:
            for sub_id, notified in updates:
                repo.update_subscription_notified(sub_id, notified)
            logging.info("Updated %s subscriptions with expiry notifications", len(updates))
    
    await _run_periodic(
        "notify_expiring_subscriptions",
        interval_seconds=60,
        job=job,
        max_backoff=600,
    )


async def retry_failed_subscription_notifications() -> None:
    """
    Повторная отправка уведомлений о покупке подписки через process_subscription_purchase
    
    ИСПРАВЛЕНО: Теперь использует process_subscription_purchase вместо прямой отправки уведомлений.
    Это предотвращает дублирование уведомлений и обеспечивает единую логику обработки.
    """
    
    async def job() -> None:
        try:
            from payments.services.subscription_purchase_service import SubscriptionPurchaseService
            from payments.repositories.payment_repository import PaymentRepository
            from app.settings import settings as app_settings
            
            payment_repo = PaymentRepository(app_settings.DATABASE_PATH)
            subscription_service = SubscriptionPurchaseService()
            
            # Получаем оплаченные платежи за подписки со статусом paid (не completed)
            # Это означает, что подписка была создана/продлена, но уведомление не было отправлено
            paid_payments = await payment_repo.get_paid_payments_without_keys()
            
            subscription_payments = []
            for payment in paid_payments:
                # Проверяем, что это платеж за подписку
                if (payment.metadata and 
                    payment.metadata.get('key_type') == 'subscription' and 
                    payment.protocol == 'v2ray' and
                    payment.status.value == 'paid'):  # Только paid, не completed
                    subscription_payments.append(payment)
            
            if not subscription_payments:
                return
            
            logger.info(f"[RETRY] Found {len(subscription_payments)} subscription payments without notification")
            
            # Обрабатываем каждый платеж через process_subscription_purchase
            # Это гарантирует правильную логику и предотвращает дублирование
            for payment in subscription_payments:
                try:
                    # Проверяем, не обрабатывается ли уже этот платеж
                    if payment.metadata and payment.metadata.get('_processing_subscription'):
                        logger.debug(f"[RETRY] Payment {payment.payment_id} is already being processed, skipping")
                        continue
                    
                    # Проверяем возраст платежа - обрабатываем только старые (более 10 минут)
                    now = int(time.time())
                    if payment.paid_at:
                        payment_age = now - int(payment.paid_at.timestamp())
                        MIN_AGE_FOR_RETRY = 600  # 10 минут
                        if payment_age < MIN_AGE_FOR_RETRY:
                            logger.debug(
                                f"[RETRY] Skipping payment {payment.payment_id} - paid {payment_age}s ago "
                                f"(less than {MIN_AGE_FOR_RETRY}s, should be processed via normal flow)"
                            )
                            continue
                    
                    # КРИТИЧНО: Проверяем статус платежа ПЕРЕД обработкой (защита от race condition)
                    # Статус мог измениться на completed между получением списка и этой проверкой
                    fresh_payment = await payment_repo.get_by_payment_id(payment.payment_id)
                    if not fresh_payment:
                        logger.warning(f"[RETRY] Payment {payment.payment_id} not found, skipping")
                        continue
                    
                    if fresh_payment.status.value != 'paid':
                        logger.info(
                            f"[RETRY] Payment {payment.payment_id} status is {fresh_payment.status.value} (not paid), skipping. "
                            f"Payment was likely processed by another process."
                        )
                        continue
                    
                    logger.info(f"[RETRY] Retrying subscription purchase for payment {payment.payment_id}, user {payment.user_id}")
                    
                    # Используем process_subscription_purchase для обработки
                    # Он сам проверит статус, создаст/продлит подписку и отправит уведомление
                    success, error_msg = await subscription_service.process_subscription_purchase(payment.payment_id)
                    
                    if success:
                        logger.info(f"[RETRY] Successfully processed subscription purchase for payment {payment.payment_id}")
                    else:
                        logger.warning(
                            f"[RETRY] Failed to process subscription purchase for payment {payment.payment_id}: {error_msg}"
                        )
                    
                    # Небольшая задержка между обработкой
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    logger.error(
                        f"[RETRY] Error retrying subscription purchase for payment {payment.payment_id}: {e}",
                        exc_info=True
                    )
                    continue
                    
        except Exception as e:
            logger.error(f"[RETRY] Error in retry_failed_subscription_notifications: {e}", exc_info=True)
    
    await _run_periodic(
        "retry_failed_subscription_notifications",
        interval_seconds=300,  # Каждые 5 минут
        job=job,
        max_backoff=1800,
    )


async def create_keys_for_new_server(server_id: int) -> None:
    """
    Создать ключи на новом сервере для всех активных подписок
    
    Args:
        server_id: ID нового сервера
    """
    logger.info(f"Starting to create keys for new server {server_id} for all active subscriptions")
    
    try:
        # Получить информацию о сервере
        from app.repositories.server_repository import ServerRepository
        repo = ServerRepository()
        server = repo.get_server(server_id)
        
        if not server:
            logger.warning(f"Server {server_id} not found")
            return
        
        server_id_db, name, api_url, cert_sha256, max_keys, active, country, protocol, domain, api_key, v2ray_path, available_for_purchase = server
        
        if protocol != 'v2ray' or not active:
            logger.info(f"Server {server_id} is not an active V2Ray server, skipping")
            return
        
        # Получить все активные подписки
        subscription_repo = SubscriptionRepository()
        now = int(time.time())
        
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT id, user_id, subscription_token, expires_at, tariff_id
                FROM subscriptions
                WHERE is_active = 1 AND expires_at > ?
            """, (now,))
            active_subscriptions = cursor.fetchall()
        
        if not active_subscriptions:
            logger.info(f"No active subscriptions found for server {server_id}")
            return
        
        logger.info(f"Found {len(active_subscriptions)} active subscriptions for server {server_id}")
        
        created_count = 0
        failed_count = 0
        
        # Для каждой подписки
        for subscription_id, user_id, token, expires_at, tariff_id in active_subscriptions:
            try:
                # Проверить, что у пользователя еще нет ключа на этом сервере для этой подписки
                with get_db_cursor() as cursor:
                    cursor.execute("""
                        SELECT id FROM v2ray_keys
                        WHERE server_id = ? AND user_id = ? AND subscription_id = ?
                    """, (server_id, user_id, subscription_id))
                    existing_key = cursor.fetchone()
                
                if existing_key:
                    logger.debug(f"Key already exists for subscription {subscription_id} on server {server_id}")
                    continue
                
                # Генерация email для ключа
                key_email = f"{user_id}_subscription_{subscription_id}@veilbot.com"
                
                # Создать ключ на новом сервере
                server_config = {
                    'api_url': api_url,
                    'api_key': api_key,
                    'domain': domain,
                }
                
                from vpn_protocols import ProtocolFactory
                protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                # Передаем название сервера вместо email для name в V2Ray API
                user_data = await protocol_client.create_user(key_email, name=name)
                
                if not user_data or not user_data.get('uuid'):
                    raise Exception("Failed to create user on V2Ray server")
                
                v2ray_uuid = user_data['uuid']
                
                # Получить client_config
                client_config = await protocol_client.get_user_config(
                    v2ray_uuid,
                    {
                        'domain': domain or 'veil-bot.ru',
                        'port': 443,
                        'email': key_email,
                    }
                )
                
                # Извлекаем VLESS URL из конфигурации
                if 'vless://' in client_config:
                    lines = client_config.split('\n')
                    for line in lines:
                        if line.strip().startswith('vless://'):
                            client_config = line.strip()
                            break
                
                # Сохранить ключ в БД
                # Временно отключаем проверку внешних ключей из-за несоответствия структуры users(id) vs users(user_id)
                with get_db_cursor(commit=True) as cursor:
                    cursor.connection.execute("PRAGMA foreign_keys = OFF")
                    try:
                        cursor.execute("""
                            INSERT INTO v2ray_keys 
                            (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id, client_config, subscription_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            server_id,
                            user_id,
                            v2ray_uuid,
                            key_email,
                            int(time.time()),
                            expires_at,
                            tariff_id,
                            client_config,
                            subscription_id,
                        ))
                    finally:
                        cursor.connection.execute("PRAGMA foreign_keys = ON")
                
                # Инвалидировать кэш подписки
                invalidate_subscription_cache(token)
                
                created_count += 1
                logger.info(
                    f"Created key for subscription {subscription_id} on server {server_id} "
                    f"(user_id={user_id})"
                )
                
            except Exception as e:
                failed_count += 1
                logger.error(
                    f"Failed to create key for subscription {subscription_id} on server {server_id}: {e}",
                    exc_info=True
                )
                # Продолжаем создание ключей для других подписок
                continue
        
        logger.info(
            f"Finished creating keys for server {server_id}: "
            f"{created_count} created, {failed_count} failed"
        )
        
    except Exception as e:
        logger.error(
            f"Error in create_keys_for_new_server for server {server_id}: {e}",
            exc_info=True
        )


async def check_and_create_keys_for_new_servers() -> None:
    """
    Периодическая проверка наличия новых серверов без ключей для активных подписок
    Запускается каждые 15 минут
    """
    async def job() -> None:
        try:
            from app.repositories.server_repository import ServerRepository
            from app.repositories.subscription_repository import SubscriptionRepository
            
            server_repo = ServerRepository()
            subscription_repo = SubscriptionRepository()
            now = int(time.time())
            
            # Получаем все активные V2Ray серверы
            with get_db_cursor() as cursor:
                cursor.execute("""
                    SELECT id, name, api_url, api_key, domain, v2ray_path, available_for_purchase
                    FROM servers
                    WHERE protocol = 'v2ray' AND active = 1 AND available_for_purchase = 1
                """)
                active_v2ray_servers = cursor.fetchall()
            
            if not active_v2ray_servers:
                logger.debug("No active V2Ray servers found for periodic check")
                return
            
            # Получаем все активные подписки
            with get_db_cursor() as cursor:
                cursor.execute("""
                    SELECT id, user_id, subscription_token, expires_at, tariff_id
                    FROM subscriptions
                    WHERE is_active = 1 AND expires_at > ?
                """, (now,))
                active_subscriptions = cursor.fetchall()
            
            if not active_subscriptions:
                logger.debug("No active subscriptions found for periodic check")
                return
            
            logger.info(
                f"Periodic check: Found {len(active_v2ray_servers)} active V2Ray servers "
                f"and {len(active_subscriptions)} active subscriptions"
            )
            
            total_created = 0
            total_failed = 0
            
            # Для каждого сервера проверяем наличие ключей для всех подписок
            for server_id, name, api_url, api_key, domain, v2ray_path, available_for_purchase in active_v2ray_servers:
                if not available_for_purchase:
                    continue
                
                for subscription_id, user_id, token, expires_at, tariff_id in active_subscriptions:
                    try:
                        # Проверяем, есть ли уже ключ для этой подписки на этом сервере
                        with get_db_cursor() as cursor:
                            cursor.execute("""
                                SELECT id FROM v2ray_keys
                                WHERE server_id = ? AND user_id = ? AND subscription_id = ?
                            """, (server_id, user_id, subscription_id))
                            existing_key = cursor.fetchone()
                        
                        if existing_key:
                            continue  # Ключ уже существует
                        
                        # Создаем ключ
                        logger.info(
                            f"Periodic check: Creating missing key for subscription {subscription_id} "
                            f"on server {server_id} (user_id={user_id})"
                        )
                        
                        # Генерация email для ключа
                        key_email = f"{user_id}_subscription_{subscription_id}@veilbot.com"
                        
                        # Создать ключ на сервере
                        server_config = {
                            'api_url': api_url,
                            'api_key': api_key,
                            'domain': domain,
                        }
                        
                        from vpn_protocols import ProtocolFactory
                        protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                        # Передаем название сервера вместо email для name в V2Ray API
                        user_data = await protocol_client.create_user(key_email, name=name)
                        
                        if not user_data or not user_data.get('uuid'):
                            raise Exception("Failed to create user on V2Ray server")
                        
                        v2ray_uuid = user_data['uuid']
                        
                        # Получить client_config
                        client_config = await protocol_client.get_user_config(
                            v2ray_uuid,
                            {
                                'domain': domain or 'veil-bot.ru',
                                'port': 443,
                                'email': key_email,
                            }
                        )
                        
                        # Извлекаем VLESS URL из конфигурации
                        if 'vless://' in client_config:
                            lines = client_config.split('\n')
                            for line in lines:
                                if line.strip().startswith('vless://'):
                                    client_config = line.strip()
                                    break
                        
                        # Сохранить ключ в БД
                        # Временно отключаем проверку внешних ключей из-за несоответствия структуры users(id) vs users(user_id)
                        with get_db_cursor(commit=True) as cursor:
                            cursor.connection.execute("PRAGMA foreign_keys = OFF")
                            try:
                                cursor.execute("""
                                    INSERT INTO v2ray_keys 
                                    (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id, client_config, subscription_id)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (
                                    server_id,
                                    user_id,
                                    v2ray_uuid,
                                    key_email,
                                    now,
                                    expires_at,
                                    tariff_id,
                                    client_config,
                                    subscription_id,
                                ))
                            finally:
                                cursor.connection.execute("PRAGMA foreign_keys = ON")
                        
                        # Инвалидировать кэш подписки
                        from bot.services.subscription_service import invalidate_subscription_cache
                        invalidate_subscription_cache(token)
                        
                        total_created += 1
                        logger.info(
                            f"Periodic check: Created key for subscription {subscription_id} "
                            f"on server {server_id}"
                        )
                        
                    except Exception as e:
                        total_failed += 1
                        logger.error(
                            f"Periodic check: Failed to create key for subscription {subscription_id} "
                            f"on server {server_id}: {e}",
                            exc_info=True
                        )
                        continue
            
            if total_created > 0 or total_failed > 0:
                logger.info(
                    f"Periodic check completed: {total_created} keys created, {total_failed} failed"
                )
        
        except Exception as e:
            logger.error(f"Error in periodic check for new servers: {e}", exc_info=True)
    
    await _run_periodic(
        "check_and_create_keys_for_new_servers",
        interval_seconds=900,  # 15 минут
        job=job,
        max_backoff=3600,
    )


async def sync_subscription_keys_with_active_servers() -> None:
    """
    Синхронизация ключей подписок с активными серверами.
    Удаляет ключи на неактивных серверах и создает недостающие на активных.
    Запускается каждые 30 минут.
    """
    async def job() -> None:
        try:
            now = int(time.time())
            subscription_repo = SubscriptionRepository()
            
            # Получаем все активные подписки
            with get_db_cursor() as cursor:
                cursor.execute("""
                    SELECT id, user_id, subscription_token, expires_at, tariff_id
                    FROM subscriptions
                    WHERE is_active = 1 AND expires_at > ?
                """, (now,))
                active_subscriptions = cursor.fetchall()
            
            if not active_subscriptions:
                logger.debug("No active subscriptions found for sync")
                return
            
            # Получаем все активные V2Ray серверы
            with get_db_cursor() as cursor:
                cursor.execute("""
                    SELECT id, name, api_url, api_key, domain, v2ray_path
                    FROM servers
                    WHERE protocol = 'v2ray' AND active = 1
                    ORDER BY id
                """)
                active_servers = cursor.fetchall()
            
            if not active_servers:
                logger.debug("No active V2Ray servers found for sync")
                return
            
            active_server_ids = {server[0] for server in active_servers}
            active_servers_dict = {server[0]: server for server in active_servers}
            
            total_created = 0
            total_deleted = 0
            total_failed_create = 0
            total_failed_delete = 0
            
            logger.info(
                f"Starting sync: {len(active_subscriptions)} subscriptions, "
                f"{len(active_servers)} active servers"
            )
            
            for subscription_id, user_id, token, expires_at, tariff_id in active_subscriptions:
                try:
                    # Получаем существующие ключи подписки
                    with get_db_cursor() as cursor:
                        cursor.execute("""
                            SELECT k.id, k.server_id, k.v2ray_uuid, s.api_url, s.api_key
                            FROM v2ray_keys k
                            JOIN servers s ON k.server_id = s.id
                            WHERE k.subscription_id = ?
                        """, (subscription_id,))
                        existing_keys = cursor.fetchall()
                    
                    existing_server_ids = {key[1] for key in existing_keys}
                    
                    # Определяем серверы, где нужно создать ключи
                    servers_to_create = active_server_ids - existing_server_ids
                    
                    # Определяем ключи на неактивных серверах, которые нужно удалить
                    keys_to_delete = [
                        key for key in existing_keys
                        if key[1] not in active_server_ids
                    ]
                    
                    # Создаем недостающие ключи
                    for server_id in servers_to_create:
                        if server_id not in active_servers_dict:
                            continue
                        
                        server_id_db, name, api_url, api_key, domain, v2ray_path = active_servers_dict[server_id]
                        
                        try:
                            key_email = f"{user_id}_subscription_{subscription_id}@veilbot.com"
                            server_config = {
                                'api_url': api_url,
                                'api_key': api_key,
                                'domain': domain,
                            }
                            
                            protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                            user_data = await protocol_client.create_user(key_email, name=name)
                            
                            if not user_data or not user_data.get('uuid'):
                                raise Exception("Failed to create user on V2Ray server")
                            
                            v2ray_uuid = user_data['uuid']
                            
                            # Получаем client_config
                            client_config = await protocol_client.get_user_config(
                                v2ray_uuid,
                                {
                                    'domain': domain or 'veil-bot.ru',
                                    'port': 443,
                                    'email': key_email,
                                }
                            )
                            
                            # Извлекаем VLESS URL из конфигурации
                            if 'vless://' in client_config:
                                lines = client_config.split('\n')
                                for line in lines:
                                    if line.strip().startswith('vless://'):
                                        client_config = line.strip()
                                        break
                            
                            # Сохраняем ключ в БД
                            with get_db_cursor(commit=True) as cursor:
                                cursor.connection.execute("PRAGMA foreign_keys = OFF")
                                try:
                                    cursor.execute("""
                                        INSERT INTO v2ray_keys 
                                        (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id, client_config, subscription_id)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """, (
                                        server_id,
                                        user_id,
                                        v2ray_uuid,
                                        key_email,
                                        now,
                                        expires_at,
                                        tariff_id,
                                        client_config,
                                        subscription_id,
                                    ))
                                finally:
                                    cursor.connection.execute("PRAGMA foreign_keys = ON")
                            
                            invalidate_subscription_cache(token)
                            total_created += 1
                            logger.info(
                                f"Sync: Created key for subscription {subscription_id} "
                                f"on server {server_id}"
                            )
                            await protocol_client.close()
                            
                        except Exception as e:
                            total_failed_create += 1
                            logger.error(
                                f"Sync: Failed to create key for subscription {subscription_id} "
                                f"on server {server_id}: {e}",
                                exc_info=True
                            )
                            continue
                    
                    # Удаляем ключи на неактивных серверах
                    for key_id, server_id, v2ray_uuid, api_url, api_key in keys_to_delete:
                        try:
                            # Удаляем через V2Ray API
                            if api_url and api_key and v2ray_uuid:
                                server_config = {
                                    'api_url': api_url,
                                    'api_key': api_key,
                                    'domain': '',
                                }
                                protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                                try:
                                    await protocol_client.delete_user(v2ray_uuid)
                                    logger.debug(
                                        f"Sync: Deleted key {v2ray_uuid[:8]}... "
                                        f"from server {server_id} via API"
                                    )
                                except Exception as api_error:
                                    logger.warning(
                                        f"Sync: Failed to delete key {v2ray_uuid[:8]}... "
                                        f"from server {server_id} via API: {api_error}"
                                    )
                                finally:
                                    await protocol_client.close()
                            
                            # Удаляем из БД
                            with get_db_cursor(commit=True) as cursor:
                                with safe_foreign_keys_off(cursor):
                                    cursor.execute(
                                        "DELETE FROM v2ray_keys WHERE id = ?",
                                        (key_id,)
                                    )
                            
                            invalidate_subscription_cache(token)
                            total_deleted += 1
                            logger.info(
                                f"Sync: Deleted key {key_id} for subscription {subscription_id} "
                                f"from inactive server {server_id}"
                            )
                            
                        except Exception as e:
                            total_failed_delete += 1
                            logger.error(
                                f"Sync: Failed to delete key {key_id} for subscription {subscription_id} "
                                f"from server {server_id}: {e}",
                                exc_info=True
                            )
                            continue
                
                except Exception as e:
                    logger.error(
                        f"Sync: Error processing subscription {subscription_id}: {e}",
                        exc_info=True
                    )
                    continue
            
            if total_created > 0 or total_deleted > 0 or total_failed_create > 0 or total_failed_delete > 0:
                logger.info(
                    f"Sync completed: {total_created} created, {total_deleted} deleted, "
                    f"{total_failed_create} failed to create, {total_failed_delete} failed to delete"
                )
        
        except Exception as e:
            logger.error(f"Error in sync_subscription_keys_with_active_servers: {e}", exc_info=True)
    
    await _run_periodic(
        "sync_subscription_keys_with_active_servers",
        interval_seconds=1800,  # 30 минут
        job=job,
        max_backoff=3600,
    )
