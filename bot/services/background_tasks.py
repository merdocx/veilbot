"""
Модуль для фоновых задач бота
Вынесен из bot.py для улучшения поддерживаемости
"""
import asyncio
import time
import logging
import sqlite3
from collections import defaultdict
from typing import Optional, Callable, Awaitable, Dict, Any, List, Tuple, Set
from datetime import datetime

from app.infra.sqlite_utils import get_db_cursor, retry_db_operation
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
                JOIN subscriptions sub ON k.subscription_id = sub.id
                LEFT JOIN users u ON sub.user_id = u.user_id
                WHERE sub.expires_at <= ?
                  AND COALESCE(u.is_vip, 0) = 0
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
                cursor.execute("""
                    DELETE FROM keys WHERE id IN (
                        SELECT k.id FROM keys k
                        JOIN subscriptions sub ON k.subscription_id = sub.id
                        LEFT JOIN users u ON sub.user_id = u.user_id
                        WHERE sub.expires_at <= ?
                          AND COALESCE(u.is_vip, 0) = 0
                    )
                """, (grace_threshold,))
                outline_deleted = cursor.rowcount

            cursor.execute(
                """
                SELECT k.id, k.v2ray_uuid, s.api_url, s.api_key
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                JOIN subscriptions sub ON k.subscription_id = sub.id
                LEFT JOIN users u ON sub.user_id = u.user_id
                WHERE sub.expires_at <= ?
                  AND COALESCE(u.is_vip, 0) = 0
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
                    cursor.execute("""
                        DELETE FROM v2ray_keys WHERE id IN (
                            SELECT k.id FROM v2ray_keys k
                            JOIN subscriptions sub ON k.subscription_id = sub.id
                            LEFT JOIN users u ON sub.user_id = u.user_id
                            WHERE sub.expires_at <= ?
                              AND COALESCE(u.is_vip, 0) = 0
                        )
                    """, (grace_threshold,))
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
        interval_seconds=3600,  # 1 час
        job=job,
        max_backoff=10800,  # до 3 часов при повторяющихся ошибках
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
            cursor.execute("""
                SELECT COUNT(*) FROM keys k
                JOIN subscriptions s ON k.subscription_id = s.id
                WHERE s.expires_at > ?
            """, (now,))
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
        interval_seconds=600,  # 10 минут
        job=job,
        max_backoff=3600,  # до 1 часа при повторяющихся ошибках
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
                        SELECT k.user_id FROM keys k
                        JOIN subscriptions s ON k.subscription_id = s.id
                        WHERE s.expires_at > ?
                        UNION
                        SELECT k.user_id FROM v2ray_keys k
                        JOIN subscriptions s ON k.subscription_id = s.id
                        WHERE s.expires_at > ?
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
                        from payments.repositories.payment_repository import PaymentRepository
                        from payments.models.payment import PaymentStatus
                        from app.settings import settings as app_settings
                        
                        # КРИТИЧНО: Атомарная проверка статуса платежа через репозиторий
                        # Это предотвращает race condition, когда между проверкой и обработкой другой процесс уже обработал платеж
                        payment_repo = PaymentRepository(app_settings.DATABASE_PATH)
                        fresh_payment = await payment_repo.get_by_payment_id(payment_uuid)
                        
                        if not fresh_payment:
                            logging.warning(f"[AUTO-ISSUE] Payment {payment_uuid} not found, skipping")
                            continue
                        
                        # Если платеж уже обработан, пропускаем
                        if fresh_payment.status == PaymentStatus.COMPLETED:
                            logging.info(
                                f"[AUTO-ISSUE] Payment {payment_uuid} already completed, skipping subscription processing. "
                                f"Payment was likely processed by webhook or another process."
                            )
                            continue
                        
                        # Если статус не PAID, пропускаем (возможно, платеж еще не оплачен или уже обработан)
                        if fresh_payment.status != PaymentStatus.PAID:
                            logging.debug(
                                f"[AUTO-ISSUE] Payment {payment_uuid} status is {fresh_payment.status.value} (not paid), skipping"
                            )
                            continue
                        
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
                    # ВАЖНО: expiry_at удалено из таблицы keys - срок действия берется из subscriptions
                    # Этот код создает standalone ключи (без subscription_id), что не соответствует новой архитектуре
                    # TODO: Переделать на создание через SubscriptionService
                    cursor.execute(
                        "INSERT INTO keys (server_id, user_id, access_url, traffic_limit_mb, notified, key_id, created_at, email, tariff_id, protocol) "
                        "VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
                        (
                            server_dict["id"],
                            user_id,
                            key["accessUrl"],
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



async def cleanup_expired_payments() -> None:
    """
    Периодическая очистка истекших платежей:
    перевод старых pending платежей в статус expired.
    """

    async def job() -> None:
        try:
            # Используем новый платежный модуль через memory_optimizer (как в других задачах)
            from memory_optimizer import get_payment_service

            payment_service = get_payment_service()
            if not payment_service:
                logging.debug("Payment service is not available for cleanup_expired_payments")
                return

            # 24 часа по умолчанию — соответствует смыслу истечения платежной ссылки
            cleaned_count = await payment_service.cleanup_expired_payments(hours=24)
            if cleaned_count:
                logging.info(
                    "[PAYMENTS] cleanup_expired_payments marked %s payments as expired",
                    cleaned_count,
                )
        except Exception as exc:  # noqa: BLE001
            logging.error("Error in cleanup_expired_payments job: %s", exc, exc_info=True)

    await _run_periodic(
        "cleanup_expired_payments",
        interval_seconds=3600,  # раз в час
        job=job,
        max_backoff=21600,  # до 6 часов при повторяющихся ошибках
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
                    
                    # Удалить ключи через API (V2Ray и Outline)
                    from vpn_protocols import ProtocolFactory
                    for key_data in subscription_keys:
                        if len(key_data) < 4:
                            continue
                        key_id, api_url, auth_data, protocol = key_data[0], key_data[1], key_data[2], key_data[3]
                        
                        if not key_id or not api_url or not auth_data:
                            continue
                        
                        protocol_client = None
                        try:
                            if protocol == 'v2ray':
                                server_config = {'api_url': api_url, 'api_key': auth_data}
                                protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                                await protocol_client.delete_user(key_id)
                                logging.info(
                                    "Successfully deleted V2Ray key %s for subscription %s",
                                    key_id, subscription_id
                                )
                            elif protocol == 'outline':
                                server_config = {'api_url': api_url, 'cert_sha256': auth_data}
                                protocol_client = ProtocolFactory.create_protocol('outline', server_config)
                                await protocol_client.delete_user(key_id)
                                logging.info(
                                    "Successfully deleted Outline key %s for subscription %s",
                                    key_id, subscription_id
                                )
                        except Exception as exc:
                            logging.warning(
                                "Failed to delete %s key %s for subscription %s: %s",
                                protocol, key_id, subscription_id, exc
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
                            v2ray_deleted = cursor.rowcount
                            cursor.execute("DELETE FROM keys WHERE subscription_id = ?", (subscription_id,))
                            outline_deleted = cursor.rowcount
                            deleted_keys_count = v2ray_deleted + outline_deleted
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
        for key_identifier, api_url, api_key, protocol in keys:
            # Обрабатываем только V2Ray ключи
            if protocol != 'v2ray':
                continue
            v2ray_uuid = key_identifier
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
        
        # ОПТИМИЗАЦИЯ: Разделяем чтение и запись
        # Шаг 1: Читаем все необходимые данные из БД (без долгих операций между чтениями)
        active_keys = []
        subscriptions = []
        traffic_sums = {}
        
        # Получить все активные (не истекшие) ключи V2Ray
        def read_active_keys():
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
                    JOIN subscriptions sub ON k.subscription_id = sub.id
                    WHERE sub.expires_at > ?
                      AND s.protocol = 'v2ray'
                      AND s.api_url IS NOT NULL
                      AND s.api_key IS NOT NULL
                """, (now,))
                return cursor.fetchall()
        
        active_keys = retry_db_operation(read_active_keys, max_attempts=3)
        
        if not active_keys:
            logging.debug("[TRAFFIC] No active V2Ray keys found")
            return
        
        logging.info(f"[TRAFFIC] Found {len(active_keys)} active V2Ray keys to update")
        
        # Получить активные подписки с лимитами (читаем сразу после ключей)
        subscriptions = repo.get_subscriptions_with_traffic_limits(now)
        
        if subscriptions:
            # Оптимизация: получить все суммы трафика одним batch-запросом
            subscription_ids = [sub[0] for sub in subscriptions]
            traffic_sums = repo.get_all_subscriptions_traffic_sum(subscription_ids)
        
        # Шаг 2: Выполняем долгие операции с API (БД уже закрыта, блокировок нет)
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
        
        # Шаг 3: Выполняем все записи в БД (одна транзакция для всех обновлений ключей)
        key_updates = []
        for key_id, usage_bytes in usage_map.items():
            if usage_bytes is not None:
                key_updates.append((usage_bytes, key_id))
        
        logging.info(f"[TRAFFIC] Updating traffic for {len(key_updates)} keys")
        if key_updates:
            def update_keys_traffic():
                with get_db_cursor(commit=True) as cursor:
                    cursor.executemany(
                        "UPDATE v2ray_keys SET traffic_usage_bytes = ? WHERE id = ?",
                        key_updates
                    )
            
            retry_db_operation(update_keys_traffic, max_attempts=3)
            logging.info(f"[TRAFFIC] Updated traffic_usage_bytes for {len(key_updates)} keys")
        
        if not subscriptions:
            logging.debug("[TRAFFIC] No active subscriptions with traffic limits found")
            return
        
        # Проверить превышение лимитов только для подписок
        warn_notifications = []
        disable_notifications = []
        updates = []
        traffic_updates = []  # Для batch-обновления трафика
        
        for sub in subscriptions:
            subscription_id, user_id, stored_usage, over_limit_at, notified_flags, expires_at, tariff_id, limit_mb, tariff_name = sub
            
            # Получить трафик из batch-результата
            total_usage = traffic_sums.get(subscription_id, 0)
            
            # Добавить в список для batch-обновления
            traffic_updates.append((subscription_id, total_usage))
            
            # Вычислить лимит в байтах (limit_mb уже есть в данных подписки)
            limit_bytes = int(limit_mb) * 1024 * 1024 if limit_mb else 0
            
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
                        "⚠️ Превышен лимит трафика для вашей подписки.\n"
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
                        # Деактивировать подписку (с retry)
                        def deactivate_sub():
                            repo.deactivate_subscription(subscription_id)
                        retry_db_operation(deactivate_sub, max_attempts=3)
                        
                        limit_display = _format_bytes_short(limit_bytes)
                        usage_display = _format_bytes_short(total_usage)
                        message = (
                            "❌ Ваша подписка отключена из-за превышения лимита трафика.\n"
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
        
        # Batch-обновление трафика всех подписок одним запросом (с retry)
        if traffic_updates:
            def update_subscriptions_traffic():
                repo.batch_update_subscriptions_traffic(traffic_updates)
            
            retry_db_operation(update_subscriptions_traffic, max_attempts=3)
            logging.info(f"[TRAFFIC] Batch-updated traffic for {len(traffic_updates)} subscriptions")
        
        # Batch-обновление флагов подписок (с retry)
        if updates:
            def update_subscriptions_flags():
                with get_db_cursor(commit=True) as cursor:
                    cursor.executemany("""
                        UPDATE subscriptions
                        SET traffic_over_limit_at = ?,
                            traffic_over_limit_notified = ?
                        WHERE id = ?
                    """, updates)
            
            retry_db_operation(update_subscriptions_flags, max_attempts=3)
        
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
        
        now = int(time.time())
        one_day = 86400
        one_hour = 3600
        ten_minutes = 600
        
        # Читаем подписки с повторными попытками, чтобы избежать падений при временных блокировках БД
        def _load_subscriptions() -> List[Tuple]:
            return repo.get_expiring_subscriptions(now)
        subscriptions = retry_db_operation(_load_subscriptions, max_attempts=5, initial_delay=0.2)
        
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
                    f"⏳ Ваша подписка истечет через {time_str}\n\n"
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
                    f"⏳ Ваша подписка истечет через {time_str}\n\n"
                    f"🔗 `{subscription_url}`\n\n"
                    f"Продлите доступ:"
                )
                new_notified = notified | 2
            elif remaining_time > 0 and remaining_time <= ten_minutes and (notified & 8) == 0:
                time_str = format_duration(remaining_time)
                subscription_url = f"https://veil-bot.ru/api/subscription/{token}"
                message = (
                    f"⏳ Ваша подписка истечет через {time_str}\n\n"
                    f"🔗 `{subscription_url}`\n\n"
                    f"Продлите доступ:"
                )
                new_notified = notified | 8
            elif remaining_time > 0 and remaining_time <= ten_percent_threshold and (notified & 1) == 0:
                time_str = format_duration(remaining_time)
                subscription_url = f"https://veil-bot.ru/api/subscription/{token}"
                message = (
                    f"⏳ Ваша подписка истечет через {time_str}\n\n"
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
            def _apply_updates() -> None:
                for sub_id, notified in updates:
                    repo.update_subscription_notified(sub_id, notified)
            retry_db_operation(_apply_updates, max_attempts=5, initial_delay=0.2)
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
                    # КРИТИЧНО: Получаем свежий статус платежа из БД для атомарной проверки
                    fresh_payment = await payment_repo.get_by_payment_id(payment.payment_id)
                    if not fresh_payment:
                        logger.warning(f"[RETRY] Payment {payment.payment_id} not found, skipping")
                        continue
                    
                    # КРИТИЧНО: Проверяем статус через enum для надежности
                    from payments.models.payment import PaymentStatus
                    if fresh_payment.status == PaymentStatus.COMPLETED:
                        logger.info(
                            f"[RETRY] Payment {payment.payment_id} already completed, skipping. "
                            f"Payment was likely processed by webhook or another process."
                        )
                        continue
                    
                    if fresh_payment.status != PaymentStatus.PAID:
                        logger.info(
                            f"[RETRY] Payment {payment.payment_id} status is {fresh_payment.status.value} (not paid), skipping. "
                            f"Payment was likely processed by another process."
                        )
                        continue
                    
                    logger.info(f"[RETRY] Retrying subscription purchase for payment {payment.payment_id}, user {payment.user_id}")
                    
                    # Используем process_subscription_purchase для обработки
                    # Он сам проверит статус, создаст/продлит подписку и отправит уведомление
                    # Внутри process_subscription_purchase есть дополнительная защита от повторной обработки
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


async def fix_payments_without_subscription_id() -> None:
    """
    Мониторинг и исправление платежей без subscription_id
    
    Эта задача периодически проверяет completed платежи за подписки, которые не имеют subscription_id,
    и пытается исправить это, привязывая платежи к соответствующим подпискам.
    
    Это решает проблему, когда update_subscription_id завершился с ошибкой или был вызван
    до добавления этого вызова в код.
    """
    
    async def job() -> None:
        try:
            from payments.repositories.payment_repository import PaymentRepository
            from app.repositories.subscription_repository import SubscriptionRepository
            from app.settings import settings as app_settings
            from payments.models.payment import PaymentStatus
            
            payment_repo = PaymentRepository(app_settings.DATABASE_PATH)
            subscription_repo = SubscriptionRepository(app_settings.DATABASE_PATH)
            
            # Получаем все completed платежи за подписки без subscription_id
            # Используем прямой SQL запрос для эффективности
            from app.infra.sqlite_utils import open_async_connection
            
            async with open_async_connection(app_settings.DATABASE_PATH) as conn:
                async with conn.execute(
                    """
                    SELECT p.id, p.payment_id, p.user_id, p.tariff_id, p.created_at, p.updated_at
                    FROM payments p
                    WHERE p.status = 'completed'
                    AND p.subscription_id IS NULL
                    AND p.protocol = 'v2ray'
                    AND p.metadata LIKE '%subscription%'
                    AND p.created_at > ?  -- Только платежи за последние 90 дней
                    ORDER BY p.created_at DESC
                    LIMIT 50  -- Обрабатываем максимум 50 платежей за раз
                    """,
                    (int(time.time()) - 90 * 24 * 3600,)
                ) as cursor:
                    payments_without_sub = await cursor.fetchall()
            
            if not payments_without_sub:
                return
            
            logger.info(f"[FIX_SUBSCRIPTION_ID] Found {len(payments_without_sub)} completed payments without subscription_id")
            
            fixed_count = 0
            failed_count = 0
            
            for payment_row in payments_without_sub:
                payment_id_db = payment_row[0]
                payment_id = payment_row[1]
                user_id = payment_row[2]
                tariff_id = payment_row[3]
                payment_created_at = payment_row[4]
                
                try:
                    # Ищем активную подписку для этого пользователя и тарифа
                    # Проверяем подписки, созданные до или в момент обработки платежа
                    grace_period = 24 * 3600  # 24 часа
                    grace_threshold = payment_created_at - grace_period
                    
                    async with open_async_connection(app_settings.DATABASE_PATH) as conn:
                        async with conn.execute(
                            """
                            SELECT id, created_at, expires_at
                            FROM subscriptions
                            WHERE user_id = ?
                            AND tariff_id = ?
                            AND is_active = 1
                            AND created_at <= ?
                            AND expires_at > ?
                            ORDER BY created_at DESC
                            LIMIT 1
                            """,
                            (user_id, tariff_id, payment_created_at + 3600, grace_threshold)
                        ) as sub_cursor:
                            subscription_row = await sub_cursor.fetchone()
                    
                    if subscription_row:
                        subscription_id = subscription_row[0]
                        sub_created_at = subscription_row[1]
                        sub_expires_at = subscription_row[2]
                        
                        # Проверяем, что подписка была активна на момент обработки платежа
                        # Платеж должен был продлить эту подписку
                        if payment_created_at <= sub_expires_at + 3600:  # Разница менее часа
                            # Привязываем платеж к подписке
                            success = await payment_repo.update_subscription_id(payment_id, subscription_id)
                            
                            if success:
                                fixed_count += 1
                                logger.info(
                                    f"[FIX_SUBSCRIPTION_ID] Fixed payment {payment_id} (DB ID: {payment_id_db}): "
                                    f"linked to subscription {subscription_id} (user {user_id}, tariff {tariff_id})"
                                )
                            else:
                                failed_count += 1
                                logger.warning(
                                    f"[FIX_SUBSCRIPTION_ID] Failed to update subscription_id for payment {payment_id} "
                                    f"(DB ID: {payment_id_db}): update_subscription_id returned False"
                                )
                        else:
                            logger.debug(
                                f"[FIX_SUBSCRIPTION_ID] Payment {payment_id} (created {payment_created_at}) "
                                f"does not match subscription {subscription_id} (expires {sub_expires_at})"
                            )
                    else:
                        # Не нашли подписку - возможно, платеж был обработан как покупка, но подписка не была создана
                        # Или подписка была деактивирована
                        logger.debug(
                            f"[FIX_SUBSCRIPTION_ID] No matching subscription found for payment {payment_id} "
                            f"(user {user_id}, tariff {tariff_id}, created {payment_created_at})"
                        )
                    
                    # Небольшая задержка между обработкой
                    await asyncio.sleep(0.5)
                    
                except Exception as e:
                    failed_count += 1
                    logger.error(
                        f"[FIX_SUBSCRIPTION_ID] Error fixing payment {payment_id} (DB ID: {payment_id_db}): {e}",
                        exc_info=True
                    )
                    continue
            
            if fixed_count > 0:
                logger.info(
                    f"[FIX_SUBSCRIPTION_ID] Fixed {fixed_count} payments, {failed_count} failed"
                )
            elif failed_count > 0:
                logger.warning(
                    f"[FIX_SUBSCRIPTION_ID] Failed to fix {failed_count} payments"
                )
                    
        except Exception as e:
            logger.error(f"[FIX_SUBSCRIPTION_ID] Error in fix_payments_without_subscription_id: {e}", exc_info=True)
    
    await _run_periodic(
        "fix_payments_without_subscription_id",
        interval_seconds=1800,  # Каждые 30 минут
        job=job,
        max_backoff=3600,
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
        
        if protocol not in ('v2ray', 'outline') or not active:
            logger.info(f"Server {server_id} is not an active V2Ray or Outline server, skipping")
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
                # Генерация email для ключа
                key_email = f"{user_id}_subscription_{subscription_id}@veilbot.com"
                
                # Проверить, что у пользователя еще нет ключа на этом сервере для этой подписки
                with get_db_cursor() as cursor:
                    if protocol == 'v2ray':
                        cursor.execute("""
                            SELECT id FROM v2ray_keys
                            WHERE server_id = ? AND user_id = ? AND subscription_id = ?
                        """, (server_id, user_id, subscription_id))
                    elif protocol == 'outline':
                        cursor.execute("""
                            SELECT id FROM keys
                            WHERE server_id = ? AND user_id = ? AND subscription_id = ?
                        """, (server_id, user_id, subscription_id))
                    existing_key = cursor.fetchone()
                
                if existing_key:
                    logger.debug(f"Key already exists for subscription {subscription_id} on server {server_id}")
                    continue
                
                # Создать ключ на новом сервере
                from vpn_protocols import ProtocolFactory
                protocol_client = None
                v2ray_uuid = None
                outline_key_id = None
                
                if protocol == 'v2ray':
                    server_config = {
                        'api_url': api_url,
                        'api_key': api_key,
                        'domain': domain,
                    }
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
                    
                    # Перепроверяем в БД перед созданием (мог появиться конкурентно)
                    with get_db_cursor() as check_cursor:
                        check_cursor.execute("""
                            SELECT id FROM v2ray_keys
                            WHERE server_id = ? AND subscription_id = ?
                        """, (server_id, subscription_id))
                        if check_cursor.fetchone():
                            logger.debug(
                                f"Key already exists for subscription {subscription_id} on server {server_id}, skipping"
                            )
                            await protocol_client.close()
                            # Удаляем созданный на сервере ключ, так как он не нужен
                            try:
                                await protocol_client.delete_user(v2ray_uuid)
                            except Exception as cleanup_error:
                                logger.warning(
                                    f"Failed to cleanup orphaned key on server {server_id}: {cleanup_error}"
                                )
                            continue
                    
                    # Сохранить V2Ray ключ в БД
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
                        except sqlite3.IntegrityError as e:
                            # Если все же произошла ошибка уникальности (например, по v2ray_uuid)
                            logger.warning(
                                f"Integrity error when inserting key for subscription {subscription_id} "
                                f"on server {server_id}: {e}. Key may have been created concurrently."
                            )
                            await protocol_client.close()
                            # Удаляем созданный на сервере ключ, так как он не нужен
                            try:
                                await protocol_client.delete_user(v2ray_uuid)
                            except Exception as cleanup_error:
                                logger.warning(
                                    f"Failed to cleanup orphaned key on server {server_id}: {cleanup_error}"
                                )
                            continue
                        finally:
                            cursor.connection.execute("PRAGMA foreign_keys = ON")
                
                elif protocol == 'outline':
                    server_config = {
                        'api_url': api_url,
                        'cert_sha256': cert_sha256,
                    }
                    protocol_client = ProtocolFactory.create_protocol('outline', server_config)
                    user_data = await protocol_client.create_user(key_email)
                    
                    if not user_data or not user_data.get('id'):
                        raise Exception("Failed to create user on Outline server")
                    
                    outline_key_id = user_data['id']
                    access_url = user_data['accessUrl']
                    
                    # Сохранить Outline ключ в БД
                    with get_db_cursor(commit=True) as cursor:
                        cursor.connection.execute("PRAGMA foreign_keys = OFF")
                        try:
                            cursor.execute("""
                                INSERT INTO keys 
                                (server_id, user_id, access_url, expiry_at, traffic_limit_mb, notified, key_id, created_at, email, tariff_id, protocol, subscription_id)
                                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                            """, (
                                server_id,
                                user_id,
                                access_url,
                                expires_at,
                                0,  # traffic_limit_mb
                                outline_key_id,
                                int(time.time()),
                                key_email,
                                tariff_id,
                                'outline',
                                subscription_id,
                            ))
                        finally:
                            cursor.connection.execute("PRAGMA foreign_keys = ON")
                else:
                    logger.warning(
                        f"Unknown protocol {protocol} for server {server_id}, skipping"
                    )
                    continue
                
                # Закрываем соединение с протоколом
                if protocol_client:
                    try:
                        await protocol_client.close()
                    except Exception:
                        pass
                
                # Инвалидировать кэш подписки
                invalidate_subscription_cache(token)
                
                created_count += 1
                logger.info(
                    f"Created {protocol} key for subscription {subscription_id} on server {server_id} "
                    f"(user_id={user_id})"
                )
                
            except Exception as e:
                failed_count += 1
                logger.error(
                    f"Failed to create {protocol} key for subscription {subscription_id} on server {server_id}: {e}",
                    exc_info=True
                )
                # Если ключ был создан на сервере, но не сохранен в БД - пытаемся удалить его с сервера
                if protocol_client:
                    try:
                        if protocol == 'v2ray' and v2ray_uuid:
                            await protocol_client.delete_user(v2ray_uuid)
                        elif protocol == 'outline' and outline_key_id:
                            await protocol_client.delete_user(outline_key_id)
                    except Exception as cleanup_error:
                        logger.error(f"Failed to cleanup orphaned key: {cleanup_error}")
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


async def _check_key_exists_on_server(
    v2ray_uuid: str,
    api_url: str,
    api_key: str,
    domain: str,
    api_semaphore: asyncio.Semaphore,
    protocol: str = 'v2ray',
    cert_sha256: Optional[str] = None,
    outline_key_id: Optional[str] = None,
) -> bool:
    """
    Проверить, существует ли ключ на сервере.
    
    Args:
        v2ray_uuid: UUID ключа V2Ray (для protocol='v2ray')
        api_url: URL API сервера
        api_key: API ключ (для V2Ray)
        domain: Домен сервера (для V2Ray)
        api_semaphore: Семафор для ограничения параллелизма
        protocol: Протокол ('v2ray' или 'outline')
        cert_sha256: SHA256 сертификата (для Outline)
        outline_key_id: ID ключа Outline (для protocol='outline')
    
    Returns:
        True если ключ существует на сервере, False если нет (404) или ошибка
    """
    async with api_semaphore:
        try:
            if protocol == 'v2ray':
                server_config = {
                    'api_url': api_url,
                    'api_key': api_key,
                    'domain': domain,
                }
                protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                try:
                    # Пытаемся получить конфигурацию ключа
                    await protocol_client.get_user_config(
                        v2ray_uuid,
                        {
                            'domain': domain or 'veil-bot.ru',
                            'port': 443,
                            'email': f'user@veilbot.com',
                        }
                    )
                    return True
                except Exception as e:
                    # Если 404 - ключа нет на сервере
                    error_str = str(e).lower()
                    if '404' in error_str or 'not found' in error_str:
                        return False
                    # Для других ошибок считаем, что ключ может существовать
                    logger.warning(f"Error checking key {v2ray_uuid[:8]}... on server: {e}")
                    return True  # В случае сомнений считаем, что ключ существует
                finally:
                    if hasattr(protocol_client, 'close'):
                        await protocol_client.close()
            elif protocol == 'outline':
                server_config = {
                    'api_url': api_url,
                    'cert_sha256': cert_sha256 or '',
                }
                protocol_client = ProtocolFactory.create_protocol('outline', server_config)
                try:
                    # Получаем все ключи с сервера
                    all_keys = await protocol_client.get_all_keys()
                    if all_keys is None:
                        return False
                    # Проверяем наличие ключа по ID
                    if outline_key_id:
                        for key in all_keys:
                            if str(key.get('id', '')) == str(outline_key_id):
                                return True
                    return False
                except Exception as e:
                    logger.warning(f"Error checking Outline key {outline_key_id} on server: {e}")
                    return True  # В случае сомнений считаем, что ключ существует
            else:
                return True
        except Exception as e:
            logger.error(f"Failed to check key existence: {e}", exc_info=True)
            return True  # В случае ошибки считаем, что ключ существует (не пересоздаем)


async def _create_subscription_key_on_server(
    subscription_id: int,
    user_id: int,
    server_id: int,
    server_info: tuple,
    expires_at: int,
    tariff_id: Optional[int],
    now: int,
    api_semaphore: asyncio.Semaphore,
    protocol: str = 'v2ray',
) -> Tuple[bool, Optional[str]]:
    """
    Создать ключ подписки на сервере.
    
    Args:
        subscription_id: ID подписки
        user_id: ID пользователя
        server_id: ID сервера
        server_info: Кортеж с данными сервера (зависит от протокола)
        expires_at: Timestamp истечения подписки
        tariff_id: ID тарифа
        now: Текущий timestamp
        api_semaphore: Семафор для ограничения параллелизма
        protocol: Протокол ('v2ray' или 'outline')
    
    Returns:
        (success: bool, error_message: Optional[str])
    """
    if protocol == 'v2ray':
        server_id_db, name, api_url, api_key, domain, v2ray_path = server_info
    elif protocol == 'outline':
        server_id_db, name, api_url, cert_sha256 = server_info[:4]
        api_key = None
        domain = None
        v2ray_path = None
    else:
        return False, f"Unsupported protocol: {protocol}"
    
    async with api_semaphore:  # Ограничиваем параллелизм API-запросов
        try:
            key_email = f"{user_id}_subscription_{subscription_id}@veilbot.com"
            
            if protocol == 'v2ray':
                server_config = {
                    'api_url': api_url,
                    'api_key': api_key,
                    'domain': domain,
                }
                protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                try:
                    user_data = await protocol_client.create_user(key_email, name=name)
                    
                    if not user_data or not user_data.get('uuid'):
                        return False, "Failed to create user on V2Ray server"
                    
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
                    
                    # Используем INSERT OR IGNORE для избежания race conditions
                    with get_db_cursor(commit=True) as cursor:
                        cursor.connection.execute("PRAGMA foreign_keys = OFF")
                        try:
                            cursor.execute("""
                                INSERT OR IGNORE INTO v2ray_keys 
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
                            if cursor.rowcount == 0:
                                # Ключ уже существует (race condition), удаляем с сервера
                                await protocol_client.delete_user(v2ray_uuid)
                                return False, "Key already exists (race condition)"
                        finally:
                            cursor.connection.execute("PRAGMA foreign_keys = ON")
                    
                    return True, None
                finally:
                    if hasattr(protocol_client, 'close'):
                        await protocol_client.close()
            elif protocol == 'outline':
                server_config = {
                    'api_url': api_url,
                    'cert_sha256': cert_sha256 or '',
                }
                protocol_client = ProtocolFactory.create_protocol('outline', server_config)
                try:
                    user_data = await protocol_client.create_user(key_email)
                    
                    if not user_data or not user_data.get('id') or not user_data.get('accessUrl'):
                        return False, "Failed to create user on Outline server"
                    
                    outline_key_id = user_data['id']
                    access_url = user_data['accessUrl']
                    
                    # Используем INSERT OR IGNORE для избежания race conditions
                    with get_db_cursor(commit=True) as cursor:
                        cursor.connection.execute("PRAGMA foreign_keys = OFF")
                        try:
                            cursor.execute("""
                                INSERT OR IGNORE INTO keys 
                                (server_id, user_id, access_url, traffic_limit_mb, notified, key_id, created_at, email, tariff_id, protocol, subscription_id)
                                VALUES (?, ?, ?, 0, 0, ?, ?, ?, ?, 'outline', ?)
                            """, (
                                server_id,
                                user_id,
                                access_url,
                                outline_key_id,
                                now,
                                key_email,
                                tariff_id,
                                subscription_id,
                            ))
                            if cursor.rowcount == 0:
                                # Ключ уже существует (race condition), удаляем с сервера
                                await protocol_client.delete_user(outline_key_id)
                                return False, "Key already exists (race condition)"
                        finally:
                            cursor.connection.execute("PRAGMA foreign_keys = ON")
                    
                    return True, None
                    
                except Exception as e:
                    logger.error(f"Error creating Outline key: {e}", exc_info=True)
                    return False, str(e)
                finally:
                    if hasattr(protocol_client, 'close'):
                        await protocol_client.close()
        except sqlite3.IntegrityError as e:
            logger.warning(
                f"Sync: Integrity error when inserting key for subscription {subscription_id} "
                f"on server {server_id}: {e}. Key may have been created concurrently."
            )
            return False, f"Integrity error: {e}"
        except Exception as e:
            logger.error(
                f"Sync: Failed to create key for subscription {subscription_id} "
                f"on server {server_id}: {e}",
                exc_info=True
            )
            return False, str(e)


async def _delete_subscription_key_from_server(
    key_id: int,
    subscription_id: int,
    server_id: int,
    v2ray_uuid: Optional[str],
    api_url: Optional[str],
    api_key: Optional[str],
    api_semaphore: asyncio.Semaphore,
    protocol: str = 'v2ray',
    cert_sha256: Optional[str] = None,
    outline_key_id: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Удалить ключ подписки с сервера и из БД.
    
    Args:
        key_id: ID ключа в БД
        subscription_id: ID подписки
        server_id: ID сервера
        v2ray_uuid: UUID ключа V2Ray (для protocol='v2ray')
        api_url: URL API сервера
        api_key: API ключ (для V2Ray)
        api_semaphore: Семафор для ограничения параллелизма
        protocol: Протокол ('v2ray' или 'outline')
        cert_sha256: SHA256 сертификата (для Outline)
        outline_key_id: ID ключа Outline (для protocol='outline')
    
    Returns:
        (success: bool, error_message: Optional[str])
    """
    async with api_semaphore:  # Ограничиваем параллелизм API-запросов
        try:
            deleted_from_server = False
            
            if protocol == 'v2ray':
                # Удаляем через V2Ray API
                if api_url and api_key and v2ray_uuid:
                    server_config = {
                        'api_url': api_url,
                        'api_key': api_key,
                        'domain': '',
                    }
                    protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                    try:
                        deleted_from_server = await protocol_client.delete_user(v2ray_uuid)
                        if deleted_from_server:
                            logger.debug(
                                f"Sync: Deleted key {v2ray_uuid[:8]}... "
                                f"from server {server_id} via API"
                            )
                        else:
                            logger.warning(
                                f"Sync: Failed to delete key {v2ray_uuid[:8]}... "
                                f"from server {server_id} via API (returned False)"
                            )
                    except Exception as api_error:
                        logger.warning(
                            f"Sync: Failed to delete key {v2ray_uuid[:8]}... "
                            f"from server {server_id} via API: {api_error}"
                        )
                        deleted_from_server = False
                    finally:
                        if hasattr(protocol_client, 'close'):
                            await protocol_client.close()
                else:
                    # Если нет данных для удаления с сервера, считаем что нужно удалить из БД
                    logger.debug(
                        f"Sync: Key {key_id} has no server info, will delete from DB only"
                    )
                    deleted_from_server = True
                
                # Удаляем из БД только если удаление с сервера успешно или нет данных о сервере
                if deleted_from_server:
                    with get_db_cursor(commit=True) as cursor:
                        with safe_foreign_keys_off(cursor):
                            cursor.execute(
                                "DELETE FROM v2ray_keys WHERE id = ?",
                                (key_id,)
                            )
                            if cursor.rowcount == 0:
                                logger.warning(
                                    f"Sync: Key {key_id} not found in DB (may have been deleted already)"
                                )
                                return False, "Key not found in DB"
                    return True, None
                else:
                    return False, "Failed to delete from server"
            elif protocol == 'outline':
                # Удаляем через Outline API
                if api_url and outline_key_id:
                    server_config = {
                        'api_url': api_url,
                        'cert_sha256': cert_sha256 or '',
                    }
                    protocol_client = ProtocolFactory.create_protocol('outline', server_config)
                    try:
                        deleted_from_server = await protocol_client.delete_user(outline_key_id)
                        if deleted_from_server:
                            logger.debug(
                                f"Sync: Deleted Outline key {outline_key_id} "
                                f"from server {server_id} via API"
                            )
                        else:
                            logger.warning(
                                f"Sync: Failed to delete Outline key {outline_key_id} "
                                f"from server {server_id} via API (returned False)"
                            )
                    except Exception as api_error:
                        logger.warning(
                            f"Sync: Failed to delete Outline key {outline_key_id} "
                            f"from server {server_id} via API: {api_error}"
                        )
                        deleted_from_server = False
                else:
                    # Если нет данных для удаления с сервера, считаем что нужно удалить из БД
                    logger.debug(
                        f"Sync: Outline key {key_id} has no server info, will delete from DB only"
                    )
                    deleted_from_server = True
                
                # Удаляем из БД только если удаление с сервера успешно или нет данных о сервере
                if deleted_from_server:
                    with get_db_cursor(commit=True) as cursor:
                        with safe_foreign_keys_off(cursor):
                            cursor.execute(
                                "DELETE FROM keys WHERE id = ? AND protocol = 'outline'",
                                (key_id,)
                            )
                            if cursor.rowcount == 0:
                                logger.warning(
                                    f"Sync: Outline key {key_id} not found in DB (may have been deleted already)"
                                )
                                return False, "Key not found in DB"
                    return True, None
                else:
                    return False, "Failed to delete from server"
            else:
                return False, f"Unsupported protocol: {protocol}"
                
        except Exception as e:
            logger.error(
                f"Sync: Failed to delete key {key_id} for subscription {subscription_id}: {e}",
                exc_info=True
            )
            return False, str(e)


async def _process_subscription_sync(
    subscription: tuple,
    v2ray_keys_by_subscription: Dict[int, list],
    outline_keys_by_subscription: Dict[int, list],
    v2ray_active_server_ids: set,
    v2ray_active_servers_dict: Dict[int, tuple],
    outline_active_server_ids: set,
    outline_active_servers_dict: Dict[int, tuple],
    now: int,
    api_semaphore: asyncio.Semaphore,
) -> Dict[str, Any]:
    """
    Обработать синхронизацию одной подписки для V2Ray и Outline серверов.
    
    Returns:
        dict с результатами: created, deleted, failed_create, failed_delete, tokens_to_invalidate
    """
    subscription_id, user_id, token, expires_at, tariff_id = subscription
    
    result = {
        'created': 0,
        'deleted': 0,
        'failed_create': 0,
        'failed_delete': 0,
        'tokens_to_invalidate': set(),
    }
    
    try:
        # Обрабатываем V2Ray ключи
        if v2ray_active_server_ids:
            await _process_protocol_sync(
                subscription_id, user_id, token, expires_at, tariff_id,
                v2ray_keys_by_subscription.get(subscription_id, []),
                v2ray_active_server_ids, v2ray_active_servers_dict,
                'v2ray', now, api_semaphore, result
            )
        
        # Обрабатываем Outline ключи
        if outline_active_server_ids:
            await _process_protocol_sync(
                subscription_id, user_id, token, expires_at, tariff_id,
                outline_keys_by_subscription.get(subscription_id, []),
                outline_active_server_ids, outline_active_servers_dict,
                'outline', now, api_semaphore, result
            )
        
        # Добавляем токен для инвалидации кэша, если были изменения
        if result['created'] > 0 or result['deleted'] > 0:
            result['tokens_to_invalidate'].add(token)
        
        return result
        
    except Exception as e:
        logger.error(
            f"Sync: Error processing subscription {subscription_id}: {e}",
            exc_info=True
        )
        return result


async def _process_protocol_sync(
    subscription_id: int,
    user_id: int,
    token: str,
    expires_at: int,
    tariff_id: Optional[int],
    existing_keys: list,
    active_server_ids: set,
    active_servers_dict: Dict[int, tuple],
    protocol: str,
    now: int,
    api_semaphore: asyncio.Semaphore,
    result: Dict[str, Any],
) -> None:
    """
    Обработать синхронизацию ключей для одного протокола (V2Ray или Outline).
    """
    existing_server_ids = {key[1] for key in existing_keys}
    
    # Проверяем существующие ключи на серверах
    keys_to_recreate = []
    verified_existing_server_ids = set()
    
    for key in existing_keys:
        if protocol == 'v2ray':
            key_id, server_id, v2ray_uuid, api_url, api_key = key
            outline_key_id = None
            cert_sha256 = None
        else:  # outline
            key_id, server_id, outline_key_id, api_url, cert_sha256 = key
            v2ray_uuid = None
            api_key = None
        
        if server_id in active_server_ids:
            # Проверяем наличие ключа на сервере
            server_info = active_servers_dict.get(server_id)
            if server_info:
                if protocol == 'v2ray':
                    server_id_db, name, api_url_from_dict, api_key_from_dict, domain, v2ray_path = server_info
                    key_exists = await _check_key_exists_on_server(
                        v2ray_uuid,
                        api_url_from_dict or api_url,
                        api_key_from_dict or api_key,
                        domain or '',
                        api_semaphore,
                        protocol='v2ray'
                    )
                else:  # outline
                    server_id_db, name, api_url_from_dict, cert_sha256_from_dict = server_info
                    key_exists = await _check_key_exists_on_server(
                        '',
                        api_url_from_dict or api_url,
                        '',
                        '',
                        api_semaphore,
                        protocol='outline',
                        cert_sha256=cert_sha256_from_dict or cert_sha256,
                        outline_key_id=outline_key_id
                    )
                
                if key_exists:
                    verified_existing_server_ids.add(server_id)
                else:
                    # Ключ есть в БД, но его нет на сервере - нужно пересоздать
                    logger.info(
                        f"Sync: Key {key_id} for subscription {subscription_id} "
                        f"exists in DB but not on server {server_id}, will recreate"
                    )
                    keys_to_recreate.append(key)
            else:
                verified_existing_server_ids.add(server_id)
        else:
            verified_existing_server_ids.add(server_id)
    
    # Определяем серверы, где нужно создать ключи
    servers_to_create = active_server_ids - verified_existing_server_ids
    
    # Определяем ключи на неактивных серверах, которые нужно удалить
    keys_to_delete = [
        key for key in existing_keys
        if key[1] not in active_server_ids
    ]
    
    # Находим дубликаты
    keys_by_server = defaultdict(list)
    for key in existing_keys:
        if key[1] in active_server_ids:
            keys_by_server[key[1]].append(key)
    
    # Для каждого сервера, если есть несколько ключей - оставляем самый новый, остальные удаляем
    duplicate_keys_to_delete = []
    for server_id, server_keys in keys_by_server.items():
        if len(server_keys) > 1:
            server_keys_sorted = sorted(server_keys, key=lambda k: k[0], reverse=True)
            for duplicate_key in server_keys_sorted[1:]:
                duplicate_keys_to_delete.append(duplicate_key)
                logger.info(
                    f"Sync: Found duplicate {protocol} key {duplicate_key[0]} for subscription {subscription_id} "
                    f"on server {server_id}, will be deleted"
                )
    
    keys_to_delete.extend(duplicate_keys_to_delete)
    
    # Удаляем ключи, которые нужно пересоздать
    for key_to_recreate in keys_to_recreate:
        key_id = key_to_recreate[0]
        if protocol == 'v2ray':
            with get_db_cursor(commit=True) as cursor:
                cursor.execute("DELETE FROM v2ray_keys WHERE id = ?", (key_id,))
        else:  # outline
            with get_db_cursor(commit=True) as cursor:
                cursor.execute("DELETE FROM keys WHERE id = ? AND protocol = 'outline'", (key_id,))
        logger.info(f"Sync: Deleted DB record for {protocol} key {key_id} before recreation")
        server_id = key_to_recreate[1]
        if server_id not in servers_to_create:
            servers_to_create.add(server_id)
    
    # Параллельно создаем недостающие ключи
    create_tasks = []
    for server_id in servers_to_create:
        if server_id not in active_servers_dict:
            continue
        server_info = active_servers_dict[server_id]
        create_tasks.append(
            _create_subscription_key_on_server(
                subscription_id, user_id, server_id, server_info,
                expires_at, tariff_id, now, api_semaphore, protocol=protocol
            )
        )
    
    if create_tasks:
        create_results = await asyncio.gather(*create_tasks, return_exceptions=True)
        for task_result in create_results:
            if isinstance(task_result, Exception):
                result['failed_create'] += 1
                logger.error(f"Sync: Exception in create task: {task_result}", exc_info=True)
            else:
                success, error = task_result
                if success:
                    result['created'] += 1
                else:
                    result['failed_create'] += 1
                    if error and "already exists" not in error.lower():
                        logger.warning(f"Sync: Failed to create {protocol} key: {error}")

    # Параллельно удаляем ключи
    delete_tasks = []
    for key in keys_to_delete:
        key_id = key[0]
        server_id = key[1]
        if protocol == 'v2ray':
            v2ray_uuid = key[2]
            api_url = key[3]
            api_key = key[4]
            delete_tasks.append(
                _delete_subscription_key_from_server(
                    key_id, subscription_id, server_id, v2ray_uuid,
                    api_url, api_key, api_semaphore, protocol='v2ray'
                )
            )
        else:  # outline
            outline_key_id = key[2]
            api_url = key[3]
            cert_sha256 = key[4]
            delete_tasks.append(
                _delete_subscription_key_from_server(
                    key_id, subscription_id, server_id, None,
                    api_url, None, api_semaphore, protocol='outline',
                    cert_sha256=cert_sha256, outline_key_id=outline_key_id
                )
            )
    
    if delete_tasks:
        delete_results = await asyncio.gather(*delete_tasks, return_exceptions=True)
        for task_result in delete_results:
            if isinstance(task_result, Exception):
                result['failed_delete'] += 1
                logger.error(f"Sync: Exception in delete task: {task_result}", exc_info=True)
            else:
                success, error = task_result
                if success:
                    result['deleted'] += 1
                else:
                    result['failed_delete'] += 1
                    if error:
                        logger.warning(f"Sync: Failed to delete {protocol} key: {error}")


def _extract_v2ray_uuid(remote_entry: Dict[str, Any]) -> Optional[str]:
    """Извлечь UUID ключа V2Ray из ответа сервера"""
    uuid = remote_entry.get("uuid")
    if not uuid:
        key_info = remote_entry.get("key") or {}
        if isinstance(key_info, dict):
            uuid = key_info.get("uuid")
    if isinstance(uuid, str) and uuid.strip():
        return uuid.strip()
    return None


def _extract_outline_key_id(remote_entry: Dict[str, Any]) -> Optional[str]:
    """Извлечь ID ключа Outline из ответа сервера"""
    key_id = remote_entry.get("id")
    if key_id is not None:
        return str(key_id).strip()
    return None


async def _delete_orphaned_keys_from_server(
    server_id: int,
    server_name: str,
    protocol: str,
    api_url: str,
    api_key: Optional[str] = None,
    cert_sha256: Optional[str] = None,
    api_semaphore: asyncio.Semaphore = None,
) -> Tuple[int, int]:
    """
    Удалить orphaned ключи с сервера (которых нет в БД).
    
    Returns:
        (deleted_count, error_count)
    """
    if api_semaphore is None:
        api_semaphore = asyncio.Semaphore(10)
    
    deleted_count = 0
    error_count = 0
    
    try:
        # Получаем ключи из БД для этого сервера
        if protocol == 'v2ray':
            with get_db_cursor() as cursor:
                cursor.execute("""
                    SELECT v2ray_uuid, email
                    FROM v2ray_keys
                    WHERE server_id = ?
                """, (server_id,))
                db_keys = cursor.fetchall()
            
            db_uuids: Set[str] = {row[0].strip() for row in db_keys if row[0]}
            db_emails: Set[str] = {(row[1] or "").lower().strip() for row in db_keys if row[1]}
        else:  # outline
            with get_db_cursor() as cursor:
                cursor.execute("""
                    SELECT key_id, email
                    FROM keys
                    WHERE server_id = ? AND protocol = 'outline' AND (key_id IS NOT NULL AND key_id != '')
                """, (server_id,))
                db_keys = cursor.fetchall()
            
            db_key_ids: Set[str] = {str(row[0]).strip() for row in db_keys if row[0]}
            db_emails: Set[str] = {(row[1] or "").lower().strip() for row in db_keys if row[1]}
        
        # Создаем клиент протокола
        if protocol == 'v2ray':
            if not api_url or not api_key:
                return 0, 0
            server_config = {
                'api_url': api_url,
                'api_key': api_key,
            }
            protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
        else:  # outline
            if not api_url:
                return 0, 0
            server_config = {
                'api_url': api_url,
                'cert_sha256': cert_sha256 or '',
            }
            protocol_client = ProtocolFactory.create_protocol('outline', server_config)
        
        # Получаем все ключи с сервера
        try:
            remote_keys = await protocol_client.get_all_keys()
            if not remote_keys:
                if hasattr(protocol_client, 'close'):
                    await protocol_client.close()
                return 0, 0
        except Exception as e:
            logger.warning(f"Sync: Failed to get keys from {protocol} server {server_id} ({server_name}): {e}")
            if hasattr(protocol_client, 'close'):
                await protocol_client.close()
            return 0, 1
        
        # Находим orphaned ключи
        keys_to_delete = []
        for remote_entry in remote_keys:
            if protocol == 'v2ray':
                remote_uuid = _extract_v2ray_uuid(remote_entry)
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
            else:  # outline
                remote_key_id = _extract_outline_key_id(remote_entry)
                if not remote_key_id or remote_key_id in db_key_ids:
                    continue
                
                remote_name = (remote_entry.get("name") or "").lower().strip()
                if remote_name in db_emails:
                    continue
                
                keys_to_delete.append({"key_id": remote_key_id})
        
        # Удаляем orphaned ключи
        if keys_to_delete:
            logger.info(f"Sync: Found {len(keys_to_delete)} orphaned {protocol} keys on server {server_id} ({server_name})")
            
            async def delete_key(key_info: Dict[str, Any]) -> bool:
                async with api_semaphore:
                    try:
                        if protocol == 'v2ray':
                            deleted = await protocol_client.delete_user(str(key_info["id"]))
                        else:  # outline
                            deleted = await protocol_client.delete_user(key_info["key_id"])
                        return deleted
                    except Exception as e:
                        logger.warning(f"Sync: Failed to delete orphaned {protocol} key: {e}")
                        return False
            
            delete_tasks = [delete_key(key_info) for key_info in keys_to_delete]
            delete_results = await asyncio.gather(*delete_tasks, return_exceptions=True)
            
            for result in delete_results:
                if isinstance(result, Exception):
                    error_count += 1
                elif result:
                    deleted_count += 1
                else:
                    error_count += 1
        
        # Закрываем соединение (если метод доступен)
        try:
            if hasattr(protocol_client, 'close'):
                await protocol_client.close()
        except Exception:
            pass
        
    except Exception as e:
        logger.error(f"Sync: Error deleting orphaned keys from {protocol} server {server_id}: {e}", exc_info=True)
        error_count += 1
    
    return deleted_count, error_count


async def sync_subscription_keys_with_active_servers() -> None:
    """
    Синхронизация ключей подписок с активными серверами (V2Ray и Outline).
    Удаляет ключи на неактивных серверах, создает недостающие на активных,
    и удаляет orphaned ключи с всех серверов (которых нет в БД).
    Запускается каждый час.
    
    Оптимизированная версия с:
    - Оптимизацией SQL-запросов (один запрос для всех ключей)
    - Параллельной обработкой подписок
    - Батчингом операций БД
    - Батчингом инвалидации кэша
    - Rate limiting для API-запросов
    - Поддержкой V2Ray и Outline серверов
    - Удалением orphaned ключей с всех серверов
    """
    async def job() -> None:
        try:
            now = int(time.time())
            
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
                # Все равно проверяем orphaned ключи на всех серверах
                active_subscriptions = []
            
            # Получаем все активные V2Ray серверы
            with get_db_cursor() as cursor:
                cursor.execute("""
                    SELECT id, name, api_url, api_key, domain, v2ray_path
                    FROM servers
                    WHERE protocol = 'v2ray' AND active = 1
                    ORDER BY id
                """)
                v2ray_servers = cursor.fetchall()
            
            # Получаем активные Outline серверы (только сервер с id=8 и available_for_purchase=1)
            with get_db_cursor() as cursor:
                cursor.execute("""
                    SELECT id, name, api_url, cert_sha256
                    FROM servers
                    WHERE protocol = 'outline' AND active = 1 
                    AND available_for_purchase = 1
                    ORDER BY CASE WHEN id = 8 THEN 0 ELSE 1 END, id
                    LIMIT 1
                """)
                outline_servers = cursor.fetchall()
            
            # Получаем ВСЕ серверы для проверки orphaned ключей (не только активные)
            with get_db_cursor() as cursor:
                cursor.execute("""
                    SELECT id, name, protocol, api_url, api_key, cert_sha256
                    FROM servers
                    WHERE protocol IN ('v2ray', 'outline')
                """)
                all_servers = cursor.fetchall()
            
            if not v2ray_servers and not outline_servers and not active_subscriptions:
                logger.debug("No active servers or subscriptions found for sync")
                # Все равно проверяем orphaned ключи
                if not all_servers:
                    return
            
            # Обрабатываем V2Ray серверы
            v2ray_active_server_ids = {server[0] for server in v2ray_servers}
            v2ray_active_servers_dict = {server[0]: server for server in v2ray_servers}
            
            # Обрабатываем Outline серверы
            outline_active_server_ids = {server[0] for server in outline_servers}
            outline_active_servers_dict = {server[0]: server for server in outline_servers}
            
            logger.info(
                f"Starting sync: {len(active_subscriptions)} subscriptions, "
                f"{len(v2ray_servers)} V2Ray servers, {len(outline_servers)} Outline servers, "
                f"{len(all_servers)} total servers for orphaned check"
            )
            
            # ОПТИМИЗАЦИЯ 2: Rate limiting для API-запросов (макс 10 параллельных запросов к серверам)
            api_semaphore = asyncio.Semaphore(10)
            
            # Синхронизация подписок (если есть активные подписки)
            total_created = 0
            total_deleted = 0
            total_failed_create = 0
            total_failed_delete = 0
            tokens_to_invalidate = set()
            
            if active_subscriptions:
                # ОПТИМИЗАЦИЯ 1: Получаем все ключи подписок одним запросом для каждого протокола
                subscription_ids = [sub[0] for sub in active_subscriptions]
                placeholders = ','.join('?' * len(subscription_ids))
                
                # V2Ray ключи
                v2ray_keys_by_subscription: Dict[int, list] = defaultdict(list)
                if v2ray_servers:
                    with get_db_cursor() as cursor:
                        cursor.execute(f"""
                            SELECT k.id, k.server_id, k.v2ray_uuid, s.api_url, s.api_key, k.subscription_id
                            FROM v2ray_keys k
                            JOIN servers s ON k.server_id = s.id
                            WHERE k.subscription_id IN ({placeholders})
                        """, subscription_ids)
                        all_v2ray_keys = cursor.fetchall()
                    
                    # Группируем ключи по subscription_id
                    for key_row in all_v2ray_keys:
                        key_id, server_id, v2ray_uuid, api_url, api_key, sub_id = key_row
                        v2ray_keys_by_subscription[sub_id].append((key_id, server_id, v2ray_uuid, api_url, api_key))
                
                # Outline ключи
                outline_keys_by_subscription: Dict[int, list] = defaultdict(list)
                if outline_servers:
                    with get_db_cursor() as cursor:
                        cursor.execute(f"""
                            SELECT k.id, k.server_id, k.key_id, s.api_url, s.cert_sha256, k.subscription_id
                            FROM keys k
                            JOIN servers s ON k.server_id = s.id
                            WHERE k.subscription_id IN ({placeholders})
                            AND k.protocol = 'outline'
                        """, subscription_ids)
                        all_outline_keys = cursor.fetchall()
                    
                    # Группируем ключи по subscription_id
                    for key_row in all_outline_keys:
                        key_id, server_id, outline_key_id, api_url, cert_sha256, sub_id = key_row
                        outline_keys_by_subscription[sub_id].append((key_id, server_id, outline_key_id, api_url, cert_sha256))
            
            # ОПТИМИЗАЦИЯ 3: Параллельная обработка подписок батчами
            batch_size = 20  # Обрабатываем по 20 подписок параллельно
            
            for i in range(0, len(active_subscriptions), batch_size):
                batch = active_subscriptions[i:i + batch_size]
                
                # Создаем задачи для батча
                tasks = [
                    _process_subscription_sync(
                            subscription, 
                            v2ray_keys_by_subscription, outline_keys_by_subscription,
                            v2ray_active_server_ids, v2ray_active_servers_dict,
                            outline_active_server_ids, outline_active_servers_dict,
                        now, api_semaphore
                    )
                    for subscription in batch
                ]
                
                # Параллельно обрабатываем батч
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Собираем статистику
                for result in batch_results:
                    if isinstance(result, Exception):
                        logger.error(f"Sync: Exception in batch processing: {result}", exc_info=True)
                        continue
                    
                    total_created += result['created']
                    total_deleted += result['deleted']
                    total_failed_create += result['failed_create']
                    total_failed_delete += result['failed_delete']
                    tokens_to_invalidate.update(result.get('tokens_to_invalidate', set()))
            
            # ОПТИМИЗАЦИЯ 4: Батчинг инвалидации кэша (один раз для всех измененных подписок)
            for token in tokens_to_invalidate:
                invalidate_subscription_cache(token)
            
            # Удаление orphaned ключей с ВСЕХ серверов (не только активных)
            total_orphaned_deleted = 0
            total_orphaned_errors = 0
            
            if all_servers:
                logger.info(f"Checking {len(all_servers)} servers for orphaned keys...")
                
                orphaned_tasks = []
                for server_row in all_servers:
                    server_id, server_name, protocol, api_url, api_key, cert_sha256 = server_row
                    if protocol == 'v2ray':
                        orphaned_tasks.append(
                            _delete_orphaned_keys_from_server(
                                server_id, server_name, 'v2ray', api_url, api_key,
                                None, api_semaphore
                            )
                        )
                    elif protocol == 'outline':
                        orphaned_tasks.append(
                            _delete_orphaned_keys_from_server(
                                server_id, server_name, 'outline', api_url, None,
                                cert_sha256, api_semaphore
                            )
                        )
                
                if orphaned_tasks:
                    orphaned_results = await asyncio.gather(*orphaned_tasks, return_exceptions=True)
                    for result in orphaned_results:
                        if isinstance(result, Exception):
                            logger.error(f"Sync: Exception in orphaned keys deletion: {result}", exc_info=True)
                            total_orphaned_errors += 1
                        else:
                            deleted, errors = result
                            total_orphaned_deleted += deleted
                            total_orphaned_errors += errors
            
            logger.info(
                f"Sync completed: {total_created} created, {total_deleted} deleted, "
                f"{total_failed_create} failed to create, {total_failed_delete} failed to delete, "
                f"{total_orphaned_deleted} orphaned keys deleted, {total_orphaned_errors} orphaned errors, "
                f"{len(tokens_to_invalidate)} subscriptions cache invalidated"
            )
        
        except Exception as e:
            logger.error(f"Error in sync_subscription_keys_with_active_servers: {e}", exc_info=True)
    
    await _run_periodic(
        "sync_subscription_keys_with_active_servers",
        interval_seconds=3600,  # 1 час
        job=job,
        max_backoff=10800,  # до 3 часов при повторяющихся ошибках
    )


