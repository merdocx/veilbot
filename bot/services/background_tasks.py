"""
Модуль для фоновых задач бота
Вынесен из bot.py для улучшения поддерживаемости
"""
import asyncio
import time
import logging
from collections import defaultdict
from typing import Optional, Callable, Awaitable, Dict, Any

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
from app.repositories.subscription_repository import SubscriptionRepository
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


async def monitor_v2ray_traffic_limits() -> None:
    """Контроль превышения трафиковых лимитов для V2Ray ключей."""

    async def _fetch_usage_for_server(server_id: int, config: Dict[str, str], keys: list[Dict[str, Any]]) -> Dict[int, Optional[int]]:
        if not keys:
            return {}
        try:
            protocol = ProtocolFactory.create_protocol('v2ray', config)
        except Exception as e:
            logger.error(f"[TRAFFIC LIMIT] Failed to initialise V2Ray protocol for server {server_id}: {e}")
            return {}

        results: Dict[int, Optional[int]] = {}
        try:
            history = await protocol.get_traffic_history()
            traffic_map: Dict[str, int] = {}
            if isinstance(history, dict):
                data = history.get('data') or {}
                items = data.get('keys') or []
                for item in items:
                    uuid_val = item.get('key_uuid') or item.get('uuid')
                    total = item.get('total_traffic') or {}
                    total_bytes = total.get('total_bytes')
                    if uuid_val and isinstance(total_bytes, (int, float)):
                        traffic_map[uuid_val] = int(total_bytes)
            for key_row in keys:
                uuid = key_row.get('v2ray_uuid')
                key_pk = key_row.get('id')
                if uuid is None or key_pk is None:
                    continue
                results[key_pk] = traffic_map.get(uuid)
        except Exception as e:
            logger.error(f"[TRAFFIC LIMIT] Error fetching usage for server {server_id}: {e}")
        finally:
            try:
                await protocol.close()
            except Exception as close_error:
                logger.warning(f"[TRAFFIC LIMIT] Error closing protocol client for server {server_id}: {close_error}")
        return results

    async def _disable_v2ray_key(server_id: int, config: Dict[str, str], key_uuid: str) -> bool:
        try:
            protocol = ProtocolFactory.create_protocol('v2ray', config)
        except Exception as e:
            logger.error(f"[TRAFFIC LIMIT] Failed to init protocol for disable on server {server_id}: {e}")
            return False

        try:
            result = await protocol.delete_user(key_uuid)
            if not result:
                logger.warning(f"[TRAFFIC LIMIT] Failed to delete V2Ray user {key_uuid} on server {server_id}")
            return result
        except Exception as e:
            logger.error(f"[TRAFFIC LIMIT] Error disabling V2Ray key {key_uuid}: {e}")
            return False
        finally:
            try:
                await protocol.close()
            except Exception as close_error:
                logger.warning(f"[TRAFFIC LIMIT] Error closing protocol during disable: {close_error}")

    async def job() -> None:
        now = int(time.time())

        with get_db_cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS v2ray_usage_snapshots (
                    key_id INTEGER PRIMARY KEY,
                    server_bytes INTEGER DEFAULT 0,
                    updated_at INTEGER DEFAULT 0
                )
                """
            )
            cursor.execute(
                """
                SELECT 
                    k.id,
                    k.user_id,
                    k.v2ray_uuid,
                    k.server_id,
                    COALESCE(k.traffic_limit_mb, 0) AS traffic_limit_mb,
                    COALESCE(k.traffic_usage_bytes, 0) AS traffic_usage_bytes,
                    k.traffic_over_limit_at,
                    COALESCE(k.traffic_over_limit_notified, 0) AS traffic_over_limit_notified,
                    k.expiry_at,
                    IFNULL(s.api_url, '') AS api_url,
                    IFNULL(s.api_key, '') AS api_key,
                    IFNULL(t.name, '') AS tariff_name,
                    IFNULL(k.email, '') AS email
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                LEFT JOIN tariffs t ON k.tariff_id = t.id
                WHERE k.expiry_at > ?
                  AND COALESCE(k.traffic_limit_mb, 0) > 0
                """,
                (now,),
            )
            rows = cursor.fetchall()

            rows_data = [dict(row) for row in rows]
            if rows_data:
                key_ids = [row["id"] for row in rows_data]
            else:
                key_ids = []

            snapshot_map: Dict[int, int] = {}
            if key_ids:
                placeholders = ",".join("?" for _ in key_ids)
                cursor.execute(
                    f"SELECT key_id, server_bytes FROM v2ray_usage_snapshots WHERE key_id IN ({placeholders})",
                    key_ids,
                )
                snapshot_map = {
                    int(row[0]): int(row[1] or 0)
                    for row in cursor.fetchall()
                }
            else:
                snapshot_map = {}

        if not rows_data:
            return

        server_configs: Dict[int, Dict[str, str]] = {}
        server_keys_map: Dict[int, list[Dict[str, Any]]] = defaultdict(list)

        for row in rows_data:
            api_url = row["api_url"]
            api_key = row["api_key"]
            if not api_url or not api_key:
                logger.warning(
                    "[TRAFFIC LIMIT] Missing API credentials for server %s, skipping key %s",
                    row["server_id"],
                    row["v2ray_uuid"],
                )
                continue
            config = {"api_url": api_url, "api_key": api_key}
            server_configs[row["server_id"]] = config
            server_keys_map[row["server_id"]].append(row)

        usage_map: Dict[int, Optional[int]] = {}
        if server_keys_map:
            tasks = [
                _fetch_usage_for_server(server_id, server_configs[server_id], keys)
                for server_id, keys in server_keys_map.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, dict):
                    usage_map.update(result)
                else:
                    logger.error(f"[TRAFFIC LIMIT] Error in usage fetch task: {result}")

        warn_notifications = []
        disable_notifications = []
        updates = []
        snapshot_updates: list[tuple[int, int, int]] = []

        for row in rows_data:
            key_id = row["id"]
            limit_mb = row["traffic_limit_mb"] or 0
            try:
                limit_mb_value = float(limit_mb)
            except (TypeError, ValueError):
                limit_mb_value = 0.0
            limit_bytes = max(0, int(limit_mb_value * 1024 * 1024))

            stored_total = int(row["traffic_usage_bytes"] or 0)
            usage_bytes = stored_total
            server_usage = usage_map.get(key_id)
            server_usage_int: Optional[int] = None
            if server_usage is not None:
                server_usage_int = max(int(server_usage), 0)
                last_server = snapshot_map.get(key_id)
                if last_server is None:
                    if stored_total > server_usage_int:
                        delta = server_usage_int
                    elif stored_total == server_usage_int:
                        delta = 0
                    else:
                        delta = server_usage_int - stored_total
                else:
                    delta = server_usage_int - last_server
                    if delta < 0:
                        if stored_total > server_usage_int:
                            delta = server_usage_int
                        elif stored_total == server_usage_int:
                            delta = 0
                        else:
                            delta = server_usage_int - stored_total
                if delta < 0:
                    delta = 0
                usage_bytes = stored_total + delta
                snapshot_updates.append((key_id, server_usage_int, now))
                snapshot_map[key_id] = server_usage_int

            over_limit_at = row.get("traffic_over_limit_at")
            notified_flags = row.get("traffic_over_limit_notified") or 0
            expiry_at = row.get("expiry_at") or 0

            over_limit = limit_bytes > 0 and usage_bytes > limit_bytes
            new_over_limit_at = over_limit_at
            new_notified_flags = notified_flags
            new_expiry = expiry_at
            key_uuid = row["v2ray_uuid"]
            server_id = row["server_id"]
            server_config = server_configs.get(server_id)
            tariff_name = row.get("tariff_name") or "V2Ray"
            user_id = row.get("user_id")

            if over_limit:
                if not new_over_limit_at:
                    new_over_limit_at = now

                if not (new_notified_flags & TRAFFIC_NOTIFY_WARNING):
                    limit_display = _format_bytes_short(limit_bytes)
                    usage_display = _format_bytes_short(usage_bytes)
                    deadline_ts = (new_over_limit_at or now) + TRAFFIC_DISABLE_GRACE
                    remaining = max(0, deadline_ts - now)
                    message = (
                        "⚠️ Превышен лимит трафика для вашего V2Ray ключа.\n"
                        f"Тариф: {tariff_name}\n"
                        f"Израсходовано: {usage_display} из {limit_display}.\n"
                        f"Ключ будет отключён через {format_duration(remaining)}.\n"
                        "Продлите доступ, чтобы сбросить лимит."
                    )
                    if user_id:
                        warn_notifications.append((user_id, message))
                    else:
                        logger.warning("[TRAFFIC LIMIT] Cannot notify user for key %s - user_id missing", key_uuid)
                    new_notified_flags |= TRAFFIC_NOTIFY_WARNING

                should_disable = False
                disable_deadline = None
                if new_over_limit_at:
                    disable_deadline = new_over_limit_at + TRAFFIC_DISABLE_GRACE
                    should_disable = (
                        now >= disable_deadline
                        and not (new_notified_flags & TRAFFIC_NOTIFY_DISABLED)
                        and server_config is not None
                    )

                if should_disable:
                    disable_success = await _disable_v2ray_key(server_id, server_config, key_uuid)
                    if disable_success:
                        new_expiry = now
                        new_over_limit_at = None
                        new_notified_flags |= TRAFFIC_NOTIFY_DISABLED

                        limit_display = _format_bytes_short(limit_bytes)
                        usage_display = _format_bytes_short(usage_bytes)
                        message = (
                            "❌ Ваш V2Ray ключ был отключён из-за превышения лимита трафика более чем на 24 часа.\n"
                            f"Всего израсходовано: {usage_display} из {limit_display}.\n"
                            "Продлите доступ, чтобы восстановить ключ и сбросить лимит."
                        )
                        if user_id:
                            disable_notifications.append((user_id, message))
                        else:
                            logger.warning("[TRAFFIC LIMIT] Disabled key %s without user_id", key_uuid)
                    else:
                        logger.warning(
                            "[TRAFFIC LIMIT] Failed to disable key %s on server %s; will retry later",
                            key_uuid,
                            server_id,
                        )
            else:
                new_over_limit_at = None
                new_notified_flags = 0

            updates.append(
                (
                    usage_bytes,
                    new_over_limit_at,
                    new_notified_flags,
                    new_expiry,
                    key_id,
                )
            )

        if updates:
            with get_db_cursor(commit=True) as cursor:
                cursor.executemany(
                    """
                    UPDATE v2ray_keys
                    SET traffic_usage_bytes = ?,
                        traffic_over_limit_at = ?,
                        traffic_over_limit_notified = ?,
                        expiry_at = ?
                    WHERE id = ?
                    """,
                    updates,
                )
                if snapshot_updates:
                    cursor.executemany(
                        """
                        INSERT INTO v2ray_usage_snapshots (key_id, server_bytes, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(key_id) DO UPDATE
                        SET server_bytes = excluded.server_bytes,
                            updated_at = excluded.updated_at
                        """,
                        snapshot_updates,
                    )

        if warn_notifications or disable_notifications:
            bot = get_bot_instance()
            if bot:
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

                keyboard = InlineKeyboardMarkup()
                keyboard.add(InlineKeyboardButton("🔁 Продлить", callback_data="buy"))

                for user_id, message in warn_notifications + disable_notifications:
                    result = await safe_send_message(
                        bot,
                        user_id,
                        message,
                        reply_markup=keyboard,
                        disable_web_page_preview=True,
                    )
                    if result:
                        logger.info("[TRAFFIC LIMIT] Sent notification to user %s", user_id)
                    else:
                        logger.warning("[TRAFFIC LIMIT] Failed to deliver notification to user %s", user_id)

    await _run_periodic(
        "monitor_v2ray_traffic_limits",
        interval_seconds=300,
        job=job,
        max_backoff=1800,
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
                                f"[AUTO-ISSUE] Subscription purchase processed successfully for payment {payment_uuid}, user {user_id}"
                            )
                            cursor.execute("UPDATE payments SET status = 'completed' WHERE id = ?", (payment_db_id,))
                        else:
                            logging.error(
                                f"[AUTO-ISSUE] Failed to process subscription purchase for payment {payment_uuid}, "
                                f"user {user_id}: {error_msg}"
                            )
                            # НЕ помечаем платеж как completed при ошибке, чтобы повторить попытку
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
                                    traffic_limit_mb, traffic_usage_bytes, traffic_over_limit_at, traffic_over_limit_notified
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, 0)
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
                    
                    # Удалить ключи из БД
                    with safe_foreign_keys_off(cursor):
                        deleted_keys_count = repo.delete_subscription_keys(subscription_id)
                    
                    # Деактивировать подписку
                    repo.deactivate_subscription(subscription_id)
                    
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
                    message = (
                        f"⏳ Ваша подписка V2Ray истечет через {time_str}\n\n"
                        f"🔗 https://veil-bot.ru/api/subscription/{token}\n\n"
                        f"Продлите доступ:"
                    )
                    new_notified = notified | 4
                elif (
                    original_duration > one_hour
                    and ten_minutes < remaining_time <= (one_hour + 60)
                    and (notified & 2) == 0
                ):
                    time_str = format_duration(remaining_time)
                    message = (
                        f"⏳ Ваша подписка V2Ray истечет через {time_str}\n\n"
                        f"🔗 https://veil-bot.ru/api/subscription/{token}\n\n"
                        f"Продлите доступ:"
                    )
                    new_notified = notified | 2
                elif remaining_time > 0 and remaining_time <= ten_minutes and (notified & 8) == 0:
                    time_str = format_duration(remaining_time)
                    message = (
                        f"⏳ Ваша подписка V2Ray истечет через {time_str}\n\n"
                        f"🔗 https://veil-bot.ru/api/subscription/{token}\n\n"
                        f"Продлите доступ:"
                    )
                    new_notified = notified | 8
                elif remaining_time > 0 and remaining_time <= ten_percent_threshold and (notified & 1) == 0:
                    time_str = format_duration(remaining_time)
                    message = (
                        f"⏳ Ваша подписка V2Ray истечет через {time_str}\n\n"
                        f"🔗 https://veil-bot.ru/api/subscription/{token}\n\n"
                        f"Продлите доступ:"
                    )
                    new_notified = notified | 1
                
                if message:
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    keyboard = InlineKeyboardMarkup()
                    keyboard.add(InlineKeyboardButton("🔁 Продлить", callback_data="buy"))
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
