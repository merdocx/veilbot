"""
Сервис для работы с бесплатными тарифами
"""
import time
import sqlite3
import logging
from typing import Optional, Dict, Any, cast
from aiogram import types
from config import (
    PROTOCOLS,
    ADMIN_ID,
    FREE_V2RAY_TARIFF_ID,
    FREE_V2RAY_COUNTRY,
)
from bot.keyboards import get_main_menu
from bot.services.key_creation import create_new_key_flow_with_protocol, select_available_server_by_protocol
from bot.utils import safe_send_message
from bot.core import get_bot_instance
from app.infra.sqlite_utils import get_db_cursor
from vpn_protocols import ProtocolFactory, normalize_vless_host
from app.infra.foreign_keys import safe_foreign_keys_off
from memory_optimizer import get_security_logger


def check_free_tariff_limit_by_protocol_and_country(
    cursor: sqlite3.Cursor,
    user_id: int,
    protocol: str = "outline",
    country: Optional[str] = None,
    enforce_global: bool = False,
) -> bool:
    """
    Проверка лимита бесплатных ключей для конкретного протокола и страны - один раз навсегда
    
    Args:
        cursor: Курсор БД
        user_id: ID пользователя
        protocol: Протокол (outline или v2ray)
        country: Страна (опционально)
    
    Returns:
        True если пользователь уже получал бесплатный ключ, False иначе
    """
    # Если пользователь уже когда-либо пользовался бесплатным тарифом (любым протоколом) — запрещаем повтор
    if enforce_global:
        cursor.execute(
            """
            SELECT 1 FROM free_key_usage 
            WHERE user_id = ?
            LIMIT 1
            """,
            (user_id,),
        )
        if cursor.fetchone():
            return True

    # Проверяем в таблице free_key_usage для конкретного протокола и страны (на случай отсутствия общего следа)
    cursor.execute("""
        SELECT created_at FROM free_key_usage 
        WHERE user_id = ? AND protocol = ?
    """, (user_id, protocol))
    
    row = cursor.fetchone()
    if row:
        return True  # Пользователь уже получал бесплатный ключ для этого протокола
    
    # Если указана конкретная страна, дополнительно проверяем для неё
    if country:
        cursor.execute("""
            SELECT created_at FROM free_key_usage 
            WHERE user_id = ? AND protocol = ? AND country = ?
        """, (user_id, protocol, country))
        
        row = cursor.fetchone()
        if row:
            return True  # Пользователь уже получал бесплатный ключ для этого протокола и страны
    
    # Дополнительная проверка в таблицах ключей (для обратной совместимости)
    if protocol == "outline":
        if country:
            cursor.execute("""
                SELECT k.created_at FROM keys k
                JOIN tariffs t ON k.tariff_id = t.id
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND t.price_rub = 0 AND s.country = ?
                ORDER BY k.created_at DESC LIMIT 1
            """, (user_id, country))
        else:
            cursor.execute("""
                SELECT k.created_at FROM keys k
                JOIN tariffs t ON k.tariff_id = t.id
                WHERE k.user_id = ? AND t.price_rub = 0
                ORDER BY k.created_at DESC LIMIT 1
            """, (user_id,))
    else:  # v2ray
        if country:
            cursor.execute("""
                SELECT k.created_at FROM v2ray_keys k
                JOIN tariffs t ON k.tariff_id = t.id
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND t.price_rub = 0 AND s.country = ?
                ORDER BY k.created_at DESC LIMIT 1
            """, (user_id, country))
        else:
            cursor.execute("""
                SELECT k.created_at FROM v2ray_keys k
                JOIN tariffs t ON k.tariff_id = t.id
                WHERE k.user_id = ? AND t.price_rub = 0
                ORDER BY k.created_at DESC LIMIT 1
            """, (user_id,))
    
    row = cursor.fetchone()
    # Если найден любой бесплатный ключ — нельзя (только один раз навсегда)
    if row:
        return True
    # Иначе можно
    return False


def check_free_tariff_limit(cursor: sqlite3.Cursor, user_id: int) -> bool:
    """
    Проверка лимита бесплатных ключей - один раз навсегда (для обратной совместимости)
    
    Args:
        cursor: Курсор БД
        user_id: ID пользователя
    
    Returns:
        True если пользователь уже получал бесплатный ключ, False иначе
    """
    return check_free_tariff_limit_by_protocol_and_country(
        cursor,
        user_id,
        "outline",
        enforce_global=True,
    )


def check_free_tariff_limit_by_protocol(
    cursor: sqlite3.Cursor,
    user_id: int,
    protocol: str = "outline",
) -> bool:
    """
    Проверка лимита бесплатных ключей для конкретного протокола - один раз навсегда (для обратной совместимости)
    
    Args:
        cursor: Курсор БД
        user_id: ID пользователя
        protocol: Протокол (outline или v2ray)
    
    Returns:
        True если пользователь уже получал бесплатный ключ, False иначе
    """
    return check_free_tariff_limit_by_protocol_and_country(
        cursor,
        user_id,
        protocol,
        enforce_global=False,
    )


def record_free_key_usage(
    cursor: sqlite3.Cursor, 
    user_id: int, 
    protocol: str = "outline", 
    country: Optional[str] = None
) -> bool:
    """
    Записывает использование бесплатного ключа пользователем
    
    Args:
        cursor: Курсор БД
        user_id: ID пользователя
        protocol: Протокол (outline или v2ray)
        country: Страна (опционально)
    
    Returns:
        True если запись успешна, False если запись уже существует или произошла ошибка
    """
    now = int(time.time())
    try:
        cursor.execute("""
            INSERT INTO free_key_usage (user_id, protocol, country, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, protocol, country, now))
        return True
    except sqlite3.IntegrityError:
        # Запись уже существует (UNIQUE constraint)
        return False
    except Exception as e:
        logging.error(f"Failed to record free key usage: {e}")
        return False


async def handle_free_tariff(
    cursor: sqlite3.Cursor, 
    message: types.Message, 
    user_id: int, 
    tariff: Dict[str, Any], 
    country: Optional[str] = None
) -> None:
    """
    Обработка бесплатного тарифа (старая версия без протоколов, для обратной совместимости)
    
    Args:
        cursor: Курсор БД
        message: Telegram сообщение
        user_id: ID пользователя
        tariff: Словарь с данными тарифа
        country: Страна (опционально)
    """
    main_menu = get_main_menu()
    
    if check_free_tariff_limit(cursor, user_id):
        await message.answer("Вы уже получали бесплатный тариф ранее. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
        return
    
    now = int(time.time())
    # Проверяем наличие активного ключа и его тип
    cursor.execute("""
        SELECT k.id, COALESCE(sub.expires_at, 0) as expiry_at, t.price_rub
        FROM keys k
        JOIN tariffs t ON k.tariff_id = t.id
        LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
        WHERE k.user_id = ? AND sub.expires_at > ?
        ORDER BY sub.expires_at DESC LIMIT 1
    """, (user_id, now))
    existing_key = cursor.fetchone()
    
    if existing_key:
        if existing_key[2] > 0:
            await message.answer("У вас уже есть активный платный ключ. Бесплатный ключ можно получить только если нет активных платных ключей.", reply_markup=main_menu)
            return
        else:
            await message.answer("У вас уже есть активный бесплатный ключ. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
            return
    else:
        # Импортируем create_new_key_flow из bot.py для обратной совместимости
        # Это старая функция, которая не поддерживает протоколы
        from bot import create_new_key_flow
        await create_new_key_flow(cursor, message, user_id, tariff, None, country)


async def handle_free_tariff_with_protocol(
    cursor: sqlite3.Cursor, 
    message: types.Message, 
    user_id: int, 
    tariff: Dict[str, Any], 
    country: Optional[str] = None, 
    protocol: str = "outline"
) -> None:
    """
    Обработка бесплатного тарифа с поддержкой протоколов
    
    Args:
        cursor: Курсор БД
        message: Telegram сообщение
        user_id: ID пользователя
        tariff: Словарь с данными тарифа
        country: Страна (опционально)
        protocol: Протокол (outline или v2ray)
    """
    main_menu = get_main_menu()
    
    # Проверяем лимит бесплатных ключей для выбранного протокола и страны
    if check_free_tariff_limit_by_protocol_and_country(
        cursor,
        user_id,
        protocol,
        country,
        enforce_global=True,
    ):
        if country:
            await message.answer(f"Вы уже получали бесплатный тариф {PROTOCOLS[protocol]['name']} для страны {country} ранее. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
        else:
            await message.answer(f"Вы уже получали бесплатный тариф {PROTOCOLS[protocol]['name']} ранее. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
        return
    
    now = int(time.time())
    
    # Проверяем наличие активного ключа для конкретной страны и протокола
    if protocol == "outline":
        if country:
            cursor.execute("""
                SELECT k.id, COALESCE(sub.expires_at, 0) as expiry_at, t.price_rub
                FROM keys k
                JOIN tariffs t ON k.tariff_id = t.id
                JOIN servers s ON k.server_id = s.id
                LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.user_id = ? AND sub.expires_at > ? AND s.country = ?
                ORDER BY sub.expires_at DESC LIMIT 1
            """, (user_id, now, country))
        else:
            cursor.execute("""
                SELECT k.id, COALESCE(sub.expires_at, 0) as expiry_at, t.price_rub
                FROM keys k
                JOIN tariffs t ON k.tariff_id = t.id
                LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.user_id = ? AND sub.expires_at > ?
                ORDER BY sub.expires_at DESC LIMIT 1
            """, (user_id, now))
    else:  # v2ray
        if country:
            cursor.execute("""
                SELECT k.id, COALESCE(sub.expires_at, 0) as expiry_at, t.price_rub
                FROM v2ray_keys k
                JOIN tariffs t ON k.tariff_id = t.id
                JOIN servers s ON k.server_id = s.id
                LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.user_id = ? AND sub.expires_at > ? AND s.country = ?
                ORDER BY sub.expires_at DESC LIMIT 1
            """, (user_id, now, country))
        else:
            cursor.execute("""
                SELECT k.id, COALESCE(sub.expires_at, 0) as expiry_at, t.price_rub
                FROM v2ray_keys k
                JOIN tariffs t ON k.tariff_id = t.id
                LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.user_id = ? AND sub.expires_at > ?
                ORDER BY sub.expires_at DESC LIMIT 1
            """, (user_id, now))
    
    existing_key = cursor.fetchone()
    if existing_key:
        if existing_key[2] > 0:
            if country:
                await message.answer(f"У вас уже есть активный платный ключ {PROTOCOLS[protocol]['name']} для страны {country}. Бесплатный ключ можно получить только если нет активных платных ключей.", reply_markup=main_menu)
            else:
                await message.answer(f"У вас уже есть активный платный ключ {PROTOCOLS[protocol]['name']}. Бесплатный ключ можно получить только если нет активных платных ключей.", reply_markup=main_menu)
            return
        else:
            if country:
                await message.answer(f"У вас уже есть активный бесплатный ключ {PROTOCOLS[protocol]['name']} для страны {country}. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
            else:
                await message.answer(f"У вас уже есть активный бесплатный ключ {PROTOCOLS[protocol]['name']}. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
            return
    else:
        # Для бесплатного тарифа создаем ключ сразу без запроса email
        # Импортируем user_states из bot.py через lazy import для избежания циклических зависимостей
        try:
            import importlib
            bot_module = importlib.import_module('bot')
            user_states = getattr(bot_module, 'user_states', {})
        except Exception as e:
            logging.error(f"Error importing user_states: {e}")
            user_states = {}
        
        await create_new_key_flow_with_protocol(
            cursor, 
            message, 
            user_id, 
            tariff, 
            None,  # email
            country, 
            protocol,
            for_renewal=False,
            user_states=user_states
        )


async def issue_free_v2ray_subscription_on_start(message: types.Message) -> Dict[str, Any]:
    """
    Автоматическая выдача бесплатной V2Ray подписки при первом /start.
    Возвращает статус операции и данные подписки (если успешно).
    """
    user_id = message.from_user.id
    telegram_user = message.from_user

    outline_result: Dict[str, Any] | None = None

    with get_db_cursor(commit=True) as cursor:
        if check_free_tariff_limit_by_protocol_and_country(
            cursor,
            user_id,
            protocol="v2ray",
            country=FREE_V2RAY_COUNTRY,
            enforce_global=True,
        ):
            return {"status": "already_issued"}

        cursor.execute(
            "SELECT id, name, duration_sec, traffic_limit_mb, price_rub "
            "FROM tariffs WHERE id = ?",
            (FREE_V2RAY_TARIFF_ID,),
        )
        row = cursor.fetchone()
        if not row:
            logging.error("Tariff with id %s not found for free issuance", FREE_V2RAY_TARIFF_ID)
            return {
                "status": "tariff_missing",
                "message": "Бесплатный тариф временно недоступен.",
            }

        tariff = {
            "id": row[0],
            "name": row[1],
            "duration_sec": row[2],
            "traffic_limit_mb": row[3] or 0,
            "price_rub": row[4] or 0,
        }

        # Проверяем наличие активных V2Ray серверов
        cursor.execute(
            "SELECT COUNT(*) FROM servers WHERE protocol = 'v2ray' AND active = 1"
        )
        server_count = cursor.fetchone()[0]
        if server_count == 0:
            logging.warning("No active V2Ray servers available for free subscription issuance")
            return {"status": "no_server"}

        # Создаем подписку через SubscriptionService
        subscription_data = None
        try:
            from bot.services.subscription_service import SubscriptionService
            service = SubscriptionService()
            subscription_data = await service.create_subscription(
                user_id=user_id,
                tariff_id=tariff["id"],
                duration_sec=tariff["duration_sec"],
            )
            
            if not subscription_data:
                logging.error("Failed to create free subscription for user %s", user_id)
                return {"status": "error"}
            
            # Записываем использование бесплатного тарифа
            # Используем FREE_V2RAY_COUNTRY для записи, хотя ключи создаются на всех серверах
            record_free_key_usage(
                cursor,
                user_id=user_id,
                protocol="v2ray",
                country=FREE_V2RAY_COUNTRY,
            )

            try:
                outline_result = await _issue_outline_key_for_start(
                    cursor=cursor,
                    message=message,
                    user_id=user_id,
                    tariff=tariff,
                    subscription_id=subscription_data["id"],
                )
                # Проверяем статус результата, не только исключения
                if outline_result and outline_result.get("status") not in ("issued", "already_issued"):
                    error_status = outline_result.get("status", "unknown")
                    logging.warning(
                        "Failed to auto-issue Outline key for user %s: status=%s",
                        user_id,
                        error_status,
                    )
            except Exception as outline_exc:  # noqa: BLE001
                logging.exception(
                    "Failed to auto-issue Outline key for user %s: %s",
                    user_id,
                    outline_exc,
                )
                outline_result = {"status": "error", "error": str(outline_exc)}
            
        except Exception as exc:  # noqa: BLE001
            logging.exception("Failed to create free V2Ray subscription for user %s: %s", user_id, exc)
            return {"status": "error"}

    # Уведомление администратора (вне транзакции)
    if subscription_data:
        try:
            bot = get_bot_instance()
            created_v2ray_keys = subscription_data.get("created_keys", 0)
            failed_servers = subscription_data.get("failed_servers", [])
            
            # Проверяем, был ли создан Outline ключ
            outline_created = outline_result and outline_result.get("status") == "issued"
            
            admin_message = (
                "🎁 *Выдана бесплатная подписка*\n"
                f"Пользователь: `{user_id}`\n"
                f"Тариф: *{tariff['name']}*\n"
                f"Создано V2Ray ключей: *{created_v2ray_keys}*\n"
                f"Создано Outline ключей: *{1 if outline_created else 0}*"
            )
            if failed_servers:
                admin_message += f"\nНе удалось создать на серверах: {failed_servers}"
            await safe_send_message(
                bot,
                ADMIN_ID,
                admin_message,
                disable_web_page_preview=True,
                parse_mode="Markdown",
                mark_blocked=False,
            )
        except Exception as notify_exc:  # noqa: BLE001
            logging.warning("Failed to notify admin about free subscription issuance: %s", notify_exc)

    if not subscription_data:
        return {"status": "error"}

    return {
        "status": "issued",
        "subscription_token": subscription_data["token"],
        "subscription_id": subscription_data["id"],
        "tariff": tariff,
        "expires_at": subscription_data["expires_at"],
        "created_keys": subscription_data.get("created_keys", 0),
        "failed_servers": subscription_data.get("failed_servers", []),
        "outline_key": outline_result,
    }


# Алиас для обратной совместимости
issue_free_v2ray_key_on_start = issue_free_v2ray_subscription_on_start


async def _issue_outline_key_for_start(
    cursor: sqlite3.Cursor,
    message: types.Message,
    user_id: int,
    tariff: Dict[str, Any],
    subscription_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Создает бесплатный Outline ключ при первом /start.
    """
    if check_free_tariff_limit_by_protocol_and_country(
        cursor,
        user_id,
        protocol="outline",
        country=None,
        enforce_global=False,
    ):
        return {"status": "already_issued"}

    server = select_available_server_by_protocol(
        cursor,
        protocol="outline",
    )
    if not server:
        logging.warning("No Outline servers available for free auto-issue")
        return {"status": "no_server"}

    server_id, server_name, api_url, cert_sha256, domain, api_key, v2ray_path = server
    server_config = {
        "api_url": api_url,
        "cert_sha256": cert_sha256,
    }

    protocol_client = ProtocolFactory.create_protocol("outline", server_config)
    key_email = (
        f"{user_id}_subscription_{subscription_id}@veilbot.com"
        if subscription_id
        else f"user_{user_id}@veilbot.com"
    )

    now = int(time.time())
    expiry_at = now + int(tariff.get("duration_sec") or 0)
    traffic_limit_mb = int(tariff.get("traffic_limit_mb") or 0)

    user_data: Dict[str, Any] | None = None

    try:
        user_data = await protocol_client.create_user(key_email)
        if not user_data or not user_data.get("id") or not user_data.get("accessUrl"):
            raise RuntimeError("Invalid response from Outline server while creating key")

        access_url = user_data["accessUrl"]
        outline_key_id = user_data["id"]

        # ВАЖНО: expiry_at удалено из таблицы keys - срок действия берется из subscriptions
        with safe_foreign_keys_off(cursor):
            cursor.execute(
                """
                INSERT INTO keys (
                    server_id,
                    user_id,
                    access_url,
                    traffic_limit_mb,
                    notified,
                    key_id,
                    created_at,
                    email,
                    tariff_id,
                    protocol,
                    subscription_id
                )
                VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, 'outline', ?)
                """,
                (
                    server_id,
                    user_id,
                    access_url,
                    traffic_limit_mb,
                    outline_key_id,
                    now,
                    key_email,
                    tariff["id"],
                    subscription_id,
                ),
            )

        cursor.execute("SELECT country FROM servers WHERE id = ?", (server_id,))
        row_country = cursor.fetchone()
        server_country = row_country[0] if row_country else None

        record_free_key_usage(
            cursor,
            user_id=user_id,
            protocol="outline",
            country=server_country,
        )

        try:
            security_logger = get_security_logger()
            if security_logger:
                security_logger.log_key_creation(
                    user_id=user_id,
                    key_id=outline_key_id,
                    protocol="outline",
                    server_id=server_id,
                    tariff_id=tariff["id"],
                    ip_address=None,
                    user_agent="Telegram Bot (/start auto-issue - outline)",
                )
        except Exception as sec_exc:  # noqa: BLE001
            logging.warning("Failed to log security event for Outline key: %s", sec_exc)

        logging.info(
            "Issued Outline key %s for user %s on server %s",
            outline_key_id,
            user_id,
            server_id,
        )

        return {
            "status": "issued",
            "access_url": access_url,
            "key_id": outline_key_id,
            "server": {
                "id": server_id,
                "name": server_name,
                "country": server_country,
            },
            "expires_at": expiry_at,
            "tariff": tariff,
        }
    except Exception:
        # Если Outline ключ создан и произошла ошибка позже - очищаем
        try:
            if protocol_client and user_data and user_data.get("id"):
                await protocol_client.delete_user(user_data["id"])
        except Exception as cleanup_exc:  # noqa: BLE001
            logging.warning("Failed to cleanup Outline user after error: %s", cleanup_exc)
        raise
    finally:
        try:
            await protocol_client.close()
        except Exception as close_exc:  # noqa: BLE001
            logging.debug("Failed to close Outline protocol client: %s", close_exc)


async def _create_v2ray_key_for_start(
    cursor: sqlite3.Cursor,
    server: tuple,
    user_id: int,
    tariff: Dict[str, Any],
    telegram_user: types.User | None = None,
) -> Dict[str, Any]:
    """
    Создание V2Ray ключа для автоматической выдачи при /start.
    Возвращает словарь с конфигурацией и данными ключа.
    """
    now = int(time.time())
    expiry = now + int(tariff["duration_sec"] or 0)
    traffic_limit_mb = int(tariff.get("traffic_limit_mb") or 0)

    server_id, server_name, api_url, cert_sha256, domain, api_key, v2ray_path = server
    cursor.execute("SELECT country FROM servers WHERE id = ?", (server_id,))
    row_country = cursor.fetchone()
    server_country = row_country[0] if row_country and row_country[0] else None
    server_config = {
        "api_url": api_url,
        "cert_sha256": cert_sha256,
        "api_key": api_key,
        "domain": domain,
        "path": v2ray_path,
    }

    protocol_client = None
    user_data = None
    user_email = None

    try:
        protocol_client = ProtocolFactory.create_protocol("v2ray", server_config)
        user_email = (
            f"{telegram_user.username or 'user'}_{user_id}@veilbot.com"
            if telegram_user and telegram_user.username
            else f"user_{user_id}@veilbot.com"
        )
        user_data = await protocol_client.create_user(user_email)
        if not user_data or not user_data.get("uuid"):
            raise RuntimeError("Invalid response from V2Ray server while creating user")

        config = await _extract_vless_config(user_data, server_config, user_email, protocol_client)

        with safe_foreign_keys_off(cursor):
            cursor.execute(
                """
                INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, created_at, last_active_at, blocked)
                VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM users WHERE user_id = ?), ?), ?, 0)
                """,
                (
                    user_id,
                    getattr(telegram_user, "username", None),
                    getattr(telegram_user, "first_name", None),
                    getattr(telegram_user, "last_name", None),
                    user_id,
                    now,
                    now,
                ),
            )

            cursor.execute(
                """
                INSERT INTO v2ray_keys (
                    server_id,
                    user_id,
                    v2ray_uuid,
                    email,
                    created_at,
                    expiry_at,
                    tariff_id,
                    client_config,
                    notified,
                    traffic_limit_mb,
                    traffic_usage_bytes,
                    traffic_over_limit_at,
                    traffic_over_limit_notified
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, NULL, 0)
                """,
                (
                    server_id,
                    user_id,
                    user_data["uuid"],
                    user_email,
                    now,
                    expiry,
                    tariff["id"],
                    config,
                    traffic_limit_mb,
                ),
            )

        try:
            security_logger = get_security_logger()
            if security_logger:
                security_logger.log_key_creation(
                    user_id=user_id,
                    key_id=user_data["uuid"],
                    protocol="v2ray",
                    server_id=server_id,
                    tariff_id=tariff["id"],
                    ip_address=None,
                    user_agent="Telegram Bot (/start auto-issue)",
                )
        except Exception as sec_exc:  # noqa: BLE001
            logging.warning("Failed to log security event for free V2Ray key: %s", sec_exc)

        return {
            "config": config,
            "server": {
                "id": server_id,
                "name": server_name,
                "country": server_country,
            },
            "expires_at": expiry,
        }
    except Exception:
        if protocol_client and user_data and user_data.get("uuid"):
            try:
                await protocol_client.delete_user(user_data["uuid"])
            except Exception as cleanup_exc:  # noqa: BLE001
                logging.warning("Failed to cleanup V2Ray user after error: %s", cleanup_exc)
        raise
    finally:
        if protocol_client:
            try:
                await protocol_client.close()
            except Exception as close_exc:  # noqa: BLE001
                logging.debug("Failed to close V2Ray client cleanly: %s", close_exc)


async def _extract_vless_config(
    user_data: Dict[str, Any],
    server_config: Dict[str, Any],
    email: str,
    protocol_client,
) -> str:
    """
    Извлекает VLESS-конфиг из ответа create_user или через дополнительный запрос.
    """
    config_data = user_data.get("client_config")
    config: Optional[str] = None
    if isinstance(config_data, str):
        config = config_data
    elif config_data is not None:
        config = str(config_data)

    if config and "vless://" in config:
        for line in config.splitlines():
            if line.strip().startswith("vless://"):
                return cast(
                    str,
                    normalize_vless_host(
                    line.strip(),
                    server_config.get("domain"),
                    server_config.get("api_url", ""),
                    ),
                )

    raw_config = await protocol_client.get_user_config(
        user_data["uuid"],
        {
            "domain": server_config.get("domain"),
            "port": 443,
            "path": server_config.get("path") or "/v2ray",
            "email": email,
        },
    )
    config = raw_config if isinstance(raw_config, str) else str(raw_config)
    if config and "vless://" in config:
        for line in config.splitlines():
            if line.strip().startswith("vless://"):
                return cast(
                    str,
                    normalize_vless_host(
                    line.strip(),
                    server_config.get("domain"),
                    server_config.get("api_url", ""),
                    ),
                )
    # Fallback на простую ссылку если API вернул неожиданный формат
    domain = cast(str, server_config.get("domain") or "example.com")
    path = cast(str, server_config.get("path") or "/v2ray")
    uuid = str(user_data["uuid"])
    fallback = (
        f"vless://{uuid}@{domain}:443?path={path}&security=tls&type=ws"
        f"#{email or 'VeilBot-V2Ray'}"
    )
    return cast(
        str,
        normalize_vless_host(
            fallback,
            server_config.get("domain"),
            server_config.get("api_url", ""),
        ),
    )

