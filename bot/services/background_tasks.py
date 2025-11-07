"""
Модуль для фоновых задач бота
Вынесен из bot.py для улучшения поддерживаемости
"""
import asyncio
import time
import logging
from typing import Optional, Callable, Awaitable, Dict

from utils import get_db_cursor
from outline import delete_key
from vpn_protocols import format_duration, ProtocolFactory
from bot.utils import format_key_message, format_key_message_unified, safe_send_message
from bot.keyboards import get_main_menu
from bot.core import get_bot_instance
from bot.services.key_creation import select_available_server_by_protocol
from app.infra.foreign_keys import safe_foreign_keys_off
from memory_optimizer import optimize_memory, log_memory_usage
from config import ADMIN_ID

logger = logging.getLogger(__name__)

# Глобальная переменная для отслеживания уведомлений о низком количестве ключей
low_key_notified: bool = False

_TASK_ERROR_COOLDOWN_SECONDS = 1800
_task_last_error: Dict[str, float] = {}


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
                logging.warning("Failed to deliver expiry notification to user %s", user_id)

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
                    )
                ''',
                (int(time.time()), int(time.time())),
            )
            payments = cursor.fetchall()

            for payment_db_id, user_id, tariff_id, email, protocol, country in payments:
                cursor.execute("SELECT status, payment_id FROM payments WHERE id = ?", (payment_db_id,))
                status_row = cursor.fetchone()
                if not status_row:
                    logging.warning("[AUTO-ISSUE] Payment id=%s not found, skipping", payment_db_id)
                    continue
                
                payment_status = (status_row[0] or "").lower()
                payment_uuid = status_row[1]
                
                if payment_status == "completed":
                    logging.info(
                        "[AUTO-ISSUE] Payment %s already completed, skipping key issuance", payment_uuid
                    )
                    continue
                
                if payment_status != "paid":
                    cursor.execute("UPDATE payments SET status = 'paid' WHERE id = ?", (payment_db_id,))
                cursor.execute("SELECT name, duration_sec, price_rub FROM tariffs WHERE id=?", (tariff_id,))
                tariff_row = cursor.fetchone()
                if not tariff_row:
                    logging.error("[AUTO-ISSUE] Не найден тариф id=%s для user_id=%s", tariff_id, user_id)
                    continue

                tariff = {
                    "id": tariff_id,
                    "name": tariff_row[0],
                    "duration_sec": tariff_row[1],
                    "price_rub": tariff_row[2],
                }

                protocol = protocol or "outline"
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

                    now = int(time.time())
                    expiry = now + tariff["duration_sec"]
                    cursor.execute(
                        "INSERT INTO keys (server_id, user_id, access_url, expiry_at, key_id, created_at, email, tariff_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (server_dict["id"], user_id, key["accessUrl"], expiry, key["id"], now, email, tariff_id),
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
                            cursor.execute(
                                "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id, client_config) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    server_dict["id"],
                                    user_id,
                                    user_data["uuid"],
                                    email or f"user_{user_id}@veilbot.com",
                                    now,
                                    expiry,
                                    tariff_id,
                                    config,
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

